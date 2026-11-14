# Energy rig bring-up (gate M3, 14 Nov 2026)

How the INA219/ESP32 energy rig was brought up, validated and calibrated, and what it showed about the Nano's idle
current. The rig is described in `hardware/energy_rig/README.md`; the campaign itself is run with
`tools/measure.py` and writes `results/hw_measurements.csv` (CONTRACT.md 6.4).

Rig: Nano 33 BLE on the cut Ubon cable (laptop port 1) → INA219 Vin+/Vin− with its on-board R100 → ESP32
NodeMCU-32S logger (`logger/`, `esp32:esp32@3.3.12`). **Calibrated without a DMM** (below): effective shunt
**0.1139 Ω**; the logger is built with `SHUNT_OHM_MEASURED=0.1139`.

## Where things are

| Path | What |
|---|---|
| `hardware/energy_rig/` | KiCad project, BOM, wiring table |
| `tools/rig_check.py` | pre-flight check (`--cal R`, `--watch`, `--simulate`) |
| `tools/measure.py` | the energy campaign |
| `tools/summarise_hw.py` | quick look at a campaign CSV: energy per inference / front end / end-to-end |
| `tools/run_dip.py` | runs one idle-dip experiment window and analyses it |
| `firmware/experiments/` | experiment sketches: `nano_dipx/` (Nano), `inastream/` (ESP32 10 Hz INA219 stream) |
| `results/rig/` (local only, git-ignored) | raw calibration runs and idle-dip experiment data; summarised in this document |

## Calibration (14 Nov 2026, no multimeter)

Raw data (one row per run plus the `rig_check --cal` output) is kept locally in `results/rig/calibration/`, not in the repo.
Nano unplugged, known resistor from Vin− to GND, sync window made by hand (3V3 jumper into the SYNC row, with the
100 Ω from D2 lifted out of the SYNC row so no current can reach the unpowered Nano).

| Run | Load | Current leaves via | Zero | Gain (zero subtracted) |
|---|---|---|---|---|
| cal1 | 1 kΩ ±1 % (brown-black-black-brown-brown) | Vin− header pin | 134 µW | **1.1394** |
| cal3 | 1 kΩ ±1 % | Vin− screw (the Nano's path) | 152 µW | **1.1390** |
| cal2 | 100 Ω (tolerance band not read) | Vin− header pin | 135 µW | 1.1300 |

- With the logger built for 0.100 Ω the rig read **13.9 % high** wherever the current leaves the module, linear from
  5 to 57 mA. Effective shunt = 0.100 × 1.139 = **0.1139 Ω (± ~1.2 %: resistor 1 % + INA219 bus ADC)**.
- `rig_check --cal` reports the gain without subtracting the zero window; the gains above subtract it
  (2 % with R100 at 5 mA).
- Zero offset 134–152 µW (~27 µA); it cancels in energy above idle, matters only for absolute idle/sleep power.
- After rebuilding the logger with 0.1139: `rig_check` 8/8 PASS, idle 18.27–18.83 mW (expected 21.34 / 1.139 = 18.74).
- `cal_100r_nominal.txt` is the first 100 Ω attempt: the ESP32 dropped off USB during the load step (its only ground
  return was the ESP32's USB cable); the repeat (`cal_100r_nano_return.txt`) completed.
- When a multimeter is available: measure the **same 1 kΩ** (a cheap DMM cannot measure 0.1 Ω directly), repeat
  `rig_check.py --cal <measured value>`, and correct the gain by the difference.

## Repo bugs found and fixed

1. **`tools/bundle.py` did not copy `handoff/hann_fx.h`** into `firmware/bench/`, but `frontend_fx.c` includes it:
   every bundle build failed (`fatal error: hann_fx.h`). Fixed.
2. **`ml/export_handoff.py` wrote `#include "arm_math_types.h"` into `hann_fx.h`.** That header exists only in
   CMSIS-DSP ≥ 1.10; the pinned `Arduino_CMSIS-DSP 5.7.0` has `arm_math.h`. Fixed; `handoff/hann_fx.h` regenerated.
3. Still open: `tools/build_flash.py` needs `arm-none-eabi-nm` on PATH (under
   `~/.arduino15/packages/arduino/tools/arm-none-eabi-gcc/*/bin/`).

With 1 and 2 fixed, **all 4 bundles link**:

| Bundle | Runtime | Models | Flash | Static RAM |
|---|---|---|---|---|
| 1 | emlearn | 30 | 499,080 B | 62,560 B |
| 2 | tflm-cmsis | 5 | 931,856 B | 191,424 B |
| 3 | tflm-cmsis | 20 | 931,424 B | 190,400 B |
| 4 | tflm-cmsis | 25 | 479,720 B | 128,960 B |

## Finding: the ~42 s idle dip is the Nano's own USB stack

Back-to-back `IDLE` windows showed a −3 % dip (~0.12 mA, ~4 s long) every 42.0–43.1 s, with nobody near the rig.
Measured with the 10 Hz INA219 stream (`firmware/experiments/inastream`), sync-independent; data in
`results/rig/wfi_dip/`, kept locally:

| Run | Nano state | Median current | 42 s pattern |
|---|---|---|---|
| bench, command loop, port closed | CPU awake | 7.17 mA | none |
| bench, command loop, port open | CPU awake | 8.58 mA | none |
| bench `IDLE 130000`, port open | WFI | 4.17 mA | yes, autocorr 42.4 s r = 0.50 |
| bench `IDLE 130000`, port closed after sending | WFI | 2.67 mA | weaker, ~43–45 s, r = 0.11 |
| `nano_dipx` R0: WFI, USB on | WFI | 4.15 mA | yes, 42.5 s r = 0.48 |
| `nano_dipx` R1: WFI, **USB peripheral off** | WFI | **0.87 mA** | **gone** (only a ±11 µA wobble at ~32 s) |

(Currents in this table were measured before the calibration, with the 0.100 Ω build: divide by 1.139.)
Clock state (`nano_dipx INFO`): HFCLK on crystal, LFCLK on the 32 kHz crystal (no RC calibration cycles), DC/DC off.

Conclusions:
- The dip needs the Nano's USB device to be active; the laptop polling the open port makes it stronger but is not
  required. It is not the wiring, the rig or the host's USB power management (autosuspend is off).
- **USB costs ~3.3 mA of the 4.15 mA WFI idle current** (an open, polled CDC port ~1.5 mA of that). The campaign
  needs USB for the protocol, so this is a fixed condition to state in the paper ("USB enumerated, CDC port open").
  Energy per inference subtracts the idle baseline, so it does not carry the USB cost; absolute idle/sleep powers do.
- Size: ≤ 3 % for ≤ 5 s once per 42 s, only while the CPU sleeps (not seen with the CPU busy). Over a 10 s IDLE
  window it can bias the baseline by up to ~1 %; measure.py takes 5 IDLE windows per model, which averages it.
- The dip is ~0.7–1.2 INA219 LSB with R100 (12-bit, 10 µV ≈ 88 µA per LSB at 0.1139 Ω); it shows only after
  averaging.

Not run: HFXO-off (flag 2), RTOS sleep (flag 4), LFCLK→LFSYNT (flag 8); `tools/run_dip.py` supports them.

## Other bring-up results

- Sync fixed (the 100 Ω and 100 kΩ had been swapped: brown-black-brown goes D2 → SYNC row, brown-black-yellow
  SYNC row → GND).
- Nano clock was +0.77 % fast before the HFXO fix in `firmware/bench/hal_nano33ble.cpp` (now 3 ppm vs the ESP32).
- Wiggle test: 0 lost windows, Vbus 5135–5136 mV. 6-min idle soak: 341 windows, CV 0.77 % (0.37 % without the dips).
- ESP32 USB drops (`error -71`): occasional, with nothing running. Re-seat its micro-USB before a session.
- The laptop clock jumped back 5.5 h at ~21:20 IST on 14 Nov (journald "Time jumped backwards").

## measure.py / logger issues seen in the lab test runs

1. **Windows shorter than the 10 s contract minimum (21 of 62): fixed.** N was sized from LAT's median over the 5
   inputs with no margin (forest RUN windows of 7.58–9.36 s; FRONT 9.96 s because the timed loop runs ~0.3 % faster
   than LAT). Now N comes from the fastest input (`*_min_us`) × 1.05, and a window still under 10 s is written
   `rejected=1` and re-measured with a larger N.
2. **Logger line corruption: 1 of 61 windows.** One `W` line arrived as a fragment repeated ~80 times. measure.py's
   reader is correct, so the repetition happened on the ESP32 side or on its flaky USB link. The contract check
   rejected it and the retry passed, as designed.

The lab test runs themselves (uncalibrated) were deleted before the campaign.

## Firmware hash note (history rewrite, 14 Nov 2026)

The `hridayesh` branch history was rewritten to change commit messages only (file trees identical). Session 11's
rows record `fw_hash = 1a37de7`, which is now **`42eaab5`** (same tree). Full map:

| Old | New |
|---|---|
| 6737a80 | e43dffe |
| df3ad3e | 7fa694a |
| b2e4b39 | ce31721 |
| b2e23fa | cb7639d |
| 2f178d3 | d5e22db |
| 1a37de7 | 42eaab5 |
