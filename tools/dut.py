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
        d.run(d.n_for(10.0))      # {'n': ..., 'us': ..., 'cls': ...}

Command line (manual bring-up):
    python tools/dut.py /dev/ttyACM0 PING
    python tools/dut.py /dev/ttyACM0 "SEL 0" LAT "RUN 1000"

Parsing is strict. Any ERR reply, a timeout or a malformed line raises DUTError; nothing is guessed.
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
CMD_TIMEOUT_S = 2.0      # commands without a measured window
WINDOW_MARGIN_S = 5.0    # windowed commands: expected length + this
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

    def command(self, cmd: str, timeout_s: float = CMD_TIMEOUT_S, payload: bytes | None = None
                ) -> tuple[dict[str, str], list[str]]:
        """Send one command; return (final OK fields, preceding lines). Raise DUTError on ERR/timeout."""
        if "\n" in cmd or len(cmd) > 63:
            raise ValueError(f"invalid command {cmd!r}")
        self._ser.reset_input_buffer()  # a stale line from an earlier failure must never answer this command
        self._ser.write((cmd + "\n").encode("ascii"))
        self._ser.flush()
        deadline = time.monotonic() + timeout_s
        extra: list[str] = []
        while True:
            line = self._readline(deadline)
            if line.startswith("RDY ") and payload is not None:
                want = int(line.split()[1])
                if want != len(payload):
                    raise DUTError(f"board wants {want} bytes, payload has {len(payload)}")
                self._ser.write(payload)
                self._ser.flush()
                payload = None
                continue
            if line.startswith("ERR"):
                raise DUTError(f"{cmd!r} -> {line}")
            if line == "OK" or line.startswith("OK "):
                return _parse_kv(line.split()[1:], line), extra
            extra.append(line)

    # -- commands (CONTRACT.md 7.2) -------------------------------------------------------------------------------
    def ping(self) -> dict:
        kv, _ = self.command("PING")
        if "bench" not in kv:
            raise DUTError(f"PING reply without bench=: {kv}")
        return {"bench": kv["bench"], **_ints(kv, "n_models", line="PING")}

    def list(self) -> list[ModelEntry]:
        kv, lines = self.command("LIST")
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
        return _ints(kv, "j", line="INPUT")

    def idle(self, ms: int) -> dict:
        kv, _ = self.command(f"IDLE {int(ms)}", timeout_s=ms / 1000 + WINDOW_MARGIN_S)
        return _ints(kv, "us", line="IDLE")

    def sleep(self, ms: int) -> dict:
        kv, _ = self.command(f"SLEEP {int(ms)}", timeout_s=ms / 1000 + WINDOW_MARGIN_S)
        return _ints(kv, "us", line="SLEEP")

    def _window_timeout(self, n: int, per_call_us: float | None) -> float:
        if per_call_us is None:
            return 120.0 + WINDOW_MARGIN_S  # unknown latency: generous but finite
        return n * per_call_us / 1e6 * 1.5 + WINDOW_MARGIN_S

    def run(self, n: int) -> dict:
        kv, _ = self.command(f"RUN {int(n)}", timeout_s=self._window_timeout(n, self.lat_us))
        return _ints(kv, "n", "us", "cls", line="RUN")

    def front(self, n: int) -> dict:
        kv, _ = self.command(f"FRONT {int(n)}", timeout_s=self._window_timeout(n, self.fe_us))
        return _ints(kv, "n", "us", line="FRONT")

    def e2e(self, n: int) -> dict:
        per = None if self.lat_us is None or self.fe_us is None else self.lat_us + self.fe_us
        kv, _ = self.command(f"E2E {int(n)}", timeout_s=self._window_timeout(n, per))
        return _ints(kv, "n", "us", "cls", line="E2E")

    def lat(self) -> dict:
        kv, _ = self.command("LAT", timeout_s=60.0)
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
        kv, _ = self.command("VERIFY", timeout_s=10.0, payload=w.tobytes())
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
                kv, lines = d.command(cmd, timeout_s=120.0)
                for ln in lines:
                    print(ln)
                print("OK", " ".join(f"{k}={v}" for k, v in kv.items()))
            except DUTError as e:
                print(e, file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
