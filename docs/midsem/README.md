# Mid-semester status (29 Oct 2026)

Hardware has not arrived yet. This update contains everything that can be done without it. Every number below comes
from a script-written file in `results/`; the working paper is `paper/main.pdf` (IEEE two-column).

## What is new since the proposal (sprint 1, 1 Oct)

| Area | Files | What it does |
|---|---|---|
| Splits | `ml/splits.py`, `splits/README.md` | P1/P2/P3 from the manifest, with leakage assertions (file- and bearing-disjoint tests, non-overlapping test windows). 98.1 % of P1 test windows share samples with a training or validation window; 0 % for P2/P3 |
| Features | `ml/features.py`, `handoff/features_spec.md` v1.1 | Python reference for R0–R3. **R2 is now 24 features** (bands above 5.25 kHz removed; see "Pitfalls") |
| Training sweep | `ml/models.py`, `ml/train.py`, `results/host/` | 36 architectures → 68 model IDs, all protocols and folds, FP32/PTQ/QAT, noise robustness at 20/10/5/0 dB. Scores come from the exported `.tflite`, never from Keras |
| Export / handoff | `ml/export.py`, `models/`, `configs/`, `handoff/` | `.tflite` + 16-byte-aligned C arrays, emlearn float headers, `meta.json`, `registry.csv`, norm constants (`.npz`/`.h`), the 100 parity test vectors with host outputs, `ops_union.txt` |
| Statistics | `ml/stats.py`, `ml/check_ref_kernels.py` | Host summary, Pareto fronts (P1–P3 × inference-only/end-to-end), hypervolume per family, Kendall τ, bootstrap membership (accuracy and cost-model uncertainty), battery model, rubric grid, figures, `results/headline_numbers.json` |
| Virtual Cortex-M4 | `tools/vmcu/` | Deployable code compiled for Cortex-M4F (softfp, `-Os`) and run in QEMU with an instruction-counting plugin and a TRM cycle model: per-model cycles, flash, arena, stack; M2 parity and VERIFY replay. See `tools/vmcu/README.md` |
| Board firmware | `firmware/bench/frontend.*`, `firmware/bench/tflm_glue.*`, `tools/bundle.py`, `tools/build_flash.py` | C front end and TFLM glue shared by the board, the emulator and the PC simulator; bundles for the Nano that **compile and link** with the pinned toolchain (`results/fw_build.csv`) |
| Simulated board | `tools/vmcu/host_bundle.py`, `tools/demo_sim.py` | The unmodified `bench_core.cpp`, built for the PC with the real models and driven by `tools/dut.py` over the serial protocol (SEL, VERIFY, RUN) |
| Logger | `logger/` | ESP32 INA219 window integrator (CONTRACT §7.4) with PC tests that check `measure.py` parses its lines and that energies are exact |
| R4 front end | `ml/features.py`, `firmware/bench/frontend.c`, `handoff/features_spec.md` v1.2, CONTRACT rc4 | Physics-informed envelope analysis in 41 features (no shaft speed needed). Best bearing-disjoint macro-F1 0.623 → 0.742; C on the virtual Cortex-M4 matches Python to 7e-5; 20 new model IDs (88 in total) |
| Virtual measurement campaign | `tools/vrig/` | INA219 + ESP32 rig simulated through the logger's own C core: expected session CV ≈ 0.5 % (1 Ω shunt), precision set by baseline stability, not INA219 resolution; campaign ≈ 16 h of board time, ≈ 8 h with adaptive LAT (proposal in CONTRACT rc4) |
| Model selector | `tools/make_selector.py`, `docs/selector/index.html`, live at <https://agnibha11.github.io/TinyML-Pareto/selector/> | The selection rubric as an interactive page: budgets in, best fitting model out, with the reason every other model is ruled out |
| Tests | `tests/` | 51 tests pass (37 before sprint 1) |
| Paper | `paper/main.tex`, `paper/make_numbers.py` | Working paper; every number is a macro generated from `results/` |

## Pitfalls found (all fixed)

1. **R2 bands 14–15 below float32 precision.** They sit in the stop-band of the 5 kHz low-pass. The C front end
   could not match Python there (0.62 σ error against the 1e-3 gate). Fix: feature spec v1.1, R2 = 24 features;
   CONTRACT rc3.
2. **Random forest: soft vs hard voting.** scikit-learn averages probabilities and the emlearn C code takes a majority
   vote. Host scores now use the deployed rule (`ml/train.py::rf_hard_predict`).
3. **Desktop TFLite int8 kernels ≠ TFLM arithmetic.** The VERIFY reference outputs now come from TFLite's
   reference kernels, which match TFLM bit for bit. `results/ref_kernel_check.csv` bounds the effect on scores.

4. **The window mean is a recording fingerprint.** R1's first feature is the accelerometer's DC offset; on P2 fold 0
   one held-out recording lies 16.6 σ outside the training range, and every R4 MLP that kept it scored ≈ 0.34
   macro-F1 there (0.90 without). R4 drops it; R1–R3 keep it as frozen (`results/ablation_r4_mean.csv`).

## For review

- **Abhinav:** `firmware/bench/frontend.*`, `firmware/bench/tflm_glue.*`, `tools/bundle.py`, `tools/build_flash.py`
  (they implement items listed as yours in CONTRACT §5 and VERSIONS.md); the recorded arduino-cli/gcc versions in
  `firmware/VERSIONS.md`.
- **Hridayesh:** `logger/` (the sketch is untested on an ESP32: compile it on bring-up); the paper draft; the battery
  model's sleep figures.
- **All:** CONTRACT.md rc4 (R2 = 24 features; R4; adaptive-LAT proposal) needs the three sign-offs.

## Reproduce

```bash
python ml/splits.py && python ml/features.py
python ml/train.py --families dt rf --seeds 0 1 2 3 4
python ml/train.py --families mlp cnn --seeds 0 1 2 3 4 --shard 0/2 & python ml/train.py --families mlp cnn --seeds 0 1 2 3 4 --shard 1/2
python ml/export.py && python ml/check_ref_kernels.py
python tools/vmcu/run_vmcu.py --deps <deps> --plugin <libvmcount.so>      # tools/vmcu/README.md
python ml/stats.py && python tools/vrig/campaign.py && python tools/make_selector.py
python paper/make_numbers.py && (cd paper && latexmk -pdf main.tex)
python tools/bundle.py && python tools/build_flash.py --all --compile-only
```
