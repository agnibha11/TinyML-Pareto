#!/usr/bin/env python3
"""measure.py - unattended energy campaign: Nano (via dut.py) + ESP32/INA219 logger -> hw_measurements.csv.

One call = one session over every model in the flashed bundle (CONTRACT.md 6.4, 7.2-7.4):

    for each model k:   SEL k -> LAT -> N from latency (window >= --window-s)
      for each stored input j = 0..4:   INPUT j -> IDLE -> RUN N -> FRONT N_fe -> E2E N_e2e
    each window: board reply (n, us) is paired with the logger's next "W ..." line;
                 rejected if durations differ by > 0.1 % or ovf > 0, then retried once.

Usage:
    python tools/measure.py --dry-run                       # fake board + fake logger -> results/dryrun/
    python tools/measure.py --port /dev/ttyACM0 --logger /dev/ttyUSB0 --session 1 --cable MEASURED-1 \\
                            --usb-port "left-rear" --shunt-ohm 1.012

Rows are appended and flushed one at a time, so a crash never loses finished windows. Rows are never edited:
a bad window is written with rejected=1 and measured again.
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.contract import HW_COLUMNS, MAX_DURATION_MISMATCH, SESSION_COLUMNS, parse_model_id  # noqa: E402
from tools.dut import DUT, DUTError, ModelEntry  # noqa: E402

N_INPUTS = 5
# CONTRACT.md 7.4: exact keys in exact order; integers for rise_us, fall_us, n, ovf
W_LINE = re.compile(r"^W rise_us=(\d+) fall_us=(\d+) n=(\d+) E_uJ=(-?[0-9.]+) Pmean_uW=(-?[0-9.]+) "
                    r"Pstd_uW=([0-9.]+) Vbus_mV=([0-9.]+) ovf=(\d+)$")
W_DEADLINE_S = 3.0   # logger must print the W line within this time after the board's reply


# ------------------------------------------------------------------------------------------------------------------
# Logger (ESP32 + INA219)
# ------------------------------------------------------------------------------------------------------------------
@dataclass
class Window:
    rise_us: int
    fall_us: int
    n: int
    E_uJ: float
    Pmean_uW: float
    Pstd_uW: float
    Vbus_mV: float
    ovf: int

    @property
    def duration_us(self) -> int:
        return self.fall_us - self.rise_us


def parse_w_line(line: str) -> Window:
    m = W_LINE.match(line.strip())
    if not m:
        raise ValueError(f"not a CONTRACT.md 7.4 window line: {line!r}")
    g = m.groups()
    try:
        return Window(int(g[0]), int(g[1]), int(g[2]), float(g[3]), float(g[4]), float(g[5]), float(g[6]), int(g[7]))
    except ValueError as e:
        raise ValueError(f"bad number in logger line {line!r}: {e}") from None


class Logger:
    """Reads 'W ...' lines from the ESP32 logger's USB serial port (format: CONTRACT.md 7.4)."""

    def __init__(self, port: str, logger_hash: str):
        import serial
        self._ser = serial.serial_for_url(port, baudrate=115_200, timeout=0.05)
        self.hash = logger_hash

    def arm(self) -> None:
        """Drop anything older than the window about to be measured."""
        self._ser.reset_input_buffer()

    def next_window(self, timeout_s: float = W_DEADLINE_S) -> Window:
        deadline = time.monotonic() + timeout_s
        buf = b""
        while time.monotonic() < deadline:
            buf += self._ser.read(256)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("ascii", errors="replace").strip()
                if text.startswith("W "):
                    return parse_w_line(text)
        raise TimeoutError("no W line from the logger")


# ------------------------------------------------------------------------------------------------------------------
# Dry-run stand-ins (same interface as DUT / Logger; numbers are fake and every row says so in fw_hash)
# ------------------------------------------------------------------------------------------------------------------
class FakeDUT:
    MODELS = [("dt-r2-d8-fp32", "emlearn", 9.0, 60.0), ("mlp-r2-h32x32-int8", "tflm-cmsis", 45.0, 60.0),
              ("cnn-r0-w050d4-int8", "tflm-cmsis", 2100.0, 2.0)]   # (id, runtime, inference us, front-end us)

    def __init__(self, rng: random.Random):
        self.rng, self.k, self.lat_us, self.fe_us, self.last_us = rng, None, None, None, 0

    def ping(self):
        return {"bench": "DRYRUN", "n_models": len(self.MODELS)}

    def list(self):
        return [ModelEntry(i, m[0], m[1]) for i, m in enumerate(self.MODELS)]

    def sel(self, k):
        self.k = k
        return {"k": k, "arena_used": [0, 1480, 23120][k]}

    def input(self, j):
        return {"j": j}

    def lat(self):
        _, _, inf, fe = self.MODELS[self.k]
        self.lat_us, self.fe_us = inf, fe
        return {"inf_med_us": inf, "fe_med_us": fe}

    def _window(self, us):
        self.last_us = int(us * self.rng.uniform(0.999, 1.001))
        return self.last_us

    def idle(self, ms):
        return {"us": self._window(ms * 1000)}

    def sleep(self, ms):
        return {"us": self._window(ms * 1000)}

    def run(self, n):
        return {"n": n, "us": self._window(n * self.lat_us), "cls": 3}

    def front(self, n):
        return {"n": n, "us": self._window(n * self.fe_us)}

    def e2e(self, n):
        return {"n": n, "us": self._window(n * (self.lat_us + self.fe_us)), "cls": 3}

    @staticmethod
    def n_for(window_s, per_call_us):
        return DUT.n_for(window_s, per_call_us)


class FakeLogger:
    hash = "DRYRUN"

    def __init__(self, dut: FakeDUT, rng: random.Random):
        self.dut, self.rng = dut, rng

    def arm(self):
        pass

    def next_window(self, timeout_s=3.0):
        if self.rng.random() < 0.02:  # ~2 % of windows: no W line, to exercise the timeout-as-rejection path
            raise TimeoutError("no W line (simulated)")
        us = self.dut.last_us
        # ~5 % of windows get a logger/board duration mismatch, to exercise the reject-and-retry path
        if self.rng.random() < 0.05:
            us = int(us * 1.01)
        p_uw = self.rng.gauss(24_000, 150)
        return Window(0, us, max(1, us // 1060), p_uw * us / 1e6, p_uw, self.rng.uniform(80, 200),
                      self.rng.gauss(4_950, 5), 0)


# ------------------------------------------------------------------------------------------------------------------
# Campaign
# ------------------------------------------------------------------------------------------------------------------
class CsvAppender:
    def __init__(self, path: Path, columns: list[str]):
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists() or path.stat().st_size == 0
        self._f = open(path, "a", newline="")
        self._w = csv.DictWriter(self._f, fieldnames=columns)
        if new:
            self._w.writeheader()
        elif path.read_text().splitlines()[0].split(",") != columns:
            raise SystemExit(f"{path} has a different header than the contract; refusing to append")

    def write(self, row: dict) -> None:
        self._w.writerow(row)
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def measure_window(dut, logger, out: CsvAppender, base: dict, mode: str, n: int, call, p_idle_uw):
    """Run one windowed command, pair it with the logger, write the row; retry once if rejected (CONTRACT.md 7.4).

    A missing or malformed W line counts as a rejection (row written with empty logger fields). After a second
    rejection the window is left rejected and the campaign continues; such windows are re-measured later.
    """
    for attempt in (1, 2):
        logger.arm()
        reply = call()
        t_dut = reply["us"]
        row = {**base, "mode": mode, "N": n, "t_window_us_dut": t_dut,
               "P_idle_uW": "" if p_idle_uw is None else f"{p_idle_uw:.3f}", "timestamp": now_iso()}
        try:
            w = logger.next_window()
        except (TimeoutError, ValueError) as e:
            out.write({**row, "rejected": 1})
            print(f"    {mode} rejected (logger: {e}), attempt {attempt}/2")
            continue
        mismatch = abs(w.duration_us - t_dut) / max(t_dut, 1)
        rejected = int(mismatch > MAX_DURATION_MISMATCH or w.ovf > 0)
        out.write({**row, "t_window_us_logger": w.duration_us, "E_window_uJ": f"{w.E_uJ:.3f}",
                   "P_mean_uW": f"{w.Pmean_uW:.3f}", "P_std_uW": f"{w.Pstd_uW:.3f}", "V_bus_mV": f"{w.Vbus_mV:.1f}",
                   "n_samples": w.n, "ovf": w.ovf, "rejected": rejected})
        if not rejected:
            return w
        print(f"    {mode} rejected (duration mismatch {mismatch:.4%}, ovf={w.ovf}), attempt {attempt}/2")
    print(f"    {mode} still rejected after 2 attempts: left for re-measurement")
    return None


def run_session(dut, logger, out: CsvAppender, session: int, window_s: float, models: list[int] | None,
                sleep_ms: int, allow_nonconforming_ids: bool = False) -> None:
    fw = dut.ping()["bench"]
    entries = dut.list()
    todo = [e for e in entries if models is None or e.k in models]
    if not allow_nonconforming_ids:
        for e in todo:
            parse_model_id(e.model_id)   # CONTRACT.md 4: refuse to measure anything that is not a valid model ID
    print(f"session {session}: firmware {fw}, {len(todo)} of {len(entries)} models, window >= {window_s} s")
    window_ms = int(math.ceil(window_s * 1000))

    for e in todo:
        t0 = time.monotonic()
        dut.sel(e.k)
        lat = dut.lat()
        n_inf = dut.n_for(window_s, lat["inf_med_us"])
        n_fe = dut.n_for(window_s, lat["fe_med_us"])
        n_e2e = dut.n_for(window_s, lat["inf_med_us"] + lat["fe_med_us"])
        print(f"  [{e.k}] {e.model_id}: inf {lat['inf_med_us']:.1f} us, fe {lat['fe_med_us']:.1f} us "
              f"-> N={n_inf}, N_fe={n_fe}, N_e2e={n_e2e}")
        for j in range(N_INPUTS):
            dut.input(j)
            base = {"model_id": e.model_id, "session": session, "input_j": j, "fw_hash": fw,
                    "logger_hash": logger.hash}
            w_idle = measure_window(dut, logger, out, {**base, "input_j": ""}, "IDLE", 0,
                                    lambda: dut.idle(window_ms), None)
            p_idle = w_idle.Pmean_uW if w_idle else None
            measure_window(dut, logger, out, base, "RUN", n_inf, lambda: dut.run(n_inf), p_idle)
            measure_window(dut, logger, out, base, "FRONT", n_fe, lambda: dut.front(n_fe), p_idle)
            measure_window(dut, logger, out, base, "E2E", n_e2e, lambda: dut.e2e(n_e2e), p_idle)
        print(f"      done in {time.monotonic() - t0:.0f} s")

    if sleep_ms:
        base = {"model_id": "", "session": session, "input_j": "", "fw_hash": fw, "logger_hash": logger.hash}
        measure_window(dut, logger, out, base, "SLEEP", 0, lambda: dut.sleep(sleep_ms), None)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="fake board and logger; writes to results/dryrun/")
    p.add_argument("--port", help="Nano serial port, e.g. /dev/ttyACM0")
    p.add_argument("--logger", help="ESP32 logger serial port, e.g. /dev/ttyUSB0")
    p.add_argument("--logger-hash", help="git hash of the flashed ESP32 logger firmware (required for real runs)")
    p.add_argument("--session", type=int, help="session number (required for real runs; must be new)")
    p.add_argument("--allow-nonconforming-ids", action="store_true",
                   help="bring-up only: also measure models whose IDs break CONTRACT.md 4 (e.g. stub-noop)")
    p.add_argument("--window-s", type=float, default=10.0, help="minimum energy window (contract: >= 10 s)")
    p.add_argument("--models", help="comma-separated model indices (default: all in the bundle)")
    p.add_argument("--sleep-ms", type=int, default=0, help="also measure one SLEEP window of this length")
    p.add_argument("--out", type=Path)
    p.add_argument("--usb-port", default="", help="session log: which PC USB port")
    p.add_argument("--cable", default="", help="session log: cable label")
    p.add_argument("--shunt-ohm", default="", help="session log: shunt resistance measured with the DMM")
    p.add_argument("--room-temp", default="", help="session log: approximate room temperature in C")
    p.add_argument("--notes", default="")
    a = p.parse_args()

    models = [int(x) for x in a.models.split(",")] if a.models else None
    if a.dry_run:
        rng = random.Random(0)
        dut = FakeDUT(rng)
        logger = FakeLogger(dut, rng)
        out_path = a.out or REPO / "results" / "dryrun" / "hw_measurements.csv"
        window_s = min(a.window_s, 10.0)
        session = a.session or 1
    else:
        if not a.port or not a.logger:
            p.error("--port and --logger are required (or use --dry-run)")
        if a.session is None or not a.logger_hash:
            p.error("--session and --logger-hash are required for real runs")
        if a.window_s < 10.0:
            p.error("the contract requires windows >= 10 s")
        out_path = a.out or REPO / "results" / "hw_measurements.csv"
        session = a.session
        log_path = out_path.parent / "session_log.csv"
        if log_path.exists() and any(r["session"] == str(session) for r in csv.DictReader(open(log_path))):
            p.error(f"session {session} already exists in {log_path}; use a new session number")
        dut = DUT(a.port)
        logger = Logger(a.logger, a.logger_hash)
        window_s = a.window_s

    out = CsvAppender(out_path, HW_COLUMNS)
    log = CsvAppender(out_path.parent / "session_log.csv", SESSION_COLUMNS)
    log.write({"session": session, "date": now_iso(), "room_temp_C": a.room_temp, "usb_port": a.usb_port,
               "cable": a.cable, "shunt_ohm_measured": a.shunt_ohm, "logger_hash": logger.hash,
               "fw_hash": dut.ping()["bench"], "notes": ("DRY RUN - fake data. " if a.dry_run else "") + a.notes})
    try:
        run_session(dut, logger, out, session, window_s, models, a.sleep_ms, a.allow_nonconforming_ids)
    except (DUTError, ValueError) as e:
        print(f"ABORTED: {e}", file=sys.stderr)
        return 1
    finally:
        out.close()
        log.close()
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
