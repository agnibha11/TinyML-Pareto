#!/usr/bin/env python3
"""Virtual measurement campaign: what will the INA219 + ESP32 rig be able to measure, and how long will it take?

Before the hardware exists, this script replays the measurement campaign of CONTRACT.md 7 on a simulated board:
    * every model's window schedule (IDLE, RUN, FRONT, E2E; N chosen so each window lasts >= 10 s) is built from its
      latency on the virtual Cortex-M4 (results/prehw_costs.csv);
    * the board current during each window is synthesised from a stated power model (below) with noise and drift;
    * tools/vrig/vrig_sim.cpp quantises it exactly as the INA219 registers do and integrates it with the real
      ESP32 logger core (logger/logger_core.cpp); the W lines are parsed with tools/measure.py's parser;
    * energy per inference is recovered as in the analysis plan (dE = (Pmean_RUN - Pmean_IDLE) * T_RUN / N) and
      compared with the injected truth, over 3 sessions and many Monte-Carlo replications.

Outputs (results/vrig/):
    campaign_plan.csv     per model: window lengths, N, LAT and VERIFY time, energy-window time, total wall time
    precision.csv         per model and shunt: bias and 95 % spread of the 3-session dE estimate, session CV
    sensitivity.csv       CV and bias against board noise, baseline drift and shunt value (one reference model)
    summary.json          headline numbers (campaign hours, worst CV, minimum detectable power difference, ...)
    figs: fig_vrig_sensitivity.{png,pdf}, fig_campaign_time.{png,pdf}

The power-model numbers are ASSUMPTIONS, marked as such; the rig's first sessions replace them (M3). What the
script establishes is how the measurement method behaves under them: which effects dominate, which shunt to fit,
whether the M3 gate (CV < 3 %) is in reach, and how long the campaign takes.

Usage: python tools/vrig/campaign.py [--reps 30]
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.measure import parse_w_line  # noqa: E402

OUT = REPO / "results" / "vrig"
EXE = REPO / "build" / "vrig_sim"

# ---- power model and rig parameters (assumptions unless a source is given) ------------------------------------------
CFG = {
    "vbus_v": 5.0,             # USB VBUS
    "i_idle_a": 6.0e-3,        # board current at 5 V, CPU in WFI, USB CDC up, LEDs and sensor rail off (assumption)
    "i_active_a": 4.9e-3,      # extra current at 5 V while the CPU runs from flash at 64 MHz: nRF52840 6.3 mA (LDO,
                               # CoreMark, 3 V; product specification) x 3.3 V / (0.85 x 5 V) via the buck (assumption)
    "noise_a": 0.3e-3,         # per-sample current noise std: USB frames, regulator ripple (assumption; swept)
    "drift_in_window": 0.002,  # linear baseline drift across one window, fraction of i_idle (assumption; swept)
    "block_sd": 0.003,         # baseline change between the IDLE window and the windows that use it (assumption; swept)
    "session_sd": 0.01,        # baseline change between sessions (assumption)
    "offset_sd_uv": 10.0,      # INA219 shunt offset, typical +-10 uV (TI datasheet), redrawn per session
    "gain_err": 0.005,         # INA219B gain error 0.5 % max (TI datasheet), fixed for the device
    "period_us": 1064.0,       # 12-bit shunt + bus conversions, 532 us each (TI datasheet), continuous mode
    "jitter_us": 20.0,         # ESP32 read-loop jitter (assumption)
    "min_window_s": 10.0,      # CONTRACT.md 7.3
    "sessions": 3,             # execution plan: 3 repeats on at least 2 days
    "lat_reps": 101, "lat_inputs": 5, "verify_vectors": 100,   # CONTRACT.md 7.2
    "verify_io_s": 0.05,       # 4 KB over USB CDC + reply, per VERIFY (assumption)
    "cmd_overhead_s": 1.5,     # per window: command, resync, logger line (assumption)
    "warmup_s": 60.0,          # per session and bundle (execution plan)
    "flash_s": 45.0,           # compile + upload per bundle (assumption)
}


def windows_for(lat_us: float, min_s: float) -> tuple[int, float]:
    n = max(1, math.ceil(min_s * 1e6 / lat_us))
    return n, n * lat_us


def plan(costs: pd.DataFrame, n_bundles: int) -> pd.DataFrame:
    rows = []
    c = CFG
    for r in costs.itertuples(index=False):
        n_run, w_run = windows_for(r.lat_inf_us, c["min_window_s"])
        n_fe, w_fe = windows_for(max(r.lat_fe_us, 1e-3), c["min_window_s"])
        n_e2e, w_e2e = windows_for(r.lat_e2e_us, c["min_window_s"])
        lat_s = c["lat_inputs"] * c["lat_reps"] * (r.lat_inf_us + r.lat_fe_us) * 1e-6
        ver_s = c["verify_vectors"] * (r.lat_e2e_us * 1e-6 + c["verify_io_s"])
        energy_s = c["sessions"] * (c["min_window_s"] + (w_run + w_fe + w_e2e) * 1e-6 + 4 * c["cmd_overhead_s"])
        rows.append({"model_id": r.model_id, "family": r.family, "input_rep": r.input_rep, "precision": r.precision,
                     "lat_inf_us": r.lat_inf_us, "lat_fe_us": r.lat_fe_us, "N_run": n_run, "T_run_s": w_run * 1e-6,
                     "N_front": n_fe, "T_front_s": w_fe * 1e-6, "N_e2e": n_e2e, "T_e2e_s": w_e2e * 1e-6,
                     "lat_cmd_s": lat_s, "verify_s": ver_s, "energy_windows_s": energy_s,
                     "total_s": lat_s + ver_s + energy_s})
    p = pd.DataFrame(rows)
    p.attrs["fixed_s"] = c["sessions"] * n_bundles * (c["warmup_s"] + c["flash_s"])
    return p


def simulate(specs: list[tuple]) -> list:
    """Run vrig_sim on a list of window specs; returns parsed W lines (tools/measure.py's parser)."""
    txt = "\n".join(" ".join(f"{v:.10g}" if isinstance(v, float) else str(v) for v in s) for s in specs) + "\n"
    out = subprocess.run([str(EXE)], input=txt, capture_output=True, text=True, check=True).stdout.splitlines()
    assert len(out) == len(specs), (len(out), len(specs))
    return [parse_w_line(ln) for ln in out]


def campaign_mc(p: pd.DataFrame, shunt: float, reps: int, rng: np.random.Generator, cfg: dict,
                modes=("RUN", "FRONT", "E2E")) -> pd.DataFrame:
    """Monte-Carlo of the 3-session campaign for every model; returns per-model error statistics of dE."""
    c = cfg
    specs, keys = [], []
    for rep in range(reps):
        for s in range(c["sessions"]):
            base_s = c["i_idle_a"] * (1 + rng.normal(0, c["session_sd"]))
            off = rng.normal(0, c["offset_sd_uv"])
            for i, r in enumerate(p.itertuples(index=False)):
                def spec(dur_s, ia):
                    b = base_s * (1 + rng.normal(0, c["block_sd"]))
                    return (dur_s * 1e6, int(rng.integers(1, 2**62)), shunt, c["vbus_v"], b, ia, c["noise_a"],
                            float(rng.normal(0, c["drift_in_window"])), off, c["gain_err"], c["period_us"],
                            c["jitter_us"])
                specs.append(spec(c["min_window_s"], 0.0)); keys.append((rep, s, i, "IDLE"))
                for m, T in (("RUN", r.T_run_s), ("FRONT", r.T_front_s), ("E2E", r.T_e2e_s)):
                    if m in modes:
                        specs.append(spec(T, c["i_active_a"])); keys.append((rep, s, i, m))
    W = simulate(specs)
    pm = {k: w.Pmean_uW for k, w in zip(keys, W)}
    rows = []
    # injected active power as delivered to the board (load side of the shunt): P = (V - I R) I
    ib, ia, v = c["i_idle_a"], c["i_active_a"], c["vbus_v"]
    true_dp_uw = ((v - shunt * (ib + ia)) * (ib + ia) - (v - shunt * ib) * ib) * 1e6
    for i, r in enumerate(p.itertuples(index=False)):
        for m, T, N in (("RUN", r.T_run_s, r.N_run), ("FRONT", r.T_front_s, r.N_front), ("E2E", r.T_e2e_s, r.N_e2e)):
            if m not in modes:
                continue
            truth = true_dp_uw * T / N                          # uJ per inference
            est = np.array([[(pm[(rep, s, i, m)] - pm[(rep, s, i, "IDLE")]) * T / N for s in range(c["sessions"])]
                            for rep in range(reps)])
            mean3 = est.mean(axis=1)
            rel = mean3 / truth - 1
            cv = est.std(axis=1, ddof=1) / est.mean(axis=1)
            rows.append({"model_id": r.model_id, "family": r.family, "mode": m, "shunt_ohm": shunt,
                         "truth_uJ": truth, "bias_pct": 100 * rel.mean(), "spread95_pct": 100 * np.percentile(np.abs(rel - rel.mean()), 95),
                         "cv_median_pct": 100 * np.median(cv), "cv_p95_pct": 100 * np.percentile(cv, 95)})
    return pd.DataFrame(rows)


def sensitivity(p: pd.DataFrame, rng: np.random.Generator, reps: int) -> pd.DataFrame:
    """One reference model (median RUN window), swept over noise, drift and shunt."""
    ref = p.iloc[[int(np.argsort(p.T_run_s.to_numpy())[len(p) // 2])]]
    rows = []
    for shunt in (0.1, 1.0):
        for noise_ma in (0.01, 0.03, 0.1, 0.3, 1.0):
            cfg = dict(CFG, noise_a=noise_ma * 1e-3)
            d = campaign_mc(ref, shunt, reps, rng, cfg, modes=("RUN",)).iloc[0]
            rows.append({"sweep": "noise", "x": noise_ma, "shunt_ohm": shunt, "bias_pct": d.bias_pct,
                         "spread95_pct": d.spread95_pct, "cv_median_pct": d.cv_median_pct, "cv_p95_pct": d.cv_p95_pct})
        for block in (0.001, 0.003, 0.01, 0.03, 0.1):
            cfg = dict(CFG, block_sd=block)
            d = campaign_mc(ref, shunt, reps, rng, cfg, modes=("RUN",)).iloc[0]
            rows.append({"sweep": "block_drift", "x": 100 * block, "shunt_ohm": shunt, "bias_pct": d.bias_pct,
                         "spread95_pct": d.spread95_pct, "cv_median_pct": d.cv_median_pct, "cv_p95_pct": d.cv_p95_pct})
    out = pd.DataFrame(rows)
    out.attrs["ref_model"] = ref.model_id.iloc[0]
    return out


def figures(p: pd.DataFrame, sens: pd.DataFrame, fixed_s: float) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.grid": True, "grid.color": "#e4e3df", "grid.linewidth": .5,
                         "legend.frameon": False, "savefig.dpi": 300})
    figs = REPO / "results" / "figs"
    col = {0.1: "#eb6834", 1.0: "#2a78d6"}
    fig, ax = plt.subplots(1, 2, figsize=(7.0, 2.4))
    for k, (sw, xl) in enumerate((("noise", "board current noise per sample (mA, std)"),
                                  ("block_drift", "baseline shift between IDLE and RUN window (%)"))):
        for sh in (0.1, 1.0):
            d = sens[(sens.sweep == sw) & (sens.shunt_ohm == sh)]
            ax[k].plot(d.x, d.cv_p95_pct, "o-", color=col[sh], ms=3, lw=1.2, label=f"{sh:g} Ω shunt: session CV (95th pct)")
            ax[k].plot(d.x, np.abs(d.bias_pct) + d.spread95_pct, "s--", color=col[sh], ms=3, lw=1.0, alpha=.7,
                       label=f"{sh:g} Ω shunt: |bias| + 95 % spread")
        ax[k].axhline(3.0, color="#a3361f", lw=1, ls=":")
        ax[k].set_xscale("log"); ax[k].set_xlabel(xl)
    from matplotlib.ticker import FuncFormatter
    ax[0].set_ylim(0, 4.2)
    ax[0].text(0.012, 3.12, "M3 gate: CV < 3 %", color="#a3361f", fontsize=6.5)
    ax[1].set_yscale("log")
    ax[1].yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax[1].text(0.11, 3.4, "M3 gate: CV < 3 %", color="#a3361f", fontsize=6.5)
    for a_ in ax:
        a_.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax[0].set_ylabel("error of energy per inference (%)")
    ax[0].legend(fontsize=5.6, loc="lower center", ncol=2, columnspacing=0.8)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(figs / f"fig_vrig_sensitivity.{ext}", bbox_inches="tight")
    plt.close(fig)

    g = p.assign(group=p.family.str.upper() + " " + p.input_rep.str.upper() + " " + p.precision)
    t = g.groupby("group")[["lat_cmd_s", "verify_s", "energy_windows_s"]].sum() / 3600
    t = t.loc[t.sum(axis=1).sort_values().index]
    fig, ax = plt.subplots(figsize=(3.5, 3.2))
    left = np.zeros(len(t))
    for colname, lab, cc in (("energy_windows_s", "energy windows (3 sessions)", "#1baf7a"),
                             ("lat_cmd_s", "LAT (5 × 101 timed runs)", "#4a3aa7"),
                             ("verify_s", "VERIFY (100 vectors)", "#eb6834")):
        ax.barh(t.index, t[colname], left=left, color=cc, label=lab, height=.7)
        left += t[colname].to_numpy()
    for y_, v in enumerate(left):
        ax.text(v + 0.05, y_, f"{v:.2f} h", va="center", fontsize=6)
    ax.set_xlim(0, left.max() * 1.18)
    ax.set_xlabel("board time per model group (hours)")
    ax.tick_params(axis="y", labelsize=6)
    ax.legend(fontsize=6, loc="lower right")
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(figs / f"fig_campaign_time.{ext}", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=30)
    a = ap.parse_args()
    if not EXE.exists() or EXE.stat().st_mtime < (REPO / "tools" / "vrig" / "vrig_sim.cpp").stat().st_mtime:
        EXE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["g++", "-O2", "-std=c++17", f"-I{REPO / 'logger'}", str(REPO / "logger" / "logger_core.cpp"),
                        str(REPO / "tools" / "vrig" / "vrig_sim.cpp"), "-o", str(EXE)], check=True)
    costs = pd.read_csv(REPO / "results" / "prehw_costs.csv")
    n_bundles = pd.read_csv(REPO / "models" / "bundles.csv").bundle.nunique() + 1   # + the int8ref ablation build
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(2026)

    p = plan(costs, n_bundles)
    p.to_csv(OUT / "campaign_plan.csv", index=False)
    prec = pd.concat([campaign_mc(p, sh, a.reps, rng, CFG) for sh in (0.1, 1.0)], ignore_index=True)
    prec.to_csv(OUT / "precision.csv", index=False)
    sens = sensitivity(p, rng, a.reps)
    sens.to_csv(OUT / "sensitivity.csv", index=False)
    figures(p, sens, p.attrs["fixed_s"])

    # adaptive LAT (reps = clamp(ceil(60 s / t), 11, 101)) as a proposed protocol change
    t_one = (p.lat_inf_us + p.lat_fe_us) * 1e-6
    reps_ad = np.clip(np.ceil(60.0 / t_one), 11, CFG["lat_reps"])
    lat_ad = CFG["lat_inputs"] * reps_ad * t_one
    one = prec[prec.shunt_ohm == 1.0]
    tenth = prec[prec.shunt_ohm == 0.1]
    total_h = (p.total_s.sum() + p.attrs["fixed_s"]) / 3600
    top = p.sort_values("total_s", ascending=False).head(3)
    sref = sens[(sens.sweep == "block_drift") & (sens.shunt_ohm == 1.0)]
    gate_drift = sref[sref.cv_p95_pct >= 3.0].x.min()
    summary = {
        "n_models": int(len(p)), "assumptions": CFG, "reps": a.reps,
        "campaign_hours": round(total_h, 1),
        "campaign_hours_lat": round(p.lat_cmd_s.sum() / 3600, 1),
        "campaign_hours_verify": round(p.verify_s.sum() / 3600, 1),
        "campaign_hours_energy": round((p.energy_windows_s.sum() + p.attrs["fixed_s"]) / 3600, 1),
        "slowest_models": [{"model_id": r.model_id, "hours": round(r.total_s / 3600, 2)} for r in top.itertuples()],
        "campaign_hours_adaptive_lat": round((p.total_s.sum() - p.lat_cmd_s.sum() + lat_ad.sum() + p.attrs["fixed_s"]) / 3600, 1),
        "cv_p95_max_pct_1ohm": round(float(one.cv_p95_pct.max()), 2),
        "cv_median_pct_1ohm": round(float(one.cv_median_pct.median()), 2),
        "bias_abs_max_pct_1ohm": round(float(one.bias_pct.abs().max()), 2),
        "spread95_max_pct_1ohm": round(float(one.spread95_pct.max()), 2),
        "bias_abs_max_pct_0p1ohm": round(float(tenth.bias_pct.abs().max()), 2),
        "min_detectable_power_diff_pct": round(float(2.8 * one.cv_median_pct.median() / math.sqrt(CFG["sessions"])), 2),
        "sens_ref_model": sens.attrs["ref_model"],
        "block_drift_pct_where_cv_p95_reaches_3pct": None if pd.isna(gate_drift) else float(gate_drift),
        "quiet_board_bias_pct_0p1ohm": round(float(sens[(sens.sweep == "noise") & (sens.shunt_ohm == 0.1) & (sens.x == 0.01)].bias_pct.iloc[0]), 2),
        "quiet_board_bias_pct_1ohm": round(float(sens[(sens.sweep == "noise") & (sens.shunt_ohm == 1.0) & (sens.x == 0.01)].bias_pct.iloc[0]), 2),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "assumptions"}, indent=1))


if __name__ == "__main__":
    main()
