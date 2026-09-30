#!/usr/bin/env python3
"""Download the 40 CWRU files, cut 1024-sample windows, write windows.npy + manifest.parquet.

Outputs (paths relative to the repo root):
    data/raw/<n>.mat                  CWRU files (downloaded if missing)
    data/processed/windows.npy        (N, 1024) float32, stride-512 windows, 12 kHz
    data/processed/manifest.parquet   one row per window (schema: CONTRACT.md section 3)
    results/data_counts.csv           per-file sample and window counts (goes in the paper)
    results/data_files.csv            per-file keys, lengths, RPM, sha256 (provenance)
    results/figs/class_examples.png   one window + spectrum per class (visual sanity check)

Usage:
    python ml/prepare_data.py                  # download if needed, build everything
    python ml/prepare_data.py --check          # verify existing outputs, build nothing
    python ml/prepare_data.py --source npz --npz-dir <srigas/cwru_bearing_numpy/Data>
                                               # fallback when the CWRU site is unreachable

Data traps handled here (see CONTRACT.md section 3):
    * Normal files 97-100 are 48 kHz -> decimated by 4 (FIR, zero phase) to 12 kHz.
    * Every file is read by its explicit key X{nnn}_DE_time. 99.mat also holds X098 keys (a copy of 98.mat) that
      must never be loaded.
    * Sampling-rate artefact: the 12 kHz fault recordings roll off steeply above ~5.5 kHz (the recorder's anti-alias
      filter), the decimated normal recordings do not. Without a fix, energy in 5.6-6 kHz alone separates normal
      from every fault window. So every recording, normal and fault, gets the same zero-phase FIR low-pass
      (--lpf-hz, default 5000 Hz, 255 taps). Measured before/after: normal and fault ranges in the top band go from
      disjoint to overlapping. Disable only for an ablation (--lpf-hz 0).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.io
import scipy.signal

REPO = Path(__file__).resolve().parents[1]
CLASSES_JSON = REPO / "handoff" / "classes.json"

WIN = 1024
STRIDE = 512
FS = 12_000
CWRU_URL = "https://engineering.case.edu/sites/default/files/{n}.mat"
# Accepted recording length at the original sampling rate, in seconds. A normal file wrongly assumed to be 48 kHz
# (if it were really 12 kHz) would come out at ~2.5 s and fail this check, so the rate assumption is verified.
DURATION_RANGE_S = (4.0, 12.0)
LPF_TAPS = 255
# If a file's internal X-number ever differs from its file number, map it here (file_id -> key id). Empty on purpose:
# the loader fails loudly instead of guessing.
KEY_OVERRIDE: dict[int, int] = {}

MANIFEST_DTYPES = {
    "window_id": "int32", "file_id": "int16", "class10": "int8", "class4": "int8", "fault_type": "object",
    "fault_size_in": "float32", "load_hp": "int8", "rpm": "float32", "start_idx": "int32", "fs_original": "int32",
}
CLASS4 = {"N": 0, "IR": 1, "B": 2, "OR": 3}


# ----------------------------------------------------------------------------------------------------------------
# File table
# ----------------------------------------------------------------------------------------------------------------
def file_table() -> pd.DataFrame:
    """One row per CWRU file, built from handoff/classes.json (the frozen class map)."""
    spec = json.loads(CLASSES_JSON.read_text())
    rpm_nominal = {d["load_hp"]: d["rpm_nominal"] for d in spec["loads"]}
    rows = []
    for c in spec["class10"]:
        assert len(c["cwru_files"]) == 4, c
        for load_hp, file_id in enumerate(c["cwru_files"]):
            rows.append(dict(
                file_id=file_id, class10=c["id"], name=c["name"], fault_type=c["fault_type"],
                class4=CLASS4[c["fault_type"]], fault_size_in=c["fault_size_in"], load_hp=load_hp,
                rpm_nominal=rpm_nominal[load_hp], fs_original=48_000 if c["fault_type"] == "N" else 12_000,
            ))
    df = pd.DataFrame(rows)
    assert len(df) == 40 and df.file_id.is_unique, "classes.json must list 40 distinct files"
    return df


# ----------------------------------------------------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------------------------------------------------
def download(file_ids, raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    missing = [n for n in file_ids if not (raw_dir / f"{n}.mat").exists()]
    for i, n in enumerate(missing, 1):
        dst = raw_dir / f"{n}.mat"
        url = CWRU_URL.format(n=n)
        print(f"[download {i}/{len(missing)}] {url}")
        tmp = dst.with_suffix(".part")
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                f.write(r.read())
        except Exception as e:  # noqa: BLE001 - report and stop; never continue with a partial dataset
            tmp.unlink(missing_ok=True)
            sys.exit(f"ERROR: could not download {url}: {e}\n"
                     f"Download the 40 files by hand into {raw_dir}/ (named <n>.mat) or use --source npz.")
        tmp.rename(dst)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_mat(path: Path, file_id: int) -> tuple[np.ndarray, float | None, list[str], str]:
    """Return (DE signal, rpm or None, all data keys, sha256). Reads only X{id}_DE_time."""
    m = scipy.io.loadmat(path)
    keys = sorted(k for k in m if not k.startswith("__"))
    kid = KEY_OVERRIDE.get(file_id, file_id)
    key = f"X{kid:03d}_DE_time"
    if key not in m:
        sys.exit(f"ERROR: {path.name} has no key {key}. Keys: {keys}. "
                 f"Inspect the file and add file_id -> key id to KEY_OVERRIDE; never pick 'the first DE key'.")
    x = np.asarray(m[key], dtype=np.float64).ravel()
    rpm_key = f"X{kid:03d}RPM"
    rpm = float(np.asarray(m[rpm_key]).ravel()[0]) if rpm_key in m else None
    return x, rpm, keys, sha256(path)


def npz_path(npz_dir: Path, row) -> Path:
    """Path inside srigas/cwru_bearing_numpy/Data for one file (fallback source)."""
    rpm = row.rpm_nominal
    if row.fault_type == "N":
        name = f"{rpm}_Normal.npz"
    else:
        size = int(round(row.fault_size_in * 1000))
        ft = "OR@6" if row.fault_type == "OR" else row.fault_type
        name = f"{rpm}_{ft}_{size}_DE12.npz"
    return npz_dir / f"{rpm} RPM" / name


def load_npz(path: Path) -> tuple[np.ndarray, float | None, list[str], str]:
    d = np.load(path)
    return np.asarray(d["DE"], dtype=np.float64).ravel(), None, sorted(d.files), sha256(path)


# ----------------------------------------------------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------------------------------------------------
def to_12k(x: np.ndarray, fs_original: int) -> np.ndarray:
    if fs_original == FS:
        return x
    assert fs_original == 4 * FS
    return scipy.signal.decimate(x, 4, ftype="fir", zero_phase=True)


def common_lowpass(x: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """Identical zero-phase FIR low-pass for every recording (removes the 48k-vs-12k band-edge artefact)."""
    if not cutoff_hz:
        return x
    taps = scipy.signal.firwin(LPF_TAPS, cutoff_hz, fs=FS)
    return scipy.signal.filtfilt(taps, [1.0], x)


def window_starts(n: int) -> np.ndarray:
    return np.arange(0, n - WIN + 1, STRIDE, dtype=np.int64)


def build(args) -> None:
    ft = file_table()
    if args.source == "mat":
        download(ft.file_id.tolist(), args.raw_dir)

    windows, manifest, counts, files = [], [], [], []
    wid = 0
    for row in ft.itertuples(index=False):
        if args.source == "mat":
            path = args.raw_dir / f"{row.file_id}.mat"
            x, rpm, keys, digest = load_mat(path, row.file_id)
        else:
            path = npz_path(args.npz_dir, row)
            x, rpm, keys, digest = load_npz(path)

        dur = len(x) / row.fs_original
        if not DURATION_RANGE_S[0] <= dur <= DURATION_RANGE_S[1]:
            sys.exit(f"ERROR: file {row.file_id}: {len(x)} samples at an assumed {row.fs_original} Hz = {dur:.2f} s, "
                     f"outside {DURATION_RANGE_S}. The sampling-rate assumption is wrong for this file.")
        if not np.all(np.isfinite(x)):
            sys.exit(f"ERROR: file {row.file_id} contains NaN/inf")

        x12 = common_lowpass(to_12k(x, row.fs_original), args.lpf_hz).astype(np.float32)
        starts = window_starts(len(x12))
        idx = starts[:, None] + np.arange(WIN)[None, :]
        windows.append(x12[idx])
        n = len(starts)
        manifest.append(pd.DataFrame({
            "window_id": np.arange(wid, wid + n), "file_id": row.file_id, "class10": row.class10,
            "class4": row.class4, "fault_type": row.fault_type, "fault_size_in": row.fault_size_in,
            "load_hp": row.load_hp, "rpm": rpm if rpm is not None else float(row.rpm_nominal),
            "start_idx": starts, "fs_original": row.fs_original,
        }))
        wid += n
        counts.append(dict(
            file_id=row.file_id, class10=row.class10, name=row.name, load_hp=row.load_hp,
            fs_original=row.fs_original, n_samples_original=len(x), n_samples_12k=len(x12),
            duration_s=round(len(x12) / FS, 3), n_windows_stride512=n,
            n_windows_nonoverlap=int(np.sum(starts % WIN == 0)),
        ))
        files.append(dict(
            file_id=row.file_id, source=args.source, path=str(path.relative_to(path.parents[1])),
            keys=";".join(keys), rpm_in_file=rpm, lpf_hz=args.lpf_hz, sha256=digest,
        ))
        print(f"  {row.file_id:>3} {row.name:<6} {row.load_hp} HP  fs={row.fs_original:>5}  "
              f"{len(x):>7} -> {len(x12):>6} samples  {dur:5.2f} s  windows={n}")

    W = np.concatenate(windows)
    M = pd.concat(manifest, ignore_index=True).astype(MANIFEST_DTYPES)
    C = pd.DataFrame(counts)
    assert len(W) == len(M) and (M.window_id.values == np.arange(len(M))).all()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "figs").mkdir(parents=True, exist_ok=True)
    np.save(args.out_dir / "windows.npy", W)
    M.to_parquet(args.out_dir / "manifest.parquet", index=False)
    C.to_csv(args.results_dir / "data_counts.csv", index=False)
    pd.DataFrame(files).to_csv(args.results_dir / "data_files.csv", index=False)
    if not args.no_plot:
        plot_examples(W, M, args.results_dir / "figs" / "class_examples.png")

    print(f"\nwindows.npy {W.shape} {W.dtype}  ({W.nbytes / 1e6:.1f} MB)")
    print(f"stride-512 windows: {C.n_windows_stride512.sum()}   non-overlapping: {C.n_windows_nonoverlap.sum()}   "
          f"signal: {C.duration_s.sum():.1f} s")
    print(C.groupby("name", sort=False)[["n_windows_nonoverlap", "n_windows_stride512"]].sum().to_string())
    check(args)


def plot_examples(W: np.ndarray, M: pd.DataFrame, out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [c["name"] for c in json.loads(CLASSES_JSON.read_text())["class10"]]
    fig, ax = plt.subplots(10, 2, figsize=(11, 16), constrained_layout=True)
    t = np.arange(WIN) / FS * 1e3
    f = np.fft.rfftfreq(WIN, 1 / FS)
    for c in range(10):
        # first non-overlapping window of the 0 HP file of this class
        i = int(M[(M.class10 == c) & (M.load_hp == 0) & (M.start_idx % WIN == 0)].window_id.iloc[0])
        x = W[i]
        ax[c, 0].plot(t, x, lw=0.6)
        ax[c, 0].set_ylabel(names[c])
        ax[c, 1].semilogy(f, np.abs(np.fft.rfft(x * np.hanning(WIN))) + 1e-9, lw=0.6)
    ax[-1, 0].set_xlabel("time (ms)")
    ax[-1, 1].set_xlabel("frequency (Hz)")
    fig.suptitle("One 1024-sample window per class (0 HP, 12 kHz, common low-pass applied)")
    fig.savefig(out, dpi=120)
    plt.close(fig)


# ----------------------------------------------------------------------------------------------------------------
# Check
# ----------------------------------------------------------------------------------------------------------------
def check(args) -> None:
    ft = file_table()
    W = np.load(args.out_dir / "windows.npy", mmap_mode="r")
    M = pd.read_parquet(args.out_dir / "manifest.parquet")
    C = pd.read_csv(args.results_dir / "data_counts.csv")

    problems = []
    if W.dtype != np.float32 or W.ndim != 2 or W.shape[1] != WIN:
        problems.append(f"windows.npy is {W.shape} {W.dtype}, expected (N, {WIN}) float32")
    if len(W) != len(M):
        problems.append(f"{len(W)} windows but {len(M)} manifest rows")
    if list(M.columns) != list(MANIFEST_DTYPES):
        problems.append(f"manifest columns {list(M.columns)} != contract {list(MANIFEST_DTYPES)}")
    if not (M.window_id.values == np.arange(len(M))).all():
        problems.append("window_id is not 0..N-1 in order")
    if set(M.file_id) != set(ft.file_id):
        problems.append("manifest files differ from classes.json")
    # every file keeps the label, load and sampling rate the contract assigns it
    merged = M.drop_duplicates("file_id").merge(ft, on="file_id", suffixes=("", "_spec"))
    for col in ["class10", "class4", "load_hp", "fs_original"]:
        bad = merged[merged[col] != merged[f"{col}_spec"]]
        if len(bad):
            problems.append(f"{col} mismatch for files {bad.file_id.tolist()}")
    got = M.groupby("file_id").agg(n=("window_id", "size"),
                                   n_no=("start_idx", lambda s: int((s % WIN == 0).sum())))
    exp = C.set_index("file_id")
    if not (got.n == exp.n_windows_stride512).all() or not (got.n_no == exp.n_windows_nonoverlap).all():
        problems.append("window counts differ from results/data_counts.csv")
    if (M.start_idx % STRIDE != 0).any():
        problems.append("a start_idx is not a multiple of the stride")
    sample = W[:: max(1, len(W) // 500)]
    if not np.isfinite(sample).all():
        problems.append("NaN/inf in windows.npy")

    if problems:
        sys.exit("CHECK FAILED:\n  - " + "\n  - ".join(problems))
    print(f"CHECK OK: {len(ft)} files, {len(M)} stride-512 windows, "
          f"{int((M.start_idx % WIN == 0).sum())} non-overlapping, 10 classes x 4 loads.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="verify existing outputs only")
    p.add_argument("--source", choices=["mat", "npz"], default="mat")
    p.add_argument("--raw-dir", type=Path, default=REPO / "data" / "raw")
    p.add_argument("--npz-dir", type=Path, help="srigas/cwru_bearing_numpy/Data (only with --source npz)")
    p.add_argument("--out-dir", type=Path, default=REPO / "data" / "processed")
    p.add_argument("--results-dir", type=Path, default=REPO / "results")
    p.add_argument("--lpf-hz", type=float, default=5000.0,
                   help="common low-pass applied to every recording (0 = off; see module docstring)")
    p.add_argument("--no-plot", action="store_true")
    args = p.parse_args()
    if args.source == "npz" and not args.npz_dir:
        p.error("--source npz needs --npz-dir")
    check(args) if args.check else build(args)


if __name__ == "__main__":
    main()
