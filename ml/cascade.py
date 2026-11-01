#!/usr/bin/env python3
"""Detect-then-diagnose cascade: a cheap always-on fault detector gates the expensive diagnosis model.

Stage 1 runs on every window: the integer R1 front end (firmware/bench/frontend_fx.c, fx_r1; 22.4 k estimated cycles
on the virtual Cortex-M4F) and a gate. Stage 2, any of the 88 deployable models, runs only when the gate raises an
alarm; otherwise the window is labelled healthy. False alarms cost energy, missed faults cost accuracy, so every
gate is tuned on the validation split for recall, never on test data. Three gates:

  sup     supervised: depth-2 decision tree on the 10 R1 features, normal vs fault (training split, balanced
          weights); alarm when the fault probability reaches the lowest value seen on validation faults
  healthy healthy-only: Mahalanobis distance of 9 R1 features (log rms/std/peak/p2p, crest, kurtosis, skewness,
          shape, impulse) to the training split's healthy windows; alarm above the 99th percentile of validation
          healthy distances. Needs no fault data, which is what a newly installed sensor has.
  either  alarm if either gate fires (highest recall)
  either2 "either", confirmed: alarm only if the previous window of the same recording also alarmed. Sporadic
          false alarms vanish; a real fault is escalated one window (85 ms) later.

Stage-2 predictions are the stored per-window predictions of all 3,520 host runs (results/host/*.json), so the
cascade is scored for every model, fold and seed without retraining. Leakage-free protocols only (P2, P3).

Energy per window at fault prevalence pi (fraction of windows from a faulty machine), from the measured gate rates:
    E(pi) = E_stage1 + [pi * TPR + (1 - pi) * FPR] * E_e2e(model)
with E_e2e from results/prehw_costs.csv (stage 2 recomputes its whole front end: no reuse of R1 is assumed) and
E_stage1 from results/fixedpoint/fx_cost_summary.csv. Chip-level energy model of ml/stats.py.

Outputs (results/cascade/): gates.csv (TPR/FPR per protocol, fold, gate), cascade_runs.csv (per stored run),
cascade_models.csv (per model and protocol), summary.json; figures fig_cascade_energy and fig_cascade_pareto in
results/figs/.

Usage: python ml/cascade.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from splits import LABEL, PROTOCOLS, load_split  # noqa: E402
from train import manifest, metrics  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
RES = REPO / "results"
OUT = RES / "cascade"
FIGS = RES / "figs"
FX_R1_COLS = (75, 85)          # integer R1 block of data/processed/feat_fx.npy (tools/fxfe/run_fxfe.py)
GATES = ("sup", "healthy", "either", "either2")
PIS = (0.0, 0.01, 0.1, 1.0)
HEADLINE = "either"           # gate used in the figures and the paper
FAMILY = {"dt": "DT", "rf": "RF", "mlp": "MLP", "cnn": "CNN"}


def gate_features(F: np.ndarray) -> np.ndarray:
    return np.column_stack([np.log(F[:, 1:5]), F[:, 5:10]])


def fit_gates(F: np.ndarray, yb: np.ndarray, sp: dict, man: pd.DataFrame) -> dict[str, np.ndarray]:
    """Alarm (bool) per test window for each gate, tuned on the validation split."""
    from sklearn.tree import DecisionTreeClassifier
    tr, va, te = sp["train"], sp["val"], sp["test"]
    t = DecisionTreeClassifier(max_depth=2, random_state=0, class_weight="balanced").fit(F[tr], yb[tr])
    th = t.predict_proba(F[va])[:, 1][yb[va] == 1].min()
    sup = t.predict_proba(F[te])[:, 1] >= th
    G = gate_features(F)
    H = G[tr][yb[tr] == 0]
    mu, ci = H.mean(axis=0), np.linalg.inv(np.cov(H.T) + 1e-3 * np.eye(G.shape[1]))
    dist = lambda X: np.einsum("ij,jk,ik->i", X - mu, ci, X - mu)  # noqa: E731
    healthy = dist(G[te]) > np.percentile(dist(G[va][yb[va] == 0]), 99)
    either = sup | healthy
    m = man.iloc[te][["file_id", "start_idx"]].reset_index(drop=True)
    order = m.sort_values(["file_id", "start_idx"]).index.to_numpy()     # test windows in time order per recording
    al, fid = either[order], m.file_id.to_numpy()[order]
    conf = np.zeros_like(either)
    conf[order] = al & np.r_[False, al[:-1]] & np.r_[False, fid[1:] == fid[:-1]]
    return {"sup": sup, "healthy": healthy, "either": either, "either2": conf}


def load_runs(protocols=("P2", "P3")) -> list[dict]:
    runs = []
    for f in sorted((RES / "host").glob("*.json")):
        d = json.loads(f.read_text())
        if d["protocol"] in protocols and d.get("snr_db") is None:
            runs.append({k: d[k] for k in ("model_id", "protocol", "fold", "seed", "f1_macro", "y_pred")})
    return runs


def costs() -> tuple[pd.DataFrame, dict]:
    c = pd.read_csv(RES / "prehw_costs.csv").set_index("model_id")
    fx = pd.read_csv(RES / "fixedpoint" / "fx_cost_summary.csv")
    fx = fx[fx.core == "m4f"].set_index("region")
    em = json.loads((RES / "headline_numbers.json").read_text())["energy_model"]
    uj = lambda cyc: cyc / (em["f_cpu_MHz"] * 1e6) * em["P_active_mW"] * 1e3  # noqa: E731
    tree_inf = float(c.loc["dt-r1-d4-fp32", "inf_cyc_med"])     # a depth-4 tree bounds the depth-2 gate
    s1 = {"sup": uj(fx.loc["FX_R1", "cyc_med"] + tree_inf),
          "healthy": uj(fx.loc["FX_R1", "cyc_med"] + fx.loc["GATE_MAHAL", "cyc_med"]),
          "either": uj(fx.loc["FX_R1", "cyc_med"] + tree_inf + fx.loc["GATE_MAHAL", "cyc_med"])}
    s1["either2"] = s1["either"]
    return c, s1


def figures(models: pd.DataFrame, gates: pd.DataFrame, s1: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    sys.path.insert(0, str(REPO / "ml"))
    from stats import COLORS as FAMILY_COLOR  # noqa: E402
    FIGS.mkdir(parents=True, exist_ok=True)
    m3 = models[(models.protocol == "P3") & (models.gate == HEADLINE)]
    rate = gates[(gates.protocol == "P3") & (gates.gate == HEADLINE)][["tpr", "fpr"]].mean()
    pis = np.logspace(-3, 0, 60)
    fig, ax = plt.subplots(figsize=(3.5, 2.5))
    for fam in ("dt", "rf", "mlp", "cnn"):
        r = m3[m3.family == fam].sort_values("f1_single").iloc[-1]
        col = FAMILY_COLOR[FAMILY[fam]]
        e = s1[HEADLINE] + (pis * rate.tpr + (1 - pis) * rate.fpr) * r.E_escalate_reuse_uJ
        ax.plot(pis, e / r.E_e2e_uJ, color=col, lw=1.6, label=f"{FAMILY[fam]} ({r.E_e2e_uJ:,.0f} µJ alone)")
    ax.axhline(1.0, color="#16202a", lw=0.8, ls="--")
    ax.text(1.2e-3, 0.82, "model alone", fontsize=6, color="#16202a")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("fault prevalence (share of windows)")
    ax.set_ylabel("energy vs. model alone")
    ax.set_title("Cascade, most accurate P3 model per family", fontsize=7)
    ax.legend(fontsize=6, frameon=False, loc="lower right")
    ax.grid(True, which="major", lw=0.3, alpha=0.5)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(FIGS / f"fig_cascade_energy.{ext}", dpi=300)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    for fam in ("dt", "rf", "mlp", "cnn"):
        d = m3[m3.family == fam]
        col = FAMILY_COLOR[FAMILY[fam]]
        ax.scatter(d.E_e2e_uJ, 1 - d.f1_single, s=9, facecolors="none", edgecolors=col, lw=0.6)
        ax.scatter(d["E_pi0.01_uJ"], 1 - d.f1_cascade, s=9, color=col, label=FAMILY[fam])
    ax.set_xscale("log")
    ax.set_xlabel("energy per window (µJ, est.)")
    ax.set_ylabel("error = 1 − macro-F1 (P3)")
    ax.set_title("Hollow: model alone; filled: cascade at 1 % fault prevalence", fontsize=7)
    ax.legend(fontsize=6, frameon=False, ncol=2, loc="lower right")
    ax.grid(True, which="major", lw=0.3, alpha=0.5)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(FIGS / f"fig_cascade_pareto.{ext}", dpi=300)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    man = manifest()
    F = np.load(REPO / "data" / "processed" / "feat_fx.npy")[:, slice(*FX_R1_COLS)].astype(np.float64)
    yb = (man.class10.to_numpy() != 0).astype(int)

    alarms, grows = {}, []
    for p in ("P2", "P3"):
        for k in range(PROTOCOLS[p]):
            sp = load_split(p, k)
            a = fit_gates(F, yb, sp, man)
            alarms[(p, k)] = a
            yt = yb[sp["test"]]
            for g, al in a.items():
                grows.append({"protocol": p, "fold": k, "gate": g, "tpr": float(al[yt == 1].mean()),
                              "fpr": float(al[yt == 0].mean()), "n_fault": int((yt == 1).sum()),
                              "n_healthy": int((yt == 0).sum())})
    gates = pd.DataFrame(grows)
    gates.to_csv(OUT / "gates.csv", index=False)
    print(gates.groupby(["protocol", "gate"])[["tpr", "fpr"]].agg(["mean", "min", "max"]).round(4).to_string())

    rows = []
    for r in load_runs():
        p, k = r["protocol"], r["fold"]
        te = load_split(p, k)["test"]
        y = man[LABEL[p]].to_numpy()[te]
        yp = np.frombuffer(r["y_pred"].encode(), dtype=np.uint8) - ord("0")
        nc = 10 if LABEL[p] == "class10" else 4
        for g in GATES:
            al = alarms[(p, k)][g]
            yc = np.where(al, yp, 0)
            rows.append({"model_id": r["model_id"], "protocol": p, "fold": k, "seed": r["seed"], "gate": g,
                         "f1_single": r["f1_macro"], "f1_cascade": metrics(y, yc, nc)["f1_macro"],
                         "escalated": float(al.mean())})
    runs = pd.DataFrame(rows)
    runs.to_csv(OUT / "cascade_runs.csv", index=False)

    c, s1 = costs()
    rate = gates.groupby(["protocol", "gate"])[["tpr", "fpr"]].mean()
    m = runs.groupby(["model_id", "protocol", "gate"])[["f1_single", "f1_cascade"]].mean().reset_index()
    m["family"] = m.model_id.str.split("-").str[0]
    m["E_e2e_uJ"] = m.model_id.map(c.E_e2e_uJ)
    m["E_stage1_uJ"] = m.gate.map(s1)
    for pi in PIS:
        tp = np.array([rate.loc[(p, g), "tpr"] for p, g in zip(m.protocol, m.gate)])
        fp = np.array([rate.loc[(p, g), "fpr"] for p, g in zip(m.protocol, m.gate)])
        m[f"E_pi{pi:g}_uJ"] = m.E_stage1_uJ + (pi * tp + (1 - pi) * fp) * m.E_e2e_uJ
    m["saving_pi0.01"] = m.E_e2e_uJ / m["E_pi0.01_uJ"]
    # with reuse: R1/R2/R4 models need not recompute the R1 statistics stage 1 already has (R2 and R4 contain them)
    rep = m.model_id.str.split("-").str[1]
    m["E_escalate_reuse_uJ"] = np.where(rep.isin(["r1", "r2", "r4"]),
                                        m.E_e2e_uJ - float(c.loc["dt-r1-d4-fp32", "E_fe_uJ"]), m.E_e2e_uJ)
    for pi in PIS:
        tp = np.array([rate.loc[(p, g), "tpr"] for p, g in zip(m.protocol, m.gate)])
        fp = np.array([rate.loc[(p, g), "fpr"] for p, g in zip(m.protocol, m.gate)])
        m[f"E_pi{pi:g}_reuse_uJ"] = m.E_stage1_uJ + (pi * tp + (1 - pi) * fp) * m.E_escalate_reuse_uJ
    m.to_csv(OUT / "cascade_models.csv", index=False)

    summ = {"E_stage1_uJ": s1, "gates": {f"{p}_{g}": {"tpr_mean": float(rate.loc[(p, g), "tpr"]),
                                                       "fpr_mean": float(rate.loc[(p, g), "fpr"])}
                                         for p, g in rate.index}}
    for p in ("P2", "P3"):
        for g in GATES:
            d = m[(m.protocol == p) & (m.gate == g)]
            dd = runs[(runs.protocol == p) & (runs.gate == g)]
            diff = dd.f1_cascade - dd.f1_single
            best = d.sort_values("f1_single").iloc[-1]
            summ[f"{p}_{g}"] = {
                "dF1_mean": float(diff.mean()), "dF1_min_run": float(diff.min()),
                "dF1_model_mean_min": float((d.f1_cascade - d.f1_single).min()),
                "share_models_not_worse": float(((d.f1_cascade - d.f1_single) > -0.005).mean()),
                "saving_median_pi0.01": float(d["saving_pi0.01"].median()),
                "saving_max_pi0.01": float(d["saving_pi0.01"].max()),
                "best_model": best.model_id, "best_f1_single": float(best.f1_single),
                "best_f1_cascade": float(best.f1_cascade), "best_E_single_uJ": float(best.E_e2e_uJ),
                **{f"best_E_pi{pi:g}_uJ": float(best[f"E_pi{pi:g}_uJ"]) for pi in PIS},
                **{f"best_E_pi{pi:g}_reuse_uJ": float(best[f"E_pi{pi:g}_reuse_uJ"]) for pi in PIS},
                "families": {fam: {"model": r.model_id, "f1_single": float(r.f1_single),
                                   "f1_cascade": float(r.f1_cascade), "E_single_uJ": float(r.E_e2e_uJ),
                                   "E_pi0.01_uJ": float(r["E_pi0.01_uJ"]), "saving_pi0.01": float(r["saving_pi0.01"])}
                             for fam, r in ((f, d[d.family == f].sort_values("f1_single").iloc[-1])
                                            for f in ("dt", "rf", "mlp", "cnn"))}}
    (OUT / "summary.json").write_text(json.dumps(summ, indent=1))
    print(json.dumps(summ, indent=1))
    figures(m, gates, s1)


if __name__ == "__main__":
    main()
