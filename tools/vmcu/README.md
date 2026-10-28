# tools/vmcu — virtual Cortex-M4F for pre-hardware measurements

Runs the **deployable code** of every model as Cortex-M4F machine code inside QEMU (`mps2-an386`), with:

- **exact instruction counts** per measured phase, from a QEMU TCG plugin (`plugin/vmcount.c`);
- an **estimated cycle count** per phase, from a static Cortex-M4 TRM timing model (`cyclemodel.py`);
- **memory**: flash per model (ELF symbol sizes), TFLM `arena_used_bytes()`, peak stack (painted stack), and the
  per-library code/data split from the linker map;
- **gates**: the M2 feature-parity check (C front end vs `ml/features.py` on the 100 test vectors) and a VERIFY replay
  (device outputs vs `handoff/test_vectors/outputs/`).

Phases follow CONTRACT.md §7.5: *front end* = R0 normalisation or R1–R3 features + z-score; *inference* = input
quantisation + `Invoke()`/`predict()` + argmax.

**What it is not.** QEMU is not cycle-accurate. Instruction counts are exact; cycles are a model, with no flash wait
states, no bus contention, and branches costed by their average. Energy derived from it is a chip-level estimate. The
board's DWT (`LAT`) and the INA219 rig replace both. Comparing them is planned as a paper result: how well does an
emulator predict MCU cost?

## Pieces

| File | Role |
|---|---|
| `fw/frontend.c/.h` | C front end R0–R3 (features_spec.md v1.1) on CMSIS-DSP `arm_rfft_fast_f32`; candidate for `firmware/bench/frontend.*` |
| `fw/vbench.cc` | benchmark main: per model, setup → 5 × (front end, inference) between markers → stack probe → 100 VERIFY lines |
| `fw/vm_tflm.cc` | TFLM runner: static arena, explicit 5-op resolver (CONV_2D, FULLY_CONNECTED, MAX_POOL_2D, MEAN, SOFTMAX) |
| `fw/startup.c`, `fw/mps2_an386.ld` | vector table, FPU enable, marker functions, memory map |
| `build_libs.py` | builds TFLM (CMSIS-NN and reference kernels), CMSIS-NN and CMSIS-DSP with `arm-none-eabi-gcc -Os`, hard-float; `--only host` builds the x86 versions for the PC simulator |
| `run_vmcu.py` | generates the three images (trees / cmsis / ref), builds, runs QEMU + plugin, writes `results/vmcu/*` |
| `host_bundle.py` | builds the team's `firmware/bench/bench_core.cpp` for the PC with the real models (used by `tools/demo_sim.py`) |
| `deps.lock` | pinned third-party sources |

## Run

```bash
sudo apt-get install gcc-arm-none-eabi libnewlib-arm-none-eabi qemu-system-arm libglib2.0-dev
bash tools/vmcu/fetch_deps.sh ~/vmcu-deps            # git-fetches the pinned commits in deps.lock
curl -sSfL -o ~/vmcu-deps/qemu-plugin.h https://raw.githubusercontent.com/qemu/qemu/v8.2.2/include/qemu/qemu-plugin.h
gcc -O2 -shared -fPIC -I ~/vmcu-deps tools/vmcu/plugin/vmcount.c -o ~/vmcu-deps/libvmcount.so $(pkg-config --cflags glib-2.0)
python tools/vmcu/build_libs.py --deps ~/vmcu-deps -j 4
python tools/vmcu/run_vmcu.py --deps ~/vmcu-deps --plugin ~/vmcu-deps/libvmcount.so
```

The plugin header must match the installed QEMU (`qemu-system-arm --version`; tested with 8.2.2). Outputs:
`results/vmcu/vmcu_metrics.csv`, `vmcu_parity.csv`, `vmcu_footprint.csv`, `<image>_regions.csv`.

Nothing from the third-party trees is executed on the host. They are compiled as plain sources by `build_libs.py`.
