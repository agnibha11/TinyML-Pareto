# CONTRACT.md — M0 interface contract

Status: **DRAFT v0.1** — becomes binding when all three sign the table at the bottom (gate M0, due Sun 11 Oct 2026).

This file fixes every interface between the three workstreams, so the three codebases fit together without anyone
reading anyone else's code. If you need to change something here, open a PR that edits this file. All three approve it,
and the version number goes up. Nothing in this file changes silently.

| Owner | Workstream | Owns in this repo |
|---|---|---|
| Agnibha | ML & data | `ml/`, `data/`, `splits/`, `configs/`, `models/`, `handoff/`, `results/host/` |
| Abhinav | Embedded firmware | `firmware/`, `tools/dut.py`, `tools/bundle.py`, `tools/build_flash.py`, `results/fw_metrics.csv` |
| Hridayesh | Measurement & paper | `logger/` (ESP32), `tools/measure.py`, `figs/`, `paper/`, `results/hw_measurements.csv`, `results/session_log.csv` |

---

## 1. Git rules

1. `main` is protected. Nobody pushes to `main` directly.
2. Each person works on their own branch named after them: `agnibha`, `abhinav`, `hridayesh`. For work that runs in
   parallel, use short-lived topic branches: `<name>/<topic>` (e.g. `abhinav/lat-command`).
3. **One reviewer per merge.** Every merge into `main` goes through a pull request that is approved by one
   teammate who is not the author. The reviewer runs the relevant check before approving (§8).
4. Rebase your branch on `main` before opening a PR (`git pull --rebase origin main`). Squash-merge is the default.
5. Commit messages: `<area>: <imperative summary>` — areas: `ml`, `data`, `fw`, `tools`, `logger`, `figs`, `paper`,
   `contract`, `repo`. Example: `fw: add LAT command with DWT timing`.
6. Never commit: raw or processed data (`data/`), build output (`build/`, `*.elf`, `*.bin`, `*.hex`), virtual
   environments, notebooks with outputs, credentials. `.gitignore` enforces most of this.
7. Results files under `results/` are written only by scripts. Never edit a results CSV by hand. A bad row is marked
   `rejected=1` and re-measured; it is not deleted.
8. Tag the data freeze: `git tag m6-freeze` on the commit that holds the final `hw_measurements.csv`.

## 2. Classes (frozen)

`handoff/classes.json` is the only source for label integers. Summary:

| class10 | name | class4 | fault_type | fault_size_in | CWRU files (0/1/2/3 HP) |
|---|---|---|---|---|---|
| 0 | N | 0 | N | 0 | 97, 98, 99, 100 |
| 1 | IR007 | 1 | IR | 0.007 | 105, 106, 107, 108 |
| 2 | B007 | 2 | B | 0.007 | 118, 119, 120, 121 |
| 3 | OR007 | 3 | OR | 0.007 | 130, 131, 132, 133 |
| 4 | IR014 | 1 | IR | 0.014 | 169, 170, 171, 172 |
| 5 | B014 | 2 | B | 0.014 | 185, 186, 187, 188 |
| 6 | OR014 | 3 | OR | 0.014 | 197, 198, 199, 200 |
| 7 | IR021 | 1 | IR | 0.021 | 209, 210, 211, 212 |
| 8 | B021 | 2 | B | 0.021 | 222, 223, 224, 225 |
| 9 | OR021 | 3 | OR | 0.021 | 234, 235, 236, 237 |

Loads: 0/1/2/3 HP = 1797/1772/1750/1730 rpm (nominal). Outer race = 6:00 position only.
P1 and P2 use `class10`; P3 uses `class4`.

## 3. Data conventions

- Sampling rate after preprocessing: **12 000 Hz** for every window. The normal files (97–100) are 48 kHz and are decimated by 4
  (`scipy.signal.decimate(x, 4, ftype='fir')`), with `fs_original = 48000` recorded in the manifest.
- **Common low-pass (proposed, needs sign-off):** after decimation, every recording (normal and fault) is filtered with
  the same zero-phase 255-tap FIR low-pass at 5 kHz. Without it, the recorder's anti-alias roll-off above ~5.5 kHz exists
  only in the fault files, and one band feature separates normal from every fault window: a sampling-rate artefact,
  not physics. `results/data_files.csv` records the cutoff used.
- Each file is loaded by its explicit key `X{nnn}_DE_time` (`nnn` = zero-padded file number). 99.mat also contains
  X098 keys (a copy of 98.mat), and these are never loaded.
- Window = **1024 samples**, float32, in raw accelerometer units (g). Windows are cut at stride 512. A window with
  `start_idx % 1024 == 0` is a non-overlapping window; P2/P3 test sets use only these.
- `data/processed/windows.npy`: shape `(N, 1024)`, float32. Row `i` = `window_id` `i`.
- `data/processed/manifest.parquet`: one row per window:

| column | dtype | meaning |
|---|---|---|
| window_id | int32 | row index into `windows.npy` |
| file_id | int16 | CWRU record number |
| class10 | int8 | per `classes.json` |
| class4 | int8 | 0 N, 1 IR, 2 B, 3 OR |
| fault_type | str | N / IR / B / OR |
| fault_size_in | float32 | 0 for normal |
| load_hp | int8 | 0–3 |
| rpm | float32 | from the file's `X{nnn}RPM`, else nominal |
| start_idx | int32 | first sample of the window in the 12 kHz recording |
| fs_original | int32 | 12000 or 48000 (before decimation) |

## 4. Model IDs

Format: `<family>-<input>-<size>-<precision>`, lowercase, regex
`^(dt|rf|mlp|cnn)-(r[0-3])-([a-z0-9]+)-(fp32|int8|int8qat|int8ref)$`.

| token | values |
|---|---|
| family | `dt` decision tree, `rf` random forest, `mlp`, `cnn` |
| input | `r0` raw window, `r1` 10 stats, `r2` r1 + 16 log bands, `r3` 256-bin log spectrum |
| size | dt: `d<depth>` · rf: `t<trees>d<depth>` · mlp: `h<u1>[x<u2>]` · cnn: `w<width×100, 3 digits>d<blocks>` |
| precision | `fp32`, `int8` (PTQ), `int8qat` (QAT), `int8ref` (the `int8` model built on TFLM reference kernels — ablation) |

Examples: `dt-r2-d8-fp32`, `rf-r1-t10d6-fp32`, `mlp-r2-h32x32-int8`, `cnn-r0-w050d4-int8qat`, `cnn-r0-w050d4-int8ref`.
One ID = one row in every table. IDs are never reused for a different model.

## 5. Handoff layout (Agnibha → Abhinav, Agnibha → Hridayesh)

Everything Abhinav needs to build firmware is under `handoff/` and `models/`. He reads them and never edits them.

```
handoff/
  classes.json                 frozen class map (this contract)
  features_spec.md             R0–R3 formulas, word for word (Agnibha)
  hann_1024.h                  const float hann[1024], generated in Python
  norm_constants.h / .npz      R0 global mu/sigma; per-feature mean/std for R1, R2, R3 (P2 fold 0 training set)
  ops_union.txt                union of TFLite ops over all NN models, one op per line
  test_vectors/
    ids.npy                    100 window_ids (10 per class), fixed
    raw.npy                    (100, 1024) float32
    r1.npy r1_norm.npy         (100, 10)
    r2.npy r2_norm.npy         (100, 26)
    r3.npy r3_norm.npy         (100, 256)
    logits/<model_id>.npy      host TFLite outputs (dequantized) for the same windows
    stored_inputs.npy / .json  the 5 windows behind INPUT 0–4 + provenance (P2 fold-0 test set)
models/
  registry.csv                 one row per deployable model (§6.1)
  <model_id>/
    model.tflite | model.h     NN: .tflite + 16-byte-aligned C array; tree: emlearn header (float, method='inline')
    meta.json                  same fields as the registry row
```

`ml/export_handoff.py` writes `handoff/hann_1024.*`, `handoff/test_vectors/stored_inputs.*` and the matching
`firmware/bench/stored_inputs.cpp`. Abhinav's `bundle.py` generates `firmware/bench/models/bundle_<b>.h` from
`models/`. None of these generated files is ever edited by hand.

## 6. File formats

All CSVs: UTF-8, comma-separated, header row, `.` decimal point, no index column. Units appear in the column names.

### 6.1 `models/registry.csv` (Agnibha writes)

`model_id, family, input_rep, precision, runtime, n_classes, input_scale, input_zero_point, output_scale, output_zero_point, params, macs, ops, protocol_trained, fold, seed, artifact, feasible`

- `runtime` ∈ `tflm-cmsis`, `tflm-ref`, `emlearn`. `ops` = semicolon-separated op names (empty for trees).
- Scale/zero-point fields are empty for fp32 models and trees.
- `artifact` = path relative to the repo root. `feasible` = 0 if the C array exceeds 900 KB (logged, never deployed).

### 6.2 Host metrics (Agnibha writes)

- `results/host/<model_id>__<P>__f<fold>__s<seed>.json`: `{model_id, protocol, fold, seed, snr_db, f1_macro, accuracy, confusion, n_test}`
- `results/host_summary.csv`: `model_id, protocol, snr_db, n_runs, f1_macro_mean, f1_macro_std, accuracy_mean, accuracy_std`

### 6.3 `results/fw_metrics.csv` (Abhinav writes)

`model_id, runtime, bundle, flash_model_B, arena_used_B, frontend_ram_B, ram_model_peak_B, lat_inf_med_us, lat_inf_min_us, lat_inf_max_us, lat_fe_med_us, git_hash`

### 6.4 `results/hw_measurements.csv` (Hridayesh writes, one row per window)

`model_id, session, input_j, mode, N, t_window_us_dut, t_window_us_logger, E_window_uJ, P_mean_uW, P_std_uW, V_bus_mV, n_samples, ovf, P_idle_uW, fw_hash, logger_hash, timestamp, rejected`

- `mode` ∈ `IDLE`, `SLEEP`, `RUN`, `FRONT`, `E2E`. For IDLE/SLEEP rows, `input_j` is empty and `N = 0`.
- `timestamp` = ISO-8601 with timezone. `rejected` ∈ {0, 1}.
- Derived quantities (E per inference, ΔE, CV) are computed downstream from these columns. They are never stored here.

### 6.5 `results/session_log.csv` (Hridayesh writes)

`session, date, room_temp_C, usb_port, cable, shunt_ohm_measured, logger_hash, fw_hash, notes`

## 7. Serial protocol, sync pin, phase definitions

### 7.1 Link

- Nano 33 BLE USB CDC, 115200 baud (nominal), 8N1.
- Host → board: one ASCII command per line, terminated by `\n`, at most 63 characters, uppercase keyword,
  single-space-separated integer arguments.
- Board → host: one or more lines terminated by `\r\n`. **Every command ends with exactly one final line that starts with
  `OK` or `ERR`.** A final `OK` line may carry `key=value` pairs separated by single spaces.
- `ERR <CODE> <free text>` codes: `UNKNOWN` (unknown command), `ARG` (bad or missing argument), `NOSEL` (no model selected),
  `RANGE` (index out of range), `ALLOC` (AllocateTensors failed), `INVOKE` (inference failed), `IO` (short binary read),
  `NOTIMPL` (command not implemented in this firmware build).
- The board never prints anything unless it is answering a command. It sends no banner at boot.

### 7.2 Commands

| Command | Board action | Final line (sent after D2 is LOW) |
|---|---|---|
| `PING` | none | `OK bench=<git-hash> n_models=<K>` |
| `LIST` | sends K lines `M <k> <model_id> <runtime>` | `OK n=<K>` |
| `SEL <k>` | set up model k: allocate, cache tensor pointers | `OK k=<k> arena_used=<bytes>` (trees: `arena_used=0`) |
| `INPUT <j>` | select stored window j ∈ 0..4 as the fixed input | `OK j=<j>` |
| `IDLE <ms>` | D2 HIGH, `__WFI()` loop for ms, D2 LOW | `OK us=<window_us>` |
| `SLEEP <ms>` | D2 HIGH, `rtos::ThisThread::sleep_for(ms)` with peripherals off, D2 LOW | `OK us=<window_us>` |
| `RUN <N>` | front end once (outside the window); 1 warm-up inference; D2 HIGH; N inferences; D2 LOW | `OK n=<N> us=<window_us> cls=<class>` |
| `FRONT <N>` | 1 warm-up; D2 HIGH; N front-end calls; D2 LOW | `OK n=<N> us=<window_us>` |
| `E2E <N>` | 1 warm-up; D2 HIGH; N × (front end + inference); D2 LOW | `OK n=<N> us=<window_us> cls=<class>` |
| `LAT` | for each stored window: 101 timed inferences and 101 timed front-end calls (DWT) | `OK inf_med_cyc=<> inf_min_cyc=<> inf_max_cyc=<> fe_med_cyc=<> fe_min_cyc=<> fe_max_cyc=<>` |
| `VERIFY` | board sends `RDY 4096`; host sends 4096 bytes (1024 × float32 LE); front end + inference | `OK cls=<k> out=<v0>,<v1>,…` (dequantized outputs, `%.6g`) |

- `N` is a 32-bit unsigned integer ≥ 1. `ms` is 1..600000.
- `<window_us>` = `micros()` read just after D2 goes LOW minus `micros()` read just before D2 goes HIGH.
- `LAT` statistics: for each of the 5 stored windows take the median of 101 runs, then report the median of the 5 medians
  (`*_med_cyc`) and the min/max over the 5 medians (`*_min_cyc`, `*_max_cyc`). µs = cycles / 64.
- Host timeouts: 2 s for commands without a window; for windowed commands, the expected window length + 5 s.

### 7.3 Sync pin (D2)

- D2 is a push-pull output and LOW at rest. It is LOW from the end of `setup()` onward.
- D2 goes HIGH immediately before the first measured iteration and LOW immediately after the last one.
- While D2 is HIGH: no serial I/O, no allocation, no model setup, no `delay()`, no logging.
- ESP32 logger: sync input on **GPIO 4**, INA219 on I2C (SDA 21, SCL 22, 400 kHz). The Nano, ESP32 and INA219 grounds are all tied together.
- Energy windows last ≥ 10 s. The host picks `N = ceil(10 s / latency)` from `LAT`.

### 7.4 ESP32 logger window line

At every falling edge of D2 the logger prints exactly one line:

```
W rise_us=<> fall_us=<> n=<samples> E_uJ=<> Pmean_uW=<> Pstd_uW=<> Vbus_mV=<> ovf=<count>
```

`measure.py` pairs each board reply with the next `W` line. A window is rejected if the board and logger durations differ by
more than 0.1 % or if `ovf > 0`. A rejected window is retried once.

### 7.5 What counts as each phase

| Phase | FRONT | RUN (inference) | E2E |
|---|---|---|---|
| Front end (R0 normalize, or R1/R2/R3 features + z-score) | yes | no | yes |
| Input quantization (float → int8) | no | yes | yes |
| `interpreter.Invoke()` / `<id>_predict()` | no | yes | yes |
| Argmax on output | no | yes | yes |
| `AllocateTensors()`, model selection | no | no | no |

The paper states this table verbatim.

## 8. Checks the reviewer runs before approving a merge

| Area | Command | Must pass |
|---|---|---|
| data | `python ml/prepare_data.py --check` | 40 files, window counts match `results/data_counts.csv` |
| splits | `python ml/splits.py --check` | all assertions |
| firmware | `python tools/build_flash.py <bundle> --compile-only` | builds with pinned versions |
| host tools | `python -m pytest tests/` | green |
| measurement | `python tools/measure.py --dry-run` | writes a complete dummy CSV |

## 9. Sign-off

| Name | Role | Signed (date) | Commit |
|---|---|---|---|
| Agnibha | ML & data | | |
| Abhinav | Embedded firmware | | |
| Hridayesh | Measurement & paper | | |

Version history: v0.1 (draft, 1 Oct 2026).
