#!/usr/bin/env python3
"""measure.py - unattended energy campaign: Nano (via dut.py) + ESP32/INA219 logger -> hw_measurements.csv.

One call = one session over every model in the flashed bundle (CONTRACT.md 6.4, 7.2-7.4):

    for each model k:   SEL k -> E2E 1 probe -> LAT reps (11..101 by latency)
                        -> N from the fastest input's latency x 1.05 (window >= --window-s)
      for each stored input j = 0..4:   INPUT j -> IDLE -> RUN N -> FRONT N_fe -> E2E N_e2e
    each window: board reply (n, us) is paired with the logger's next "W ..." line;
                 rejected if durations differ by > 0.1 %, ovf > 0, or the window is shorter than --window-s
                 (then N is scaled up); up to 3 attempts per window.

Usage:
    python tools/measure.py --dry-run                       # fake board + fake logger -> results/dryrun/
    python tools/measure.py --port /dev/ttyACM0 --logger /dev/ttyUSB0 --session 1 --cable MEASURED-1 \\
                            --usb-port "left-rear" --shunt-ohm 0.1139

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
# N is sized from LAT's FASTEST stored input (*_min_us), times this margin: the timed loop runs slightly faster than
# LAT's per-call timing (FRONT -0.3 %, E2E -1.4 % on the rig, 14 Nov 2026), and sizing from the median left 21 of
# 62 windows under 10 s (forests: input-dependent latency, 29.5-69.8 us for one model).
WINDOW_MARGIN = 1.05
MAX_ATTEMPTS = 3     # per window: logger rejections, duration mismatches and too-short windows all count
LOGGER_RECONNECT_S = 180.0   # after the ESP32 drops off USB, wait this long for it to come back before aborting
# LAT repetitions per stored window: clamp(ceil(60 s / t), 11, 101) with t = front end + inference from an E2E 1
# probe (CONTRACT.md 7.2). Fast models keep 101; cnn-r0-w200d4-fp32 (20.9 s per call) gets 11 instead of 101,
# so LAT takes ~21 min instead of 2.9 h.
LAT_TARGET_S, LAT_REPS_MIN, LAT_REPS_MAX = 60.0, 11, 101
LAT_MAX_CALL_S = 60.0   # one call must stay below 2^32 DWT cycles = 67.1 s at 64 MHz, or the cycle count wraps


def lat_reps(per_call_us: float) -> int:
    """Repetitions per stored window for LAT, from one front end + inference call in microseconds."""
    return int(min(LAT_REPS_MAX, max(LAT_REPS_MIN, math.ceil(LAT_TARGET_S * 1e6 / max(per_call_us, 1.0)))))


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


class LoggerDropped(Exception):
    """The logger's USB serial link dropped during a window; it has been reopened, the window must be redone."""


class Logger:
    """Reads 'W ...' lines from the ESP32 logger's USB serial port (format: CONTRACT.md 7.4).

    If the ESP32 drops off USB (seen on 15 Nov 2026: `error -71`, the port vanishing and coming back), the port is
    reopened, waiting up to LOGGER_RECONNECT_S. A drop inside a window raises LoggerDropped, which measure_window
    treats as a rejected attempt; a logger that does not come back aborts the session. Pass a stable path such as
    /dev/serial/by-id/usb-Silicon_Labs_CP2102_..., because the kernel may rename ttyUSB0 to ttyUSB1 on re-enumeration.
    """

    def __init__(self, port: str, logger_hash: str, reconnect_s: float = LOGGER_RECONNECT_S):
        self.port, self.hash, self.reconnect_s = port, logger_hash, reconnect_s
        self.drops = 0
        self.banner_wait_s = 5.0
        self._ser = self._open()

    def _open(self):
        """Open with DTR and RTS released: setting them while the ESP32 is booting can hold IO0 low and leave it
        in its ROM bootloader (no W lines), which is what happened after the first drop on 15 Nov 2026."""
        import serial
        ser = serial.serial_for_url(self.port, baudrate=115_200, timeout=0.05, do_not_open=True)
        ser.dtr = False
        ser.rts = False
        ser.open()
        return ser

    def _restart(self) -> bool:
        """Reset the ESP32 through RTS (EN) with IO0 released and wait for its '# logger' banner."""
        self._ser.rts = True
        time.sleep(0.1)
        self._ser.rts = False
        deadline, buf = time.monotonic() + self.banner_wait_s, b""
        while time.monotonic() < deadline:
            buf += self._ser.read(256)
            if b"# logger" in buf:
                return True
        return False

    def _reconnect(self, why: Exception) -> None:
        self.drops += 1
        print(f"    logger link dropped ({why}); reopening {self.port}", flush=True)
        try:
            self._ser.close()
        except Exception:  # noqa: BLE001 - the port is already gone
            pass
        deadline = time.monotonic() + self.reconnect_s
        last: Exception = why
        while time.monotonic() < deadline:
            time.sleep(2.0)
            try:
                self._ser = self._open()
                if not self._restart():
                    self._ser.close()
                    last = RuntimeError("port is back but no '# logger' banner after a reset")
                    continue
                print(f"    logger link back after drop {self.drops} (banner seen after a clean reset)", flush=True)
                return
            except Exception as e:  # noqa: BLE001 - not back yet
                last = e
        raise RuntimeError(f"logger did not come back within {self.reconnect_s:.0f} s: {last}")

    def arm(self) -> None:
        """Drop anything older than the window about to be measured (reopening the port first if it dropped)."""
        try:
            self._ser.reset_input_buffer()
        except Exception as e:  # noqa: BLE001 - serial.SerialException / OSError: the ESP32 left the bus
            self._reconnect(e)

    def next_window(self, timeout_s: float = W_DEADLINE_S) -> Window:
        deadline = time.monotonic() + timeout_s
        buf = b""
        while time.monotonic() < deadline:
            try:
                buf += self._ser.read(256)
            except Exception as e:  # noqa: BLE001 - serial.SerialException / OSError: the ESP32 left the bus
                self._reconnect(e)
                raise LoggerDropped(f"logger link dropped during the window ({e})") from None
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
        self.rng, self.k, self.lat_us, self.fe_us, self.last_us, self.j = rng, None, None, None, 0, 0

    def ping(self):
        return {"bench": "DRYRUN", "n_models": len(self.MODELS)}

    def list(self):
        return [ModelEntry(i, m[0], m[1]) for i, m in enumerate(self.MODELS)]

    def sel(self, k):
        self.k = k
        return {"k": k, "arena_used": [0, 1480, 23120][k]}

    def input(self, j):
        self.j = j
        return {"j": j}

    def lat(self, reps=None, per_call_us=None):
        _, _, inf, fe = self.MODELS[self.k]
        self.lat_us, self.fe_us = inf, fe
        self.last_reps = reps
        # stored input 2 is the fastest (like a forest's shortest tree path): min over inputs < median
        return {"inf_med_us": inf, "fe_med_us": fe, "inf_min_us": inf * self.FAST[2], "fe_min_us": fe * self.FAST[2],
                "reps": reps or 101}

    FAST = (1.0, 1.0, 0.8, 1.0, 1.0)   # per-input speed factor of the fake board

    def _window(self, us):
        self.last_us = int(us * self.rng.uniform(0.999, 1.001))
        return self.last_us

    def idle(self, ms):
        return {"us": self._window(ms * 1000)}

    def sleep(self, ms):
        return {"us": self._window(ms * 1000)}

    def run(self, n):
        return {"n": n, "us": self._window(n * self.lat_us * self.FAST[self.j]), "cls": 3}

    def front(self, n):
        return {"n": n, "us": self._window(n * self.fe_us * self.FAST[self.j])}

    def e2e(self, n):
        _, _, inf, fe = self.MODELS[self.k]   # from the model table: e2e(1) is also the probe before LAT
        return {"n": n, "us": self._window(n * (inf + fe) * self.FAST[self.j]), "cls": 3}

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


def resize_n(n: int, t_us: int, min_us: float) -> int:
    """N for the next attempt after a window of t_us came out shorter than min_us (same per-call time, plus margin)."""
    return max(n + 1, math.ceil(n * min_us / max(t_us, 1) * WINDOW_MARGIN))


def measure_window(dut, logger, out: CsvAppender, base: dict, mode: str, n: int, call, p_idle_uw,
                   min_us: float | None = None):
    """Run one windowed command call(n), pair it with the logger, write the row (CONTRACT.md 7.4).

    Up to MAX_ATTEMPTS attempts; every attempt writes one row (rows are never edited). An attempt is rejected when
    the W line is missing or malformed (empty logger fields), when board and logger durations differ by more than
    MAX_DURATION_MISMATCH or ovf > 0, or when the window is shorter than min_us (energy windows must last >= 10 s,
    CONTRACT.md 7.3): then N is scaled up for the next attempt. Still rejected after the last attempt: the window
    is left for re-measurement and the campaign continues.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.arm()
        reply = call(n)
        t_dut = reply["us"]
        row = {**base, "mode": mode, "N": n, "t_window_us_dut": t_dut,
               "P_idle_uW": "" if p_idle_uw is None else f"{p_idle_uw:.3f}", "timestamp": now_iso()}
        try:
            w = logger.next_window()
        except (TimeoutError, ValueError, LoggerDropped) as e:
            out.write({**row, "rejected": 1})
            print(f"    {mode} rejected (logger: {e}), attempt {attempt}/{MAX_ATTEMPTS}")
            continue
        mismatch = abs(w.duration_us - t_dut) / max(t_dut, 1)
        short = min_us is not None and t_dut < min_us
        rejected = int(mismatch > MAX_DURATION_MISMATCH or w.ovf > 0 or short)
        out.write({**row, "t_window_us_logger": w.duration_us, "E_window_uJ": f"{w.E_uJ:.3f}",
                   "P_mean_uW": f"{w.Pmean_uW:.3f}", "P_std_uW": f"{w.Pstd_uW:.3f}", "V_bus_mV": f"{w.Vbus_mV:.1f}",
                   "n_samples": w.n, "ovf": w.ovf, "rejected": rejected})
        if not rejected:
            return w
        if short:
            n_new = resize_n(n, t_dut, min_us)
            print(f"    {mode} rejected (window {t_dut / 1e6:.2f} s < {min_us / 1e6:.0f} s), N {n} -> {n_new}, "
                  f"attempt {attempt}/{MAX_ATTEMPTS}")
            n = n_new
        else:
            print(f"    {mode} rejected (duration mismatch {mismatch:.4%}, ovf={w.ovf}), attempt {attempt}/{MAX_ATTEMPTS}")
    print(f"    {mode} still rejected after {MAX_ATTEMPTS} attempts: left for re-measurement")
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
        # probe: one front end + inference (E2E 1) to size LAT; its D2 window is dropped by the next logger.arm()
        t_call = dut.e2e(1)["us"]
        if t_call > LAT_MAX_CALL_S * 1e6:
            print(f"  [{e.k}] {e.model_id}: SKIPPED, one call takes {t_call / 1e6:.1f} s > {LAT_MAX_CALL_S:.0f} s "
                  "(the DWT cycle counter would wrap during LAT)")
            continue
        lat = dut.lat(lat_reps(t_call), t_call)
        # size from the fastest stored input (LAT's min over the 5 per-input medians) so every input's window is
        # >= window_s; slower inputs just get longer windows
        inf_us = lat.get("inf_min_us", lat["inf_med_us"])
        fe_us = lat.get("fe_min_us", lat["fe_med_us"])
        target_s = window_s * WINDOW_MARGIN
        n_inf = dut.n_for(target_s, inf_us)
        n_fe = dut.n_for(target_s, fe_us)
        n_e2e = dut.n_for(target_s, inf_us + fe_us)
        print(f"  [{e.k}] {e.model_id}: inf {lat['inf_med_us']:.1f} us (min {inf_us:.1f}), "
              f"fe {lat['fe_med_us']:.1f} us (min {fe_us:.1f}) -> N={n_inf}, N_fe={n_fe}, N_e2e={n_e2e}"
              f" (LAT reps {lat.get('reps', 101)})")
        min_us = window_s * 1e6
        for j in range(N_INPUTS):
            dut.input(j)
            base = {"model_id": e.model_id, "session": session, "input_j": j, "fw_hash": fw,
                    "logger_hash": logger.hash}
            w_idle = measure_window(dut, logger, out, {**base, "input_j": ""}, "IDLE", 0,
                                    lambda _n: dut.idle(window_ms), None)
            p_idle = w_idle.Pmean_uW if w_idle else None
            measure_window(dut, logger, out, base, "RUN", n_inf, dut.run, p_idle, min_us)
            measure_window(dut, logger, out, base, "FRONT", n_fe, dut.front, p_idle, min_us)
            measure_window(dut, logger, out, base, "E2E", n_e2e, dut.e2e, p_idle, min_us)
        print(f"      done in {time.monotonic() - t0:.0f} s")

    if sleep_ms:
        base = {"model_id": "", "session": session, "input_j": "", "fw_hash": fw, "logger_hash": logger.hash}
        measure_window(dut, logger, out, base, "SLEEP", 0, lambda _n: dut.sleep(sleep_ms), None)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="fake board and logger; writes to results/dryrun/")
    p.add_argument("--port", help="Nano serial port, e.g. /dev/ttyACM0")
    p.add_argument("--logger", help="ESP32 logger serial port; prefer /dev/serial/by-id/usb-Silicon_Labs_CP2102_... (survives re-enumeration)")
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
        out_path = a.out or REPO / "results" / "cwru" / "hardware" / "hw_measurements.csv"
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
    except (DUTError, ValueError, RuntimeError) as e:
        print(f"ABORTED: {e}", file=sys.stderr)
        return 1
    finally:
        out.close()
        log.close()
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
