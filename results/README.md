# results/

Every file here is written by a script (CONTRACT.md §1 rule 7). Never edit a row by hand: rerun the script. Both
datasets use the same layout, so a file has the same relative path under `cwru/` and `pu/`.

```
results/
├── cwru/                  CWRU bearing data set (10 classes; protocols P1-P3)
│   ├── data/              window and split counts, raw-file provenance
│   ├── host/              one JSON per training run (3,520 runs)
│   ├── analysis/          host summary, cost table, Pareto fronts, statistics, headline numbers
│   ├── fixedpoint/        fixed-point and hybrid front ends on three core classes
│   ├── cascade/           detect-then-diagnose gate (host estimate)
│   └── hardware/          energy rig measurements on the Arduino Nano 33 BLE
├── pu/                    Paderborn bearing data set (3 classes; protocols P1-P4), same layout
│   ├── data/  host/ (4,400 runs)  analysis/  cascade/  hardware/
│   └── run_config.json    settings of the training run (tools/pu/run_pu.py)
├── vmcu/                  virtual Cortex-M4 (QEMU + cycle model): cycles, memory, feature parity
├── vrig/                  simulated INA219 rig: campaign plan and precision study
├── figs/                  every figure of the paper, both data sets (png + pdf)
├── demo/                  terminal captures used on the slides
└── rig/                   local only, git-ignored: calibration runs, run logs, rig experiments
```

## cwru/

| File | Content | Written by |
|---|---|---|
| `data/data_counts.csv` | per recording: samples and windows | `ml/prepare_data.py` |
| `data/data_files.csv` | per recording: keys, length, RPM, sha256, low-pass cutoff | `ml/prepare_data.py` |
| `data/split_counts.csv` | windows per protocol, fold, set and class | `ml/splits.py` |
| `host/<model_id>__<P>__f<fold>__s<seed>.json` | one training run: F1, accuracy, confusion, per-window predictions (CONTRACT §6.2) | `ml/train.py` |
| `analysis/host_summary.csv` | per model and protocol: macro-F1 and accuracy, mean ± std over folds and seeds, clean and noisy (CONTRACT §6.2) | `ml/stats.py` |
| `analysis/prehw_costs.csv` | per model: flash, peak RAM, cycles, estimated energy (virtual Cortex-M4) | `ml/stats.py` |
| `analysis/pareto_<P>_<inf\|e2e>.csv` | Pareto membership per protocol, inference-only or end-to-end energy | `ml/stats.py` |
| `analysis/hv_table.csv`, `kendall_tau.csv`, `rubric_grid.csv`, `lifetime.csv` | hypervolume per family, rank agreement between protocols, rubric grid, battery lifetime | `ml/stats.py` |
| `analysis/headline_numbers.json` | every number the paper and slides quote, plus the energy-model constants | `ml/stats.py` |
| `analysis/ref_kernel_check.csv` | TFLM reference vs optimised kernels: effect on scores | `ml/check_ref_kernels.py` |
| `analysis/ablation_r4_mean.csv` | R4 window-mean ablation (one-off record) | one-off run, commit `deb7112` |
| `fixedpoint/fx_fidelity.csv`, `fx_accuracy*.csv`, `fx_mlp_agreement.csv`, `fx_summary.json` | fixed-point front end vs float: feature error and accuracy | `ml/fixed_point.py` |
| `fixedpoint/fx_cycles.csv`, `fx_cost_summary.csv` | front-end cycles on three core classes | `tools/fxfe/run_fxfe.py` |
| `fixedpoint/fx_cost_summary_loopunroll.csv` | the same with CMSIS-DSP loop unrolling | `FX_UNROLL=1 tools/fxfe/run_fxfe.py` |
| `cascade/gates.csv`, `cascade_runs.csv`, `cascade_models.csv`, `summary.json` | gate TPR/FPR, cascade F1 and energy per run and model | `ml/cascade.py` |
| `hardware/hw_measurements.csv` | one row per measurement window (CONTRACT §6.4) | `tools/measure.py` |
| `hardware/session_log.csv` | one row per rig session: date, firmware and logger hash, shunt, notes (CONTRACT §6.5) | `tools/measure.py` |
| `hardware/fw_build.csv` | per bundle and model: flash, static RAM, arena, git hash of the build | `tools/build_flash.py` |
| `hardware/board_verify.csv` | on-board VERIFY per model on the unpatched Chirale 2.0.0: class agreement and max error against the host (int8 fails) | `tools/verify_board.py` |
| `hardware/board_verify_int8v2.csv` | the same VERIFY of every TFLM model on the patched library `chirale-2.0.0+int8v2`: all pass | `tools/verify_board.py` |

## pu/

| File | Content | Written by |
|---|---|---|
| `data/data_counts.csv`, `data/split_counts.csv` | windows per recording; per protocol, fold, set and class | `ml/pu_data.py` |
| `host/*.json` | one training run, same format as CWRU | `ml/train.py` via `tools/pu/run_pu.py` |
| `analysis/host_summary.csv`, `kendall_tau.csv` | per model and protocol scores; rank agreement within Paderborn and with CWRU | `ml/pu_analysis.py` |
| `analysis/pareto_P3.csv`, `pareto_P4.csv` | accuracy-energy fronts on unseen bearings and on real damage | `ml/pu_analysis.py` |
| `analysis/per_bearing_P3.csv`, `per_bearing_P4.csv` | scores per test bearing | `ml/pu_analysis.py` |
| `analysis/summary.json` | every Paderborn number the paper quotes | `ml/pu_analysis.py` |
| `cascade/gates.csv`, `cascade_models.csv` | detect-then-diagnose gate on Paderborn | `ml/pu_analysis.py` |
| `hardware/hw_measurements.csv`, `session_log.csv`, `fw_build.csv` | as for CWRU | `tools/measure.py`, `tools/build_flash.py --dataset pu` |
| `hardware/board_verify.csv`, `board_verify_int8v2.csv` | as for CWRU | `tools/verify_board.py` |
| `run_config.json` | seeds, protocols and families of the training run | `tools/pu/run_pu.py` |

`pu/logs/` and `pu/progress.json` (worker logs and the live status of a training run) are git-ignored.

## Shared

| Folder | Content | Written by |
|---|---|---|
| `vmcu/` | `vmcu_metrics.csv` (per model: flash, arena, stack, cycles, VERIFY), `vmcu_parity.csv` (C vs Python front end), `vmcu_footprint.csv`, `<image>_regions.csv` | `tools/vmcu/run_vmcu.py` |
| `vrig/` | `campaign_plan.csv`, `precision.csv`, `sensitivity.csv`, `summary.json` | `tools/vrig/campaign.py` |
| `figs/` | paper figures; `paper/make_numbers.py` copies them into `paper/figs/` | `ml/stats.py`, `ml/cascade.py`, `ml/pu_analysis.py`, `ml/fixed_point.py`, `ml/prepare_data.py`, `tools/vrig/campaign.py` |
| `demo/` | terminal captures (svg, png) | `tools/demo_sim.py`, `tools/vmcu/run_vmcu.py`, `tools/svg2png.py` |

## Session numbering (hardware/)

Session = 10 × repeat + bundle; a stopped session continued later gets +100 (for example 131 continues 31). Repeats
1-3 are complete for both data sets: 80 models each, at least 20 accepted windows per model and repeat.
Repeats 1-3 ran on the unpatched library, whose int8 outputs are wrong. Repeats 4 and 5 re-measure only the TFLM int8
models plus fp32 anchors: repeat 4 (CWRU 42-44, Paderborn 44-46) on patch v1 `chirale-2.0.0+int8`, with sessions
CWRU 42 and Paderborn 45 stopped early; repeats 5-7 (CWRU 52-54, 62-64, 72-74; Paderborn 54-56, 64-66, 74-76) on
`chirale-2.0.0+int8v2`, complete: 29 int8 models + 6 fp32 anchors per data set, 3 repeats each. Four repeat-6/7
sessions (CWRU 63, 74; Paderborn 74, 76) stopped on a damaged serial reply; their remaining models are in the
continuations CWRU 163, 174 and Paderborn 174, 176 (partial rows of the two models cut mid-way were removed).
`session_log.csv` notes name the library of each session; `fw_build.csv` holds the latest (int8v2) builds.
