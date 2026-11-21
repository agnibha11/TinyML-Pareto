#!/usr/bin/env python3
"""Analysis of the measured cascade campaign (docs/cascade_preregistration.md §6; PLAN B7). `ml/cascade.py --hw`.

Input: results/<ds>/cascade_hw/{cascade_measurements,gate_verify}.csv (sessions 81-83, continuations 181-183), the
energy campaign's results/<ds>/hardware/hw_measurements.csv (stage-2 E2E and FRONT), the host design study's stage-1
cost table (results/rig/cascade_design/costs.csv) and the host gate rates (handoff/cascade/<ds>/summary.json).

Energy per call of a timed window: E = (P_mean - P_idle) * t_window / N  (incremental, above the board's idle).
Board-level energy (P_mean * t / N) is reported only for DUTY/SLEEP (RQ8), kept apart from incremental energy.

Idle baseline (--idle):
  window   P_idle_uW as recorded: the accepted IDLE window immediately before the block (CONTRACT §7, the definition
           the energy campaign uses). Default.
  session  the mean of all accepted IDLE windows of the session (7 per session). A single 10 s IDLE window can sit in
           one of the board's USB idle dips (session 81's S2 idle read 18.52 mW against ~18.75 mW typical), which
           shifts every energy of that block by the same amount; averaging over the session removes that.
The stage-2 E2E values from the energy campaign are recomputed with the same rule, so both sides of RQ3/RQ4 use one
baseline. Every output table carries an `idle` column; `idle_sensitivity.csv` puts the two side by side.

Per condition and repeat: the mean of the accepted windows (one per stored input; the drift anchors count as extra
windows of G1 input 0, averaged per input first). A condition over stored inputs with fewer than 3 accepted inputs in
a repeat is missing, not imputed. Over repeats: the mean and a 95 % t-interval (n = 3, t = 4.303).
Differences of quantities measured in different sessions (RQ3: CASCADE, GATE and the energy campaign's E2E) combine
the standard errors in quadrature; paired quantities (RQ6: CASCADE m=1 vs m=2, same session) use the per-repeat
differences.

Outputs: results/<ds>/cascade_hw/analysis/*.csv and summary.json (local, not committed).

Usage: python ml/cascade.py --hw [--dataset cwru|pu|both] [--idle window|session|both]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
T975_2 = 4.302652729911275                   # t(0.975, df = 2)
F_CPU, P_ACT_MW = 64e6, 9.984                # chip-level energy model (ml/stats.py)
E_BATTERY_J = 0.225 * 3600 * 3.0             # CR2032, as in ml/stats.py
STREAM_LEN = 100
CASCADE_SESSIONS = {81: 1, 82: 2, 83: 3, 181: 1, 182: 2, 183: 3}
# gate -> (front end, detector) in the design study's cost table, and the host-rate key in handoff summary.json
GATE_COST = {1: ("fxr1", "tree2"), 2: ("fxr1", "mahal"), 3: ("fxr1", "either"), 4: ("fxr1", "either"),
             5: ("fxr1", "mahal"), 6: ("hyb_r2", "mahal")}
GATE_NAME = {1: "G1 sup", 2: "G2 healthy", 3: "G3 either", 4: "G4 either2", 5: "G5 selfcal", 6: "G6 selfcal_r2"}
HOST_KEY = {1: "sup", 2: "healthy", 3: "either", 4: "either2", 5: "selfcal32", 6: "selfcal_r2"}
STREAMS = ("S0", "S2", "S10", "S100", "ST")
STREAM_MODEL = {"cwru": "rf-r2-t25d10-fp32", "pu": "mlp-r4-h32-fp32"}   # tools/measure_cascade.py
FXFE_REGION = {0: "FX_R1", 1: "HYB_R2", 2: "HYB_R4"}
KEY = ["mode", "gate", "model_id", "stream", "arg"]


# ---------------------------------------------------------------------------------------------------------- helpers
def ci(x: pd.Series) -> pd.Series:
    x = x.dropna()
    n = len(x)
    m = x.mean()
    h = T975_2 * x.std(ddof=1) / np.sqrt(n) if n == 3 else np.nan
    return pd.Series({"mean": m, "ci_lo": m - h, "ci_hi": m + h, "se": x.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan,
                      "cv": x.std(ddof=1) / abs(m) if n > 1 and m else np.nan, "n_rep": n})


def with_idle(d: pd.DataFrame, idle: str) -> pd.DataFrame:
    """Adds P_idle (chosen baseline), E (incremental µJ per call) and E_board (board-level µJ per call)."""
    d = d.copy()
    acc_idle = d[(d["mode"] == "IDLE") & (d.rejected == 0)]
    sess_idle = acc_idle.groupby("session").P_mean_uW.mean()
    d["P_idle_sess"] = d.session.map(sess_idle)
    if idle == "window":
        d["P_idle"] = d.P_idle_uW.fillna(d.P_idle_sess)      # empty only when that IDLE was rejected
    else:
        d["P_idle"] = d.P_idle_sess
    t = d.t_window_us_logger / 1e6
    n = d.N.where(d.N > 0)
    d["E"] = (d.P_mean_uW - d.P_idle) * t / n
    d["E_board"] = d.P_mean_uW * t / n
    return d


def load_cascade(ds: str, idle: str) -> pd.DataFrame:
    d = pd.read_csv(REPO / "results" / ds / "cascade_hw" / "cascade_measurements.csv")
    d = d[d.session.isin(CASCADE_SESSIONS)].copy()
    d["rep"] = d.session.map(CASCADE_SESSIONS)
    d = with_idle(d, idle)
    d = d[d.rejected == 0]
    for c in ("gate", "stream", "arg", "input_j"):
        d[c] = d[c].fillna(-1).astype(int)
    d["model_id"] = d.model_id.fillna("")
    return d


def per_repeat(d: pd.DataFrame, col: str = "E", min_inputs: int = 3) -> pd.DataFrame:
    """Mean per condition and repeat: per stored input first, then over inputs (missing below min_inputs)."""
    by_in = d.groupby(KEY + ["rep", "input_j"])[col].mean().reset_index()
    g = by_in.groupby(KEY + ["rep"])
    r = g[col].mean().to_frame(col)
    r["n_inputs"] = g.input_j.apply(lambda s: int((s >= 0).sum()))
    over_inputs = r.index.get_level_values("mode").isin(["GATE", "CASCADE", "FXFE", "FRONT"])
    r.loc[over_inputs & (r.n_inputs < min_inputs), col] = np.nan
    return r.reset_index()


def over_repeats(r: pd.DataFrame, col: str = "E") -> pd.DataFrame:
    return r.groupby(KEY)[col].apply(ci).unstack().reset_index()


def e2e_campaign(ds: str, idle: str) -> pd.DataFrame:
    """Stage-2 E2E and FRONT from the energy campaign, per model: mean / CI over repeats 1-3 (int8: 5-7)."""
    d = pd.read_csv(REPO / "results" / ds / "hardware" / "hw_measurements.csv", low_memory=False)
    d = with_idle(d, idle)
    d = d[(d.rejected == 0) & d["mode"].isin(["E2E", "FRONT", "RUN"])].copy()
    d["rep"] = (d.session % 100) // 10
    int8 = d.model_id.str.contains("int8")
    d = d[(~int8 & d.rep.isin([1, 2, 3])) | (int8 & d.rep.isin([5, 6, 7]))]
    r = d.groupby(["model_id", "mode", "rep", "input_j"]).E.mean().groupby(["model_id", "mode", "rep"]).mean()
    return r.groupby(["model_id", "mode"]).apply(ci).unstack()


# ------------------------------------------------------------------------------------------------------ RQ analyses
def rq1(ds: str) -> pd.DataFrame:
    v = pd.read_csv(REPO / "results" / ds / "cascade_hw" / "gate_verify.csv")
    v["calib_n"] = v.calib_n.fillna(-1).astype(int)
    g = v.groupby(["session", "kind", "gate", "calib_n"])
    out = g.agg(n=("agree", "size"), agree=("agree", "sum"), explained=("explained", "sum"),
                min_margin_rel=("margin_rel", "min"), max_fx_rel_err=("fx_max_rel_err", "max"),
                max_d_rel_err=("d_rel_err", "max")).reset_index()
    out["disagree_unexplained"] = out.n - out.agree - out.explained
    return out


def rq2(rep: pd.DataFrame, d: pd.DataFrame, costs: pd.DataFrame) -> pd.DataFrame:
    g = rep[rep["mode"] == "GATE"]
    s = over_repeats(g)
    by_in = d[d["mode"] == "GATE"].groupby(["gate", "input_j"]).E.mean().unstack()
    s["input_min"] = s.gate.map(by_in.min(axis=1))
    s["input_max"] = s.gate.map(by_in.max(axis=1))
    s["input_spread_rel"] = (s.input_max - s.input_min) / s["mean"]
    c = costs.set_index(["front_end", "detector"])
    s["name"] = s.gate.map(GATE_NAME)
    s["E1_est_uJ"] = [c.loc[GATE_COST[k], "E1_est_uJ"] for k in s.gate]
    s["E1_hwcal_uJ"] = [c.loc[GATE_COST[k], "E1_hwcal_uJ"] for k in s.gate]
    s["ratio_meas_est"] = s["mean"] / s.E1_est_uJ
    s["ratio_meas_hwcal"] = s["mean"] / s.E1_hwcal_uJ
    return s[["gate", "name", "mean", "ci_lo", "ci_hi", "cv", "n_rep", "input_min", "input_max", "input_spread_rel",
              "E1_est_uJ", "ratio_meas_est", "E1_hwcal_uJ", "ratio_meas_hwcal"]]


def e1_per_rep(rep: pd.DataFrame) -> pd.DataFrame:
    return rep[rep["mode"] == "GATE"].set_index(["gate", "rep"]).E


def inbundle_e2e(rep: pd.DataFrame, e2e: pd.DataFrame) -> dict[str, float]:
    """Stage-2 cost on the cascade image itself: its S6 FRONT + the energy campaign's RUN (inference only).

    The float front ends run 1-8 % cheaper on the cascade bundle than on the energy-campaign bundles (code layout:
    both the time per call and the active power change; idle is unchanged), so the campaign's E2E carries a
    bundle systematic. Only the models with an S6 FRONT condition (R2/R4) have this value."""
    fr = over_repeats(rep[rep["mode"] == "FRONT"]).set_index("model_id")["mean"]
    return {m: float(fr[m] + e2e.loc[(m, "RUN"), "mean"]) for m in fr.index if (m, "RUN") in e2e.index}


def rq3_rq6(rep: pd.DataFrame, e2e: pd.DataFrame, d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    e1 = e1_per_rep(rep)
    inb = inbundle_e2e(rep, e2e)
    c = rep[rep["mode"] == "CASCADE"]
    rows, rows6 = [], []
    for (gate, model), x in c.groupby(["gate", "model_id"]):
        on = x[x.arg == 1].set_index("rep").E
        off = x[x.arg == 0].set_index("rep").E
        reuse = x[x.arg == 2].set_index("rep").E
        g1 = e1.xs(gate, level="gate")
        e2 = e2e.loc[(model, "E2E")] if (model, "E2E") in e2e.index else None
        if e2 is None:
            continue
        on_s, g_s = ci(on), ci(g1)
        delta = on_s["mean"] - g_s["mean"] - e2["mean"]
        se = np.sqrt(on_s.se ** 2 + g_s.se ** 2 + e2.se ** 2)
        esc0 = d[(d["mode"] == "CASCADE") & (d.model_id == model) & (d.arg == 0)]
        rows.append({"gate": gate, "model_id": model, "E_cascade_on": on_s["mean"], "E_cascade_on_cv": on_s.cv,
                     "E_cascade_off": ci(off)["mean"], "esc_rate_off": float(esc0.escalations.sum() / esc0.N.sum()),
                     "E1_gate": g_s["mean"], "E2E_model": e2["mean"], "E2E_cv": e2.cv,
                     "delta_uJ": delta, "delta_ci_lo": delta - T975_2 * se, "delta_ci_hi": delta + T975_2 * se,
                     "delta_rel_E2E": delta / e2["mean"], "rq3_pass_2pct": abs(delta / e2["mean"]) <= 0.02,
                     "E2E_inbundle": inb.get(model, np.nan),
                     "delta_inbundle_uJ": on_s["mean"] - g_s["mean"] - inb.get(model, np.nan),
                     "delta_inbundle_rel": (on_s["mean"] - g_s["mean"] - inb.get(model, np.nan))
                     / inb.get(model, np.nan)})
        if len(reuse):
            sav = (on - reuse).dropna()
            s = ci(sav)
            rep_ = model.split("-")[1]
            fe = e2e.loc[(model, "FRONT"), "mean"] if (model, "FRONT") in e2e.index else np.nan
            r1 = e2e.loc[("dt-r1-d4-fp32", "FRONT"), "mean"] if ("dt-r1-d4-fp32", "FRONT") in e2e.index else np.nan
            whole_r2 = gate == 6 and rep_ == "r2"
            expect = fe if whole_r2 else r1
            rows6.append({"gate": gate, "model_id": model, "rep": rep_, "reused": "whole R2 front end" if whole_r2
                          else "R1 part", "E_on": on_s["mean"], "E_reuse": ci(reuse)["mean"],
                          "saving_uJ": s["mean"], "saving_ci_lo": s.ci_lo, "saving_ci_hi": s.ci_hi,
                          "saving_rel_E2E": s["mean"] / e2["mean"], "model_FRONT_uJ": fe, "float_R1_FRONT_uJ": r1,
                          "expected_saving_uJ": expect, "saving_over_expected": s["mean"] / expect})
    return pd.DataFrame(rows), pd.DataFrame(rows6)


def rq4(rep: pd.DataFrame, d: pd.DataFrame, e2e: pd.DataFrame, summ: dict) -> pd.DataFrame:
    e1 = e1_per_rep(rep)
    inb = inbundle_e2e(rep, e2e)
    s = d[d["mode"] == "STREAM"].copy()
    s["E_win"] = s.E / STREAM_LEN
    s["esc_rate"] = s.escalations / (s.N * STREAM_LEN)
    rows = []
    for (gate, model, st), x in s.groupby(["gate", "model_id", "stream"]):
        e2 = e2e.loc[(model, "E2E")]
        per = []
        for r, y in x.groupby("rep"):
            pred = e1.get((gate, r), np.nan) + y.esc_rate.mean() * e2["mean"]
            pred_inb = e1.get((gate, r), np.nan) + y.esc_rate.mean() * inb.get(model, np.nan)
            per.append({"rep": r, "meas": y.E_win.mean(), "pred": pred, "pred_inb": pred_inb})
        p = pd.DataFrame(per)
        err = (p.meas - p.pred) / p.pred
        es = ci(err)
        ei = ci((p.meas - p.pred_inb) / p.pred_inb)
        name = STREAMS[st] if st < len(STREAMS) else str(st)
        host = summ["streams"].get(name, {}).get(HOST_KEY.get(gate, ""), np.nan)
        rows.append({"gate": gate, "stream": name, "model_id": model, "esc_rate_board": x.esc_rate.mean(),
                     "host_alarms_per_100": host, "E_meas_uJ_per_window": p.meas.mean(),
                     "E_pred_uJ_per_window": p.pred.mean(), "rel_err": es["mean"], "rel_err_ci_lo": es.ci_lo,
                     "rel_err_ci_hi": es.ci_hi, "rq4_pass_3pct": abs(es["mean"]) <= 0.03,
                     "E_pred_inbundle": p.pred_inb.mean(), "rel_err_inbundle": ei["mean"],
                     "rel_err_inbundle_ci_lo": ei.ci_lo, "rel_err_inbundle_ci_hi": ei.ci_hi})
    return pd.DataFrame(rows)


def rq5(rq2t: pd.DataFrame, summ: dict, e2_stream: float) -> pd.DataFrame:
    h = summ["host_rates_test_split"]
    t = rq2t[["gate", "name", "mean"]].rename(columns={"mean": "E1_uJ"}).copy()
    t["host_key"] = t.gate.map(HOST_KEY)
    t["tpr"] = [h.get(k, {}).get("tpr", np.nan) for k in t.host_key]
    t["fpr"] = [h.get(k, {}).get("fpr", np.nan) for k in t.host_key]
    ok = t[t.tpr >= 0.98]
    front = []
    for _, r in t.iterrows():
        dom = ((ok.E1_uJ <= r.E1_uJ) & (ok.fpr <= r.fpr) & ((ok.E1_uJ < r.E1_uJ) | (ok.fpr < r.fpr))).any()
        front.append(bool(r.tpr >= 0.98 and not dom))
    t["on_front_tpr098"] = front
    # E(pi = 1 %) with the dataset's stream model (in-bundle stage-2 cost where available): a gate with FPR 1.0 is
    # formally on the (E1, FPR) front when it is the cheapest, but it escalates every window
    t["E_pi1pct_uJ"] = t.E1_uJ + (0.01 * t.tpr + 0.99 * t.fpr) * e2_stream
    t["E2_stream_model_uJ"] = e2_stream
    return t


def rq7(rep: pd.DataFrame) -> pd.DataFrame:
    c = over_repeats(rep[rep["mode"] == "CALIB"])
    c = c.rename(columns={"arg": "calib_n"})
    c["uJ_per_window_calibrated"] = c["mean"] / c.calib_n
    return c[["gate", "calib_n", "mean", "ci_lo", "ci_hi", "cv", "uJ_per_window_calibrated"]]


def rq8(d: pd.DataFrame, rep: pd.DataFrame, e2e: pd.DataFrame) -> pd.DataFrame:
    """Measured: board power during DUTY and its increment over SLEEP. The increment per period at T >= 1 s
    (~10-30 µJ per period, i.e. 1-30 µW) is below the rig's resolution for a 10-20 s window (idle-window spread
    ~0.1-0.3 % of 17.7 mW, i.e. 20-50 µW), so it is reported with its CI and next to the modelled per-period energy
    E1 + esc_rate * E2E from the measured S-1 / S-2 terms (and the in-bundle stage-2 cost)."""
    sl = d[d["mode"] == "SLEEP"].groupby("rep").P_mean_uW.mean()
    e1 = over_repeats(rep[rep["mode"] == "GATE"]).set_index("gate")["mean"]
    inb = inbundle_e2e(rep, e2e)
    du = d[d["mode"] == "DUTY"]
    rows = []
    for (gate, model, T), x in du.groupby(["gate", "model_id", "arg"]):
        per = x.groupby("rep").agg(P=("P_mean_uW", "mean"), esc=("escalations", "sum"), N=("N", "sum"))
        P = ci(per.P)
        inc = ci(per.P - sl.reindex(per.index))
        T_s = T / 1000
        rows.append({"gate": gate, "model_id": model, "T_ms": T, "P_board_mW": P["mean"] / 1e3,
                     "P_board_cv": P.cv, "E_board_per_period_mJ": P["mean"] * T_s / 1e3,
                     "E_board_per_hour_J": P["mean"] * 3600 / 1e6, "P_sleep_mW": sl.mean() / 1e3,
                     "P_inc_over_sleep_uW": inc["mean"], "P_inc_ci_lo": inc.ci_lo, "P_inc_ci_hi": inc.ci_hi,
                     "E_inc_per_period_uJ": inc["mean"] * T_s, "E_inc_per_hour_J": inc["mean"] * 3600 / 1e6,
                     "life_days_CR2032_inc_only": E_BATTERY_J / (inc["mean"] / 1e6) / 86400 if inc["mean"] > 0
                     else np.inf, "esc_rate": per.esc.sum() / per.N.sum(),
                     "E_model_per_period_uJ": e1.get(gate, np.nan) + per.esc.sum() / per.N.sum()
                     * inb.get(model, e2e.loc[(model, "E2E"), "mean"]),
                     })
    # Per-period energy from the 1/T dependence of the DUTY power, P = P_base + E_period / T, fitted per repeat
    # over the three periods. It needs no SLEEP window, so a baseline offset between the SLEEP and DUTY windows
    # cancels; it is the estimator RQ8's energy per hour uses (E_period * 3600 / T on top of the board's base).
    fit = []
    for (gate, model), x in du.groupby(["gate", "model_id"]):
        for r, y in x.groupby("rep"):
            y = y.groupby("arg").P_mean_uW.mean()
            if len(y) >= 2:
                b, a = np.polyfit(1000 / y.index.to_numpy(float), y.to_numpy(), 1)   # µW = µJ/s; 1/T in 1/s
                fit.append({"gate": gate, "model_id": model, "rep": r, "E_period_uJ": b, "P_base_uW": a})
    f = pd.DataFrame(fit)
    for r in rows:
        r["E_model_per_hour_J"] = r["E_model_per_period_uJ"] * 3600 / (r["T_ms"] / 1000) / 1e6
        g = f[(f.gate == r["gate"]) & (f.model_id == r["model_id"])]
        e, p = ci(g.E_period_uJ), ci(g.P_base_uW)
        r.update({"E_period_fit_uJ": e["mean"], "E_period_fit_ci_lo": e.ci_lo, "E_period_fit_ci_hi": e.ci_hi,
                  "P_base_fit_mW": p["mean"] / 1e3,
                  "E_inc_per_hour_fit_J": e["mean"] * 3600 / (r["T_ms"] / 1000) / 1e6,
                  "life_days_CR2032_fit_inc_only": E_BATTERY_J / (e["mean"] / (r["T_ms"] / 1000) / 1e6) / 86400})
    return pd.DataFrame(rows)


def front(rep: pd.DataFrame, e2e: pd.DataFrame) -> pd.DataFrame:
    fx = pd.read_csv(REPO / "results" / "cwru" / "fixedpoint" / "fx_cost_summary.csv")
    fx = fx[fx.core == "m4f"].set_index("region").cyc_med
    rows = []
    for _, r in over_repeats(rep[rep["mode"] == "FXFE"]).iterrows():
        est = fx[FXFE_REGION[r.arg]] / F_CPU * P_ACT_MW * 1e3
        rows.append({"what": f"FXFE {FXFE_REGION[r.arg]}", "E_uJ": r["mean"], "cv": r.cv, "E_est_uJ": est,
                     "ratio_meas_est": r["mean"] / est, "energy_campaign_uJ": np.nan, "cascade_over_campaign": np.nan})
    for _, r in over_repeats(rep[rep["mode"] == "FRONT"]).iterrows():
        ec = e2e.loc[(r.model_id, "FRONT"), "mean"] if (r.model_id, "FRONT") in e2e.index else np.nan
        rows.append({"what": f"FRONT {r.model_id}", "E_uJ": r["mean"], "cv": r.cv, "E_est_uJ": np.nan,
                     "ratio_meas_est": np.nan, "energy_campaign_uJ": ec, "cascade_over_campaign": r["mean"] / ec})
    return pd.DataFrame(rows)


def quality(d: pd.DataFrame, rep: pd.DataFrame) -> dict:
    a = d[(d["mode"] == "GATE") & (d.gate == 1) & (d.input_j == 0)]
    anchors = {int(s): [round(v, 3) for v in x.sort_values("timestamp").E] for s, x in a.groupby("session")}
    drift = {s: (max(v) - min(v)) / np.mean(v) for s, v in anchors.items()}
    cv = over_repeats(rep).cv
    idle = d[d["mode"] == "IDLE"].groupby("session").P_mean_uW.agg(["mean", "min", "max", "count"]) / [1e3, 1e3, 1e3, 1]
    return {"anchors_uJ": anchors, "anchor_drift_rel": drift,
            "repeat_cv_median": float(cv.median()), "repeat_cv_p90": float(cv.quantile(0.9)),
            "repeat_cv_max": float(cv.max()),
            "idle_mW": {int(s): {k: round(float(v), 4) for k, v in r.items()} for s, r in idle.iterrows()},
            "die_temp_C": {int(s): [float(x.die_temp_C.min()), float(x.die_temp_C.max())]
                           for s, x in d.groupby("session")},
            "windows_accepted": {int(s): int(len(x)) for s, x in d.groupby("session")},
            "fw_hash": sorted(d.fw_hash.unique().tolist())}


# ---------------------------------------------------------------------------------------------------------------- main
def analyse(ds: str, idle: str) -> dict:
    out = REPO / "results" / ds / "cascade_hw" / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    d = load_cascade(ds, idle)
    rep = per_repeat(d)
    e2e = e2e_campaign(ds, idle)
    costs = pd.read_csv(REPO / "results" / "rig" / "cascade_design" / "costs.csv")
    summ = json.loads((REPO / "handoff" / "cascade" / ds / "summary.json").read_text())
    tabs = {"rq1_gverify": rq1(ds), "rq2_stage1": rq2(rep, d, costs)}
    tabs["rq3_additivity"], tabs["rq6_reuse"] = rq3_rq6(rep, e2e, d)
    tabs["rq4_streams"] = rq4(rep, d, e2e, summ)
    sm = STREAM_MODEL[ds]
    tabs["rq5_gate_front"] = rq5(tabs["rq2_stage1"], summ, inbundle_e2e(rep, e2e).get(sm, e2e.loc[(sm, "E2E"), "mean"]))
    tabs["rq7_calib"] = rq7(rep)
    tabs["rq8_duty"] = rq8(d, rep, e2e)
    tabs["s6_front_ends"] = front(rep, e2e)
    tabs["conditions"] = over_repeats(rep)
    sfx = "" if idle == "window" else f"_{idle}"
    for k, t in tabs.items():
        t.insert(0, "idle", idle)
        t.to_csv(out / f"{k}{sfx}.csv", index=False)
    q = quality(d, rep)
    r1 = tabs["rq1_gverify"]
    s = {"dataset": ds, "idle": idle,
         "rq1": {"checks": int(r1.n.sum()), "agree": int(r1.agree.sum()), "explained": int(r1.explained.sum()),
                 "unexplained": int(r1.disagree_unexplained.sum())},
         "rq2_E1_uJ": {GATE_NAME[int(r.gate)]: [round(r["mean"], 3), round(r.ratio_meas_est, 3)]
                       for _, r in tabs["rq2_stage1"].iterrows()},
         "rq3_delta_rel_E2E": {r.model_id: round(r.delta_rel_E2E, 4) for _, r in tabs["rq3_additivity"].iterrows()},
         "rq3_all_within_2pct": bool(tabs["rq3_additivity"].rq3_pass_2pct.all()),
         "rq3_inbundle_delta_rel": {r.model_id: round(r.delta_inbundle_rel, 4)
                                    for _, r in tabs["rq3_additivity"].iterrows() if pd.notna(r.delta_inbundle_rel)},
         "rq4_rel_err": {f"G{int(r.gate)} {r.stream}": round(r.rel_err, 4) for _, r in tabs["rq4_streams"].iterrows()},
         "rq4_all_within_3pct": bool(tabs["rq4_streams"].rq4_pass_3pct.all()),
         "rq4_rel_err_inbundle": {f"G{int(r.gate)} {r.stream}": round(r.rel_err_inbundle, 4)
                                  for _, r in tabs["rq4_streams"].iterrows()},
         "rq6_saving_uJ": {r.model_id: round(r.saving_uJ, 2) for _, r in tabs["rq6_reuse"].iterrows()},
         "rq7_calib_uJ": {f"G{int(r.gate)} n={int(r.calib_n)}": round(r["mean"], 1)
                          for _, r in tabs["rq7_calib"].iterrows()},
         "rq8_E_inc_per_hour_fit_J": {int(r.T_ms): round(r.E_inc_per_hour_fit_J, 4)
                                      for _, r in tabs["rq8_duty"].iterrows()},
         "rq8_E_period_fit_uJ": [round(float(tabs["rq8_duty"].E_period_fit_uJ.iloc[0]), 2),
                                 round(float(tabs["rq8_duty"].E_period_fit_ci_lo.iloc[0]), 2),
                                 round(float(tabs["rq8_duty"].E_period_fit_ci_hi.iloc[0]), 2)],
         "rq8_E_model_per_period_uJ": round(float(tabs["rq8_duty"].E_model_per_period_uJ.iloc[0]), 2),
         "quality": q}
    (out / f"summary{sfx}.json").write_text(json.dumps(s, indent=1, default=float))
    return s


def sensitivity(ds: str) -> pd.DataFrame:
    out = REPO / "results" / ds / "cascade_hw" / "analysis"
    a = pd.read_csv(out / "conditions.csv")
    b = pd.read_csv(out / "conditions_session.csv")
    k = KEY
    j = a.merge(b, on=k, suffixes=("_window", "_session"))
    j["rel_diff"] = j.mean_session / j.mean_window - 1
    j = j[k + ["mean_window", "mean_session", "rel_diff", "cv_window", "cv_session"]]
    j.to_csv(out / "idle_sensitivity.csv", index=False)
    return j


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hw", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--dataset", default="both", choices=("cwru", "pu", "both"))
    ap.add_argument("--idle", default="both", choices=("window", "session", "both"))
    a = ap.parse_args(argv)
    for ds in (("cwru", "pu") if a.dataset == "both" else (a.dataset,)):
        for idle in (("window", "session") if a.idle == "both" else (a.idle,)):
            s = analyse(ds, idle)
            print(json.dumps({k: v for k, v in s.items() if k != "quality"}, indent=1, default=float))
        if a.idle == "both":
            j = sensitivity(ds)
            print(f"{ds}: idle session vs window, per condition: median {j.rel_diff.median():+.4f}, "
                  f"max |{j.rel_diff.abs().max():.4f}|; repeat CV median window {j.cv_window.median():.4f}, "
                  f"session {j.cv_session.median():.4f}")


if __name__ == "__main__":
    main()
