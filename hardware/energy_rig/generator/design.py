"""TinyML-Pareto energy rig: laptop -> (USB, VBUS through shunt) -> Nano 33 BLE ; INA219 -> ESP32 logger.

Implements CONTRACT.md v1.0-rc4 section 7.3 / 7.4 and logger/logger.ino. Run: python3 design.py <outdir>
"""
import json
import os
import sys

from schbuild import Sch
from symlib import make_library, write_kicad_sym

PROJECT = "tinyml_energy_rig"
OUT = sys.argv[1] if len(sys.argv) > 1 else "out"
os.makedirs(OUT, exist_ok=True)

L = make_library()
S = Sch(L, PROJECT, {
    "title": "TinyML-Pareto energy rig: laptop -> INA219 shunt -> Nano 33 BLE, ESP32 logger",
    "date": "2026-11-10", "rev": "B",
    "company": "EEE G513, BITS Pilani K K Birla Goa Campus",
    "comments": ["Implements CONTRACT.md v1.0-rc4 sec. 7.3/7.4 and logger/logger.ino pin map",
                 "Measurement owner: Hridayesh Kundu",
                 "Rev B: breadboard build with the parts on hand (no button, 4 resistors)"]})
w, st, j = S.wire, S.stub, S.junction

# =====================================================================================================================
# 1  HOST + MEASURED USB PATH
S.frame(9, 9, 158, 62, "1  HOST + MEASURED USB PATH (cables, off-board)")
H1 = S.place("Laptop_Host", "H1", 14, 20, value="Laptop (Ubuntu host)", in_bom=False, on_board=False)
for pin, net in [(1, "VBUS_HOST"), (2, "USB1_DM"), (3, "USB1_DP"), (4, "GND"),
                 (5, "VBUS_LOG"), (6, "USB2_DM"), (7, "USB2_DP"), (8, "GND")]:
    st(H1, pin, net)
S.text("Laptop, two USB ports (same machine for both, one ground reference):\n"
       "  port 1 -> Nano:  /dev/ttyACM0  (tools/dut.py, measure.py --port)\n"
       "  port 2 -> ESP32: /dev/ttyUSB0  (measure.py --logger)\n"
       "Do not use a USB hub on port 1: it adds its own supply noise.", 10, 44)

J1 = S.place("USB_A_Plug", "J1", 52, 20, value="W1 USB-A plug -> laptop port 1", on_board=False,
             desc="W1: USB-A male/female extension cable, red VBUS wire cut (BOM line)")
J2 = S.place("USB_A_Socket", "J2", 98, 20, value="W1 USB-A socket (Nano end)", in_bom=False, on_board=False)
J4 = S.place("USB_MicroB_Dev", "J4", 130, 20, value="A1 micro-USB (via W2)", in_bom=False, on_board=False,
             desc="The Nano's own micro-USB connector, reached with cable W2")
st(J1, 1, "VBUS_HOST", 3)
st(J2, 1, "VBUS_NANO", 3)
for y, net in [(22, "USB1_DM"), (24, "USB1_DP")]:
    w((62, y), (79, y)); S.label(net, 79, y, "r"); w((79, y), (96, y))
w((62, 26), (79, 26)); S.power("GND", 79, 26); w((79, 26), (96, 26))
S.text("Red VBUS wire of W1 cut: laptop end -> U1 VIN+, Nano end -> U1 VIN-", 64, 12)
S.text("W1 = USB-A male/female extension; D+, D-, GND and shield pass through uncut.\n"
       "W2 = ordinary USB-A to micro-B data cable from J2 into the Nano (A1).", 64, 30)
for pin, net in [(1, "VBUS_NANO"), (2, "USB1_DM"), (3, "USB1_DP"), (4, "GND")]:
    st(J4, pin, net)
S.power("PWR_FLAG", 126, 20)

# =====================================================================================================================
# 2  SHUNT + INA219 + CALIBRATION LOAD
S.frame(162, 9, 320, 76, "2  SHUNT, INA219 AND CALIBRATION LOAD (breadboard)")
S.label("VBUS_HOST", 184, 24, "l")
w((184, 24), (192, 24)); w((192, 24), (199, 24)); j(192, 24)
R1 = S.place("R_H", "R1", 202, 24, value="1R shunt (your 1 ohm)",
             desc="Shunt, clamped in U1's VIN+/VIN- screw terminal together with the two cut VBUS wire ends")
w((205, 24), (212, 24)); w((212, 24), (220, 24)); j(212, 24)
S.label("VBUS_NANO", 220, 24, "r")
w((192, 24), (192, 38)); w((212, 24), (212, 38))
U1 = S.place("INA219_Module", "U1", 188, 42, value="INA219 module (0x40)",
             ref_at=((188, 60), "left"), val_at=((188, 62), "left"),
             desc="INA219 breakout; its own R100 (0.1 ohm) removed when R1 = 1 ohm is used")
st(U1, 1, "+3V3"); st(U1, 2, "GND"); st(U1, 3, "I2C_SCL", 3); st(U1, 4, "I2C_SDA", 3)
S.text("R1 and both cut ends of W1's red wire go into U1's 2-way\n"
       "screw terminal: VIN+ = laptop end + R1 leg, VIN- = Nano end +\n"
       "other R1 leg. The current never crosses the breadboard.", 196, 12)
S.text("INA219: VIN+ = laptop side, VIN- = Nano side (high side).\n"
       "Bus voltage is read at VIN- = the Nano's VBUS, so P = V x I is\n"
       "the power delivered to the Nano. VCC from the ESP32 3V3, never\n"
       "from the Nano. Module keeps its 10k I2C pull-ups; A0 = A1 = GND.\n"
       "Shunt option A: R1 = 1 ohm and DESOLDER the module's R100.\n"
       "Option B (no soldering): omit R1, keep R100, logger 0.100 ohm.",
       166, 64)
x = 262
S.label("VBUS_NANO", x, 20, "u"); w((x, 20), (x, 22))
S.place("JP2_V", "JP1", x, 26, value="CAL link (plug only to calibrate)", footprint="", on_board=False,
        desc="Breadboard link: plug R9 between the VIN- row and GND only during calibration")
w((x, 30), (x, 32))
S.place("R", "R9", x, 35, value="1k (your 1k ohm): 5.0 mA", desc="Calibration load; measure it with a DMM")
S.power("GND", x, 38)
S.text("Calibration (gate M3): W2 unplugged from J2,\n"
       "R9 1k from the VIN- row to GND, sync wire on 3V3\n"
       "(JP4 2-3) for >= 10 s per window. Expect\n"
       "P = V_VIN-^2 / R9 (DMM), about 25 mW. Pull R9\n"
       "out before plugging the Nano back in.", 256, 44)

# =====================================================================================================================
# 3  DUT + SYNC
S.frame(9, 66, 158, 150, "3  DUT (Nano 33 BLE on the breadboard) + SYNC LINE (CONTRACT 7.3)")
A1 = S.place("Arduino_Nano_33_BLE", "A1", 26, 86, value="Arduino Nano 33 BLE (Rev2 ok)", footprint="", on_board=False,
             desc="DUT, on the breadboard across the centre gap; powered ONLY from its micro-USB (W2)")
for p in A1[1].pins:
    n = int(p.num)
    if n in (4, 29):
        st(A1, n, "GND")
    elif n != 5:
        st(A1, n, "NC")
R6 = S.place("R_H", "R6", 56, 106, value="100R", ref_at=((56, 102.2), "center"), val_at=((56, 104), "center"), desc="Series resistor on the sync line")
w((50, 106), (53, 106))
w((59, 106), (68, 106)); S.label("SYNC_NANO", 68, 106, "r"); w((68, 106), (76, 106))
JP4 = S.place("JP3", "JP4", 80, 106, value="sync wire: 1-2 RUN / 2-3 CAL", val_at=((78.5, 111.4), "right"),
              footprint="", on_board=False,
              desc="One dupont wire into the SYNC row: from R6 (normal) or from the 3V3 rail (manual window)")
w((84, 106), (88, 106)); S.power("+3V3", 88, 106)
w((80, 109), (80, 116)); w((80, 116), (86, 116)); w((86, 116), (104, 116)); j(86, 116)
S.label("SYNC", 104, 116, "r")
R7 = S.place("R", "R7", 86, 120, value="100k (your 100k ohm)", desc="External pull-down on ESP32 GPIO4 (CONTRACT 7.3)")
w((86, 116), (86, 117)); S.power("GND", 86, 123)
S.power("PWR_FLAG", 150, 142); S.power("GND", 150, 142)
S.text("A1 is powered ONLY through its micro-USB (W2), so every joule it uses passes R1.\n"
       "Pins 17 (3V3), 27 (+5V/VUSB) and 30 (VIN) stay unconnected: anything on them would\n"
       "bypass the shunt or load the measured rail. Do not bridge the Nano's VUSB jumper.\n"
       "Wire A1 pin 4 or 29 (GND) to the breadboard GND rail: that ties the grounds.", 10, 120)
S.text("D2 = sync output of firmware/bench: LOW at rest, HIGH only around measured loops.\n"
       "R7 100k + GPIO4 INPUT_PULLDOWN hold SYNC LOW while D2 floats (reset, upload).\n"
       "No push button: the SYNC row's source is one dupont wire. Normal = from R6 (JP4 1-2).\n"
       "Calibration = Nano unplugged, move that wire end to the 3V3 rail for >= 10 s, then\n"
       "pull it out (JP4 2-3). Never put the wire on 3V3 while the Nano's D2 is also wired:\n"
       "remove the D2-R6 wire first. The logger ignores bounces shorter than 100 ms.", 10, 131)

# =====================================================================================================================
# 4  ESP32 LOGGER
S.frame(162, 80, 320, 152, "4  ESP32 LOGGER (DevKit, off-board; logger/logger.ino)")
U2 = S.place("ESP32_DevKit_V1", "U2", 232, 92, value="ESP32 DOIT DevKit V1 (30-pin)", on_board=False,
             desc="ESP32 logger, off the breadboard, wired with dupont jumpers")
used = {14: "GND", 29: "GND", 31: "VBUS_LOG", 32: "USB2_DM", 33: "USB2_DP", 34: "GND",
        17: "I2C_SCL", 20: "I2C_SDA", 26: "SYNC"}
for p in U2[1].pins:
    n = int(p.num)
    if n == 30:
        st(U2, n, "+3V3", 6)
    elif n in used:
        st(U2, n, used[n], 3 if used[n].startswith(("I2C", "SYNC")) else 2)
    else:
        st(U2, n, "NC")
S.text("U2 sits off the breadboard (too wide): use\n"
       "female-male dupont wires from its pins:\n"
       "3V3 -> + rail, GND -> - rail, IO21 -> SDA,\n"
       "IO22 -> SCL, IO4 -> SYNC row.\n\n"
       "Powered from laptop USB port 2 (its own\n"
       "micro-USB). VIN (pin 15) stays unconnected:\n"
       "never feed the logger from the measured rail.", 164, 92)
S.text("logger.ino pin map (CONTRACT 7.3):\n"
       " IO21 = SDA, IO22 = SCL, 400 kHz\n"
       " IO4  = SYNC, INPUT_PULLDOWN, ISR on CHANGE\n"
       " INA219 at 0x40, 12-bit, PGA /1 (+-40 mV),\n"
       "   BRNG 16 V, about 1 kHz shunt+bus pairs\n"
       " W line within 500 ms of each D2 fall (7.4)\n\n"
       "Build with the measured shunt:\n"
       " arduino-cli compile --fqbn esp32:esp32:esp32\n"
       "  --build-property \"compiler.cpp.extra_flags=\n"
       "  -DSHUNT_OHM_MEASURED=1.012\" logger", 268, 94)

# =====================================================================================================================
# NOTES + TRACEABILITY
S.frame(9, 154, 232, 224, "BUILD NOTES (breadboard, parts on hand: Nano 33 BLE, INA219, ESP32, 1R / 100R / 1k / 100k)")
S.text(
    "1. Measured path: laptop port 1 -> W1 (USB-A M/F extension, only the red VBUS wire cut) -> U1 VIN+ terminal -> R1 -> U1 VIN- terminal\n"
    "   -> W1 -> W2 -> Nano. USB data never leaves the cable, so enumeration and the 115200-baud CDC link are unaffected.\n"
    "2. Shunt option A (default; matches logger.ino SHUNT_OHM_MEASURED = 1.000): R1 = your 1 ohm, clamped in U1's screw terminal,\n"
    "   and DESOLDER the module's own R100 (0.1 ohm). If R100 stays, the shunt is 0.091 ohm and every energy is 11x off.\n"
    "   Range with PGA /1 (+-40 mV): 0 to 40 mA; the Nano needs about 11 mA (vrig assumption), so R1 drops about 11 mV.\n"
    "   Option B (no soldering iron): leave R1 out, keep R100, build the logger with the DMM value (about 0.100); range 0-400 mA.\n"
    "3. Measure the shunt with a DMM every session -> session_log.csv shunt_ohm_measured and -DSHUNT_OHM_MEASURED.\n"
    "   Measure across the screw-terminal screws, Nano unplugged (subtract the DMM lead resistance).\n"
    "4. Grounds: one breadboard GND rail joins Nano GND (A1 pin 4 or 29), INA219 GND and ESP32 GND. Ground current does not\n"
    "   affect a high-side shunt. 3V3 rail: fed only by the ESP32 3V3 pin; feeds INA219 VCC and the manual sync wire.\n"
    "5. I2C: INA219 SDA/SCL rows -> ESP32 IO21/IO22 with wires <= 20 cm. The module's 10k pull-ups are enough at that length.\n"
    "6. Sync: A1 D2 -> R6 100R -> SYNC row -> ESP32 IO4, R7 100k from the SYNC row to GND. R6 limits current if only one board\n"
    "   is powered. D2 HIGH draws about 0.1 mA from the Nano, in IDLE and RUN windows alike, so it cancels in dE.\n"
    "7. Calibration with one load (1k, 5.0 mA) checks offset (open) and gain at one point. A 470R and a 220R (10.6 / 22.7 mA,\n"
    "   0.25 W ok) would add linearity points; your 100R would draw 50 mA, beyond the 40 mA range, so it is used for R6.\n"
    "8. Not fitted for lack of parts (add later if needed): INA219 input RC filter, extra 4k7 I2C pull-ups, local 100 nF\n"
    "   (the module already has one), push button (replaced by the sync wire).\n"
    "9. Bring-up order: ESP32 alone (W lines from the sync wire) -> 1k calibration -> Nano with bench firmware (PING, LAT)\n"
    "   -> python tools/measure.py --dry-run -> first real session.",
    10, 159)

S.frame(236, 156, 320, 198, "REQUIREMENT -> WHERE")
S.text(
    "CONTRACT 7.3 sync on GPIO4, INPUT_PULLDOWN + 100k  -> U2 IO4, R7\n"
    "CONTRACT 7.3 INA219 SDA 21 / SCL 22, 400 kHz        -> U2 IO21/IO22\n"
    "CONTRACT 7.3 Nano, ESP32, INA219 grounds tied       -> GND rail\n"
    "CONTRACT 7.3 D2 push-pull, LOW at rest, floats in reset -> R7, JP4\n"
    "CONTRACT 7.4 / logger.ino: INA219 on Nano 5 V USB,  -> R1, U1\n"
    "   VIN+ PC side, VIN- Nano side                        (VIN+/VIN-)\n"
    "logger.ino INA219_ADDR 0x40                         -> U1 A0/A1 open\n"
    "logger.ino SHUNT_OHM_MEASURED = 1.000               -> R1 1 ohm\n"
    "Sprint 2 plan, gate M3: validate on resistor loads  -> R9 1k, JP4 2-3\n"
    "measure.py --port ttyACM0 / --logger ttyUSB0        -> H1 ports 1/2\n"
    "session_log.csv shunt_ohm_measured                  -> note 3",
    238, 161)

S.write(os.path.join(OUT, PROJECT + ".kicad_sch"), paper="A3")
write_kicad_sym(L, os.path.join(OUT, "TinyML_Rig.kicad_sym"))
with open(os.path.join(OUT, "sym-lib-table"), "w") as fh:
    fh.write('(sym_lib_table\n  (version 7)\n  (lib (name "TinyML_Rig")(type "KiCad")(uri "${KIPRJMOD}/TinyML_Rig.kicad_sym")'
             '(options "")(descr "TinyML-Pareto energy rig symbols"))\n)\n')
pro = {
    "board": {"design_settings": {"defaults": {}, "rules": {}}},
    "boards": [], "cvpcb": {"equivalence_files": []},
    "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
    "meta": {"filename": PROJECT + ".kicad_pro", "version": 1},
    "net_settings": {"classes": [{"name": "Default", "clearance": 0.2, "track_width": 0.25, "via_diameter": 0.8,
                                  "via_drill": 0.4, "wire_width": 6, "bus_width": 12, "line_style": 0,
                                  "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_width": 0.2,
                                  "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25, "pcb_color": "rgba(0, 0, 0, 0.000)",
                                  "schematic_color": "rgba(0, 0, 0, 0.000)"}],
                     "meta": {"version": 3}},
    "pcbnew": {"last_paths": {}, "page_layout_descr_file": ""},
    "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
    "sheets": [[S.root, "Root"]],
    "text_variables": {},
}
with open(os.path.join(OUT, PROJECT + ".kicad_pro"), "w") as fh:
    json.dump(pro, fh, indent=2)
print("written", OUT)
