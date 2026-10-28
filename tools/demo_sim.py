#!/usr/bin/env python3
"""Pipeline dry run on the simulated board: the bench firmware (PC build, real models, C front end) driven by
tools/dut.py over the CONTRACT.md serial protocol, exactly as measure.py will drive the Nano.

For every model in the simulated bundle: SEL k, then VERIFY on the 100 fixed test vectors, compared with the host
outputs in handoff/test_vectors/outputs/ (class agreement and max |output difference|), plus one RUN window.
Writes results/demo/demo_sim.txt and .svg (a terminal capture used on the midsem slides).

Usage: python tools/demo_sim.py [--exe build/host/bench_sim_models]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.table import Table

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.dut import DUT  # noqa: E402

TV = REPO / "handoff" / "test_vectors"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, default=REPO / "build" / "host" / "bench_sim_models")
    ap.add_argument("--n-vectors", type=int, default=100)
    ap.add_argument("--show-per-group", type=int, default=0,
                    help="list only N models per (family, input, precision) group in the table (all are tested)")
    a = ap.parse_args()
    con = Console(record=True, width=118)
    raw = np.load(TV / "raw.npy")[: a.n_vectors]
    proc = subprocess.Popen([str(a.exe)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    pty = proc.stdout.readline().split()[1]
    t0 = time.time()
    try:
        with DUT(pty) as d:
            con.print(f"[bold]$ python tools/demo_sim.py[/]   simulated Nano on {pty}  (bench_core.cpp built for PC)")
            con.print(f"> PING   -> {d.ping()}")
            models = d.list()
            con.print(f"> LIST   -> {len(models)} models: {models[0].model_id}, {models[1].model_id}, ... "
                      f"{models[-1].model_id}")
            tab = Table(title="SEL k + VERIFY x 100 test vectors vs host outputs (CONTRACT.md 7.2)", show_lines=False,
                        header_style="bold")
            for c in ("k", "model_id", "runtime", "arena B", "class agree", "max |Δout|", "RUN 50 -> cls"):
                tab.add_column(c, justify="right" if c not in ("model_id", "runtime") else "left")
            n_ok, shown, hidden = 0, {}, 0
            for m in models:
                sel = d.sel(m.k)
                host = np.load(TV / "outputs" / f"{m.model_id}.npy")[: a.n_vectors]
                cls, outs = zip(*(d.verify(w) for w in raw))
                outs = np.stack(outs)
                agree = float((np.array(cls) == host.argmax(1)).mean())
                err = float(np.abs(outs - host[:, : outs.shape[1]]).max())
                d.input(0)
                run = d.run(50)
                n_ok += agree >= 0.99
                fam, rep, _, prec = m.model_id.split("-")
                g = (fam, rep, prec)
                shown[g] = shown.get(g, 0) + 1
                if a.show_per_group and shown[g] > a.show_per_group and agree >= 0.99:
                    hidden += 1
                    continue
                tab.add_row(str(m.k), m.model_id, m.runtime, str(sel["arena_used"]), f"{agree:.0%}",
                            f"{err:.1e}", str(run["cls"]))
            if hidden:
                tab.add_row("…", f"{hidden} more models (all tested)", "", "", "", "", "")
            con.print(tab)
            con.print(f"[bold green]{n_ok}/{len(models)} models agree with the host on >= 99 % of the "
                      f"{a.n_vectors} vectors[/]  ({time.time() - t0:.0f} s, {len(models) * a.n_vectors} VERIFY "
                      f"round trips over the serial protocol)")
    finally:
        proc.kill()
    out = REPO / "results" / "demo"
    out.mkdir(parents=True, exist_ok=True)
    con.save_svg(str(out / "demo_sim.svg"), title="tools/demo_sim.py — simulated board")
    (out / "demo_sim.txt").write_text(con.export_text())


if __name__ == "__main__":
    main()
