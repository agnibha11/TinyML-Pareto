"""Run one experiment window and analyse it for the ~42 s idle-current dip (docs/energy_rig_bringup.md).

Needs: ESP32 running firmware/experiments/inastream (10 Hz INA219 stream on /dev/ttyUSB0) instead of logger/, and
the Nano running firmware/experiments/nano_dipx (or firmware/bench with --bench, which sends "IDLE <ms>" /
"SLEEP <ms>"). Reflash logger/ and firmware/bench afterwards. Writes results/rig/wfi_dip/<time>_<label>.csv.

    python tools/run_dip.py --label usb_off --ms 130000 --flags 1
    python tools/run_dip.py --label info --info
"""
from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import numpy as np
import serial

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "results" / "rig" / "wfi_dip"


def analyse(csv: Path) -> str:
    d = np.genfromtxt(csv, delimiter=",", names=True)
    m = d["sync_pct"] >= 100
    if m.sum() < 100:
        return f"only {m.sum()} in-window samples - window not seen (sync?)"
    t = (d["esp_ms"][m] - d["esp_ms"][m][0]) / 1000
    i = d["I_uA"][m]
    med = np.median(i)
    sm = np.convolve(i, np.ones(10) / 10, "valid")
    x = i - i.mean()
    ac = np.correlate(x, x, "full")[len(x) - 1:]
    ac /= ac[0]
    lags = np.arange(len(ac)) * 0.1
    sel = (lags > 20) & (lags < 80)
    j = np.argmax(ac[sel])
    lines = [f"{t[-1]:.1f} s in window, median {med / 1000:.3f} mA, 1-s avg min {(sm.min() - med) / med:+.2%} "
             f"max {(sm.max() - med) / med:+.2%}, std(1-s) {sm.std() / med:.2%}, "
             f"autocorr peak {lags[sel][j]:.1f} s r={ac[sel][j]:.2f}, "
             f"Vbus {d['Vbus_mV'][m].min():.0f}-{d['Vbus_mV'][m].max():.0f} mV"]
    for k in range(0, int(t[-1]) - 4, 5):
        s = (t >= k) & (t < k + 5)
        v = (i[s].mean() - med) / med
        lines.append(f"  {k:4d}-{k + 5:<4d}s {v:+6.2%} " + "#" * int(max(0.0, -v) * 1000))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--ms", type=int, default=130000)
    ap.add_argument("--flags", type=int, default=0)
    ap.add_argument("--bench", choices=["IDLE", "SLEEP"], help="firmware/bench is on the Nano: send this command")
    ap.add_argument("--close-port", action="store_true", help="close the Nano port right after sending")
    ap.add_argument("--info", action="store_true", help="only print INFO from nano_dipx")
    ap.add_argument("--nano", default="/dev/ttyACM0")
    ap.add_argument("--esp", default="/dev/ttyUSB0")
    a = ap.parse_args()

    if a.info:
        n = serial.Serial(a.nano, 115200, timeout=2)
        time.sleep(0.3)
        n.reset_input_buffer()
        n.write(b"INFO\n")
        print(n.readline().decode().strip())
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{time.strftime('%m%d_%H%M%S')}_{a.label}.csv"
    out = open(out_path, "w")
    out.write("esp_ms,I_uA,Vbus_mV,sync_pct\n")
    esp = serial.Serial()
    esp.port, esp.baudrate, esp.timeout = a.esp, 115200, 0.05
    esp.dtr = esp.rts = False
    esp.open()
    esp.reset_input_buffer()
    state = {"stop": False, "n": 0, "err": None}

    def reader() -> None:
        buf = b""
        try:
            while not state["stop"]:
                buf += esp.read(4096)
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    p = line.decode(errors="replace").split()
                    if len(p) == 5 and p[0] == "S":
                        out.write(",".join(p[1:]) + "\n")
                        state["n"] += 1
        except Exception as e:  # noqa: BLE001 - report whatever killed the reader
            state["err"] = e

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    time.sleep(3)
    if state["n"] < 20:
        raise SystemExit(f"ESP32 not streaming ({state['n']} lines, {state['err']}) - is inastream flashed?")

    cmd = f"{a.bench} {a.ms}\n" if a.bench else f"WIN {a.ms} {a.flags}\n"
    nano = serial.Serial(a.nano, 115200, timeout=a.ms / 1000 + 10)
    time.sleep(0.5)
    nano.reset_input_buffer()
    nano.write(cmd.encode())
    nano.flush()
    no_reply = a.close_port or (not a.bench and a.flags & 1)
    print(f"[{a.label}] sent {cmd.strip()!r}", flush=True)
    if no_reply:
        time.sleep(0.3)
        nano.close()
        time.sleep(a.ms / 1000 + 6)
        reply = "(no reply expected)"
    else:
        reply = nano.readline().decode().strip()
        nano.close()
        time.sleep(2)
    state["stop"] = True
    th.join()
    out.close()
    print(f"[{a.label}] Nano: {reply}; {state['n']} stream lines; reader error: {state['err']}")
    print(f"[{a.label}] {out_path.relative_to(REPO)}")
    print(analyse(out_path))


if __name__ == "__main__":
    main()
