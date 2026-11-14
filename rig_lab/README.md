# rig_lab — energy-rig bring-up lab (14 Nov 2026)

Rig bring-up work of 14 Nov 2026: rig characterisation, the idle-dip investigation, lab build tools and the
calibration. **No model (inference) measurements are kept here**: the lab test runs were deleted before the energy
campaign, which writes to `results/hw_measurements.csv` with `tools/measure.py`.

Rig: Nano 33 BLE on the cut Ubon cable (laptop port 1) → INA219 Vin+/Vin− with its on-board R100 → ESP32
NodeMCU-32S logger (`logger/`, core 3.3.12). **Calibrated without a DMM** (see "Calibration"): effective shunt
**0.1139 Ω**; the logger is built with `SHUNT_OHM_MEASURED=0.1139`.

## Calibration (14 Nov 2026, no multimeter)

Data: `calibration/calibration.csv` (one row per run) and the raw `rig_check --cal` output next to it. Nano unplugged,
known resistor from Vin− to GND, sync window made by hand (3V3 jumper into the SYNC row).

| Run | Load | Current leaves via | Zero | Gain (zero subtracted) |
|---|---|---|---|---|
| cal1 | 1 kΩ ±1 % (brown-black-black-brown-brown) | Vin− header pin | 134 µW | **1.1394** |
| cal3 | 1 kΩ ±1 % | Vin− screw (the Nano's path) | 152 µW | **1.1390** |
| cal2 | 100 Ω (tolerance band not read) | Vin− header pin | 135 µW | 1.1300 |

- With the logger built for 0.100 Ω the rig read **13.9 % high** wherever the current leaves the module, linear from
  5 to 57 mA. Effective shunt = 0.100 × 1.139 = **0.1139 Ω (± ~1.2 %: resistor 1 % + INA219 bus ADC)**.
- Zero offset 134–152 µW (~27 µA); it cancels in energy above idle, matters only for absolute idle/sleep power.
- After rebuilding the logger with 0.1139: `rig_check` 8/8 PASS, idle 18.27–18.72 mW (expected 21.34 / 1.139 = 18.74).
- `cal_100r_nominal.txt` is the first 100 Ω attempt: the ESP32 dropped off USB during the load step (its only ground
  return was the ESP32's USB cable); the repeat (`cal_100r_nano_return.txt`) completed.
- When a multimeter is available: measure the **same 1 kΩ** (a cheap DMM cannot measure 0.1 Ω directly), repeat
  `rig_check.py --cal <measured value>`, and correct the gain by the difference.

## Layout

| Path | What |
|---|---|
| `fw/bench/` | copy of `firmware/bench` (with the HFXO fix) + generated bundles (`models/`, `bundle_gen.cpp`, handoff headers) |
| `fw/bundles.csv` | bundle plan (same packing as `tools/bundle.py`) |
| `build/fw/b1..b4/` | compiled bundles (`bench.ino.hex` etc.) |
| `tools/lab_build.py` | runs `tools/bundle.py` / `tools/build_flash.py` logic with every output redirected here |
| `tools/summarise.py` | energy per inference / front end / end-to-end from a `hw_measurements.csv` (any path) |
| `calibration/` | `calibration.csv` + raw `rig_check --cal` logs |
| `wfi_dip/` | the ~42 s idle-dip investigation: Nano sketch `nano_dipx/`, ESP32 10 Hz stream `inastream/`, `run_dip.py`, raw CSVs (idle current only) |
| `results/fw_build.csv` | link facts per bundle/model (flash, static RAM, arena) |
| `logs/` | (git-ignored) build, rig-check, wiggle and soak logs |

Lab builds (from the repo root, `Tiny_ML/.venv` active, Arduino gcc on PATH for `arm-none-eabi-nm`). Since the repo
fixes, the campaign itself uses `tools/bundle.py` + `tools/build_flash.py` directly:
```bash
python rig_lab/tools/lab_build.py plan
python rig_lab/tools/lab_build.py compile 1
python rig_lab/tools/summarise.py results/hw_measurements.csv
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

## measure.py / logger issues seen in the lab runs

1. **Windows shorter than the 10 s contract minimum (21 of 62 windows): FIXED in `tools/measure.py`.** It sized N
   from LAT's median over the 5 inputs with no margin, so faster inputs got short windows (forest RUN windows of
   7.58–9.36 s; FRONT 9.96 s because the timed loop runs ~0.3 % faster than LAT). Now N is sized from the fastest
   input (`*_min_us`) × 1.05, and a window still under 10 s is written `rejected=1` and re-measured with a larger N.
2. **Logger line corruption: 1 of 61 windows.** One `W` line arrived as the fragment
   `W rise_us=45473077 fall_us=55473` repeated ~80 times before the rest of the line. measure.py's reader is
   correct (no re-append), so the repetition happened on the ESP32 side or on its flaky USB link (the CP2102 link
   that throws `error -71`). The contract check rejected it and the retry passed, as designed.
