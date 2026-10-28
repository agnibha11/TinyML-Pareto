#!/usr/bin/env python3
"""Static Cortex-M4 cycle model: classify every instruction of an ELF and write the cost table vmcount.so reads.

Costs follow the Cortex-M4 Technical Reference Manual (r0p1), "Processor instruction timings" and the FPU
instruction-timing table, with these simplifications (the model is calibrated later against the board's DWT):
    ALU, MOV, CMP, MUL, MLA, SMULL/SMLAL, DSP SIMD (SMLAD, SXTB16 ...)   1
    LDR*/STR* (single)                                                    2
    LDRD/STRD                                                             3
    LDM/STM/PUSH/POP                                                      1 + N   (+2 if POP/LDM loads PC)
    B (unconditional), BL, BX, BLX                                        1 + P, P = 2
    B<cond>                                                               2       (mean of not-taken 1, taken 3)
    CBZ/CBNZ                                                              2
    TBB/TBH                                                               4
    SDIV/UDIV                                                             7       (2..12, data dependent)
    VADD/VSUB/VMUL/VABS/VNEG/VCMP/VCVT/VMOV/VMRS                         1
    VMLA/VMLS/VNMLA/VNMLS/VFMA/VFMS/VFNMA/VFNMS                           3
    VDIV, VSQRT                                                           14
    VLDR/VSTR                                                             2
    VLDM/VSTM/VPUSH/VPOP                                                  1 + N
No flash wait states and no bus contention are modelled.

Output line format: "<pc hex> <cycles> <is_load> <is_store> <is_branch> <is_fp>"

Usage: python tools/vmcu/cyclemodel.py <elf> <out.txt>
"""
from __future__ import annotations

import re
import subprocess
import sys

COND = ("eq", "ne", "cs", "hs", "cc", "lo", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt", "gt", "le")
LINE = re.compile(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{4}\s?){1,2})\s+([a-z][\w.]*)\s*(.*)$")


def reg_count(ops: str) -> int:
    m = re.search(r"\{([^}]*)\}", ops)
    if not m:
        return 1
    n = 0
    for part in m.group(1).split(","):
        part = part.strip()
        r = re.match(r"([a-z]+)(\d+)-[a-z]+(\d+)", part)
        n += (int(r.group(3)) - int(r.group(2)) + 1) if r else 1
    return max(n, 1)


def strip_cond(m: str) -> str:
    """'addeq' -> 'add', 'ldrbne.w' -> 'ldrb' (only when the suffix is a condition code)."""
    m = m.split(".")[0]
    for c in COND:
        if m.endswith(c) and len(m) > len(c) and m not in ("teq", "vcmpe", "bls", "bhi"):
            base = m[: -len(c)]
            if base in ("b", "bx", "bl", "blx") or len(base) >= 2:
                return base
    return m


def cost(mn: str, ops: str) -> tuple[int, int, int, int, int]:
    """(cycles, load, store, branch, fp) for one instruction."""
    full = mn.split(".")[0]
    if full[0] == "b" and full[1:] in COND:
        return 2, 0, 0, 1, 0                              # conditional branch
    m = strip_cond(mn)
    loads_pc = "pc" in ops.split(",")[0] or ("{" in ops and "pc" in ops)
    if m.startswith("v"):
        if m in ("vldr",):
            return 2, 1, 0, 0, 1
        if m in ("vstr",):
            return 2, 0, 1, 0, 1
        if m.startswith(("vldm", "vpop")):
            return 1 + reg_count(ops), 1, 0, 0, 1
        if m.startswith(("vstm", "vpush")):
            return 1 + reg_count(ops), 0, 1, 0, 1
        if m.startswith(("vdiv", "vsqrt")):
            return 14, 0, 0, 0, 1
        if m.startswith(("vmla", "vmls", "vnmla", "vnmls", "vfma", "vfms", "vfnma", "vfnms")):
            return 3, 0, 0, 0, 1
        return 1, 0, 0, 0, 1
    if m in ("ldrd",):
        return 3, 1, 0, 0, 0
    if m in ("strd",):
        return 3, 0, 1, 0, 0
    if m.startswith(("ldm", "pop")):
        pc = "pc" in ops
        return 1 + reg_count(ops) + (2 if pc else 0), 1, 0, int(pc), 0
    if m.startswith(("stm", "push")):
        return 1 + reg_count(ops), 0, 1, 0, 0
    if m.startswith("ldr"):
        if ops.split(",")[0].strip() == "pc":
            return 4, 1, 0, 1, 0
        return 2, 1, 0, 0, 0
    if m.startswith("str"):
        return 2, 0, 1, 0, 0
    if m in ("sdiv", "udiv"):
        return 7, 0, 0, 0, 0
    if m in ("b", "bl", "bx", "blx"):
        return 3, 0, 0, 1, 0
    if m in ("cbz", "cbnz"):
        return 2, 0, 0, 1, 0
    if m in ("tbb", "tbh"):
        return 4, 0, 0, 1, 0
    if m in ("mov", "add") and ops.split(",")[0].strip() == "pc":
        return 3, 0, 0, 1, 0
    return 1, 0, 0, 0, 0


def table(elf: str) -> list[tuple[int, tuple]]:
    out = subprocess.run(["arm-none-eabi-objdump", "-d", "--no-show-raw-insn", elf], capture_output=True,
                         text=True, check=True).stdout
    rows = []
    line_re = re.compile(r"^\s*([0-9a-f]+):\s+([a-z][\w.]*)\s*(.*)$")
    for ln in out.splitlines():
        m = line_re.match(ln)
        if not m:
            continue
        mn, ops = m.group(2), m.group(3).split(";")[0].split("@")[0]
        if mn.startswith(".") or mn in ("udf",):
            continue
        rows.append((int(m.group(1), 16), cost(mn, ops)))
    return rows


def main() -> None:
    elf, dst = sys.argv[1], sys.argv[2]
    rows = table(elf)
    with open(dst, "w") as f:
        for pc, c in rows:
            f.write(f"{pc:x} {' '.join(map(str, c))}\n")
    print(f"{len(rows)} instructions classified -> {dst}")


if __name__ == "__main__":
    main()
