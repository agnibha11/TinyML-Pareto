#!/usr/bin/env python3
"""dut.py - host wrapper for the bench firmware (protocol: CONTRACT.md section 7).

Hridayesh's measure.py imports this; nobody else writes to the board's serial port.

    from tools.dut import DUT
    with DUT("/dev/ttyACM0") as d:
        d.ping()                  # {'bench': 'a1b2c3d', 'n_models': 4}
        d.list()                  # [ModelEntry(k=0, model_id='dt-r2-d8-fp32', runtime='emlearn'), ...]
        d.sel(0)                  # {'k': 0, 'arena_used': 0}
        d.input(2)
        lat = d.lat()             # {'inf_med_cyc': ..., 'inf_med_us': ..., ...}
        d.run(d.n_for(10.0, lat["inf_med_us"]))   # {'n': ..., 'us': ..., 'cls': ...}

Command line (manual bring-up):
    python tools/dut.py /dev/ttyACM0 PING
    python tools/dut.py /dev/ttyACM0 "SEL 0" LAT "RUN 1000"

Parsing is strict. Any ERR reply, a timeout, a malformed line, an unexpected extra line, or an echoed k/j/n that
differs from what was sent raises DUTError; nothing is guessed. After a timeout the link is resynchronised
(CONTRACT.md 7.1) before the error is raised, so the next command never receives a stale reply.
"""
from __future__ import annotations

import math
import re
import sys
import time
from dataclasses import dataclass

import numpy as np

try:
    import serial  # pyserial
except ImportError:  # pragma: no cover - only hit when pyserial is missing
    serial = None

CPU_HZ = 64_000_000
BAUD = 115_200
# Host timeouts (CONTRACT.md 7.2)
CMD_TIMEOUT_S = 2.0          # PING, LIST, SEL, INPUT
LAT_TIMEOUT_S = 600.0        # LAT: 5 x 101 x (inference + front end) can reach minutes for large CNNs
VERIFY_TIMEOUT_S = 10.0      # VERIFY, host side (the board waits 2 s for the payload after RDY)
WINDOW_MARGIN_S = 5.0        # windowed commands: 1.5 x expected length + 5 s
MAX_WINDOW_S = 600.0         # longest window the firmware accepts; used when latency is not known yet
RESYNC_SILENCE_S = 0.2
VERIFY_BYTES = 1024 * 4
_KV = re.compile(r"^[a-z_]+=[^\s=]+$")


class DUTError(RuntimeError):
    """The board replied ERR, sent something malformed, or did not answer in time."""


@dataclass(frozen=True)
class ModelEntry:
    k: int
    model_id: str
    runtime: str


def _parse_kv(tokens: list[str], line: str) -> dict[str, str]:
    out = {}
    for t in tokens:
        if not _KV.match(t):
            raise DUTError(f"malformed key=value token {t!r} in {line!r}")
        k, v = t.split("=", 1)
        out[k] = v
    return out


def _ints(d: dict[str, str], *keys: str, line: str) -> dict[str, int]:
    try:
        return {k: int(d[k]) for k in keys}
    except (KeyError, ValueError) as e:
        raise DUTError(f"missing or non-integer field {e} in {line!r}") from None


class DUT:
    def __init__(self, port: str, baud: int = BAUD, open_timeout_s: float = 5.0):
        if serial is None:
            raise ImportError("pyserial is required: pip install pyserial")
        self.port = port
        self._ser = serial.serial_for_url(port, baudrate=baud, timeout=0.05)
        self.lat_us: float | None = None  # inference latency from the last lat(), used for timeouts and n_for()
        self.fe_us: float | None = None
        self._drain(open_timeout_s)

    # -- context manager ------------------------------------------------------------------------------------------
    def __enter__(self) -> "DUT":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._ser.close()

    # -- low level ------------------------------------------------------------------------------------------------
    def _drain(self, settle_s: float) -> None:
        """Discard anything pending (the firmware never prints unprompted, so this should be empty)."""
        time.sleep(min(settle_s, 0.2))
        self._ser.reset_input_buffer()

    def _readline(self, deadline: float) -> str:
        buf = bytearray()
        while time.monotonic() < deadline:
            b = self._ser.read(1)
            if not b:
                continue
            if b == b"\n":
                return buf.decode("ascii", errors="replace").rstrip("\r")
            buf += b
        raise DUTError(f"timeout waiting for a reply line on {self.port} (partial: {bytes(buf)!r})")

    def resync(self, max_wait_s: float = MAX_WINDOW_S + WINDOW_MARGIN_S) -> None:
        """CONTRACT.md 7.1: after a host timeout, wait for 200 ms of silence, then PING and discard every line
        until 'OK bench=' arrives. A board still busy with a long window answers the PING when it is done."""
        deadline = time.monotonic() + max_wait_s
        quiet_since = time.monotonic()
        while time.monotonic() - quiet_since < RESYNC_SILENCE_S and time.monotonic() < deadline:
            if self._ser.read(256):
                quiet_since = time.monotonic()
        self._ser.reset_input_buffer()
        self._ser.write(b"PING\n")
        self._ser.flush()
        while True:
            line = self._readline(deadline)   # raises DUTError if the board never answers
            if line.startswith("OK bench="):
                return

    def command(self, cmd: str, timeout_s: float = CMD_TIMEOUT_S, payload: bytes | None = None,
                allow_extra: bool = False) -> tuple[dict[str, str], list[str]]:
        """Send one command; return (final OK fields, preceding lines). Raise DUTError on ERR/timeout.

        Only LIST may send lines before its final line (allow_extra=True); for every other command an extra line
        means the board printed something unasked, which breaks CONTRACT.md 7.1.
        """
        if "\n" in cmd or len(cmd) > 63:
            raise ValueError(f"invalid command {cmd!r}")
        self._ser.reset_input_buffer()  # a stale line from an earlier failure must never answer this command
        self._ser.write((cmd + "\n").encode("ascii"))
        self._ser.flush()
        deadline = time.monotonic() + timeout_s
        extra: list[str] = []
        while True:
            try:
                line = self._readline(deadline)
            except DUTError as e:
                try:
                    self.resync()
                except DUTError:
                    raise DUTError(f"{cmd!r}: {e}; resync also failed - power-cycle the board") from None
                raise DUTError(f"{cmd!r}: {e} (link resynchronised)") from None
            if line.startswith("RDY ") and payload is not None:
                parts = line.split()
                if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) != len(payload):
                    raise DUTError(f"bad handshake {line!r} for a {len(payload)}-byte payload")
                self._ser.write(payload)
                self._ser.flush()
                payload = None
                continue
            if line.startswith("ERR"):
                raise DUTError(f"{cmd!r} -> {line}")
            if line == "OK" or line.startswith("OK "):
                return _parse_kv(line.split()[1:], line), extra
            if not allow_extra:
                raise DUTError(f"{cmd!r}: unexpected line {line!r} before the final OK/ERR")
            extra.append(line)

    # -- commands (CONTRACT.md 7.2) -------------------------------------------------------------------------------
    def ping(self) -> dict:
        kv, _ = self.command("PING")
        if "bench" not in kv:
            raise DUTError(f"PING reply without bench=: {kv}")
        return {"bench": kv["bench"], **_ints(kv, "n_models", line="PING")}

    def list(self) -> list[ModelEntry]:
        kv, lines = self.command("LIST", allow_extra=True)
        n = _ints(kv, "n", line="LIST")["n"]
        models = []
        for ln in lines:
            parts = ln.split()
            if len(parts) != 4 or parts[0] != "M" or not parts[1].isdigit():
                raise DUTError(f"malformed LIST line {ln!r}")
            models.append(ModelEntry(int(parts[1]), parts[2], parts[3]))
        if len(models) != n or [m.k for m in models] != list(range(n)):
            raise DUTError(f"LIST returned {len(models)} lines for n={n}")
        return models

    def sel(self, k: int) -> dict:
        kv, _ = self.command(f"SEL {int(k)}")
        out = _ints(kv, "k", "arena_used", line="SEL")
        if out["k"] != k:
            raise DUTError(f"SEL {k} answered for k={out['k']}")
        self.lat_us = self.fe_us = None  # latency belongs to the previous model
        return out

    def input(self, j: int) -> dict:
        kv, _ = self.command(f"INPUT {int(j)}")
        out = _ints(kv, "j", line="INPUT")
        if out["j"] != j:
            raise DUTError(f"INPUT {j} answered for j={out['j']}")
        return out

    def idle(self, ms: int) -> dict:
        kv, _ = self.command(f"IDLE {int(ms)}", timeout_s=1.5 * ms / 1000 + WINDOW_MARGIN_S)
        return _ints(kv, "us", line="IDLE")

    def sleep(self, ms: int) -> dict:
        kv, _ = self.command(f"SLEEP {int(ms)}", timeout_s=1.5 * ms / 1000 + WINDOW_MARGIN_S)
        return _ints(kv, "us", line="SLEEP")

    def _window_timeout(self, n: int, per_call_us: float | None) -> float:
        if per_call_us is None:
            return MAX_WINDOW_S + WINDOW_MARGIN_S  # latency unknown (no LAT yet)
        return n * per_call_us / 1e6 * 1.5 + WINDOW_MARGIN_S

    def _windowed(self, cmd: str, n: int, per_call_us: float | None, keys: tuple[str, ...]) -> dict:
        kv, _ = self.command(f"{cmd} {int(n)}", timeout_s=self._window_timeout(n, per_call_us))
        out = _ints(kv, *keys, line=cmd)
        if out["n"] != n:
            raise DUTError(f"{cmd} {n} answered with n={out['n']}")
        return out

    def run(self, n: int) -> dict:
        return self._windowed("RUN", n, self.lat_us, ("n", "us", "cls"))

    def front(self, n: int) -> dict:
        return self._windowed("FRONT", n, self.fe_us, ("n", "us"))

    def e2e(self, n: int) -> dict:
        per = None if self.lat_us is None or self.fe_us is None else self.lat_us + self.fe_us
        return self._windowed("E2E", n, per, ("n", "us", "cls"))

    def lat(self) -> dict:
        kv, _ = self.command("LAT", timeout_s=LAT_TIMEOUT_S)
        out = _ints(kv, "inf_med_cyc", "inf_min_cyc", "inf_max_cyc", "fe_med_cyc", "fe_min_cyc", "fe_max_cyc",
                    line="LAT")
        for k in list(out):
            out[k.replace("_cyc", "_us")] = out[k] * 1e6 / CPU_HZ
        self.lat_us, self.fe_us = out["inf_med_us"], out["fe_med_us"]
        return out

    def verify(self, window: np.ndarray) -> tuple[int, np.ndarray]:
        w = np.ascontiguousarray(window, dtype="<f4").ravel()
        if w.size != 1024:
            raise ValueError(f"window must have 1024 samples, got {w.size}")
        kv, _ = self.command("VERIFY", timeout_s=VERIFY_TIMEOUT_S, payload=w.tobytes())
        try:
            cls = int(kv["cls"])
            out = np.array([float(v) for v in kv["out"].split(",")], dtype=np.float32)
        except (KeyError, ValueError) as e:
            raise DUTError(f"malformed VERIFY reply {kv}: {e}") from None
        return cls, out

    # -- helpers --------------------------------------------------------------------------------------------------
    @staticmethod
    def n_for(window_s: float, per_call_us: float) -> int:
        """Iterations so the measured window lasts at least window_s (MLPerf Tiny practice: >= 10 s)."""
        if per_call_us <= 0:
            raise ValueError("latency must be positive")
        return max(1, math.ceil(window_s * 1e6 / per_call_us))


def _cli(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    with DUT(argv[1]) as d:
        for cmd in argv[2:]:
            try:
                kv, lines = d.command(cmd, timeout_s=MAX_WINDOW_S + WINDOW_MARGIN_S, allow_extra=True)
                for ln in lines:
                    print(ln)
                print("OK", " ".join(f"{k}={v}" for k, v in kv.items()))
            except DUTError as e:
                print(e, file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
