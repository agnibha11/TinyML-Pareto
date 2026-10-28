#!/usr/bin/env python3
"""Build the static libraries of the virtual MCU for Cortex-M4F (arm-none-eabi-gcc), without running any script
from the third-party trees (they are treated as plain source data).

Libraries (written to build/vmcu/lib/):
    libtflm_cmsis.a   TFLM + CMSIS-NN optimized kernels      (runtime 'tflm-cmsis', the board's main build)
    libtflm_ref.a     TFLM reference kernels only             (runtime 'tflm-ref', the kernel ablation)
    libcmsisdsp.a     CMSIS-DSP subset for the R2/R3 front end (arm_rfft_fast_f32 and its tables)
and, with --only host, x86 builds of TFLM (reference kernels) and CMSIS-DSP in build/host/lib/ for the PC simulator.

Flags match the board build (arduino:mbed_nano 4.6.0 cflags.txt): Cortex-M4F, -mfloat-abi=softfp, -Os.
Pinned sources (tools/vmcu/deps.lock): tflite-micro, flatbuffers, CMSIS_6, CMSIS-NN, CMSIS-DSP.

Usage: python tools/vmcu/build_libs.py --deps <dir with tflite-micro flatbuffers CMSIS_6 CMSIS-NN CMSIS-DSP> [-j 2]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "build" / "vmcu"

ARCH = ["-mcpu=cortex-m4", "-mthumb", "-mfpu=fpv4-sp-d16", "-mfloat-abi=softfp"]   # as the mbed_nano core
OPT = ["-Os"]
COMMON = ["-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-ffunction-sections", "-fdata-sections",
          "-fmessage-length=0", "-DTFLM_REMOVE_VIRTUAL_DELETE", "-DTF_LITE_DISABLE_X86_NEON",
          "-DTF_LITE_MCU_DEBUG_LOG", "-DFLATBUFFERS_LOCALE_INDEPENDENT=0", "-DARM_NN_ENABLE_F16=0",
          "-DARM_NN_ENABLE_F32=0", "-fsigned-char", "-w"]
CXX = ["-std=c++17", "-fno-rtti", "-fno-exceptions", "-fno-threadsafe-statics"]
C = ["-std=c17"]

EXCLUDE_KERNEL_PARTS = ("_test", "test_", "kernel_runner", "testdata", "conv_test_common")


def tflm_sources(tflm: Path, variant: str) -> list[Path]:
    m = tflm / "tensorflow" / "lite" / "micro"
    src = []
    for sub in ("", "arena_allocator", "memory_planner", "kernels/internal", "kernels/internal/reference"):
        src += sorted((m / sub).glob("*.cc"))
    kern = [p for p in sorted((m / "kernels").glob("*.cc")) if not any(x in p.name for x in EXCLUDE_KERNEL_PARTS)]
    if variant == "cmsis":
        opt = {p.name: p for p in (m / "kernels" / "cmsis_nn").glob("*.cc")}
        kern = [opt.get(p.name, p) for p in kern]
    src += kern
    drop = ("_test.cc", "test_helper", "fake_micro_context", "mock_micro_graph", "recording_micro_allocator",
            "recording_single_arena", "micro_profiler", "test_helper_custom_ops", "debug_log.cc", "micro_time.cc",
            "system_setup.cc")
    return [p for p in src if not any(d in p.name for d in drop)]


def cmsis_nn_sources(nn: Path) -> list[Path]:
    return [p for p in sorted((nn / "Source").rglob("*.c")) if not p.name.endswith(("_f16.c", "_f32.c"))]


def cmsis_dsp_sources(dsp: Path) -> list[Path]:
    s = dsp / "Source"
    names = ["TransformFunctions/arm_rfft_fast_f32.c", "TransformFunctions/arm_rfft_fast_init_f32.c",
             "TransformFunctions/arm_cfft_f32.c", "TransformFunctions/arm_cfft_init_f32.c",
             "TransformFunctions/arm_cfft_radix8_f32.c", "TransformFunctions/arm_bitreversal2.c",
             "TransformFunctions/arm_bitreversal.c", "CommonTables/arm_common_tables.c",
             "CommonTables/arm_const_structs.c", "ComplexMathFunctions/arm_cmplx_mag_f32.c",
             "ComplexMathFunctions/arm_cmplx_mag_squared_f32.c"]
    return [s / n for n in names]


def compile_all(srcs, incs, defs, objdir: Path, jobs: int, host: bool = False) -> list[Path]:
    objdir.mkdir(parents=True, exist_ok=True)

    def one(p: Path) -> Path:
        o = objdir / (p.parent.name + "_" + p.stem + ".o")
        if o.exists() and o.stat().st_mtime > p.stat().st_mtime:
            return o
        if host:
            cc, arch, opt = ("g++" if p.suffix == ".cc" else "gcc"), [], ["-O2", "-fPIC"]
        else:
            cc, arch, opt = ("arm-none-eabi-g++" if p.suffix == ".cc" else "arm-none-eabi-gcc"), ARCH, OPT
        lang = CXX if p.suffix == ".cc" else C
        cmd = [cc, *arch, *opt, *COMMON, *lang, *defs, *[f"-I{i}" for i in incs], "-c", str(p), "-o", str(o)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"compile failed: {p}\n{r.stderr[-3000:]}")
        return o

    with ThreadPoolExecutor(jobs) as ex:
        return list(ex.map(one, srcs))


def archive(objs, lib: Path, ar: str = "arm-none-eabi-ar") -> None:
    lib.parent.mkdir(parents=True, exist_ok=True)
    if lib.exists():
        lib.unlink()
    subprocess.run([ar, "rcs", str(lib), *map(str, objs)], check=True)
    print(f"{lib.name}: {len(objs)} objects, {lib.stat().st_size / 1e6:.2f} MB")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deps", type=Path, required=True)
    ap.add_argument("-j", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--only", choices=["cmsis", "ref", "dsp", "host"], nargs="*")
    a = ap.parse_args()
    d = a.deps
    tflm, fb, cm, nn, dsp = d / "tflite-micro", d / "flatbuffers", d / "CMSIS_6", d / "CMSIS-NN", d / "CMSIS-DSP"
    core = cm / "CMSIS" / "Core" / "Include"
    base_inc = [tflm, fb / "include", core]
    todo = a.only or ["dsp", "ref", "cmsis", "host"]
    if "dsp" in todo:
        objs = compile_all(cmsis_dsp_sources(dsp), [dsp / "Include", dsp / "PrivateInclude", core],
                           ["-DARM_MATH_CM4", "-D__FPU_PRESENT=1"], OUT / "obj" / "dsp", a.j)
        archive(objs, OUT / "lib" / "libcmsisdsp.a")
    if "ref" in todo:
        objs = compile_all(tflm_sources(tflm, "ref"), base_inc, [], OUT / "obj" / "ref", a.j)
        archive(objs, OUT / "lib" / "libtflm_ref.a")
    if "host" in todo:   # x86 build (reference kernels) for the PC bench simulator (tools/vmcu/host_bundle.py)
        objs = compile_all(cmsis_dsp_sources(dsp), [dsp / "Include", dsp / "PrivateInclude", core],
                           ["-D__GNUC_PYTHON__"], REPO / "build" / "host" / "obj" / "dsp", a.j, host=True)
        archive(objs, REPO / "build" / "host" / "lib" / "libcmsisdsp.a", "ar")
        objs = compile_all(tflm_sources(tflm, "ref"), base_inc, ["-DTF_LITE_USE_CTIME"],
                           REPO / "build" / "host" / "obj" / "tflm", a.j, host=True)
        archive(objs, REPO / "build" / "host" / "lib" / "libtflm.a", "ar")
    if "cmsis" in todo:
        inc = [*base_inc, nn, nn / "Include"]
        objs = compile_all(tflm_sources(tflm, "cmsis"), inc, ["-DCMSIS_NN"], OUT / "obj" / "cmsis", a.j)
        objs += compile_all(cmsis_nn_sources(nn), [nn / "Include", core], [], OUT / "obj" / "cmsisnn", a.j)
        archive(objs, OUT / "lib" / "libtflm_cmsis.a")


if __name__ == "__main__":
    main()
