# TinyML-Pareto

Which TinyML model family should run on the sensor? This repo measures accuracy, energy and memory for bearing-fault
diagnosis (CWRU, 10 classes) on an Arduino Nano 33 BLE. Energy comes from an INA219 read by an ESP32 logger. The study
compares the Pareto frontiers under leaky (P1) and leakage-free (P2, P3) evaluation.

Interfaces between the three workstreams are fixed in **[CONTRACT.md](CONTRACT.md)**. Read it before writing code.

**Model selector (interactive):** <https://agnibha11.github.io/TinyML-Pareto/selector/>. Give it RAM and flash
budgets, an inference rate, a battery and a target life; it returns the most accurate model that fits. Source:
`docs/selector/index.html`, built by `tools/make_selector.py` and served by GitHub Pages from `docs/`.

## Layout

```
CONTRACT.md            M0 interface contract (git rules, formats, serial protocol, sync pin)
requirements.txt       direct dependencies (pinned)
requirements-lock.txt  full pip freeze; install from this
data/raw/              CWRU .mat files          (git-ignored)
data/processed/        windows.npy, manifest.parquet (git-ignored)
ml/                    prepare_data.py, splits.py, features.py, train.py, export.py, evaluate.py, stats.py
configs/               one YAML per model config
models/                registry.csv + models/<model_id>/
handoff/               classes.json, feature spec, constants, test vectors (Agnibha -> Abhinav)
firmware/              VERSIONS.md, bench/ (benchmark sketch), tests/
tools/                 dut.py (board wrapper), measure.py (campaign), bundle.py, build_flash.py,
                       vmcu/ (virtual Cortex-M4), demo_sim.py (simulated-board dry run)
logger/                ESP32 INA219 logger firmware
results/               script-written outputs only
docs/                  related-work gap table, notes for the paper
tests/                 host-side tests (pytest)
```

## Quick start

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-lock.txt
export TF_USE_LEGACY_KERAS=1            # needed for QAT (tf-keras); put it in your shell rc

python ml/env_smoke_test.py             # ~1 min: QAT + PTQ int8 export and an emlearn tree; must end "ENV OK"
python ml/prepare_data.py               # downloads the 40 CWRU files, writes data/processed/*
python ml/prepare_data.py --check       # re-verifies counts against results/data_counts.csv
python -m pytest tests/                 # host tools against the simulated board
python tools/measure.py --dry-run       # writes a dummy results/dryrun/hw_measurements.csv
```

## ML pipeline (host, no hardware needed)

```bash
python ml/splits.py                     # P1/P2/P3 split files + leakage assertions -> splits/, splits/README.md
python ml/features.py                   # caches R1-R4 for every window (features_spec.md v1.2)
python ml/train.py --families dt rf --seeds 0 1 2 3 4            # trees: minutes
python ml/train.py --families mlp cnn --seeds 0 1 2 3 4 --shard 0/2 &    # NNs: run two shards, one per core
python ml/train.py --families mlp cnn --seeds 0 1 2 3 4 --shard 1/2
python ml/export.py                     # models/<id>/, registry.csv, handoff/ norm constants, test vectors, ops
python ml/stats.py                      # host_summary.csv, Pareto/HV/tau, rubric, battery model, figures
```

Every NN score comes from the exported `.tflite` run in the TFLite interpreter. Random-forest scores use the deployed
emlearn rule (hard majority vote), not scikit-learn's soft vote. See `ml/train.py::rf_hard_predict`.

## Virtual Cortex-M4 (pre-hardware latency, memory and gates)

`tools/vmcu/` compiles the deployable code (C front end on CMSIS-DSP, emlearn trees, TFLM with CMSIS-NN or reference
kernels) for Cortex-M4F and runs it in QEMU with an instruction-counting plugin and a Cortex-M4 cycle model. It writes
`results/vmcu/` and checks feature parity (M2) and VERIFY agreement on the 100 test vectors. See
[tools/vmcu/README.md](tools/vmcu/README.md). `tools/demo_sim.py` drives the bench firmware, built for the PC with the real
models, over the serial protocol via `tools/dut.py`.

If the CWRU site blocks the download, put the 40 `.mat` files in `data/raw/` by hand (names `97.mat` … `237.mat`)
and rerun the script. Another option is the `srigas/cwru_bearing_numpy` repackaging:
`python ml/prepare_data.py --source npz --npz-dir <clone>/Data`. Both give 9,346 stride-512 windows (4,683
non-overlapping, 401 s of signal).

CI (`.github/workflows/tests.yml`) runs the same tests on every push and PR; `main` requires it to pass.

The firmware tests compile `firmware/bench/bench_core.cpp` for the PC (`firmware/tests/host_sim`, needs `g++` and
`make`) and drive it through `tools/dut.py` over a pseudo-terminal. This way the serial protocol is tested before any
board exists.
