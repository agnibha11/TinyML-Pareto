#!/usr/bin/env python3
"""On-board VERIFY of the flashed bundle against the host's expected outputs (CONTRACT.md 7.2, "VERIFY outputs").

For every model in the bundle: SEL k, then VERIFY each test window of <handoff>/test_vectors/raw.npy, and compare the
board's output vector with <handoff>/test_vectors/outputs/<model_id>.npy:
    classes must agree on >= 99 % (int8) / 99.5 % (fp32) of the windows,
    outputs within one output quantization step (int8, registry output_scale) or 1e-4 absolute (fp32, trees).
One row per model is appended to --out; a failing model is reported, never fatal (the energy campaign goes on).
--dump DIR also saves every board output vector and class, DIR/<model_id>.npz (out, cls, fw_hash), for an
element-by-element comparison with an emulator.

    python tools/verify_board.py --port /dev/ttyACM0 --handoff handoff/pu --registry models/pu/registry.csv \\
                                 --out results/pu/hardware/board_verify.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.dut import DUT, DUTError  # noqa: E402

COLUMNS = ["model_id", "fw_hash", "n_vectors", "class_agree", "max_abs_err", "tol", "within_tol", "passed",
           "seconds", "timestamp", "error"]
FP32_TOL = 1e-4


def tolerance(reg_row: pd.Series) -> tuple[float, float]:
    """(output tolerance, required class agreement) for one registry row."""
    if str(reg_row.get("precision", "")).startswith("int8") and str(reg_row.get("output_scale", "")) not in ("", "nan"):
        return float(reg_row["output_scale"]) * 1.0001, 0.99
    return FP32_TOL, 0.995


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True)
    ap.add_argument("--handoff", type=Path, default=REPO / "handoff")
    ap.add_argument("--registry", type=Path, default=REPO / "models" / "registry.csv")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dump", type=Path, metavar="DIR", help="save the board's outputs per model as DIR/<model_id>.npz")
    a = ap.parse_args()

    tv = a.handoff / "test_vectors"
    raw = np.load(tv / "raw.npy").astype(np.float32)
    reg = pd.read_csv(a.registry, keep_default_na=False).set_index("model_id")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    new = not a.out.exists() or a.out.stat().st_size == 0
    n_fail = 0
    with DUT(a.port) as d, open(a.out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        fw = d.ping()["bench"]
        for e in d.list():
            t0 = time.monotonic()
            row = {"model_id": e.model_id, "fw_hash": fw, "n_vectors": len(raw), "error": ""}
            try:
                exp = np.load(tv / "outputs" / f"{e.model_id}.npy").astype(np.float32)
                tol, need = tolerance(reg.loc[e.model_id])
                d.sel(e.k)
                outs, clss, agree = [], [], 0
                for i, x in enumerate(raw):
                    cls, out = d.verify(x)
                    outs.append(out[:exp.shape[1]])
                    clss.append(cls)
                    agree += int(cls == int(np.argmax(exp[i])))
                if a.dump:
                    a.dump.mkdir(parents=True, exist_ok=True)
                    np.savez(a.dump / f"{e.model_id}.npz", out=np.stack(outs).astype(np.float32),
                             cls=np.array(clss, dtype=np.int16), fw_hash=fw)
                err = float(np.max(np.abs(np.stack(outs) - exp)))
                ca = agree / len(raw)
                row.update(class_agree=f"{ca:.4f}", max_abs_err=f"{err:.6g}", tol=f"{tol:.6g}",
                           within_tol=int(err <= tol), passed=int(ca >= need and err <= tol))
            except (DUTError, KeyError, ValueError, OSError) as ex:
                row.update(passed=0, error=str(ex)[:200])
            row.update(seconds=f"{time.monotonic() - t0:.1f}",
                       timestamp=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
            w.writerow(row)
            f.flush()
            n_fail += 1 - int(row["passed"])
            print(f"  [{e.k}] {e.model_id}: {'PASS' if row['passed'] else 'FAIL'} class_agree={row.get('class_agree')}"
                  f" max_abs_err={row.get('max_abs_err')} tol={row.get('tol')} {row['error']}", flush=True)
    print(f"VERIFY {fw}: {n_fail} failing model(s)")
    return 0 if n_fail == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
