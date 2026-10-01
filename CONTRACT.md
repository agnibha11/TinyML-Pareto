# CONTRACT.md — M0 interface contract

Status: **v1.0-rc2, ready for sign-off.** It becomes binding when all three sign §9 (gate M0, due Sun 11 Oct 2026).

This file fixes every interface between the three workstreams, so the three codebases fit together without anyone
reading anyone else's code. If you need to change something here, open a PR that edits this file. All three approve it,
and the version number goes up. Nothing in this file changes silently.

| Owner | Workstream | Owns in this repo |
|---|---|---|
| Agnibha | ML & data | `ml/`, `data/`, `splits/`, `configs/`, `models/`, `handoff/`, `results/host/`, `results/host_summary.csv`, `results/data_counts.csv`, `results/data_files.csv` |
| Abhinav | Embedded firmware | `firmware/`, `tools/dut.py`, `tools/bundle.py`, `tools/build_flash.py`, `results/fw_metrics.csv` |
| Hridayesh | Measurement & paper | `logger/` (ESP32), `tools/measure.py`, `figs/`, `paper/`, `results/hw_measurements.csv`, `results/session_log.csv` |

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
7. Results files under `results/` are written only by scripts. Never edit or delete a results row by hand.
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
  not physics. `results/data_files.csv` records the cutoff used, and `--lpf-hz 0` exists only for an ablation.
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

Family rules (enforced by `tools/contract.py::parse_model_id`):
- Trees (`dt`, `rf`) take features (`r1`–`r3`) and are `fp32` only.
- CNNs take the raw window (`r0`) only.
- MLPs take `r1`–`r3`.
- `int8ref` is only valid for `cnn`.

**What an ID names.** An ID names an *architecture configuration*.
- **Deployed and registered instance:** exactly one per ID — the model trained on **P2 fold 0, seed 0, class10 labels**.
  Only that instance gets firmware, energy and memory numbers.
- **Other instances:** every other protocol, fold and seed (including the 4-class P3 heads) is evaluated on the host
  only, under the same ID, keyed by `(model_id, protocol, fold, seed)` in `results/host/`.
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
  features_spec.md             R0–R3 formulas, word for word (Agnibha)
  hann_1024.h / .npy           Hann window constants (g_hann_1024), generated by ml/export_handoff.py
  norm_constants.h / .npz      P2 fold-0 training statistics; C symbols (npz keys = same names without g_):
                               g_r0_mu, g_r0_sigma (float); g_r1_mean[10], g_r1_std[10];
                               g_r2_mean[26], g_r2_std[26]; g_r3_mean[256], g_r3_std[256]
  ops_union.txt                union of TFLite ops over all NN models, one op per line
  test_vectors/
    ids.npy                    100 window_ids (10 per class), fixed
    raw.npy                    (100, 1024) float32
    r1.npy r1_norm.npy         (100, 10)
    r2.npy r2_norm.npy         (100, 26)
    r3.npy r3_norm.npy         (100, 256)
    outputs/<model_id>.npy     host outputs for the same windows, in exactly the form VERIFY returns (§7.2)
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

- `results/host/<model_id>__<P>__f<fold>__s<seed>.json`: `{model_id, protocol, fold, seed, snr_db, f1_macro, accuracy, confusion, n_test}`
- `results/host_summary.csv`: `model_id, protocol, snr_db, n_runs, f1_macro_mean, f1_macro_std, accuracy_mean, accuracy_std`

### 6.3 `results/fw_metrics.csv` (Abhinav writes)

`model_id, runtime, bundle, flash_model_B, arena_used_B, frontend_ram_B, ram_model_peak_B, lat_inf_med_us, lat_inf_min_us, lat_inf_max_us, lat_fe_med_us, git_hash`

### 6.4 `results/hw_measurements.csv` (Hridayesh writes, one row per window)

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
| `LAT` | for each stored window: 101 timed inferences and 101 timed front-end calls (DWT) | `OK inf_med_cyc=<> inf_min_cyc=<> inf_max_cyc=<> fe_med_cyc=<> fe_min_cyc=<> fe_max_cyc=<>` |
| `VERIFY` | board sends `RDY 4096`; the host sends the 4096 bytes (1024 × float32 LE) only after `RDY`; the board waits ≤ 2 s for them, otherwise it discards input for ≥ 1 s and until 50 ms of silence, then replies `ERR IO`; then front end + inference | `OK cls=<k> out=<v0>,<v1>,…` |

- `N` is a 32-bit unsigned integer ≥ 1. `ms` is 1..600000.
- `<window_us>` = `micros()` read just after D2 goes LOW minus `micros()` read just before D2 goes HIGH.
- `LAT` statistics: for each of the 5 stored windows take the median of 101 runs, then report the median of the 5 medians
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
  - 600 s for `LAT`.
  - 10 s for `VERIFY`.
  - Windowed commands: 1.5 × the expected window + 5 s, where the expected window = N × the latency from the last
    `LAT`. Before any `LAT`, 605 s.
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

### 7.5 What counts as each phase

| Phase | FRONT | RUN (inference) | E2E |
|---|---|---|---|
| Front end (R0 normalize, or R1/R2/R3 features + z-score) | yes | no | yes |
| Input quantization (float → int8) | no | yes | yes |
| `interpreter.Invoke()` / `<c_name>_predict()` | no | yes | yes |
| Argmax on output | no | yes | yes |
| `AllocateTensors()`, model selection | no | no | no |

The paper states this table verbatim.

## 8. Checks the reviewer runs before approving a merge

| Area | Command | Must pass |
|---|---|---|
| data | `python ml/prepare_data.py --check` | 40 files, window counts match `results/data_counts.csv` |
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
