#!/usr/bin/env python3
"""Paderborn (second dataset) analysis: protocols, rank stability, transfer from CWRU, cost frontier and cascade.

Inputs:
    results/pu/host/*.json               ml/train.py with TINYML_DATASET=pu (88 model IDs x 10 folds x 5 seeds)
    data/processed/pu/feat_r1.npy, manifest.parquet, splits/pu/   ml/pu_data.py (for the cascade gate)
    results/host_summary.csv             the CWRU results, for the cross-dataset comparison
    results/prehw_costs.csv, results/fixedpoint/fx_cost_summary.csv, results/headline_numbers.json
                                         virtual Cortex-M4 costs. A model's cost depends on its architecture and input,
                                         not on the dataset; only the last layer shrinks from 10/4 to 3 outputs, which
                                         changes MLP/CNN inference by a few MACs, so the CWRU costs are used unchanged.
Outputs (results/pu/):
    host_summary.csv       per model and protocol: macro-F1 mean/std/n, accuracy, F1 at SNR 20/10/5/0 dB
    kendall_tau.csv        rank agreement between protocols and with CWRU
    pareto_P3.csv, pareto_P4.csv   accuracy-energy frontier (P3/P4 F1 vs estimated end-to-end energy)
    cascade/gates.csv, cascade/cascade_models.csv   the detect-then-diagnose gate on Paderborn
    summary.json           every number the paper and slides quote
    figs: results/figs/fig_pu_protocols, fig_pu_transfer, fig_pu_pareto (png + pdf)

Usage: python ml/pu_analysis.py
"""
from __future__ import annotations

import os

os.environ["TINYML_DATASET"] = "pu"

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kendalltau

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as DS  # noqa: E402
from cascade import costs as cascade_costs, fit_gates  # noqa: E402
from splits import PROTOCOLS, load_split  # noqa: E402
from train import metrics  # noqa: E402

REPO = DS.REPO
OUT = DS.RESULTS
FIGS = REPO / "results" / "figs"
PROTS = ("P1", "P2", "P3", "P4")
FAM = {"dt": "DT", "rf": "RF", "mlp": "MLP", "cnn": "CNN"}
COLORS = {"DT": "#2a78d6", "RF": "#eb6834", "MLP": "#1baf7a", "CNN": "#4a3aa7"}
PCOL = {"P1": "#2a78d6", "P2": "#eb6834", "P3": "#1baf7a", "P4": "#4a3aa7"}
PNAME = {"P1": "P1 leaky", "P2": "P2 unseen condition", "P3": "P3 unseen bearings", "P4": "P4 artificial→real"}
SNRS = (20, 10, 5, 0)
PIS = (0.0, 0.01, 0.1, 1.0)
GATES = ("sup", "healthy", "either", "either2")


def load_runs() -> pd.DataFrame:
    rows = []
    for f in sorted((OUT / "host").glob("*.json")):
        d = json.loads(f.read_text())
        rows.append({"model_id": d["model_id"], "protocol": d["protocol"], "fold": d["fold"], "seed": d["seed"],
                     "f1": d["f1_macro"], "acc": d["accuracy"], "y_pred": d["y_pred"],
                     **{f"f1_snr{n['snr_db']}": n["f1_macro"] for n in d["noise"]}})
    r = pd.DataFrame(rows)
    r["family"] = r.model_id.str.split("-").str[0]
    r["rep"] = r.model_id.str.split("-").str[1]
    r["prec"] = r.model_id.str.split("-").str[-1]
    r["arch"] = r.model_id.str.rsplit("-", n=1).str[0]
    return r


def per_window(R: pd.DataFrame, man: pd.DataFrame, model: str, protocol: str) -> pd.DataFrame:
    """Every test window of every stored run of one model and protocol, with its prediction (all folds, seeds)."""
    y = man.class3.to_numpy()
    out = []
    for r in R[(R.model_id == model) & (R.protocol == protocol)].itertuples():
        te = load_split(protocol, r.fold)["test"]
        yp = np.frombuffer(r.y_pred.encode(), dtype=np.uint8) - ord("0")
        out.append(man.iloc[te].assign(pred=yp, ok=yp == y[te], fold=r.fold, seed=r.seed))
    return pd.concat(out, ignore_index=True)


def gate_roc(feat: np.ndarray, healthy: np.ndarray, split_fn, folds: int):
    """Healthy-only gate score (Mahalanobis distance of 9 R1 features to the training split's healthy windows) on
    the bearing-disjoint test folds: pooled ROC, per-fold AUC, and the validation-tuned operating point."""
    from cascade import gate_features
    from sklearn.metrics import roc_auc_score, roc_curve
    G = gate_features(feat)
    scores, labels, aucs, ops = [], [], [], []
    for k in range(folds):
        sp = split_fn(k)
        H = G[sp["train"]][healthy[sp["train"]]]
        mu, ci = H.mean(axis=0), np.linalg.inv(np.cov(H.T) + 1e-3 * np.eye(G.shape[1]))
        dist = lambda X: np.einsum("ij,jk,ik->i", X - mu, ci, X - mu)  # noqa: E731
        s, yt = dist(G[sp["test"]]), (~healthy[sp["test"]]).astype(int)
        th = np.percentile(dist(G[sp["val"]][healthy[sp["val"]]]), 99)
        aucs.append(float(roc_auc_score(yt, s)))
        ops.append((float((s[yt == 0] > th).mean()), float((s[yt == 1] > th).mean())))
        scores.append(s)
        labels.append(yt)
    fpr, tpr, _ = roc_curve(np.concatenate(labels), np.concatenate(scores))
    return fpr, tpr, aucs, ops


def deep_dive(R: pd.DataFrame, man: pd.DataFrame, summ: dict) -> None:
    """Where the errors are: per bearing, per damage origin, per operating condition, confusion, model capacity,
    family-level leakage, and why the cheap gate fails (ROC against CWRU)."""
    best3, best4 = summ["P3"]["best_model"], summ["P4"]["best_model"]
    W3, W4 = per_window(R, man, best3, "P3"), per_window(R, man, best4, "P4")
    names = {0: "N", 1: "IR", 2: "OR"}
    dd: dict = {}
    for tag, W in (("P3", W3), ("P4", W4)):
        b = W.groupby(["bearing", "fault_type", "origin"]).ok.mean().reset_index()
        dd[tag] = {"model": best3 if tag == "P3" else best4,
                   "bearing_acc": {r.bearing: float(r.ok) for r in b.itertuples()},
                   "group_acc": {f"{ft}_{o}": float(v) for (ft, o), v in W.groupby(["fault_type", "origin"]).ok.mean().items()},
                   "condition_acc": {c: float(v) for c, v in W.groupby("condition").ok.mean().items()},
                   "confusion": {names[i]: {names[j]: float(v) for j, v in row.items()}
                                 for i, row in pd.crosstab(W.class3, W.pred, normalize="index").iterrows()}}
        b.to_csv(OUT / f"per_bearing_{tag}.csv", index=False)
    S = R.groupby(["model_id", "protocol", "fold"]).f1.mean().reset_index()
    dd["P2_fold_best"] = {c: float(S[(S.protocol == "P2") & (S.fold == k)].f1.max())
                          for k, c in enumerate(("N15_M07_F10", "N09_M07_F10", "N15_M01_F10", "N15_M07_F04"))}
    dd["P2_fold_median"] = {c: float(S[(S.protocol == "P2") & (S.fold == k)].f1.median())
                            for k, c in enumerate(("N15_M07_F10", "N09_M07_F10", "N15_M01_F10", "N15_M07_F04"))}
    cnn = R[R.model_id.str.startswith("cnn") & R.model_id.str.endswith("fp32")]
    dd["cnn_capacity"] = {m: {p: float(v) for p, v in g.groupby("protocol").f1.mean().items()}
                          for m, g in cnn.groupby("model_id")}
    fam = R[~R.model_id.str.endswith("int8ref")].groupby(["family", "protocol"]).f1.mean().unstack()
    dd["family_mean"] = {f: {p: float(v) for p, v in row.items()} for f, row in fam.iterrows()}
    # gate separability, Paderborn vs CWRU, bearing-disjoint folds
    Fp = np.load(DS.PROCESSED / "feat_r1.npy").astype(np.float64)
    hp = man.class3.to_numpy() == 0
    roc_pu = gate_roc(Fp, hp, lambda k: load_split("P3", k), PROTOCOLS["P3"])
    mc = pd.read_parquet(REPO / "data" / "processed" / "manifest.parquet")
    Fc = np.load(REPO / "data" / "processed" / "feat_r1.npy").astype(np.float64)
    hc = mc.class10.to_numpy() == 0
    roc_cw = gate_roc(Fc, hc, lambda k: load_split("P3", k, root=REPO / "splits"), 3)
    dd["gate_auc"] = {"pu": roc_pu[2], "cwru": roc_cw[2], "pu_ops": roc_pu[3], "cwru_ops": roc_cw[3]}
    tp_at = lambda roc, f: float(np.interp(f, roc[0], roc[1]))  # noqa: E731
    dd["gate_recall_at_fpr"] = {f"{x:g}": {"pu": tp_at(roc_pu, x), "cwru": tp_at(roc_cw, x)} for x in (0.01, 0.05, 0.1)}
    summ["deep"] = dd

    plt = style()
    # per-bearing accuracy of the best P3 model
    b = pd.read_csv(OUT / "per_bearing_P3.csv")
    b["grp"] = b.fault_type.map({"N": 0, "IR": 1, "OR": 2})
    b = b.sort_values(["grp", "origin", "bearing"])
    col = {"healthy": "#8a8984", "artificial": "#eb6834", "real": "#2a78d6"}
    fig, ax = plt.subplots(figsize=(7.0, 2.2))
    x = np.arange(len(b))
    ax.bar(x, b.ok, color=[col[o] for o in b.origin], width=0.75, zorder=2)
    for g, lab in ((0, "healthy"), (1, "inner race (IR)"), (2, "outer race (OR)")):
        xs = x[b.grp.to_numpy() == g]
        ax.text(xs.mean(), 1.04, lab, ha="center", fontsize=7)
        if g:
            ax.axvline(xs.min() - 0.5, color="#8a8984", lw=0.6)
    ax.set_xticks(x, b.bearing, rotation=90, fontsize=6)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("share of windows correct")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=c, label=k) for k, c in col.items()], ncol=3, loc="upper right",
              bbox_to_anchor=(1.0, 1.32), fontsize=6.5)
    ax.set_title(f"P3, {best3}, each bearing while held out (all folds and seeds)", fontsize=7, loc="left")
    save(fig, "fig_pu_bearings")
    plt.close(fig)
    # gate ROC: CWRU vs Paderborn
    fig, ax = plt.subplots(figsize=(3.4, 2.5))
    ax.plot(roc_cw[0], roc_cw[1], color="#4a3aa7", lw=1.5, label=f"CWRU P3 (AUC {np.mean(roc_cw[2]):.2f})")
    ax.plot(roc_pu[0], roc_pu[1], color="#eb6834", lw=1.5, label=f"Paderborn P3 (AUC {np.mean(roc_pu[2]):.2f})")
    ax.plot([0, 1], [0, 1], color="#8a8984", lw=0.6, ls=":")
    for ops, c in ((roc_cw[3], "#4a3aa7"), (roc_pu[3], "#eb6834")):
        ax.scatter([o[0] for o in ops], [o[1] for o in ops], s=14, color=c, edgecolors="white", lw=0.5, zorder=3)
    ax.set_xlabel("false-alarm rate (healthy windows)")
    ax.set_ylabel("fault windows caught")
    ax.set_title("Healthy-only R1 gate on unseen bearings; dots: tuned threshold per fold", fontsize=6.3, loc="left")
    ax.legend(loc="lower right", fontsize=6.5)
    save(fig, "fig_pu_gate_roc")
    plt.close(fig)


def nondominated(f1: np.ndarray, e: np.ndarray) -> np.ndarray:
    keep = np.ones(len(f1), bool)
    for i in range(len(f1)):
        dom = (f1 >= f1[i]) & (e <= e[i]) & ((f1 > f1[i]) | (e < e[i]))
        keep[i] = not dom.any()
    return keep


def style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8,
        "axes.edgecolor": "#8a8984", "axes.linewidth": 0.6, "axes.grid": True, "grid.color": "#e4e3df",
        "grid.linewidth": 0.5, "xtick.color": "#52514e", "ytick.color": "#52514e", "legend.frameon": False,
        "legend.fontsize": 6.5, "savefig.dpi": 300, "axes.spines.top": False, "axes.spines.right": False})
    return plt


def save(fig, name):
    FIGS.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(FIGS / f"{name}.{ext}", bbox_inches="tight")


def main() -> None:
    R = load_runs()
    clean = R[~R.model_id.str.endswith("int8ref")]
    S = (R.groupby(["model_id", "family", "rep", "prec", "arch", "protocol"])
         .agg(f1_mean=("f1", "mean"), f1_std=("f1", "std"), n_runs=("f1", "size"), acc_mean=("acc", "mean"),
              **{f"f1_snr{s}": (f"f1_snr{s}", "mean") for s in SNRS}).reset_index())
    S.to_csv(OUT / "host_summary.csv", index=False)
    W = S.pivot(index="model_id", columns="protocol", values="f1_mean")
    summ: dict = {"n_results": int(len(R)), "n_models": int(W.shape[0]), "chance_f1": 1 / 3,
                  "runs_per_model": {p: int(S[S.protocol == p].n_runs.iloc[0]) for p in PROTS}}

    # ---- protocols: best and median per protocol, best per family, best per representation
    Sc = S[S.prec != "int8ref"]
    for p in PROTS:
        t = Sc[Sc.protocol == p].sort_values("f1_mean", ascending=False)
        b = t.iloc[0]
        summ[p] = {"best_model": b.model_id, "best_f1": float(b.f1_mean), "best_std": float(b.f1_std),
                   "median_f1": float(t.f1_mean.median()),
                   "families": {f: {"model": g.iloc[0].model_id, "f1": float(g.iloc[0].f1_mean)}
                                for f, g in t.groupby("family", sort=False)},
                   "reps": {r: float(g.f1_mean.max()) for r, g in t.groupby("rep")}}
    best3 = summ["P3"]["best_model"]
    summ["P3"]["best_fold_f1"] = [float(v) for v in
                                  R[(R.model_id == best3) & (R.protocol == "P3")].groupby("fold").f1.mean()]
    summ["P3"]["best_snr0"] = float(S[(S.model_id == best3) & (S.protocol == "P3")].f1_snr0.iloc[0])
    summ["P3"]["best_snr20"] = float(S[(S.model_id == best3) & (S.protocol == "P3")].f1_snr20.iloc[0])
    summ["gap_best_P1_P3"] = summ["P1"]["best_f1"] - summ["P3"]["best_f1"]

    # ---- precision: int8 PTQ / QAT against the same trained fp32 architecture
    prec = {}
    for p in PROTS:
        t = S[S.protocol == p].pivot(index="arch", columns="prec", values="f1_mean")
        prec[p] = {q: {"mean": float((t[q] - t.fp32).dropna().mean()), "worst": float((t[q] - t.fp32).dropna().min())}
                   for q in ("int8", "int8qat")}
    summ["precision_delta"] = prec

    # ---- rank stability between protocols and against CWRU
    C = pd.read_csv(REPO / "results" / "host_summary.csv", keep_default_na=False)
    C = C[C.snr_db == ""].pivot(index="model_id", columns="protocol", values="f1_macro_mean")
    J = W.join(C, rsuffix="_cwru")
    tau = []
    for a, b in (("P1", "P2"), ("P1", "P3"), ("P1", "P4"), ("P2", "P3"), ("P3", "P4")):
        tau.append({"a": f"PU {a}", "b": f"PU {b}", "tau": kendalltau(W[a], W[b]).statistic})
    for p in ("P1", "P2", "P3"):
        tau.append({"a": f"CWRU {p}", "b": f"PU {p}", "tau": kendalltau(J[f"{p}_cwru"], J[p]).statistic})
    tau.append({"a": "CWRU P3", "b": "PU P4", "tau": kendalltau(J["P3_cwru"], J["P4"]).statistic})
    tau = pd.DataFrame(tau)
    tau.to_csv(OUT / "kendall_tau.csv", index=False)
    summ["tau"] = {f"{r.a}|{r.b}": float(r.tau) for r in tau.itertuples()}
    cw_best = J["P3_cwru"].idxmax()
    summ["transfer"] = {"cwru_P3_best": cw_best, "cwru_P3_best_f1_cwru": float(J.loc[cw_best, "P3_cwru"]),
                        "cwru_P3_best_f1_pu_P3": float(J.loc[cw_best, "P3"]),
                        "cwru_P3_best_rank_pu_P3": int((J["P3"] > J.loc[cw_best, "P3"]).sum() + 1),
                        "pu_P3_best_f1_cwru_P3": float(J.loc[best3, "P3_cwru"]),
                        "pu_P3_best_rank_cwru_P3": int((J["P3_cwru"] > J.loc[best3, "P3_cwru"]).sum() + 1),
                        "best_rep_cwru_P3": C["P3"].groupby(C.index.str.split("-").str[1]).max().idxmax(),
                        "best_rep_pu_P3": max(summ["P3"]["reps"], key=summ["P3"]["reps"].get)}

    # ---- accuracy-energy frontier with the virtual Cortex-M4 costs
    cost = pd.read_csv(REPO / "results" / "prehw_costs.csv").set_index("model_id")
    fr = {}
    for p in ("P3", "P4"):
        t = Sc[Sc.protocol == p].copy()
        t["E_e2e_uJ"] = t.model_id.map(cost.E_e2e_uJ)
        t["ram_B"] = t.model_id.map(cost.ram_peak_B) if "ram_peak_B" in cost else np.nan
        t = t.dropna(subset=["E_e2e_uJ"])
        t["frontier"] = nondominated(t.f1_mean.to_numpy(), t.E_e2e_uJ.to_numpy())
        t.sort_values("E_e2e_uJ").to_csv(OUT / f"pareto_{p}.csv", index=False)
        f = t[t.frontier].sort_values("E_e2e_uJ")
        fr[p] = {"frontier": [{"model": r.model_id, "f1": float(r.f1_mean), "E_uJ": float(r.E_e2e_uJ)}
                              for r in f.itertuples()]}
        summ[p]["best_E_uJ"] = float(cost.loc[summ[p]["best_model"], "E_e2e_uJ"])
    summ["frontier"] = fr

    # ---- detect-then-diagnose cascade on Paderborn (6 healthy bearings instead of 4 CWRU recordings)
    man = pd.read_parquet(DS.PROCESSED / "manifest.parquet")
    F = np.load(DS.PROCESSED / "feat_r1.npy").astype(np.float64)
    yb = (man.class3.to_numpy() != 0).astype(int)                    # KB (-1) counts as faulty; never in a split
    rec = man.bearing + "_" + man.condition + "_" + man.recording.astype(str)
    gm = pd.DataFrame({"file_id": pd.factorize(rec)[0], "start_idx": man.start_idx})
    co, s1 = cascade_costs()
    alarms, grows = {}, []
    for p in ("P2", "P3", "P4"):
        for k in range(PROTOCOLS[p]):
            sp = load_split(p, k)
            a = fit_gates(F, yb, sp, gm)
            alarms[(p, k)] = a
            yt = yb[sp["test"]]
            for g, al in a.items():
                grows.append({"protocol": p, "fold": k, "gate": g, "tpr": float(al[yt == 1].mean()),
                              "fpr": float(al[yt == 0].mean()), "n_fault": int((yt == 1).sum()),
                              "n_healthy": int((yt == 0).sum())})
    gates = pd.DataFrame(grows)
    (OUT / "cascade").mkdir(parents=True, exist_ok=True)
    gates.to_csv(OUT / "cascade" / "gates.csv", index=False)
    y3 = man.class3.to_numpy()
    rows = []
    for r in R[R.protocol.isin(["P2", "P3", "P4"])].itertuples():
        te = load_split(r.protocol, r.fold)["test"]
        yp = np.frombuffer(r.y_pred.encode(), dtype=np.uint8) - ord("0")
        for g in GATES:
            al = alarms[(r.protocol, r.fold)][g]
            rows.append({"model_id": r.model_id, "protocol": r.protocol, "fold": r.fold, "seed": r.seed, "gate": g,
                         "f1_single": r.f1, "f1_cascade": metrics(y3[te], np.where(al, yp, 0), 3)["f1_macro"]})
    cr = pd.DataFrame(rows)
    rate = gates.groupby(["protocol", "gate"])[["tpr", "fpr"]].mean()
    m = cr.groupby(["model_id", "protocol", "gate"])[["f1_single", "f1_cascade"]].mean().reset_index()
    m["family"] = m.model_id.str.split("-").str[0]
    m["E_e2e_uJ"] = m.model_id.map(cost.E_e2e_uJ)
    rep = m.model_id.str.split("-").str[1]
    m["E_esc_uJ"] = np.where(rep.isin(["r1", "r2", "r4"]), m.E_e2e_uJ - float(co.loc["dt-r1-d4-fp32", "E_fe_uJ"]),
                             m.E_e2e_uJ)
    m["E_stage1_uJ"] = m.gate.map(s1)
    tp = np.array([rate.loc[(p, g), "tpr"] for p, g in zip(m.protocol, m.gate)])
    fp = np.array([rate.loc[(p, g), "fpr"] for p, g in zip(m.protocol, m.gate)])
    for pi in PIS:
        m[f"E_pi{pi:g}_uJ"] = m.E_stage1_uJ + (pi * tp + (1 - pi) * fp) * m.E_esc_uJ
    m["saving_pi0.01"] = m.E_e2e_uJ / m["E_pi0.01_uJ"]
    m.to_csv(OUT / "cascade" / "cascade_models.csv", index=False)
    cas = {"stage1_uJ": float(s1["either"])}
    for p in ("P2", "P3", "P4"):
        for g in GATES:
            d = m[(m.protocol == p) & (m.gate == g)]
            gg = gates[(gates.protocol == p) & (gates.gate == g)]
            best = d.sort_values("f1_single").iloc[-1]
            cas[f"{p}_{g}"] = {"tpr": float(gg.tpr.mean()), "fpr": float(gg.fpr.mean()),
                               "fpr_folds": [float(v) for v in gg.fpr], "tpr_folds": [float(v) for v in gg.tpr],
                               "n_healthy_test": int(gg.n_healthy.sum()),
                               "dF1_mean": float((d.f1_cascade - d.f1_single).mean()),
                               "saving_median": float(d["saving_pi0.01"].median()),
                               "saving_max": float(d["saving_pi0.01"].max()),
                               "best_model": best.model_id, "best_f1_single": float(best.f1_single),
                               "best_f1_cascade": float(best.f1_cascade), "best_E_single": float(best.E_e2e_uJ),
                               "best_E_pi0.01": float(best["E_pi0.01_uJ"])}
    summ["cascade"] = cas
    deep_dive(R, man, summ)
    (OUT / "summary.json").write_text(json.dumps(summ, indent=1))

    # ---- figures
    plt = style()
    # 1: best and median macro-F1 per protocol, CWRU vs Paderborn
    fig, ax = plt.subplots(figsize=(3.5, 2.3))
    cw = C.copy()
    cw = cw[~cw.index.str.endswith("int8ref")]
    groups = [("CWRU", {p: (cw[p].max(), cw[p].median()) for p in ("P1", "P2", "P3")}),
              ("Paderborn", {p: (summ[p]["best_f1"], summ[p]["median_f1"]) for p in PROTS})]
    x, xt, xl = 0.0, [], []
    for name, d in groups:
        for p, (b, md) in d.items():
            ax.bar(x, md, 0.7, color=PCOL[p], zorder=2)
            ax.scatter(x, b, marker="_", s=70, color="#0b0b0b", zorder=3, linewidths=1.3)
            ax.text(x, b + 0.025, f"{b:.2f}", ha="center", fontsize=5.8)
            xt.append(x)
            xl.append(p)
            x += 1
        ax.text(x - len(d) / 2 - 0.5, -0.2, name, ha="center", fontsize=7, transform=ax.get_xaxis_transform())
        x += 0.8
    ax.axhline(1 / 3, color="#8a8984", lw=0.7, ls=":")
    ax.text(x - 0.9, 1 / 3 + 0.015, "chance (PU)", fontsize=5.5, color="#52514e", ha="right")
    ax.set_xticks(xt, xl, fontsize=6.5)
    ax.set_ylim(0, 1.1)
    ax.set_ylabel("macro-F1 (host)")
    ax.set_title("Bars: median of 88 model IDs; ticks: best", fontsize=6.5, color="#52514e", loc="left")
    save(fig, "fig_pu_protocols")
    plt.close(fig)

    # 2: does the CWRU ranking transfer? CWRU P3 vs Paderborn P3 per model
    fig, ax = plt.subplots(figsize=(3.5, 2.5))
    J2 = J[~J.index.str.endswith("int8ref")]
    for fam, col in COLORS.items():
        d = J2[J2.index.str.startswith(fam.lower() + "-")]
        ax.scatter(d["P3_cwru"], d["P3"], s=10, color=col, label=fam, alpha=0.85, lw=0)
    for mid, lab in ((cw_best, "CWRU best"), (best3, "PU best")):
        ax.annotate(lab, (J.loc[mid, "P3_cwru"], J.loc[mid, "P3"]), xytext=(6, -10 if lab == "CWRU best" else 6),
                    textcoords="offset points", fontsize=6, arrowprops={"arrowstyle": "-", "lw": 0.5})
    ax.set_xlabel("macro-F1, CWRU P3 (unseen bearings)")
    ax.set_ylabel("macro-F1, Paderborn P3")
    ax.set_title(f"Kendall τ = {summ['tau']['CWRU P3|PU P3']:.2f}", fontsize=7, loc="left")
    ax.legend(ncol=4, loc="lower right", bbox_to_anchor=(1.0, 1.0), fontsize=6, handletextpad=0.1, columnspacing=0.8)
    save(fig, "fig_pu_transfer")
    plt.close(fig)

    # 3: accuracy-energy on Paderborn P3 and P4
    fig, axs = plt.subplots(1, 2, figsize=(7.0, 2.4), sharey=True)
    for ax, p in zip(axs, ("P3", "P4")):
        t = pd.read_csv(OUT / f"pareto_{p}.csv")
        for fam, col in COLORS.items():
            d = t[t.family == fam.lower()]
            ax.scatter(d.E_e2e_uJ, d.f1_mean, s=10, color=col, label=fam, alpha=0.8, lw=0)
        f = t[t.frontier].sort_values("E_e2e_uJ")
        ax.step(f.E_e2e_uJ, f.f1_mean, where="post", color="#0b0b0b", lw=0.9)
        ax.axhline(1 / 3, color="#8a8984", lw=0.7, ls=":")
        ax.set_xscale("log")
        ax.set_xlabel("energy per window, µJ (virtual Cortex-M4, est.)")
        ax.set_title(PNAME[p], fontsize=7, loc="left")
    axs[0].set_ylabel("macro-F1 (host)")
    axs[1].legend(ncol=4, loc="upper right", fontsize=6)
    save(fig, "fig_pu_pareto")
    plt.close(fig)
    print(json.dumps({k: summ[k] for k in ("P1", "P2", "P3", "P4", "tau", "transfer")}, indent=1, default=str)[:4000])
    print(json.dumps({k: v for k, v in cas.items() if "either" in k or k == "stage1_uJ"}, indent=1))


if __name__ == "__main__":
    main()
