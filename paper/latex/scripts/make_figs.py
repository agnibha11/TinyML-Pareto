#!/usr/bin/env python3
"""Draw the data figures from figdata/*.csv (written by make_numbers.py).

    python scripts/make_figs.py           # -> figs/*.pdf

Families keep one colour and one marker everywhere (colour never carries identity alone).
"""
from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import LogLocator, NullFormatter, FuncFormatter

ROOT = Path(__file__).resolve().parents[1]
FD, OUT = ROOT / "figdata", ROOT / "figs"
OUT.mkdir(exist_ok=True)

COL = {"DT": "#2a78d6", "RF": "#eb6834", "MLP": "#1baf7a", "CNN": "#eda100"}
MRK = {"DT": "o", "RF": "s", "MLP": "^", "CNN": "D"}
NAME = {"DT": "Decision tree", "RF": "Random forest", "MLP": "MLP", "CNN": "1-D CNN"}
INK, MUTED, GRID = "#222222", "#6b6b66", "#e4e3de"
ONECOL, TWOCOL = 3.45, 7.16

mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"], "mathtext.fontset": "stix",
    "font.size": 7.5, "axes.labelsize": 7.5, "axes.titlesize": 7.5, "xtick.labelsize": 6.8, "ytick.labelsize": 6.8,
    "legend.fontsize": 6.8, "axes.edgecolor": "#888885", "axes.linewidth": 0.6, "axes.labelcolor": INK,
    "xtick.color": "#555550", "ytick.color": "#555550", "xtick.major.width": 0.5, "ytick.major.width": 0.5,
    "xtick.minor.width": 0.4, "ytick.minor.width": 0.4, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "xtick.minor.size": 1.5, "ytick.minor.size": 1.5, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.5,
    "axes.axisbelow": True, "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
    "pdf.fonttype": 42, "savefig.bbox": "tight", "savefig.pad_inches": 0.02, "lines.linewidth": 1.2,
})


def uj_fmt(v, _):
    if v >= 1000:
        return f"{v / 1000:g} mJ"
    return f"{v:g}"


def log_x(ax, lo, hi):
    ax.set_xscale("log")
    ax.set_xlim(lo, hi)
    ax.xaxis.set_major_locator(LogLocator(base=10, numticks=12))
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(2, 10)))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: {10: "10", 100: "100", 1000: "1 mJ", 10000: "10 mJ",
                                                             100000: "100 mJ", 1: "1"}.get(round(v), "")))


def family_legend(ax, loc="lower right", ncol=1, **kw):
    kw.setdefault("fontsize", 6.8)
    h = [Line2D([], [], ls="", marker=MRK[k], ms=4.2, mfc=COL[k], mec="white", mew=0.5, label=NAME[k]) for k in COL]
    return ax.legend(handles=h, loc=loc, ncol=ncol, handletextpad=0.2, columnspacing=0.8, borderaxespad=0.2, **kw)


# ------------------------------------------------------------------ Fig. 3: measured Pareto fronts
def fig_pareto():
    panels = [("cwru", "P1", "CWRU, P1 (window-random, leaky)"), ("cwru", "P3", "CWRU, P3 (bearing-disjoint)"),
              ("pu", "P1", "Paderborn, P1 (window-random, leaky)"), ("pu", "P3", "Paderborn, P3 (bearing-disjoint)")]
    fig, axs = plt.subplots(1, 4, figsize=(TWOCOL, 1.95), sharex=True)
    for ax, (ds, P, title) in zip(axs, panels):
        F = pd.read_csv(FD / f"fronts_{ds}.csv")
        g = F[F.protocol == P]
        for fam in COL:
            h = g[(g.family == fam) & ~g.front2]
            ax.scatter(h.E_E2E, h.f1, s=9, marker=MRK[fam], facecolor=COL[fam], edgecolor="none", alpha=0.32,
                       zorder=2)
        fr = g[g.front2].sort_values("E_E2E")
        # step line of the attainment front
        xs, ys = [], []
        for i, (x, y) in enumerate(zip(fr.E_E2E, fr.f1)):
            if i:
                xs.append(x); ys.append(ys[-1])
            xs.append(x); ys.append(y)
        xs.append(1.5e5); ys.append(ys[-1])
        ax.plot(xs, ys, color=INK, lw=0.9, zorder=3, solid_joinstyle="miter")
        for fam in COL:
            h = fr[fr.family == fam]
            ax.scatter(h.E_E2E, h.f1, s=22, marker=MRK[fam], facecolor=COL[fam], edgecolor="white", lw=0.6,
                       zorder=4)
        chance = (0.1 if P == "P1" else 0.25) if ds == "cwru" else 1 / 3
        ax.axhline(chance, color=MUTED, lw=0.6, ls=(0, (2, 2)), zorder=1)
        ax.text(8.5, chance + 0.012, "chance", color=MUTED, fontsize=6, va="bottom")
        log_x(ax, 8, 1.5e5)
        ax.set_ylim(0, 1.04)
        ax.set_title(title, loc="left", pad=3, color=INK)
        best = g.loc[g.f1.idxmax()]
        nf = int(g.front2.sum())
        ax.text(0.97, 0.025 if ds == "pu" else 0.135,
                f"best {best.model_id.rsplit('-', 1)[0]}\nF1 {best.f1:.3f} at {best.E_E2E:,.0f} µJ; {nf} on front",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=5.6, color=MUTED, linespacing=1.25)
    axs[0].set_ylabel("Macro-F1")
    fig.supxlabel("Measured end-to-end energy per window (µJ, log scale)", fontsize=7.5, y=0.10)
    for ax in axs[1:]:
        ax.tick_params(labelleft=True)
    h = [Line2D([], [], ls="", marker=MRK[k], ms=4.5, mfc=COL[k], mec="white", mew=0.5, label=NAME[k]) for k in COL]
    h.append(Line2D([], [], color=INK, lw=0.9, label="Pareto front (F1 vs energy); large marks on it"))
    fig.legend(handles=h, loc="upper center", ncol=5, bbox_to_anchor=(0.5, 1.0), handletextpad=0.3,
               columnspacing=1.4, fontsize=6.8)
    fig.tight_layout(w_pad=0.6, rect=(0, 0.04, 1, 0.93))
    fig.savefig(OUT / "fig_pareto.pdf")
    fig.savefig(OUT / "fig_pareto.svg")
    plt.close(fig)


# ------------------------------------------------------------------ Fig. 4: front-end share vs E2E
def fig_share():
    fig, axs = plt.subplots(1, 2, figsize=(ONECOL, 1.85), gridspec_kw=dict(width_ratios=[1.35, 1]))
    ax = axs[0]
    M = pd.read_csv(FD / "models_cwru.csv")
    share = (M.E_FRONT / M.E_E2E).clip(upper=1)
    for fam in COL:
        m = M.family == fam
        ax.scatter(100 * share[m], M.E_E2E[m], s=13, marker=MRK[fam], facecolor=COL[fam], edgecolor="white",
                   lw=0.5, zorder=3)
    ax.set_yscale("log")
    ax.set_ylim(8, 1.2e5)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: {10: "10", 100: "100", 1000: "1 mJ", 10000: "10 mJ",
                                                             100000: "100 mJ"}.get(round(v), "")))
    ax.set_xlim(-5, 106)
    ax.set_xlabel("Front-end share of E2E energy (%)")
    ax.set_ylabel("Measured E2E energy (µJ)")
    ax.set_title("(a) CWRU, 80 deployed models", loc="left", pad=3)
    family_legend(ax, loc="center left", bbox_to_anchor=(0.18, 0.62))
    # (b) front-end energy by representation, newlib vs float-only log
    ax = axs[1]
    reps = ["R0", "R1", "R2", "R4", "R3"]
    lab = {"R0": "R0 raw", "R1": "R1 stats", "R2": "R2 bands", "R4": "R4 env.", "R3": "R3 log-spec."}
    E = M.groupby("rep").E_FRONT.median()
    L = pd.read_csv(FD / "logfix_cwru.csv").groupby("rep").E_FRONT_log.median()
    y = np.arange(len(reps))
    ax.barh(y + 0.18, [E[r] for r in reps], height=0.34, color="#2a78d6", label="newlib log10f", zorder=3)
    lv = [L.get(r, np.nan) for r in reps]
    ax.barh(y - 0.18, lv, height=0.34, color="#86b6ef", label="float-only log10", zorder=3, hatch="////",
            edgecolor="#1c5cab", lw=0)
    for yi, r in zip(y, reps):
        ax.text(E[r] + 4, yi + 0.18, f"{E[r]:.0f}" if E[r] >= 10 else f"{E[r]:.1f}", va="center", fontsize=5.8,
                color=INK)
        if r in L:
            ax.text(L[r] + 4, yi - 0.18, f"{L[r]:.0f}" if L[r] >= 10 else f"{L[r]:.1f}", va="center", fontsize=5.8, color=MUTED)
    ax.set_yticks(y, [lab[r] for r in reps])
    ax.invert_yaxis()
    ax.set_xlim(0, 200)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Front-end energy (µJ)")
    ax.set_title("(b) Front end, measured", loc="left", pad=3)
    ax.legend(loc="upper right", handlelength=1.2, borderaxespad=0.1, fontsize=6.2)
    fig.tight_layout(w_pad=0.4)
    fig.savefig(OUT / "fig_energy.pdf")
    fig.savefig(OUT / "fig_energy.svg")
    plt.close(fig)


# ------------------------------------------------------------------ Fig. 5: estimate vs measurement
def fig_est():
    fig, axs = plt.subplots(1, 2, figsize=(ONECOL, 1.75), gridspec_kw=dict(width_ratios=[1.15, 1]))
    ax = axs[0]
    D = pd.read_csv(FD / "est_vs_meas_cwru.csv")
    for fam in COL:
        for i8 in (False, True):
            m = (D.family == fam) & (D.prec.str.startswith("int8") == i8)
            ax.scatter(D.E_e2e_uJ[m], D.E_E2E[m], s=11, marker=MRK[fam], facecolor="white" if i8 else COL[fam],
                       edgecolor=COL[fam] if i8 else "white", lw=0.7 if i8 else 0.45, zorder=3)
    x = np.array([1, 1e6])
    ax.plot(x, x, color=MUTED, lw=0.7, ls=(0, (3, 2)), zorder=2)
    ax.plot(x, 3 * x, color=MUTED, lw=0.5, ls=(0, (1, 1.5)), zorder=2)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlim(2, 2e5); ax.set_ylim(5, 2e5)
    for a_ in (ax.xaxis, ax.yaxis):
        a_.set_major_locator(LogLocator(base=10, numticks=7))
        a_.set_minor_formatter(NullFormatter())
        a_.set_major_formatter(FuncFormatter(lambda v, _: f"$10^{{{int(round(np.log10(v)))}}}$"))
    fig.canvas.draw()
    for xx, k, lab in [(3e4, 1, "1:1"), (8e2, 3, "3:1")]:
        p0 = ax.transData.transform((xx, k * xx)); p1 = ax.transData.transform((xx * 10, k * xx * 10))
        ang = np.degrees(np.arctan2(p1[1] - p0[1], p1[0] - p0[0]))
        below = k == 1
        ax.text(xx, k * xx / 1.35 if below else k * xx * 1.3, lab, color=MUTED, fontsize=6, rotation=ang,
                rotation_mode="anchor", ha="center", va="top" if below else "bottom")
    ax.set_xlabel("Estimated E2E (µJ, cycle model)")
    ax.set_ylabel("Measured E2E (µJ)")
    ax.set_title("(a) Energy", loc="left", pad=3)
    lg = family_legend(ax, loc="upper left", fontsize=6.2)
    ax.text(0.05, 0.40, "hollow: int8", transform=ax.transAxes, fontsize=6, color=MUTED)
    ax = axs[1]
    # ratio strips per family: energy and latency
    fams = list(COL)
    for i, fam in enumerate(fams):
        d = D[D.family == fam]
        jit = (np.random.default_rng(i).random(len(d)) - 0.5) * 0.28
        i8 = d.prec.str.startswith("int8").values
        ax.scatter(d.ratio_E[~i8], i + jit[~i8], s=9, marker=MRK[fam], facecolor=COL[fam], edgecolor="white",
                   lw=0.4, zorder=3)
        ax.scatter(d.ratio_E[i8], i + jit[i8], s=9, marker=MRK[fam], facecolor="white", edgecolor=COL[fam],
                   lw=0.7, zorder=3)
        ax.scatter(d.ratio_t, i + 0.42 + jit * 0.4, s=5, marker="|", color=MUTED, lw=0.6, zorder=3)
    ax.axvline(1, color=MUTED, lw=0.6, ls=(0, (3, 2)))
    ax.set_xscale("log")
    ax.set_xlim(0.1, 8)
    ax.set_xticks([0.1, 0.3, 1, 3], ["0.1", "0.3", "1", "3"])
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_yticks(range(4), [NAME[f] if f != "RF" else "Forest" for f in fams])
    ax.set_ylim(3.75, -0.5)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Ratio (marks: energy, ticks: latency)")
    ax.set_title("(b) Measured / estimated", loc="left", pad=3)
    fig.tight_layout(w_pad=0.4)
    fig.savefig(OUT / "fig_est.pdf")
    fig.savefig(OUT / "fig_est.svg")
    plt.close(fig)


# ------------------------------------------------------------------ Fig. 6: measured cascade
def fig_cascade():
    import json
    cr = json.loads((ROOT / "scripts" / "cascade_rates.json").read_text())
    FAC, SELF = "#9a988f", "#1baf7a"
    fig, axs = plt.subplots(1, 2, figsize=(ONECOL, 1.8), gridspec_kw=dict(width_ratios=[1.0, 1.05]))
    ax = axs[0]
    G = pd.read_csv(FD / "gates_cwru.csv").set_index("gate")
    r = cr["cwru_board_split"]
    pts = [(1, r["G1"][1], "G1 tree", (5, -3)), (2, r["G2"][1], "G2, G3", (-4, 4)),
           (4, r["G4"][1], "G4 2-of-2", (4, -9))]
    for gi, fpr, lab, off in pts:
        e = G.loc[gi, "mean"]
        ax.scatter(e, fpr, s=24, marker="o", facecolor=FAC, edgecolor="white", lw=0.6, zorder=4)
        ax.annotate(lab, (e, fpr), xytext=off, textcoords="offset points", fontsize=6, color=INK,
                    ha="right" if off[0] < 0 else "left")
    e5 = G.loc[5, "mean"]
    for n, key, dy in [(16, "G5n16", -0.0), (32, "G5n32", 0.0)]:
        ax.scatter(e5, r[key][1], s=46, marker="*", facecolor=SELF, edgecolor="white", lw=0.5, zorder=5)
    ax.annotate(f"G5 self-cal.\nFPR {r['G5n16'][1]:.3f}", (e5, r["G5n16"][1]), xytext=(-6, 6),
                textcoords="offset points", fontsize=6, color="#16855e", ha="right")
    e2 = G.loc[2, "mean"]
    ax.annotate("", (e5 + 0.05, r["G5n16"][1] + 0.05), (e2 + 0.06, 0.95),
                arrowprops=dict(arrowstyle="-|>", lw=0.8, color=SELF, shrinkA=3, shrinkB=3,
                                connectionstyle="arc3,rad=-0.35"))
    ax.text(9.62, 0.62, f"re-fit on\n{cr['window_samples'] * 16 / cr['fs_hz']:.1f} s of\nhealthy\nsignal",
            fontsize=5.8, color="#16855e", ha="right", va="center", linespacing=1.15)
    ax.set_xlim(7.2, 10.9)
    ax.set_ylim(-0.08, 1.12)
    ax.set_xlabel("Measured $E_1$ (µJ per window)")
    ax.set_ylabel("False-alarm rate, unseen load")
    ax.set_title("(a) CWRU gates (P2, fold 0)", loc="left", pad=3)
    # (b) energy per window at 1 % prevalence
    ax = axs[1]
    ep, rg = cr["E_pi1_stream"], cr["pu_rule_gate"]
    M = pd.read_csv(FD / "models_cwru.csv").set_index("model_id")
    cw_alone = M.loc["rf-r2-t25d10-fp32", "E_E2E"]
    rows = [("G1 tree", ep["cwru_G1"], FAC), ("G5 self-cal.", ep["cwru_G5"], SELF),
            ("rule gate", rg["E_cascade_pi1"], FAC), ("G1 tree", ep["pu_G1"], FAC), ("G6 self-cal.", ep["pu_G6"], SELF)]
    y = np.array([0, 1, 2.6, 3.6, 4.6])
    ax.barh(y, [v for _, v, _ in rows], height=0.62, color=[c for *_, c in rows], zorder=3)
    for yi, (_, v, _) in zip(y, rows):
        ax.text(v + 2, yi, f"{v:.0f}" if v >= 20 else f"{v:.1f}", va="center", fontsize=6, color=INK)
    for (y0, y1, val, lab) in [(-0.45, 1.45, cw_alone, "model alone"), (2.15, 5.05, rg["E_model_alone"], "model alone")]:
        ax.plot([val, val], [y0, y1], color=INK, lw=0.7, ls=(0, (2, 1.5)), zorder=4)
    ax.text(cw_alone + 2, 0.5, "stage 2\nalone", fontsize=5.6, color=MUTED, va="center", linespacing=1.1)
    ax.set_yticks(y, [l for l, *_ in rows])
    ax.text(2, -0.62, "CWRU", fontsize=6.4, color=INK, va="center", fontstyle="italic")
    ax.text(2, 1.98, "Paderborn", fontsize=6.4, color=INK, va="center", fontstyle="italic")
    ax.invert_yaxis()
    ax.set_ylim(5.1, -1.05)
    ax.set_xlim(0, 128)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("µJ per window at $\\pi$ = 1 %")
    ax.set_title("(b) Cascade energy", loc="left", pad=3)
    fig.tight_layout(w_pad=0.3)
    fig.savefig(OUT / "fig_cascade.pdf")
    fig.savefig(OUT / "fig_cascade.svg")
    plt.close(fig)


if __name__ == "__main__":
    fig_pareto()
    fig_share()
    fig_est()
    fig_cascade()
    print("figures written to", OUT)
