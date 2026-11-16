#!/usr/bin/env python3
"""Pack the deployable models into firmware bundles for the Nano 33 BLE (CONTRACT.md 5: "bundle.py generates
firmware/bench/models/bundle_<b>.h from models/"). One bundle = one flash of the board.

Packing: models are grouped by runtime (emlearn trees never share an image with TFLM), sorted by size and placed
first-fit into bundles whose model bytes stay below --capacity-kb (the TFLM library, Mbed OS and the bench core take
the rest of the 983,040 B application flash; tools/build_flash.py --compile-only checks every bundle actually links).
The static TFLM arena of a bundle is 1.25 x the largest arena_used_bytes measured on the virtual MCU + 1 KB.
Runtime tflm-ref (the kernel ablation) needs the separate reference-kernel TFLM library (firmware/VERSIONS.md,
TFLM_REF) and is not bundled yet.

Writes (all generated, git-ignored, never edited by hand):
    firmware/bench/models/bundle_<b>.h        model data + front-end wrappers + the bench_model_t table
    firmware/bench/bundle_gen.cpp             selects bundle_<BUNDLE_ID>.h
    firmware/bench/hann_1024.h, hann_fx.h, norm_constants.h   copies of handoff/
    firmware/bench/models/bundles.json        plan: per bundle, models, TFLM yes/no, arena bytes
and models/bundles.csv (the plan, committed).

--dataset pu (Paderborn, models/pu): the same firmware sources are copied to build/pu/sketch/bench and the bundles,
handoff/pu/norm_constants.h and 5 Paderborn stored inputs (from handoff/pu/test_vectors/raw.npy, PU_STORED below)
are generated there, so firmware/bench (CWRU) is never touched; the plan goes to models/pu/bundles.csv. Tree sizes
come from the model.h size (the virtual-MCU sizes are CWRU's); TFLM arenas reuse the CWRU virtual-MCU arena of the
same model ID (same architecture, only the last Dense layer has 3 instead of 10 outputs, so the arena is no larger).

Usage: python tools/bundle.py [--capacity-kb 600] [--dataset pu]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.contract import c_name  # noqa: E402

BENCH = REPO / "firmware" / "bench"
OUT = BENCH / "models"
PU_SKETCH = REPO / "build" / "pu" / "sketch" / "bench"
# Paderborn INPUT 0..4: rows of handoff/pu/test_vectors/raw.npy (row t is class t // 10: 0 healthy, 1 inner race,
# 2 outer race). One healthy and four damaged windows, like CWRU's N, IR007, OR007, B014, IR021.
PU_STORED = (0, 10, 20, 15, 25)
PU_CLASS_NAMES = ("healthy", "inner race", "outer race")
N_FEAT = {"r0": 1024, "r1": 10, "r2": 24, "r3": 256, "r4": 41}

FE_WRAPPERS = """
static int s_fe_ready = 0;
static void fe_ready(void) { if (!s_fe_ready) { fe_init(); s_fe_ready = 1; } }
static void fe_rep0(const float *x, float *f) { fe_r0(x, g_r0_mu, g_r0_sigma, f); }
static void fe_rep1(const float *x, float *f) { fe_r1(x, f); fe_zscore(f, g_r1_mean, g_r1_std, 10); }
static void fe_rep2(const float *x, float *f) { fe_r2(x, f); fe_zscore(f, g_r2_mean, g_r2_std, 24); }
static void fe_rep3(const float *x, float *f) { fe_r3(x, f); fe_zscore(f, g_r3_mean, g_r3_std, 256); }
static void fe_rep4(const float *x, float *f) { fe_r4(x, f); fe_zscore(f, g_r4_mean, g_r4_std, 41); }
static int s_last_cls = 0;
static int tree_outputs(float *o, int n) {
  const int m = n < 10 ? n : 10;
  for (int i = 0; i < m; ++i) o[i] = (i == s_last_cls) ? 1.0f : 0.0f;
  return m;
}
"""


def sizes(reg: pd.DataFrame, use_vmcu: bool = True) -> dict[str, int]:
    vm_p = REPO / "results" / "vmcu" / "vmcu_metrics.csv"
    vm = pd.read_csv(vm_p).set_index("model_id") if use_vmcu and vm_p.exists() else None
    out = {}
    for r in reg.itertuples(index=False):
        if r.runtime == "emlearn":
            out[r.model_id] = int(vm.loc[r.model_id, "flash_model_B"]) if vm is not None and r.model_id in vm.index \
                else (REPO / r.artifact).stat().st_size // 6
        else:
            out[r.model_id] = (REPO / r.artifact).stat().st_size
    return out


def arena(mids: list[str]) -> int:
    vm_p = REPO / "results" / "vmcu" / "vmcu_metrics.csv"
    vm = pd.read_csv(vm_p).set_index("model_id")
    a = max(int(vm.loc[m, "arena_used_B"]) for m in mids)
    return int((a * 1.25 + 1024 + 1023) // 1024 * 1024)


def pack(reg: pd.DataFrame, size: dict[str, int], cap: int) -> list[dict]:
    bundles = []
    for runtime in ("emlearn", "tflm-cmsis"):
        ms = sorted(reg[reg.runtime == runtime].model_id, key=lambda m: -size[m])
        bins: list[list[str]] = []
        for m in ms:
            for b in bins:
                if sum(size[x] for x in b) + size[m] <= cap:
                    b.append(m)
                    break
            else:
                bins.append([m])
        for b in bins:
            order = [m for m in reg.model_id if m in b]          # registry order inside a bundle
            bundles.append({"runtime": runtime, "tflm": runtime != "emlearn", "models": order,
                            "model_bytes": sum(size[m] for m in b)})
    for i, b in enumerate(bundles, start=1):
        b["id"] = i
        b["arena_bytes"] = arena(b["models"]) if b["tflm"] else 0
    return bundles


def body(b: dict, reg: pd.DataFrame) -> str:
    r = reg.set_index("model_id")
    lines = [f"// bundle_{b['id']}.h - GENERATED by tools/bundle.py; do not edit.",
             f"// {len(b['models'])} models, runtime {b['runtime']}, ~{b['model_bytes']:,} B of model data,"
             f" arena {b['arena_bytes']:,} B.", "#pragma once", "#include <stdint.h>", '#include "../bench_core.h"',
             '#include "../frontend.h"', '#include "../norm_constants.h"']
    if b["tflm"]:
        lines.append('#include "../tflm_glue.h"')
    funcs, table = [], []
    for k, mid in enumerate(b["models"]):
        cn = c_name(mid)
        art = (REPO / r.loc[mid, "artifact"])
        src = (art if art.suffix == ".h" else art.with_name("model.h")).read_text().replace("#pragma once", "")
        lines.append(f"\n// ---- {mid} ----\n{src}")
        rep = r.loc[mid, "input_rep"]
        if r.loc[mid, "runtime"] == "emlearn":
            funcs.append(f"static int setup_{k}(uint32_t *u) {{ fe_ready(); *u = 0; return 0; }}\n"
                         f"static int infer_{k}(const float *f, int *c) {{ *c = (int){cn}_predict(f, {N_FEAT[rep]}); "
                         f"s_last_cls = *c; return 0; }}")
            table.append(f'  {{"{mid}", "emlearn", setup_{k}, fe_rep{rep[1]}, infer_{k}, tree_outputs}},')
        else:
            funcs.append(f"static int setup_{k}(uint32_t *u) {{ fe_ready(); return bench_tflm_setup(g_model_{cn}, u); }}")
            table.append(f'  {{"{mid}", "{r.loc[mid, "runtime"]}", setup_{k}, fe_rep{rep[1]}, bench_tflm_infer, '
                         'bench_tflm_outputs},')
    lines += [FE_WRAPPERS, *funcs, "", "const bench_model_t g_models[] = {", *table, "};",
              "const int g_n_models = (int)(sizeof g_models / sizeof g_models[0]);", ""]
    return "\n".join(lines)


def pu_sketch() -> Path:
    """Fresh copy of the tracked firmware/bench sources in build/pu/sketch/bench (generated files left out)."""
    if PU_SKETCH.exists():
        shutil.rmtree(PU_SKETCH)
    PU_SKETCH.mkdir(parents=True)
    generated = {"bundle_gen.cpp", "hann_1024.h", "hann_fx.h", "norm_constants.h"}
    for f in BENCH.iterdir():
        if f.is_file() and f.name not in generated:
            shutil.copy(f, PU_SKETCH / f.name)
    return PU_SKETCH


def write_pu_stored(sketch: Path) -> None:
    """stored_inputs.cpp of the Paderborn sketch: the PU_STORED rows of handoff/pu/test_vectors/raw.npy."""
    import numpy as np
    tv = REPO / "handoff" / "pu" / "test_vectors"
    raw, ids = np.load(tv / "raw.npy"), np.load(tv / "ids.npy")
    body = []
    for j, t in enumerate(PU_STORED):
        vals = [f"{v:.9e}f" for v in raw[t].astype(np.float32)]
        rows = ",\n".join("    " + ", ".join(vals[i:i + 6]) for i in range(0, len(vals), 6))
        body.append(f"  // INPUT {j}: {PU_CLASS_NAMES[t // 10]} (class3={t // 10}), Paderborn test vector {t}, "
                    f"window_id {int(ids[t])}\n  {{\n{rows},\n  }},")
    (sketch / "stored_inputs.cpp").write_text(
        "// stored_inputs.cpp - GENERATED by tools/bundle.py --dataset pu; do not edit.\n"
        "// The 5 fixed Paderborn test windows behind INPUT 0..4 (64 kHz resampled to 12 kHz, 5 kHz low-pass), rows\n"
        f"// {', '.join(map(str, PU_STORED))} of handoff/pu/test_vectors/raw.npy (P3 fold-0 test set, unseen bearings).\n"
        '#include "stored_inputs.h"\n\n'
        "const float g_stored_inputs[BENCH_N_INPUTS][BENCH_WIN] = {\n" + "\n".join(body) + "\n};\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capacity-kb", type=int, default=600)
    ap.add_argument("--dataset", choices=("cwru", "pu"), default="cwru")
    a = ap.parse_args()
    pu = a.dataset == "pu"
    models_dir = REPO / "models" / ("pu" if pu else "")
    reg = pd.read_csv(models_dir / "registry.csv", keep_default_na=False)
    reg = reg[(reg.feasible == 1) & reg.runtime.isin(["emlearn", "tflm-cmsis"])]
    if pu:   # registry paths are models/<id>/model.h relative to models/pu
        reg = reg.assign(artifact=[a_.replace("models/", "models/pu/", 1) for a_ in reg.artifact])
    size = sizes(reg, use_vmcu=not pu)
    bundles = pack(reg, size, a.capacity_kb * 1024)
    bench = pu_sketch() if pu else BENCH
    out = bench / "models"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for b in bundles:
        (out / f"bundle_{b['id']}.h").write_text(body(b, reg))
    sel = ["// bundle_gen.cpp - GENERATED by tools/bundle.py; do not edit. Selects the bundle given by -DBUNDLE_ID.",
           "#ifdef BUNDLE_ID"]
    for i, b in enumerate(bundles):
        sel.append(f"#{'if' if i == 0 else 'elif'} BUNDLE_ID == {b['id']}")
        sel.append(f'#include "models/bundle_{b["id"]}.h"')
    sel += ["#else", '#error "unknown BUNDLE_ID: run tools/bundle.py"', "#endif", "#endif", ""]
    (bench / "bundle_gen.cpp").write_text("\n".join(sel))
    for h in ("hann_1024.h", "hann_fx.h"):   # hann_fx.h: frontend_fx.c (fixed-point front end); same for both datasets
        shutil.copy(REPO / "handoff" / h, bench / h)
    shutil.copy(REPO / "handoff" / ("pu" if pu else "") / "norm_constants.h", bench / "norm_constants.h")
    if pu:
        write_pu_stored(bench)
    (out / "bundles.json").write_text(json.dumps(bundles, indent=1))
    rows = [{"bundle": b["id"], "k": k, "model_id": m, "runtime": b["runtime"], "est_flash_model_B": size[m],
             "bundle_arena_B": b["arena_bytes"]} for b in bundles for k, m in enumerate(b["models"])]
    pd.DataFrame(rows).to_csv(models_dir / "bundles.csv", index=False)
    for b in bundles:
        print(f"bundle {b['id']}: {b['runtime']:<10} {len(b['models']):2d} models  {b['model_bytes'] / 1024:7.1f} kB"
              f"  arena {b['arena_bytes'] / 1024:6.1f} kB")


if __name__ == "__main__":
    main()
