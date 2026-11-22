#!/usr/bin/env python3
"""verify_gate.py - block S-V (docs/cascade_preregistration.md §5.2, RQ1): the board's gates against the host export.

    python tools/verify_gate.py --port /dev/ttyACM0 --dataset cwru [--session 80] [--quick]
    python tools/verify_gate.py --sim firmware/tests/host_sim/bench_sim_cascade

Checks, in this order, with nothing measured:
  1. GVERIFY of every vector of handoff/cascade/<ds>/gverify_int16.npy (test vectors, stored inputs, the pool):
     G1 and G2 alarms, the fx_r1 features and the Mahalanobis distance; then again after CALIB 16 and CALIB 32 for G5,
     and, on Paderborn, after CALIB 128 with gate 6 selected for G6 (and the 14 fx_r2_hyb bands).
  2. STREAM 1 s for every stream and gate (G5 after CALIB 16 and after CALIB 32): the seq hash and the alarm count
     against streams.csv.
--quick (start of every session): 20 vectors and the S0/ST streams of every gate.

RQ1 rule: alarms agree 100 %. A mismatch is explained only when the host score lies within 1e-5 (relative) of the
threshold (gverify.csv *_margin_rel); it is reported with its margin either way. Feature and distance differences are
reported (max relative error), not judged: the board may fuse multiply-adds.

Rows: results/<ds>/cascade_hw/gate_verify.csv (one per vector x calibration state, and per stream x gate).
Exit code 0 only if every alarm agrees.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.dut import DUT, fnv1a  # noqa: E402

STREAMS = ("S0", "S2", "S10", "S100", "ST")
GATE_COLS = {1: "sup", 2: "healthy", 3: "either", 4: "either2"}
MARGIN = 1e-5


def verify(dut: DUT, ds: str, quick: bool, session) -> tuple[pd.DataFrame, int]:
    d = REPO / "handoff" / "cascade" / ds
    W = np.load(d / "gverify_int16.npy")
    ref = pd.read_csv(d / "gverify.csv")
    st = pd.read_csv(d / "streams.csv")
    fw = dut.ping()["bench"]
    vec = np.linspace(0, len(W) - 1, 20).astype(int) if quick else np.arange(len(W))
    rows, bad = [], 0
    fxc = [f"fx{i}" for i in range(10)]

    def check(i, gate, got, exp, margin, n_cal, extra):
        nonlocal bad
        ok = int(got) == int(exp)
        explained = (not ok) and abs(margin) <= MARGIN
        bad += int(not ok and not explained)
        rows.append({"session": session, "kind": "vector", "vector": int(i), "stream": "", "gate": gate,
                     "calib_n": n_cal, "board": int(got), "host": int(exp), "agree": int(ok),
                     "margin_rel": margin, "explained": int(explained), "fw_hash": fw, **extra})

    g6 = "selfcal_r2" in ref
    for n_cal in (None, 16, 32) + (("g6",) if g6 else ()):
        if n_cal == "g6":
            dut.gsel(6)
            dut.calib(128, 1)
            hbc = [f"hb{i}" for i in range(14)]
            for i in vec:
                r, e = dut.gverify(W[i]), ref.iloc[i]
                hb_err = float(np.max(np.abs(r["hb"] - e[hbc].to_numpy(float)) / np.abs(e[hbc].to_numpy(float))))
                check(i, "selfcal_r2", r["sr"], e["selfcal_r2"], e["selfcal_r2_margin_rel"], 128,
                      {"fx_max_rel_err": hb_err, "d_rel_err": abs(r["dsr"] - e["selfcal_r2_d"]) / e["selfcal_r2_d"]})
            continue
        if n_cal:
            dut.gsel(5)
            dut.calib(n_cal, 1)
        for i in vec:
            r, e = dut.gverify(W[i]), ref.iloc[i]
            fx_err = float(np.max(np.abs(r["fx"] - e[fxc].to_numpy(float)) / np.maximum(np.abs(e[fxc].to_numpy(float)), 1e-12)))
            if n_cal is None:
                check(i, "sup", r["sup"], e["sup"], e["sup_margin_rel"], "",
                      {"fx_max_rel_err": fx_err, "d_rel_err": ""})
                check(i, "healthy", r["healthy"], e["healthy"], e["healthy_margin_rel"], "",
                      {"fx_max_rel_err": fx_err, "d_rel_err": abs(r["d"] - e["mahal_d"]) / e["mahal_d"]})
            else:
                c = f"selfcal{n_cal}"
                check(i, "selfcal", r["sc"], e[c], e[f"{c}_margin_rel"], n_cal,
                      {"fx_max_rel_err": fx_err, "d_rel_err": abs(r["dsc"] - e[f"{c}_d"]) / e[f"{c}_d"]})
    stream_ids = (0, 4) if quick else range(5)
    plan = [(1, "sup", None), (2, "healthy", None), (3, "either", None), (4, "either2", None),
            (5, "selfcal16", 16), (5, "selfcal32", 32)] + ([(6, "selfcal_r2", 128)] if g6 else [])
    for g, col, n_cal in plan:
        dut.gsel(g)
        if n_cal:
            dut.calib(n_cal, 1)
        for s in stream_ids:
            exp = st[st.stream == STREAMS[s]].sort_values("pos")[col].to_numpy()
            r = dut.stream(1, s)
            ok = r["seq"] == fnv1a(exp) and r["alarms"] == exp.sum()
            bad += int(not ok)
            rows.append({"session": session, "kind": "stream", "vector": "", "stream": STREAMS[s], "gate": col,
                         "calib_n": n_cal or "", "board": r["alarms"], "host": int(exp.sum()), "agree": int(ok),
                         "margin_rel": "", "explained": 0, "fw_hash": fw, "fx_max_rel_err": "", "d_rel_err": ""})
    return pd.DataFrame(rows), bad


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port")
    p.add_argument("--sim", type=Path)
    p.add_argument("--dataset", choices=("cwru", "pu"), default="cwru")
    p.add_argument("--session", default="")
    p.add_argument("--quick", action="store_true")
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    proc = None
    if a.sim:
        proc = subprocess.Popen([str(a.sim)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        dut = DUT(proc.stdout.readline().split()[1], open_timeout_s=0.1)
        out = a.out or REPO / "results" / "dryrun" / "gate_verify.csv"
    else:
        if not a.port:
            p.error("--port or --sim")
        dut = DUT(a.port)
        out = a.out or REPO / "results" / a.dataset / "cascade_hw" / "gate_verify.csv"
    try:
        dut.sel(0)    # STREAM needs a model; any one, the alarms do not depend on it
        df, bad = verify(dut, a.dataset, a.quick, a.session)
    finally:
        dut.close()
        if proc:
            proc.kill()
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, mode="a", header=not out.exists(), index=False)
    v = df[df.kind == "vector"]
    print(f"vectors: {int(v.agree.sum())}/{len(v)} alarms agree, {int(v.explained.sum())} explained by margin; "
          f"max fx rel err {pd.to_numeric(v.fx_max_rel_err).max():.2e}, "
          f"max d rel err {pd.to_numeric(v.d_rel_err, errors='coerce').max():.2e}")
    s = df[df.kind == "stream"]
    print(f"streams: {int(s.agree.sum())}/{len(s)} seq + alarm count agree")
    print("PASS" if bad == 0 else f"FAIL: {bad} unexplained disagreements")
    print(f"wrote {out}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
