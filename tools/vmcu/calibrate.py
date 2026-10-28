#!/usr/bin/env python3
"""Calibrate the virtual-MCU cycle model against the board (run once results/fw_metrics.csv exists).

The board's LAT command reports DWT cycle medians (CONTRACT.md 7.2; fw_metrics.csv stores them in us at 64 MHz).
This script joins them with results/vmcu/vmcu_metrics.csv and reports, per runtime and phase:
  ratio = board cycles / estimated cycles, a least-squares fit board = a * estimate (through the origin),
  R^2 on log scale, and the mean absolute percentage error of the calibrated estimate.
Output: results/vmcu/calibration.csv (+ printed table). Without fw_metrics.csv it explains what is missing and exits 0.

Usage: python tools/vmcu/calibrate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
F_CPU = 64e6


def fit(est: np.ndarray, board: np.ndarray) -> dict:
    a = float((est * board).sum() / (est * est).sum())
    pred = a * est
    le, lb = np.log10(est), np.log10(board)
    r2 = 1 - ((lb - np.log10(pred)) ** 2).sum() / ((lb - lb.mean()) ** 2).sum() if len(lb) > 1 else float("nan")
    return {"n": len(est), "a": round(a, 4), "ratio_median": round(float(np.median(board / est)), 4),
            "ratio_min": round(float((board / est).min()), 4), "ratio_max": round(float((board / est).max()), 4),
            "r2_log": round(float(r2), 4), "mape_calibrated": round(float(np.mean(np.abs(pred - board) / board)), 4)}


def main() -> None:
    fw_p = REPO / "results" / "fw_metrics.csv"
    if not fw_p.exists():
        print("results/fw_metrics.csv not found: run the board's LAT campaign first (Abhinav, gate M4). Nothing to do.")
        return
    fw = pd.read_csv(fw_p)
    vm = pd.read_csv(REPO / "results" / "vmcu" / "vmcu_metrics.csv")
    d = fw.merge(vm, on=["model_id", "runtime"], suffixes=("_board", "_vm"))
    if d.empty:
        sys.exit("no model_id/runtime in common between fw_metrics.csv and vmcu_metrics.csv")
    rows = []
    for rt, g in d.groupby("runtime"):
        for phase, b_col, e_col in (("inference", "lat_inf_med_us", "inf_cyc_med"),
                                    ("front_end", "lat_fe_med_us", "fe_cyc_med")):
            ok = g[(g[b_col] > 0) & (g[e_col] > 0)]
            if len(ok):
                rows.append({"runtime": rt, "phase": phase, **fit(ok[e_col].to_numpy(float),
                                                                    ok[b_col].to_numpy(float) * F_CPU / 1e6)})
    out = pd.DataFrame(rows)
    out.to_csv(REPO / "results" / "vmcu" / "calibration.csv", index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
