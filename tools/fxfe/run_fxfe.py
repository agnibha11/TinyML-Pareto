#!/usr/bin/env python3
"""Fixed-point front end on the virtual Cortex-M4: features of every window and cycle counts, float vs q15 vs q31.

Two images, both built from firmware/bench/frontend.c (float) and firmware/bench/frontend_fx.c (fixed point) with the
flags of tools/vmcu (arm-none-eabi-gcc -Os, Cortex-M4F, softfp):

  fxfeat.elf   reads every window as int16 over semihosting and writes the features of both front ends. QEMU only,
               no plugin. Output: data/processed/feat_fx.npy (n_windows x 215, column map in COLS below)
  fxbench.elf  ten windows (one per class), each front end between the plugin's markers, built for three core
               classes: m4f (Cortex-M4F, the Nano 33 BLE), m4nofp (Cortex-M4 without FPU, standing in for M3/M33-
               class parts: DSP instructions, software float) and m0p (Cortex-M0+: neither). Output:
               results/fixedpoint/fx_cycles.csv (one row per core, region and window) and fx_cost_summary.csv
               The cycle model is the Cortex-M4 one for every build, so m0p counts are exact instructions with
               M4 timing, an approximation (the M0+ has a 2-stage pipeline and 2-cycle loads).

The int16 input is what a +/-8 g, 16-bit vibration accelerometer delivers (FX_LSB_PER_G = 4096 LSB/g). The largest
|x| in the dataset is 6.8 g, so nothing saturates.

Usage: python tools/fxfe/run_fxfe.py --deps ~/vmcu-deps --plugin ~/vmcu-deps/libvmcount.so [--skip-features]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.vmcu.build_libs import COMMON, OPT, C as C_STD, archive  # noqa: E402

FX = REPO / "tools" / "fxfe"
FW = REPO / "tools" / "vmcu" / "fw"
BENCH = REPO / "firmware" / "bench"
BUILD = REPO / "build" / "fxfe"
OUT = REPO / "results" / "fixedpoint"
PROC = REPO / "data" / "processed"
LSB_PER_G = 4096.0
F_CPU = 64e6
P_ACTIVE_MW = 9.984          # chip-level active power of the energy model (ml/stats.py, headline_numbers.json)

# column map of feat_fx.npy
COLS = {"f32_r1": (0, 10), "f32_r2": (10, 34), "f32_r4": (34, 75), "fx_r1": (75, 85), "q15_r2": (85, 109),
        "q31_r2": (109, 133), "q15_r4": (133, 174), "q31_r4": (174, 215), "hyb_r2": (215, 239),
        "hyb_r4": (239, 280)}
N_OUT = 280
import os
UNROLL = ["-DARM_MATH_LOOPUNROLL"] if os.environ.get("FX_UNROLL") == "1" else []
CORES = {   # name: (arch flags, CMSIS-DSP defines)
    "m4f": (["-mcpu=cortex-m4", "-mthumb", "-mfpu=fpv4-sp-d16", "-mfloat-abi=softfp"],
            ["-DARM_MATH_CM4", "-D__FPU_PRESENT=1"] + UNROLL),
    "m4nofp": (["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft"], ["-DARM_MATH_CM4"] + UNROLL),
    "m0p": (["-mcpu=cortex-m0plus", "-mthumb", "-mfloat-abi=soft"], ["-DARM_MATH_CM0PLUS"] + UNROLL),
}
REGIONS = ["CONV", "F32_R1", "F32_R2", "F32_R4", "FX_R1", "FX_R2_Q15", "FX_R2_Q31", "FX_R4_Q15", "FX_R4_Q31",
           "HYB_R2", "HYB_R4", "GATE_MAHAL"]


def to_int16(W: np.ndarray) -> np.ndarray:
    """g -> int16 exactly as fx_from_float: round half away from zero, saturate."""
    v = np.asarray(W, dtype=np.float64) * LSB_PER_G
    v = np.where(v >= 0, np.floor(v + 0.5), np.ceil(v - 0.5))
    return np.clip(v, -32768, 32767).astype(np.int16)


def dsp_sources(dsp: Path) -> list[Path]:
    s = dsp / "Source"
    keep = []
    for sub in ("TransformFunctions", "BasicMathFunctions", "StatisticsFunctions", "ComplexMathFunctions",
                "FastMathFunctions"):
        for p in sorted((s / sub).glob("arm_*.c")):
            n = p.name
            if n.endswith(("_q15.c", "_q31.c")) and not any(x in n for x in ("dct4", "mfcc", "_mve", "_neon")):
                keep.append(p)
    keep += [s / "TransformFunctions" / n for n in
             ("arm_rfft_fast_f32.c", "arm_rfft_fast_init_f32.c", "arm_cfft_f32.c", "arm_cfft_init_f32.c",
              "arm_cfft_radix8_f32.c", "arm_bitreversal.c", "arm_bitreversal2.c")]
    keep += [s / "CommonTables" / "arm_common_tables.c", s / "CommonTables" / "arm_const_structs.c",
             s / "ComplexMathFunctions" / "arm_cmplx_mag_f32.c"]
    return keep


def compile_srcs(srcs: list[Path], incs: list[Path], arch: list[str], defs: list[str], objdir: Path,
                 jobs: int) -> list[Path]:
    from concurrent.futures import ThreadPoolExecutor
    objdir.mkdir(parents=True, exist_ok=True)

    def one(p: Path) -> Path:
        o = objdir / (p.parent.name + "_" + p.stem + ".o")
        if o.exists() and o.stat().st_mtime > p.stat().st_mtime:
            return o
        r = subprocess.run(["arm-none-eabi-gcc", *arch, *OPT, *COMMON, *C_STD, *defs, *[f"-I{i}" for i in incs],
                            "-c", str(p), "-o", str(o)], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"compile failed: {p}\n{r.stderr[-3000:]}")
        return o

    with ThreadPoolExecutor(jobs) as ex:
        return list(ex.map(one, srcs))


def build_lib(core: str, deps: Path, jobs: int) -> Path:
    dsp, cm = deps / "CMSIS-DSP", deps / "CMSIS_6" / "CMSIS" / "Core" / "Include"
    arch, defs = CORES[core]
    lib = BUILD / core / "libcmsisdsp_fx.a"
    objs = compile_srcs(dsp_sources(dsp), [dsp / "Include", dsp / "PrivateInclude", cm], arch, defs,
                        BUILD / core / "obj", jobs)
    archive(objs, lib)
    return lib


def write_inputs(W16: np.ndarray, man: pd.DataFrame) -> list[int]:
    """Bench windows: one per class, load 0, aligned start (deterministic)."""
    ids = []
    for c in range(10):
        cand = man[(man.class10 == c) & (man.load_hp == 0) & (man.start_idx % 1024 == 0)].sort_values("start_idx")
        ids.append(int(cand.index[0]))
    rows = []
    for j, i in enumerate(ids):
        vals = ", ".join(str(int(v)) for v in W16[i])
        rows.append(f"  {{{vals}}},")
    (BUILD / "fx_inputs.h").write_text(
        "// fx_inputs.h - GENERATED by tools/fxfe/run_fxfe.py; one window per class (load 0), int16 at 4096 LSB/g.\n"
        "#pragma once\n#include <stdint.h>\n"
        f"#define FX_N_WIN {len(ids)}\n"
        f"static const int16_t g_fx_win[{len(ids)}][1024] = {{\n" + "\n".join(rows) + "\n};\n")
    return ids


def build_elf(name: str, core: str, main_src: Path, lib: Path, deps: Path) -> Path:
    dsp, cm = deps / "CMSIS-DSP", deps / "CMSIS_6" / "CMSIS" / "Core" / "Include"
    arch, _ = CORES[core]
    inc = [BUILD, BENCH, REPO / "handoff", dsp / "Include", dsp / "PrivateInclude", cm]
    flags = [*arch, *OPT, *COMMON, *C_STD, *[f"-I{p}" for p in inc]]
    work = BUILD / core / name
    work.mkdir(parents=True, exist_ok=True)
    objs = []
    startup = FX / "startup_v6m.c" if core == "m0p" else FW / "startup.c"
    for src in (startup, BENCH / "frontend.c", BENCH / "frontend_fx.c", main_src):
        o = work / (src.stem + ".o")
        r = subprocess.run(["arm-none-eabi-gcc", *flags, "-Werror=implicit-function-declaration", "-c", str(src),
                            "-o", str(o)], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"compile {src.name} ({core}) failed:\n{r.stderr[-4000:]}")
        objs.append(o)
    elf = work / f"{name}.elf"
    r = subprocess.run(["arm-none-eabi-gcc", *arch, "--specs=rdimon.specs", "-T", str(FW / "mps2_an386.ld"),
                        "-Wl,--gc-sections", f"-Wl,-Map={elf.with_suffix('.map')}", "-Wl,--no-warn-rwx-segments",
                        *map(str, objs), str(lib), "-lm", "-o", str(elf)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"link {name} ({core}) failed:\n{r.stderr[-4000:]}")
    return elf


def qemu(elf: Path, cwd: Path, plugin: str | None, timeout: int) -> str:
    cmd = ["qemu-system-arm", "-M", "mps2-an386", "-cpu", "cortex-m4", "-nographic", "-monitor", "none",
           "-serial", "none", "-semihosting-config", "enable=on,target=native", "-kernel", str(elf)]
    if plugin:
        cmd += ["-plugin", plugin, "-d", "plugin", "-D", str(elf.with_suffix(".vmc.log"))]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    print(f"  qemu {elf.name}: {time.time() - t0:.0f}s, exit {r.returncode}")
    if r.returncode:
        sys.exit(r.stdout[-2000:] + r.stderr[-2000:])
    return r.stdout


def symbols(elf: Path) -> dict[str, int]:
    out = subprocess.run(["arm-none-eabi-nm", "--defined-only", str(elf)], capture_output=True, text=True).stdout
    return {ln.split()[2]: int(ln.split()[0], 16) for ln in out.splitlines() if len(ln.split()) == 3}


def run_features(W16: np.ndarray, lib: Path, deps: Path) -> np.ndarray:
    elf = build_elf("fxfeat", "m4f", FX / "fxfeat.c", lib, deps)
    work = elf.parent
    W16.tofile(work / "fx_in.bin")
    out = qemu(elf, work, None, timeout=7200)
    print("  " + out.strip().splitlines()[-1])
    F = np.fromfile(work / "fx_out.bin", dtype=np.float32).reshape(-1, N_OUT)
    if len(F) != len(W16):
        sys.exit(f"expected {len(W16)} rows, got {len(F)}")
    return F


def run_bench(core: str, lib: Path, deps: Path, plugin: Path, ids: list[int]) -> pd.DataFrame:
    elf = build_elf("fxbench", core, FX / "fxbench.c", lib, deps)
    costs = elf.with_suffix(".costs.txt")
    subprocess.run([sys.executable, str(REPO / "tools" / "vmcu" / "cyclemodel.py"), str(elf), str(costs)],
                   check=True, capture_output=True)
    sym = symbols(elf)
    spec = f"{plugin},begin={sym['vm_mark_begin']:#x},end={sym['vm_mark_end']:#x},costs={costs}"
    out = qemu(elf, elf.parent, spec, timeout=1800)
    head = next(ln for ln in out.splitlines() if ln.startswith("FXB start"))
    ram = dict(kv.split("=") for kv in head.split()[2:])
    labels = [ln.split()[1:] for ln in out.splitlines() if ln.startswith("L ")]
    vmc = [dict(kv.split("=") for kv in ln.split()[1:]) for ln in elf.with_suffix(".vmc.log").read_text().splitlines()
           if ln.startswith("VMC ") and "error" not in ln]
    if len(labels) != len(vmc):
        sys.exit(f"{len(labels)} labels vs {len(vmc)} plugin lines")
    rows = [{"core": core, "region": lab, "window": ids[int(j)], "insn": int(v["insn"]), "cyc": int(v["cyc"]), "fp": int(v["fp"])}
            for (lab, j), v in zip(labels, vmc)]
    df = pd.DataFrame(rows)
    for k, v in ram.items():
        if k.startswith("ram_"):
            df[f"static_{k}_B"] = int(v)
    return df


def summarise(df: pd.DataFrame) -> pd.DataFrame:
    s = df.groupby(["core", "region"]).agg(insn_med=("insn", "median"), cyc_med=("cyc", "median"),
                                           cyc_min=("cyc", "min"), cyc_max=("cyc", "max"),
                                           fp_med=("fp", "median")).reset_index()
    s["region"] = pd.Categorical(s.region, REGIONS, ordered=True)
    s["core"] = pd.Categorical(s.core, list(CORES), ordered=True)
    s = s.sort_values(["core", "region"])
    s["lat_us"] = s.cyc_med / F_CPU * 1e6
    s["E_uJ"] = s.cyc_med / F_CPU * P_ACTIVE_MW * 1e3
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deps", type=Path, required=True)
    ap.add_argument("--plugin", type=Path, required=True)
    ap.add_argument("--skip-features", action="store_true")
    ap.add_argument("-j", type=int, default=2)
    a = ap.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    W = np.load(PROC / "windows.npy", mmap_mode="r")
    man = pd.read_parquet(PROC / "manifest.parquet")
    W16 = to_int16(W)
    print(f"windows {W16.shape}, max |x| = {np.abs(np.asarray(W)).max():.2f} g, "
          f"saturated samples {int((np.abs(W16.astype(np.int32)) >= 32767).sum())}")
    libs = {c: build_lib(c, a.deps, a.j) for c in CORES}
    ids = write_inputs(W16, man)
    if not a.skip_features:
        F = run_features(W16, libs["m4f"], a.deps)
        np.save(PROC / "feat_fx.npy", F)
        print(f"wrote {PROC / 'feat_fx.npy'} {F.shape}")
    df = pd.concat([run_bench(c, libs[c], a.deps, a.plugin, ids) for c in CORES], ignore_index=True)
    df.to_csv(OUT / "fx_cycles.csv", index=False)
    s = summarise(df)
    s.to_csv(OUT / "fx_cost_summary.csv", index=False)
    print(s.to_string(index=False))
    ram = df.groupby("core")[[c for c in df.columns if c.startswith("static_")]].first()
    print(ram.to_string())


if __name__ == "__main__":
    main()
