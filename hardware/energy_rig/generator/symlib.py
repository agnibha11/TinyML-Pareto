"""Symbol definitions for the TinyML-Pareto energy rig (KiCad 8 s-expression format).

Pin coordinates are in grid units (1 g = 1.27 mm), lib coordinates (y up). Graphics are in mm.
"""
G = 1.27
LIB = "TinyML_Rig"
FONT = "(effects (font (size 1.27 1.27)))"


def f(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


class Pin:
    def __init__(self, num, name, etype, x, y, angle, length=2, hidden=False):
        self.num, self.name, self.etype = str(num), name, etype
        self.x, self.y, self.angle, self.length, self.hidden = x, y, angle, length, hidden

    def sexpr(self):
        h = " hide" if self.hidden else ""
        return (f"(pin {self.etype} line (at {f(self.x*G)} {f(self.y*G)} {self.angle}) (length {f(self.length*G)}){h} "
                f"(name {q(self.name)} {FONT}) (number {q(self.num)} {FONT}))")


def rect(x1, y1, x2, y2, fill="background", w=0.254):
    return (f"(rectangle (start {f(x1)} {f(y1)}) (end {f(x2)} {f(y2)}) (stroke (width {f(w)}) (type default)) "
            f"(fill (type {fill})))")


def poly(pts, w=0.254, fill="none"):
    p = " ".join(f"(xy {f(x)} {f(y)})" for x, y in pts)
    return f"(polyline (pts {p}) (stroke (width {f(w)}) (type default)) (fill (type {fill})))"


def circle(x, y, r, w=0.0, fill="none"):
    return f"(circle (center {f(x)} {f(y)}) (radius {f(r)}) (stroke (width {f(w)}) (type default)) (fill (type {fill})))"


def gtext(s, x, y, size=1.27):
    return f"(text {q(s)} (at {f(x)} {f(y)} 0) (effects (font (size {f(size)} {f(size)}))))"


class LibSym:
    def __init__(self, name, ref, value, footprint="", desc="", pins=(), graphics=(), power=False,
                 hide_names=False, hide_numbers=False, name_offset=1.016, kind="box", body=None):
        self.name, self.ref, self.value, self.footprint, self.desc = name, ref, value, footprint, desc
        self.pins, self.graphics = list(pins), list(graphics)
        self.power, self.hide_names, self.hide_numbers, self.name_offset = power, hide_names, hide_numbers, name_offset
        self.kind = kind
        self.body = body  # (x1, y1, x2, y2) in grid, lib coords, for boxes (used for property placement)

    def pin(self, num):
        for p in self.pins:
            if p.num == str(num):
                return p
        raise KeyError(f"{self.name}: pin {num}")

    def sexpr(self, with_prefix=True):
        nm = f"{LIB}:{self.name}" if with_prefix else self.name
        out = [f"(symbol {q(nm)}"]
        if self.power:
            out.append("(power)")
        if self.hide_numbers:
            out.append("(pin_numbers hide)")
        out.append(f"(pin_names (offset {f(self.name_offset)}){' hide' if self.hide_names else ''})")
        out.append("(exclude_from_sim no) (in_bom yes) (on_board yes)")
        hid = "(effects (font (size 1.27 1.27)) hide)"
        out.append(f"(property \"Reference\" {q(self.ref)} (at 0 {f(3.81)} 0) {FONT if not self.power else hid})")
        out.append(f"(property \"Value\" {q(self.value)} (at 0 {f(-3.81)} 0) {FONT})")
        out.append(f"(property \"Footprint\" {q(self.footprint)} (at 0 0 0) {hid})")
        out.append(f"(property \"Datasheet\" \"~\" (at 0 0 0) {hid})")
        out.append(f"(property \"Description\" {q(self.desc)} (at 0 0 0) {hid})")
        out.append(f"(symbol {q(self.name + '_0_1')} " + " ".join(self.graphics) + ")")
        out.append(f"(symbol {q(self.name + '_1_1')} " + " ".join(p.sexpr() for p in self.pins) + ")")
        out.append(")")
        return "\n".join(out)


# ---------------------------------------------------------------------------------------------------------------------
def box(name, ref, value, footprint, desc, left=(), right=(), top=(), width=16, top_text=None):
    """Rectangular module symbol. left/right: lists of (num, name, type) or None for a gap, top-to-bottom,
    first slot at y = 0, slots every 2 g going down. top: list of (num, name, type, x_grid) pins on the top edge."""
    n = max(len(left), len(right), 1)
    y_top, y_bot = 2, -2 * (n - 1) - 2
    pins = []
    for i, p in enumerate(left):
        if p:
            pins.append(Pin(p[0], p[1], p[2], -2, -2 * i, 0, 2))
    for i, p in enumerate(right):
        if p:
            pins.append(Pin(p[0], p[1], p[2], width + 2, -2 * i, 180, 2))
    for p in top:
        pins.append(Pin(p[0], p[1], p[2], p[3], y_top + 2, 270, 2))
    gr = [rect(0, y_top * G, width * G, y_bot * G)]
    if top_text:
        gr.append(gtext(top_text, width * G / 2, (y_bot - 1.2) * G))
    s = LibSym(name, ref, value, footprint, desc, pins, gr, body=(0, y_top, width, y_bot))
    return s


def make_library():
    L = {}
    # --- passives -----------------------------------------------------------------------------------------------
    L["R"] = LibSym("R", "R", "R", "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", "Resistor",
                    [Pin(1, "~", "passive", 0, 3, 270, 1), Pin(2, "~", "passive", 0, -3, 90, 1)],
                    [rect(-1.016, -2.54, 1.016, 2.54, fill="none")], hide_names=True, hide_numbers=True, name_offset=0,
                    kind="R")
    L["R_H"] = LibSym("R_H", "R", "R", "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                      "Resistor (horizontal drawing)",
                      [Pin(1, "~", "passive", -3, 0, 0, 1), Pin(2, "~", "passive", 3, 0, 180, 1)],
                      [rect(-2.54, -1.016, 2.54, 1.016, fill="none")], hide_names=True, hide_numbers=True,
                      name_offset=0, kind="R_H")
    L["C"] = LibSym("C", "C", "C", "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm", "Unpolarized capacitor",
                    [Pin(1, "~", "passive", 0, 3, 270, 2.4), Pin(2, "~", "passive", 0, -3, 90, 2.4)],
                    [poly([(-2.032, 0.762), (2.032, 0.762)], 0.508), poly([(-2.032, -0.762), (2.032, -0.762)], 0.508)],
                    hide_names=True, hide_numbers=True, name_offset=0.254, kind="C")
    L["C_H"] = LibSym("C_H", "C", "C", "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm",
                      "Unpolarized capacitor (horizontal drawing)",
                      [Pin(1, "~", "passive", -3, 0, 0, 2.4), Pin(2, "~", "passive", 3, 0, 180, 2.4)],
                      [poly([(-0.762, -2.032), (-0.762, 2.032)], 0.508), poly([(0.762, -2.032), (0.762, 2.032)], 0.508)],
                      hide_names=True, hide_numbers=True, name_offset=0.254, kind="C_H")
    # --- jumpers, switch, test point -----------------------------------------------------------------------------
    L["JP2_V"] = LibSym("JP2_V", "JP", "Jumper", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
                        "2-pin jumper header (vertical drawing)",
                        [Pin(1, "A", "passive", 0, 4, 270, 2), Pin(2, "B", "passive", 0, -4, 90, 2)],
                        [circle(0, 1.524, 0.762), circle(0, -1.524, 0.762),
                         poly([(1.27, 2.286), (1.778, 2.286), (1.778, -2.286), (1.27, -2.286)], 0.254)],
                        hide_names=True, name_offset=0, kind="JP2_V")
    L["JP3"] = LibSym("JP3", "JP", "Jumper_3", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
                      "3-pin selector header: shunt 1-2 or 2-3",
                      [Pin(1, "A", "passive", -4, 0, 0, 1.6), Pin(3, "C", "passive", 4, 0, 180, 1.6),
                       Pin(2, "COM", "passive", 0, -3, 90, 2.4)],
                      [circle(-2.54, 0, 0.635), circle(0, 0, 0.635), circle(2.54, 0, 0.635),
                       poly([(-3.302, 1.27), (-3.302, 1.778), (0.762, 1.778), (0.762, 1.27)], 0.254)],
                      hide_names=True, name_offset=0, kind="JP3")
    L["SW_Push"] = LibSym("SW_Push", "SW", "SW_Push", "Button_Switch_THT:SW_PUSH_6mm", "Push button, normally open",
                          [Pin(1, "1", "passive", -4, 0, 0, 2), Pin(2, "2", "passive", 4, 0, 180, 2)],
                          [circle(-2.032, 0, 0.508), circle(2.032, 0, 0.508),
                           poly([(-2.54, 1.27), (2.54, 1.27)], 0.254), poly([(0, 1.27), (0, 3.048)], 0.254)],
                          hide_names=True, hide_numbers=True, name_offset=0, kind="SW")
    L["TestPoint"] = LibSym("TestPoint", "TP", "TestPoint", "TestPoint:TestPoint_THTPad_D2.0mm_Drill1.0mm",
                            "Test point",
                            [Pin(1, "1", "passive", 0, 0, 90, 1.6)],
                            [circle(0, 2.794, 0.762, w=0.254)], hide_names=True, hide_numbers=True, name_offset=0,
                            kind="TP")
    # --- power symbols ---------------------------------------------------------------------------------------------
    L["GND"] = LibSym("GND", "#PWR", "GND", "", "Power symbol: global net GND",
                      [Pin(1, "GND", "power_in", 0, 0, 270, 0, hidden=True)],
                      [poly([(0, 0), (0, -1.27), (1.27, -1.27), (0, -2.54), (-1.27, -1.27), (0, -1.27)], 0)],
                      power=True, hide_names=True, hide_numbers=True, name_offset=0, kind="GND")
    L["+3V3"] = LibSym("+3V3", "#PWR", "+3V3", "", "Power symbol: global net +3V3 (ESP32 DevKit regulator)",
                       [Pin(1, "+3V3", "power_in", 0, 0, 90, 0, hidden=True)],
                       [poly([(-0.762, 1.27), (0, 2.54)], 0), poly([(0, 0), (0, 2.54)], 0),
                        poly([(0, 2.54), (0.762, 1.27)], 0)],
                       power=True, hide_names=True, hide_numbers=True, name_offset=0, kind="PWRUP")
    L["PWR_FLAG"] = LibSym("PWR_FLAG", "#FLG", "PWR_FLAG", "", "Marks a net as driven (ERC)",
                           [Pin(1, "pwr", "power_out", 0, 0, 90, 0)],
                           [poly([(0, 0), (0, 1.27), (-1.016, 1.905), (0, 2.54), (1.016, 1.905), (0, 1.27)], 0)],
                           power=True, hide_names=True, hide_numbers=True, name_offset=0, kind="FLAG")

    # --- connectors and modules ------------------------------------------------------------------------------------
    pw, ps = "passive", "passive"
    L["Conn_Term_2"] = box("Conn_Term_2", "J", "Term_2", "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
                           "2-pole screw terminal", right=[(1, "1", pw), (2, "2", pw)], width=6)
    L["Conn_01x05"] = box("Conn_01x05", "J", "Conn_01x05", "Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
                          "1x05 male header", left=[(1, "+3V3", pw), (2, "SYNC", pw), (3, "SDA", pw), (4, "SCL", pw),
                                                    (5, "GND", pw)], width=8)
    L["USB_A_Plug"] = box("USB_A_Plug", "J", "USB_A_Plug", "", "USB-A plug (cable end)",
                          right=[(1, "VBUS", ps), (2, "D-", ps), (3, "D+", ps), (4, "GND", ps)], width=8,
                          top_text="USB-A plug")
    L["USB_A_Socket"] = box("USB_A_Socket", "J", "USB_A_Socket", "", "USB-A receptacle (cable end)",
                            left=[(1, "VBUS", ps), (2, "D-", ps), (3, "D+", ps), (4, "GND", ps)], width=8,
                            top_text="USB-A socket")
    L["USB_MicroB_Dev"] = box("USB_MicroB_Dev", "J", "USB_MicroB", "", "Device-side micro-USB B (on a module)",
                              left=[(1, "VBUS", "power_in"), (2, "D-", "bidirectional"), (3, "D+", "bidirectional"),
                                    (4, "GND", ps)], width=8, top_text="micro-USB")
    L["Laptop_Host"] = box("Laptop_Host", "H", "Laptop", "", "USB host (two ports used)",
                           right=[(1, "USB1 VBUS", "power_out"), (2, "USB1 D-", "bidirectional"),
                                  (3, "USB1 D+", "bidirectional"), (4, "USB1 GND", ps), None, None,
                                  (5, "USB2 VBUS", "power_out"), (6, "USB2 D-", "bidirectional"),
                                  (7, "USB2 D+", "bidirectional"), (8, "USB2 GND", ps)], width=14,
                           top_text="host PC")
    bi = "bidirectional"
    L["INA219_Module"] = box("INA219_Module", "U", "INA219_Module",
                             "Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical",
                             "INA219 breakout, header VCC GND SCL SDA VIN- VIN+ (Adafruit #904 order; check clone silkscreen)",
                             right=[(1, "VCC", "power_in"), None, (2, "GND", "power_in"), None, None, (3, "SCL", bi),
                                    (4, "SDA", bi)],
                             top=[(6, "VIN+", "input", 4), (5, "VIN-", "input", 24)], width=28,
                             top_text="INA219 (I2C 0x40)")
    # Arduino Nano 33 BLE. Pin numbers = KiCad Module:Arduino_Nano footprint (1 = D1/TX ... 30 = VIN).
    # Drawn top view with the USB end at the top: left column 16..30, right column 15..1.
    nano_left = [(16, "D13", bi), (17, "3V3", "power_out"), (18, "AREF", ps), (19, "A0", bi), (20, "A1", bi),
                 (21, "A2", bi), (22, "A3", bi), (23, "A4/SDA", bi), (24, "A5/SCL", bi), (25, "A6", bi),
                 (26, "A7", bi), (27, "+5V(VUSB)", ps), (28, "~{RESET}", "input"), (29, "GND", "power_in"),
                 (30, "VIN", ps)]
    nano_right = [(15, "D12", bi), (14, "D11", bi), (13, "D10", bi), (12, "D9", bi), (11, "D8", bi), (10, "D7", bi),
                  (9, "D6", bi), (8, "D5", bi), (7, "D4", bi), (6, "D3", bi), (5, "D2", bi), (4, "GND", "power_in"),
                  (3, "~{RESET}", "input"), (2, "D0/RX", bi), (1, "D1/TX", bi)]
    L["Arduino_Nano_33_BLE"] = box("Arduino_Nano_33_BLE", "A", "Arduino_Nano_33_BLE", "Module:Arduino_Nano",
                                   "Arduino Nano 33 BLE (nRF52840), Nano footprint; USB end at the top",
                                   left=nano_left, right=nano_right, width=22, top_text="Nano 33 BLE")
    # ESP32 DOIT DevKit V1, 30 pins, antenna up, USB down. 31..34 = its micro-USB (off-board module).
    esp_left = [(1, "EN", "input"), (2, "VP/IO36", "input"), (3, "VN/IO39", "input"), (4, "IO34", "input"),
                (5, "IO35", "input"), (6, "IO32", bi), (7, "IO33", bi), (8, "IO25", bi), (9, "IO26", bi),
                (10, "IO27", bi), (11, "IO14", bi), (12, "IO12", bi), (13, "IO13", bi), (14, "GND", "power_in"),
                (15, "VIN(5V)", "power_in"), None, (31, "USB VBUS", "power_in"), (32, "USB D-", bi),
                (33, "USB D+", bi), (34, "USB GND", ps)]
    esp_right = [(16, "IO23", bi), (17, "IO22/SCL", bi), (18, "TX0/IO1", bi), (19, "RX0/IO3", bi),
                 (20, "IO21/SDA", bi), (21, "IO19", bi), (22, "IO18", bi), (23, "IO5", bi), (24, "TX2/IO17", bi),
                 (25, "RX2/IO16", bi), (26, "IO4", bi), (27, "IO2", bi), (28, "IO15", bi), (29, "GND", "power_in"),
                 (30, "3V3", "power_out")]
    L["ESP32_DevKit_V1"] = box("ESP32_DevKit_V1", "U", "ESP32_DevKit_V1", "",
                               "ESP32 DOIT DevKit V1 (30-pin); 31-34 = its micro-USB", left=esp_left, right=esp_right,
                               width=22, top_text="ESP32 DevKit (logger)")
    return L


def write_kicad_sym(L, path):
    body = "\n".join(s.sexpr(with_prefix=False) for s in L.values())
    with open(path, "w") as fh:
        fh.write('(kicad_symbol_lib\n(version 20231120)\n(generator "kicad_symbol_editor")\n(generator_version "8.0")\n')
        fh.write(body)
        fh.write("\n)\n")
