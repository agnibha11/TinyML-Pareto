#!/usr/bin/env python3
"""Accuracy of the fixed-point and hybrid front ends (firmware/bench/frontend_fx.c), against the float front end.

Input: data/processed/feat_fx.npy, the features of every window computed by the Cortex-M4 machine code of each front
end inside QEMU from int16 samples (tools/fxfe/run_fxfe.py), and data/processed/feat_r{1,2,4}.npy, the Python float
reference on the float windows (what every model so far was trained on).

1. Fidelity: |feature - reference| in units of that feature's standard deviation, per front end.
2. Deployment: every tree and forest configuration on R1/R2/R4 (30 configurations x 8 folds x 5 seeds), trained as
   in ml/train.py on reference features, then tested on (a) reference features and (b) each on-device front end,
   normalised with the training statistics. This is the drop-in question: does a trained model survive the swap?
3. Retraining: the same configurations trained and tested on the fixed-point features (q15, q31, integer R1).
4. MLPs: the deployed float and int8 MLPs (P2 fold 0 seed 0, the instance in the firmware), class agreement with
   the reference front end on that fold's test windows.

Outputs: results/fixedpoint/fx_fidelity.csv, fx_accuracy_runs.csv (one row per run), fx_accuracy.csv (per
configuration, protocol and front end), fx_mlp_agreement.csv, fx_summary.json.

Usage: python ml/fixed_point.py [-j 2]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features as FE  # noqa: E402
import models as MD  # noqa: E402
from splits import LABEL, PROTOCOLS, load_split  # noqa: E402
from train import manifest, metrics, rf_hard_predict  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "results" / "fixedpoint"
COLS = {"f32_r1": (0, 10), "f32_r2": (10, 34), "f32_r4": (34, 75), "fx_r1": (75, 85), "q15_r2": (85, 109),
        "q31_r2": (109, 133), "q15_r4": (133, 174), "q31_r4": (174, 215), "hyb_r2": (215, 239),
        "hyb_r4": (239, 280)}
# front ends per representation: name -> column block. "f32" = float front end fed the same int16 samples.
VARIANTS = {"r1": {"f32": "f32_r1", "int": "fx_r1"},
            "r2": {"f32": "f32_r2", "q15": "q15_r2", "q31": "q31_r2", "hyb": "hyb_r2"},
            "r4": {"f32": "f32_r4", "q15": "q15_r4", "q31": "q31_r4", "hyb": "hyb_r4"}}
RETRAIN = {"r1": ["int"], "r2": ["q15", "q31"], "r4": ["q15", "q31"]}
SEEDS = range(5)


def load() -> tuple[dict, dict]:
    F = np.load(REPO / "data" / "processed" / "feat_fx.npy")
    dev = {k: F[:, a:b].astype(np.float64) for k, (a, b) in COLS.items()}
    ref = {r: FE.cached(r).astype(np.float64) for r in ("r1", "r2", "r4")}
    return ref, dev


def fidelity(ref: dict, dev: dict) -> pd.DataFrame:
    rows = []
    for r, vs in VARIANTS.items():
        sd = ref[r].std(axis=0)
        sd[sd == 0] = 1.0
        for name, col in vs.items():
            z = np.abs(dev[col] - ref[r]) / sd
            rows.append({"rep": r, "front_end": name, "median_abs_z": float(np.median(z)),
                         "p99_abs_z": float(np.percentile(z, 99)), "max_abs_z": float(z.max()),
                         "worst_feature": int(np.argmax(z.max(axis=0)))})
    return pd.DataFrame(rows)


def estimator(a, seed):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.tree import DecisionTreeClassifier
    if a.family == "dt":
        return DecisionTreeClassifier(max_depth=a.hp["max_depth"], random_state=seed)
    return RandomForestClassifier(n_estimators=a.hp["n_estimators"], max_depth=a.hp["max_depth"],
                                  random_state=seed, n_jobs=1)


def one_job(a, p: str, k: int, ref: dict, dev: dict, y: np.ndarray, n_classes: int) -> list[dict]:
    sp = load_split(p, k)
    tr, te = sp["train"], sp["test"]
    r = a.rep
    rows = []

    def fit_eval(Xtr, Xte_by_name, seed, mode):
        mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
        sd[sd == 0] = 1.0
        est = estimator(a, seed).fit((Xtr - mu) / sd, y[tr])
        pred = est.predict if a.family == "dt" else (lambda X: rf_hard_predict(est, X))
        for name, Xte in Xte_by_name.items():
            yp = pred((Xte - mu) / sd)
            rows.append({"model_id": a.model_ids[0], "family": a.family, "rep": r, "protocol": p, "fold": k,
                         "seed": seed, "mode": mode, "front_end": name,
                         "f1_macro": metrics(y[te], yp, n_classes)["f1_macro"]})

    for seed in SEEDS:
        tests = {"ref": ref[r][te], **{n: dev[c][te] for n, c in VARIANTS[r].items()}}
        fit_eval(ref[r][tr], tests, seed, "deploy")
        for n in RETRAIN[r]:
            c = VARIANTS[r][n]
            fit_eval(dev[c][tr], {n: dev[c][te]}, seed, "retrain")
    return rows


def mlp_agreement(ref: dict, dev: dict) -> pd.DataFrame:
    import tensorflow as tf  # noqa: F401
    from train import tflite_predict
    reg = pd.read_csv(REPO / "models" / "registry.csv")
    sp = load_split("P2", 0)
    tr, te = sp["train"], sp["test"]
    y = manifest()[LABEL["P2"]].to_numpy()[te]
    rows = []
    for row in reg[(reg.family == "mlp") & reg.input_rep.isin(["r2", "r4"])].itertuples():
        path = REPO / "models" / row.model_id / "model.tflite"
        if not path.exists():
            continue
        blob = path.read_bytes()
        r = row.input_rep
        norm = FE.Normalizer.fit(ref[r][tr].astype(np.float32))   # as ml/train.py did for the deployed instance
        base, _ = tflite_predict(blob, norm(ref[r][te].astype(np.float32)))
        for name, col in VARIANTS[r].items():
            yp, _ = tflite_predict(blob, norm(dev[col][te].astype(np.float32)))
            rows.append({"model_id": row.model_id, "rep": r, "precision": row.precision, "front_end": name,
                         "agree_with_ref": float((yp == base).mean()),
                         "f1_ref": metrics(y, base, 10)["f1_macro"], "f1": metrics(y, yp, 10)["f1_macro"]})
    return pd.DataFrame(rows)


def figure() -> None:
    """Estimated front-end cycles per window on three core classes (results/fixedpoint/fx_cost_summary.csv)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    s = pd.read_csv(OUT / "fx_cost_summary.csv").set_index(["core", "region"]).cyc_med
    cores = [("m4f", "Cortex-M4F (FPU)"), ("m4nofp", "Cortex-M4, no FPU"), ("m0p", "Cortex-M0+")]
    variants = [("float", "#6b7684"), ("q31", "#1baf7a"), ("q15", "#eb6834"), ("hybrid", "#4a3aa7")]
    region = {("R1", "float"): "F32_R1", ("R1", "q31"): "FX_R1", ("R1", "q15"): "FX_R1", ("R1", "hybrid"): "FX_R1",
              ("R2", "float"): "F32_R2", ("R2", "q31"): "FX_R2_Q31", ("R2", "q15"): "FX_R2_Q15",
              ("R2", "hybrid"): "HYB_R2", ("R4", "float"): "F32_R4", ("R4", "q31"): "FX_R4_Q31",
              ("R4", "q15"): "FX_R4_Q15", ("R4", "hybrid"): "HYB_R4"}
    fig, axes = plt.subplots(1, 3, figsize=(7.16, 2.3), sharey=True)
    w = 0.2
    for ax, (core, title) in zip(axes, cores):
        for i, (v, col) in enumerate(variants):
            ys = [s[(core, region[(r, v)])] / 1e3 for r in ("R1", "R2", "R4")]
            ax.bar(np.arange(3) + (i - 1.5) * w, ys, w * 0.92, color=col, label=v, zorder=2)
        ax.set_xticks(range(3), ["R1", "R2", "R4"])
        ax.set_yscale("log")
        ax.set_title(title, fontsize=8)
        ax.grid(True, axis="y", which="major", lw=0.3, alpha=0.5, zorder=0)
    axes[0].set_ylabel("k cycles per window (est.)")
    axes[0].legend(fontsize=6, frameon=False, ncol=2, loc="upper left")
    fig.tight_layout()
    figs = REPO / "results" / "figs"
    for ext in ("pdf", "png"):
        fig.savefig(figs / f"fig_fx_cores.{ext}", dpi=300)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-j", type=int, default=2)
    ap.add_argument("--skip-mlp", action="store_true")
    ap.add_argument("--figure-only", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.figure_only:
        figure()
        return
    ref, dev = load()
    fid = fidelity(ref, dev)
    fid.to_csv(OUT / "fx_fidelity.csv", index=False)
    print(fid.to_string(index=False))

    archs = [x for x in MD.all_archs() if x.family in ("dt", "rf") and x.rep in ("r1", "r2", "r4")]
    man = manifest()
    todo = [(x, p, k) for x in archs for p, nk in PROTOCOLS.items() for k in range(nk)]
    print(f"{len(archs)} configurations, {len(todo)} (configuration, fold) jobs x {len(SEEDS)} seeds")
    res = Parallel(n_jobs=a.j, verbose=5)(
        delayed(one_job)(x, p, k, ref, dev, man[LABEL[p]].to_numpy().astype(np.int64), 10 if LABEL[p] == "class10" else 4)
        for x, p, k in todo)
    runs = pd.DataFrame([r for rs in res for r in rs])
    runs.to_csv(OUT / "fx_accuracy_runs.csv", index=False)
    acc = runs.groupby(["model_id", "family", "rep", "protocol", "mode", "front_end"]).f1_macro.agg(
        ["mean", "std", "count"]).reset_index()
    acc.to_csv(OUT / "fx_accuracy.csv", index=False)

    # paired change against the reference front end (same model, same fold, same seed)
    dep = runs[runs["mode"] == "deploy"].pivot_table(index=["model_id", "rep", "protocol", "fold", "seed"],
                                                     columns="front_end", values="f1_macro")
    summary = {"n_configs": len(archs), "n_runs_deploy": int(len(dep)), "deploy": {}, "retrain": {}}
    for r, vs in VARIANTS.items():
        d = dep.xs(r, level="rep")
        for n in vs:
            diff = (d[n] - d["ref"]).dropna()
            summary["deploy"][f"{r}_{n}"] = {"mean_dF1": float(diff.mean()), "min_dF1": float(diff.min()),
                                             "abs_mean_dF1": float(diff.abs().mean()),
                                             "share_identical": float((diff.abs() < 1e-12).mean())}
    ret = runs[runs["mode"] == "retrain"].set_index(["model_id", "rep", "protocol", "fold", "seed", "front_end"])
    base = runs[(runs["mode"] == "deploy") & (runs.front_end == "ref")].set_index(
        ["model_id", "rep", "protocol", "fold", "seed"]).f1_macro
    for (r, n), g in ret.groupby(level=["rep", "front_end"]):
        diff = g.f1_macro.droplevel("front_end") - base.reindex(g.index.droplevel("front_end"))
        summary["retrain"][f"{r}_{n}"] = {"mean_dF1": float(diff.mean()), "min_dF1": float(diff.min())}
    # best P3 configuration per representation, reference vs on-device front ends (deploy)
    best = {}
    for r in VARIANTS:
        m = acc[(acc.rep == r) & (acc.protocol == "P3") & (acc["mode"] == "deploy")]
        top = m[m.front_end == "ref"].sort_values("mean").iloc[-1].model_id
        best[r] = {"model_id": top, **{fe: float(m[(m.model_id == top) & (m.front_end == fe)]["mean"].iloc[0])
                                       for fe in ["ref", *VARIANTS[r]]}}
    summary["best_P3_deploy"] = best

    if not a.skip_mlp:
        mlp = mlp_agreement(ref, dev)
        mlp.to_csv(OUT / "fx_mlp_agreement.csv", index=False)
        summary["mlp_min_agreement"] = {n: float(g.agree_with_ref.min()) for n, g in mlp.groupby("front_end")}
        summary["mlp_mean_agreement"] = {n: float(g.agree_with_ref.mean()) for n, g in mlp.groupby("front_end")}
    (OUT / "fx_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    figure()


if __name__ == "__main__":
    main()
