#!/usr/bin/env python3
"""Generate paper/numbers.tex (LaTeX macros) and copy figures into paper/figs/, from the script-written results.

Nothing in the paper is typed by hand: main.tex and results_section.tex only use the macros defined here.
Usage: python paper/make_numbers.py
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
RES = REPO / "results"
PAPER = REPO / "paper"


def pct(x: float, d: int = 1) -> str:
    return f"{100 * x:.{d}f}\\,\\%"


def sci(x: float) -> str:
    """Three significant figures; below 0.001 as m x 10^e in math mode."""
    if x == 0 or abs(x) >= 1e-3:
        return f"{x:.3g}"
    mant, exp = f"{x:.2e}".split("e")
    mant = mant.rstrip("0").rstrip(".")
    return f"${mant}\\times10^{{{int(exp)}}}$"


def f3(x: float) -> str:
    return f"{x:.3f}"


def build() -> dict[str, str]:
    h = json.loads((RES / "headline_numbers.json").read_text())
    counts = pd.read_csv(RES / "data_counts.csv")
    sc = pd.read_csv(RES / "split_counts.csv")
    summ = pd.read_csv(RES / "host_summary.csv", keep_default_na=False)
    costs = pd.read_csv(RES / "prehw_costs.csv")
    hv = pd.read_csv(RES / "hv_table.csv")
    tau = pd.read_csv(RES / "kendall_tau.csv")
    par = pd.read_csv(RES / "vmcu" / "vmcu_parity.csv")
    m: dict[str, str] = {}
    reg = pd.read_csv(REPO / "models" / "registry.csv")
    m["NumConfigs"] = str(len(reg))
    m["NumSplits"] = "8"
    m["NumSeedsMax"] = str(h["seeds_per_model"]["max"])
    m["NumSeedsMin"] = str(h["seeds_per_model"]["min"])
    m["NumRuns"] = f"{h['n_runs']:,}".replace(",", "{,}")
    m["NumTV"] = "100"
    m["NumWindows"] = f"{int(counts.n_windows_stride512.sum()):,}".replace(",", "{,}")
    m["NumWindowsNO"] = f"{int(counts.n_windows_nonoverlap.sum()):,}".replace(",", "{,}")
    m["NumSignalS"] = f"{counts.duration_s.sum():.0f}"
    leak = {}
    readme = (REPO / "splits" / "README.md").read_text()
    for ln in readme.splitlines():
        if ln.startswith("| P1/0 |"):
            leak["P1"] = ln.split("|")[5].strip().replace("%", "\\,\\%")
    m["NumPOneLeakPct"] = leak.get("P1", "?")
    for p, name in (("P1", "POne"), ("P2", "PTwo"), ("P3", "PThree")):
        s = sc[(sc.protocol == p) & (sc.fold == 0)].groupby("set").n.sum()
        m[f"Num{name}Train"], m[f"Num{name}Val"], m[f"Num{name}Test"] = (f"{int(s[k]):,}".replace(",", "{,}")
                                                                           for k in ("train", "val", "test"))
    m["NumPactmW"] = f"{h['energy_model']['P_active_mW']:.2f}"
    rr = par[par.vector == -1].set_index("rep").max_abs_err
    m["NumParityMax"] = f"\\num{{{rr.max():.1e}}}"
    for rep in ("r1", "r2", "r3", "r4"):
        if rep in rr:
            m[f"NumParity{rep.upper()}"] = f"\\num{{{rr[rep]:.1e}}}"
    fe = h["frontend_cycles"]
    for rep, key in (("r0", "RZero"), ("r1", "ROne"), ("r2", "RTwo"), ("r3", "RThree"), ("r4", "RFour")):
        if rep in fe:
            m[f"NumFe{key}Kcyc"] = f"{fe[rep] / 1e3:.1f}"
            m[f"NumFe{key}Us"] = f"{fe[rep] / 64:.0f}"
    m["NumTreeInfMax"] = f"{h['tree_inf_cycles']['max']:,}".replace(",", "{,}")
    m["NumTreeInfMin"] = f"{h['tree_inf_cycles']['min']:,}".replace(",", "{,}")
    m["NumTreeFeShareMin"] = pct(h["tree_fe_share"]["min"], 1)

    clean = summ[summ.snr_db == ""].copy()
    clean = clean[~clean.model_id.str.endswith("int8ref")]
    for p, name in (("P1", "POne"), ("P2", "PTwo"), ("P3", "PThree")):
        c = clean[clean.protocol == p].sort_values("f1_macro_mean")
        if len(c):
            m[f"Num{name}Best"] = f3(c.f1_macro_mean.iloc[-1])
            m[f"Num{name}BestModel"] = "\\texttt{" + c.model_id.iloc[-1] + "}"
    fam = {"dt": "DT", "rf": "RF", "mlp": "MLP", "cnn": "CNN"}
    clean["fam"] = clean.model_id.str.split("-").str[0].map(fam)
    for p, name in (("P1", "POne"), ("P2", "PTwo"), ("P3", "PThree")):
        for f, g in clean[clean.protocol == p].groupby("fam"):
            b = g.sort_values("f1_macro_mean").iloc[-1]
            m[f"Num{name}{f}Best"] = f3(b.f1_macro_mean)
            m[f"Num{name}{f}BestStd"] = f3(b.f1_macro_std)
            m[f"Num{name}{f}BestModel"] = "\\texttt{" + b.model_id + "}"
            m[f"Num{name}{f}Median"] = f3(g.f1_macro_mean.median())
    p3std = [float(m[f"NumPThree{f}BestStd"]) for f in ("DT", "RF", "MLP", "CNN") if f"NumPThree{f}BestStd" in m]
    if p3std:
        m["NumPThreeBestStdMax"] = f3(max(p3std))

    def tau_of(a, b, energy="e2e"):
        r = tau[(tau.energy == energy) & (tau.a == a) & (tau.b == b)]
        return f"{r.tau.iloc[0]:.2f}" if len(r) else "?"
    m["NumTauFamPOnePThree"] = tau_of("P1", "P3")
    m["NumTauFamPOnePTwo"] = tau_of("P1", "P2")
    m["NumTauFamPTwoPThree"] = tau_of("P2", "P3")
    m["NumTauModelPOnePThree"] = tau_of("P1", "P3", "model_f1")
    m["NumTauModelPOnePTwo"] = tau_of("P1", "P2", "model_f1")
    m["NumTauModelPTwoPThree"] = tau_of("P2", "P3", "model_f1")
    for p, name in (("P1", "POne"), ("P2", "PTwo"), ("P3", "PThree")):
        r = tau[(tau.energy == "e2e") & (tau.a == p)]
        rk = tau[(tau.energy == "e2e") & ((tau.a == p) | (tau.b == p))]
        if len(rk):
            row = rk.iloc[0]
            m[f"NumRank{name}"] = (row.rank_a if row.a == p else row.rank_b).replace(">", " $>$ ")
    sp = h.get("cmsis_speedup", {})
    if sp:
        v = np.array(list(sp.values()))
        m["NumCmsisMin"], m["NumCmsisMax"] = f"{v.min():.1f}", f"{v.max():.1f}"
        m["NumCmsisMedian"] = f"{np.median(v):.1f}"
    fp = h.get("fp32_over_int8_cycles", {})
    cnnfp = [v for k, v in fp.items() if k.startswith("cnn")]
    if cnnfp:
        m["NumCnnFpOverIntMin"], m["NumCnnFpOverIntMax"] = f"{min(cnnfp):.1f}", f"{max(cnnfp):.1f}"
    m["NumVerifyMin"] = pct(h["verify_class_agree_min"], 0)
    m["NumRamMinB"] = f"{h['ram_peak_B']['min']:,}".replace(",", "{,}")
    m["NumRamMaxKB"] = f"{h['ram_peak_B']['max'] / 1024:.0f}"
    m["NumFlashMaxKB"] = f"{h['flash_model_B']['max'] / 1024:.0f}"
    m["NumNModelsCost"] = str(h["n_models_with_costs"])
    # frontier membership counts
    for p, name in (("P1", "POne"), ("P2", "PTwo"), ("P3", "PThree")):
        for e, en in (("inf", "Inf"), ("e2e", "Etoe")):
            k = f"{p}_{e}"
            if k in h["pareto"]:
                m[f"NumFront{name}{en}"] = str(len(h["pareto"][k]["frontier"]))
                m[f"NumRobust{name}{en}"] = str(len(h["pareto"][k]["robust_frontier"]))
    for r in hv.itertuples(index=False):
        name = {"P1": "POne", "P2": "PTwo", "P3": "PThree"}[r.protocol]
        en = "Inf" if r.energy == "inf" else "Etoe"
        m[f"NumHv{name}{en}{r.family}"] = f"{r.hv_family:.3f}"
        m[f"NumHvExcl{name}{en}{r.family}"] = f"{r.hv_exclusive:.3f}"
        m[f"NumNFront{name}{en}{r.family}"] = str(r.n_frontier)
    # RF soft vs hard voting (pitfall 2)
    dis, df1 = [], []
    for f in (RES / "host").glob("rf-*.json"):
        r = json.loads(f.read_text())
        t = r.get("train", {})
        if "soft_hard_disagree" in t:
            dis.append(t["soft_hard_disagree"] / r["n_test"])
            df1.append(abs(r["f1_macro"] - t["f1_soft_voting"]))
    if dis:
        m["NumRfVoteDisMean"] = pct(float(np.mean(dis)), 1)
        m["NumRfVoteDisMax"] = pct(float(np.max(dis)), 1)
        m["NumRfVoteFOneMax"] = f"{max(df1):.2f}"
        m["NumRfVoteRuns"] = str(len(dis))
    # TFLite default vs reference kernels (pitfall 3)
    rk = RES / "ref_kernel_check.csv"
    if rk.exists():
        k = pd.read_csv(rk)
        cnn = k[k.model_id.str.startswith("cnn") & k.model_id.str.contains("int8")]
        m["NumRefAgreeMin"] = pct(float(k.class_agree.min()), 1)
        m["NumRefFOneMax"] = f"{float(k.f1_diff.abs().max()):.3f}"
        m["NumRefMlpAgree"] = pct(float(k[k.model_id.str.startswith("mlp")].class_agree.min()), 0)
        if len(cnn):
            m["NumRefCnnAgreeMin"] = pct(float(cnn.class_agree.min()), 1)
    # real-board builds (arduino-cli, arduino:mbed_nano 4.6.0)
    fb = RES / "fw_build.csv"
    if fb.exists():
        b = pd.read_csv(fb)
        g = b.groupby("bundle").agg(links=("links", "min"), flash=("bundle_flash_B", "first"),
                                    ram=("bundle_ram_static_B", "first"), n=("model_id", "size"))
        m["NumFwBundles"] = str(len(g))
        m["NumFwBundlesLink"] = str(int(g.links.sum()))
        m["NumFwModels"] = str(int(b.links.sum()))
        m["NumFwFlashMaxKB"] = f"{g.flash.max() / 1024:.0f}"
        m["NumFwRamMaxKB"] = f"{g.ram.max() / 1024:.0f}"
    # cycle-model band
    bw = (costs.e2e_cyc_hi - costs.e2e_cyc_lo) / costs.e2e_cyc
    m["NumBandMin"], m["NumBandMax"] = pct(float(bw.min()), 0), pct(float(bw.max()), 0)
    # SNR: best P2 model per family at 0 dB
    snr0 = summ[(summ.snr_db == "0") & (summ.protocol == "P2")].copy()
    if len(snr0):
        snr0["fam"] = snr0.model_id.str.split("-").str[0].map(fam)
        for f, g in snr0.groupby("fam"):
            m[f"NumSnrZeroPTwo{f}"] = f3(g.f1_macro_mean.max())
    # R4 (physics-informed envelope front end) against the best R1-R3 configuration of the same family
    clean["rep"] = clean.model_id.str.split("-").str[1]
    for p, name in (("P1", "POne"), ("P2", "PTwo"), ("P3", "PThree")):
        cp = clean[clean.protocol == p]
        for f in ("DT", "RF", "MLP"):
            g = cp[cp.fam == f]
            r4, base = g[g.rep == "r4"], g[g.rep.isin(["r1", "r2", "r3"])]
            if len(r4) and len(base):
                b4, bb = r4.sort_values("f1_macro_mean").iloc[-1], base.sort_values("f1_macro_mean").iloc[-1]
                m[f"Num{name}{f}RFourBest"], m[f"Num{name}{f}RFourBestStd"] = f3(b4.f1_macro_mean), f3(b4.f1_macro_std)
                m[f"Num{name}{f}RFourBestModel"] = "\\texttt{" + b4.model_id + "}"
                m[f"Num{name}{f}BaseBest"], m[f"Num{name}{f}BaseBestStd"] = f3(bb.f1_macro_mean), f3(bb.f1_macro_std)
                m[f"Num{name}{f}BaseBestModel"] = "\\texttt{" + bb.model_id + "}"
        r4all = cp[cp.rep == "r4"]
        if len(r4all):
            b = r4all.sort_values("f1_macro_mean").iloc[-1]
            m[f"Num{name}RFourBest"], m[f"Num{name}RFourBestModel"] = f3(b.f1_macro_mean), "\\texttt{" + b.model_id + "}"
        nonr4 = cp[cp.rep != "r4"]
        if len(nonr4):
            b = nonr4.sort_values("f1_macro_mean").iloc[-1]
            m[f"Num{name}NoRFourBest"], m[f"Num{name}NoRFourBestModel"] = f3(b.f1_macro_mean), "\\texttt{" + b.model_id + "}"
    # the window mean as a recording fingerprint (why R4 drops it): ablation runs and the z-score of the outlier
    ab = RES / "ablation_r4_mean.csv"
    if ab.exists():
        a = pd.read_csv(ab)
        a = a[(a.protocol == "P2") & (a.fold == 0) & a.model_id.str.startswith("mlp")]
        new = []
        for r in a.itertuples(index=False):
            f = RES / "host" / f"{r.model_id}__P2__f0__s{r.seed}.json"
            if f.exists():
                new.append(json.loads(f.read_text())["f1_macro"])
        m["NumAblMeanWith"] = f3(a.f1_macro_with_mean.mean())
        if new:
            m["NumAblMeanWithout"] = f3(float(np.mean(new)))
    try:
        import sys
        sys.path.insert(0, str(REPO / "ml"))
        import features as FE
        from splits import load_split
        F1 = np.asarray(FE.cached("r1"))
        sp = load_split("P2", 0)
        mu, sd = F1[sp["train"], 0].mean(), F1[sp["train"], 0].std()
        m["NumMeanZMax"] = f"{np.abs((F1[sp['test'], 0] - mu) / sd).max():.1f}"
    except Exception:  # noqa: BLE001 - data not present (e.g. CI): leave the macro out
        pass
    # virtual measurement campaign (tools/vrig/campaign.py)
    vs = RES / "vrig" / "summary.json"
    if vs.exists():
        v = json.loads(vs.read_text())
        m["NumVrigHours"] = f"{v['campaign_hours']:.0f}"
        m["NumVrigHoursLat"] = f"{v['campaign_hours_lat']:.0f}"
        m["NumVrigHoursAdaptive"] = f"{v['campaign_hours_adaptive_lat']:.0f}"
        m["NumVrigCvMedian"] = f"{v['cv_median_pct_1ohm']:.2f}\\,\\%"
        m["NumVrigCvMaxPNinetyFive"] = f"{v['cv_p95_max_pct_1ohm']:.1f}\\,\\%"
        m["NumVrigBiasMax"] = f"{v['bias_abs_max_pct_1ohm']:.1f}\\,\\%"
        m["NumVrigMinDetect"] = f"{v['min_detectable_power_diff_pct']:.1f}\\,\\%"
        sens = pd.read_csv(RES / "vrig" / "sensitivity.csv")
        sd_ = sens[(sens.sweep == "block_drift") & (sens.shunt_ohm == 1.0)].sort_values("x")
        below, above = sd_[sd_.cv_p95_pct < 3.0].x.max(), sd_[sd_.cv_p95_pct >= 3.0].x.min()
        m["NumVrigDriftGateLo"], m["NumVrigDriftGateHi"] = f"{below:g}", f"{above:g}"
        m["NumVrigSlowModel"] = "\\texttt{" + v["slowest_models"][0]["model_id"] + "}"
        m["NumVrigSlowHours"] = f"{v['slowest_models'][0]['hours']:.1f}"
        m["NumVrigModels"] = str(v["n_models"])
        a_ = v["assumptions"]
        m["NumVrigIdleMa"] = f"{a_['i_idle_a'] * 1e3:g}"
        m["NumVrigActiveMa"] = f"{a_['i_active_a'] * 1e3:g}"
        m["NumVrigNoiseMa"] = f"{a_['noise_a'] * 1e3:g}"
    fs = h.get("f_star_hz", {})
    if fs:
        chip = {k.split("|")[0]: v for k, v in fs.items() if k.endswith("|chip")}
        lo, hi = min(chip, key=chip.get), max(chip, key=chip.get)
        m["NumFstarLoModel"], m["NumFstarLo"] = "\\texttt{" + lo + "}", sci(chip[lo])
        m["NumFstarHiModel"], m["NumFstarHi"] = "\\texttt{" + hi + "}", sci(chip[hi])
    # the most accurate P3 CNN against its int8 (PTQ) twin: accuracy and estimated end-to-end energy
    clean = summ[(summ.snr_db == "") & (summ.protocol == "P3")].set_index("model_id")
    cnn = clean[clean.index.str.startswith("cnn-")]
    best = cnn.f1_macro_mean.idxmax()
    twin = best.replace("-fp32", "-int8")
    E = costs.set_index("model_id").E_e2e_uJ
    m["NumCnnTwinModel"] = "\\texttt{" + twin + "}"
    m["NumCnnTwinFOne"] = f3(cnn.f1_macro_mean[twin])
    m["NumCnnTwinRatio"] = f"{E[best] / E[twin]:.0f}"
    # worked example of the rubric, with the selector's own rule (tools/selector/template.html, evaluate())
    em = h["energy_model"]
    ps, batt, year = em["P_sleep_chip_uW"] * 1e-6, em["battery_J"], 365.25 * 86400
    cst = costs.set_index("model_id")

    def pick(ram_kb, flash_kb, rate, life_y):
        emax = (batt / (life_y * year) - ps) / rate * 1e6
        ok = cst[(cst.ram_peak_B <= ram_kb * 1024) & (cst.flash_model_B <= flash_kb * 1024)
                 & (cst.lat_e2e_us * 1e-6 <= 1 / rate) & (cst.E_e2e_uJ <= emax)].index.intersection(clean.index)
        f1p3 = clean.f1_macro_mean[ok]
        best_id = min(ok, key=lambda i: (-round(f1p3[i], 6), cst.E_e2e_uJ[i]))
        life = batt / (rate * cst.E_e2e_uJ[best_id] * 1e-6 + ps) / year
        return emax, best_id, f1p3[best_id], cst.E_e2e_uJ[best_id], life

    for tag, rate, life_y in (("Two", 1.0, 2), ("Five", 1.0, 5), ("Min", 1 / 60, 2)):
        emax, bid, f1v, ev, lv = pick(64, 256, rate, life_y)
        m[f"NumEx{tag}Budget"] = f"{emax:.1f}" if emax < 100 else f"{emax:,.0f}".replace(",", "{,}")
        m[f"NumEx{tag}Model"] = "\\texttt{" + bid + "}"
        m[f"NumEx{tag}FOne"] = f3(f1v)
        m[f"NumEx{tag}Energy"] = f"{ev:.1f}"
        m[f"NumEx{tag}Life"] = f"{lv:.1f}"
    m["NumExSleepLife"] = f"{batt / ps / year:.1f}"
    return m


def cost_table() -> str:
    """LaTeX table: per (family, input, precision) group, the configuration with the best P3 macro-F1."""
    c = pd.read_csv(RES / "prehw_costs.csv")
    summ = pd.read_csv(RES / "host_summary.csv", keep_default_na=False)
    clean = summ[summ.snr_db == ""]
    f1 = clean.pivot(index="model_id", columns="protocol", values="f1_macro_mean")
    c = c.join(f1, on="model_id")
    c["grp"] = c.family + "-" + c.input_rep + "-" + c.precision
    rows = []
    for g, d in c.groupby("grp", sort=False):
        b = d.sort_values("P3" if "P3" in d and d["P3"].notna().any() else "P2").iloc[-1]
        rows.append(b)
    T = pd.DataFrame(rows).sort_values("e2e_cyc")
    fmt = lambda v, d=3: "--" if pd.isna(v) else f"{v:.{d}f}"  # noqa: E731
    lines = ["\\begin{table*}[t]", "\\centering",
             "\\caption{Pre-hardware cost on the virtual Cortex-M4 (64\\,MHz, estimated) and host macro-F1 of the best "
             "P3 configuration in each family/input/precision group.}", "\\label{tab:costs}",
             "\\setlength{\\tabcolsep}{3.5pt}", "\\begin{tabular}{lrrrrrrccc}", "\\toprule",
             "Configuration & Flash (kB) & Peak RAM (kB) & Front end (kcyc) & Inference (kcyc) & Latency (ms) & "
             "Energy ($\\mu$J) & P1 F1 & P2 F1 & P3 F1\\\\", "\\midrule"]
    for r in T.itertuples(index=False):
        lines.append(f"\\texttt{{{r.model_id}}} & {r.flash_model_B / 1024:.1f} & {r.ram_peak_B / 1024:.1f} & "
                     f"{r.fe_cyc_med / 1e3:.1f} & {r.inf_cyc_med / 1e3:.1f} & {r.lat_e2e_us / 1e3:.2f} & "
                     f"{r.E_e2e_uJ:.1f} & {fmt(getattr(r, 'P1', np.nan))} & {fmt(getattr(r, 'P2', np.nan))} & "
                     f"{fmt(getattr(r, 'P3', np.nan))}\\\\")
    lines += ["\\bottomrule", "\\end{tabular}", "\\end{table*}", ""]
    return "\n".join(lines)


def main() -> None:
    (PAPER / "tab_costs.tex").write_text(cost_table())
    m = build()
    lines = ["% numbers.tex - GENERATED by paper/make_numbers.py from results/; do not edit."]
    words = {"0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four", "5": "Five", "6": "Six", "7": "Seven",
             "8": "Eight", "9": "Nine"}
    for k, v in sorted(m.items()):
        k = "".join(words.get(ch, ch) for ch in k)          # LaTeX control words are letters only
        assert k.isalpha(), k
        lines.append(f"\\newcommand{{\\{k}}}{{{v}}}")
    (PAPER / "numbers.tex").write_text("\n".join(lines) + "\n")
    figs = PAPER / "figs"
    figs.mkdir(exist_ok=True)
    for f in (RES / "figs").glob("*.pdf"):
        shutil.copy(f, figs / f.name)
    for f in (RES / "demo").glob("*.png"):          # terminal captures of the two demos (Fig. demo)
        shutil.copy(f, figs / f.name)
    print(f"{len(m)} macros -> paper/numbers.tex; figures copied to paper/figs/")


if __name__ == "__main__":
    main()
