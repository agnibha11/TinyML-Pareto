#!/usr/bin/env python3
"""Cascade gate design space (Part B3 host pre-study): which stage-1 gates and stage-2 models go on the board.

Extends ml/cascade.py (four R1 gates, estimated energies) along four axes, on CWRU (P2, P3) or, with
TINYML_DATASET=pu, Paderborn (P3, which chooses gate G6, and P4, the held-out check; docs/cascade_preregistration.md
§4.2):

  front end  fxr1      the integer R1 front end (fx_r1, 10 features; 9 used, the window mean is a recording fingerprint)
             hyb_r2    fx_r1 + the 14 log band powers of R2 (what fx_r2_hyb computes: integer R1, float FFT)
             hyb_r4    fx_r1 + the 32 log envelope bins of R4 (fx_r4_hyb)
  detector   tree1..3  supervised depth-1..3 tree, normal vs fault (balanced), alarm at the lowest fault probability
                       seen on validation faults (as cascade.py "sup")
             mahal     healthy-only Mahalanobis distance to the training split's healthy windows, alarm above the 99th
                       percentile of validation healthy distances (as cascade.py "healthy"); needs no fault data
             either    tree2 | mahal
  confirm    1of1 (none), 2of2, 2of3: alarm only if k of the last n windows of the same recording alarmed
  calibrate  mahal fitted on the first N healthy windows of the deployment's own operating condition (the test load
             in P2, load 3 in P3), threshold = factory ratio thr/median(dist) from the training split times the
             median of the calibration distances. No fault data and no validation data at the deployment.

Metrics per protocol, fold and gate: TPR and FPR on the test split, AUC of the score (base detectors), detection
latency (windows to the first alarm on each faulty test recording, in time order; 1 window = 85.3 ms), stage-1 cost.

Stage-1 cost: virtual-MCU cycles (results/cwru/fixedpoint/fx_cost_summary.csv: FX_R1, HYB_R2, HYB_R4, GATE_MAHAL for
9 features; tree <= the 281 cycles of dt-r1-d4) -> chip-level estimate, and a board-calibrated value: the estimate
times measured/estimated of the matching float front end (hardware FRONT of dt-r{1,2,4}-d4 vs F32_R{1,2,4}). The
Mahalanobis cost for 23 / 41 features is scaled from 9 by d(d+1) (an estimate until the board measures it).
Stage 2: measured board E2E energy per model (repeats 1-3; int8 TFLM models: repeats 5-7 on the patched library).

Break-even prevalence of a gate g for a stage-2 model m: the fault prevalence pi below which the cascade costs less
than running m on every window, E1 + [pi TPR + (1 - pi) FPR] E2 = E2  ->  pi* = (1 - E1/E2 - FPR) / (TPR - FPR).

Paderborn has no file_id. A recording is (bearing, condition, recording) for the confirmation and latency; the
self-calibration uses one healthy bearing at one condition (its recordings in a row), because a single Paderborn
recording has only ~46 windows (pre-registration, Deviations 2). The stage-1 cycle counts and board ratios are CWRU's
(the front ends do not depend on the data); stage-2 energies are the dataset's own board measurements.

Outputs (local until chosen for the paper): results/rig/cascade_design[/pu]/{gates,calib,costs,breakeven}.csv,
summary.txt; Paderborn also g6.json (the pre-registered G6 choice).

    python ml/cascade_design.py
    TINYML_DATASET=pu python ml/cascade_design.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import json

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as DS  # noqa: E402
from cascade import FX_R1_COLS, gate_features  # noqa: E402
from features import cached  # noqa: E402
from splits import PROTOCOLS, load_split  # noqa: E402
from train import manifest  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
RES = DS.RESULTS                          # stage-2 energies and host F1 of this dataset
COST = REPO / "results" / "cwru"          # stage-1 cycles (virtual MCU) and board front-end ratios
OUT = REPO / "results" / "rig" / "cascade_design" / ("" if DS.NAME == "cwru" else DS.NAME)
STUDY = ("P2", "P3") if DS.NAME == "cwru" else ("P3", "P4")
# G6 rule (pre-registration §4.2): chosen on P3, P4 held out; stage 2 = the Paderborn headline model (Deviations 1)
G6_PROTOCOL, G6_HEADLINE, G6_PI, G6_TPR = "P3", "mlp-r4-h32-fp32", 0.01, 0.98
WIN_MS = 1024 / 12.0
F_CPU, P_ACT_MW = 64e6, 9.984            # chip-level energy model (ml/stats.py, headline_numbers.json)
TREE_CYC = 281                           # dt-r1-d4 inference (prehw_costs.csv): bounds a depth <= 3 gate tree
CONFIRM = {"1of1": (1, 1), "2of2": (2, 2), "2of3": (2, 3)}
CALIB_N = (16, 32, 64, 128)


# ---------------------------------------------------------------------------------------------------------------- data
def front_ends() -> dict[str, np.ndarray]:
    fx = np.load(DS.PROCESSED / "feat_fx.npy", mmap_mode="r")[:, slice(*FX_R1_COLS)].astype(np.float64)
    assert np.isfinite(fx).all(), "feat_fx.npy: fx_r1 block incomplete"
    g1 = gate_features(fx)                                         # 9: log rms/std/peak/p2p, crest, kurt, skew, ...
    r2 = np.asarray(cached("r2"), np.float64)[:, 10:]              # 14 log10 band powers
    r4 = np.asarray(cached("r4"), np.float64)[:, 9:]               # 32 log envelope bins
    return {"fxr1": g1, "hyb_r2": np.column_stack([g1, r2]), "hyb_r4": np.column_stack([g1, r4])}


def recordings(man: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(recording id, calibration group id, time key) per window. CWRU: file_id for both, start_idx.
    Paderborn: (bearing, condition, recording); (bearing, condition); recording-major time."""
    if "file_id" in man:
        f = man.file_id.to_numpy()
        return f, f, man.start_idx.to_numpy().astype(np.int64)
    rec = pd.factorize(man.bearing.astype(str) + "|" + man.condition.astype(str) + "|" + man.recording.astype(str))[0]
    grp = pd.factorize(man.bearing.astype(str) + "|" + man.condition.astype(str))[0]
    return rec, grp, man.recording.to_numpy().astype(np.int64) * 10**8 + man.start_idx.to_numpy()


def time_order(rec: np.ndarray, t: np.ndarray, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Positions of idx in time order per recording, and the recording of each (in that order)."""
    o = np.lexsort((t[idx], rec[idx]))
    return o, rec[idx][o]


def confirm(al: np.ndarray, order: np.ndarray, fid: np.ndarray, k: int, n: int) -> np.ndarray:
    """k-of-n over consecutive windows of the same recording (the first windows of a recording see fewer)."""
    if (k, n) == (1, 1):
        return al
    a = al[order].astype(int)
    cnt = a.copy()
    for lag in range(1, n):
        same = np.r_[np.zeros(lag, bool), fid[lag:] == fid[:-lag]]
        cnt += np.r_[np.zeros(lag, int), a[:-lag]] * same
    out = np.zeros_like(al)
    out[order] = cnt >= k
    return out


def auc(score: np.ndarray, y: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, score)) if 0 < y.sum() < len(y) else float("nan")


def latency(al: np.ndarray, y: np.ndarray, order: np.ndarray, fid: np.ndarray) -> tuple[float, float, int]:
    """Windows to the first alarm on each faulty recording (median, max; recordings never alarmed counted apart)."""
    a, yy = al[order], y[order]
    lat, missed = [], 0
    for f in np.unique(fid[yy == 1]):
        s = np.flatnonzero((fid == f) & (yy == 1))
        hit = np.flatnonzero(a[s])
        if len(hit):
            lat.append(hit[0] + 1)
        else:
            missed += 1
    return (float(np.median(lat)) if lat else float("nan"), float(max(lat)) if lat else float("nan"), missed)


# ----------------------------------------------------------------------------------------------------------- detectors
class Mahal:
    def __init__(self, H: np.ndarray):
        self.mu = H.mean(axis=0)
        self.ci = np.linalg.inv(np.cov(H.T) + 1e-3 * np.eye(H.shape[1]))

    def __call__(self, X: np.ndarray) -> np.ndarray:
        d = X - self.mu
        return np.einsum("ij,jk,ik->i", d, self.ci, d)


def fit_detectors(G: np.ndarray, yb: np.ndarray, sp: dict) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """(score on test, alarm on test) per base detector."""
    from sklearn.tree import DecisionTreeClassifier
    tr, va, te = sp["train"], sp["val"], sp["test"]
    out = {}
    for d in (1, 2, 3):
        t = DecisionTreeClassifier(max_depth=d, random_state=0, class_weight="balanced").fit(G[tr], yb[tr])
        th = t.predict_proba(G[va])[:, 1][yb[va] == 1].min()
        s = t.predict_proba(G[te])[:, 1]
        out[f"tree{d}"] = (s, s >= th)
    m = Mahal(G[tr][yb[tr] == 0])
    dv = m(G[va][yb[va] == 0])
    th = np.percentile(dv, 99)
    s = m(G[te])
    out["mahal"] = (s, s > th)
    out["either"] = (np.full(len(te), np.nan), out["tree2"][1] | out["mahal"][1])
    out["_mahal_model"] = (m, th / np.median(dv))       # for the calibration study: factory threshold ratio
    return out


# ---------------------------------------------------------------------------------------------------------------- cost
def hw_energy() -> pd.DataFrame:
    """Measured board energy per model and mode (mean over repeats; int8 TFLM: repeats 5-7 only)."""
    d = pd.read_csv(RES / "hardware" / "hw_measurements.csv", low_memory=False)
    return _hw(d)


def _hw(d: pd.DataFrame) -> pd.DataFrame:
    d = d[(d.rejected == 0) & d["mode"].isin(["RUN", "FRONT", "E2E"])].copy()
    d["rep"] = (d.session % 100) // 10
    int8 = d.model_id.str.contains("int8")
    d = d[(~int8 & d.rep.isin([1, 2, 3])) | (int8 & d.rep.isin([5, 6, 7]))]
    d["E"] = (d.P_mean_uW - d.P_idle_uW) * d.t_window_us_logger / 1e6 / d.N
    return d.groupby(["model_id", "mode", "rep"]).E.mean().groupby(["model_id", "mode"]).mean().unstack()


def stage1_costs(hw: pd.DataFrame) -> pd.DataFrame:
    """hw: CWRU board energies (the board ratios of the float front ends)."""
    fx = pd.read_csv(COST / "fixedpoint" / "fx_cost_summary.csv")
    fx = fx[fx.core == "m4f"].set_index("region").cyc_med
    uj = lambda c: c / F_CPU * P_ACT_MW * 1e3  # noqa: E731
    ratio = {fe: hw.loc[f"dt-{r}-d4-fp32", "FRONT"] / uj(fx[f"F32_{r.upper()}"])
             for fe, r in (("fxr1", "r1"), ("hyb_r2", "r2"), ("hyb_r4", "r4"))}
    fe_cyc = {"fxr1": fx["FX_R1"], "hyb_r2": fx["HYB_R2"], "hyb_r4": fx["HYB_R4"]}
    dims = {"fxr1": 9, "hyb_r2": 23, "hyb_r4": 41}
    rows = []
    for fe in fe_cyc:
        mah = fx["GATE_MAHAL"] * dims[fe] * (dims[fe] + 1) / 90
        for det, c in (("tree1", TREE_CYC), ("tree2", TREE_CYC), ("tree3", TREE_CYC), ("mahal", mah),
                       ("either", TREE_CYC + mah)):
            cyc = fe_cyc[fe] + c
            rows.append({"front_end": fe, "detector": det, "cyc_est": cyc, "E1_est_uJ": uj(cyc),
                         "hw_ratio": ratio[fe], "E1_hwcal_uJ": uj(cyc) * ratio[fe]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------------------------- main
def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    man = manifest()
    rec, grp, tkey = recordings(man)
    FE = front_ends()
    grows, crows = [], []
    for p in STUDY:
        yb = (man[DS.LABEL[p]].to_numpy() != 0).astype(int)    # 0 = healthy in every label set
        for k in range(PROTOCOLS[p]):
            sp = load_split(p, k)
            te = sp["test"]
            yt = yb[te]
            order, fid = time_order(rec, tkey, te)
            for fe, G in FE.items():
                dets = fit_detectors(G, yb, sp)
                mahal_model, factory_ratio = dets.pop("_mahal_model")
                for det, (score, al) in dets.items():
                    for cn, (kk, nn) in CONFIRM.items():
                        a = confirm(al, order, fid, kk, nn)
                        lm, lx, miss = latency(a, yt, order, fid)
                        grows.append({"protocol": p, "fold": k, "front_end": fe, "detector": det, "confirm": cn,
                                      "tpr": a[yt == 1].mean(), "fpr": a[yt == 0].mean(),
                                      "auc": auc(score, yt) if cn == "1of1" and not np.isnan(score).all() else np.nan,
                                      "lat_med_win": lm, "lat_max_win": lx, "rec_missed": miss})
                # self-calibration: healthy recording (CWRU) / bearing at one condition (Paderborn) of the test split
                hfiles = np.unique(grp[te][yt == 0])
                for f in hfiles:
                    allw = np.flatnonzero(grp == f)
                    allw = allw[np.argsort(tkey[allw], kind="stable")]
                    for N in CALIB_N:
                        if N > len(allw):
                            continue
                        cal = allw[:N]
                        end = tkey[cal[-1]] + 1024       # healthy windows from here on were not seen in calibration
                        m = Mahal(G[cal])
                        dc = m(G[cal])
                        th_dep = np.median(dc) * factory_ratio
                        # evaluate on test windows after the calibration span (healthy) + every fault test window
                        keep = (yt == 1) | ((grp[te] == f) & (tkey[te] >= end))
                        if not (keep & (yt == 0)).any():
                            continue
                        s = m(G[te[keep]])
                        a = s > th_dep
                        yk = yt[keep]
                        # same evaluation set for the factory-calibrated gate, for a paired comparison
                        a0 = mahal_model(G[te[keep]]) > np.percentile(mahal_model(G[sp["val"]][yb[sp["val"]] == 0]), 99)
                        crows.append({"protocol": p, "fold": k, "front_end": fe, "N": N, "healthy_group": int(f),
                                      "n_eval_healthy": int((yk == 0).sum()), "tpr": a[yk == 1].mean(),
                                      "fpr": a[yk == 0].mean(), "tpr_factory": a0[yk == 1].mean(),
                                      "fpr_factory": a0[yk == 0].mean(), "auc": auc(s, yk)})
            print(f"{p} fold {k} done", flush=True)
    gates = pd.DataFrame(grows)
    calib = pd.DataFrame(crows)
    gates.to_csv(OUT / "gates.csv", index=False)
    calib.to_csv(OUT / "calib.csv", index=False)

    hw = hw_energy()
    hw_cwru = hw if DS.NAME == "cwru" else _hw(pd.read_csv(COST / "hardware" / "hw_measurements.csv", low_memory=False))
    costs = stage1_costs(hw_cwru)
    costs.to_csv(OUT / "costs.csv", index=False)
    g = gates.groupby(["protocol", "front_end", "detector", "confirm"])[["tpr", "fpr", "auc", "lat_med_win",
                                                                        "lat_max_win", "rec_missed"]].mean()
    g = g.reset_index().merge(costs, on=["front_end", "detector"])

    # stage-2 candidates: best host F1 per family and protocol (cascade.py results), with measured E2E
    cm = pd.read_csv(RES / "cascade" / "cascade_models.csv")
    cm = cm[cm.gate == "either"]
    cand = cm.sort_values("f1_single").groupby(["protocol", "family"]).tail(1)[["protocol", "family", "model_id",
                                                                                 "f1_single"]]
    cand["E2_hw_uJ"] = cand.model_id.map(hw["E2E"])
    be = g.merge(cand, on="protocol")
    for col in ("E1_est_uJ", "E1_hwcal_uJ"):
        be[f"pi_star_{col[3:-3]}"] = ((1 - be[col] / be.E2_hw_uJ - be.fpr) / (be.tpr - be.fpr)).clip(upper=1.0)
    be.to_csv(OUT / "breakeven.csv", index=False)

    lines = []
    with pd.option_context("display.width", 220, "display.float_format", lambda v: f"{v:.3g}",
                           "display.max_rows", 400):
        for p in STUDY:
            s = g[g.protocol == p].sort_values(["fpr", "E1_hwcal_uJ"])
            s = s[s.tpr >= 0.98]
            lines += [f"=== {p}: gates with mean TPR >= 0.98, by FPR then cost ({len(s)} of {(g.protocol == p).sum()})",
                      s[["front_end", "detector", "confirm", "tpr", "fpr", "auc", "lat_med_win", "lat_max_win",
                         "rec_missed", "E1_est_uJ", "E1_hwcal_uJ"]].head(25).to_string(index=False), ""]
        c = calib.groupby(["protocol", "front_end", "N"])[["tpr", "fpr", "tpr_factory", "fpr_factory", "auc"]].mean()
        lines += ["=== self-calibrating healthy-only gate (mean over folds and healthy recordings)", c.to_string(), ""]
        lines += ["=== stage-2 candidates (best host F1 per family) with measured board E2E",
                  cand.to_string(index=False), ""]
        lines += ["=== stage-1 cost (front end + detector)", costs.to_string(index=False)]
    if DS.NAME == "pu":
        lines += ["", *g6_choice(g, hw)]
    txt = "\n".join(lines)
    (OUT / "summary.txt").write_text(txt)
    print(txt)


def g6_choice(g: pd.DataFrame, hw: pd.DataFrame) -> list[str]:
    """Pre-registered G6 rule (§4.2): mean TPR >= 0.98 over the P3 folds, then the lowest predicted E(pi = 1 %) for
    the headline model with hardware-calibrated E1, ties by the lower E1; else the highest TPR - FPR (weak gate).
    P4 is reported for the chosen gate only."""
    e2 = float(hw.loc[G6_HEADLINE, "E2E"])
    s = g[g.protocol == G6_PROTOCOL].copy()
    s["E_pi"] = s.E1_hwcal_uJ + (G6_PI * s.tpr + (1 - G6_PI) * s.fpr) * e2
    key = ["front_end", "detector", "confirm"]
    ok = s[s.tpr >= G6_TPR]
    weak = ok.empty
    best = (s.assign(j=s.tpr - s.fpr).sort_values(["j", "E1_hwcal_uJ"], ascending=[False, True]) if weak
            else ok.sort_values(["E_pi", "E1_hwcal_uJ"])).iloc[0]
    p4 = g[(g.protocol == "P4") & (g[key] == best[key]).all(axis=1)].iloc[0]
    out = {"rule": "pre-registration 4.2", "protocol": G6_PROTOCOL, "headline_model": G6_HEADLINE, "E2_uJ": e2,
           "pi": G6_PI, "tpr_min": G6_TPR, "weak_gate": bool(weak), "n_passing": int(len(ok)),
           **{k: best[k] for k in key}, "P3": {c: float(best[c]) for c in ("tpr", "fpr", "E1_hwcal_uJ", "E_pi")},
           "P4_heldout": {"tpr": float(p4.tpr), "fpr": float(p4.fpr)}}
    (OUT / "g6.json").write_text(json.dumps(out, indent=1, default=str))
    top = (ok if not weak else s).sort_values(["E_pi", "E1_hwcal_uJ"])[key + ["tpr", "fpr", "E1_hwcal_uJ", "E_pi"]]
    return [f"=== G6 (rule §4.2): {len(ok)} gates pass TPR >= {G6_TPR} on {G6_PROTOCOL}; E(pi=1%) with "
            f"{G6_HEADLINE} (E2 = {e2:.1f} uJ)", top.head(15).to_string(index=False), "",
            f"chosen: {dict((k, best[k]) for k in key)}  weak={weak}", f"P4 held-out: TPR {p4.tpr:.3f} FPR {p4.fpr:.3f}"]


if __name__ == "__main__":
    main()
