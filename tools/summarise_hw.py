#!/usr/bin/env python3
"""Quick look at results/cwru/hardware/hw_measurements.csv (or any hw_measurements.csv given as argument): per model and mode, dynamic energy per operation.

E_op = (P_mean - P_idle) * t_window / N   (energy above the idle baseline, per inference / front end / end-to-end)
Also prints the spread over the 5 stored inputs and the rejected-window count. Writes <csv dir>/hw_summary.csv.
A check for a running campaign, not the paper analysis (that uses the last accepted row per window, CONTRACT.md 1).

    python tools/summarise_hw.py [csv]
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "results" / "cwru" / "hardware" / "hw_measurements.csv"
    d = pd.read_csv(p)
    rej = int(d.rejected.sum())
    d = d[d.rejected == 0].copy()
    idle = d[d["mode"] == "IDLE"]
    print(f"{p.name}: {len(d)} accepted windows, {rej} rejected")
    print(f"IDLE: P {idle.P_mean_uW.mean() / 1e3:.3f} mW (CV {idle.P_mean_uW.std() / idle.P_mean_uW.mean():.2%},"
          f" n={len(idle)}), Vbus {d.V_bus_mV.min():.0f}-{d.V_bus_mV.max():.0f} mV")
    w = d[d["mode"].isin(["RUN", "FRONT", "E2E"])].copy()
    w["t_s"] = w.t_window_us_logger / 1e6
    w["P_dyn_uW"] = w.P_mean_uW - w.P_idle_uW
    w["E_op_uJ"] = w.P_dyn_uW * w.t_s / w.N
    w["t_op_us"] = w.t_window_us_logger / w.N
    w["dt_mismatch"] = (w.t_window_us_logger - w.t_window_us_dut).abs() / w.t_window_us_dut
    g = w.groupby(["model_id", "mode"], sort=False).agg(
        n=("E_op_uJ", "size"), t_op_us=("t_op_us", "mean"), P_mW=("P_mean_uW", lambda s: s.mean() / 1e3),
        P_dyn_mW=("P_dyn_uW", lambda s: s.mean() / 1e3), E_op_uJ=("E_op_uJ", "mean"),
        E_cv=("E_op_uJ", lambda s: s.std() / s.mean() if s.mean() else float("nan")),
        max_dt_mismatch=("dt_mismatch", "max"))
    with pd.option_context("display.width", 160, "display.float_format", lambda v: f"{v:.4g}"):
        print(g.to_string())
    out = p.with_name("hw_summary.csv")
    g.to_csv(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
