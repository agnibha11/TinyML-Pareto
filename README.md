# TinyML-Pareto

Which TinyML model family should run on the sensor? This repo measures accuracy, energy and memory for bearing-fault
diagnosis (CWRU, 10 classes) on an Arduino Nano 33 BLE. Energy comes from an INA219 read by an ESP32 logger. The study
compares the Pareto frontiers under leaky (P1) and leakage-free (P2, P3) evaluation.

Interfaces between the three workstreams are fixed in **[CONTRACT.md](CONTRACT.md)**. Read it before writing code.

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
tools/                 dut.py (board wrapper), measure.py (campaign), bundle.py, build_flash.py
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

If the CWRU site blocks the download, put the 40 `.mat` files in `data/raw/` by hand (names `97.mat` … `237.mat`)
and rerun the script. Another option is the `srigas/cwru_bearing_numpy` repackaging:
`python ml/prepare_data.py --source npz --npz-dir <clone>/Data`. Both give 9,346 stride-512 windows (4,683
non-overlapping, 401 s of signal).

The firmware tests compile `firmware/bench/bench_core.cpp` for the PC (`firmware/tests/host_sim`, needs `g++` and
`make`) and drive it through `tools/dut.py` over a pseudo-terminal. This way the serial protocol is tested before any
board exists.
