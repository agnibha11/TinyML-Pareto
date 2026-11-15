# Energy rig hardware (KiCad project), rev B1: breadboard build as built

This is the schematic of the TinyML-Pareto measurement rig, built from the parts on hand: an Arduino Nano 33 BLE, an
INA219 module (its on-board R100 is the shunt), an ESP32 NodeMCU-32S and three resistors (100 Ω, 1 kΩ, 100 kΩ). The
laptop powers the Nano through a USB cable whose VBUS wire runs through the shunt. The INA219 measures the shunt, and
the ESP32 logs it. Owner: Hridayesh
Kundu (measurement). The design implements CONTRACT.md v1.0-rc4 §7.3/§7.4 and the pin map of `logger/logger.ino`.

> **As built (14 Nov 2026): option B.** No external shunt resistor; the module's R100 is the shunt. The cut cable's golden wire
> is VBUS (not red). The sync network is Nano D2 → female-male dupont → empty row → 100 Ω (brown-black-brown) → SYNC
> row; 100 kΩ (brown-black-yellow) SYNC row → GND; ESP32 P4 in the SYNC row. Calibrated effective shunt 0.1139 Ω,
> logger built with `SHUNT_OHM_MEASURED=0.1139`. Details: `docs/energy_rig_bringup.md`.

```
laptop USB port 1 ──W1, VBUS wire cut──► U1 VIN+ ─R100, 0.1139 Ω eff.─ U1 VIN- ──W1──► W2 ──► Nano 33 BLE (A1)
                                           (cut ends in U1's screw terminal; R100 is on U1)            │ D2
                                                  INA219 (U1) ── I2C 0x40 ──┐         R6 100 Ω
laptop USB port 2 ──────────────────────────────────────► ESP32 (U2) IO21/IO22 ◄┘         │
                                                                   IO4 ◄──── SYNC row ──┴── R7 100 kΩ ── GND
```

## What each of your resistors does

| Resistor | Ref | Job |
|---|---|---|
| (U1's R100) | R1 | Shunt, on the INA219 module; no external resistor. Effective 0.1139 Ω, logger built with `-DSHUNT_OHM_MEASURED=0.1139` (the 1.000 default in `logger.ino` is for option A) |
| 100 kΩ | R7 | Sync pull-down on ESP32 IO4 (CONTRACT §7.3) |
| 100 Ω | R6 | In series on the sync line; limits current if only one board is powered |
| 1 kΩ ±1 % | R9 | Calibration load: 5.1 mA, 26 mW at 5.1 V |

With R100 the range is 0–350 mA, so a 100 Ω load (51 mA) also works as a second calibration point; it was used as
one (gain 1.1300 vs 1.139 at 1 kΩ, `docs/energy_rig_bringup.md`).

## Breadboard wiring

Put the Nano across the centre gap. The INA219 module goes on the same breadboard. The ESP32 DevKit is usually too
wide for a breadboard, so leave it beside the board and use female-to-male dupont wires. Use one long side rail as
**3V3 (+)** and the other as **GND (−)**.

| # | From | To | Note |
|---|---|---|---|
| 1 | W1 VBUS wire, laptop end | INA219 screw terminal **VIN+** | Cut only the VBUS wire (golden on the Ubon cable used) |
| 2 | W1 VBUS wire, Nano end | INA219 screw terminal **VIN−** | The current goes through the module's R100, never the breadboard |
| 3 | (none) | | Option A only: a 1 Ω across VIN+/VIN− with R100 desoldered. Not used |
| 4 | ESP32 **3V3** | 3V3 rail (+) | The only thing that feeds the 3V3 rail |
| 5 | ESP32 **GND** | GND rail (−) | |
| 6 | INA219 **VCC** | 3V3 rail | Never from the Nano's 3V3 pin |
| 7 | INA219 **GND** | GND rail | |
| 8 | INA219 **SDA** | ESP32 **IO21** | Keep the wires ≤ 20 cm |
| 9 | INA219 **SCL** | ESP32 **IO22** | |
| 10 | Nano **GND** (pin 4 or 29) | GND rail | Ties all the grounds together |
| 11 | Nano **D2** | R6 100 Ω, one leg | R6's other leg goes in an empty row: the "D2 row" |
| 12 | R7 100 kΩ | from an empty row (the "SYNC row") to the GND rail | |
| 13 | ESP32 **IO4** | SYNC row | |
| 14 | Sync wire (one dupont) | from the D2 row to the SYNC row | **Normal runs.** This is JP4 position 1-2 on the schematic |
| 15 | W1 socket end → W2 → Nano micro-USB | | Laptop port 1 = `/dev/ttyACM0` |
| 16 | ESP32 micro-USB | laptop port 2 | `/dev/ttyUSB0` |

Leave the Nano's 3V3, +5V and VIN pins unwired. Anything on them would bypass the shunt or load the measured rail. Do
not bridge the Nano's VUSB jumper.

## No push button: the sync wire is the switch

A calibration window is opened by hand. Unplug W2, take out the D2-to-SYNC wire (#14), then plug a wire from the
**3V3 rail into the SYNC row**. Hold it there for at least 10 s, then pull it out. The logger prints one `W …` line
per window and ignores bounces shorter than 100 ms.

**Never connect 3V3 to the SYNC row while wire #14 is in place.** It would drive the Nano's D2 pin through 100 Ω.

## Shunt: option A or B

**Used: option B.** No external resistor; the module's own R100 is the shunt. Its nominal value is 0.100 Ω, but the
rig read 13.9 % high with it, so the calibrated effective value is **0.1139 Ω (±~1.2 %)**, from a 1 kΩ ±1 % load
without a multimeter (14 Nov 2026, `docs/energy_rig_bringup.md`). Range with PGA /1 (±40 mV): 0–350 mA. The Nano
measured 3.6 mA idle (WFI, USB on) and 7.3–9.5 mA busy in repeat 1, i.e. 0.4–1.1 mV across the shunt.

- Option A (not used): a 1 Ω external shunt with R100 desoldered (range 0–40 mA). If R100 stays, the two are in
  parallel (0.091 Ω).

`session_log.csv` records `shunt_ohm_measured = 0.1139`, and the logger is built with it:

```bash
arduino-cli compile --fqbn esp32:esp32:nodemcu-32s \
  --build-property "compiler.cpp.extra_flags=-DSHUNT_OHM_MEASURED=0.1139" logger
arduino-cli upload --fqbn esp32:esp32:nodemcu-32s -p /dev/ttyUSB0 logger
```

When a multimeter is available, measure the **same 1 kΩ** (a cheap DMM cannot resolve 0.1 Ω), repeat
`rig_check.py --cal <its value>`, and correct the shunt by the difference.

## Bring-up

1. **ESP32 alone.** Flash the logger, then open `/dev/ttyUSB0` at 115200 baud. Do a manual sync window with no load:
   you should get a `W` line with Pmean ≈ 0 (this checks the offset).
2. **Calibration (gate M3).** With W2 unplugged, plug R9 (1 kΩ) from the VIN− screw side (any breadboard row wired to
   VIN−) to GND. Run a few manual windows (`rig_check.py --cal 1000`). Expect `Pmean_uW` ≈ Vbus² / R9, about 26 000 µW
   at 5.1 V. Repeat to check the CV < 3 % gate, then **remove R9**.
3. **Nano.** Put wire #14 back and plug in W2. Flash the bench firmware, then run
   `python tools/dut.py /dev/ttyACM0 PING`, then `python tools/measure.py --dry-run`, then a real session.

The 1 kΩ (5.1 mA) and 100 Ω (51 mA) points gave gains of 1.139 and 1.130, so the rig is linear over 5–57 mA, which
covers the Nano's 3.6–9.5 mA.

## The measured cable: which wire goes where

Cut **one** data cable. The rule that makes this easy: inside one cable, each colour is the same conductor on both
sides of the cut. So rejoin **same colour to same colour**, except the 5 V wire (VBUS). Its laptop-side half goes
to the INA219 **Vin+** screw, and its Nano-side half to the **Vin−** screw. You only need to know which colour is
VBUS. Which data wire is D+ and which is D− doesn't matter as long as colours match.

| Cable | VBUS (5 V) | GND | Data |
|---|---|---|---|
| Red/black/green/yellow (not used) | red, probably; confirm below | black, probably | green and yellow (D+ and D−; yellow stands in for the usual white) |
| Ubon: black/green/yellowish/golden (**used**, MEASURED-1) | **golden** (found with the test below) | | |

**Find VBUS for sure (any cable):** keep the four cut ends on the **USB-A half** apart and taped individually, then
plug that half into a phone charger. Using a charger instead of the laptop means a slip can't short a laptop port.
On a multimeter in DC volts, the pair reading **+5 V** is VBUS (red probe) and GND (black probe). Unplug before
touching the ends again. Semi-transparent or bare golden strands are often enamel-coated: scrape them or tin them
with a hot iron before probing or joining.

**Joints:** solder and heat-shrink each data joint separately, keep the two data joints short (1–2 cm) and twisted
together, and reconnect the shield or bare drain wire if the cable has one. Twisted and taped joints are not
reliable enough for USB data. If the cable has a separate bare GND drain wire, join it to the black one.

## Checking the wiring: tools/rig_check.py

Run this after wiring and before every session. It talks to the Nano (bench firmware) and the logger, and turns each
symptom into the most likely wire at fault:

```bash
python tools/rig_check.py --shunt-ohm 0.1139             # full check, ports auto-detected (~1 min)
python tools/rig_check.py --cal 1000 --shunt-ohm 0.1139  # Nano unplugged, 1 kΩ (nominal; DMM value if you have one)
python tools/rig_check.py --watch                        # live readout; wiggle each joint, watch for jumps
python tools/rig_check.py --simulate list                # see what each fault looks like, no hardware
```

| # | Check | Catches |
|---|---|---|
| 1 | USB enumeration | broken or swapped data joints, no 5 V at the Nano, charge-only cable, brltty |
| 2 | 200 PINGs | intermittent data joint |
| 3 | logger banner | logger built with a different shunt value than `--shunt-ohm` |
| 4 | 10 s with D2 LOW | missing 100 kΩ pull-down, noisy SYNC row |
| 5 | one IDLE window | no sync, INA219 not answering on I2C, Vin+/Vin− swapped, wrong shunt value, low Vbus |
| 6 | 5 IDLE windows | loose screws and joints (power jumps between windows) |
| 7 | RUN vs IDLE | Nano powered around the shunt (5V pin, a second cable) |

## Files

| File | What it is |
|---|---|
| `tinyml_energy_rig.kicad_pro` / `.kicad_sch` | KiCad project, a one-sheet A3 schematic (KiCad 8 format) |
| `TinyML_Rig.kicad_sym`, `sym-lib-table` | Project symbol library (also embedded in the schematic) |
| `BOM.csv` | Parts with their roles |
| `preview/` | PNG/PDF of the sheet, drawn by `generator/check.py`, not by KiCad |
| `generator/` | `design.py` writes the schematic, `check.py` checks the net list and draws the preview |

On the schematic, JP1 (plug R9 in) and JP4 (where the sync wire goes) are breadboard links, not real parts.

The files were generated by script and checked with `generator/check.py`: the net list is as designed, no pin is
left unconnected, and every power net has a driver. They have not been opened in KiCad yet, so run ERC once after
opening them.

Regenerate: `python3 generator/design.py .`, then
`python3 generator/check.py tinyml_energy_rig.kicad_sch preview/tinyml_energy_rig_preview`.
