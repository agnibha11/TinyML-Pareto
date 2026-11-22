#!/usr/bin/env python3
"""Cascade export for the board (Part B4; docs/cascade_preregistration.md §4): gate parameters, the GVERIFY
expectations and the replay streams of one dataset.

Everything is fitted on the deployed instance's split (CWRU P2 fold 0, Paderborn P3 fold 0; ml/dataset.py DEPLOYED),
so the gate sees the same unseen operating point as the stage-2 models on the board. Gates G1-G4 are exactly those of
ml/cascade.py (asserted against cascade.fit_gates); G5 is the self-calibrating healthy-only gate:

  G1 sup      depth-2 tree on the 10 fx_r1 features, alarm leaves = leaves whose fault probability reaches the lowest
              value seen on validation faults
  G2 healthy  Mahalanobis distance of the 9 gate features (log rms/std/peak/p2p, crest, kurtosis, skewness, shape,
              impulse) to the training split's healthy windows, ridge 1e-3, alarm above the 99th percentile of the
              validation healthy distances
  G3 either   G1 or G2
  G4 either2  G3 on this window and on the previous window of the stream (state reset at the start of a stream)
  G5 selfcal  CALIB n on the board: mean and covariance (ridge 1e-3) of the first n healthy pool windows, threshold =
              rho x median of their distances, rho = G2 threshold / median validation healthy distance (factory)
  G6 selfcal_r2  Paderborn only (pre-registration, Deviation 4): G5 on 23 features, the 9 gate features + the 14 log
              band powers of fx_r2_hyb (hyb_r2 block of feat_fx.npy), calibrated on G6_N = 128 healthy pool windows;
              its factory part is rho from the same Mahalanobis on the training split's healthy windows

Windows are int16 (tools/fxfe/run_fxfe.py to_int16, what a sensor delivers). Gate features come from fx_r1 in
data/processed/feat_fx.npy (QEMU, or tools/local/fxr1_host on a machine without it; both bit-identical on CWRU).

Pool, CWRU: every non-overlapping test window of the first healthy test recording (at most 64), then FAULT_RUN
consecutive test windows from the middle of each faulty test recording (at most 9). Paderborn (test windows are an
evenly spaced subsample, so they are not consecutive): the first 128 windows in time order of the first healthy
(bearing, condition) of the test split, then FAULT_RUN consecutive windows from the middle of one recording per faulty
test bearing, round-robin over bearings (at most 9). Every pool window belongs to a test bearing (asserted: none is
in train or validation). Streams are lists of pool indices (§4.6):
  S0 100 healthy | S2 one run of 2 faults in 100 | S10 five runs of 2 in 100 | S100 100 faults | ST 50 healthy, 50 faults
Healthy positions cycle through the healthy pool; fault runs cycle through the faulty recordings. Run positions use
SEED. A 2-of-2 gate needs runs: a single fault window can never escalate, hence runs of 2.

Outputs (handoff/cascade/<dataset>/):
  gate_params.h, gate_params.json   parameters of G1, G2, G5 (G3/G4 are logic); float32 thresholds rounded so that
                                    float32 comparisons on the board give exactly the float64 host decisions (trees)
  pool_int16.npy, pool.csv          the window pool and its provenance
  streams.csv                       stream, position, pool index, fault flag, expected alarm of every gate
  gverify_int16.npy, gverify.csv    GVERIFY vectors (handoff test vectors + stored inputs + pool) with the expected
                                    fx_r1 features, scores and alarms, and the relative margin to each threshold
  summary.json                      host TPR/FPR of every gate on the deployed test split; G5 for n = 16, 32 on the
                                    healthy windows after the calibration span; flash budget

    python ml/cascade_export.py                        # CWRU
    TINYML_DATASET=pu python ml/cascade_export.py      # Paderborn
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as DS  # noqa: E402
from cascade import FX_R1_COLS, fit_gates, gate_features  # noqa: E402
from cascade_design import recordings  # noqa: E402
from splits import load_split  # noqa: E402
from train import manifest  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.fxfe.run_fxfe import to_int16  # noqa: E402

OUT = REPO / "handoff" / "cascade" / DS.NAME
SEED = 20261121
POOL_HEALTHY_MAX = 64 if DS.NAME == "cwru" else 128
HYB_R2_COLS = (215, 239)      # fx_r2_hyb block of feat_fx.npy (tools/fxfe/run_fxfe.py COLS)
G6_N = 128 if DS.NAME == "pu" else None
FAULT_RECS_MAX = 9
FAULT_RUN = 4
STREAM_LEN = 100
CALIB_N = (16, 32)
RIDGE = 1e-3
WIN = 1024


# ------------------------------------------------------------------------------------------------------------- gates
def f32_le(x: float) -> np.float32:
    """Largest float32 <= x: for float32 v, v <= f32_le(x) exactly when v <= x."""
    f = np.float32(x)
    return f if float(f) <= x else np.nextafter(f, np.float32(-np.inf))


class Mahal:
    def __init__(self, H: np.ndarray):
        self.mu = H.mean(axis=0)
        self.ci = np.linalg.inv(np.cov(H.T) + RIDGE * np.eye(H.shape[1]))

    def __call__(self, X: np.ndarray) -> np.ndarray:
        d = X - self.mu
        return np.einsum("ij,jk,ik->i", d, self.ci, d)

    def f32(self, X: np.ndarray) -> np.ndarray:
        """The board's arithmetic: float32 parameters and features, float32 accumulation."""
        mu, ci = self.mu.astype(np.float32), self.ci.astype(np.float32)
        d = X.astype(np.float32) - mu
        return np.einsum("ij,jk,ik->i", d, ci, d, dtype=np.float32)


def fit(F: np.ndarray, yb: np.ndarray, sp: dict) -> dict:
    from sklearn.tree import DecisionTreeClassifier
    tr, va = sp["train"], sp["val"]
    t = DecisionTreeClassifier(max_depth=2, random_state=0, class_weight="balanced").fit(F[tr], yb[tr])
    th = t.predict_proba(F[va])[:, 1][yb[va] == 1].min()
    G = gate_features(F)
    m = Mahal(G[tr][yb[tr] == 0])
    dv = m(G[va][yb[va] == 0])
    thr = float(np.percentile(dv, 99))
    return {"tree": t, "tree_th": float(th), "mahal": m, "mahal_thr": thr, "rho": thr / float(np.median(dv))}


def tree_arrays(t, th: float, X: np.ndarray) -> dict:
    """Board tables of the tree. A leaf alarms when sklearn's predict_proba of a training sample in that leaf reaches
    th: recomputing the probability from tree_.value can round 1e-14 below th (Paderborn P3 fold 0)."""
    s = t.tree_
    p = s.value[:, 0, :] / s.value[:, 0, :].sum(axis=1, keepdims=True)
    leaf = s.children_left < 0
    lv = t.apply(X)
    pr = t.predict_proba(X)[:, 1]
    p_sk = {int(n): float(pr[lv == n][0]) for n in np.unique(lv)}
    return {"feature": [int(f) if not lf else -1 for f, lf in zip(s.feature, leaf)],
            "threshold_f64": [float(x) if not lf else 0.0 for x, lf in zip(s.threshold, leaf)],
            "threshold_f32": [float(f32_le(x)) if not lf else 0.0 for x, lf in zip(s.threshold, leaf)],
            "left": [int(x) for x in s.children_left], "right": [int(x) for x in s.children_right],
            "p_fault": [float(x) for x in p[:, 1]],
            "alarm": [int(lf and p_sk.get(i, p[i, 1]) >= th) for i, lf in enumerate(leaf)]}


def tree_f32(ta: dict, F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Board tree walk in float32: (alarm, smallest |x - thr| / |thr| on the path)."""
    X = F.astype(np.float32)
    al, mg = np.zeros(len(X), bool), np.full(len(X), np.inf)
    for i, x in enumerate(X):
        n = 0
        while ta["left"][n] >= 0:
            f, th = ta["feature"][n], np.float32(ta["threshold_f32"][n])
            mg[i] = min(mg[i], abs(float(x[f]) - float(th)) / max(abs(float(th)), 1e-30))
            n = ta["left"][n] if x[f] <= th else ta["right"][n]
        al[i] = bool(ta["alarm"][n])
    return al, mg


# -------------------------------------------------------------------------------------------------------------- pool
def build_pool_pu(man: pd.DataFrame, te: np.ndarray, yb: np.ndarray, grp: np.ndarray, tkey: np.ndarray,
                  seen: np.ndarray) -> pd.DataFrame:
    teh = te[yb[te] == 0]
    hg = int(np.min(grp[teh]))
    hw = np.flatnonzero(grp == hg)
    hw = hw[np.argsort(tkey[hw], kind="stable")][:POOL_HEALTHY_MAX]
    assert len(hw) == POOL_HEALTHY_MAX
    rows = [man.iloc[hw].assign(kind="healthy", run=0)]
    tef = te[yb[te] == 1]
    per_bearing = {}
    for w in tef[np.argsort(man.file_id.to_numpy()[tef], kind="stable")]:
        b = man.bearing.iloc[w]
        f = int(man.file_id.iloc[w])
        if f not in per_bearing.setdefault(b, []):
            per_bearing[b].append(f)
    picks, r = [], 0
    while len(picks) < FAULT_RECS_MAX and any(r < len(v) for v in per_bearing.values()):
        picks += [v[r] for _, v in sorted(per_bearing.items()) if r < len(v)]
        r += 1
    for run, f in enumerate(picks[:FAULT_RECS_MAX]):
        s = np.flatnonzero(man.file_id.to_numpy() == f)
        s = s[np.argsort(tkey[s])]
        a = (len(s) - FAULT_RUN) // 2
        rows.append(man.iloc[s[a:a + FAULT_RUN]].assign(kind="fault", run=run))
    p = pd.concat(rows, ignore_index=True)
    for (k, r_), g in p.groupby(["kind", "run"]):
        if k == "fault":
            assert (np.diff(g.start_idx) == WIN).all(), (k, r_)
    assert not np.isin(p.window_id.to_numpy(), seen).any(), "a pool window is in train/val"
    return p[["kind", "run", "window_id", "file_id", "bearing", "condition", "recording", DS.LABEL[DS.DEPLOYED[0]],
              "start_idx"]]


def build_pool(man: pd.DataFrame, te: np.ndarray, yb: np.ndarray) -> pd.DataFrame:
    t = man.iloc[te].assign(win_idx=te).sort_values(["file_id", "start_idx"])
    hf = sorted(t[yb[t.win_idx] == 0].file_id.unique())[0]
    h = t[t.file_id == hf].head(POOL_HEALTHY_MAX).assign(kind="healthy", run=0)
    rows = [h]
    for r, f in enumerate(sorted(t[yb[t.win_idx] == 1].file_id.unique())[:FAULT_RECS_MAX]):
        s = t[t.file_id == f]
        a = (len(s) - FAULT_RUN) // 2
        rows.append(s.iloc[a:a + FAULT_RUN].assign(kind="fault", run=r))
    p = pd.concat(rows, ignore_index=True)
    # consecutive within a recording: non-overlapping test windows are WIN apart
    for (k, r), g in p.groupby(["kind", "run"]):
        assert (np.diff(g.start_idx) == WIN).all(), (k, r)
    assert (p.win_idx.to_numpy() == p.window_id.to_numpy()).all()       # manifest row = window id
    return p[["kind", "run", "window_id", "file_id", DS.LABEL[DS.DEPLOYED[0]], "load_hp", "start_idx"]]


def build_streams(pool: pd.DataFrame) -> dict[str, list[int]]:
    H = pool.index[pool.kind == "healthy"].to_list()
    runs = [pool.index[(pool.kind == "fault") & (pool.run == r)].to_list() for r in sorted(pool[pool.kind == "fault"].run.unique())]
    rng = np.random.default_rng(SEED)

    def healthy(n, start=0):
        return [H[(start + i) % len(H)] for i in range(n)]

    def faults(n):
        flat = [w for r in runs for w in r]
        return [flat[i % len(flat)] for i in range(n)]

    def with_runs(k):
        """k runs of 2 fault windows at seeded, non-touching positions of a healthy stream."""
        while True:
            pos = np.sort(rng.choice(STREAM_LEN - 1, size=k, replace=False))
            if k == 1 or np.diff(pos).min() >= 3:
                break
        s = healthy(STREAM_LEN)
        for i, p in enumerate(pos):
            r = runs[i % len(runs)]
            s[p], s[p + 1] = r[0], r[1]
        return s

    return {"S0": healthy(STREAM_LEN), "S2": with_runs(1), "S10": with_runs(5), "S100": faults(STREAM_LEN),
            "ST": healthy(STREAM_LEN // 2) + faults(STREAM_LEN - STREAM_LEN // 2)}


def confirm2(al: np.ndarray) -> np.ndarray:
    return al & np.r_[False, al[:-1]]


# ------------------------------------------------------------------------------------------------------------ export
def c_array(name: str, ctype: str, vals, per_line: int = 6) -> str:
    fmt = (lambda v: f"{np.float32(v):.9e}f") if ctype == "float" else str
    items = [fmt(v) for v in vals]
    lines = [", ".join(items[i:i + per_line]) for i in range(0, len(items), per_line)]
    return f"static const {ctype} {name}[{len(items)}] = {{\n  " + ",\n  ".join(lines) + "\n};\n"


def header(g: dict, ta: dict, pinfo: str) -> str:
    m = g["mahal"]
    n = len(ta["feature"])
    return "\n".join([
        f"// gate_params.h - GENERATED by ml/cascade_export.py; do not edit.",
        f"// {DS.NAME.upper()} cascade gates, fitted on {pinfo} (docs/cascade_preregistration.md §4.2).",
        "// Features: fx_r1 out10 (firmware/bench/frontend_fx.h). Gate features g9 = {logf(out[1..4]), out[5..9]}.",
        "#pragma once",
        "#include <stdint.h>",
        "",
        "// G1 sup: depth-2 tree on out10; a node is a leaf when left < 0. Go left when x[feature] <= threshold.",
        "// Thresholds are the largest float32 <= the float64 split, so float32 walks match the host exactly.",
        f"#define CASC_TREE_NODES {n}",
        c_array("g_casc_tree_feature", "int8_t", ta["feature"], 16),
        c_array("g_casc_tree_threshold", "float", ta["threshold_f32"]),
        c_array("g_casc_tree_left", "int8_t", ta["left"], 16),
        c_array("g_casc_tree_right", "int8_t", ta["right"], 16),
        c_array("g_casc_tree_alarm", "uint8_t", ta["alarm"], 16),
        "// G2 healthy: d = (g9 - mu)^T ci (g9 - mu); alarm when d > thr. ci = inv(cov + 1e-3 I), row-major 9 x 9.",
        c_array("g_casc_mahal_mu", "float", m.mu),
        c_array("g_casc_mahal_ci", "float", m.ci.ravel()),
        f"static const float g_casc_mahal_thr = {np.float32(g['mahal_thr']):.9e}f;",
        "// G5 selfcal: after CALIB n, thr = rho * median(d of the n calibration windows), mu/ci from those windows.",
        f"static const float g_casc_selfcal_rho = {np.float32(g['rho']):.9e}f;",
        f"#define CASC_RIDGE {RIDGE:.1e}f",
        *([] if "rho6" not in g else [
            "// G6 selfcal_r2 (Paderborn, pre-registration Deviation 4): G5 on g23 = {g9, fx_r2_hyb out[10..23]};",
            "// CALIB n with gate 6 selected: mu/ci/thr from n healthy pool windows, thr = rho6 * median(d).",
            "#define CASC_HAS_G6 1",
            "#define CASC_G6_NF 23",
            f"#define CASC_G6_N {G6_N}",
            f"static const float g_casc_g6_rho = {np.float32(g['rho6']):.9e}f;"]),
        ""])


def main() -> None:
    P, K, _ = DS.DEPLOYED
    OUT.mkdir(parents=True, exist_ok=True)
    man = manifest()
    rec, grp, tkey = recordings(man)
    if "file_id" not in man:     # Paderborn: a recording is (bearing, condition, recording)
        man = man.assign(file_id=rec)
    yb = (man[DS.LABEL[P]].to_numpy() != 0).astype(int)     # 0 = healthy in every label set
    F = np.load(DS.PROCESSED / "feat_fx.npy", mmap_mode="r")[:, slice(*FX_R1_COLS)].astype(np.float64)
    assert np.isfinite(F).all(), "feat_fx.npy fx_r1 block is not complete"
    W = np.load(DS.PROCESSED / "windows.npy", mmap_mode="r")
    sp = load_split(P, K)
    te = sp["test"]
    g = fit(F, yb, sp)
    ta = tree_arrays(g["tree"], g["tree_th"], F[sp["train"]])
    G = gate_features(F)
    if G6_N:
        HB = np.load(DS.PROCESSED / "feat_fx.npy", mmap_mode="r")[:, slice(*HYB_R2_COLS)].astype(np.float64)
        assert np.isfinite(HB).all(), "feat_fx.npy hyb_r2 block is not complete (tools/local/fxr1_host/run_fxhyb.py)"
        assert np.array_equal(HB[:, :10], F), "fx_r2_hyb's R1 part must equal fx_r1"
        X6 = np.column_stack([G, HB[:, 10:]])
        tr, va = sp["train"], sp["val"]
        m6 = Mahal(X6[tr][yb[tr] == 0])
        dv6 = m6(X6[va][yb[va] == 0])
        g["rho6"] = float(np.percentile(dv6, 99)) / float(np.median(dv6))

    # host decisions on the deployed test split, and identity with ml/cascade.py
    sup = g["tree"].predict_proba(F[te])[:, 1] >= g["tree_th"]
    hea = g["mahal"](G[te]) > g["mahal_thr"]
    ref = fit_gates(F, yb, sp, man)
    assert (ref["sup"] == sup).all() and (ref["healthy"] == hea).all(), "export differs from ml/cascade.py"
    assert (tree_f32(ta, F[te])[0] == sup).all(), "float32 tree walk differs from sklearn"
    yt = yb[te]
    rates = {k: {"tpr": float(a[yt == 1].mean()), "fpr": float(a[yt == 0].mean())}
             for k, a in (("sup", sup), ("healthy", hea), ("either", ref["either"]), ("either2", ref["either2"]))}

    # pool, streams
    if DS.NAME == "cwru":
        pool = build_pool(man, te, yb)
    else:
        pool = build_pool_pu(man, te, yb, grp, tkey, np.r_[sp["train"], sp["val"]])
    pid = pool.window_id.to_numpy()
    Hp = pid[(pool.kind == "healthy").to_numpy()]
    calib = {n: Mahal(G[Hp[:n]]) for n in CALIB_N}
    cal_thr = {n: g["rho"] * float(np.median(calib[n](G[Hp[:n]]))) for n in CALIB_N}
    if G6_N:
        calib6 = Mahal(X6[Hp[:G6_N]])
        thr6 = g["rho6"] * float(np.median(calib6(X6[Hp[:G6_N]])))

    # selfcal host evaluation: healthy test windows of the calibration recording / group after the calibration span
    # + every fault test window of the split
    hg = grp[Hp[0]]
    hte = te[(grp[te] == hg) & (yt == 0)]
    hte = hte[np.argsort(tkey[hte], kind="stable")]
    for n in CALIB_N + ((G6_N,) if G6_N else ()):
        ev = np.r_[hte[tkey[hte] > tkey[Hp[n - 1]]], te[yt == 1]]
        if n == G6_N:
            a, y = calib6(X6[ev]) > thr6, yb[ev]
            rates["selfcal_r2"] = {"tpr": float(a[y == 1].mean()), "fpr": float(a[y == 0].mean()),
                                   "n_eval_healthy": int((y == 0).sum()), "n_calib": G6_N}
            continue
        a = calib[n](G[ev]) > cal_thr[n]
        y = yb[ev]
        a0 = g["mahal"](G[ev]) > g["mahal_thr"]
        rates[f"selfcal{n}"] = {"tpr": float(a[y == 1].mean()), "fpr": float(a[y == 0].mean()),
                                "n_eval_healthy": int((y == 0).sum()), "fpr_factory_same_windows": float(a0[y == 0].mean())}

    def base_alarms(ids):
        s = g["tree"].predict_proba(F[ids])[:, 1] >= g["tree_th"]
        h = g["mahal"](G[ids]) > g["mahal_thr"]
        out = {"sup": s, "healthy": h, "either": s | h}
        for n in CALIB_N:
            out[f"selfcal{n}"] = calib[n](G[ids]) > cal_thr[n]
        if G6_N:
            out["selfcal_r2"] = calib6(X6[ids]) > thr6
        return out

    streams = build_streams(pool)
    srows = []
    for name, idx in streams.items():
        ids = pid[idx]
        al = base_alarms(ids)
        al["either2"] = confirm2(al["either"])
        for pos, (pi, w) in enumerate(zip(idx, ids)):
            srows.append({"stream": name, "pos": pos, "pool_idx": int(pi), "window_id": int(w), "fault": int(yb[w]),
                          **{k: int(v[pos]) for k, v in al.items()}})
    sdf = pd.DataFrame(srows)
    order = ["stream", "pos", "pool_idx", "window_id", "fault", "sup", "healthy", "either", "either2"] + \
        [f"selfcal{n}" for n in CALIB_N] + (["selfcal_r2"] if G6_N else [])
    sdf = sdf[order]

    # GVERIFY vectors: handoff test vectors + stored inputs + pool (unique, in that order)
    tv = REPO / "handoff" / ("test_vectors" if DS.NAME == "cwru" else f"{DS.NAME}/test_vectors")
    if DS.NAME == "cwru":
        stored = [int(x["window_id"]) for x in json.loads((tv / "stored_inputs.json").read_text())["inputs"]]
    else:   # INPUT 0..4 of the Paderborn sketch: rows PU_STORED of the test vectors (tools/bundle.py)
        from tools.bundle import PU_STORED
        stored = [int(np.load(tv / "ids.npy")[t]) for t in PU_STORED]
    vids = list(dict.fromkeys([int(x) for x in np.load(tv / "ids.npy")] + stored + [int(x) for x in pid]))
    vids = np.array(vids)
    W16 = to_int16(W[vids])
    al = base_alarms(vids)
    t32, tmg = tree_f32(ta, F[vids])
    d32 = g["mahal"].f32(G[vids])
    d64 = g["mahal"](G[vids])
    gv = pd.DataFrame({"vector": np.arange(len(vids)), "window_id": vids, "fault": yb[vids],
                       "source": ["stored" if v in stored else "pool" if v in set(pid) else "test_vector" for v in vids]})
    for i in range(10):
        gv[f"fx{i}"] = F[vids, i].astype(np.float32)
    gv["sup"] = al["sup"].astype(int)
    gv["sup_margin_rel"] = tmg
    gv["mahal_d"] = d64
    gv["healthy"] = al["healthy"].astype(int)
    gv["healthy_margin_rel"] = np.abs(d64 - g["mahal_thr"]) / g["mahal_thr"]
    gv["either"] = al["either"].astype(int)
    for n in CALIB_N:
        dn = calib[n](G[vids])
        gv[f"selfcal{n}_d"] = dn
        gv[f"selfcal{n}"] = al[f"selfcal{n}"].astype(int)
        gv[f"selfcal{n}_margin_rel"] = np.abs(dn - cal_thr[n]) / cal_thr[n]
    if G6_N:
        for i in range(14):
            gv[f"hb{i}"] = HB[vids, 10 + i].astype(np.float32)
        d6 = calib6(X6[vids])
        gv["selfcal_r2_d"] = d6
        gv["selfcal_r2"] = al["selfcal_r2"].astype(int)
        gv["selfcal_r2_margin_rel"] = np.abs(d6 - thr6) / thr6
    assert (t32 == al["sup"]).all()
    f32_flip = int(((d32 > np.float32(g["mahal_thr"])) != al["healthy"]).sum())

    # write
    pinfo = f"{P} fold {K} (train/val), deployed instance of {DS.NAME.upper()}"
    (OUT / "gate_params.h").write_text(header(g, ta, pinfo))
    (OUT / "gate_params.json").write_text(json.dumps({
        "dataset": DS.NAME, "protocol": P, "fold": K, "seed": SEED, "tree": ta, "tree_threshold_p": g["tree_th"],
        "mahal": {"mu": g["mahal"].mu.tolist(), "ci": g["mahal"].ci.tolist(), "thr": g["mahal_thr"], "ridge": RIDGE},
        "selfcal": {"rho": g["rho"], "calib_n": list(CALIB_N), "thr_after_calib": {str(n): cal_thr[n] for n in CALIB_N},
                    "mu_after_calib": {str(n): calib[n].mu.tolist() for n in CALIB_N}},
        **({"selfcal_r2": {"rho": g["rho6"], "n_features": 23, "calib_n": G6_N, "thr_after_calib": thr6,
                           "features": "g9 + fx_r2_hyb out[10..23]"}} if G6_N else {})}, indent=1))
    np.save(OUT / "pool_int16.npy", to_int16(W[pid]))
    pool.to_csv(OUT / "pool.csv", index_label="pool_idx")
    sdf.to_csv(OUT / "streams.csv", index=False)
    np.save(OUT / "gverify_int16.npy", W16)
    gv.to_csv(OUT / "gverify.csv", index=False, float_format="%.9g")
    near = {k: int((gv[f"{k}_margin_rel"] < 1e-5).sum())
            for k in ["sup", "healthy"] + [f"selfcal{n}" for n in CALIB_N] + (["selfcal_r2"] if G6_N else [])}
    summ = {
        "dataset": DS.NAME, "deployed_split": [P, K], "test_windows": int(len(te)),
        "host_rates_test_split": rates,
        "pool": {"healthy": int((pool.kind == "healthy").sum()), "fault_runs": int(pool.run[pool.kind == "fault"].nunique()),
                 "fault_windows": int((pool.kind == "fault").sum()), "flash_B": int(len(pool) * WIN * 2)},
        "streams": {s: {"len": len(v), "faults": int(sdf[sdf.stream == s].fault.sum()),
                        **{k: int(sdf[sdf.stream == s][k].sum()) for k in order[5:]}} for s, v in streams.items()},
        "gverify": {"vectors": int(len(vids)), "within_1e-5_of_threshold": near, "mahal_float32_flips": f32_flip},
    }
    (OUT / "summary.json").write_text(json.dumps(summ, indent=1))
    print(json.dumps(summ, indent=1))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
