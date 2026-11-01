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
ml/                    prepare_data.py, splits.py, features.py, train.py, export.py, evaluate.py, stats.py,
                       fixed_point.py (fixed-point front-end accuracy), cascade.py (detect-then-diagnose cascade)
configs/               one YAML per model config
models/                registry.csv + models/<model_id>/
handoff/               classes.json, feature spec, constants, test vectors (Agnibha -> Abhinav)
firmware/              VERSIONS.md, bench/ (benchmark sketch), tests/
tools/                 dut.py (board wrapper), measure.py (campaign), bundle.py, build_flash.py,
                       vmcu/ (virtual Cortex-M4), fxfe/ (fixed-point front ends on three core classes),
                       vrig/ (simulated INA219 rig), demo_sim.py (simulated-board dry run)
logger/                ESP32 INA219 logger firmware
results/               script-written outputs only
docs/                  related-work gap table, notes for the paper, midsem/ and sprint2/ status pages, selector/
tests/                 host-side tests (pytest)
```

## Quick start

TensorFlow 2.18 supports Python 3.9-3.12. If your system Python is newer (Ubuntu 26.04 ships 3.14, and its pip
refuses system-wide installs), let [uv](https://docs.astral.sh/uv/) fetch Python 3.11 for the venv:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh          # once; then open a new terminal
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -r requirements-lock.txt
```

Otherwise:

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
python ml/cascade.py                    # detect-then-diagnose cascade over every stored run (docs/sprint2)
```

Sprint 2 added a fixed-point/hybrid front end (`firmware/bench/frontend_fx.c`, `tools/fxfe/`, `ml/fixed_point.py`)
and the detect-then-diagnose cascade; see [docs/sprint2/README.md](docs/sprint2/README.md).

The second dataset (Paderborn) has its own section below.

Every NN score comes from the exported `.tflite` run in the TFLite interpreter. Random-forest scores use the deployed
emlearn rule (hard majority vote), not scikit-learn's soft vote. See `ml/train.py::rf_hard_predict`.

## Second dataset: Paderborn (real bearing damage)

The [Paderborn KAt bearing dataset](https://mb.uni-paderborn.de/kat/datacenter) (Lessmeier et al., PHM Europe 2016)
has 32 separate physical bearings: 6 healthy, 12 with artificial damage and 14 with real damage from accelerated
lifetime tests, each recorded at 4 operating conditions. It is prepared exactly like CWRU (12 kHz, 5 kHz low-pass,
1024-sample windows, R0-R4) and all 88 model IDs are trained on it with the same `ml/train.py` recipe.

**Data is not in the repository** (5.4 GB of archives, owned by the dataset authors). The script below downloads
it from the KAt server and rebuilds the same windows and splits: every archive is size-checked, splits use fixed
seeds, and the library versions are pinned in `requirements-lock.txt`.

### 1. One-time setup

```bash
source .venv/bin/activate                         # the environment from Quick start
sudo apt install libarchive-tools                 # RAR extractor (bsdtar); unrar or p7zip-full also work
```

### 2. GPU (optional, NVIDIA only)

CNNs train on an NVIDIA GPU if TensorFlow can see one; otherwise they train on the CPU (several times slower).
TensorFlow 2.18 brings its own CUDA 12 and cuDNN 9 libraries as pip packages, so no system CUDA toolkit is needed,
only the NVIDIA driver:

```bash
nvidia-smi                                        # must list the GPU; if not: sudo ubuntu-drivers install, reboot
uv pip install "tensorflow[and-cuda]==2.18.1"     # or: pip install ... ; inside the activated venv
python -c "import tensorflow as tf; print(tf.config.list_physical_devices('GPU'))"   # must not be []
```

- `lspci | grep -iE "vga|3d|nvidia"` shows which GPUs the machine has. Intel (Iris Xe) and AMD GPUs are not used
  by this TensorFlow build; the CNNs then run on the CPU.
- An NVIDIA card in `lspci` but a failing `nvidia-smi` means the driver is missing (`sudo ubuntu-drivers install`).
- `nvidia-smi` works but TensorFlow prints `[]`: the `[and-cuda]` extra is missing from the venv (step above).
- Tested on a GeForce MX450 (2 GB): the CNNs fit; TensorFlow memory growth is on, so the card is shared politely.
- The script checks all of this at start-up, says what is missing, and asks before training CNNs on the CPU
  (`--no-gpu` skips the question).

### 3. Run

```bash
python tools/pu/run_pu.py --bearings smoke --recordings 2 --seeds 0   # optional 10-minute trial on 6 bearings
rm -rf results/pu splits/pu data/processed/pu                         # discard the trial before the real run

python tools/pu/run_pu.py                         # download -> prepare -> features -> splits -> train everything
python tools/pu/run_pu.py --status                # progress from another terminal
```

- Trees and MLPs run on CPU worker processes (`--workers`, default cores - 2, at most 6), CNNs on the GPU.
- Every CNN checkpoints after each epoch (`build/pu/ckpt/`). Ctrl-C, a crash or a reboot loses at most one epoch:
  run the same command again to resume. Finished models are never retrained.
- Jobs run seed-major (seeds 0-4), so a stopped run still holds complete seeds. `--seeds 0 1 2` shortens it.
- Reference run (ThinkPad T14 Gen 2i, 4-core Intel CPU + GeForce MX450): trees and MLPs finished within 3 h on
  6 CPU workers; the 400 CNN jobs are the long part (about 16 h on the MX450, estimated from the first 70).
- `--data-only` stops after the splits (to share or inspect the prepared data).

### What it produces

| Path | Content |
|---|---|
| `data/raw/pu/*.rar` | the 32 archives (git-ignored) |
| `data/processed/pu/` | `windows.npy`, `manifest.parquet`, `feat_r1..r4.npy`, `prepare.json` (git-ignored) |
| `splits/pu/` | P1-P4 split files (ids git-ignored), `protocols.json`, `README.md` with the definitions and sizes |
| `results/pu/host/*.json` | one file per model, protocol, fold and seed (same format as `results/host/`) |
| `results/pu/data_counts.csv`, `split_counts.csv`, `run_config.json` | data and split counts, the run's settings |
| `results/pu/logs/`, `results/pu/progress.json` | worker logs and the live status (git-ignored) |

Protocols: **P1** leaky random split; **P2** leave one operating condition out (4 folds); **P3** bearing-disjoint
(4 folds, about a quarter of the healthy, inner-race and outer-race bearings held out each time); **P4** train on
artificial damage, test on real damage. Classes: healthy, inner race, outer race; the three bearings with damage on
both rings (KB23/24/27) are prepared but kept out of the classification splits. Details: `ml/pu_data.py`,
[tools/pu/README.md](tools/pu/README.md). The same code runs on either dataset, selected with `TINYML_DATASET`
(`cwru` by default, `pu` for Paderborn; see `ml/dataset.py`); nothing in the Paderborn run touches CWRU files.

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
