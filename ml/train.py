#!/usr/bin/env python3
"""Host sweep: train every architecture on every protocol/fold/seed, score it, write results/host/*.json.

For each job (architecture, protocol, fold, seed):
  * inputs from ml/features.py, normalised with statistics of that fold's training windows only;
  * trees: scikit-learn, fitted on the training windows;
  * NNs: tf_keras, Adam lr 1e-3, batch 64, <= 100 epochs, early stopping on val loss (patience 10, best weights);
    converted to .tflite (fp32, int8 PTQ with 500 fixed training windows as representative data) and, for CNNs,
    fine-tuned with QAT (20 epochs, lr 1e-4) and converted again (int8qat). NN scores come from the .tflite file
    run in tf.lite.Interpreter, never from Keras (workstream doc: "Score the exported .tflite");
  * scored on the clean test set and on the same test windows with white Gaussian noise at SNR 20/10/5/0 dB
    (features recomputed from the noisy window). The noise draw depends only on (protocol, fold, SNR), so every
    model sees the same noisy windows.
The deployed instance (P2, fold 0, seed 0) also writes its artefacts to build/deploy/ for ml/export.py.

Output per model ID (CONTRACT.md 6.2): results/host/<model_id>__<P>__f<fold>__s<seed>.json
    {model_id, protocol, fold, seed, snr_db, f1_macro, accuracy, confusion, n_test, y_pred, noise[], train{}}

Usage:
    python ml/train.py --families dt rf --seeds 0 1 2 3 4
    python ml/train.py --families cnn --seeds 0 --shard 0/2      # run two shards in parallel, one per core
    python ml/train.py --list                                    # print the job list and what is done
"""
from __future__ import annotations

import os

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features as FE  # noqa: E402
import models as MD  # noqa: E402
from splits import LABEL, PROTOCOLS, load_split  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "results" / "host"
DEPLOY = REPO / "build" / "deploy"
SNRS = (20, 10, 5, 0)
DEPLOYED = ("P2", 0, 0)
N_REP = 500
EPOCHS, PATIENCE, BATCH, LR = 100, 10, 64, 1e-3
QAT_EPOCHS, QAT_LR = 20, 1e-4
SPE = 32          # steps_per_execution: fewer Python round trips; identical per-batch updates

_MANIFEST = None


def manifest() -> pd.DataFrame:
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = pd.read_parquet(REPO / "data" / "processed" / "manifest.parquet")
    return _MANIFEST


def out_path(model_id: str, p: str, k: int, s: int) -> Path:
    return HOST / f"{model_id}__{p}__f{k}__s{s}.json"


def jobs(families, seeds, protocols) -> list[tuple]:
    out = []
    for s in seeds:                                       # seed-major: seed 0 of everything first
        for a in MD.all_archs():
            if a.family not in families:
                continue
            for p in protocols:
                for k in range(PROTOCOLS[p]):
                    out.append((a, p, k, s))
    return out


def done(job) -> bool:
    a, p, k, s = job
    return all(out_path(m, p, k, s).exists() for m in a.model_ids)


# ------------------------------------------------------------------------------------------------------------------
# Data
# ------------------------------------------------------------------------------------------------------------------
class Data:
    """Normalised inputs for one (representation, protocol, fold)."""

    def __init__(self, rep: str, p: str, k: int):
        self.rep, self.p, self.k = rep, p, k
        self.sp = load_split(p, k)
        lab = manifest()[LABEL[p]].to_numpy().astype(np.int64)
        self.n_classes = 10 if LABEL[p] == "class10" else 4
        self.y = {s: lab[ids] for s, ids in self.sp.items()}
        W = FE.windows()
        if rep == "r0":
            self.norm = FE.R0Norm.fit(W[self.sp["train"]])
            self.X = {s: self.norm(W[ids]) for s, ids in self.sp.items()}
        else:
            F = FE.cached(rep)
            self.norm = FE.Normalizer.fit(F[self.sp["train"]])
            self.X = {s: self.norm(F[ids]) for s, ids in self.sp.items()}
        self._noisy = {}

    def test_noisy(self, snr: int) -> np.ndarray:
        if snr not in self._noisy:
            pi = list(PROTOCOLS).index(self.p)
            rng = np.random.default_rng([7, pi, self.k, snr])
            Xn = FE.add_noise(np.asarray(FE.windows()[self.sp["test"]]), snr, rng)
            self._noisy[snr] = self.norm(Xn) if self.rep == "r0" else self.norm(FE.compute(self.rep, Xn))
        return self._noisy[snr]

    def shaped(self, X: np.ndarray) -> np.ndarray:
        return X.reshape(-1, 1024, 1, 1) if self.rep == "r0" else X


def rep_ids(train_ids: np.ndarray, n: int = N_REP) -> np.ndarray:
    """Fixed representative-dataset rows (indices into the training set) for PTQ calibration."""
    rng = np.random.default_rng(12345)
    return np.sort(rng.choice(len(train_ids), size=min(n, len(train_ids)), replace=False))


# ------------------------------------------------------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------------------------------------------------------
def metrics(y, yp, n_classes) -> dict:
    return {"f1_macro": float(f1_score(y, yp, average="macro", labels=list(range(n_classes)), zero_division=0)),
            "accuracy": float(accuracy_score(y, yp))}


def record(model_id, p, k, s, d: Data, yp, noisy: dict, train_info: dict) -> dict:
    y = d.y["test"]
    r = {"model_id": model_id, "protocol": p, "fold": k, "seed": s, "snr_db": None, **metrics(y, yp, d.n_classes),
         "confusion": confusion_matrix(y, yp, labels=list(range(d.n_classes))).tolist(), "n_test": int(len(y)),
         "y_pred": "".join(str(int(v)) for v in yp),
         "noise": [{"snr_db": snr, **metrics(y, noisy[snr], d.n_classes)} for snr in SNRS],
         "train": train_info}
    return r


def write(r: dict) -> None:
    HOST.mkdir(parents=True, exist_ok=True)
    path = out_path(r["model_id"], r["protocol"], r["fold"], r["seed"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(r, separators=(",", ":")) + "\n")
    tmp.rename(path)


# ------------------------------------------------------------------------------------------------------------------
# Trees
# ------------------------------------------------------------------------------------------------------------------
def rf_hard_predict(est, X: np.ndarray) -> np.ndarray:
    """The deployed forest's rule (emlearn 0.23 inline C): each tree votes its leaf's majority class; the class with
    the most votes wins; ties go to the lowest class index. scikit-learn's own predict() averages leaf probabilities
    instead (soft voting), which can pick a different class, so it is NOT used for reported scores."""
    votes = np.zeros((len(X), len(est.classes_)), np.int32)
    rows = np.arange(len(X))
    for t in est.estimators_:
        votes[rows, t.predict(X).astype(np.int64)] += 1
    return est.classes_[votes.argmax(axis=1)].astype(np.int64)


def run_tree(a: MD.Arch, p, k, s, d: Data) -> None:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.tree import DecisionTreeClassifier
    t0 = time.time()
    if a.family == "dt":
        est = DecisionTreeClassifier(max_depth=a.hp["max_depth"], random_state=s)
    else:
        est = RandomForestClassifier(n_estimators=a.hp["n_estimators"], max_depth=a.hp["max_depth"],
                                     random_state=s, n_jobs=1)
    est.fit(d.X["train"], d.y["train"])
    fit_s = time.time() - t0
    predict = est.predict if a.family == "dt" else (lambda X: rf_hard_predict(est, X))
    yp = predict(d.X["test"])
    noisy = {snr: predict(d.test_noisy(snr)) for snr in SNRS}
    if a.family == "dt":
        info = {"fit_s": round(fit_s, 3), "n_nodes": int(est.tree_.node_count), "depth": int(est.get_depth()),
                "n_leaves": int(est.get_n_leaves())}
    else:
        soft = est.predict(d.X["test"])
        info = {"fit_s": round(fit_s, 3), "n_nodes": int(sum(e.tree_.node_count for e in est.estimators_)),
                "depth": int(max(e.get_depth() for e in est.estimators_)),
                "voting": "hard (emlearn)", "f1_soft_voting": metrics(d.y["test"], soft, d.n_classes)["f1_macro"],
                "soft_hard_disagree": int((soft != yp).sum())}
    write(record(a.model_ids[0], p, k, s, d, yp, noisy, info))
    if (p, k, s) == DEPLOYED:
        import joblib
        DEPLOY.mkdir(parents=True, exist_ok=True)
        joblib.dump({"estimator": est, "mean": d.norm.mean, "std": d.norm.std}, DEPLOY / f"{a.key}.joblib")


# ------------------------------------------------------------------------------------------------------------------
# Neural networks
# ------------------------------------------------------------------------------------------------------------------
def tflite_convert(model, rep_x: np.ndarray | None) -> bytes:
    import tensorflow as tf
    c = tf.lite.TFLiteConverter.from_keras_model(model)
    if rep_x is not None:
        c.optimizations = [tf.lite.Optimize.DEFAULT]
        c.representative_dataset = lambda: ([rep_x[i:i + 1]] for i in range(len(rep_x)))
        c.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
        c.inference_input_type = tf.int8
        c.inference_output_type = tf.int8
    return c.convert()


def tflite_predict(blob: bytes, X: np.ndarray, ref: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Run a .tflite on the host, one window per Invoke() as on the MCU. Returns (argmax, dequantised outputs).

    ref=True uses TFLite's reference kernels (BUILTIN_REF), whose integer arithmetic matches TFLM on the board
    bit for bit (checked on the virtual Cortex-M4). The default optimised kernels round differently in int8 CNNs
    (up to ~19 output steps); ml/check_ref_kernels.py bounds what that does to the reported scores."""
    import tensorflow as tf
    kw = {"experimental_op_resolver_type": tf.lite.experimental.OpResolverType.BUILTIN_REF} if ref else {}
    it = tf.lite.Interpreter(model_content=blob, num_threads=1, **kw)
    it.allocate_tensors()
    inp, out = it.get_input_details()[0], it.get_output_details()[0]
    xin = X.reshape(len(X), *inp["shape"][1:]).astype(np.float32, copy=False)
    if inp["dtype"] == np.int8:
        sc, zp = inp["quantization"]
        xin = np.clip(np.round(xin / sc) + zp, -128, 127).astype(np.int8)
    O = np.empty((len(X), int(np.prod(out["shape"][1:]))), np.float32)
    for i in range(len(X)):
        it.set_tensor(inp["index"], xin[i:i + 1])
        it.invoke()
        O[i] = it.get_tensor(out["index"]).reshape(-1)
    if out["dtype"] == np.int8:
        sc, zp = out["quantization"]
        O = (O - zp) * sc
    return O.argmax(axis=1), O


def run_nn(a: MD.Arch, p, k, s, d: Data) -> None:
    import tensorflow as tf
    import tf_keras as keras
    keras.backend.clear_session()
    keras.utils.set_random_seed(s)
    try:
        tf.config.experimental.enable_op_determinism()
    except Exception:  # noqa: BLE001
        pass
    Xtr, Xva = d.shaped(d.X["train"]), d.shaped(d.X["val"])
    Xte = d.shaped(d.X["test"])
    noisy_X = {snr: d.shaped(d.test_noisy(snr)) for snr in SNRS}
    model = MD.build(a, d.n_classes, Xtr.shape[1] if a.rep != "r0" else 1024)
    model.compile(keras.optimizers.Adam(LR), "sparse_categorical_crossentropy", metrics=["accuracy"],
                  steps_per_execution=SPE)
    es = keras.callbacks.EarlyStopping(monitor="val_loss", patience=PATIENCE, restore_best_weights=True)
    t0 = time.time()
    h = model.fit(Xtr, d.y["train"], validation_data=(Xva, d.y["val"]), batch_size=BATCH, epochs=EPOCHS,
                  callbacks=[es], verbose=0, shuffle=True)
    fit_s = time.time() - t0
    vl = h.history["val_loss"]
    info = {"fit_s": round(fit_s, 1), "epochs": len(vl), "best_epoch": int(np.argmin(vl)) + 1,
            "val_loss_best": float(np.min(vl))}
    params, macs = MD.nn_cost(a, d.n_classes, Xtr.shape[1] if a.rep != "r0" else 1024)
    info.update(params=params, macs=macs)
    rep_x = Xtr[rep_ids(d.sp["train"])]

    blobs = {"fp32": tflite_convert(model, None), "int8": tflite_convert(model, rep_x)}
    if a.family == "cnn":
        import tensorflow_model_optimization as tfmot
        q = tfmot.quantization.keras.quantize_model(model)
        q.compile(keras.optimizers.Adam(QAT_LR), "sparse_categorical_crossentropy", metrics=["accuracy"],
                  steps_per_execution=SPE)
        t1 = time.time()
        q.fit(Xtr, d.y["train"], batch_size=BATCH, epochs=QAT_EPOCHS, verbose=0, shuffle=True)
        info["qat_s"] = round(time.time() - t1, 1)
        blobs["int8qat"] = tflite_convert(q, rep_x)

    for prec, blob in blobs.items():
        mid = f"{a.key}-{prec}"
        yp, _ = tflite_predict(blob, Xte)
        noisy = {snr: tflite_predict(blob, X)[0] for snr, X in noisy_X.items()}
        r = record(mid, p, k, s, d, yp, noisy, {**info, "tflite_B": len(blob)})
        write(r)
        if prec == "int8":
            write({**r, "model_id": f"{a.key}-int8ref", "note": "same .tflite as int8; TFLM reference kernels "
                   "change hardware cost only"} if a.family == "cnn" else r)
        if (p, k, s) == DEPLOYED:
            DEPLOY.mkdir(parents=True, exist_ok=True)
            (DEPLOY / f"{mid}.tflite").write_bytes(blob)
            np.savez(DEPLOY / f"{a.key}.norm.npz", **({"mu": d.norm.mu, "sigma": d.norm.sigma} if a.rep == "r0"
                                                      else {"mean": d.norm.mean, "std": d.norm.std}))
    keras.backend.clear_session()


# ------------------------------------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--families", nargs="+", default=["dt", "rf", "mlp", "cnn"])
    ap.add_argument("--archs", nargs="+", help="restrict to these architecture keys (e.g. cnn-r0-w025d2)")
    ap.add_argument("--seeds", nargs="+", type=int, default=[0])
    ap.add_argument("--protocols", nargs="+", default=list(PROTOCOLS))
    ap.add_argument("--shard", default="0/1", help="i/n: run every n-th job starting at i")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--deadline", type=float, default=0,
                    help="unix time after which no new job is started (lets a capped runner exit cleanly)")
    a = ap.parse_args()

    J = jobs(a.families, a.seeds, a.protocols)
    if a.archs:
        J = [j for j in J if j[0].key in a.archs]
    if a.list:
        n_done = sum(done(j) for j in J)
        print(f"{len(J)} jobs, {n_done} done")
        return
    i, n = map(int, a.shard.split("/"))
    J = [j for idx, j in enumerate(J) if idx % n == i and not done(j)]
    if any(x.is_nn for x, *_ in J):
        import tensorflow as tf
        tf.config.threading.set_intra_op_parallelism_threads(a.threads)
        tf.config.threading.set_inter_op_parallelism_threads(a.threads)
    cache: dict = {}
    t_start = time.time()
    for idx, (arch, p, k, s) in enumerate(J, 1):
        if a.deadline and time.time() > a.deadline:
            print(f"deadline reached; {len(J) - idx + 1} jobs left for the next run", flush=True)
            break
        key = (arch.rep, p, k)
        if key not in cache:
            cache.clear()
            cache[key] = Data(arch.rep, p, k)
        t0 = time.time()
        try:
            (run_tree if arch.family in ("dt", "rf") else run_nn)(arch, p, k, s, cache[key])
            r = json.loads(out_path(arch.model_ids[0], p, k, s).read_text())
            print(f"[{idx}/{len(J)}] {arch.key:<16} {p}/f{k}/s{s}  F1={r['f1_macro']:.4f}  "
                  f"{time.time() - t0:6.1f}s  (total {(time.time() - t_start) / 60:.1f} min)", flush=True)
        except Exception:  # noqa: BLE001 - log and continue; the job stays "not done" and is retried next run
            print(f"[{idx}/{len(J)}] {arch.key} {p}/f{k}/s{s} FAILED", flush=True)
            traceback.print_exc()


if __name__ == "__main__":
    main()
