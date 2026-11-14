# rig_lab — energy-rig bring-up lab (14 Nov 2026)

Everything from the unattended session of 14 Nov lives here. **Nothing outside `rig_lab/` was changed in that
session.** Two repo files were changed earlier the same day with Hridayesh's OK and are left as they are:
`firmware/bench/hal_nano33ble.cpp` (HFXO start) and `firmware/VERSIONS.md` (ESP32 core pin, HFCLK row, change log).
Nothing is committed or pushed.

Rig as measured: Nano 33 BLE on the cut Ubon cable (laptop port 1) → INA219 Vin+/Vin− with its on-board **R100
(0.100 Ω nominal, not DMM-measured)** → ESP32 NodeMCU-32S logger (`logger/`, built with `SHUNT_OHM_MEASURED=0.100`,
core 3.3.12). **Not calibrated** (no multimeter yet): absolute numbers carry the R100 tolerance (~1 %) and the INA219
offset; comparisons between models on this rig are fine.

## Layout

| Path | What |
|---|---|
| `fw/bench/` | copy of `firmware/bench` (with the HFXO fix) + generated bundles (`models/`, `bundle_gen.cpp`, handoff headers) |
| `fw/bundles.csv` | bundle plan (same packing as `tools/bundle.py`) |
| `build/fw/b1..b4/` | compiled bundles (`bench.ino.hex` etc.) |
| `tools/lab_build.py` | runs `tools/bundle.py` / `tools/build_flash.py` logic with every output redirected here |
| `tools/summarise.py` | energy per inference / front end / end-to-end from `campaign/hw_measurements.csv` |
| `campaign/` | `tools/measure.py` output (`hw_measurements.csv`, `session_log.csv`, `summary.csv`) |
| `wfi_dip/` | the ~42 s idle-dip investigation: Nano sketch `nano_dipx/`, ESP32 10 Hz stream `inastream/`, `run_dip.py`, raw CSVs |
| `results/fw_build.csv` | link facts per bundle/model (flash, static RAM, arena) |
| `logs/` | build, rig-check, wiggle, soak and measurement logs |

Reproduce (from the repo root, `Tiny_ML/.venv` active, Arduino gcc on PATH for `arm-none-eabi-nm`):
```bash
python rig_lab/tools/lab_build.py plan
python rig_lab/tools/lab_build.py compile 1
arduino-cli upload --fqbn arduino:mbed_nano:nano33ble -p /dev/ttyACM0 --input-dir rig_lab/build/fw/b1
python tools/measure.py --port /dev/ttyACM0 --logger /dev/ttyUSB0 --logger-hash dev --session <n> \
    --models 0,17,29 --out rig_lab/campaign/hw_measurements.csv
python rig_lab/tools/summarise.py
```

## Repo bugs found

**Update 14 Nov (with Hridayesh's OK): 1 and 2 are now fixed in the repo** — `tools/bundle.py` copies
`hann_fx.h`; `ml/export_handoff.py` emits `#include "arm_math.h"` and `handoff/hann_fx.h` matches its output
byte for byte. Checked: repo `tools/bundle.py` + `arduino-cli compile firmware/bench` link bundles 1 and 4 (same
sizes as below), `models/bundles.csv` unchanged, `tests/test_contract.py`, `test_host_tools.py`,
`test_rig_check.py` 52 passed. The lab workaround in `lab_build.py` is now redundant but harmless. 3 is still open.

1. **`tools/bundle.py` does not copy `handoff/hann_fx.h`** into `firmware/bench/`, but `frontend_fx.c` includes it.
   Every bundle build fails with `fatal error: hann_fx.h: No such file or directory`.
   Fix: add `"hann_fx.h"` to the `for h in ("hann_1024.h", "norm_constants.h")` loop.
2. **`ml/export_handoff.py` writes `#include "arm_math_types.h"` into `hann_fx.h`.** That header exists only in
   CMSIS-DSP ≥ 1.10; the pinned `Arduino_CMSIS-DSP 5.7.0` has `arm_math.h` (which defines `q15_t`/`q31_t`).
   Fix: emit `#include "arm_math.h"` and regenerate `handoff/hann_fx.h`.
3. `tools/build_flash.py` needs `arm-none-eabi-nm` on PATH (it is under
   `~/.arduino15/packages/arduino/tools/arm-none-eabi-gcc/*/bin/`); worth a note in `firmware/VERSIONS.md`.

`lab_build.py` applies 1 and 2 to the lab copy only. With them, **all 4 bundles link**:

| Bundle | Runtime | Models | Flash | Static RAM |
|---|---|---|---|---|
| 1 | emlearn | 30 | 499,080 B | 62,560 B |
| 2 | tflm-cmsis | 5 | 931,856 B | 191,424 B |
| 3 | tflm-cmsis | 20 | 931,424 B | 190,400 B |
| 4 | tflm-cmsis | 25 | 479,720 B | 128,960 B |

## Finding: the ~42 s idle dip is the Nano's own USB stack

Back-to-back `IDLE` windows showed a −3 % dip (~0.12 mA, ~4 s long) every 42.0–43.1 s, with nobody near the rig.
Measured with a 10 Hz INA219 stream (`wfi_dip/inastream`), sync-independent:

| Run | Nano state | Median current | 42 s pattern |
|---|---|---|---|
| bench, command loop, port closed | CPU awake | 7.17 mA | none |
| bench, command loop, port open | CPU awake | 8.58 mA | none |
| bench `IDLE 130000`, port open | WFI | 4.17 mA | yes, autocorr 42.4 s r = 0.50 |
| bench `IDLE 130000`, port closed after sending | WFI | 2.67 mA | weaker, ~43–45 s, r = 0.11 |
| `nano_dipx` R0: WFI, USB on | WFI | 4.15 mA | yes, 42.5 s r = 0.48 |
| `nano_dipx` R1: WFI, **USB peripheral off** | WFI | **0.87 mA** | **gone** (only a ±11 µA wobble at ~32 s) |

Clock state (`INFO`): HFCLK on crystal, LFCLK on the 32 kHz crystal (no RC calibration cycles), DC/DC off.

Conclusions:
- The dip needs the Nano's USB device to be active; the laptop polling the open port makes it stronger but is not
  required. It is not the wiring, the rig or the host's USB power management (autosuspend is off).
- **USB costs ~3.3 mA of the 4.15 mA WFI idle current** (and an open, polled CDC port ~1.5 mA of that). The campaign
  needs USB for the protocol, so this is a fixed condition to state in the paper ("USB enumerated, CDC port open").
  The dynamic energy per inference subtracts the idle baseline, so it does not carry the USB cost; absolute idle
  and sleep powers do.
- Size of the effect: ≤ 3 % for ≤ 5 s once per 42 s, only while the CPU sleeps (not seen with the CPU busy). Over a
  10 s IDLE window it can bias the baseline by up to ~1 %. Mitigation: IDLE windows ≥ 42 s, or average several IDLE
  windows per model (measure.py already takes 5).
- The dip is ~0.7–1.2 INA219 LSB with R100 (12-bit, 10 µV = 100 µA per LSB); it shows only after averaging. With the
  1 Ω shunt (10 µA/LSB) it would be resolved directly — worth remembering if idle/sleep precision matters.

Not run (time): HFXO-off (flag 2), RTOS sleep (flag 4), LFCLK→LFSYNT (flag 8). `run_dip.py` supports them.

## Other bring-up results (same day)

- Sync fixed (100 Ω and 100 kΩ had been swapped). `tools/rig_check.py`: 8/8 PASS after the HFXO fix
  (IDLE 21.34 mW, 5-window CV 0.14 %, RUN stub +131 % over IDLE). Only WARN: ESP32 ROM boot text when the port opens.
- Nano clock was +0.77 % fast before the HFXO fix (now 3 ppm vs the ESP32).
- Wiggle test: 0 lost windows, Vbus 5135–5136 mV. 6-min soak: 341 windows, CV 0.77 % (0.37 % without the dips).
- ESP32 USB drops (`error -71`): one burst at ~16:04 (laptop clock), with nothing running. Re-seat its micro-USB.
- The laptop clock jumped back 5.5 h at ~21:20 IST (journald "Time jumped backwards"): timestamps in these logs
  before/after differ by that offset.

## Model measurements (uncalibrated, R100 nominal)

`tools/measure.py` unchanged, output in `campaign/`. Per model: LAT, then for each of the 5 stored inputs
IDLE → RUN N → FRONT N_fe → E2E N_e2e (windows ≥ 10 s target). Summary by `tools/summarise.py`;
E/op = (P_mean − P_idle) × t_window / N, i.e. energy above idle per call. t/op from the logger's window length.

### Session 1 — bundle 1 (emlearn trees)

IDLE baseline: **21.41 mW** (15 windows, CV 0.24 %), Vbus 5132–5136 mV. Window length logger vs board: ≤ 7 ppm.
60 windows accepted, 1 rejected (see "Logger line corruption").

| Model | Mode | t/op | P_mean | P above idle | **E/op** | spread over 5 inputs |
|---|---|---|---|---|---|---|
| dt-r1-d4-fp32 | RUN (inference) | 4.76 µs | 42.9 mW | 21.5 mW | **0.102 µJ** | 3.4 % |
| | FRONT (R1 features) | 637 µs | 45.6 mW | 24.1 mW | **15.4 µJ** | 0.3 % |
| | E2E | 643 µs | 45.7 mW | 24.2 mW | **15.6 µJ** | 0.5 % |
| rf-r1-t25d10-fp32 | RUN | 42.9 µs | 52.3 mW | 30.8 mW | **1.33 µJ** | 35 % (real, see below) |
| | FRONT (R1) | 636 µs | 45.5 mW | 24.1 mW | 15.3 µJ | 0.3 % |
| | E2E | 696 µs | 47.6 mW | 26.2 mW | 18.2 µJ | 3.8 % |
| rf-r4-t25d10-fp32 | RUN | 47.6 µs | 52.8 mW | 31.4 mW | **1.50 µJ** | 31 % (real) |
| | FRONT (R4 features, FFT) | 3950 µs | 47.0 mW | 25.6 mW | **101 µJ** | 0.2 % |
| | E2E | 4002 µs | 47.1 mW | 25.7 mW | 102.8 µJ | 0.2 % |

Reading:
- The rig resolves a 4.7 µs decision-tree call: 0.10 µJ with 3 % spread over inputs.
- **The front end dominates end-to-end energy for trees**: R1 features cost 150× the DT inference, R4 (FFT)
  ~70× a 25-tree forest. Front-end choice moves the Pareto front more than the classifier does.
- Forest spread is real, not noise: power is flat (~31 mW) but time per inference changes with the input
  (29.5–69.8 µs: different path lengths through 25 trees of depth 10).

### Session 2 — bundle 4 (TFLM, CMSIS-NN)

IDLE baseline: **21.35 mW** (15 windows, CV 0.45 %). 0 windows rejected. Window length logger vs board: ≤ 7 ppm.

| Model | Mode | t/op | P_mean | P above idle | **E/op** | spread over 5 inputs |
|---|---|---|---|---|---|---|
| mlp-r2-h32-fp32 | RUN | 487 µs | 47.5 mW | 26.2 mW | **12.75 µJ** | 1.1 % |
| | FRONT (R2 features) | 2489 µs | 46.2 mW | 24.9 mW | **62.0 µJ** | 0.6 % |
| | E2E | 2976 µs | 46.4 mW | 25.1 mW | 74.7 µJ | 0.7 % |
| mlp-r2-h32-int8 | RUN | 303 µs | 46.3 mW | 24.9 mW | **7.54 µJ** | 6.1 % (input 0 is 17 % faster) |
| | FRONT (R2) | 2487 µs | 46.2 mW | 24.8 mW | 61.7 µJ | 0.4 % |
| | E2E | 2792 µs | 46.2 mW | 24.9 mW | 69.4 µJ | 0.6 % |
| cnn-r0-w100d2-int8 | RUN | **92.3 ms** | 47.2 mW | 25.9 mW | **2386 µJ** | 0.3 % |
| | FRONT (R0, normalisation only) | 173 µs | 54.8 mW | 33.4 mW | 5.79 µJ | 2.9 % |
| | E2E | 92.5 ms | 47.3 mW | 25.9 mW | 2395 µJ | 0.2 % |

Reading:
- int8 vs fp32 for the same MLP: **−38 % time, −41 % energy per inference**; but end-to-end only −7 %, because the
  R2 front end (62 µJ) is 5–8× the inference.
- The CNN on raw samples (R0) has an almost free front end but its inference is 92 ms / 2.4 mJ: ~30× the MLP's
  whole end-to-end cost. The R0 CNN family will sit far right on the energy axis.
- Active power is remarkably constant across all six models (P above idle 21.5–33 mW, mostly 24–26 mW): on this
  MCU, **energy per inference is almost entirely latency × ~25 mW**. Latency is the quantity to get right.
- **Odd: mlp-r2-h32-int8 input 0 runs 261 µs vs 312–314 µs** for inputs 1–4 (fp32 version: flat 485–489 µs).
  Data-dependent timing in a dense int8 network is unexpected; worth a look on the TFLM side (quantize op
  saturation?). Not a rig effect: its power is the same as the other inputs.
- This MLP-int8 RUN window for input 0 was 8.74 s, again below 10 s (issue 1 below).

## measure.py / logger issues seen in the runs (repo tools not changed)

1. **Windows shorter than the 10 s contract minimum: 21 of 62 in session 1, more in session 2.** `run_session` sizes N from LAT's *median over
   the 5 inputs* with no margin (`DUT.n_for` = ceil(10 s / median)). Inputs faster than the median give short
   windows (forest RUN windows of 7.58, 8.95 and 9.36 s); FRONT windows land at 9.96–9.97 s because the in-window
   loop runs ~0.3 % faster than LAT predicts. Fix: size N per input (or from the fastest input) and add ~5 % margin.
2. **Logger line corruption: 1 of 61 windows.** One `W` line arrived as the fragment
   `W rise_us=45473077 fall_us=55473` repeated ~80 times before the rest of the line. measure.py's reader is
   correct (no re-append), so the repetition happened on the ESP32 side or on its flaky USB link (the CP2102 link
   that throws `error -71`). The contract check rejected it and the retry passed, as designed.
