#!/usr/bin/env python3
"""Everything after the sweep: host summary, pre-hardware cost table, Pareto analysis, figures, headline numbers.

Inputs
    results/host/*.json                 ml/train.py (one per model x protocol x fold x seed)
    results/vmcu/vmcu_metrics.csv       tools/vmcu/run_vmcu.py (virtual Cortex-M4: cycles, memory, VERIFY)
    models/registry.csv                 ml/export.py

Outputs
    results/host_summary.csv            CONTRACT.md 6.2 (mean +- std over folds x seeds; clean and SNR rows)
    results/prehw_costs.csv             per model: flash, peak RAM, front-end / inference cycles, estimated energy
    results/pareto_<P>_<inf|e2e>.csv    frontier membership (point and bootstrap probability) per protocol x energy
    results/hv_table.csv, results/kendall_tau.csv, results/rubric_grid.csv, results/lifetime.csv
    results/headline_numbers.json       every number the slides and the paper quote
    results/figs/*.png|pdf

Energy here is a CHIP-LEVEL ESTIMATE, E = cycles / 64 MHz x P_active with P_active = 3 V x 52 uA/MHz x 64 MHz
(nRF52840 PS: CoreMark from flash, cache on, DC/DC). It is replaced by the INA219 measurements (hw_measurements.csv)
once the rig exists; the analysis code does not change, only the energy column.

Usage: python ml/stats.py [--boot 1000]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from splits import LABEL, PROTOCOLS, load_split  # noqa: E402
from tools.contract import HOST_SUMMARY_COLUMNS  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
RES = REPO / "results"
FIGS = RES / "figs"
F_CPU = 64e6
P_ACTIVE_W = 3.0 * 52e-6 * 64          # 9.98 mW: nRF52840 PS, 52 uA/MHz CoreMark from flash, DC/DC, 3 V
P_SLEEP_CHIP_W = 3.0 * 3.16e-6         # System ON, full RAM retention + RTC (nRF52840 product brief)
P_SLEEP_BOARD_W = 3.3 * 0.9e-3         # Nano 33 BLE standby after Arduino's power-reduction steps (~0.9 mA)
E_BATTERY_J = 0.225 * 3600 * 3.0       # CR2032 coin cell, 225 mAh nominal at 3 V = 2.43 kJ (as in the proposal)
SNRS = (20, 10, 5, 0)
FAM_NAME = {"dt": "DT", "rf": "RF", "mlp": "MLP", "cnn": "CNN"}
FAM_ORDER = ["DT", "RF", "MLP", "CNN"]
# Validated (dataviz validator, --pairs all, light): blue, orange, aqua, dark violet. Aqua < 3:1 on white -> legend
# and labels always present. Precision is a second encoding (marker shape).
COLORS = {"DT": "#2a78d6", "RF": "#eb6834", "MLP": "#1baf7a", "CNN": "#4a3aa7"}
MARKERS = {"fp32": "o", "int8": "s", "int8qat": "^", "int8ref": "x"}
PROT_COLORS = {"P1": "#2a78d6", "P2": "#eb6834", "P3": "#1baf7a"}
PROT_NAME = {"P1": "P1 leaky", "P2": "P2 recording-wise", "P3": "P3 bearing-disjoint"}


# ------------------------------------------------------------------------------------------------------------------
# Loading
# ------------------------------------------------------------------------------------------------------------------
def load_runs() -> pd.DataFrame:
    rows = []
    for p in sorted((RES / "host").glob("*.json")):
        r = json.loads(p.read_text())
        row = {k: r[k] for k in ("model_id", "protocol", "fold", "seed", "f1_macro", "accuracy", "n_test")}
        for n in r["noise"]:
            row[f"f1_snr{n['snr_db']}"] = n["f1_macro"]
            row[f"acc_snr{n['snr_db']}"] = n["accuracy"]
        row["y_pred"] = r["y_pred"]
        row["epochs"] = r.get("train", {}).get("epochs")
        rows.append(row)
    df = pd.DataFrame(rows)
    tok = df.model_id.str.split("-", expand=True)
    df["family"], df["rep"], df["size"], df["precision"] = tok[0], tok[1], tok[2], tok[3]
    df["fam"] = df.family.map(FAM_NAME)
    return df


def host_summary(runs: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (mid, p), g in runs.groupby(["model_id", "protocol"]):
        out.append({"model_id": mid, "protocol": p, "snr_db": "", "n_runs": len(g),
                    "f1_macro_mean": g.f1_macro.mean(), "f1_macro_std": g.f1_macro.std(ddof=0),
                    "accuracy_mean": g.accuracy.mean(), "accuracy_std": g.accuracy.std(ddof=0)})
        for s in SNRS:
            out.append({"model_id": mid, "protocol": p, "snr_db": s, "n_runs": len(g),
                        "f1_macro_mean": g[f"f1_snr{s}"].mean(), "f1_macro_std": g[f"f1_snr{s}"].std(ddof=0),
                        "accuracy_mean": g[f"acc_snr{s}"].mean(), "accuracy_std": g[f"acc_snr{s}"].std(ddof=0)})
    return pd.DataFrame(out)[HOST_SUMMARY_COLUMNS]


def cost_table() -> pd.DataFrame:
    reg = pd.read_csv(REPO / "models" / "registry.csv", keep_default_na=False)
    vm = pd.read_csv(RES / "vmcu" / "vmcu_metrics.csv")
    fp = pd.read_csv(RES / "vmcu" / "vmcu_footprint.csv")
    fe_ram = int(fp.fe_static_ram_B.max())
    c = reg[["model_id", "family", "input_rep", "config", "precision", "runtime", "params", "macs"]].merge(
        vm, on=["model_id", "runtime"], how="inner")
    feat_buf = {"r0": 4 * 1024, "r1": 4 * 10, "r2": 4 * 24, "r3": 4 * 256, "r4": 4 * 42}
    c["frontend_ram_B"] = c.input_rep.map(feat_buf) + np.where(c.input_rep.isin(["r2", "r3", "r4"]), fe_ram, 0)
    c["ram_peak_B"] = c.arena_used_B + c.frontend_ram_B + c.stack_peak_B
    c["e2e_cyc"] = c.fe_cyc_med + c.inf_cyc_med
    # Cycle-model uncertainty band (tools/vmcu/cyclemodel.py): every load could pipeline to 1 cycle and every
    # branch could cost 1 cycle less (lower bound) or 1 more (upper bound) than its modelled average.
    for ph, cyc, ld, br in (("fe", "fe_cyc_med", "fe_ld_med", "fe_br_med"), ("inf", "inf_cyc_med", "inf_ld_med", "inf_br_med")):
        c[f"{ph}_cyc_lo"] = c[cyc] - c[ld] - c[br]
        c[f"{ph}_cyc_hi"] = c[cyc] + c[br]
    c["e2e_cyc_lo"], c["e2e_cyc_hi"] = c.fe_cyc_lo + c.inf_cyc_lo, c.fe_cyc_hi + c.inf_cyc_hi
    for ph, col in (("fe", "fe_cyc_med"), ("inf", "inf_cyc_med"), ("e2e", "e2e_cyc")):
        c[f"lat_{ph}_us"] = c[col] / F_CPU * 1e6
        c[f"E_{ph}_uJ"] = c[col] / F_CPU * P_ACTIVE_W * 1e6
        c[f"E_{ph}_uJ_lo"] = c[f"{ph}_cyc_lo"] / F_CPU * P_ACTIVE_W * 1e6
        c[f"E_{ph}_uJ_hi"] = c[f"{ph}_cyc_hi"] / F_CPU * P_ACTIVE_W * 1e6
    c["fe_share"] = c.fe_cyc_med / c.e2e_cyc
    c["fam"] = c.family.map(FAM_NAME)
    return c


# ------------------------------------------------------------------------------------------------------------------
# Pareto, hypervolume, bootstrap
# ------------------------------------------------------------------------------------------------------------------
def nondominated(F: np.ndarray) -> np.ndarray:
    n = len(F)
    nd = np.ones(n, bool)
    for i in range(n):
        if not nd[i]:
            continue
        dom = np.all(F <= F[i], axis=1) & np.any(F < F[i], axis=1)
        if dom.any():
            nd[i] = False
    return nd


def objectives(df: pd.DataFrame, energy: str, f1_col: str = "f1") -> np.ndarray:
    return np.stack([1.0 - df[f1_col].to_numpy(), np.log10(df[f"E_{energy}_uJ"].to_numpy()),
                     np.log2(df.ram_peak_B.to_numpy())], axis=1)


def hypervolume(F: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    from pymoo.indicators.hv import HV
    if len(F) == 0:
        return 0.0
    Z = (F - lo) / np.where(hi > lo, hi - lo, 1.0)
    return float(HV(ref_point=np.full(F.shape[1], 1.1))(Z))


def boot_f1(runs: pd.DataFrame, protocol: str, B: int, rng) -> dict[str, np.ndarray]:
    """Bootstrap draws of each model's mean macro-F1 over its runs. Test windows are resampled per fold with the
    same indices for every model (paired), so draws are comparable across models."""
    lab = pd.read_parquet(REPO / "data" / "processed" / "manifest.parquet")[LABEL[protocol]].to_numpy()
    n_cls = 10 if LABEL[protocol] == "class10" else 4
    sub = runs[runs.protocol == protocol]
    out: dict[str, list] = {}
    for k in range(PROTOCOLS[protocol]):
        y = lab[load_split(protocol, k)["test"]]
        idx = rng.integers(0, len(y), size=(B, len(y)))
        yb = y[idx]
        for r in sub[sub.fold == k].itertuples(index=False):
            p = np.frombuffer(r.y_pred.encode(), np.uint8) - 48
            pb = p[idx]
            f1 = np.zeros(B)
            for c in range(n_cls):
                t, q = yb == c, pb == c
                tp = (t & q).sum(1)
                denom = t.sum(1) + q.sum(1)
                f1 += np.where(denom > 0, 2 * tp / np.maximum(denom, 1), 0.0)
            out.setdefault(r.model_id, []).append(f1 / n_cls)
    return {m: np.mean(v, axis=0) for m, v in out.items()}


# ------------------------------------------------------------------------------------------------------------------
# Figures
# ------------------------------------------------------------------------------------------------------------------
def style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8,
        "axes.edgecolor": "#8a8984", "axes.linewidth": 0.6, "axes.grid": True, "grid.color": "#e4e3df",
        "grid.linewidth": 0.5, "xtick.color": "#52514e", "ytick.color": "#52514e", "axes.labelcolor": "#0b0b0b",
        "legend.frameon": False, "legend.fontsize": 7, "lines.linewidth": 2, "savefig.dpi": 300,
        "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": "white"})
    return plt


def save(fig, name: str):
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGS / f"{name}.png", bbox_inches="tight")
    fig.savefig(FIGS / f"{name}.pdf", bbox_inches="tight")


def fig_protocols(summary: pd.DataFrame, plt):
    """Mean macro-F1 per family under P1/P2/P3 (best config of each family and the family median)."""
    s = summary[summary.snr_db == ""].copy()
    s["fam"] = s.model_id.str.split("-").str[0].map(FAM_NAME)
    s = s[~s.model_id.str.endswith("int8ref")]
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    x = np.arange(len(FAM_ORDER))
    w = 0.26
    for i, p in enumerate(["P1", "P2", "P3"]):
        g = s[s.protocol == p].groupby("fam").f1_macro_mean
        med, best = g.median().reindex(FAM_ORDER), g.max().reindex(FAM_ORDER)
        ax.bar(x + (i - 1) * w, med, w - 0.03, color=PROT_COLORS[p], label=PROT_NAME[p], zorder=2)
        ax.scatter(x + (i - 1) * w, best, marker="_", s=60, color="#0b0b0b", zorder=3, linewidths=1.2)
    ax.set_xticks(x, FAM_ORDER)
    ax.set_ylabel("macro-F1 (host)")
    ax.set_ylim(0, 1.05)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.22), ncol=3, fontsize=6, columnspacing=0.8)
    ax.text(0.0, -0.2, "Bars: median configuration of the family · black ticks: best configuration",
            transform=ax.transAxes, fontsize=6, color="#52514e")
    save(fig, "fig_protocol_drop")
    plt.close(fig)


def legend_handles(d: pd.DataFrame, plt) -> list:
    """Two separate keys: colour = family, marker = precision (only those present), plus the frontier line."""
    from matplotlib.lines import Line2D
    h = [Line2D([], [], ls="", marker="o", ms=5, color=COLORS[f], label=f) for f in FAM_ORDER if (d.fam == f).any()]
    names = {"fp32": "float32", "int8": "int8 PTQ", "int8qat": "int8 QAT", "int8ref": "int8 ref. kernels"}
    h += [Line2D([], [], ls="", marker=mk, ms=5, color="#6b6a66", label=names[p])
          for p, mk in MARKERS.items() if (d.precision == p).any()]
    h.append(Line2D([], [], color="#0b0b0b", lw=1.0, label="2-D frontier"))
    return h


def fig_pareto(table: pd.DataFrame, protocol: str, energy: str, plt, name: str, prob=None):
    d = table[table.protocol == protocol]
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    for fam in FAM_ORDER:
        for prec, mk in MARKERS.items():
            g = d[(d.fam == fam) & (d.precision == prec)]
            if g.empty:
                continue
            ax.scatter(g[f"E_{energy}_uJ"], 1 - g.f1, s=22 if mk != "x" else 18, marker=mk, color=COLORS[fam],
                       edgecolors="white" if mk not in ("x",) else None, linewidths=0.5, zorder=3)
    # 2-D accuracy-energy frontier (staircase)
    F = d[[f"E_{energy}_uJ", "f1"]].to_numpy()
    nd = nondominated(np.stack([F[:, 0], 1 - F[:, 1]], 1))
    fr = d[nd].sort_values(f"E_{energy}_uJ")
    xs, ys = [], []
    for e, f in zip(fr[f"E_{energy}_uJ"], 1 - fr.f1):
        if xs:
            xs.append(e)
            ys.append(ys[-1])
        xs.append(e)
        ys.append(f)
    ax.plot(xs, ys, color="#0b0b0b", lw=1.0, zorder=2)
    ax.set_xscale("log")
    ax.set_xlabel(f"{'end-to-end' if energy == 'e2e' else 'inference-only'} energy per window (µJ, est.)")
    ax.set_ylabel("error = 1 − macro-F1")
    ax.set_title(PROT_NAME[protocol], loc="left")
    ax.legend(handles=legend_handles(d, plt), loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=4, fontsize=6,
              handletextpad=0.2, columnspacing=0.8)
    save(fig, name)
    plt.close(fig)


def fig_rq2(table: pd.DataFrame, protocol: str, plt):
    d = table[table.protocol == protocol]
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    for energy, ls, col, lab in (("inf", "--", "#8a8984", "inference only"), ("e2e", "-", "#0b0b0b",
                                                                              "end-to-end (front end + inference)")):
        F = d[[f"E_{energy}_uJ", "f1"]].to_numpy()
        nd = nondominated(np.stack([F[:, 0], 1 - F[:, 1]], 1))
        fr = d[nd].sort_values(f"E_{energy}_uJ")
        xs, ys = [], []
        for e, f in zip(fr[f"E_{energy}_uJ"], 1 - fr.f1):
            if xs:
                xs.append(e)
                ys.append(ys[-1])
            xs.append(e)
            ys.append(f)
        ax.plot(xs, ys, ls=ls, color=col, lw=1.2, label=lab, zorder=2)
        for fam in FAM_ORDER:
            g = fr[fr.fam == fam]
            ax.scatter(g[f"E_{energy}_uJ"], 1 - g.f1, color=COLORS[fam], s=22, zorder=3,
                       marker="o" if energy == "e2e" else "D", edgecolors="white", linewidths=0.5,
                       label=f"{fam} ({'e2e' if energy == 'e2e' else 'inf'})" if not g.empty else None)
    ax.set_xscale("log")
    ax.set_xlabel("energy per window (µJ, est.)")
    ax.set_ylabel("error = 1 − macro-F1")
    ax.set_title(f"Frontier models, {PROT_NAME[protocol]}", loc="left")
    ax.legend(fontsize=6, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.22))
    save(fig, "fig_rq2_frontend")
    plt.close(fig)


def fig_breakdown(costs: pd.DataFrame, plt):
    """Front-end vs inference cycles per (family, input, precision) group: dots = median over sizes, bars = range."""
    c = costs.copy()
    c["group"] = c.fam + " " + c.input_rep.str.upper() + " " + c.precision
    order = (c.groupby("group").e2e_cyc.median().sort_values().index.tolist())
    fig, ax = plt.subplots(figsize=(3.5, 0.18 * len(order) + 0.9))
    for i, g in enumerate(order):
        d = c[c.group == g]
        fam = d.fam.iloc[0]
        ax.plot([d.inf_cyc_med.min(), d.inf_cyc_med.max()], [i, i], color=COLORS[fam], lw=1.2, zorder=2)
        ax.scatter([d.inf_cyc_med.median()], [i], color=COLORS[fam], s=26, zorder=3, edgecolors="white",
                   linewidths=0.5)
        ax.scatter([d.fe_cyc_med.median()], [i], marker="D", color="#52514e", s=18, zorder=3)
    ax.scatter([], [], marker="D", color="#52514e", s=18, label="front end (features or R0 normalisation)")
    for fam in FAM_ORDER:
        ax.scatter([], [], color=COLORS[fam], s=26, label=f"inference: {fam}")
    ax.set_yticks(range(len(order)), order, fontsize=6)
    ax.set_xscale("log")
    ax.set_xlabel("cycles per window (virtual Cortex-M4, estimated)")
    ax.legend(fontsize=6, loc="upper center", bbox_to_anchor=(0.45, -0.16), ncol=2)
    ax.grid(axis="y", visible=False)
    save(fig, "fig_energy_breakdown")
    plt.close(fig)


def fig_ablation(costs: pd.DataFrame, plt):
    c = costs.set_index("model_id")
    cnn = sorted({m.rsplit("-", 1)[0] for m in c.index if m.startswith("cnn") and m.endswith("int8ref")})
    if not cnn:
        return
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    x = np.arange(len(cnn))
    w = 0.38
    ref = [c.loc[f"{k}-int8ref", "inf_cyc_med"] / 1e6 for k in cnn]
    opt = [c.loc[f"{k}-int8", "inf_cyc_med"] / 1e6 if f"{k}-int8" in c.index else np.nan for k in cnn]
    ax.bar(x - w / 2, ref, w - 0.03, color="#8a8984", label="TFLM reference kernels", zorder=2)
    ax.bar(x + w / 2, opt, w - 0.03, color=COLORS["CNN"], label="CMSIS-NN kernels", zorder=2)
    for i, (r, o) in enumerate(zip(ref, opt)):
        if o == o:
            ax.text(i, max(r, o) * 1.15, f"{r / o:.1f}×", ha="center", fontsize=6, color="#0b0b0b")
    ax.set_yscale("log")
    ax.set_xticks(x, [k.replace("cnn-r0-", "") for k in cnn], rotation=30, fontsize=6)
    ax.set_ylabel("M cycles per inference (int8)")
    ax.legend(fontsize=6, loc="upper left")
    save(fig, "fig_cmsis_ablation")
    plt.close(fig)


def fig_snr(summary: pd.DataFrame, protocol: str, plt):
    s = summary[(summary.protocol == protocol)].copy()
    s = s[~s.model_id.str.endswith("int8ref")]
    s["fam"] = s.model_id.str.split("-").str[0].map(FAM_NAME)
    s["snr"] = s.snr_db.replace("", 99).astype(float)
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    order = [99, 20, 10, 5, 0]
    for fam in FAM_ORDER:
        g = s[s.fam == fam]
        best = g[g.snr == 99].sort_values("f1_macro_mean").model_id.iloc[-1] if len(g) else None
        if best is None:
            continue
        b = g[g.model_id == best].set_index("snr").reindex(order)
        ax.plot(range(len(order)), b.f1_macro_mean, marker="o", ms=4, color=COLORS[fam], label=f"{fam}: {best}")
    ax.set_xticks(range(len(order)), ["clean", "20", "10", "5", "0"])
    ax.set_xlabel("test SNR (dB)")
    ax.set_ylabel("macro-F1")
    ax.set_title(f"Best config per family, {PROT_NAME[protocol]}", loc="left")
    ax.legend(fontsize=5.5, loc="lower left")
    save(fig, f"fig_snr_{protocol}")
    plt.close(fig)


def fig_lifetime(life: pd.DataFrame, plt):
    fig, ax = plt.subplots(figsize=(3.5, 2.5))
    for mid, g in life[life.sleep == "chip"].groupby("model_id", sort=False):
        fam = FAM_NAME[mid.split("-")[0]]
        ax.plot(g.rate_hz, g.life_days / 365, color=COLORS[fam], lw=1.4, label=f"{mid} (F1 {g.f1.iloc[0]:.2f})")
        fs = g.fstar_hz.iloc[0]
        ax.axvline(fs, color=COLORS[fam], lw=0.6, ls=":")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("inference rate (windows per second)")
    ax.set_ylabel("battery life (years, CR2032)")
    ax.set_title("Best P3 model per family · dotted: crossover rate r*", loc="left", fontsize=7, color="#52514e")
    ax.legend(fontsize=5.5, loc="lower left")
    save(fig, "fig_lifetime")
    plt.close(fig)


def fig_rubric(grid: pd.DataFrame, plt):
    if grid.empty:
        return
    piv_f = grid.pivot(index="ram_budget_kB", columns="energy_budget_uJ", values="best_f1")
    piv_m = grid.pivot(index="ram_budget_kB", columns="energy_budget_uJ", values="best_fam")
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    fams = FAM_ORDER
    for i, ram in enumerate(piv_f.index):
        for j, e in enumerate(piv_f.columns):
            fam = piv_m.loc[ram, e]
            f1 = piv_f.loc[ram, e]
            col = COLORS.get(fam, "#f0efec") if isinstance(fam, str) else "#f0efec"
            ax.add_patch(plt.Rectangle((j, i), 0.96, 0.92, color=col, alpha=0.85 if isinstance(fam, str) else 1))
            ax.text(j + 0.48, i + 0.46, f"{f1:.2f}" if f1 == f1 else "–", ha="center", va="center", fontsize=5.5,
                    color="white" if isinstance(fam, str) else "#52514e")
    ax.set_xlim(0, len(piv_f.columns))
    ax.set_ylim(0, len(piv_f.index))
    ax.set_xticks(np.arange(len(piv_f.columns)) + 0.48, [f"{e:.0f}" if e >= 10 else f"{e:.1f}" for e in piv_f.columns],
                  fontsize=5.5, rotation=45)
    ax.set_yticks(np.arange(len(piv_f.index)) + 0.46, [f"{r}" for r in piv_f.index], fontsize=6)
    ax.set_xlabel("energy budget per window (µJ, est.)")
    ax.set_ylabel("RAM budget (kB)")
    ax.grid(False)
    for fam in fams:
        ax.scatter([], [], marker="s", color=COLORS[fam], label=fam)
    ax.legend(ncol=4, fontsize=6, loc="upper center", bbox_to_anchor=(0.5, 1.18))
    save(fig, "fig_rubric")
    plt.close(fig)


# ------------------------------------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args()
    rng = np.random.default_rng(2026)
    plt = style()

    runs = load_runs()
    summary = host_summary(runs)
    summary.to_csv(RES / "host_summary.csv", index=False)
    costs = cost_table()
    costs.to_csv(RES / "prehw_costs.csv", index=False)
    head: dict = {"n_runs": int(len(runs)), "n_models_with_host": int(runs.model_id.nunique()),
                  "n_models_with_costs": int(len(costs)),
                  "energy_model": {"P_active_mW": P_ACTIVE_W * 1e3, "f_cpu_MHz": F_CPU / 1e6,
                                   "P_sleep_chip_uW": P_SLEEP_CHIP_W * 1e6, "P_sleep_board_mW": P_SLEEP_BOARD_W * 1e3,
                                   "battery_J": E_BATTERY_J}}

    clean = summary[summary.snr_db == ""].copy()
    clean["fam"] = clean.model_id.str.split("-").str[0].map(FAM_NAME)
    main_set = clean[~clean.model_id.str.endswith("int8ref")]
    head["f1_by_family"] = {p: {f: {"best": round(float(g.f1_macro_mean.max()), 4),
                                    "median": round(float(g.f1_macro_mean.median()), 4),
                                    "best_model": g.sort_values("f1_macro_mean").model_id.iloc[-1]}
                                for f, g in main_set[main_set.protocol == p].groupby("fam")}
                            for p in PROTOCOLS if (main_set.protocol == p).any()}
    seeds = runs.groupby("model_id").seed.nunique()
    head["seeds_per_model"] = {"min": int(seeds.min()), "max": int(seeds.max())}

    # ---------------- Pareto tables per protocol x energy definition
    tables = []
    for p in PROTOCOLS:
        s = main_set[main_set.protocol == p][["model_id", "f1_macro_mean", "f1_macro_std"]]
        t = costs.merge(s, on="model_id").rename(columns={"f1_macro_mean": "f1", "f1_macro_std": "f1_std"})
        t = t[~t.model_id.str.endswith("int8ref")]
        t["protocol"] = p
        tables.append(t)
    T = pd.concat(tables, ignore_index=True)
    hv_rows, pareto_head = [], {}
    for p in PROTOCOLS:
        d = T[T.protocol == p].reset_index(drop=True)
        if d.empty:
            continue
        draws = boot_f1(runs[runs.model_id.isin(d.model_id)], p, a.boot, rng) if a.boot else {}
        for energy in ("inf", "e2e"):
            F = objectives(d, energy)
            nd = nondominated(F)
            lo, hi = F.min(0), F.max(0)
            prob = np.zeros(len(d))
            if draws:
                # each draw: bootstrap F1 (paired test resamples) and an energy drawn log-uniformly inside the
                # cycle-model band of each model, so membership reflects accuracy noise AND cost-model uncertainty
                elo = np.log10(d[f"E_{energy}_uJ_lo"].to_numpy())
                ehi = np.log10(d[f"E_{energy}_uJ_hi"].to_numpy())
                fb = np.stack([draws.get(m, np.full(a.boot, np.nan)) for m in d.model_id], axis=1)
                for b in range(a.boot):
                    Fb = F.copy()
                    Fb[:, 0] = 1 - fb[b]
                    Fb[:, 1] = rng.uniform(elo, ehi)
                    prob += nondominated(Fb)
                prob /= a.boot
            out = d[["model_id", "fam", "precision", "f1", "f1_std", f"E_{energy}_uJ", "ram_peak_B"]].copy()
            out["on_frontier"] = nd.astype(int)
            out["p_frontier"] = prob.round(3)
            out.sort_values(f"E_{energy}_uJ").to_csv(RES / f"pareto_{p}_{energy}.csv", index=False)
            hv_all = hypervolume(F[nd], lo, hi)
            for fam in FAM_ORDER:
                m = (d.fam == fam).to_numpy()
                hv_f = hypervolume(F[m], lo, hi) if m.any() else 0.0
                rest = F[~m]
                hv_wo = hypervolume(rest[nondominated(rest)], lo, hi) if len(rest) else 0.0
                hv_rows.append({"protocol": p, "energy": energy, "family": fam, "hv_family": round(hv_f, 4),
                                "hv_all": round(hv_all, 4), "hv_exclusive": round(hv_all - hv_wo, 4),
                                "n_frontier": int(nd[m].sum()), "n_robust": int((prob[m] >= 0.5).sum())})
            pareto_head[f"{p}_{energy}"] = {
                "frontier": out[out.on_frontier == 1].sort_values(f"E_{energy}_uJ").model_id.tolist(),
                "robust_frontier": out[out.p_frontier >= 0.5].model_id.tolist()}
    hv = pd.DataFrame(hv_rows)
    hv.to_csv(RES / "hv_table.csv", index=False)
    head["pareto"] = pareto_head

    # ---------------- Kendall tau between family rankings across protocols (and inf vs e2e)
    from scipy.stats import kendalltau
    taus = []
    for energy in ("inf", "e2e"):
        rk = {p: hv[(hv.protocol == p) & (hv.energy == energy)].set_index("family").hv_family.reindex(FAM_ORDER)
              for p in PROTOCOLS if ((hv.protocol == p) & (hv.energy == energy)).any()}
        ps = list(rk)
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                t, pv = kendalltau(rk[ps[i]], rk[ps[j]])
                taus.append({"energy": energy, "a": ps[i], "b": ps[j], "tau": round(float(t), 3), "p": round(float(pv), 3),
                             "rank_a": ">".join(rk[ps[i]].sort_values(ascending=False).index),
                             "rank_b": ">".join(rk[ps[j]].sort_values(ascending=False).index)})
    for p in PROTOCOLS:
        a_ = hv[(hv.protocol == p) & (hv.energy == "inf")].set_index("family").hv_family.reindex(FAM_ORDER)
        b_ = hv[(hv.protocol == p) & (hv.energy == "e2e")].set_index("family").hv_family.reindex(FAM_ORDER)
        if a_.notna().all() and b_.notna().all():
            t, pv = kendalltau(a_, b_)
            taus.append({"energy": "inf_vs_e2e", "a": p, "b": p, "tau": round(float(t), 3), "p": round(float(pv), 3),
                         "rank_a": ">".join(a_.sort_values(ascending=False).index),
                         "rank_b": ">".join(b_.sort_values(ascending=False).index)})
    # model-level rank agreement of macro-F1 between protocols
    piv = main_set.pivot(index="model_id", columns="protocol", values="f1_macro_mean")
    for x, y in (("P1", "P2"), ("P1", "P3"), ("P2", "P3")):
        if x in piv and y in piv:
            z = piv[[x, y]].dropna()
            t, pv = kendalltau(z[x], z[y])
            taus.append({"energy": "model_f1", "a": x, "b": y, "tau": round(float(t), 3), "p": round(float(pv), 4),
                         "rank_a": "", "rank_b": ""})
    tau = pd.DataFrame(taus)
    tau.to_csv(RES / "kendall_tau.csv", index=False)
    head["kendall_tau"] = tau.to_dict("records")

    # ---------------- cost headline numbers
    c = costs.set_index("model_id")
    fe = costs.groupby("input_rep").fe_cyc_med.median()
    head["frontend_cycles"] = {k: int(v) for k, v in fe.items()}
    head["frontend_us"] = {k: round(v / F_CPU * 1e6, 1) for k, v in fe.items()}
    trees = costs[costs.family.isin(["dt", "rf"])]
    if len(trees):
        head["tree_fe_share"] = {"min": round(float(trees.fe_share.min()), 4), "max": round(float(trees.fe_share.max()), 4)}
        head["tree_inf_cycles"] = {"min": int(trees.inf_cyc_med.min()), "max": int(trees.inf_cyc_med.max())}
    sp = {}
    for m in c.index:
        if m.endswith("int8ref") and m.replace("int8ref", "int8") in c.index:
            sp[m.replace("-int8ref", "")] = round(float(c.loc[m, "inf_cyc_med"] / c.loc[m.replace("int8ref", "int8"),
                                                                                       "inf_cyc_med"]), 2)
    head["cmsis_speedup"] = sp
    fp = {}
    for m in c.index:
        if m.endswith("-fp32") and m.replace("fp32", "int8") in c.index and m.startswith(("cnn", "mlp")):
            fp[m.replace("-fp32", "")] = round(float(c.loc[m, "inf_cyc_med"] / c.loc[m.replace("fp32", "int8"),
                                                                                    "inf_cyc_med"]), 2)
    head["fp32_over_int8_cycles"] = fp
    head["verify_class_agree_min"] = float(costs.verify_class_agree.min())
    head["verify_models_below_99"] = costs[costs.verify_class_agree < 0.99].model_id.tolist()
    par = pd.read_csv(RES / "vmcu" / "vmcu_parity.csv")
    head["parity"] = par[par.vector == -1][["rep", "max_abs_err"]].to_dict("records")
    head["ram_peak_B"] = {"min": int(costs.ram_peak_B.min()), "max": int(costs.ram_peak_B.max())}
    head["flash_model_B"] = {"min": int(costs.flash_model_B.min()), "max": int(costs.flash_model_B.max())}

    # ---------------- battery life (RQ4) for the P3 end-to-end frontier
    life_rows = []
    fr = T[(T.protocol == "P3")]
    if len(fr):
        sel = fr.sort_values("f1").groupby("fam").tail(1).sort_values("E_e2e_uJ")
        rates = np.logspace(-4, 2, 61)
        fstar = {}
        for r in sel.itertuples(index=False):
            e = r.E_e2e_uJ * 1e-6
            for sleep, ps in (("chip", P_SLEEP_CHIP_W), ("board", P_SLEEP_BOARD_W)):
                fstar[f"{r.model_id}|{sleep}"] = ps / e
                for f in rates:
                    life_rows.append({"model_id": r.model_id, "sleep": sleep, "rate_hz": f, "f1": r.f1,
                                      "E_e2e_uJ": r.E_e2e_uJ, "fstar_hz": ps / e,
                                      "life_days": E_BATTERY_J / (f * e + ps) / 86400})
        head["f_star_hz"] = {k: round(v, 5) for k, v in fstar.items()}
    life = pd.DataFrame(life_rows)
    life.to_csv(RES / "lifetime.csv", index=False)

    # ---------------- rubric grid (best P3 F1 under RAM x energy budgets)
    grid = []
    p3 = T[T.protocol == "P3"]
    if len(p3):
        ebud = np.logspace(np.log10(p3.E_e2e_uJ.min()), np.log10(p3.E_e2e_uJ.max()), 8)
        for ram in (8, 16, 32, 64, 128):
            for eb in ebud:
                ok = p3[(p3.ram_peak_B <= ram * 1024) & (p3.E_e2e_uJ <= eb * 1.0001)]
                if ok.empty:
                    grid.append({"ram_budget_kB": ram, "energy_budget_uJ": eb, "best_f1": np.nan, "best_fam": None,
                                 "best_model": None})
                else:
                    b = ok.sort_values("f1").iloc[-1]
                    grid.append({"ram_budget_kB": ram, "energy_budget_uJ": eb, "best_f1": round(float(b.f1), 4),
                                 "best_fam": b.fam, "best_model": b.model_id})
    G = pd.DataFrame(grid)
    G.to_csv(RES / "rubric_grid.csv", index=False)

    (RES / "headline_numbers.json").write_text(json.dumps(head, indent=2, default=str) + "\n")

    # ---------------- figures
    fig_protocols(summary, plt)
    for p in PROTOCOLS:
        if (T.protocol == p).any():
            fig_pareto(T, p, "e2e", plt, f"fig_pareto_{p}_e2e")
    if (T.protocol == "P2").any():
        fig_pareto(T, "P2", "inf", plt, "fig_pareto_P2_inf")
        fig_rq2(T, "P2", plt)
        fig_snr(summary, "P2", plt)
    if (T.protocol == "P3").any():
        fig_snr(summary, "P3", plt)
    fig_breakdown(costs, plt)
    fig_ablation(costs, plt)
    if not life.empty:
        fig_lifetime(life, plt)
    fig_rubric(G, plt)
    print(json.dumps({k: head[k] for k in ("n_runs", "n_models_with_host", "n_models_with_costs", "f1_by_family")},
                     indent=1)[:3000])


if __name__ == "__main__":
    main()
