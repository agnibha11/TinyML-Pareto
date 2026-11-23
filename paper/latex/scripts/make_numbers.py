#!/usr/bin/env python3
"""Build every number used by the paper from the repository's result files.

    python paper/latex/scripts/make_numbers.py [--repo <TinyML-Pareto>] [--out paper/latex]

Writes  numbers.tex   (\\Num... macros, the only source of numbers in main.tex)
        figdata/*.csv (tables the figure script reads)

Rules (documented decisions, also stated in the paper):
  * energy per call  E = (P_mean - P_idle) * t_window(logger) / N, P_idle = IDLE window recorded just before;
  * last accepted row per (model, session, input, mode); mean over the five stored inputs per repeat;
    mean (and CV) over repeats;
  * repeats 1-3 for trees, forests and fp32 networks; repeats 5-7 (patched int8v2 library, bit-exact outputs) for
    int8/int8qat models, so every reported energy belongs to code whose outputs were verified on the board;
  * accuracy = host macro-F1 of the exported model (mean over folds x seeds); valid for the board because VERIFY
    shows board outputs equal the host reference (bit-exact for int8 after the patch);
  * Pareto objectives: maximise macro-F1, minimise E2E energy and model flash; hypervolume on (1-F1, log10 E,
    log10 flash), each min-max normalised over the 80 deployed models of the protocol, reference point 1.1.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kendalltau, spearmanr

DIG = dict(zip("0123456789", ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]))
MACROS: dict[str, str] = {}
LF_SHIFT: list[float] = []   # largest anchor shift per dataset (float-log campaign)


def name(*parts) -> str:
    s = "".join(str(p)[:1].upper() + str(p)[1:] for p in parts if p != "")
    s = re.sub(r"\d", lambda m: DIG[m.group()], s)
    s = re.sub(r"[^A-Za-z]", "", s)
    return s


def put(key: str, val) -> None:
    key = re.sub(r"\d", lambda m: DIG[m.group()], key)
    assert re.fullmatch(r"[A-Za-z]+", key), key
    MACROS[key] = str(val)


def neg(t: str) -> str:
    return "$-$" + t[1:] if t.startswith("-") else t


def f(x, nd=1):
    return neg(f"{x:,.{nd}f}".replace(",", "{,}"))


def pct(x, nd=1):
    return neg(f"{100 * x:.{nd}f}\\,\\%")


def sig(x, n=2):
    if x == 0 or not math.isfinite(x):
        return "0"
    d = max(0, n - 1 - int(math.floor(math.log10(abs(x)))))
    return f(x, d)


def parse(mid: str) -> dict:
    fam, rep, cfg, prec = mid.split("-")
    return dict(family=fam.upper(), rep=rep.upper(), cfg=cfg, prec=prec)


def repeat_of(session: int) -> int:
    return (int(session) % 100) // 10


def energy_table(csv: Path, reps_plain, reps_int8):
    d = pd.read_csv(csv)
    n_all, n_rej = len(d), int(d.rejected.sum())
    acc = d[d.rejected == 0].copy()
    idle = acc[acc["mode"] == "IDLE"]
    w = acc[acc["mode"].isin(["RUN", "FRONT", "E2E"])].copy()
    w["input_j"] = w["input_j"].astype(int)
    w = w.groupby(["model_id", "session", "input_j", "mode"], as_index=False).last()
    w["E"] = (w.P_mean_uW - w.P_idle_uW) * (w.t_window_us_logger / 1e6) / w.N
    w["t_us"] = w.t_window_us_logger / w.N
    w["rep"] = w.session.map(repeat_of)
    per = w.groupby(["model_id", "mode", "rep"], as_index=False).agg(E=("E", "mean"), t_us=("t_us", "mean"),
                                                                     n_in=("E", "size"))
    rows = []
    for (mid, mode), g in per.groupby(["model_id", "mode"]):
        is8 = parse(mid)["prec"].startswith("int8")
        use = g[g.rep.isin(reps_int8 if is8 else reps_plain)]
        old = g[g.rep.isin(reps_plain)]
        if use.empty:
            continue
        rows.append(dict(model_id=mid, mode=mode, E=use.E.mean(), t_us=use.t_us.mean(), n_rep=len(use),
                         cv=use.E.std(ddof=1) / use.E.mean() if len(use) > 1 else np.nan,
                         E_r123=old.E.mean() if not old.empty else np.nan))
    t = pd.DataFrame(rows)
    wide = t.pivot(index="model_id", columns="mode", values=["E", "t_us", "cv", "E_r123", "n_rep"])
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    stats = dict(n_windows=n_all, n_rejected=n_rej, idle_med=idle.P_mean_uW.median() / 1e3,
                 idle_p5=idle.P_mean_uW.quantile(0.05) / 1e3, idle_p95=idle.P_mean_uW.quantile(0.95) / 1e3,
                 vbus_min=acc.V_bus_mV.min() / 1e3, vbus_max=acc.V_bus_mV.max() / 1e3,
                 sessions=d.session.nunique(), cv_med=t[t["mode"] == "E2E"].cv.median(),
                 cv_max=t[t["mode"] == "E2E"].cv.max())
    # additivity: E2E vs FRONT + RUN
    add = (wide["E_E2E"] - wide["E_FRONT"] - wide["E_RUN"]).abs() / wide["E_E2E"]
    stats["add_med"] = add.median()
    return wide.reset_index(), stats


def hv3(points: np.ndarray, ref: float = 1.1) -> float:
    """Exact hypervolume of minimisation points in 3-D (slice along the 3rd objective)."""
    if len(points) == 0:
        return 0.0
    p = points[np.argsort(points[:, 2])]
    total = 0.0
    for i in range(len(p)):
        z0 = p[i, 2]
        z1 = p[i + 1, 2] if i + 1 < len(p) else ref
        if z1 <= z0:
            continue
        q = p[: i + 1, :2]
        q = q[np.argsort(q[:, 0])]
        area, best_y = 0.0, ref
        for x, y in q:
            if y < best_y:
                area += (ref - x) * (best_y - y)
                best_y = y
        total += area * (z1 - z0)
    return total


def front_mask(obj: np.ndarray) -> np.ndarray:
    """Non-dominated mask for minimisation objectives."""
    n = len(obj)
    keep = np.ones(n, bool)
    for i in range(n):
        dom = np.all(obj <= obj[i], axis=1) & np.any(obj < obj[i], axis=1)
        if dom.any():
            keep[i] = False
    return keep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=Path(__file__).resolve().parents[3], type=Path,
                    help="TinyML-Pareto checkout (default: the repo this paper/latex folder sits in)")
    ap.add_argument("--out", default=Path(__file__).resolve().parents[1], type=Path)
    a = ap.parse_args()
    R = a.repo / "results"
    fd = a.out / "figdata"
    fd.mkdir(exist_ok=True)

    rc = json.loads((a.out / "scripts" / "rig_constants.json").read_text())
    for k, v in rc.items():
        if not k.startswith("_"):
            put("Rig" + k, v)

    # ---------------- cascade gate rates transcribed from the pre-registration (see cascade_rates.json) ----
    cr = json.loads((a.out / "scripts" / "cascade_rates.json").read_text())
    for g, (tpr, fpr) in cr["cwru_board_split"].items():
        put(name("CasCw", g, "Tpr"), f(tpr, 3 if tpr < 1 and round(tpr, 2) != tpr else 2))
        put(name("CasCw", g, "Fpr"), f(fpr, 3 if round(fpr, 2) != fpr else 2))
    put("CasCwFactoryFprAll", f(cr["cwru_factory_fpr_all_p2_folds"], 2))
    put("CasCwSelfFprAll", f(cr["cwru_selfcal_all_p2_folds_n32"], 3))
    rg = cr["pu_rule_gate"]
    put("CasPuRuleNPass", rg["n_pass"]); put("CasPuRuleNGates", rg["n_gates"])
    put("CasPuRuleE", rg["E_cascade_pi1"]); put("CasPuModelE", rg["E_model_alone"])
    for n in ["n64", "n128"]:
        g6 = cr["pu_G6"][n]
        put(name("CasPuSix", n, "TprPThree"), f(g6["p3"][0], 3)); put(name("CasPuSix", n, "FprPThree"), f(g6["p3"][1], 3))
        put(name("CasPuSix", n, "TprPFour"), f(g6["p4"][0], 2)); put(name("CasPuSix", n, "FprPFour"), f(g6["p4"][1], 3))
        put(name("CasPuSix", n, "E"), g6["E_pi1"])
    for k, v in cr["E_pi1_stream"].items():
        put(name("CasEpi", k.replace("_", "")), f(v, 1))
    put("CasCalibSixteenS", f(16 * cr["window_samples"] / cr["fs_hz"], 1))
    put("CasCalibOneTwoEightS", f(128 * cr["window_samples"] / cr["fs_hz"], 0))
    put("CasCalibThreeTwoS", f(32 * cr["window_samples"] / cr["fs_hz"], 1))
    put("WindowMs", f(cr["window_samples"] / cr["fs_hz"] * 1e3, 1))

    # ---------------- host facts carried over from the repository's own paper/numbers.tex --------------
    old = {}
    for m in re.finditer(r"\\newcommand\{\\Num([A-Za-z]+)\}\{(.*)\}\s*$", (a.repo / "paper" / "numbers.tex").read_text(),
                         re.M):
        old[m.group(1)] = m.group(2)
    for k in ["POneLeakPct", "AblMeanWith", "AblMeanWithout", "MeanZMax", "CmsisMedian", "CmsisMin", "CmsisMax"]:
        if k in old:
            put("Host" + k, old[k])

    allmodels = {}
    summary = {}
    for ds, nprot in [("cwru", ["P1", "P2", "P3"]), ("pu", ["P1", "P2", "P3", "P4"])]:
        D = name(ds)  # Cwru / Pu
        wide, st = energy_table(R / ds / "hardware" / "hw_measurements.csv", [1, 2, 3], [5, 6, 7])
        meta = pd.DataFrame([parse(m) for m in wide.model_id])
        wide = pd.concat([wide, meta], axis=1)
        fb = pd.read_csv(R / ds / "hardware" / "fw_build.csv")[["model_id", "flash_model_B_board", "arena_B", "runtime"]]
        wide = wide.merge(fb, on="model_id", how="left")
        pre = pd.read_csv(R / "cwru" / "analysis" / "prehw_costs.csv")[
            ["model_id", "E_e2e_uJ", "lat_e2e_us", "E_fe_uJ", "E_inf_uJ", "ram_peak_B", "params", "macs"]]
        wide = wide.merge(pre, on="model_id", how="left")
        # accuracy
        hs = pd.read_csv(R / ds / "analysis" / "host_summary.csv")
        if ds == "cwru":
            hs = hs[hs.snr_db.isna()].rename(columns={"f1_macro_mean": "f1", "f1_macro_std": "f1sd"})
        else:
            hs = hs.rename(columns={"f1_mean": "f1", "f1_std": "f1sd"})
        acc = hs.pivot(index="model_id", columns="protocol", values="f1")
        acc.columns = [f"f1_{c}" for c in acc.columns]
        wide = wide.merge(acc, left_on="model_id", right_index=True, how="left")
        wide["dataset"] = ds
        wide.to_csv(fd / f"models_{ds}.csv", index=False)
        allmodels[ds] = wide

        # ---- campaign statistics
        put(D + "Windows", f(st["n_windows"], 0))
        put(D + "Rejected", f(st["n_rejected"], 0))
        put(D + "RejPct", pct(st["n_rejected"] / st["n_windows"], 2))
        put(D + "Sessions", st["sessions"])
        put(D + "IdleMed", f(st["idle_med"], 2))
        put(D + "IdleSpreadMW", f(st["idle_p95"] - st["idle_p5"], 2))
        put(D + "IdleLo", f(st["idle_p5"], 2))
        put(D + "IdleHi", f(st["idle_p95"], 2))
        put(D + "CvMed", pct(st["cv_med"], 2))
        put(D + "CvMax", pct(st["cv_max"], 2))
        put(D + "AddMed", pct(st["add_med"], 2))
        put(D + "NModels", len(wide))
        summary[ds] = st

        # ---- front-end energy per representation (FRONT mode, median over models that use it)
        for rp in ["R0", "R1", "R2", "R3", "R4"]:
            g = wide[wide.rep == rp]
            if len(g):
                put(D + "Front" + rp, sig(g.E_FRONT.median(), 3))
                put(D + "FrontT" + rp, f(g.t_us_FRONT.median() / 1e3, 2))
        # ---- family E2E ranges and front-end share
        for fam in ["DT", "RF", "MLP", "CNN"]:
            g = wide[wide.family == fam]
            share = (g.E_FRONT / g.E_E2E).median()
            put(D + fam + "EMin", sig(g.E_E2E.min(), 3))
            put(D + fam + "EMed", sig(g.E_E2E.median(), 3))
            put(D + fam + "EMax", sig(g.E_E2E.max(), 3))
            put(D + fam + "FrontShare", pct(share, 1))
            put(D + fam + "RunMin", sig(g.E_RUN.min(), 2))
            put(D + fam + "RunMax", sig(g.E_RUN.max(), 2))
            put(D + fam + "FlashMinKB", sig(g.flash_model_B_board.min() / 1024, 2))
            put(D + fam + "FlashMaxKB", sig(g.flash_model_B_board.max() / 1024, 2))
            put(D + fam + "TMinMs", sig(g.t_us_E2E.min() / 1e3, 2))
            put(D + fam + "TMaxMs", sig(g.t_us_E2E.max() / 1e3, 2))
            put(D + fam + "N", len(g))
        cheapest_tree = wide[wide.family.isin(["DT", "RF"])].E_E2E.min()
        put(D + "CnnOverTreeMin", f(wide[wide.family == "CNN"].E_E2E.min() / cheapest_tree, 0))
        put(D + "CnnOverTreeMax", f(wide[wide.family == "CNN"].E_E2E.max() / cheapest_tree, 0))
        put(D + "CnnEMinMJ", sig(wide[wide.family == "CNN"].E_E2E.min() / 1e3, 2))
        put(D + "CnnEMaxMJ", sig(wide[wide.family == "CNN"].E_E2E.max() / 1e3, 2))
        # int8 re-measure ratio (patched / unpatched) on E2E
        i8 = wide[wide.prec.str.startswith("int8")]
        r = (i8.E_E2E / i8.E_r123_E2E).dropna()
        put(D + "PatchRatioMin", f(r.min(), 3))
        put(D + "PatchRatioMax", f(r.max(), 3))
        put(D + "PatchRatioMed", f(r.median(), 3))

        # ---- estimates vs measurement (chip-level cycle model of the pre-hardware study)
        ok = wide.dropna(subset=["E_e2e_uJ"])
        re_ = ok.E_E2E / ok.E_e2e_uJ
        rl = ok.t_us_E2E / ok.lat_e2e_us
        ok = ok.assign(ratio_E=re_, ratio_t=rl)
        ok[["model_id", "family", "rep", "prec", "E_E2E", "E_e2e_uJ", "t_us_E2E", "lat_e2e_us", "ratio_E",
            "ratio_t"]].to_csv(fd / f"est_vs_meas_{ds}.csv", index=False)
        put(D + "EstRatioMin", f(re_.min(), 2))
        put(D + "EstRatioMax", f(re_.max(), 2))
        put(D + "EstRatioMed", f(re_.median(), 2))
        tr = ok[ok.family.isin(["DT", "RF"])].ratio_E
        nn = ok[ok.family.isin(["MLP", "CNN"])].ratio_E
        put(D + "EstRatioTreeMin", f(tr.min(), 2))
        put(D + "EstRatioTreeMax", f(tr.max(), 2))
        put(D + "EstRatioNnMin", f(nn.min(), 2))
        put(D + "EstRatioNnMax", f(nn.max(), 2))
        put(D + "LatRatioMin", f(rl.min(), 2))
        put(D + "LatRatioMax", f(rl.max(), 2))
        put(D + "LatRatioMed", f(rl.median(), 2))
        rho = spearmanr(ok.E_E2E, ok.E_e2e_uJ).statistic
        put(D + "EstSpearman", f(rho, 3))
        # rank changes among the 10 cheapest models
        # ---- worst latency mis-estimate (largest CNN)
        big = ok.loc[ok.lat_e2e_us.idxmax()]
        put(D + "BigCnnId", "\\texttt{" + big.model_id + "}")
        put(D + "BigCnnMeasS", f(big.t_us_E2E / 1e6, 2))
        put(D + "BigCnnEstS", f(big.lat_e2e_us / 1e6, 1))

        # ---- Pareto analysis on measured energy
        hvrows, frontrows = [], []
        for P in nprot:
            g = wide.dropna(subset=[f"f1_{P}"]).copy()
            o = np.c_[1 - g[f"f1_{P}"].values, np.log10(g.E_E2E.values), np.log10(g.flash_model_B_board.values)]
            lo, hi = o.min(0), o.max(0)
            on = (o - lo) / np.where(hi > lo, hi - lo, 1)
            fm2 = front_mask(o[:, :2])
            fm3 = front_mask(o)
            g["front2"] = fm2
            g["front3"] = fm3
            frontrows.append(g.assign(protocol=P)[["model_id", "family", "rep", "prec", "E_E2E", "flash_model_B_board",
                                                   f"f1_{P}", "front2", "front3", "protocol"]]
                             .rename(columns={f"f1_{P}": "f1"}))
            hv_all = hv3(on[fm3])
            put(D + P + "HvAll", f(hv_all, 3))
            put(D + P + "NFront", int(fm2.sum()))
            put(D + P + "NFrontThree", int(fm3.sum()))
            best = g.loc[g[f"f1_{P}"].idxmax()]
            put(D + P + "Best", f(best[f"f1_{P}"], 3))
            put(D + P + "BestId", "\\texttt{" + best.model_id + "}")
            put(D + P + "BestE", sig(best.E_E2E, 3))
            for fam in ["DT", "RF", "MLP", "CNN"]:
                m = (g.family == fam).values
                hvf = hv3(on[m][front_mask(on[m])]) if m.any() else 0
                rest = ~m
                hvx = hv_all - hv3(on[rest][front_mask(on[rest])])
                hvrows.append(dict(protocol=P, family=fam, hv=hvf, hv_excl=hvx, n_front2=int((fm2 & m).sum()),
                                   n_front3=int((fm3 & m).sum()), best_f1=g[m][f"f1_{P}"].max()))
                put(D + P + "Hv" + fam, f(hvf, 3))
                put(D + P + "HvExcl" + fam, f(max(hvx, 0), 3))
                put(D + P + "NFront" + fam, int((fm2 & m).sum()))
                put(D + P + "Best" + fam, f(g[m][f"f1_{P}"].max(), 3))
            # cheapest model within 0.01 F1 of best
            ge = g.dropna(subset=["E_e2e_uJ"])
            fe = front_mask(np.c_[1 - ge[f"f1_{P}"].values, ge.E_e2e_uJ.values])
            fm_ = front_mask(np.c_[1 - ge[f"f1_{P}"].values, ge.E_E2E.values])
            put(D + P + "EstFrontDiff", int((fe != fm_).sum()))
            put(D + P + "EstFrontN", int(fe.sum()))
            near = g[g[f"f1_{P}"] >= best[f"f1_{P}"] - 0.01].sort_values("E_E2E").iloc[0]
            put(D + P + "NearId", "\\texttt{" + near.model_id + "}")
            put(D + P + "NearE", sig(near.E_E2E, 3))
            put(D + P + "NearFOne", f(near[f"f1_{P}"], 3))
            put(D + P + "NearSaving", f(best.E_E2E / near.E_E2E, 0) if best.E_E2E > near.E_E2E else "1")
        hv = pd.DataFrame(hvrows)
        hv.to_csv(fd / f"hv_{ds}.csv", index=False)
        pd.concat(frontrows).to_csv(fd / f"fronts_{ds}.csv", index=False)
        Pl = nprot[-1] if ds == "cwru" else "P3"
        # family HV ranking P1 vs strictest disjoint protocol
        r1 = hv[hv.protocol == "P1"].set_index("family").hv
        r3 = hv[hv.protocol == Pl].set_index("family").hv
        put(D + "RankPOne", ">".join(r1.sort_values(ascending=False).index))
        put(D + "RankPLast", ">".join(r3.sort_values(ascending=False).index))
        # model-level Kendall tau of macro-F1 rankings
        for x, y in [("P1", "P2"), ("P1", "P3"), ("P2", "P3")] + ([("P1", "P4"), ("P3", "P4")] if ds == "pu" else []):
            g = wide.dropna(subset=[f"f1_{x}", f"f1_{y}"])
            put(D + "Tau" + x + y, f(kendalltau(g[f"f1_{x}"], g[f"f1_{y}"]).statistic, 2))
        # leakage gap per family (P1 best - P_last best) and median drop
        for fam in ["DT", "RF", "MLP", "CNN"]:
            g = wide[wide.family == fam]
            put(D + "Drop" + fam, f((g["f1_P1"] - g[f"f1_{Pl}"]).median(), 3))
        put(D + "DropAll", f((wide["f1_P1"] - wide[f"f1_{Pl}"]).median(), 3))
        # P1-front models that survive on the strict front
        F = pd.concat(frontrows)
        s1 = set(F[(F.protocol == "P1") & F.front2].model_id)
        s3 = set(F[(F.protocol == Pl) & F.front2].model_id)
        put(D + "FrontKept", len(s1 & s3))
        put(D + "FrontPOneN", len(s1))
        # flash range
        put(D + "FlashMinKB", f(wide.flash_model_B_board.min() / 1024, 1))
        put(D + "FlashMaxKB", f(wide.flash_model_B_board.max() / 1024, 0))

        # ---- logfix (float-only log10) campaign, if present
        lf = R / ds / "hardware" / "logfix" / "hw_measurements.csv"
        if lf.exists():
            lw, lst = energy_table(lf, [1, 2, 3], [1, 2, 3])
            lw = lw.merge(wide[["model_id", "E_E2E", "E_FRONT", "rep", "family"]], on="model_id", suffixes=("_log", ""))
            lw.to_csv(fd / f"logfix_{ds}.csv", index=False)
            put(D + "LfReps", int(lw["n_rep_E2E"].max()))
            put(D + "LfModels", len(lw))
            for rp in ["R0", "R1", "R2", "R3", "R4"]:
                g = lw[lw.rep == rp]
                if len(g):
                    put(D + "LfFront" + rp, sig(g.E_FRONT_log.median(), 3))
                    put(D + "LfSave" + rp, pct(1 - (g.E_FRONT_log / g.E_FRONT).median(), 0))
                    put(D + "LfFactor" + rp, f((g.E_FRONT / g.E_FRONT_log).median(), 2))
            put(D + "LfEtoeSaveMax", pct((1 - lw.E_E2E_log / lw.E_E2E).max(), 0))
            # anchors (R0/R1) take no logarithm: any change is the rebuilt image (code placement)
            an = lw[lw.rep.isin(["R0", "R1"])]
            if len(an):
                chg = an.E_FRONT_log / an.E_FRONT - 1
                LF_SHIFT.append(chg.abs().max())
                put(D + "LfImageShiftMax", f(100 * chg.abs().max(), 0))
                put("LfImageShiftMax", f(100 * max(LF_SHIFT), 0))
                r1 = an[an.rep == "R1"]   # R0 anchors move both ways, so only R1 gets a median change
                if len(r1):
                    s = pct((r1.E_FRONT_log / r1.E_FRONT).median() - 1, 0)
                    put(D + "LfChangeROne", s if s.startswith("$-$") else "+" + s)

        # ---- board verification
        bv = pd.read_csv(R / ds / "hardware" / "board_verify.csv")
        bv2 = pd.read_csv(R / ds / "hardware" / "board_verify_int8v2.csv")
        put(D + "VerPass", int(bv.passed.sum()))
        put(D + "VerN", len(bv))
        i8m = bv.model_id.str.contains("int8")
        put(D + "VerIntFail", int((~bv.passed.astype(bool) & i8m).sum()))
        put(D + "VerIntN", int(i8m.sum()))
        put(D + "VerIntAgreeMin", pct(bv[i8m].class_agree.min(), 0))
        put(D + "VerTwoPass", int(bv2.passed.sum()))
        put(D + "VerTwoN", len(bv2))

        # ---- cascade (pre-registered, measured)
        cj = json.loads((R / ds / "cascade_hw" / "analysis" / "summary.json").read_text())
        put(D + "CasChecks", f(cj["rq1"]["checks"], 0))
        put(D + "CasAgree", f(cj["rq1"]["agree"], 0))
        rq2 = pd.read_csv(R / ds / "cascade_hw" / "analysis" / "rq2_stage1.csv")
        rq2.to_csv(fd / f"gates_{ds}.csv", index=False)
        put(D + "GateEMin", f(rq2[rq2.gate <= 5]["mean"].min(), 1))
        put(D + "GateEMax", f(rq2[rq2.gate <= 5]["mean"].max(), 1))
        put(D + "GateRatioMin", f(rq2.ratio_meas_est.min(), 1))
        put(D + "GateRatioMax", f(rq2.ratio_meas_est.max(), 1))
        put(D + "GateCvMax", pct(rq2.cv.max(), 1))
        for _, rw in rq2.iterrows():
            put(D + "GateE" + name("G" + str(int(rw.gate))), f(rw["mean"], 1))
        d3 = cj["rq3_delta_rel_E2E"]
        put(D + "RqThreeN", len(d3))
        put(D + "RqThreeFail", sum(abs(v) > 0.02 for v in d3.values()))
        put(D + "RqThreeMin", pct(min(d3.values()), 1))
        dib = cj.get("rq3_inbundle_delta_rel", {})
        if dib:
            put(D + "RqThreeInMin", pct(min(dib.values()), 1))
            put(D + "RqThreeInMax", pct(max(dib.values()), 1))
        d4 = cj["rq4_rel_err"]
        put(D + "RqFourN", len(d4))
        put(D + "RqFourFail", sum(abs(v) > 0.03 for v in d4.values()))
        d4i = cj.get("rq4_rel_err_inbundle", {})
        if d4i:
            put(D + "RqFourInMin", pct(min(d4i.values()), 1))
            put(D + "RqFourInMax", pct(max(d4i.values()), 1))
        s6 = cj.get("rq6_saving_uJ", {})
        if s6:
            put(D + "RqSixMin", f(min(s6.values()), 1))
            put(D + "RqSixMax", f(max(s6.values()), 1))
        e8 = cj.get("rq8_E_period_fit_uJ")
        if e8:
            put(D + "RqEightFit", f(e8[0], 1))
            put(D + "RqEightLo", f(e8[1], 1))
            put(D + "RqEightHi", f(e8[2], 1))
            put(D + "RqEightModel", f(cj["rq8_E_model_per_period_uJ"], 1))
            put(D + "IncJhOneS", f(e8[0] * 3600 / 1e6, 3))
        put(D + "IdleJh", f(st["idle_med"] * 3.6, 0))
        put(D + "CasRepCv", pct(cj["quality"]["repeat_cv_median"], 2))
        for k, v in cj.get("rq7_calib_uJ", {}).items():
            put(D + "Calib" + name(*re.findall(r"[A-Za-z]+|\d+", k)), f(v, 0))
        ga = pd.read_csv(R / ds / "cascade" / "gates.csv")
        ga.to_csv(fd / f"hostgates_{ds}.csv", index=False)

    # cross-dataset transfer of rankings
    c, p = allmodels["cwru"], allmodels["pu"]
    m = c.merge(p, on="model_id", suffixes=("_c", "_p"))
    put("TauCwruPuPThree", f(kendalltau(m.f1_P3_c, m.f1_P3_p).statistic, 2))
    put("TauCwruPuPOne", f(kendalltau(m.f1_P1_c, m.f1_P1_p).statistic, 2))
    bestc = m.loc[m.f1_P3_c.idxmax()]
    rank = int((m.f1_P3_p > bestc.f1_P3_p).sum()) + 1
    put("CwruBestRankOnPu", rank)
    put("NModelsBoth", len(m))
    # cascade savings and calibration payback (CWRU stream model = most accurate P2 model)
    cw = allmodels["cwru"].set_index("model_id")
    e2 = cw.loc["rf-r2-t25d10-fp32", "E_E2E"]
    put("CasCwSaving", f(e2 / cr["E_pi1_stream"]["cwru_G5"], 1))
    put("CasPuSaving", f(cr["pu_rule_gate"]["E_model_alone"] / cr["E_pi1_stream"]["pu_G6"], 1))
    cal = json.loads((R / "cwru" / "cascade_hw" / "analysis" / "summary.json").read_text())["rq7_calib_uJ"]["G5 n=16"]
    put("CasCwPayback", f(cal / (e2 - cr["E_pi1_stream"]["cwru_G5"]), 0))
    tot = summary["cwru"]["n_windows"] + summary["pu"]["n_windows"]
    put("TotWindows", f(tot, 0))
    put("TotRejected", f(summary["cwru"]["n_rejected"] + summary["pu"]["n_rejected"], 0))
    put("TotSessions", summary["cwru"]["sessions"] + summary["pu"]["sessions"])

    out = a.out / "numbers.tex"
    with out.open("w") as fh:
        fh.write("% Generated by scripts/make_numbers.py -- do not edit by hand.\n")
        for k in sorted(MACROS):
            fh.write(f"\\newcommand{{\\Num{k}}}{{{MACROS[k]}}}\n")
    print(f"wrote {out} ({len(MACROS)} macros)")


if __name__ == "__main__":
    main()
