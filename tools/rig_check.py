#!/usr/bin/env python3
"""rig_check.py - pre-flight check of the energy rig: cut USB cable, INA219, ESP32 logger, D2 sync line.

Run it after wiring and at the start of every session, before tools/measure.py. Every check prints PASS, WARN or
FAIL with the most likely wiring cause. Exit code 0 = no FAIL.

    python tools/rig_check.py --port /dev/ttyACM0 --logger /dev/ttyUSB0 --shunt-ohm 1.012
    python tools/rig_check.py --logger /dev/ttyUSB0 --cal 998.7 --shunt-ohm 1.012   # Nano unplugged, 1k load
    python tools/rig_check.py --port /dev/ttyACM0 --logger /dev/ttyUSB0 --watch     # live wiggle test
    python tools/rig_check.py --simulate r100     # show what a given fault looks like (no hardware needed)
    python tools/rig_check.py --simulate list     # list the simulated faults

What it checks (normal mode, Nano with the bench firmware connected):
  1  USB enumeration    Nano (Arduino VID 2341) and ESP32 (CP210x/CH340) visible -> D+/D- splice and cables OK
  2  Nano data link     200 PINGs, no loss, sane round trip -> the spliced data wires are solid
  3  Logger alive       boot banner, shunt value it was built with, no garbage on the line
  4  Sync idle          10 s with D2 LOW: no W lines -> SYNC row is pulled down (100k) and not picking up noise
  5  Sync + INA219      IDLE 2 s: W line arrives, duration matches the board, samples present, Vbus in range,
                        current positive (VIN+/VIN- not swapped) and plausible (shunt value / R100 not in parallel)
  6  Repeatability      5 IDLE windows: Pmean CV and Vbus spread -> loose screws or splices show up here
  7  Activity signature RUN on model 0 must draw more than IDLE -> the shunt is really in the Nano's supply path
Calibration mode (--cal R): Nano unplugged, known resistor from the VIN- row to GND, sync wire moved to 3V3 by hand:
  zero window (no load) -> offset; load windows -> Pmean vs Vbus^2/R -> gain error and the implied shunt value.
"""
from __future__ import annotations

import argparse
import math
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.contract import MAX_DURATION_MISMATCH  # noqa: E402
from tools.dut import DUT, DUTError, ModelEntry  # noqa: E402
from tools.measure import Window, parse_w_line  # noqa: E402

# ---------------------------------------------------------------------------------------------------------------------
# Limits (one place, so they can be tuned after the first real measurements)
# ---------------------------------------------------------------------------------------------------------------------
VBUS_OK_MV = (4750.0, 5250.0)      # USB 2.0 port at the device end, minus the splice and shunt drop
VBUS_HARD_LOW_MV = 4400.0          # below this the Nano's 3.3 V regulator margin is gone
NANO_IDLE_MA = (3.0, 30.0)         # plausible Nano 33 BLE idle current at 5 V (vrig assumed ~11 mA; not measured yet)
RANGE_MA_PER_OHM = 40.0            # INA219 PGA /1: +-40 mV full scale -> 40 mA with a 1 ohm shunt
SAMPLE_US = 1060.0                 # one 12-bit shunt+bus conversion pair
MIN_SAMPLE_FRACTION = 0.8          # at least 80 % of the ideal sample count in a window
CV_PASS, CV_WARN = 0.01, 0.03      # repeatability of IDLE windows (CONTRACT gate: session CV < 3 %)
VBUS_SPREAD_WARN_MV = 30.0
RUN_OVER_IDLE_MIN = 0.02           # RUN must draw >= 2 % more than IDLE
PING_COUNT, PING_RTT_WARN_MS = 200, 20.0
CAL_GAIN_PASS, CAL_GAIN_WARN = 0.02, 0.05
CAL_ZERO_WARN_UW = 500.0           # INA219 offset (<= 100 uV max) x 5 V / 1 ohm
ARDUINO_VIDS = {0x2341, 0x2A03}
NANO33_PIDS = {0x805A: "Nano 33 BLE (sketch)", 0x005A: "Nano 33 BLE (bootloader)", 0x8063: "Nano 33 BLE Rev2"}
ESP_BRIDGES = {0x10C4: "CP210x", 0x1A86: "CH340/CH9102", 0x0403: "FTDI", 0x303A: "ESP32 native USB"}


# ---------------------------------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Check:
    status: str      # PASS / WARN / FAIL / SKIP
    name: str
    detail: str
    hint: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    color: bool = True

    def add(self, status: str, name: str, detail: str, hint: str = "") -> Check:
        c = Check(status, name, detail, hint)
        self.checks.append(c)
        col = {"PASS": "32", "WARN": "33", "FAIL": "31", "SKIP": "90"}[status] if self.color else ""
        tag = f"\033[{col}m{status}\033[0m" if col else status
        print(f"[{tag}] {name}: {detail}")
        if hint and status in ("WARN", "FAIL"):
            for ln in hint.splitlines():
                print(f"        -> {ln}")
        return c

    @property
    def failed(self) -> bool:
        return any(c.status == "FAIL" for c in self.checks)

    def summary(self) -> None:
        n = {s: sum(c.status == s for c in self.checks) for s in ("PASS", "WARN", "FAIL", "SKIP")}
        print(f"\n{n['PASS']} passed, {n['WARN']} warnings, {n['FAIL']} failed, {n['SKIP']} skipped")
        if self.failed:
            print("Fix the FAIL items in the order listed (later checks depend on earlier ones), then run again.")
        elif n["WARN"]:
            print("Usable, but look at the warnings before a measurement session.")
        else:
            print("Rig OK - go ahead with tools/measure.py.")


def cv(xs: list[float]) -> float:
    m = statistics.fmean(xs)
    return statistics.pstdev(xs) / abs(m) if m else math.inf


# ---------------------------------------------------------------------------------------------------------------------
# Logger link: raw lines (banner, garbage) as well as W lines
# ---------------------------------------------------------------------------------------------------------------------
BANNER = re.compile(r"^# logger (\S+) shunt_ohm=([0-9.]+) period_us=(\d+)")


class LoggerLink:
    def __init__(self, port: str):
        import serial
        self._ser = serial.serial_for_url(port, baudrate=115_200, timeout=0.05)
        self._buf = b""

    def lines(self, duration_s: float) -> list[str]:
        out, end = [], time.monotonic() + duration_s
        while time.monotonic() < end:
            self._buf += self._ser.read(256)
            while b"\n" in self._buf:
                ln, self._buf = self._buf.split(b"\n", 1)
                out.append(ln.decode("ascii", errors="replace").strip())
        return [ln for ln in out if ln]

    def arm(self) -> None:
        self._ser.reset_input_buffer()
        self._buf = b""

    def next_window(self, timeout_s: float) -> Window:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            for ln in self.lines(0.05):
                if ln.startswith("W "):
                    return parse_w_line(ln)
        raise TimeoutError("no W line from the logger")

    def reset_board(self) -> None:
        """Pulse EN through RTS (DevKit auto-reset circuit) so the boot banner is printed again."""
        try:
            self._ser.dtr = False
            self._ser.rts = True
            time.sleep(0.1)
            self._ser.rts = False
        except Exception:  # noqa: BLE001 - not every adapter supports it; the banner check then just warns
            pass

    def close(self) -> None:
        self._ser.close()


def list_usb_ports() -> list[tuple[str, int | None, int | None, str]]:
    from serial.tools import list_ports
    return [(p.device, p.vid, p.pid, p.description or "") for p in list_ports.comports()]


# ---------------------------------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------------------------------
def check_enumeration(rep: Report, ports, want_nano: bool) -> tuple[str | None, str | None]:
    nano = [p for p in ports if p[1] in ARDUINO_VIDS]
    esp = [p for p in ports if p[1] in ESP_BRIDGES]
    nano_port = esp_port = None
    if want_nano:
        if not nano:
            rep.add("FAIL", "1 USB: Nano", "no Arduino device on USB",
                    "Nano power LED ON  -> VBUS reaches it but D+/D- do not: re-check the two data-wire joints\n"
                    "                      (same colour to same colour), or a joint is open.\n"
                    "Nano power LED OFF -> no 5 V: red wire not clamped in VIN+/VIN-, or R1 open/missing\n"
                    "                      (with R100 removed, R1 is the only path).")
        else:
            dev, vid, pid, desc = nano[0]
            kind = NANO33_PIDS.get(pid, f"PID {pid:04X}")
            if pid == 0x005A:
                rep.add("WARN", "1 USB: Nano", f"{dev} is in the bootloader ({kind})",
                        "Double-tap reset or a failed upload: flash the bench firmware again.")
            else:
                rep.add("PASS", "1 USB: Nano", f"{dev} ({kind}) - spliced data wires carry USB")
            nano_port = dev
    if not esp:
        rep.add("FAIL", "1 USB: ESP32", "no CP210x/CH340 serial bridge on USB",
                "Charge-only cable on the ESP32, or Ubuntu's brltty grabbed the port\n"
                "(dmesg shows 'brltty'): sudo apt remove brltty, then replug.")
    else:
        dev, vid, pid, desc = esp[0]
        rep.add("PASS", "1 USB: ESP32", f"{dev} ({ESP_BRIDGES[vid]})")
        esp_port = dev
    return nano_port, esp_port


def check_ping(rep: Report, dut) -> bool:
    try:
        info = dut.ping()
    except DUTError as e:
        rep.add("FAIL", "2 Nano link", f"no reply to PING: {e}",
                "Bench firmware not flashed / wrong port, or the data joints are intermittent.")
        return False
    fails, rtts = 0, []
    for _ in range(PING_COUNT):
        t = time.perf_counter()
        try:
            dut.ping()
            rtts.append((time.perf_counter() - t) * 1e3)
        except DUTError:
            fails += 1
    if not rtts:
        rep.add("FAIL", "2 Nano link", f"all {PING_COUNT} PINGs failed")
        return False
    med, worst = statistics.median(rtts), max(rtts)
    detail = f"bench={info['bench']} n_models={info['n_models']}; {PING_COUNT - fails}/{PING_COUNT} ok, " \
             f"RTT median {med:.1f} ms, max {worst:.1f} ms"
    if fails:
        rep.add("FAIL", "2 Nano link", detail,
                "Lost replies = intermittent D+ or D- joint. Re-solder/re-twist the two data joints, keep them\n"
                "short and twisted together, insulate each joint separately.")
        return False
    if worst > PING_RTT_WARN_MS:
        rep.add("WARN", "2 Nano link", detail, "Slow replies: USB retries on a marginal data joint, or a busy hub.")
    else:
        rep.add("PASS", "2 Nano link", detail)
    return True


def check_logger_banner(rep: Report, log, shunt_expected: float | None) -> None:
    log.reset_board()
    lines = log.lines(3.0)
    banner = next((BANNER.match(ln) for ln in lines if BANNER.match(ln)), None)
    junk = [ln for ln in lines if not (ln.startswith("#") or ln.startswith("W ") or ln.startswith("ets ")
                                       or ln.startswith("rst:") or ln.startswith("boot:"))]
    if banner is None:
        rep.add("WARN", "3 Logger", "no boot banner seen in 3 s",
                "Press EN on the ESP32 while this runs, or check that logger.ino is flashed (115200 baud).")
    else:
        h, ohm = banner.group(1), float(banner.group(2))
        msg = f"logger {h}, built with shunt_ohm={ohm:.4f}"
        if shunt_expected is not None and abs(ohm - shunt_expected) / shunt_expected > 0.005:
            rep.add("FAIL", "3 Logger", msg + f", but the DMM value is {shunt_expected:.4f}",
                    "Rebuild the logger with -DSHUNT_OHM_MEASURED=<DMM value>; every power is scaled by it.")
        else:
            rep.add("PASS", "3 Logger", msg)
    if len(junk) > 3:
        rep.add("WARN", "3 Logger", f"{len(junk)} unexpected lines, e.g. {junk[0][:60]!r}",
                "Garbage on the logger port: wrong baud, ESP32 rebooting (brown-out), or a different sketch.")


def check_sync_idle(rep: Report, log, listen_s: float) -> None:
    log.arm()
    ws = [ln for ln in log.lines(listen_s) if ln.startswith("W ")]
    if ws:
        rep.add("FAIL", "4 Sync idle", f"{len(ws)} W line(s) in {listen_s:.0f} s while D2 was LOW",
                "SYNC row floats or picks up noise: check R7 100k from the SYNC row to GND, the IO4 wire, and that\n"
                "the sync wire is not on the 3V3 rail (JP4 2-3) while the Nano is connected.")
    else:
        rep.add("PASS", "4 Sync idle", f"no spurious windows in {listen_s:.0f} s")


def judge_window(rep: Report, name: str, w: Window, board_us: int | None, shunt_ohm: float,
                 expect_ma: tuple[float, float] | None) -> bool:
    ok = True
    n_before = len(rep.checks)
    dur = w.duration_us
    if w.n == 0:
        rep.add("FAIL", name, "window has 0 samples - the INA219 never answered",
                "I2C: INA219 SDA -> ESP32 P21, SCL -> P22, VCC -> 3V3 rail, GND -> GND rail.\n"
                "Address must be 0x40: leave the A0/A1 pads unbridged.")
        return False
    if board_us:
        mis = abs(dur - board_us) / board_us
        if mis > MAX_DURATION_MISMATCH:
            rep.add("FAIL", name, f"logger saw {dur} us, board reports {board_us} us ({mis:.2%})",
                    "Sync edges arrive late or bounce: firm sync wire (D2 -> 100R -> SYNC row -> IO4), common GND.")
            ok = False
    n_min = MIN_SAMPLE_FRACTION * dur / SAMPLE_US
    if w.n < n_min:
        rep.add("WARN", name, f"only {w.n} samples in {dur / 1e6:.2f} s (expected >= {n_min:.0f})",
                "I2C reads failing part of the time: shorten / re-seat the SDA and SCL wires.")
    if w.ovf:
        rep.add("FAIL", name, f"ovf={w.ovf} (INA219 overflow or dropped samples)",
                f"Current above the {RANGE_MA_PER_OHM / shunt_ohm:.0f} mA range: a short or an extra load in the\n"
                "VIN- row, or a bigger shunt than the logger was built for.")
        ok = False
    v = w.Vbus_mV
    if v < 500:
        rep.add("FAIL", name, f"Vbus {v:.0f} mV - no voltage at VIN-",
                "Nano end of the red wire is not in the VIN- screw, or that screw is loose.")
        return False
    if v < VBUS_HARD_LOW_MV:
        rep.add("FAIL", name, f"Vbus {v:.0f} mV - far too low",
                "Series resistance in the supply: a bad red-wire joint or screw, or a very long/thin cable.")
        ok = False
    elif not VBUS_OK_MV[0] <= v <= VBUS_OK_MV[1]:
        rep.add("WARN", name, f"Vbus {v:.0f} mV outside {VBUS_OK_MV[0]:.0f}-{VBUS_OK_MV[1]:.0f} mV",
                "Weak laptop port or a resistive joint; try another port, re-tighten the screws.")
    i_ma = w.Pmean_uW / v  # uW / mV = mA
    if i_ma < 0:
        rep.add("FAIL", name, f"current {i_ma:.2f} mA is NEGATIVE",
                "VIN+ and VIN- are swapped: laptop end of the red wire -> VIN+, Nano end -> VIN-.")
        return False
    if expect_ma:
        lo, hi = expect_ma
        if i_ma < lo:
            rep.add("FAIL", name, f"current {i_ma:.2f} mA is implausibly low (expected {lo:.0f}-{hi:.0f} mA)",
                    "Most likely the module's R100 (0.1 ohm) is still fitted in parallel with the 1 ohm\n"
                    "(reads ~11x low), or the logger's SHUNT_OHM_MEASURED does not match the fitted shunt.")
            ok = False
        elif i_ma > hi:
            rep.add("WARN", name, f"current {i_ma:.2f} mA is high (expected {lo:.0f}-{hi:.0f} mA)",
                    "Calibration resistor still in the VIN- row? Anything else wired to the Nano's pins?")
    values = (f"{dur / 1e6:.3f} s, n={w.n}, Vbus {v:.0f} mV, I {i_ma:.2f} mA, "
              f"P {w.Pmean_uW / 1e3:.2f} mW (std {w.Pstd_uW / 1e3:.2f} mW)")
    if ok and len(rep.checks) == n_before:
        rep.add("PASS", name, values)
    else:
        print(f"        values: {values}")
    return ok


def window_via(dut, log, call, timeout_s: float) -> tuple[Window, int]:
    log.arm()
    reply = call()
    return log.next_window(timeout_s), reply["us"]


def check_sync_and_ina(rep: Report, dut, log, shunt: float) -> Window | None:
    try:
        w, us = window_via(dut, log, lambda: dut.idle(2000), 3.0)
    except TimeoutError:
        rep.add("FAIL", "5 Sync + INA219", "board ran IDLE 2000 but the logger printed no W line",
                "D2 is not reaching ESP32 IO4: Nano D2 -> 100R -> SYNC row -> IO4 wire, sync wire in the D2 row\n"
                "(JP4 1-2), and the Nano GND wired to the GND rail.")
        return None
    except DUTError as e:
        rep.add("FAIL", "5 Sync + INA219", f"IDLE failed on the board: {e}")
        return None
    return w if judge_window(rep, "5 Sync + INA219", w, us, shunt, NANO_IDLE_MA) else None


def check_repeatability(rep: Report, dut, log, k: int = 5, ms: int = 3000) -> None:
    ps, vs = [], []
    for _ in range(k):
        try:
            w, _ = window_via(dut, log, lambda: dut.idle(ms), ms / 1000 + 3)
        except (TimeoutError, DUTError) as e:
            rep.add("FAIL", "6 Repeatability", f"window lost: {e}", "Intermittent sync or data contact.")
            return
        ps.append(w.Pmean_uW)
        vs.append(w.Vbus_mV)
    c, spread = cv(ps), max(vs) - min(vs)
    detail = f"{k} x IDLE {ms / 1000:.0f} s: Pmean CV {c:.2%}, Vbus spread {spread:.0f} mV"
    hint = ("Something moves between windows: re-tighten the VIN+/VIN- screws, check the red-wire joints, keep\n"
            "the cables still, no phone charger or fan on the same laptop. Run --watch and wiggle each joint.")
    if c > CV_WARN:
        rep.add("FAIL", "6 Repeatability", detail, hint)
    elif c > CV_PASS or spread > VBUS_SPREAD_WARN_MV:
        rep.add("WARN", "6 Repeatability", detail, hint)
    else:
        rep.add("PASS", "6 Repeatability", detail)


def check_activity(rep: Report, dut, log, p_idle_uw: float) -> None:
    try:
        models = dut.list()
        if not models:
            rep.add("SKIP", "7 Activity", "no models in the flashed bundle")
            return
        dut.sel(0)
        dut.input(0)
        lat = dut.lat()
        n = dut.n_for(3.0, lat["inf_med_us"])
        w, us = window_via(dut, log, lambda: dut.run(n), 3.0 * 1.5 + 5)
    except (DUTError, TimeoutError) as e:
        rep.add("WARN", "7 Activity", f"could not run model 0: {e}")
        return
    rise = (w.Pmean_uW - p_idle_uw) / p_idle_uw
    detail = f"RUN {models[0].model_id}: {w.Pmean_uW / 1e3:.2f} mW vs IDLE {p_idle_uw / 1e3:.2f} mW ({rise:+.1%})"
    if rise < RUN_OVER_IDLE_MIN:
        rep.add("FAIL", "7 Activity", detail,
                "The meter does not see the CPU working: the Nano is (also) powered by another route around\n"
                "the shunt - its 5V/VIN pin wired, a second USB cable, or the uncut cable plugged in.")
    else:
        rep.add("PASS", "7 Activity", detail + " - the shunt is in the Nano's supply path")


# ---------------------------------------------------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------------------------------------------------
def run_normal(rep: Report, dut, log, ports, shunt: float, shunt_dmm: float | None, quick: bool) -> None:
    print("== Rig check: Nano connected through the measured cable ==")
    if ports is not None:
        nano_port, _ = check_enumeration(rep, ports, want_nano=True)
        if nano_port is None:
            dut = None
    if dut is None or not check_ping(rep, dut):
        rep.add("SKIP", "3-7", "need a working Nano link first")
        return
    check_logger_banner(rep, log, shunt_dmm)
    check_sync_idle(rep, log, 3.0 if quick else 10.0)
    w = check_sync_and_ina(rep, dut, log, shunt)
    if w is None:
        rep.add("SKIP", "6-7", "need a good window first")
        return
    check_repeatability(rep, dut, log, k=3 if quick else 5)
    check_activity(rep, dut, log, w.Pmean_uW)


def run_cal(rep: Report, log, r_load: float, shunt: float, n_windows: int, wait_s: float, ask=input) -> None:
    print("== Calibration: Nano UNPLUGGED from the measured cable, sync wire moved by hand ==")
    print("Each window: plug the sync wire into the 3V3 rail for >= 10 s, then pull it out.")
    ask("Step 1/2 - NO load in the VIN- row. Press Enter, then make one window (>= 3 s) ... ")
    log.arm()
    try:
        w0 = log.next_window(wait_s)
        if w0.n == 0:
            judge_window(rep, "cal zero", w0, None, shunt, None)
            return
        msg = f"offset {w0.Pmean_uW:.0f} uW at {w0.Vbus_mV:.0f} mV (n={w0.n})"
        rep.add("PASS" if abs(w0.Pmean_uW) < CAL_ZERO_WARN_UW else "WARN", "cal zero", msg,
                "Large offset with nothing connected: a leak in the VIN- row (stray wire/resistor), or noise.")
    except TimeoutError:
        rep.add("FAIL", "cal zero", f"no W line within {wait_s:.0f} s",
                "Sync wire 3V3 -> SYNC row -> IO4 not working, or logger not running.")
        return
    ask(f"Step 2/2 - put the {r_load:.1f} ohm resistor from the VIN- row to GND. Press Enter, then make "
        f"{n_windows} windows (>= 10 s each) ... ")
    ratios = []
    for i in range(n_windows):
        try:
            w = log.next_window(wait_s)
        except TimeoutError:
            rep.add("FAIL", "cal load", f"window {i + 1}: no W line within {wait_s:.0f} s")
            return
        if not judge_window(rep, f"cal load {i + 1}", w, None, shunt, None):
            return
        p_exp = (w.Vbus_mV / 1e3) ** 2 / r_load * 1e6
        ratios.append(w.Pmean_uW / p_exp)
        print(f"        window {i + 1}: {w.duration_us / 1e6:.1f} s, P {w.Pmean_uW / 1e3:.3f} mW, "
              f"expected V^2/R {p_exp / 1e3:.3f} mW, ratio {ratios[-1]:.4f}")
    g = statistics.fmean(ratios)
    implied = shunt * g
    detail = f"gain {g:.4f} ({g - 1:+.2%}), CV {cv(ratios):.2%}; implied shunt {implied:.4f} ohm " \
             f"(logger built for {shunt:.4f})"
    if abs(g - 1) <= CAL_GAIN_PASS:
        rep.add("PASS", "cal gain", detail)
    else:
        hint = "Use the DMM value of the load resistor in --cal, and rebuild the logger with the measured shunt."
        if 0.07 < g < 0.12:
            hint = "Ratio ~0.09: R100 (0.1 ohm) is still fitted in parallel with the 1 ohm shunt - remove it."
        elif 8 < g < 13:
            hint = "Ratio ~10: logger built for 1 ohm but the shunt is 0.1 ohm (option B) - rebuild with 0.100."
        rep.add("WARN" if abs(g - 1) <= CAL_GAIN_WARN else "FAIL", "cal gain", detail, hint)
    if len(ratios) > 1 and cv(ratios) > CV_PASS:
        rep.add("WARN", "cal repeat", f"window-to-window CV {cv(ratios):.2%}",
                "Loose resistor leg or screw - re-seat and repeat.")


def run_watch(dut, log, ms: int = 1000, max_windows: int | None = None) -> None:
    print("== Wiggle test: one IDLE window per second. Wiggle each joint, screw and cable in turn;")
    print("   a jump in Vbus or P points at the bad one. Ctrl-C to stop. ==")
    print(f"{'#':>4} {'Vbus mV':>8} {'I mA':>7} {'P mW':>8} {'dP':>7}")
    ref, i, ps = None, 0, []
    try:
        while max_windows is None or i < max_windows:
            i += 1
            try:
                w, _ = window_via(dut, log, lambda: dut.idle(ms), ms / 1000 + 3)
            except (TimeoutError, DUTError) as e:
                print(f"{i:>4}  -- window lost ({e}) <- sync or data contact broke")
                continue
            ref = ref or w.Pmean_uW
            d = (w.Pmean_uW - ref) / ref if ref else 0.0
            bar = "#" * min(40, int(abs(d) * 400))
            ps.append(w.Pmean_uW)
            print(f"{i:>4} {w.Vbus_mV:8.0f} {w.Pmean_uW / w.Vbus_mV:7.3f} {w.Pmean_uW / 1e3:8.3f} {d:+7.2%} {bar}")
    except KeyboardInterrupt:
        pass
    if len(ps) > 1:
        print(f"\n{len(ps)} windows: P min {min(ps) / 1e3:.3f} / max {max(ps) / 1e3:.3f} mW, CV {cv(ps):.2%}")


# ---------------------------------------------------------------------------------------------------------------------
# Simulated rig (no hardware): shows what each wiring fault looks like and lets the checks be tested
# ---------------------------------------------------------------------------------------------------------------------
FAULTS = {
    "ok": "everything wired correctly",
    "no_nano": "data wires of the cut cable open or swapped (Nano not enumerated)",
    "flaky_data": "intermittent D+/D- joint (PINGs lost)",
    "swapped": "VIN+ and VIN- swapped",
    "r100": "module R100 still fitted in parallel with the 1 ohm shunt",
    "no_i2c": "INA219 SDA/SCL/VCC not connected",
    "no_sync": "D2 -> IO4 sync wire missing",
    "floating_sync": "100k pull-down missing, SYNC row picks up noise",
    "loose": "loose screw / red-wire joint (power jumps between windows)",
    "low_vbus": "resistive red-wire joint (Vbus sags)",
    "bypass": "Nano also powered around the shunt (5V pin or second cable)",
    "wrong_build": "logger built with a shunt value that differs from the DMM value",
}


class SimRig:
    P_IDLE_UW, P_RUN_UW, V_MV, SHUNT = 55_000.0, 68_000.0, 4_960.0, 1.0

    def __init__(self, fault: str, seed: int = 1):
        self.fault, self.rng, self.mode, self.last_us = fault, random.Random(seed), "IDLE", 0
        self.pending: list[str] = []

    # serial ports as list_ports would report them
    def ports(self):
        p = [("/dev/ttyUSB0", 0x10C4, 0xEA60, "CP2102 USB to UART Bridge Controller")]
        if self.fault != "no_nano":
            p.insert(0, ("/dev/ttyACM0", 0x2341, 0x805A, "Nano 33 BLE"))
        return p

    # -- DUT interface ----------------------------------------------------------------------------------------------
    def ping(self):
        if self.fault == "flaky_data" and self.rng.random() < 0.03:
            raise DUTError("'PING': timeout waiting for a reply line (simulated)")
        return {"bench": "SIM", "n_models": 1}

    def list(self):
        return [ModelEntry(0, "dt-r2-d8-fp32", "emlearn")]

    def sel(self, k):
        return {"k": k, "arena_used": 0}

    def input(self, j):
        return {"j": j}

    def lat(self):
        return {"inf_med_us": 9.0, "fe_med_us": 60.0}

    @staticmethod
    def n_for(window_s, per_call_us):
        return DUT.n_for(window_s, per_call_us)

    def _window(self, us, p_true):
        self.last_us = int(us * self.rng.uniform(0.9999, 1.0001))
        if self.fault == "no_sync":
            return
        v = self.V_MV - (500 if self.fault == "low_vbus" else 0) + self.rng.gauss(0, 2)
        p = p_true * (1 + self.rng.gauss(0, 0.002))
        if self.fault == "loose":
            p *= 1 + self.rng.choice([-1, 1]) * self.rng.uniform(0.03, 0.08)
            v += self.rng.uniform(-60, 60)
        if self.fault == "swapped":
            p = -p
        if self.fault == "r100":
            p *= 0.1 / 1.1
        if self.fault == "wrong_build":
            p *= 1.0 / 1.012
        n = 0 if self.fault == "no_i2c" else int(self.last_us / 1000)
        if self.fault == "no_i2c":
            p, v = 0.0, 0.0
        e = p * self.last_us / 1e6
        self.pending.append(f"W rise_us=1000 fall_us={1000 + self.last_us} n={n} E_uJ={e:.3f} "
                            f"Pmean_uW={p:.3f} Pstd_uW={abs(p) * 0.01:.3f} Vbus_mV={v:.1f} ovf=0")

    def idle(self, ms):
        p = self.P_IDLE_UW
        self._window(ms * 1000, p)
        return {"us": self.last_us}

    def run(self, n):
        p = self.P_IDLE_UW if self.fault == "bypass" else self.P_RUN_UW
        self._window(int(n * 9.0), p)
        return {"n": n, "us": self.last_us, "cls": 3}

    # -- logger interface -------------------------------------------------------------------------------------------
    def reset_board(self):
        self.pending.append("ets Jun  8 2016 00:22:57")
        self.pending.append("# logger SIM shunt_ohm=1.0000 period_us=1000")

    def lines(self, duration_s):
        out = list(self.pending)
        self.pending.clear()
        if self.fault == "floating_sync" and duration_s >= 3:
            out.append("W rise_us=5 fall_us=180000 n=170 E_uJ=9000.0 Pmean_uW=50000.0 Pstd_uW=900.0 "
                       "Vbus_mV=4960.0 ovf=0")
        return out

    def arm(self):
        self.pending.clear()

    def next_window(self, timeout_s):
        for ln in self.lines(0):
            if ln.startswith("W "):
                return parse_w_line(ln)
        raise TimeoutError("no W line from the logger")

    def close(self):
        pass


class SimCal(SimRig):
    """Calibration with the Nano unplugged: each 'manual window' produces load (or zero) power."""

    def __init__(self, fault, r_load, seed=1):
        super().__init__(fault, seed)
        self.r_load, self.step = r_load, 0

    def next_window(self, timeout_s):
        v = self.V_MV
        p = 0.0 if self.step == 0 else (v / 1e3) ** 2 / self.r_load * 1e6
        self.step += 1
        if self.fault == "r100":
            p *= 0.1 / 1.1
        self.last_us = 10_000_000
        p += self.rng.gauss(0, 30)
        return parse_w_line(f"W rise_us=0 fall_us=10000000 n=9500 E_uJ={p * 10:.3f} Pmean_uW={p:.3f} "
                            f"Pstd_uW=50.000 Vbus_mV={v:.1f} ovf=0")


# ---------------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="Nano serial port (default: auto-detect)")
    ap.add_argument("--logger", help="ESP32 logger serial port (default: auto-detect)")
    ap.add_argument("--shunt-ohm", type=float, default=None,
                    help="DMM value of the fitted shunt; compared with the logger build (default: take the logger's)")
    ap.add_argument("--cal", type=float, metavar="R_OHM", help="calibration mode with this load resistor (DMM value)")
    ap.add_argument("--cal-windows", type=int, default=3)
    ap.add_argument("--watch", action="store_true", help="live wiggle test (Ctrl-C to stop)")
    ap.add_argument("--quick", action="store_true", help="shorter listening and fewer repeat windows")
    ap.add_argument("--simulate", metavar="FAULT", help="run against a simulated rig; 'list' shows the faults")
    ap.add_argument("--no-color", action="store_true")
    a = ap.parse_args(argv)
    rep = Report(color=not a.no_color and sys.stdout.isatty())

    if a.simulate:
        if a.simulate == "list" or a.simulate not in FAULTS:
            for k, v in FAULTS.items():
                print(f"  {k:14s} {v}")
            return 0 if a.simulate == "list" else 2
        print(f"(simulated rig, fault = {a.simulate}: {FAULTS[a.simulate]})")
        shunt = a.shunt_ohm or 1.0
        if a.cal:
            sim = SimCal(a.simulate, a.cal)
            run_cal(rep, sim, a.cal, shunt, a.cal_windows, 60, ask=lambda msg: print(msg))
        elif a.watch:
            sim = SimRig(a.simulate)
            run_watch(sim, sim, max_windows=8)
            return 0
        else:
            sim = SimRig(a.simulate)
            dmm = a.shunt_ohm if a.shunt_ohm else (1.012 if a.simulate == "wrong_build" else None)
            run_normal(rep, sim, sim, sim.ports(), shunt, dmm, quick=True)
        rep.summary()
        return 1 if rep.failed else 0

    ports = list_usb_ports()
    nano_port = a.port or next((p[0] for p in ports if p[1] in ARDUINO_VIDS), None)
    log_port = a.logger or next((p[0] for p in ports if p[1] in ESP_BRIDGES), None)
    if log_port is None:
        check_enumeration(rep, ports, want_nano=not a.cal)
        rep.summary()
        return 1
    log = LoggerLink(log_port)
    try:
        if a.cal:
            check_enumeration(rep, ports, want_nano=False)
            check_logger_banner(rep, log, a.shunt_ohm)
            built = next((c for c in rep.checks if c.name == "3 Logger" and "shunt_ohm=" in c.detail), None)
            shunt = a.shunt_ohm or (float(re.search(r"shunt_ohm=([0-9.]+)", built.detail).group(1)) if built else 1.0)
            run_cal(rep, log, a.cal, shunt, a.cal_windows, 120)
        else:
            dut = None
            if nano_port:
                try:
                    dut = DUT(nano_port)
                except Exception as e:  # noqa: BLE001
                    rep.add("FAIL", "2 Nano link", f"cannot open {nano_port}: {e}",
                            "Port busy (Arduino IDE serial monitor open?) or no permission (dialout group).")
            if a.watch:
                if dut is None:
                    print("--watch needs the Nano connected")
                    return 1
                run_watch(dut, log)
                return 0
            run_normal(rep, dut, log, ports, a.shunt_ohm or 1.0, a.shunt_ohm, a.quick)
            if dut:
                dut.close()
    finally:
        log.close()
    rep.summary()
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
