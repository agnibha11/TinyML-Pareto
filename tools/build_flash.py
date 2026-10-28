#!/usr/bin/env python3
"""Compile (and, with a board, upload) the bench firmware for one bundle or all of them (CONTRACT.md 8, from W4).

    python tools/build_flash.py --all --compile-only          # link-check every bundle -> results/fw_build.csv
    python tools/build_flash.py 2 --port /dev/ttyACM0          # compile + upload bundle 2

Flags passed to arduino-cli (firmware/VERSIONS.md "Build stamping"): -DBENCH_GIT_HASH, -DBUNDLE_ID, -DARENA_BYTES and
-DBUNDLE_TFLM for bundles with networks. A bundle is feasible (registry `feasible`, CONTRACT.md 6.1) when it links;
the static arena is part of .bss, so a link also proves the arena fits in RAM (the board's SEL still has the last
word, because the TFLM version on the board may allocate slightly differently from the virtual MCU's).

results/fw_build.csv (compile-time facts; fw_metrics.csv with board timings stays Abhinav's):
    bundle, model_id, runtime, links, bundle_flash_B, bundle_ram_static_B, arena_B, flash_model_B_board, git_hash
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.contract import c_name  # noqa: E402

FQBN = "arduino:mbed_nano:nano33ble"
SKETCH = REPO / "firmware" / "bench"


def git_hash() -> str:
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() or "nogit"


def compile_bundle(cli: str, b: dict, gh: str, upload_port: str | None) -> dict:
    # arduino-cli splits the recipe on spaces and strips quotes: single quotes keep the inner "" of the string macro
    flags = f'-DBUNDLE_ID={b["id"]} -DARENA_BYTES={max(b["arena_bytes"], 16)} \'-DBENCH_GIT_HASH="{gh}"\''
    if b["tflm"]:
        flags += " -DBUNDLE_TFLM=1"
    bp = REPO / "build" / "fw" / f"b{b['id']}"
    cmd = [cli, "compile", "--fqbn", FQBN, "--build-path", str(bp),
           "--build-property", f"compiler.cpp.extra_flags={flags}", "--build-property", f"compiler.c.extra_flags={flags}"]
    if upload_port:
        cmd += ["--upload", "--port", upload_port]
    r = subprocess.run([*cmd, str(SKETCH)], capture_output=True, text=True)
    out = r.stdout + r.stderr
    m1 = re.search(r"Sketch uses (\d+) bytes", out)
    m2 = re.search(r"Global variables use (\d+) bytes", out)
    res = {"links": int(r.returncode == 0 and m1 is not None), "bundle_flash_B": int(m1.group(1)) if m1 else None,
           "bundle_ram_static_B": int(m2.group(1)) if m2 else None, "log": out[-2000:]}
    elf = bp / "bench.ino.elf"
    res["sym"] = {}
    if res["links"] and elf.exists():
        nm = shutil.which("arm-none-eabi-nm") or "arm-none-eabi-nm"
        txt = subprocess.run([nm, "-S", "-C", "--defined-only", str(elf)], capture_output=True, text=True).stdout
        for ln in txt.splitlines():
            p = ln.split(maxsplit=3)
            if len(p) == 4:
                res["sym"][p[3]] = int(p[1], 16)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle", nargs="?", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--compile-only", action="store_true")
    ap.add_argument("--port")
    ap.add_argument("--arduino-cli", default=shutil.which("arduino-cli") or "arduino-cli")
    a = ap.parse_args()
    plan = json.loads((SKETCH / "models" / "bundles.json").read_text())
    todo = plan if a.all else [b for b in plan if b["id"] == a.bundle]
    if not todo:
        sys.exit("give a bundle id or --all (run tools/bundle.py first)")
    gh = git_hash()
    rows = []
    for b in todo:
        res = compile_bundle(a.arduino_cli, b, gh, None if a.compile_only else a.port)
        print(f"bundle {b['id']}: links={res['links']} flash={res['bundle_flash_B']} ram_static={res['bundle_ram_static_B']}"
              f" ({len(b['models'])} models)")
        if not res["links"]:
            print(res["log"])
        for m in b["models"]:
            cn = c_name(m)
            fm = res["sym"].get(f"g_model_{cn}") if b["tflm"] else \
                sum(v for k, v in res["sym"].items() if k.startswith(cn + "_")) or None
            rows.append({"bundle": b["id"], "model_id": m, "runtime": b["runtime"], "links": res["links"],
                         "bundle_flash_B": res["bundle_flash_B"], "bundle_ram_static_B": res["bundle_ram_static_B"],
                         "arena_B": b["arena_bytes"], "flash_model_B_board": fm, "git_hash": gh})
    out = REPO / "results" / "fw_build.csv"
    new = pd.DataFrame(rows)
    if out.exists() and not a.all:
        old = pd.read_csv(out)
        new = pd.concat([old[~old.bundle.isin(new.bundle)], new], ignore_index=True)
    new.sort_values(["bundle", "model_id"]).to_csv(out, index=False)


if __name__ == "__main__":
    main()
