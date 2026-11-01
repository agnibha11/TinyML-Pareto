#!/usr/bin/env python3
"""Paderborn University (KAt) bearing dataset: download, extract, resample to 12 kHz, windows, features, splits.

The second dataset of the study (Lessmeier et al., PHM Europe 2016; https://mb.uni-paderborn.de/kat/datacenter).
32 ball bearings of type 6203, each a separate physical bearing: 6 healthy, 12 with artificial damage (EDM,
engraver, drilling) and 14 with real damage grown in accelerated lifetime tests. Each bearing was recorded at four
operating conditions (speed, load torque, radial force), 20 recordings of 4 s each, vibration at 64 kHz.

Stages (each one resumable; `python ml/pu_data.py all` runs them in order):
    download   data/raw/pu/<code>.rar                       32 archives, ~5.4 GB, HTTP range resume, size-checked
    prepare    data/processed/pu/bearings/<code>.npz          per bearing: the chosen recordings, 64 -> 12 kHz,
                                                              the CWRU pipeline's 5 kHz low-pass, 1024-sample
                                                              non-overlapping windows
               data/processed/pu/windows.npy + manifest.parquet, results/pu/data_counts.csv
    features   data/processed/pu/feat_r{1,2,3,4}.npy         ml/features.py, unchanged
    splits     splits/pu/<P>/<k>/{train,val,test}.npy, splits/pu/protocols.json, splits/pu/README.md,
               results/pu/split_counts.csv

Labels (class3): 0 healthy, 1 inner race (KI), 2 outer race (KA). The three bearings with damage on both rings
(KB23, KB24, KB27) are prepared and kept in the manifest (class3 = -1) for the detect-then-diagnose gate, but are
left out of the classification splits.

Protocols (all with a stratified 15 % validation carve-out of the training windows, as for CWRU):
    P1  leaky                   1 fold   random stratified 70/15/15 of all windows (same recordings on both sides)
    P2  leave one condition out 4 folds  test = one operating condition, train = the other three (same bearings)
    P3  bearing-disjoint        4 folds  each fold holds out about a quarter of the healthy, IR and OR bearings
                                         (assigned round-robin in code order, so artificial and real damage mix)
    P4  artificial -> real      1 fold   train = artificial damage + healthy K001-K003; test = real damage +
                                         healthy K004-K006 (the dataset authors' own benchmark question)
Test sets keep at most --test-per-bc windows per (bearing, condition), evenly spaced, so a test set is about the size
of a CWRU one (~1,300 windows) and scoring every model on clean + 4 noisy copies stays affordable.

Usage:
    python ml/pu_data.py all                      # everything
    python ml/pu_data.py download                 # just the archives
    python ml/pu_data.py prepare --recordings 5   # 5 of the 20 recordings per bearing and condition (default)
    python ml/pu_data.py splits --check
"""
from __future__ import annotations

import os

os.environ["TINYML_DATASET"] = "pu"            # before dataset/features are imported: this module is Paderborn only

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as DS  # noqa: E402

REPO = DS.REPO
RAW = REPO / "data" / "raw" / "pu"
PROCESSED = DS.PROCESSED
BEARING_CACHE = PROCESSED / "bearings"
SPLITS = DS.SPLITS
RESULTS = DS.RESULTS

URL = "https://groups.uni-paderborn.de/kat/BearingDataCenter/{code}.rar"
WIN = 1024
FS = 12_000
FS_ORIG = 64_000
UP, DOWN = 3, 16                               # 64 kHz * 3 / 16 = 12 kHz
LPF_HZ = 5000.0                                # same common low-pass as the CWRU windows (prepare_data.py)
SAMPLES_RANGE = (250_000, 270_000)             # a 4 s recording at 64 kHz has ~256,000 samples
VAL_FRAC = 0.15
SPLIT_SEED = 20261101
N_REC_TOTAL = 20

# Operating conditions: code -> (rpm, load torque N m, radial force N)
CONDITIONS = {
    "N15_M07_F10": (1500, 0.7, 1000),
    "N09_M07_F10": (900, 0.7, 1000),
    "N15_M01_F10": (1500, 0.1, 1000),
    "N15_M07_F04": (1500, 0.7, 400),
}

# code -> (fault type, origin, archive size in bytes as served by the KAt server, checked 2026-11-01)
BEARINGS = {
    "K001": ("N", "healthy", 173881721), "K002": ("N", "healthy", 161981588),
    "K003": ("N", "healthy", 173194389), "K004": ("N", "healthy", 163337851),
    "K005": ("N", "healthy", 167366765), "K006": ("N", "healthy", 175997605),
    "KA01": ("OR", "artificial", 166571438), "KA03": ("OR", "artificial", 176196084),
    "KA05": ("OR", "artificial", 163644977), "KA06": ("OR", "artificial", 164139086),
    "KA07": ("OR", "artificial", 160346192), "KA08": ("OR", "artificial", 169188540),
    "KA09": ("OR", "artificial", 171467156),
    "KI01": ("IR", "artificial", 175265779), "KI03": ("IR", "artificial", 159217524),
    "KI05": ("IR", "artificial", 160359281), "KI07": ("IR", "artificial", 160646876),
    "KI08": ("IR", "artificial", 163760131),
    "KA04": ("OR", "real", 180920703), "KA15": ("OR", "real", 164268273), "KA16": ("OR", "real", 167165704),
    "KA22": ("OR", "real", 159054470), "KA30": ("OR", "real", 163625195),
    "KI04": ("IR", "real", 172644279), "KI14": ("IR", "real", 163581312), "KI16": ("IR", "real", 166348305),
    "KI17": ("IR", "real", 167363476), "KI18": ("IR", "real", 161226797), "KI21": ("IR", "real", 164882839),
    "KB23": ("OR+IR", "real", 170933916), "KB24": ("OR+IR", "real", 186677308),
    "KB27": ("OR+IR", "real", 162452979),
}
CLASS3 = {"N": 0, "IR": 1, "OR": 2, "OR+IR": -1}
CLASS3_NAMES = ["N", "IR", "OR"]
SMOKE = ["K001", "K004", "KA01", "KA04", "KI01", "KI04"]   # 6 bearings: enough for every protocol (2 P3 folds)

MANIFEST_DTYPES = {
    "window_id": "int32", "bearing": "object", "class3": "int8", "fault_type": "object", "origin": "object",
    "condition": "object", "rpm": "int16", "torque_nm": "float32", "radial_n": "int16", "recording": "int8",
    "start_idx": "int32", "fs_original": "int32",
}

Progress = Callable[[str, int, int], None]     # (item, done, total)


def recordings(n: int) -> list[int]:
    """n of the 20 recordings per (bearing, condition), spread evenly over the session (1 and 20 included)."""
    return sorted({int(round(v)) for v in np.linspace(1, N_REC_TOTAL, n)})


def codes(subset: str | None = None) -> list[str]:
    if subset == "smoke":
        return list(SMOKE)
    if subset:
        out = [c.strip().upper() for c in subset.split(",") if c.strip()]
        bad = [c for c in out if c not in BEARINGS]
        if bad:
            raise SystemExit(f"unknown bearing codes {bad}; known: {', '.join(BEARINGS)}")
        return out
    return list(BEARINGS)


# ----------------------------------------------------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------------------------------------------------
def archive(code: str) -> Path:
    return RAW / f"{code}.rar"


def downloaded(code: str) -> bool:
    p = archive(code)
    return p.exists() and p.stat().st_size == BEARINGS[code][2]


def download_one(code: str, progress: Progress | None = None, retries: int = 5) -> Path:
    """Download one archive with HTTP range resume; the finished file must have the server's exact size."""
    RAW.mkdir(parents=True, exist_ok=True)
    dst, part = archive(code), archive(code).with_suffix(".rar.part")
    total = BEARINGS[code][2]
    if downloaded(code):
        if progress:
            progress(code, total, total)
        return dst
    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        if have > total:
            part.unlink()
            have = 0
        req = urllib.request.Request(URL.format(code=code), headers={"User-Agent": "TinyML-Pareto/pu_data"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:          # server ignored the range: start again
                    have = 0
                with open(part, "ab" if have else "wb") as f:
                    done = have
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(code, done, total)
            if part.stat().st_size == total:
                part.replace(dst)
                return dst
        except Exception as e:  # noqa: BLE001 - network hiccup: keep the partial file and retry from where it stopped
            if attempt == retries:
                raise RuntimeError(f"{code}: download failed after {retries} attempts: {e}") from e
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"{code}: size {part.stat().st_size} != expected {total}")


# ----------------------------------------------------------------------------------------------------------------
# Extract and prepare
# ----------------------------------------------------------------------------------------------------------------
EXTRACTORS = ("bsdtar", "unrar", "7z", "7za", "unar")
INSTALL_HINT = ("No RAR extractor found. Install one of them, e.g.\n"
                "    sudo apt install libarchive-tools      # provides bsdtar (recommended)\n"
                "    sudo apt install unrar                  # or this\n"
                "    sudo apt install p7zip-full             # or this (7z)")


def extractor() -> str:
    for tool in EXTRACTORS:
        if shutil.which(tool):
            return tool
    raise SystemExit(INSTALL_HINT)


def extract(rar: Path, members: list[str], dest: Path) -> None:
    tool = extractor()
    cmd = {
        "bsdtar": ["bsdtar", "-xf", str(rar), "-C", str(dest), *members],
        "unrar": ["unrar", "x", "-o+", "-inul", str(rar), *members, str(dest) + os.sep],
        "7z": ["7z", "x", "-y", "-bd", f"-o{dest}", str(rar), *members],
        "7za": ["7za", "x", "-y", "-bd", f"-o{dest}", str(rar), *members],
        "unar": ["unar", "-q", "-f", "-o", str(dest), str(rar), *members],
    }[tool]
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def load_vibration(path: Path) -> np.ndarray:
    """vibration_1 (64 kHz) from one Paderborn .mat file. The struct is the file's only variable; its signals sit in
    field Y as entries with a Name; never pick a channel by position."""
    import scipy.io
    m = scipy.io.loadmat(path)
    keys = [k for k in m if not k.startswith("__")]
    if len(keys) != 1:
        raise ValueError(f"{path.name}: expected one variable, found {keys}")
    Y = m[keys[0]]["Y"][0, 0][0]
    for e in Y:
        if e["Name"].size and str(e["Name"][0]) == "vibration_1":
            return np.asarray(e["Data"], dtype=np.float64).ravel()
    raise ValueError(f"{path.name}: no vibration_1 signal")


def to_windows(x64: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """64 kHz recording -> 12 kHz (polyphase, anti-aliased) -> CWRU's common 5 kHz low-pass -> windows."""
    import scipy.signal
    from prepare_data import common_lowpass
    x12 = scipy.signal.resample_poly(x64, UP, DOWN)
    x12 = common_lowpass(x12, LPF_HZ).astype(np.float32)
    starts = np.arange(0, len(x12) - WIN + 1, WIN, dtype=np.int64)
    return x12[starts[:, None] + np.arange(WIN)[None, :]], starts


def prepare_bearing(code: str, recs: list[int], progress: Progress | None = None) -> Path:
    """Extract the chosen recordings of one bearing and cache its windows; skips work already cached."""
    out = BEARING_CACHE / f"{code}.npz"
    if out.exists():
        with np.load(out, allow_pickle=False) as z:
            if list(z["recs_used"]) == recs:
                if progress:
                    progress(code, 1, 1)
                return out
    if not downloaded(code):
        raise RuntimeError(f"{code}: archive missing or incomplete; run the download stage first")
    BEARING_CACHE.mkdir(parents=True, exist_ok=True)
    ft, origin, _ = BEARINGS[code]
    members = [f"{code}/{cond}_{code}_{r}.mat" for cond in CONDITIONS for r in recs]
    W, rows, skipped = [], [], []
    with tempfile.TemporaryDirectory(prefix=f"pu_{code}_", dir=RAW) as tmp:
        extract(archive(code), members, Path(tmp))
        for i, mem in enumerate(members):
            path = Path(tmp) / mem
            cond, r = mem.split("/")[1][:11], int(mem.rsplit("_", 1)[1].split(".")[0])
            if not path.exists():
                skipped.append(f"{mem}: not in archive")
                continue
            try:
                x = load_vibration(path)
            except Exception as e:  # noqa: BLE001
                skipped.append(f"{mem}: {e}")
                continue
            if not SAMPLES_RANGE[0] <= len(x) <= SAMPLES_RANGE[1] or not np.isfinite(x).all():
                skipped.append(f"{mem}: {len(x)} samples / non-finite")
                continue
            w, starts = to_windows(x)
            rpm, tq, fr = CONDITIONS[cond]
            W.append(w)
            rows.append(pd.DataFrame({"bearing": code, "class3": CLASS3[ft], "fault_type": ft, "origin": origin,
                                      "condition": cond, "rpm": rpm, "torque_nm": tq, "radial_n": fr,
                                      "recording": r, "start_idx": starts, "fs_original": FS_ORIG}))
            if progress:
                progress(code, i + 1, len(members))
    if not W:
        raise RuntimeError(f"{code}: no usable recording ({skipped[:3]})")
    M = pd.concat(rows, ignore_index=True)
    cols = {c: (M[c].to_numpy().astype(str) if M[c].dtype == object else M[c].to_numpy()) for c in M.columns}
    np.savez(out, windows=np.concatenate(W), recs_used=np.asarray(recs),
             skipped=np.asarray(skipped, dtype=str), **cols)
    return out


def assemble(bearing_codes: list[str], recs: list[int]) -> tuple[np.ndarray, pd.DataFrame]:
    """Concatenate the per-bearing caches in a fixed order into windows.npy + manifest.parquet."""
    W, M, counts, notes = [], [], [], []
    for code in bearing_codes:
        with np.load(BEARING_CACHE / f"{code}.npz", allow_pickle=False) as z:
            if list(z["recs_used"]) != recs:
                raise RuntimeError(f"{code}: cache built with recordings {list(z['recs_used'])}, expected {recs}")
            W.append(z["windows"])
            cols = [c for c in MANIFEST_DTYPES if c != "window_id"]
            M.append(pd.DataFrame({c: z[c] for c in cols}))
            notes += [f"{code}: {s}" for s in z["skipped"]]
        d = M[-1]
        counts.append({"bearing": code, "fault_type": BEARINGS[code][0], "origin": BEARINGS[code][1],
                       "n_windows": len(d), **{c: int((d.condition == c).sum()) for c in CONDITIONS}})
    Wa = np.concatenate(W)
    Ma = pd.concat(M, ignore_index=True)
    Ma.insert(0, "window_id", np.arange(len(Ma)))
    Ma = Ma[list(MANIFEST_DTYPES)].astype(MANIFEST_DTYPES)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    np.save(PROCESSED / "windows.npy", Wa)
    Ma.to_parquet(PROCESSED / "manifest.parquet", index=False)
    pd.DataFrame(counts).to_csv(RESULTS / "data_counts.csv", index=False)
    meta = {"bearings": bearing_codes, "recordings": recs, "n_windows": int(len(Ma)), "fs": FS, "win": WIN,
            "lpf_hz": LPF_HZ, "skipped_recordings": notes}
    (PROCESSED / "prepare.json").write_text(json.dumps(meta, indent=1))
    for f in PROCESSED.glob("feat_*.npy"):     # features of an older window set are stale
        f.unlink()
    return Wa, Ma


# ----------------------------------------------------------------------------------------------------------------
# Features
# ----------------------------------------------------------------------------------------------------------------
def build_features(progress: Progress | None = None) -> None:
    import features as FE
    for i, rep in enumerate(("r1", "r2", "r3", "r4"), 1):
        F = FE.cached(rep)
        if not np.isfinite(F).all():
            raise RuntimeError(f"non-finite values in PU {rep} features")
        if progress:
            progress("features", i, 4)


# ----------------------------------------------------------------------------------------------------------------
# Splits
# ----------------------------------------------------------------------------------------------------------------
def _cap_test(m: pd.DataFrame, ids: np.ndarray, per_bc: int) -> np.ndarray:
    """At most per_bc windows per (bearing, condition), evenly spaced through that group's windows."""
    d = m.loc[ids, ["window_id", "bearing", "condition"]]
    keep = []
    for _, g in d.groupby(["bearing", "condition"], sort=False):
        w = g.window_id.to_numpy()
        if len(w) > per_bc:
            w = w[np.unique(np.round(np.linspace(0, len(w) - 1, per_bc)).astype(int))]
        keep.append(w)
    return np.sort(np.concatenate(keep)) if keep else np.zeros(0, np.int64)


def _carve_val(m: pd.DataFrame, train_ids: np.ndarray, rs: int) -> tuple[np.ndarray, np.ndarray]:
    from sklearn.model_selection import train_test_split
    tr, va = train_test_split(train_ids, test_size=VAL_FRAC, stratify=m.loc[train_ids, "class3"].to_numpy(),
                              random_state=rs)
    return np.sort(tr), np.sort(va)


def p3_groups(m: pd.DataFrame) -> list[list[str]]:
    """Round-robin bearings of each class (in code order) into F folds, F = min(4, smallest class count)."""
    by = {c: sorted(m[m.class3 == c].bearing.unique()) for c in range(3)}
    f = min(4, *(len(v) for v in by.values()))
    groups: list[list[str]] = [[] for _ in range(f)]
    for c in range(3):
        for i, b in enumerate(by[c]):
            groups[i % f].append(b)
    return groups


def make_splits(m: pd.DataFrame, per_bc: int) -> tuple[dict, dict]:
    from sklearn.model_selection import train_test_split
    m = m.set_index("window_id", drop=False)
    cls = m[m.class3 >= 0]
    ids = cls.window_id.to_numpy()
    out, desc = {}, {}

    trv, te = train_test_split(ids, test_size=0.15, stratify=cls.class3, random_state=SPLIT_SEED)
    tr, va = train_test_split(trv, test_size=0.15 / 0.85, stratify=m.loc[trv, "class3"], random_state=SPLIT_SEED)
    out[("P1", 0)] = {"train": np.sort(tr), "val": np.sort(va), "test": _cap_test(m, np.sort(te), per_bc)}
    desc["P1"] = ["random stratified 70/15/15 of all windows"]

    desc["P2"] = []
    for k, cond in enumerate(CONDITIONS):
        test = cls[cls.condition == cond].window_id.to_numpy()
        tr, va = _carve_val(m, cls[cls.condition != cond].window_id.to_numpy(), SPLIT_SEED + 100 + k)
        out[("P2", k)] = {"train": tr, "val": va, "test": _cap_test(m, test, per_bc)}
        desc["P2"].append(f"test condition {cond}")

    groups = p3_groups(cls)
    desc["P3"] = []
    for k, g in enumerate(groups):
        held = cls.bearing.isin(g)
        tr, va = _carve_val(m, cls[~held].window_id.to_numpy(), SPLIT_SEED + 200 + k)
        out[("P3", k)] = {"train": tr, "val": va, "test": _cap_test(m, cls[held].window_id.to_numpy(), per_bc)}
        desc["P3"].append("test bearings " + " ".join(g))

    healthy = sorted(cls[cls.class3 == 0].bearing.unique())
    h_tr, h_te = healthy[: (len(healthy) + 1) // 2], healthy[(len(healthy) + 1) // 2:]
    art = cls.origin == "artificial"
    real = cls.origin == "real"
    tr_mask = art | cls.bearing.isin(h_tr)
    te_mask = real | cls.bearing.isin(h_te)
    tr, va = _carve_val(m, cls[tr_mask].window_id.to_numpy(), SPLIT_SEED + 300)
    out[("P4", 0)] = {"train": tr, "val": va, "test": _cap_test(m, cls[te_mask].window_id.to_numpy(), per_bc)}
    desc["P4"] = [f"train artificial damage + healthy {' '.join(h_tr)}; test real damage + healthy {' '.join(h_te)}"]
    return out, desc


def check_splits(m: pd.DataFrame, splits: dict) -> pd.DataFrame:
    mi = m.set_index("window_id")
    problems, rows = [], []
    for (p, k), s in splits.items():
        a, b, c = (set(s[x].tolist()) for x in ("train", "val", "test"))
        if a & b or a & c or b & c:
            problems.append(f"{p}/{k}: a window is in two sets")
        allids = np.concatenate([s["train"], s["val"], s["test"]])
        if (mi.loc[allids, "class3"] < 0).any():
            problems.append(f"{p}/{k}: an OR+IR (KB) window is in a classification split")
        trb = set(mi.loc[np.concatenate([s["train"], s["val"]]), "bearing"])
        teb = set(mi.loc[s["test"], "bearing"])
        if p in ("P3", "P4") and trb & teb:
            problems.append(f"{p}/{k}: bearings {sorted(trb & teb)} in both train and test")
        if p == "P2":
            trc = set(mi.loc[np.concatenate([s["train"], s["val"]]), "condition"])
            if trc & set(mi.loc[s["test"], "condition"]):
                problems.append(f"P2/{k}: an operating condition is in both train and test")
        for name in ("train", "val", "test"):
            cnt = mi.loc[s[name], "class3"].value_counts().reindex(range(3), fill_value=0)
            if name != "val" and (cnt == 0).any():
                problems.append(f"{p}/{k}/{name}: classes {cnt[cnt == 0].index.tolist()} missing")
            for cl, n in cnt.items():
                rows.append({"protocol": p, "fold": k, "set": name, "cls": CLASS3_NAMES[cl], "n": int(n)})
    if problems:
        raise SystemExit("PU SPLIT CHECK FAILED:\n  - " + "\n  - ".join(problems))
    return pd.DataFrame(rows)


def write_splits(per_bc: int) -> dict:
    m = pd.read_parquet(PROCESSED / "manifest.parquet")
    splits, desc = make_splits(m, per_bc)
    if SPLITS.exists():
        for p in SPLITS.glob("P*"):
            shutil.rmtree(p)
    for (p, k), s in splits.items():
        d = SPLITS / p / str(k)
        d.mkdir(parents=True, exist_ok=True)
        for name, a in s.items():
            np.save(d / f"{name}.npy", a.astype(np.int32))
    df = check_splits(m, splits)
    folds = {p: sum(1 for q, _ in splits if q == p) for p in ("P1", "P2", "P3", "P4")}
    (SPLITS / "protocols.json").write_text(json.dumps(
        {"folds": folds, "label": "class3", "classes": CLASS3_NAMES, "test_per_bc": per_bc, "folds_desc": desc},
        indent=1))
    df.to_csv(RESULTS / "split_counts.csv", index=False)
    lines = ["# splits/pu/ — generated by `ml/pu_data.py splits` (never edit by hand)", "",
             "Paderborn, 3 classes (0 N, 1 IR, 2 OR); window ids index data/processed/pu/windows.npy.", "",
             "| split | definition | train | val | test |", "|---|---|---|---|---|"]
    for (p, k), s in splits.items():
        lines.append(f"| {p}/{k} | {desc[p][k]} | {len(s['train'])} | {len(s['val'])} | {len(s['test'])} |")
    lines += ["", "Checks: no window in two sets; no OR+IR (KB) window in any split; P3/P4 train and test share no "
              "bearing; P2 train and test share no operating condition; every class in every train and test set."]
    (SPLITS / "README.md").write_text("\n".join(lines) + "\n")
    return folds


def check_only() -> None:
    m = pd.read_parquet(PROCESSED / "manifest.parquet")
    pj = json.loads((SPLITS / "protocols.json").read_text())
    splits = {(p, k): {s: np.load(SPLITS / p / str(k) / f"{s}.npy") for s in ("train", "val", "test")}
              for p, n in pj["folds"].items() for k in range(n)}
    check_splits(m, splits)
    print(f"PU SPLIT CHECK OK: {len(splits)} splits {pj['folds']}")


# ----------------------------------------------------------------------------------------------------------------
def _print_progress(item: str, done: int, total: int) -> None:
    print(f"\r  {item}: {done / max(total, 1):6.1%}", end="", flush=True)
    if done >= total:
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["all", "download", "prepare", "features", "splits"])
    ap.add_argument("--bearings", help="'smoke' or a comma list of codes (default: all 32)")
    ap.add_argument("--recordings", type=int, default=5, help="recordings per bearing and condition (1-20)")
    ap.add_argument("--test-per-bc", type=int, default=46, help="test windows per (bearing, condition)")
    ap.add_argument("--check", action="store_true", help="with 'splits': only re-check the existing files")
    a = ap.parse_args()
    bc, recs = codes(a.bearings), recordings(a.recordings)
    if a.stage == "splits" and a.check:
        check_only()
        return
    if a.stage in ("all", "download"):
        for c in bc:
            download_one(c, _print_progress)
    if a.stage in ("all", "prepare"):
        extractor()
        for c in bc:
            prepare_bearing(c, recs, _print_progress)
        W, M = assemble(bc, recs)
        print(f"windows.npy {W.shape}; {M.bearing.nunique()} bearings; recordings {recs}")
    if a.stage in ("all", "features"):
        build_features(_print_progress)
    if a.stage in ("all", "splits"):
        print(f"splits: {write_splits(a.test_per_bc)}")


if __name__ == "__main__":
    main()
