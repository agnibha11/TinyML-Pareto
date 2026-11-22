# CONTRACT.md — M0 interface contract

Status: **v1.1** (21 Nov 2026): v1.0-rc4 plus the §7.6 cascade commands.

This file fixes every interface between the three workstreams, so the three codebases fit together without anyone
reading anyone else's code. If you need to change something here, open a PR that edits this file. All three approve it,
and the version number goes up. Nothing in this file changes silently.

| Owner | Workstream | Owns in this repo |
|---|---|---|
| Agnibha | ML & data | `ml/`, `data/`, `splits/`, `configs/`, `models/`, `handoff/`, `results/<dataset>/host/`, `results/<dataset>/analysis/host_summary.csv`, `results/<dataset>/data/` |
| Abhinav | Embedded firmware | `firmware/`, `tools/dut.py`, `tools/bundle.py`, `tools/build_flash.py`, `results/<dataset>/hardware/fw_metrics.csv` |
| Hridayesh | Measurement & paper | `logger/` (ESP32), `tools/measure.py`, `figs/`, `paper/`, `results/<dataset>/hardware/hw_measurements.csv`, `results/<dataset>/hardware/session_log.csv` |

## M0 checklist: where each item is fixed

| # | M0 item | Proposed by | Section | Machine-checked by |
|---|---|---|---|---|
| 1 | Serial command protocol, incl. "never prints unless replying" | Abhinav | §7.1, §7.2 | `tests/test_contract.py` (every §7.2 command runs on the firmware core), `tests/test_host_tools.py` |
| 2 | Sync-pin contract (D2) | Abhinav | §7.3 | `test_windows_and_sync_pin`: exactly one HIGH/LOW pair per windowed command |
| 3 | Inference-phase table | Abhinav | §7.5 | implemented in `firmware/bench/bench_core.cpp` (`cmd_window`, `cmd_lat`) |
| 4 | Class mapping `classes.json` | Agnibha | §2 | `test_contract.py`: §2 table == `handoff/classes.json` == `ml/prepare_data.py` file table |
| 5 | Model-registry schema | Agnibha | §6.1 | `test_contract.py`: §6.1 == `models/registry.csv` header == `tools/contract.py` |
| 6 | Model-ID format | Agnibha | §4 | `test_contract.py`: regex accepts every §4 example, rejects malformed IDs |
| 7 | Results CSV schema (`hw_measurements.csv`) | Hridayesh | §6.4, §6.5 | `test_contract.py` + `test_measure_dry_run_matches_contract` |
| 8 | Logger window-line format | Hridayesh | §7.4 | `test_w_line_parser`; `test_contract.py` parses the §7.4 example |
| 9 | Handoff directory layout | all | §5 | `test_contract.py`: every committed `handoff/` file is listed in §5 |
| 10 | Git rules (own branch, one reviewer per merge) | all | §1 | GitHub branch protection on `main` + required CI check `tests` (§1, rules 9–10) |

---

## 1. Git rules

1. `main` is protected. Nobody pushes to `main` directly.
2. Each person works on their own branch named after them: `agnibha`, `abhinav`, `hridayesh`. For work that runs in
   parallel, use short-lived topic branches: `<name>/<topic>` (e.g. `abhinav/lat-command`).
3. **One reviewer per merge.** Every merge into `main` goes through a pull request that is approved by one
   teammate who is not the author. The reviewer runs the relevant check before approving (§8).
4. Before opening a PR, merge the latest `main` into your branch (`git fetch origin && git merge origin/main`) and
   run the tests. PRs are merged with **"Create a merge commit"**, never squash or rebase. That way the personal branches
   keep sharing history with `main`, and nobody ever needs to force-push.
5. Commit messages: `<area>: <imperative summary>` — areas: `ml`, `data`, `fw`, `tools`, `logger`, `figs`, `paper`,
   `contract`, `repo`. Example: `fw: add LAT command with DWT timing`.
6. Never commit: raw or processed data (`data/`), build output (`build/`, `*.elf`, `*.bin`, `*.hex`), virtual
   environments, notebooks with outputs, credentials. `.gitignore` enforces most of this.
7. Results files under `results/` are written only by scripts (layout: `results/README.md`; `<dataset>` is `cwru` or `pu`). Never edit or delete a results row by hand.
   - A window rejected automatically gets `rejected=1` from `measure.py`.
   - A window you want to discard after review is simply measured again.
   - The analysis uses, for each (model_id, session, input_j, mode), the **last row with `rejected=0`**.
8. Tag the data freeze: `git tag m6-freeze` on the commit that holds the final `hw_measurements.csv`.
9. Rule 3 is enforced by GitHub, not by memory. The repo owner (agnibha11) sets this up once, under **Settings → Branches →
   Add branch ruleset** (or "Add rule") for `main`:
   - require a pull request before merging, with **1 approval**;
   - require the status check **`tests`** to pass (rule 10);
   - block force pushes and deletions;
   - do not allow bypassing.

   Leave "dismiss stale approvals" off, so the sign-off commits in §9 don't erase earlier approvals. The repo is public,
   so rulesets are free.

   PR #1 (`abhinav` → `main`) was merged without a review. From M0 on, that is no longer possible.
10. CI: `.github/workflows/tests.yml` runs `pytest tests/` on every push and PR (Ubuntu, Python 3.11, g++). It compiles
    the firmware core for the PC, so a protocol change that breaks `dut.py` fails before review.

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
- **Common low-pass (adopted in v1.0; signing this contract accepts it):** after decimation, every recording (normal and fault) is filtered with
  the same zero-phase 255-tap FIR low-pass at 5 kHz. Without it, the recorder's anti-alias roll-off above ~5.5 kHz exists
  only in the fault files, and one band feature separates normal from every fault window: a sampling-rate artefact,
  not physics. `results/cwru/data/data_files.csv` records the cutoff used, and `--lpf-hz 0` exists only for an ablation.
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
`^(dt|rf|mlp|cnn)-(r[0-4])-([a-z0-9]+)-(fp32|int8|int8qat|int8ref)$`.

| token | values |
|---|---|
| family | `dt` decision tree, `rf` random forest, `mlp`, `cnn` |
| input | `r0` raw window, `r1` 10 stats, `r2` r1 + 14 log bands (0–5.25 kHz, v1.1 of the feature spec), `r3` 256-bin log spectrum, `r4` r1 without its mean + 32 log envelope-spectrum bins (11.7–375 Hz; v1.2 of the feature spec) |
| size | dt: `d<depth>` · rf: `t<trees>d<depth>` · mlp: `h<u1>[x<u2>]` · cnn: `w<width×100, 3 digits>d<blocks>` |
| precision | `fp32`, `int8` (PTQ), `int8qat` (QAT), `int8ref` (the `int8` model built on TFLM reference kernels — ablation) |

Examples: `dt-r2-d8-fp32`, `rf-r1-t10d6-fp32`, `mlp-r2-h32x32-int8`, `cnn-r0-w050d4-int8qat`, `cnn-r0-w050d4-int8ref`.

Family rules (enforced by `tools/contract.py::parse_model_id`):
- Trees (`dt`, `rf`) take features (`r1`–`r3`) and are `fp32` only.
- CNNs take the raw window (`r0`) only.
- MLPs take `r1`–`r3`.
- `int8ref` is only valid for `cnn`.

**What an ID names.** An ID names an *architecture configuration*.
- **Deployed and registered instance:** exactly one per ID — the model trained on **P2 fold 0, seed 0, class10 labels**.
  Only that instance gets firmware, energy and memory numbers.
- **Other instances:** every other protocol, fold and seed (including the 4-class P3 heads) is evaluated on the host
  only, under the same ID, keyed by `(model_id, protocol, fold, seed)` in `results/<dataset>/host/`.
- **Hardware cost:** taken from the deployed instance. The 4-class head differs only in its last layer, a difference
  the paper states.

One ID = one row in `registry.csv` and `fw_metrics.csv`. IDs are never reused for a different configuration.

**C names.**
- **`c_name`:** the model ID with `-` replaced by `_` (e.g. `dt_r2_d8_fp32`).
- **NN models:** `alignas(16) const unsigned char g_model_<c_name>[]` and `const unsigned int g_model_<c_name>_len`.
- **Trees:** the emlearn function `int32_t <c_name>_predict(const float *features, int32_t n_features)`.
- **Bench table:** `bundle.py` builds it from these symbols only.

## 5. Handoff layout (Agnibha → Abhinav, Agnibha → Hridayesh)

Everything Abhinav needs to build firmware is under `handoff/` and `models/`. He reads them and never edits them.

```
handoff/
  classes.json                 frozen class map (this contract)
  features_spec.md             R0–R4 formulas, word for word (Agnibha)
  hann_1024.h / .npy           Hann window constants (g_hann_1024), generated by ml/export_handoff.py
  hann_fx.h                    q15/q31 Hann windows (N = 1024 and 256) for the fixed-point front end
                               (firmware/bench/frontend_fx.c), generated by ml/export_handoff.py
  norm_constants.h / .npz      P2 fold-0 training statistics; C symbols (npz keys = same names without g_):
                               g_r0_mu, g_r0_sigma (float); g_r1_mean[10], g_r1_std[10];
                               g_r2_mean[24], g_r2_std[24]; g_r3_mean[256], g_r3_std[256];
                               g_r4_mean[41], g_r4_std[41]
  ops_union.txt                union of TFLite ops over all NN models, one op per line
  test_vectors/
    ids.npy                    100 window_ids (10 per class), fixed
    raw.npy                    (100, 1024) float32
    r0_norm.npy                (100, 1024) R0-normalised windows (the CNN input before quantisation)
    r1.npy r1_norm.npy         (100, 10)
    r2.npy r2_norm.npy         (100, 24)
    r3.npy r3_norm.npy         (100, 256)
    r4.npy r4_norm.npy         (100, 41)
    outputs/<model_id>.npy     host outputs for the same windows, in exactly the form VERIFY returns (§7.2)
    stored_inputs.npy / .json  the 5 windows behind INPUT 0–4 + provenance (P2 fold-0 test set)
  cascade/<ds>/                cascade bundle data (§7.6), written by ml/cascade_export.py
    gate_params.h / .json      gate parameters (tree, Mahalanobis, selfcal rho), fitted on the deployed split
    pool_int16.npy, pool.csv   (P, 1024) int16 window pool (healthy first) + provenance
    streams.csv                stream, position, pool index, fault flag, expected alarm of every gate
    gverify_int16.npy          (V, 1024) int16 GVERIFY vectors
    gverify.csv                expected fx_r1 features, scores, alarms and threshold margins per vector
    summary.json               host gate rates on the deployed test split, stream alarm counts, pool size
models/
  registry.csv                 one row per deployable model (§6.1)
  <model_id>/
    model.tflite | model.h     NN: .tflite + 16-byte-aligned C array; tree: emlearn header (float, method='inline')
    meta.json                  same fields as the registry row
```

`ml/export_handoff.py` writes `handoff/hann_1024.*`, `handoff/hann_fx.h`, `handoff/test_vectors/stored_inputs.*` and the matching
`firmware/bench/stored_inputs.cpp`. Abhinav's `bundle.py` generates `firmware/bench/models/bundle_<b>.h` from
`models/`. None of these generated files is ever edited by hand.

## 6. File formats

All CSVs: UTF-8, comma-separated, header row, `.` decimal point, no index column. Units appear in the column names.

### 6.1 `models/registry.csv` (Agnibha writes)

`model_id, family, input_rep, config, precision, runtime, n_classes, input_scale, input_zero_point, output_scale, output_zero_point, params, macs, ops, protocol_trained, fold, seed, artifact, feasible`

- `family`, `input_rep`, `config` and `precision` are the four tokens of `model_id` (§4), stored separately so tables can be
  grouped without parsing. `config` is the size token (`d8`, `t10d6`, `h32x32`, `w050d4`). The full hyper-parameters
  live in `configs/<model_id>.yaml`.
- `runtime` ∈ `tflm-cmsis`, `tflm-ref`, `emlearn`. `ops` = semicolon-separated op names (empty for trees).
- Scale/zero-point fields are empty for fp32 models and trees.
- `artifact` = path relative to the repo root.
- `feasible` = 1 if the model's bundle **links** (`--compile-only`) within the Nano's 983,040 B application flash and
  `SEL` succeeds (the arena fits RAM). Otherwise it is 0: logged, kept in the paper as an infeasible point, and never
  measured. A C array above 900 KB is rejected early without building.

### 6.2 Host metrics (Agnibha writes)

- `results/<dataset>/host/<model_id>__<P>__f<fold>__s<seed>.json`: `{model_id, protocol, fold, seed, snr_db, f1_macro, accuracy, confusion, n_test}`
- `results/<dataset>/analysis/host_summary.csv`: `model_id, protocol, snr_db, n_runs, f1_macro_mean, f1_macro_std, accuracy_mean, accuracy_std`

### 6.3 `results/<dataset>/hardware/fw_metrics.csv` (Abhinav writes)

`model_id, runtime, bundle, flash_model_B, arena_used_B, frontend_ram_B, ram_model_peak_B, lat_inf_med_us, lat_inf_min_us, lat_inf_max_us, lat_fe_med_us, git_hash`

### 6.4 `results/<dataset>/hardware/hw_measurements.csv` (Hridayesh writes, one row per window)

`model_id, session, input_j, mode, N, t_window_us_dut, t_window_us_logger, E_window_uJ, P_mean_uW, P_std_uW, V_bus_mV, n_samples, ovf, P_idle_uW, fw_hash, logger_hash, timestamp, rejected`

- `mode` ∈ `IDLE`, `SLEEP`, `RUN`, `FRONT`, `E2E`.
- **IDLE rows:** `input_j` is empty and `N = 0`. `model_id` is the model selected at the time; the board is idle either
  way, but this ties the idle power to its block.
- **SLEEP rows:** `model_id` and `input_j` are empty, and `N = 0`.
- **`P_idle_uW`:** the `P_mean_uW` of the accepted IDLE window measured immediately before, in the same
  (model_id, input_j) block of the same session. It is empty on IDLE and SLEEP rows, and when that IDLE window was
  rejected twice.
- **Logger fields:** when no valid `W` line arrived (§7.4), `t_window_us_logger` through `ovf` are empty and
  `rejected = 1`.
- **Board field:** when the board's reply arrived damaged (§7.4), `t_window_us_dut` is empty and `rejected = 1`; the
  logger fields hold that window's `W` line if one arrived.
- `timestamp` = ISO-8601 with timezone. `rejected` ∈ {0, 1}.
- Derived quantities (E per inference, ΔE, CV) are computed downstream from these columns. They are never stored here.

### 6.5 `results/<dataset>/hardware/session_log.csv` (Hridayesh writes)

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
  `NOTIMPL` (command not implemented in this firmware build), `NOCAL` (§7.6: gate 5 used before a successful `CALIB`).
- If several errors apply, `UNKNOWN` wins: an unknown keyword is reported before any argument error.
- The board never prints anything unless it is answering a command. It sends no banner at boot. Only `LIST` sends
  lines before its final line, and `VERIFY` sends its `RDY` handshake. Any other line is a protocol violation, and
  `dut.py` raises an error on it.
- **Resynchronisation:** after any host-side timeout, the host
  1. waits until the line has been silent for 200 ms;
  2. sends `PING`;
  3. discards every line until one starting `OK bench=` arrives (a board still busy with a long window answers once it
     finishes);
  4. only then reports the original error.

  If even that fails, power-cycle the board. `dut.py` implements this.
- **Echo check:** the host checks that the echoed `k`, `j` and `n` equal what it sent.

### 7.2 Commands

| Command | Board action | Final line (sent after D2 is LOW) |
|---|---|---|
| `PING` | none | `OK bench=<git-hash> n_models=<K>` |
| `LIST` | sends K lines `M <k> <model_id> <runtime>` | `OK n=<K>` |
| `SEL <k>` | set up model k: allocate, cache tensor pointers | `OK k=<k> arena_used=<bytes>` (trees: `arena_used=0`) |
| `INPUT <j>` | select stored window j ∈ 0..4 as the fixed input | `OK j=<j>` |
| `IDLE <ms>` | D2 HIGH, `__WFI()` loop for ms, D2 LOW | `OK us=<window_us>` |
| `SLEEP <ms>` | D2 HIGH, `rtos::ThisThread::sleep_for(ms)`, D2 LOW. USB CDC and the `micros()` timer stay active, so this is **RTOS thread sleep** (the board's practical floor), not nRF52840 System OFF or deep sleep; the paper calls it "thread sleep" | `OK us=<window_us>` |
| `RUN <N>` | front end once (outside the window); 1 warm-up inference; D2 HIGH; N inferences; D2 LOW | `OK n=<N> us=<window_us> cls=<class>` |
| `FRONT <N>` | 1 warm-up; D2 HIGH; N front-end calls; D2 LOW | `OK n=<N> us=<window_us>` |
| `E2E <N>` | 1 warm-up; D2 HIGH; N × (front end + inference); D2 LOW | `OK n=<N> us=<window_us> cls=<class>` |
| `LAT [<reps>]` | for each stored window: `reps` timed inferences and `reps` timed front-end calls (DWT); `reps` 1..101, default 101 | `OK inf_med_cyc=<> inf_min_cyc=<> inf_max_cyc=<> fe_med_cyc=<> fe_min_cyc=<> fe_max_cyc=<> reps=<reps>` |
| `VERIFY` | board sends `RDY 4096`; the host sends the 4096 bytes (1024 × float32 LE) only after `RDY`; the board waits ≤ 2 s for them, otherwise it discards input for ≥ 1 s and until 50 ms of silence, then replies `ERR IO`; then front end + inference | `OK cls=<k> out=<v0>,<v1>,…` |

- `N` is a 32-bit unsigned integer ≥ 1. `ms` is 1..600000.
- `<window_us>` = `micros()` read just after D2 goes LOW minus `micros()` read just before D2 goes HIGH.
- `LAT` statistics: for each of the 5 stored windows take the median of `reps` runs (the host sends
  `reps = clamp(ceil(60 s / t), 11, 101)`, `t` = one front end + inference from an `E2E 1` probe; one call must stay
  below 2^32 cycles = 67.1 s), then report the median of the 5 medians
  (`*_med_cyc`) and the min/max over the 5 medians (`*_min_cyc`, `*_max_cyc`). µs = cycles / 64.
- **VERIFY outputs:**
  - Contents: the model's output tensor, dequantized `(q − zero_point) × scale` for int8. NN models end in **Softmax**,
    so these are class probabilities. Trees return a one-hot vector of the predicted class.
  - Number format: fixed 6 decimals (`0.012345`), with `nan`, `inf` and `-inf` for non-finite values, and finite
    values clamped to ±2000000000.000000.
  - Agreement test (M4): the classes must match on ≥ 99 % of windows (int8) or ≥ 99.5 % (fp32). Outputs must match
    the host's `handoff/test_vectors/outputs/<model_id>.npy` within one output quantization step (int8) or 1e-4
    absolute (fp32).
- **Host timeouts** (`tools/dut.py`):
  - 2 s for `PING`, `LIST`, `SEL` and `INPUT`.
  - `LAT`: 600 s, or 1.5 × 5 × (reps + 1) × t + 30 s when the probe time `t` is known, whichever is larger.
  - 10 s for `VERIFY`.
  - Windowed commands: 1.5 × the expected command time + 5 s, where the expected command time = (N + 1) × the
    latency from the last `LAT` (N calls in the window plus the warm-up call before it). Before any `LAT`, 605 s.
  - `IDLE`/`SLEEP`: 1.5 × ms + 5 s.

### 7.3 Sync pin (D2)

- D2 is a push-pull output and LOW at rest. It is LOW from the end of `setup()` onward.
- D2 goes HIGH immediately before the first measured iteration and LOW immediately after the last one.
- While D2 is HIGH: no serial I/O, no allocation, no model setup, no `delay()`, no logging.
- ESP32 logger: sync input on **GPIO 4**, INA219 on I2C (SDA 21, SCL 22, 400 kHz). The Nano, ESP32 and INA219 grounds are all tied together.
- **D2 floats while the Nano resets, sits in the bootloader or is being uploaded.**
  - GPIO 4 is configured `INPUT_PULLDOWN`, plus an external 100 kΩ resistor to GND.
  - The logger ignores HIGH pulses shorter than **100 ms** and prints no `W` line for them.
  - Every real window is ≥ 10 s.
- Energy windows last ≥ 10 s. The host picks `N = ceil(10 s / latency)` from `LAT`.

### 7.4 ESP32 logger window line

At every falling edge of D2 the logger prints exactly one line:

```
W rise_us=<> fall_us=<> n=<samples> E_uJ=<> Pmean_uW=<> Pstd_uW=<> Vbus_mV=<> ovf=<count>
```

Example (values made up): `W rise_us=1203 fall_us=10004410 n=9434 E_uJ=239908.014 Pmean_uW=23983.110 Pstd_uW=151.882 Vbus_mV=4951.2 ovf=0`

Definitions. Samples i = 1..n lie inside [rise, fall]; P_i = V_bus,i × I_i with I_i = V_shunt,i / R_shunt (measured R).
- `E_uJ` = Σ P_i · Δt_i, where Δt_i is the time from the previous sample (or from `rise_us` for the first).
- `Pmean_uW` = `E_uJ` / ((fall_us − rise_us) / 10⁶): the time-weighted mean, so E = Pmean × duration exactly.
- `Pstd_uW` = population standard deviation of the P_i.
- `Vbus_mV` = mean bus voltage over the window.
- `ovf` = the number of samples whose INA219 overflow flag (bus register bit 0) was set, **plus** the number of samples
  the logger dropped (ring-buffer overrun). Any non-zero value rejects the window.

Rules for the line:
- Integers for `rise_us`, `fall_us`, `n` and `ovf`; decimals for the rest.
- Single spaces between fields, and the keys in exactly this order.
- Timestamps come from `esp_timer_get_time()`.
- Only `W` lines start with `W `. Any other logger output (e.g. STREAM mode) must not.
- The logger's USB serial runs at 115200 baud, and the line is printed within 500 ms of the falling edge.

`measure.py` pairs each board reply with the next `W` line, waiting up to 3 s. A window is rejected if:
- no valid `W` line arrives in time;
- the board and logger durations differ by more than 0.1 %; or
- `ovf > 0`.

A rejected window is retried once. After a second rejection the campaign continues, and that window is re-measured
before M6 (see §1, rule 7).

A **damaged board reply** (an empty or unexpected line, or a malformed or missing field; seen four times on 18 Nov 2026
with no USB drop, the start of the line missing) is never parsed for values. `dut.py` resynchronises the link (§7.1)
and raises `DUTReplyError`. For a windowed command, `measure.py` still reads that window's `W` line, so it cannot pair
with the next window, writes the attempt as rejected, and measures the window again. Setup commands (`SEL`, `INPUT`,
`LAT`, the `E2E 1` probe) are repeated, at most twice.

### 7.5 What counts as each phase

| Phase | FRONT | RUN (inference) | E2E |
|---|---|---|---|
| Front end (R0 normalize, or R1/R2/R3/R4 features + z-score) | yes | no | yes |
| Input quantization (float → int8) | no | yes | yes |
| `interpreter.Invoke()` / `<c_name>_predict()` | no | yes | yes |
| Argmax on output | no | yes | yes |
| `AllocateTensors()`, model selection | no | no | no |

The paper states this table verbatim.

### 7.6 Cascade commands (v1.1, 21 Nov 2026)

Only the **cascade bundle** of each dataset implements these (`tools/bundle.py --cascade`, bundle 90, built with
`-DBENCH_CASCADE=1`; data from `handoff/cascade/<ds>/`, `ml/cascade_export.py`; code in
`firmware/bench/bench_cascade.cpp`). Every other bundle answers them with `ERR NOTIMPL`. All §7.1 and
§7.3 rules apply.
- Setup always happens outside the window: gate selection, the int16 copy of the stored input, model setup, the
  warm-up call.
- Every timed command does one warm-up of its body, then D2 HIGH, the loop, D2 LOW.
- Every timed reply carries `us=<window_us>` as defined in §7.2.
- Gate math: `firmware/bench/cascade.c`. Study design: `docs/cascade_preregistration.md`.

**Data on the board.**
- The gate parameters (`gate_params.h`).
- A pool of `P` int16 windows: the first `H` are healthy, in time order.
- 5 streams of 100 pool indices: S0, S2, S10, S100, ST, numbered 0..4.
- The 5 stored inputs, as int16 copies made with `fx_from_float` in each command's setup, outside the window.

**Gates.** 1 `sup`, 2 `healthy`, 3 `either`, 4 `either2`, 5 `selfcal`. The Paderborn bundle also has 6 `selfcal_r2`
(pre-registration, Deviation 4): gate 5 on 23 features, the 9 gate features plus the 14 log band powers of
`fx_r2_hyb`, built with `-DCASC_NG_MAX=23 -DCASC_CAL_MAX=128`.
- **Decisions** are float32 on the board.
- **Gate 4** keeps the decision of gate 3 on the previous window. That state is reset at the start of every
  `GATE`, `CASCADE` and `DUTY` window and every `STREAM` pass.
- **Gate 5** needs a successful `CALIB`, and gate 6 a successful `CALIB` with gate 6 selected. Until then, any
  command that uses it replies `ERR NOCAL`.

| Command | Timed? | Board action | Final line |
|---|---|---|---|
| `TEMP` | no | nRF52840 die temperature (TEMP peripheral, 0.25 °C steps) | `OK temp_centi_c=<centi-°C>` |
| `GSEL <g>` | no | select gate g ∈ 1..5, Paderborn 1..6 (`ERR RANGE` otherwise); `GATE`, `CASCADE`, `STREAM`, `DUTY` reply `ERR NOSEL` until a gate is selected | `OK g=<g> name=<name>` |
| `GVERIFY` | no | `RDY 2048`, then 1024 × int16 LE (the same handshake, timeout and drain as `VERIFY`); fx_r1 and every base gate on that window | `OK sup=<0\|1> leaf=<node> healthy=<0\|1> d=<hex> sc=<-1\|0\|1> dsc=<hex> fx=<hex>,…` (10 values); Paderborn appends ` sr=<-1\|0\|1> dsr=<hex> hb=<hex>,…` (gate 6 alarm and distance, the 14 bands of `fx_r2_hyb`). The line can exceed 256 characters |
| `GATE <N>` | yes | N × (fx_r1 + the selected gate) on the stored input | `OK n=<N> us=<> alarm=<0\|1>` |
| `CASCADE <N> <m>` | yes | N × gate on the int16 copy + stage 2 of the selected model (`SEL`) on the float stored input itself, the window `E2E` measures. m = 0: stage 2 only on an alarm; 1: stage 2 always; 2: stage 2 always, with the model's R1 part taken from the gate's fx_r1 output (R1: z-score only; R2: `fe_r2_tail`; R4: `fe_r4_tail`, `frontend.h`). With gate 6, an R2 model takes the gate's whole `fx_r2_hyb` output (z-score only). `ERR NOTIMPL` for an R0 or R3 model | `OK n=<N> us=<> alarms=<> esc=<> cls=<last>` |
| `STREAM <N> <s>` | yes | N passes over stream s. Each window: gate; on an alarm, `fx_to_float` + the model's front end + inference | `OK n=<N> us=<> windows=<100 N> alarms=<> esc=<> seq=<hex>` |
| `CALIB <n> <R>` | yes | R × the calibration on the first n healthy pool windows (n = 2..min(64, H); Paderborn 2..min(128, H)). It calibrates gate 6 when gate 6 is selected, gate 5 otherwise; the last result becomes that gate's state | `OK n=<n> r=<R> us=<> thr=<hex>` |
| `DUTY <T> <N>` | yes | N periods of T ms (T = 10..60000, T × N ≤ 600 000 ms) on a fixed schedule. Each period runs one stream-0 window (cycling) through the gate and, on an alarm, stage 2 (as in `STREAM`), then thread sleep (as `SLEEP`) and `WFI` to the end of the period | `OK n=<N> us=<> alarms=<> esc=<> overrun=<periods whose work exceeded T>` |
| `FXFE <N> <p>` | yes | N × fixed-point front end p on the int16 stored input: 0 fx_r1, 1 fx_r2_hyb, 2 fx_r4_hyb. The q15/q31 paths are not offered: the board's Arduino_CMSIS-DSP 5.7.0 lacks `arm_absmax_q15/q31` | `OK n=<N> us=<>` |

- **`<hex>`** is the IEEE-754 bit pattern of a float32: 8 lowercase hex digits. The host compares exact values, not
  rounded decimals.
- **`seq`** is the 32-bit FNV-1a hash of the alarm bits of the first pass, one byte per window. The host compares it
  with the hash of the expected sequence in `streams.csv`.
- **`esc`** counts stage-2 runs. `alarms` and `esc` are totals over the whole window, and the host checks them
  against N × the expected counts.
- **N sizing.** As in §7.3, from a 1-call probe: `GATE 1`, `CASCADE 1 m`, `STREAM 1 s`, `CALIB n 1`. `DUTY` lasts
  N × T.
- **Timeouts.** 1.5 × the expected time + 5 s. Before a probe, 605 s.
- **New error code** `NOCAL`: gate 5 used before a successful `CALIB`. A non-positive-definite calibration
  covariance also replies `ERR NOCAL`.
- **Rows** go to `results/<ds>/cascade_hw/cascade_measurements.csv`, one row per window:

  `session, mode, gate, model_id, input_j, stream, arg, N, t_window_us_dut, t_window_us_logger, E_window_uJ, P_mean_uW, P_std_uW, V_bus_mV, n_samples, ovf, P_idle_uW, alarms, escalations, seq, die_temp_C, fw_hash, logger_hash, timestamp, rejected`

  - `mode` ∈ `IDLE`, `SLEEP`, `GATE`, `CASCADE`, `STREAM`, `CALIB`, `DUTY`, `FXFE`.
  - `arg` holds the command's second argument (m, s, R, T or p).
  - The rejection and pairing rules are those of §7.4.

## 8. Checks the reviewer runs before approving a merge

| Area | Command | Must pass |
|---|---|---|
| data | `python ml/prepare_data.py --check` | 40 files, window counts match `results/cwru/data/data_counts.csv` |
| splits (from W2) | `python ml/splits.py --check` | all assertions |
| firmware | `arduino-cli compile --fqbn arduino:mbed_nano:nano33ble firmware/bench` (from W4: `tools/build_flash.py <bundle> --compile-only`) | builds with pinned versions |
| host tools | `python -m pytest tests/` (also run by CI as the required `tests` check) | green |
| measurement | `python tools/measure.py --dry-run` | writes a complete dummy CSV |

## 9. Sign-off

How to sign:
1. Agnibha opens one PR titled `contract: v1.0`. Its first commit sets the status line to **v1.0 (binding)** and
   `CONTRACT_VERSION = "1.0"` in `tools/contract.py`. The tests check that the two agree.
2. Each person reads the whole file, then adds their date to their own row as a commit on that PR. A signature commit
   is that person's agreement.
3. Abhinav and Hridayesh approve the PR on GitHub **after the last signature commit**. GitHub does not let the PR
   author approve, so Agnibha's signature commit stands in for his approval.
4. Merge (merge commit) only when all three rows are filled and the `tests` check is green.
5. Tag the merge commit and push the tag: `git tag m0-contract && git push origin m0-contract`.

| Name | Role | Signed (date) | Commit |
|---|---|---|---|
| Agnibha | ML & data | | |
| Abhinav | Embedded firmware | | |
| Hridayesh | Measurement & paper | | |

Version history:
- v0.1 (draft, 1 Oct 2026).
- v1.0-rc1 (1 Oct 2026):
  - M0 checklist added;
  - `config` column added to the registry;
  - VERIFY number format matches the firmware;
  - branch protection added (§1.9);
  - reviewer checks marked with the week they start.
- v1.0-rc2 (1 Oct 2026), after an independent review:
  - host timeouts written out (LAT 600 s), plus the resync and echo-check rules;
  - VERIFY handshake and drain, output meaning (softmax / one-hot), number format and tolerance;
  - model-ID scope (one deployed instance per ID), family rules and C symbol names;
  - D2 pull-down and the 100 ms glitch filter; W-line definitions (E, Pmean, ovf, baud, deadline);
  - definitions for P_idle_uW and the IDLE/SLEEP rows; `feasible` defined as "links";
  - git rules changed to merge commits plus required CI; SLEEP is called thread sleep;
  - the low-pass filter is adopted;
  - sign-off procedure fixed.
- v1.0-rc3 (29 Oct 2026), from the first M2 parity run (C front end on the virtual Cortex-M4, `tools/vmcu/`):
  - R2 bands 14–15 (5.25–6 kHz) lie in the stop-band of the common 5 kHz low-pass (§3). Their log-power is
    ≈ −10, about twelve decades below the signal bands and below float32 FFT precision, so the C front end
    cannot match Python there (max error 0.62 σ against the 1e-3 gate). R2 now ends at band 13: **24 features**
    (`handoff/features_spec.md` v1.1). `norm_constants` and the test vectors change shape accordingly;
  - no other interface changes.
- v1.0-rc4 (29 Oct 2026), proposal (Agnibha), after the first host sweep showed that every family scores ≤ 0.63
  macro-F1 on unseen bearings (P3):
  - new input representation **R4** = R1 without its mean (the sensor's DC offset, a recording fingerprint) + 32
    log envelope-spectrum bins (classical envelope analysis: 2–5 kHz
    band, envelope, its 11.7–375 Hz spectrum; `handoff/features_spec.md` v1.2). It needs no shaft speed, so the
    serial protocol and VERIFY are unchanged;
  - model-ID input token `r4` for trees, forests and MLPs (20 new IDs, same sizes as on R1/R2);
  - `norm_constants` gain `g_r4_mean[41]`, `g_r4_std[41]`; the test vectors gain `r4.npy`, `r4_norm.npy`.
  - proposal for §7.2 `LAT`, from the virtual measurement campaign (`tools/vrig/campaign.py`): repeat each stored
    window `clamp(ceil(60 s / t), 11, 101)` times instead of always 101, where `t` is the model's front end plus
    inference time from the virtual Cortex-M4. With 101 repetitions, LAT alone is about 10 h of the 16 h campaign,
    mostly for a few float and reference-kernel CNNs; with the cap the campaign takes about 8 h. Not yet applied to
    §7.2: it needs the firmware's `LAT` to take a repetition count.
- v1.0-rc4 addendum (14 Nov 2026), §7.2 `LAT [<reps>]` (backward compatible: plain `LAT` = 101, reply gains
  `reps=`): the rc3 proposal above is applied, with `t` from an on-board `E2E 1` probe instead of the virtual
  Cortex-M4. Needed because `cnn-r0-w200d4-fp32` takes 2.62 s per inference on the board (the virtual Cortex-M4
  estimated ~20.9 s): 101 × 5 calls would be ~22 min, past the 600 s host timeout; with the 23 repetitions the
  rule gives it is ~5 min (session 112). Window timeouts now count the warm-up call. Energy windows
  are unchanged. Proposed by Hridayesh; needs Abhinav's and Agnibha's sign-off.
- v1.0-rc4 addendum (1 Nov 2026), no interface change: `handoff/hann_fx.h` (q15/q31 Hann tables) added to §5 for
  the optional fixed-point front end `firmware/bench/frontend_fx.c`. Same features and units as the float front end,
  so model IDs, VERIFY and the norm constants are unchanged; adopting it on the board is a firmware decision.
- v1.0-rc4 addendum (17 Nov 2026), file locations only, no schema or interface change: `results/` is split by
  dataset into `results/cwru/` and `results/pu/` with the same subfolders, `data/` (window and split counts),
  `host/` (one JSON per run), `analysis/` (host summary, Pareto fronts, headline numbers), `cascade/`, `fixedpoint/`
  (CWRU) and `hardware/` (rig measurements, session log, build record, board VERIFY). Shared tool outputs stay at
  `results/vmcu/`, `results/vrig/`, `results/figs/`, `results/demo/`. §1, §6 and §8 paths updated; every column is
  unchanged. Index of every file and the script that writes it: `results/README.md`. Proposed by Hridayesh.
- v1.1 (21 Nov 2026), Hridayesh (sole owner of every workstream since 20 Nov):
  - §7.6 cascade commands `TEMP`, `GSEL`, `GVERIFY`, `GATE`, `CASCADE`, `STREAM`, `CALIB`, `DUTY`, `FXFE` for a
    separate cascade bundle per dataset;
  - error code `NOCAL`;
  - the `cascade_hw/cascade_measurements.csv` row format.

  Existing commands and their replies are unchanged; the dispatcher now accepts two arguments, and non-cascade
  builds answer the §7.6 keywords with `ERR NOTIMPL` instead of `ERR UNKNOWN`. Implemented in
  `firmware/bench/bench_cascade.cpp` and covered by `tests/test_cascade.py` on the PC simulator (`make
  bench_sim_cascade`). Changes from the draft: `TEMP` replies `temp_centi_c` (§7.1 keys are lowercase); `FXFE` offers
  fx_r1 and the two hybrids only; `CASCADE` runs stage 2 on the float stored input; Paderborn gate 6 (`selfcal_r2`,
  pre-registration Deviation 4) with its `CALIB` and `GVERIFY` fields. Board verification (S-V) comes
  with cascade pilot session 80 (`docs/cascade_preregistration.md`).
