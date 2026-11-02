# Paderborn run (second dataset)

One command downloads the [Paderborn KAt bearing dataset](https://mb.uni-paderborn.de/kat/datacenter), prepares it
exactly like CWRU, and trains all 88 model IDs on it. Trees and MLPs run on CPU worker processes and CNNs on the
GPU. A live dashboard shows progress, and Ctrl-C followed by the same command resumes the run.

```bash
source .venv/bin/activate                        # the project environment (requirements-lock.txt)
python tools/pu/run_pu.py                        # everything: ~5.4 GB download, then training
python tools/pu/run_pu.py --status               # what is done so far
```

## One-time setup

See the main [README](../../README.md#second-dataset-paderborn-real-bearing-damage): Python 3.11 venv, a RAR extractor (`sudo apt install libarchive-tools`) and, for the GPU, `pip install "tensorflow[and-cuda]==2.18.1"` with a working NVIDIA driver. If TensorFlow sees no GPU, the script says why and asks before training the CNNs on the CPU (`--no-gpu` accepts CPU training without asking).

## What it runs

| Stage | Output |
|---|---|
| Download 32 archives (3 at a time, resumable, size-checked) | `data/raw/pu/*.rar` |
| Extract 5 of 20 recordings per bearing and condition; 64 → 12 kHz; CWRU's 5 kHz low-pass; 1024-sample windows | `data/processed/pu/` |
| Features R1–R4 (`ml/features.py`, unchanged) | `data/processed/pu/feat_r*.npy` |
| Splits P1–P4 with leakage checks (`ml/pu_data.py`) | `splits/pu/` |
| Training: 51 architectures × 10 folds × seeds 0–4 (`ml/train.py`, unchanged recipe) | `results/pu/host/*.json` |

Protocols: **P1** leaky random split; **P2** leave one operating condition out (4 folds); **P3** bearing-disjoint
(4 folds, a quarter of the healthy, inner-race and outer-race bearings held out each time); **P4** train on artificial
damage, test on real damage. Classes: healthy, inner race, outer race. The three bearings with damage on both rings
(KB23/24/27) are prepared but kept out of the classification splits.

Jobs run seed-major: all of seed 0 finishes before seed 1 starts, so a run stopped early still has a complete seed.
Every CNN writes a checkpoint after each epoch to `build/pu/ckpt/`, so an interrupted job loses at most one epoch.
The live state goes to `results/pu/progress.json` and the worker logs to `results/pu/logs/`.

## Analysis

After the run, `python ml/pu_analysis.py` writes `results/pu/summary.json`, `host_summary.csv`, `kendall_tau.csv`,
`per_bearing_P3.csv`, the cascade results and the `fig_pu_*` figures used by the paper (section "Second data set").

## Useful options

| Option | Effect |
|---|---|
| `--bearings smoke --recordings 2 --seeds 0` | a few-minute trial on 6 bearings |
| `--seeds 0 1 2` | fewer seeds (shorter run) |
| `--workers N` | CPU worker processes (default: cores − 2, at most 6) |
| `--hours H` | start no new job after H hours |
| `--plain` | log lines instead of the dashboard (e.g. inside `nohup` or `tmux` logging) |
| `--data-only` | stop after the splits |

Nothing here touches the CWRU data or results. The same training code runs on both datasets, selected with
`TINYML_DATASET=pu` (see `ml/dataset.py`).
