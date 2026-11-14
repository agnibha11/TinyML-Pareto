"""Independent check of a generated .kicad_sch: parse it, rebuild connectivity the way eeschema does for this subset
(rotation-0 symbols, wires joined at endpoints, junctions, labels, power symbols), list nets, flag problems, and
render a PNG/PDF preview. Usage: python3 check.py file.kicad_sch preview_basename"""
import re
import sys
from collections import defaultdict

TOK = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')


def parse(text):
    stack, cur = [], []
    for m in TOK.finditer(text):
        t = m.group(0)
        if t == "(":
            stack.append(cur); cur = []
        elif t == ")":
            done = cur; cur = stack.pop(); cur.append(done)
        else:
            if t.startswith('"'):
                t = bytes(t[1:-1], "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
                t = ("S", t)
            cur.append(t)
    assert not stack, "unbalanced"
    assert len(cur) == 1
    return cur[0]


def val(t):
    return t[1] if isinstance(t, tuple) else t


def find(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def one(node, key):
    r = find(node, key)
    return r[0] if r else None


def main(path, prev):
    root = parse(open(path, encoding="utf-8").read())
    assert root[0] == "kicad_sch"
    libs = {}
    for s in find(one(root, "lib_symbols"), "symbol"):
        name = val(s[1]); pins = []; gfx = []
        power = any(c == ["power"] for c in s if isinstance(c, list))
        for sub in find(s, "symbol"):
            for p in find(sub, "pin"):
                at = one(p, "at"); ln = float(one(p, "length")[1])
                nm = val(one(p, "name")[1]); num = val(one(p, "number")[1])
                pins.append(dict(etype=p[1], x=float(at[1]), y=float(at[2]), a=int(float(at[3])), len=ln, name=nm,
                                 num=num, hidden="hide" in p))
            for g in sub[2:]:
                if isinstance(g, list) and g[0] in ("rectangle", "polyline", "circle", "text"):
                    gfx.append(g)
        libs[name] = dict(pins=pins, power=power, gfx=gfx)

    def props(n):
        return {val(p[1]): (val(p[2]), p) for p in find(n, "property")}

    insts = []
    for s in find(root, "symbol"):
        lid = val(one(s, "lib_id")[1]); at = one(s, "at")
        assert float(at[3]) == 0, "rotation not handled"
        pr = props(s)
        insts.append(dict(lib=lid, X=float(at[1]), Y=float(at[2]), ref=pr["Reference"][0], value=pr["Value"][0],
                          fp=pr["Footprint"][0], props=pr, on_board=val(one(s, "on_board")[1]),
                          in_bom=val(one(s, "in_bom")[1]), dnp=val(one(s, "dnp")[1])))
    wires = [((float(w[1][1][1]), float(w[1][1][2])), (float(w[1][2][1]), float(w[1][2][2])))
             for w in [[c[0], one(c, "pts")] for c in find(root, "wire")]]
    wires = []
    for c in find(root, "wire"):
        pts = one(c, "pts"); a, b = pts[1], pts[2]
        wires.append(((round(float(a[1]), 3), round(float(a[2]), 3)), (round(float(b[1]), 3), round(float(b[2]), 3))))
    junc = {(round(float(one(c, "at")[1]), 3), round(float(one(c, "at")[2]), 3)) for c in find(root, "junction")}
    ncs = {(round(float(one(c, "at")[1]), 3), round(float(one(c, "at")[2]), 3)) for c in find(root, "no_connect")}
    labels = [(val(c[1]), (round(float(one(c, "at")[1]), 3), round(float(one(c, "at")[2]), 3)), int(float(one(c, "at")[3])))
              for c in find(root, "label")]
    texts = [(val(c[1]), float(one(c, "at")[1]), float(one(c, "at")[2]),
              float(one(one(one(c, "effects"), "font"), "size")[1])) for c in find(root, "text")]
    polys = []
    for c in find(root, "polyline"):
        pts = [(float(p[1]), float(p[2])) for p in one(c, "pts")[1:]]
        polys.append(pts)

    # ---------------- connectivity
    parent = {}

    def fnd(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    def uni(a, b):
        parent[fnd(a)] = fnd(b)

    pin_pts = []   # (ref, num, name, etype, point, lib)
    for ins in insts:
        lib = libs[ins["lib"]]
        for p in lib["pins"]:
            pt = (round(ins["X"] + p["x"], 3), round(ins["Y"] - p["y"], 3))
            pin_pts.append((ins["ref"], p["num"], p["name"], p["etype"], pt, ins))
    errors, warns = [], []
    # wires: nodes at endpoints; check endpoints-on-segment
    def on_seg(pt, a, b):
        if pt in (a, b):
            return False
        if a[0] == b[0] == pt[0]:
            return min(a[1], b[1]) < pt[1] < max(a[1], b[1])
        if a[1] == b[1] == pt[1]:
            return min(a[0], b[0]) < pt[0] < max(a[0], b[0])
        return False
    for a, b in wires:
        uni(("P", a), ("P", b))
    special = [pp[4] for pp in pin_pts] + [l[1] for l in labels] + list(junc)
    for a, b in wires:
        for pt in special + [e for w in wires for e in w]:
            if on_seg(pt, a, b):
                if pt in junc:
                    uni(("P", pt), ("P", a))
                else:
                    warns.append(f"point {pt} lies on wire {a}-{b} without a junction")
    for name, pt, _ in labels:
        uni(("P", pt), ("L", name))
    for ref, num, name, et, pt, ins in pin_pts:
        node = ("PIN", ref, num)
        uni(node, ("P", pt))
        if libs[ins["lib"]]["power"] and et == "power_in":
            uni(node, ("L", ins["value"]))
    # which points have something other than the pin itself
    usage = defaultdict(int)
    for a, b in wires:
        usage[a] += 1; usage[b] += 1
    for _, pt, _ in labels:
        usage[pt] += 1
    for pp in pin_pts:
        usage[pp[4]] += 1
    for ref, num, name, et, pt, ins in pin_pts:
        if pt in ncs:
            if usage[pt] > 1:
                errors.append(f"{ref}.{num} has a no-connect flag but is connected")
            continue
        if usage[pt] < 2 and not any(on_seg(pt, a, b) for a, b in wires):
            errors.append(f"{ref}.{num} ({name}) unconnected")
    for pt in ncs:
        if not any(pp[4] == pt for pp in pin_pts):
            errors.append(f"no-connect at {pt} not on a pin")
    for name, pt, _ in labels:
        if usage[pt] < 2:
            errors.append(f"label {name} at {pt} dangling")
    for a, b in wires:
        for e in (a, b):
            if usage[e] < 2 and e not in junc:
                errors.append(f"wire end {e} dangling")
    # nets
    nets = defaultdict(list)
    for ref, num, name, et, pt, ins in pin_pts:
        if pt in ncs:
            continue
        nets[fnd(("PIN", ref, num))].append((ref, num, name, et))
    named = {}
    for root_, members in nets.items():
        lbls = sorted({k[1] for k in parent if k[0] == "L" and fnd(k) == root_})
        nm = "/".join(lbls) if lbls else "Net-(" + ",".join(f"{r}.{n}" for r, n, *_ in members[:2]) + ")"
        if len(lbls) > 1:
            errors.append(f"net has several names: {lbls}")
        named[nm] = sorted(members, key=lambda m: (m[0], int(m[1]) if m[1].isdigit() else 0))
    # ERC-lite: power_in needs a power_out on the net
    for nm, mem in named.items():
        ets = [m[3] for m in mem]
        if "power_in" in ets and "power_out" not in ets:
            errors.append(f"net {nm}: power_in without a driver")
        if ets.count("power_out") > 1:
            errors.append(f"net {nm}: {ets.count('power_out')} power outputs")
        real = [m for m in mem if not m[0].startswith("#")]
        if len(real) < 2:
            warns.append(f"net {nm}: only {len(real)} real pin(s): {real}")
    refs = [i["ref"] for i in insts]
    dup = {r for r in refs if refs.count(r) > 1}
    if dup:
        errors.append(f"duplicate references {dup}")
    return dict(libs=libs, insts=insts, wires=wires, junc=junc, ncs=ncs, labels=labels, texts=texts, polys=polys,
                nets=named, errors=errors, warns=sorted(set(warns)))


def render(R, base):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Rectangle
    fig = plt.figure(figsize=(420 / 25.4, 297 / 25.4), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 420); ax.set_ylim(297, 0); ax.axis("off")
    ax.add_patch(Rectangle((10, 10), 400, 277, fill=False, lw=0.6, color="#802020"))
    FS = 72 / 25.4  # points per mm
    for pts in R["polys"]:
        xs, ys = zip(*pts); ax.plot(xs, ys, "--", color="#a0a0a0", lw=0.5)
    for s, x, y, size in R["texts"]:
        ax.text(x, y, s, fontsize=size * FS * 0.95, va="top", ha="left", family="DejaVu Sans Mono", color="#004080",
                linespacing=1.25)
    for a, b in R["wires"]:
        ax.plot([a[0], b[0]], [a[1], b[1]], color="#008000", lw=0.7)
    for x, y in R["junc"]:
        ax.add_patch(Circle((x, y), 0.45, color="#008000"))
    for x, y in R["ncs"]:
        d = 0.6; ax.plot([x - d, x + d], [y - d, y + d], color="#0000c0", lw=0.6)
        ax.plot([x - d, x + d], [y + d, y - d], color="#0000c0", lw=0.6)
    for name, (x, y), ang in R["labels"]:
        kw = dict(fontsize=1.27 * FS, color="#000000", family="DejaVu Sans")
        if ang == 0:
            ax.text(x + 0.2, y - 0.2, name, ha="left", va="bottom", **kw)
        elif ang == 180:
            ax.text(x - 0.2, y - 0.2, name, ha="right", va="bottom", **kw)
        elif ang == 90:
            ax.text(x - 0.2, y - 0.2, name, ha="right", va="bottom", rotation=90, **kw)
        else:
            ax.text(x - 0.2, y + 0.2, name, ha="right", va="top", rotation=90, **kw)
    for ins in R["insts"]:
        lib = R["libs"][ins["lib"]]; X, Y = ins["X"], ins["Y"]
        col = "#a00000"
        for g in lib["gfx"]:
            if g[0] == "rectangle":
                s, e = one(g, "start"), one(g, "end")
                x1, y1, x2, y2 = float(s[1]), float(s[2]), float(e[1]), float(e[2])
                ax.add_patch(Rectangle((X + min(x1, x2), Y - max(y1, y2)), abs(x2 - x1), abs(y2 - y1), fill=True,
                                       fc="#fffbe6" if one(g, "fill")[1][1] == "background" else "none", ec=col, lw=0.6))
            elif g[0] == "polyline":
                pts = [(X + float(p[1]), Y - float(p[2])) for p in one(g, "pts")[1:]]
                xs, ys = zip(*pts); ax.plot(xs, ys, color=col, lw=0.6)
            elif g[0] == "circle":
                c = one(g, "center"); r = float(one(g, "radius")[1])
                ax.add_patch(Circle((X + float(c[1]), Y - float(c[2])), r, fill=False, ec=col, lw=0.5))
            elif g[0] == "text":
                at = one(g, "at")
                ax.text(X + float(at[1]), Y - float(at[2]), val(g[1]), ha="center", va="center", fontsize=1.27 * FS,
                        color=col)
        box = any(g[0] == "rectangle" for g in lib["gfx"]) and len(lib["pins"]) > 2
        for p in lib["pins"]:
            ex, ey = X + p["x"], Y - p["y"]
            d = {0: (1, 0), 180: (-1, 0), 90: (0, -1), 270: (0, 1)}[p["a"]]
            bx, by = ex + d[0] * p["len"], ey + d[1] * p["len"]
            if p["len"] > 0:
                ax.plot([ex, bx], [ey, by], color=col, lw=0.6)
            if box:
                if p["a"] in (0, 180):
                    ax.text(bx + d[0] * 0.8, by, p["name"].replace("~{", "/").replace("}", ""), ha="left" if p["a"] == 0 else "right",
                            va="center", fontsize=1.1 * FS, color="#006060")
                    ax.text((ex + bx) / 2, ey - 0.3, p["num"], ha="center", va="bottom", fontsize=0.9 * FS, color=col)
                else:
                    ax.text(bx, by + 0.8, p["name"], ha="center", va="top", fontsize=1.1 * FS, color="#006060")
        for key in ("Reference", "Value"):
            v, node = ins["props"][key]
            eff = one(node, "effects")
            if "hide" in eff:
                continue
            at = one(node, "at"); j = one(eff, "justify")
            ha = "left" if j and "left" in j else ("right" if j and "right" in j else "center")
            ax.text(float(at[1]), float(at[2]), v, ha=ha, va="center", fontsize=1.27 * FS,
                    color="#606000" if key == "Value" else "#800080")
    fig.savefig(base + ".png"); fig.savefig(base + ".pdf")


if __name__ == "__main__":
    R = main(sys.argv[1], None)
    for n in sorted(R["nets"]):
        print(f"{n:22s}", " ".join(f"{r}.{p}" for r, p, *_ in R["nets"][n] if not r.startswith("#")))
    print("\nERRORS:", len(R["errors"])); [print("  ", e) for e in R["errors"]]
    print("WARNINGS:", len(R["warns"])); [print("  ", e) for e in R["warns"]]
    if len(sys.argv) > 2:
        render(R, sys.argv[2])
