#!/usr/bin/env python3
"""measure_cascade.py - one cascade session on the cascade bundle (CONTRACT.md 7.6, docs/cascade_preregistration.md §5).

    python tools/measure_cascade.py --port /dev/ttyACM0 --logger /dev/serial/by-id/... --logger-hash <h> \\
                                    --session 81 --dataset cwru [--blocks S1,S2,S3,S4,S5,S6] [--seed 81]
    python tools/measure_cascade.py --sim firmware/tests/host_sim/bench_sim_cascade   # simulator + fake logger

Blocks (pre-registration §5.2; S-V is tools/verify_gate.py, run before this):
    S1  GATE: every gate x the 5 stored inputs (G5 after CALIB 32, Paderborn G6 after CALIB 128)
    S2  CASCADE m = 0 / 1 / 2 for every stage-2 model x the 5 stored inputs, with gate --cascade-gate (default CWRU 3,
        Paderborn 6: m = 2 then reuses the whole R2 front end of the R2 models)
    S3  STREAM S0, S2, S10, S100, ST with the stream model; CWRU gates 1, 4, 5; Paderborn 3, 4, 6
    S4  CALIB 16 and CALIB 32 of G5, Paderborn also CALIB 128 of G6 (calibration cost)
    S5  SLEEP, then DUTY at 100 ms, 1 s, 10 s with gate --duty-gate (default CWRU 5, Paderborn 6)
    S6  FXFE 0 / 1 / 2 and FRONT of the stage-2 models with an R2 or R4 front end, x the 5 stored inputs

Session rules: an IDLE window before every block; the drift anchor (GATE, gate 1, input 0) at the start, the middle
and the end; block order and the order of conditions inside a block shuffled with --seed (written to the session
log); die temperature (TEMP) read before every window. N comes from a 1-call probe (GATE 1, CASCADE 1 m, STREAM 1 s,
CALIB n 1, FXFE 1 p, FRONT 1) so every window lasts >= --window-s; windows are paired with the logger and rejected
exactly as in tools/measure.py. Gate 5 is re-calibrated with an unmeasured CALIB 32 1 before every condition that
uses it, so a CALIB 16 window never leaves it in the other state.

Rows: results/<ds>/cascade_hw/cascade_measurements.csv (CONTRACT.md 7.6 columns), session log next to it.
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.contract import CASCADE_COLUMNS, SESSION_COLUMNS  # noqa: E402
from tools.dut import DUT, DUTError  # noqa: E402
from tools.measure import (W_DEADLINE_S, WINDOW_MARGIN, CsvAppender, Logger, LoggerDropped,  # noqa: E402
                           Window, measure_window, now_iso, retry_reply)

N_INPUTS = 5
STREAM_MODEL = {"cwru": "rf-r2-t25d10-fp32", "pu": "mlp-r4-h32-fp32"}   # most accurate on the deployed protocol (§4.6)
STREAM_GATES = {"cwru": (1, 4, 5), "pu": (3, 4, 6)}
DUTY_MS = (100, 1000, 10000)
CALIB_N = (16, 32)
G5_CALIB = 32
G6_CALIB = 128          # Paderborn gate 6 (pre-registration Deviation 4)
GATES = {"cwru": (1, 2, 3, 4, 5), "pu": (1, 2, 3, 4, 5, 6)}
DEFAULT_GATE = {"cwru": {"cascade": 3, "duty": 5}, "pu": {"cascade": 6, "duty": 6}}   # Deviation 4: S-2 uses G6
ANCHOR = {"mode": "GATE", "gate": 1, "input_j": 0}
LOGGER_MIN_WINDOW_US = 100_000   # logger/logger.ino MIN_WINDOW_US: shorter D2 windows get no W line
LOGGER_BOOT_S = 2.0              # wait after opening the logger port before the first window
PROBE2_US = 500_000              # fast calls: a second probe of ~0.5 s sizes N (pilot 80: 1-call probes gave 9.5-9.9 s)


class SimLogger:
    """Stand-in for the ESP32 logger in --sim runs: a W line per window from the board's own duration."""
    hash = "simlogger"

    def __init__(self):
        self.last_us = None

    def arm(self) -> None:
        self.last_us = None

    def next_window(self, timeout_s: float = 3.0) -> Window:
        if self.last_us is None:
            raise TimeoutError("no window")
        us = self.last_us
        return Window(0, us, max(1, us // 1000), us * 1e-2, 10_000.0, 50.0, 3300.0, 0)


def sim_wrap(dut: DUT, logger: SimLogger) -> None:
    """In --sim runs, make every board reply's us visible to the fake logger."""
    orig = dut.command

    def command(cmd, *a, **k):
        kv, extra = orig(cmd, *a, **k)
        if "us" in kv:
            logger.last_us = int(kv["us"])
        return kv, extra
    dut.command = command


def conditions(ds: str, models: dict[str, int], reuse_ok: set[str], cascade_gate: int, duty_gate: int,
               fe_models: list[str]) -> dict[str, list[dict]]:
    b = {"S1": [{"mode": "GATE", "gate": g, "input_j": j} for g in GATES[ds] for j in range(N_INPUTS)],
         "S2": [{"mode": "CASCADE", "gate": cascade_gate, "model_id": m, "arg": a, "input_j": j}
                for m in models for a in (0, 1, 2) if a < 2 or m in reuse_ok for j in range(N_INPUTS)],
         "S4": [{"mode": "CALIB", "gate": 5, "arg": n} for n in CALIB_N]
         + ([{"mode": "CALIB", "gate": 6, "arg": G6_CALIB}] if 6 in GATES[ds] else []),
         "S5": [{"mode": "SLEEP"}] + [{"mode": "DUTY", "gate": duty_gate, "model_id": STREAM_MODEL[ds], "arg": t}
                                      for t in DUTY_MS],
         "S6": [{"mode": "FXFE", "arg": p, "input_j": j} for p in (0, 1, 2) for j in range(N_INPUTS)]
         + [{"mode": "FRONT", "model_id": m, "input_j": j} for m in fe_models for j in range(N_INPUTS)]}
    if STREAM_MODEL[ds]:
        b["S3"] = [{"mode": "STREAM", "gate": g, "model_id": STREAM_MODEL[ds], "stream": s}
                   for g in STREAM_GATES[ds] for s in range(5)]
    return b


class Session:
    def __init__(self, dut, logger, out: CsvAppender, session: int, window_s: float, models: dict[str, int]):
        self.dut, self.logger, self.out, self.session, self.window_s = dut, logger, out, session, window_s
        self.models = models
        self.fw = dut.ping()["bench"]
        self.state = {"sel": None, "gate": None, "input": None, "cal5": None, "cal6": None}
        self.p_idle = None

    # -- board state, outside any window --------------------------------------------------------------------------
    def unmeasured(self, fn, *args):
        """A timed command that is not a measurement (probe, setup CALIB). Its D2 window still makes the logger print a
        W line if it lasts >= 100 ms, a moment after the board's reply; read and drop that line here, or it pairs with
        the next measured window (pilot 80: 22 rejected first attempts)."""
        self.logger.arm()
        r = retry_reply(fn, *args)
        if r.get("us", 0) >= 0.9 * LOGGER_MIN_WINDOW_US:
            try:
                self.logger.next_window(W_DEADLINE_S)
            except (TimeoutError, ValueError, LoggerDropped):
                pass
        return r

    def setup(self, c: dict) -> None:
        d = self.dut
        if c.get("model_id") and self.state["sel"] != c["model_id"]:
            retry_reply(d.sel, self.models[c["model_id"]])
            self.state["sel"] = c["model_id"]
        if c.get("input_j") not in (None, "") and self.state["input"] != c["input_j"]:
            retry_reply(d.input, c["input_j"])
            self.state["input"] = c["input_j"]
        g = c.get("gate")
        if g and self.state["gate"] != g:
            retry_reply(d.gsel, g)
            self.state["gate"] = g
        # CALIB calibrates the selected gate (6 if selected, else 5); an unmeasured calibration before every
        # condition that uses a self-calibrating gate, unless it is already in the session's state
        want = {5: G5_CALIB, 6: G6_CALIB}.get(g) if c["mode"] != "CALIB" else None
        if want and self.state[f"cal{g}"] != want:
            self.unmeasured(d.calib, want, 1)
            self.state[f"cal{g}"] = want
        if c["mode"] == "CALIB":
            self.state[f"cal{g}"] = c["arg"]

    def call(self, c: dict):
        """(probe, windowed call) for one condition; both take N."""
        d, m, a = self.dut, c["mode"], c.get("arg")
        if m == "GATE":
            return lambda n: d.gate(n, self.per)
        if m == "CASCADE":
            return lambda n: d.cascade(n, a, self.per)
        if m == "STREAM":
            return lambda n: d.stream(n, c["stream"], self.per)
        if m == "CALIB":
            return lambda n: d.calib(a, n, self.per)
        if m == "FXFE":
            return lambda n: d.fxfe(n, a, self.per)
        if m == "FRONT":
            return lambda n: d.front(n)
        raise ValueError(m)

    def row_base(self, c: dict, temp_c: float) -> dict:
        return {"session": self.session, "gate": c.get("gate", ""), "model_id": c.get("model_id", ""),
                "input_j": c.get("input_j", ""), "stream": c.get("stream", ""), "arg": c.get("arg", ""),
                "die_temp_C": f"{temp_c:.2f}", "fw_hash": self.fw, "logger_hash": self.logger.hash}

    def measure(self, c: dict) -> None:
        self.setup(c)
        d, m = self.dut, c["mode"]
        base = self.row_base(c, retry_reply(d.temp))
        min_us = self.window_s * 1e6
        win_ms = int(math.ceil(self.window_s * 1000))
        fields = lambda r: {k2: r[k] for k, k2 in (("alarms", "alarms"), ("alarm", "alarms"), ("esc", "escalations"),  # noqa: E731
                                                     ("seq", "seq")) if k in r} | (
            {"seq": f"{r['seq']:08x}"} if "seq" in r else {})
        if m in ("IDLE", "SLEEP"):
            fn = d.idle if m == "IDLE" else d.sleep
            w = measure_window(d, self.logger, self.out, base, m, 0, lambda _n: fn(win_ms), None)
            if m == "IDLE" and w:
                self.p_idle = w.Pmean_uW
            return
        if m == "DUTY":
            t = c["arg"]
            n = max(1, math.ceil(self.window_s * WINDOW_MARGIN * 1000 / t))
            measure_window(d, self.logger, self.out, base, m, n, lambda n_: d.duty(t, n_), self.p_idle, min_us,
                           fields)
            return
        self.per = None
        call = self.call(c)
        if m == "FRONT":
            self.dut.fe_us = None
        t1 = self.unmeasured(call, 1)["us"]          # probe: one call
        self.per = max(float(t1), 1.0)
        if self.per < PROBE2_US / 10:                  # fast calls: one call overstates the per-call time
            n2 = math.ceil(PROBE2_US / self.per)
            self.per = max(self.unmeasured(call, n2)["us"] / n2, 1.0)
        if m == "FRONT":
            self.dut.fe_us = self.per
        n = max(1, math.ceil(self.window_s * WINDOW_MARGIN * 1e6 / self.per))
        measure_window(d, self.logger, self.out, base, m, n, call, self.p_idle, min_us, fields)

    def block(self, name: str, conds: list[dict]) -> None:
        print(f"  block {name}: {len(conds)} conditions", flush=True)
        self.measure({"mode": "IDLE"})
        for i, c in enumerate(conds):
            t0 = time.monotonic()
            self.measure(c)
            print(f"    [{i + 1}/{len(conds)}] {c} {time.monotonic() - t0:.0f} s", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", choices=("cwru", "pu"), default="cwru")
    p.add_argument("--port")
    p.add_argument("--logger")
    p.add_argument("--logger-hash")
    p.add_argument("--session", type=int)
    p.add_argument("--sim", type=Path, help="run against this cascade simulator binary with a fake logger")
    p.add_argument("--blocks", default="S1,S2,S3,S4,S5,S6")
    p.add_argument("--seed", type=int, help="condition order (default: the session number)")
    p.add_argument("--window-s", type=float, default=10.0)
    p.add_argument("--cascade-gate", type=int, help="S-2 gate (default: CWRU 3, Paderborn 6)")
    p.add_argument("--duty-gate", type=int, help="S-5 DUTY gate (default: CWRU 5, Paderborn 6)")
    p.add_argument("--out", type=Path)
    p.add_argument("--room-temp", default="")
    p.add_argument("--usb-port", default="")
    p.add_argument("--cable", default="")
    p.add_argument("--shunt-ohm", default="")
    p.add_argument("--notes", default="")
    a = p.parse_args()

    proc = None
    if a.sim:
        proc = subprocess.Popen([str(a.sim)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        dut = DUT(proc.stdout.readline().split()[1], open_timeout_s=0.1)
        logger = SimLogger()
        sim_wrap(dut, logger)
        session, window_s = a.session or 80, min(a.window_s, 0.05)
        out_path = a.out or REPO / "results" / "dryrun" / "cascade_measurements.csv"
    else:
        if not (a.port and a.logger and a.logger_hash and a.session is not None):
            p.error("--port, --logger, --logger-hash and --session are required (or use --sim)")
        if a.window_s < 10.0:
            p.error("the contract requires windows >= 10 s")
        out_path = a.out or REPO / "results" / a.dataset / "cascade_hw" / "cascade_measurements.csv"
        log_path = out_path.parent / "session_log.csv"
        if log_path.exists() and any(r["session"] == str(a.session) for r in csv.DictReader(open(log_path))):
            p.error(f"session {a.session} already exists in {log_path}")
        dut, logger = DUT(a.port), Logger(a.logger, a.logger_hash)
        time.sleep(LOGGER_BOOT_S)   # opening the CP2102 port reboots the ESP32 (session 81: first IDLE missed)
        logger.arm()
        session, window_s = a.session, a.window_s
    seed = a.seed if a.seed is not None else session
    a.cascade_gate = a.cascade_gate or DEFAULT_GATE[a.dataset]["cascade"]
    a.duty_gate = a.duty_gate or DEFAULT_GATE[a.dataset]["duty"]

    entries = dut.list()
    models = {e.model_id: e.k for e in entries}
    from tools.bundle import CASCADE_MODELS     # the bundle's stage-2 set; on the simulator: whatever LIST says
    stage2 = [m for m in (CASCADE_MODELS.get(a.dataset) or []) if m in models] or list(models)
    reuse_ok = {m for m in stage2 if m.split("-")[1] in ("r1", "r2", "r4") or a.sim}
    fe_models = [m for m in stage2 if m.split("-")[1] in ("r2", "r4")] if not a.sim else list(models)
    if a.sim:
        STREAM_MODEL[a.dataset] = STREAM_MODEL[a.dataset] if STREAM_MODEL[a.dataset] in models else stage2[0]
    blocks = conditions(a.dataset, {m: models[m] for m in stage2}, reuse_ok, a.cascade_gate, a.duty_gate, fe_models)
    if a.sim:   # keep the simulator run short: the 10 s duty period alone would take 20 s
        blocks["S5"] = [c for c in blocks["S5"] if c.get("arg") in (None, 100)]
    todo = [b for b in a.blocks.split(",") if b in blocks]
    rng = random.Random(seed)
    rng.shuffle(todo)
    for b in todo:
        rng.shuffle(blocks[b])

    out = CsvAppender(out_path, CASCADE_COLUMNS)
    log = CsvAppender(out_path.parent / "session_log.csv", SESSION_COLUMNS)
    log.write({"session": session, "date": now_iso(), "room_temp_C": a.room_temp, "usb_port": a.usb_port,
               "cable": a.cable, "shunt_ohm_measured": a.shunt_ohm, "logger_hash": logger.hash,
               "fw_hash": dut.ping()["bench"],
               "notes": ("SIM - fake logger. " if a.sim else "") + f"cascade {a.dataset}; seed {seed}; block order "
                        f"{'/'.join(todo)}; cascade_gate {a.cascade_gate}; duty_gate {a.duty_gate}. {a.notes}"})
    s = Session(dut, logger, out, session, window_s, models)
    print(f"cascade session {session} ({a.dataset}): firmware {s.fw}, blocks {todo}, seed {seed}")
    try:
        s.measure({"mode": "IDLE"})    # so the start anchor has an idle reference (session 81's had none)
        s.measure(ANCHOR)
        for i, b in enumerate(todo):
            s.block(b, blocks[b])
            if i == len(todo) // 2 - 1:
                s.measure(ANCHOR)
        s.measure(ANCHOR)
    except (DUTError, ValueError, RuntimeError) as e:
        print(f"ABORTED: {e}", file=sys.stderr)
        return 1
    finally:
        out.close()
        log.close()
        if proc:
            dut.close()
            proc.kill()
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
