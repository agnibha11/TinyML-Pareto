"""Tiny KiCad 8 schematic writer. All placement coordinates are grid units (1 g = 1.27 mm); rotation 0 only."""
import uuid

from symlib import G, LIB, f, q

NS = uuid.UUID("6f1c7b2e-4d0a-4b5e-9a8f-3e2d1c0b9a87")
FONT = "(font (size 1.27 1.27))"


class Sch:
    def __init__(self, lib, project, title_block):
        self.lib, self.project, self.tb = lib, project, title_block
        self._n = 0
        self.root = self.uid("root")
        self.items = []         # raw s-expr strings
        self.insts = []         # (ref, LibSym, X, Y)
        self.wires = []         # ((x1,y1),(x2,y2)) grid
        self.used = set()
        self.pwr_n = 0
        self.flg_n = 0

    def uid(self, tag=None):
        self._n += 1
        return str(uuid.uuid5(NS, f"{tag}-{self._n}"))

    # ---------------------------------------------------------------------------------------------------- symbols
    def place(self, sym_name, ref, X, Y, value=None, footprint=None, in_bom=True, on_board=True, dnp=False,
              ref_at=None, val_at=None, desc=None, show_value=True):
        s = self.lib[sym_name]
        self.used.add(sym_name)
        value = s.value if value is None else value
        fp = s.footprint if footprint is None else footprint
        xm, ym = X * G, Y * G
        if ref_at is None or val_at is None:
            if s.kind == "R" or s.kind == "C":
                ra, va = ((X + 2, Y - 1), "left"), ((X + 2, Y + 1), "left")
            elif s.kind in ("R_H", "C_H"):
                ra, va = ((X, Y - 2), "center"), ((X, Y + 2.2), "center")
            elif s.kind == "JP2_V":
                ra, va = ((X + 2.5, Y - 1), "left"), ((X + 2.5, Y + 1), "left")
            elif s.kind == "JP3":
                ra, va = ((X, Y - 2.6), "center"), ((X + 5, Y + 2.2), "left")
            elif s.kind == "SW":
                ra, va = ((X, Y - 3.4), "center"), ((X, Y + 2), "center")
            elif s.kind == "TP":
                ra, va = ((X + 1.4, Y - 3), "left"), ((X + 1.4, Y - 1.4), "left")
            elif s.kind in ("GND", "PWRUP", "FLAG"):
                if s.kind == "GND":
                    ra, va = ((X, Y + 4), "center"), ((X, Y + 3.1), "center")
                else:
                    ra, va = ((X, Y - 4.5), "center"), ((X, Y - 2.8), "center")
            else:  # box: above the body
                x1, ytop, x2, ybot = s.body
                ra, va = ((X + x1, Y - ytop - 3), "left"), ((X + x1, Y - ytop - 1), "left")
            ref_at = ref_at or ra
            val_at = val_at or va
        (rx, ry), rj = ref_at if isinstance(ref_at[0], tuple) else (ref_at, "left")
        (vx, vy), vj = val_at if isinstance(val_at[0], tuple) else (val_at, "left")
        ref_hide = " hide" if s.power else ""
        val_hide = "" if show_value else " hide"
        if s.kind == "FLAG":
            val_hide = ""

        def just(j):
            return "" if j == "center" else f" (justify {j})"
        u = self.uid(ref)
        lines = [
            "(symbol",
            f"(lib_id {q(LIB + ':' + s.name)})",
            f"(at {f(xm)} {f(ym)} 0)",
            "(unit 1)",
            "(exclude_from_sim no)",
            f"(in_bom {'yes' if in_bom else 'no'})",
            f"(on_board {'yes' if on_board else 'no'})",
            f"(dnp {'yes' if dnp else 'no'})",
            f"(uuid {q(u)})",
            f"(property \"Reference\" {q(ref)} (at {f(rx*G)} {f(ry*G)} 0) (effects {FONT}{just(rj)}{ref_hide}))",
            f"(property \"Value\" {q(value)} (at {f(vx*G)} {f(vy*G)} 0) (effects {FONT}{just(vj)}{val_hide}))",
            f"(property \"Footprint\" {q(fp)} (at {f(xm)} {f(ym)} 0) (effects {FONT} hide))",
            f"(property \"Datasheet\" \"~\" (at {f(xm)} {f(ym)} 0) (effects {FONT} hide))",
            f"(property \"Description\" {q(desc or s.desc)} (at {f(xm)} {f(ym)} 0) (effects {FONT} hide))",
        ]
        for p in s.pins:
            lines.append(f"(pin {q(p.num)} (uuid {q(self.uid(ref + p.num))}))")
        lines.append(f"(instances (project {q(self.project)} (path {q('/' + self.root)} (reference {q(ref)}) (unit 1))))")
        lines.append(")")
        self.items.append("\n".join(lines))
        self.insts.append((ref, s, X, Y))
        return (ref, s, X, Y)

    @staticmethod
    def pin_xy(inst, num):
        ref, s, X, Y = inst
        p = s.pin(num)
        return (X + p.x, Y - p.y)

    @staticmethod
    def outward(inst, num):
        p = inst[1].pin(num)
        return {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}[p.angle]

    # ---------------------------------------------------------------------------------------------- primitives
    def wire(self, *pts):
        for a, b in zip(pts, pts[1:]):
            if a == b:
                continue
            assert a[0] == b[0] or a[1] == b[1], (a, b)
            self.wires.append((a, b))
            self.items.append(f"(wire (pts (xy {f(a[0]*G)} {f(a[1]*G)}) (xy {f(b[0]*G)} {f(b[1]*G)})) "
                              f"(stroke (width 0) (type default)) (uuid {q(self.uid('w'))}))")

    def junction(self, x, y):
        self.items.append(f"(junction (at {f(x*G)} {f(y*G)}) (diameter 0) (color 0 0 0 0) (uuid {q(self.uid('j'))}))")

    def nc(self, x, y):
        self.items.append(f"(no_connect (at {f(x*G)} {f(y*G)}) (uuid {q(self.uid('nc'))}))")

    def label(self, name, x, y, direction):
        """direction = where the text extends from the anchor: 'r', 'l', 'u', 'd'."""
        ang, j = {"r": (0, "left bottom"), "l": (180, "right bottom"), "u": (90, "left bottom"),
                  "d": (270, "right bottom")}[direction]
        self.items.append(f"(label {q(name)} (at {f(x*G)} {f(y*G)} {ang}) (fields_autoplaced yes) "
                          f"(effects {FONT} (justify {j})) (uuid {q(self.uid('lbl'))}))")

    def power(self, kind, x, y):
        if kind == "PWR_FLAG":
            self.flg_n += 1
            ref = f"#FLG0{self.flg_n}"
        else:
            self.pwr_n += 1
            ref = f"#PWR0{self.pwr_n:02d}"
        return self.place(kind, ref, x, y, in_bom=False, on_board=False)

    def text(self, s, x, y, size=1.27):
        self.items.append(f"(text {q(s)} (exclude_from_sim no) (at {f(x*G)} {f(y*G)} 0) "
                          f"(effects (font (size {f(size)} {f(size)})) (justify left top)) (uuid {q(self.uid('t'))}))")

    def frame(self, x1, y1, x2, y2, title=None):
        for a, b in (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)), ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))):
            self.items.append(f"(polyline (pts (xy {f(a[0]*G)} {f(a[1]*G)}) (xy {f(b[0]*G)} {f(b[1]*G)})) "
                              f"(stroke (width 0.254) (type dash)) (uuid {q(self.uid('fr'))}))")
        if title:
            self.text(title, x1 + 1, y1 + 1, size=2.0)

    # ---------------------------------------------------------------------------------------------- helpers
    def stub(self, inst, num, net, length=2, label_dir=None):
        """Short wire from a pin outward, ending in a label, a power symbol, or (net == 'NC') a no-connect flag."""
        x, y = self.pin_xy(inst, num)
        if net == "NC":
            self.nc(x, y)
            return (x, y)
        dx, dy = self.outward(inst, num)
        ex, ey = x + dx * length, y + dy * length
        self.wire((x, y), (ex, ey))
        if net in ("GND", "+3V3"):
            self.power(net, ex, ey)
        else:
            d = label_dir or {(1, 0): "r", (-1, 0): "l", (0, -1): "u", (0, 1): "d"}[(dx, dy)]
            self.label(net, ex, ey, d)
        return (ex, ey)

    # ---------------------------------------------------------------------------------------------- output
    def write(self, path, paper="A3"):
        tb = self.tb
        tbs = [f"(title {q(tb['title'])})", f"(date {q(tb['date'])})", f"(rev {q(tb['rev'])})",
               f"(company {q(tb['company'])})"]
        for i, c in enumerate(tb.get("comments", []), 1):
            tbs.append(f"(comment {i} {q(c)})")
        libs = "\n".join(self.lib[n].sexpr() for n in sorted(self.used))
        with open(path, "w") as fh:
            fh.write("(kicad_sch\n(version 20231120)\n(generator \"eeschema\")\n(generator_version \"8.0\")\n")
            fh.write(f"(uuid {q(self.root)})\n(paper {q(paper)})\n")
            fh.write("(title_block " + " ".join(tbs) + ")\n")
            fh.write("(lib_symbols\n" + libs + "\n)\n")
            fh.write("\n".join(self.items))
            fh.write("\n(sheet_instances (path \"/\" (page \"1\")))\n)\n")
