"""Ferro front-door prototype: parametric part families -> SVG preview + OpenSCAD.
Three families: bracket (L), plate, enclosure.
"""
import math

# ---------- parametric schemas ----------
SCHEMAS = {
    "bracket": dict(
        base_len=100.0, base_wid=60.0, base_th=8.0,
        vert_len=80.0, vert_th=8.0,
        hole_d=6.5, hole_count=4, fillet_r=3.0,
    ),
    "plate": dict(
        length=150.0, width=100.0, thickness=10.0,
        hole_d=6.5, hole_count=4, corner_r=6.0,
    ),
    "enclosure": dict(
        width=120.0, depth=80.0, height=50.0, wall=3.0,
        lid=True, vents=True, port_w=30.0, port_h=15.0,
    ),
    # round flange / bearing mount: bolt circle
    "flange": dict(
        od=90.0, bore=25.0, thickness=10.0,
        bcd=68.0, bolt_count=4, bolt_d=6.6,
    ),
    # spacer / tube: plain ring
    "spacer": dict(
        od=30.0, bore=10.0, thickness=15.0,
    ),
    # shaft collar: ring + set-screw hole
    "shaft_collar": dict(
        od=40.0, bore=12.0, width=15.0, set_screw_d=5.0,
    ),
    # biotech: centrifuge/tube rack — grid of blind holes in a walled base
    "tube_rack": dict(
        rows=8, cols=6, hole_d=16.5, pitch=19.0,
        base_th=45.0, hole_depth=40.0, wall=10.0,
    ),
    # biotech: saddle clamp block for tubing/HPLC lines
    "tube_clamp": dict(
        tube_od=12.7, block_len=40.0, block_wid=25.0,
        block_th=15.0, hole_d=4.5, hole_count=2,
    ),
    # optics/breadboard: grid of holes on a pitch
    "grid_plate": dict(
        length=300.0, width=200.0, thickness=10.0,
        rows=8, cols=12, hole_d=6.6, pitch=25.0,
    ),
    # T-slot extrusion bracket (2020/2040/4040 machine building)
    "extrusion_bracket": dict(
        profile_w=20.0, arm_len=40.0, arm_th=8.0,
        slot_w=6.0, slot_depth=4.0, hole_d=5.2, hole_count=2,
    ),
    # belt pulley (V-belt idler)
    "pulley": dict(
        od=60.0, bore=8.0, belt_w=10.0, groove_depth=6.0,
        width=16.0, flange_d=66.0, setscrew_d=5.0,
    ),
    # machine leveling foot
    "leveling_foot": dict(
        pad_od=50.0, pad_th=12.0, stem_d=12.0, stem_len=25.0,
    ),
    # pneumatic/hydraulic port block / simple manifold
    "port_block": dict(
        length=60.0, width=40.0, thickness=25.0,
        port_d=10.5, port_count=3, port_depth=12.0,
    ),
}

PARAM_LIMITS = {  # (min, max) per key, mm — DFM guardrails
    "base_len": (20, 400), "base_wid": (20, 300), "base_th": (3, 25),
    "vert_len": (20, 300), "vert_th": (3, 25),
    "hole_d": (2, 20), "hole_count": (1, 12), "fillet_r": (0, 10),
    "length": (20, 600), "width": (20, 400), "thickness": (2, 40),
    "corner_r": (0, 20),
    "wall": (1.5, 12), "lid": (0, 1), "vents": (0, 1),
    "port_w": (5, 80), "port_h": (5, 60),
    "od": (8, 400), "bore": (2, 300), "bcd": (10, 380),
    "bolt_count": (2, 16), "bolt_d": (2, 20),
    "set_screw_d": (2, 12),
    # shaft collar axial width — turning can hold thin rings fine
    "width": (2, 400),
    "rows": (1, 24), "cols": (1, 24),
    "base_th": (5, 150), "hole_depth": (3, 140), "wall": (3, 40),
    "tube_od": (2, 100), "block_len": (15, 200), "block_wid": (10, 150),
    "block_th": (5, 80), "pitch": (8, 200),
    "profile_w": (10, 60), "arm_len": (20, 200), "arm_th": (4, 20),
    "slot_w": (4, 15), "slot_depth": (2, 10),
    "belt_w": (4, 40), "groove_depth": (2, 15), "width": (2, 400),
    "flange_d": (10, 400), "setscrew_d": (2, 12),
    "pad_od": (15, 200), "pad_th": (5, 60), "stem_d": (4, 40), "stem_len": (5, 150),
    "port_d": (2, 40), "port_count": (1, 12), "port_depth": (3, 120),
    "w": None, "d": None, "h": None,  # unused keys allowed
    # enclosure dims handled via width/depth/height keys; alias below
}

ALIAS = {"w": "width", "d": "depth", "h": "height", "t": "thickness",
         "th": "thickness", "len": "length", "wid": "width"}

MATERIALS = {"6061-T6": 2.70e-6, "7075-T6": 2.81e-6, "steel-1018": 7.87e-6,
             "304-SS": 7.90e-6, "316L": 8.00e-6, "PEEK": 1.32e-6, "delrin": 1.41e-6}  # kg/mm^3


def normalize(family, params):
    """Clamp/validate params for a family; returns (clean, d fm_notes list)."""
    schema = SCHEMAS[family]
    clean, notes = {}, []
    src = dict(schema)
    for k, v in (params or {}).items():
        k = ALIAS.get(k, k)
        if k in src or k in PARAM_LIMITS or k in ("material",):
            src[k] = v
    for k, dv in src.items():
        if k == "material":
            clean[k] = MATERIALS.get(dv, dv) if isinstance(dv, str) and dv in MATERIALS else dv
            if isinstance(clean[k], str) and clean[k] not in MATERIALS:
                notes.append(f"Material '{clean[k]}' not in stock list {sorted(MATERIALS)} — using as custom spec.")
                clean[k] = dv
            continue
        if k in ("lid", "vents"):
            clean[k] = bool(dv)
            continue
        try:
            v = float(dv)
        except (TypeError, ValueError):
            notes.append(f"{k}: invalid value {dv!r}, kept default {dv if k in schema else 'n/a'}")
            continue
        lim = PARAM_LIMITS.get(k)
        if lim and k in SCHEMAS[family]:
            lo, hi = lim
            if v < lo:
                v = lo; notes.append(f"{k} raised to min {lo} mm (DFM: below manufacturable).")
            elif v > hi:
                v = hi; notes.append(f"{k} capped at max {hi} mm.")
            if family in ("plate",) and k == "thickness" and v < 3:
                notes.append("Thin plate under 3mm may warp in milling — flagged for review.")
        clean[k] = round(v, 2)
    if "hole_count" in clean:
        clean["hole_count"] = int(clean["hole_count"])
    for ik in ("rows", "cols"):
        if ik in clean:
            clean[ik] = int(clean[ik])
    if family == "enclosure":
        if clean.get("wall", 3.0) < clean.get("thickness", 0) / 10:
            pass
        if clean["width"] - 2 * clean["wall"] < 20:
            notes.append("Interior width under 20mm after walls — too tight, widen or thin walls.")
        if clean["port_w"] >= clean["width"] - 2 * clean["wall"]:
            clean["port_w"] = max(5.0, clean["width"] - 2 * clean["wall"] - 10)
            notes.append("Port width clipped to fit wall.")
    if family == "bracket":
        cap = round(clean["base_wid"] * 0.4, 2)
        if clean["hole_d"] > cap:
            clean["hole_d"] = cap
            notes.append("Hole diameter capped at 40% of base width — keep ≥1.5×D edge distance; widen the bracket if you need bigger bores.")
        elif clean["hole_d"] > 2.5 * clean["base_th"]:
            notes.append("Large bore relative to plate thickness — fine for clearance holes; consider a boss if threaded.")
    if family == "flange":
        rim = (clean["od"] - clean["bcd"]) / 2
        if clean["bolt_d"] > 0.7 * rim:
            clean["bolt_d"] = round(0.7 * rim, 2)
            notes.append("Bolt holes capped — keep ≥1.5×D material between bolt circle and OD.")
        if clean["bcd"] - clean["bolt_d"] / 2 <= clean["bore"] + clean["bolt_d"] / 2:
            notes.append("Bolt circle too close to bore — spokes between bore and bolts are thin; review.")
        if clean["bcd"] >= clean["od"] - 2 * clean["bolt_d"]:
            clean["bcd"] = round(clean["od"] - 3 * clean["bolt_d"], 2)
            notes.append(f"Bolt circle pulled in to {clean['bcd']}mm to keep rim material.")
    if family in ("spacer",):
        if clean["od"] - clean["bore"] < 4:
            notes.append("Wall under 2mm after bore — thin for turning; review.")
    if family == "shaft_collar":
        if clean["bore"] + 2 * clean["set_screw_d"] >= clean["od"] - 2:
            notes.append("Set screw too large for wall — clamp load may split the collar; reduce set_screw_d.")
    if family == "tube_rack":
        if clean["hole_d"] + 3 > clean["pitch"]:
            clean["pitch"] = round(clean["hole_d"] + 3, 2)
            notes.append(f"Hole pitch widened to {clean['pitch']}mm — keep ≥1.5mm wall between adjacent wells.")
        if clean["hole_depth"] > clean["base_th"] - 2:
            clean["hole_depth"] = round(clean["base_th"] - 2, 2)
            notes.append("Well depth clipped to leave ≥2mm floor.")
        if clean["wall"] < 4:
            clean["wall"] = 4.0
            notes.append("Side wall raised to 4mm minimum for handling stiffness.")
    if family == "tube_clamp":
        if clean["tube_od"] / 2 > clean["block_wid"] * 0.8:
            clean["tube_od"] = round(clean["block_wid"] * 1.6, 2)
            notes.append("Tube groove too wide for block — groove radius capped at 80% of block width.")
        edge = (clean["block_len"] - clean["tube_od"]) / 4
        if clean["hole_d"] > 2 * edge / 3:
            notes.append("Bolt holes close to groove/edges — keep ≥2×D edge distance; lengthen the block.")
    if family == "grid_plate":
        if clean["hole_d"] + 3 > clean["pitch"]:
            clean["pitch"] = round(clean["hole_d"] + 3, 2)
            notes.append(f"Grid pitch widened to {clean['pitch']}mm — keep ≥1.5mm material between holes.")
        clean["cols"] = max(2, min(clean["cols"], int(clean["length"] // clean["pitch"])))
        clean["rows"] = max(2, min(clean["rows"], int(clean["width"] // clean["pitch"])))
        if clean["cols"] * clean["pitch"] > clean["length"] + 10:
            clean["length"] = round(clean["cols"] * clean["pitch"], 2)
            notes.append("Plate length grown to fit the grid.")
        if clean["rows"] * clean["pitch"] > clean["width"] + 10:
            clean["width"] = round(clean["rows"] * clean["pitch"], 2)
            notes.append("Plate width grown to fit the grid.")
    if family == "extrusion_bracket":
        pw = clean["profile_w"]
        if clean["arm_th"] > pw * 0.6:
            clean["arm_th"] = round(pw * 0.6, 2)
            notes.append(f"Arm thickness capped at 60% of {pw}mm profile — keep the slot clear.")
        if clean["hole_d"] > pw * 0.35:
            clean["hole_d"] = round(pw * 0.35, 2)
            notes.append(f"Bolt holes capped for {pw}mm profile — M5 typical on 20mm series.")
        if clean["slot_depth"] > pw / 2:
            clean["slot_depth"] = round(pw / 2, 2)
            notes.append("Slot depth capped at half the profile — otherwise the arm is split.")
    if family == "pulley":
        if clean["flange_d"] < clean["od"]:
            clean["flange_d"] = clean["od"]
        if clean["flange_d"] > clean["od"] + 2 * clean["groove_depth"] + 4:
            clean["flange_d"] = round(clean["od"] + 2 * clean["groove_depth"] + 4, 2)
            notes.append("Flange OD clipped — flanges just need groove depth + belt clearance.")
        if clean["belt_w"] > clean["width"] - 4:
            clean["belt_w"] = round(clean["width"] - 4, 2)
            notes.append("Belt width must clear the pulley face by ≥2mm per side.")
        if clean["bore"] + 2 * clean["setscrew_d"] > clean["od"] - 4:
            clean["setscrew_d"] = round(max(2.0, (clean["od"] - 4 - clean["bore"]) / 2), 2)
            notes.append("Set screw reduced — hub wall too thin.")
    if family == "leveling_foot":
        if clean["stem_d"] > clean["pad_od"] * 0.8:
            clean["stem_d"] = round(clean["pad_od"] * 0.8, 2)
            notes.append("Stem too fat for the pad — keep pad overhang for weld/fillet area.")
    if family == "port_block":
        L, W, T = clean["length"], clean["width"], clean["thickness"]
        if clean["port_depth"] > T - 3:
            clean["port_depth"] = round(T - 3, 2)
            notes.append("Port depth clipped to leave ≥3mm wall between port floor and opposite face.")
        if clean["port_count"] * (clean["port_d"] + 4) > L:
            need = round(clean["port_count"] * (clean["port_d"] + 4), 2)
            clean["length"] = need
            notes.append(f"Block length grown to {need}mm — ports need ≥{clean['port_d']+4}mm pitch along the face.")
        if clean["port_d"] > W - 4:
            clean["port_d"] = round(W - 4, 2)
            notes.append("Port diameter capped — keep ≥2mm material each side of the port face.")
    if "port_count" in clean:
        clean["port_count"] = int(clean["port_count"])
    clean["family"] = family
    if "material" not in clean:
        clean["material"] = "6061-T6"
    return clean, notes


# ---------- geometry: boxes per family ----------
def _boxes(p):
    """Return list of (x,y,z,w,d,h) boxes + hole list [(x,y,z,axis,d)] in mm."""
    fam = p["family"]
    if fam == "bracket":
        bt = p["base_th"]
        boxes = [
            (0, 0, 0, p["base_len"], p["base_wid"], bt),                       # base
            (0, 0, bt, p["vert_th"], p["base_wid"], p["vert_len"]),           # vertical
        ]
        holes = []
        n = max(2, int(round(p["hole_count"]))) // 2
        for i in range(n):
            x = (p["base_len"] * (i + 0.5)) / n
            holes.append((x, p["base_wid"] / 2, 0, "z", p["hole_d"]))
            y = (p["base_wid"] * (i + 0.5)) / n
            holes.append((p["vert_th"] / 2, y, bt, "x", p["hole_d"]))
        return boxes, holes
    if fam == "plate":
        L, W = p["length"], p["width"]
        boxes = [(0, 0, 0, L, W, p["thickness"])]
        n = max(1, int(round(p["hole_count"])))
        inset = min(0.08 * min(L, W), 15.0)
        rows = 2 if (n % 2 == 0 and n >= 4) else 1
        cols = (n + rows - 1) // rows
        holes = []
        for j in range(rows):
            y = inset if rows == 1 else inset + j * (W - 2 * inset) / (rows - 1)
            for i in range(cols):
                idx = j * cols + i
                if idx >= n:
                    break
                x = inset + i * (L - 2 * inset) / (cols - 1) if cols > 1 else L / 2
                holes.append((x, y, 0, "z", p["hole_d"]))
        return boxes, holes
    if fam == "enclosure":
        w, d, h, t = p["width"], p["depth"], p["height"], p["wall"]
        boxes = [(0, 0, 0, w, d, h)]
        holes = [(w / 2, 0, h / 2, "y", p["port_w"], "port")]  # front port cutout (w,h)
        return boxes, holes
    if fam == "flange":
        import math as _m
        R = p["od"] / 2
        boxes = [(R, R, 0, p["od"], p["od"], p["thickness"])]  # bbox proxy for fit
        holes = []
        rb = p["bcd"] / 2
        n = max(2, int(round(p["bolt_count"])))
        for i in range(n):
            a = 2 * _m.pi * i / n
            holes.append((R + rb * _m.cos(a), R + rb * _m.sin(a), 0, "z", p["bolt_d"]))
        return boxes, holes
    if fam == "spacer":
        boxes = [(p["od"] / 2, p["od"] / 2, 0, p["od"], p["od"], p["thickness"])]
        return boxes, []
    if fam == "shaft_collar":
        boxes = [(p["od"] / 2, p["od"] / 2, 0, p["od"], p["od"], p["width"])]
        return boxes, []
    if fam == "tube_rack":
        L = p["cols"] * p["pitch"] + 2 * p["wall"]
        W = p["rows"] * p["pitch"] + 2 * p["wall"]
        boxes = [(0, 0, 0, L, W, p["base_th"])]
        holes = []
        x0 = p["wall"] + p["pitch"] / 2
        y0 = p["wall"] + p["pitch"] / 2
        for j in range(p["rows"]):
            for i in range(p["cols"]):
                holes.append((x0 + i * p["pitch"], y0 + j * p["pitch"],
                              0, "z", p["hole_d"]))
        return boxes, holes
    if fam == "tube_clamp":
        L, W, T = p["block_len"], p["block_wid"], p["block_th"]
        boxes = [(0, 0, 0, L, W, T)]
        holes = [(L / 2, W / 2, 0, "z", p["tube_od"] * 0.7, "groove")]
        n2 = max(1, int(p["hole_count"]) // 2)
        for i in range(n2):
            holes.append((p["tube_od"] / 2 + p["hole_d"] / 2 + 2,
                          W / 2, 0, "z", p["hole_d"]))
            holes.append((L - p["tube_od"] / 2 - p["hole_d"] / 2 - 2,
                          W / 2, 0, "z", p["hole_d"]))
        return boxes, holes
    if fam == "grid_plate":
        L, W = p["length"], p["width"]
        boxes = [(0, 0, 0, L, W, p["thickness"])]
        holes = []
        gx = p["pitch"] / 2 + (L - p["cols"] * p["pitch"]) / 2
        gy = p["pitch"] / 2 + (W - p["rows"] * p["pitch"]) / 2
        for j in range(p["rows"]):
            for i in range(p["cols"]):
                holes.append((gx + i * p["pitch"], gy + j * p["pitch"],
                              0, "z", p["hole_d"]))
        return boxes, holes
    if fam == "extrusion_bracket":
        A1, A2, TH = p["arm_len"], p["arm_len"], p["arm_th"]
        boxes = [(0, 0, 0, A1, TH, TH),
                 (A1 - TH, 0, TH, TH, A2, TH)]
        holes = []
        n2 = max(1, int(p["hole_count"]) // 2)
        for i in range(n2):
            holes.append((A1 * (i + 0.5) / n2, TH / 2, 0, "z", p["hole_d"]))
            holes.append((A1 - TH / 2, TH + A2 * (i + 0.5) / n2, TH, "x", p["hole_d"]))
        return boxes, holes
    if fam == "pulley":
        OD, W = p["od"], p["width"]
        boxes = [(OD / 2, 0, 0, OD, W, OD)]  # cylinder proxy box
        holes = [(OD / 2, W / 2, 0, "y", p["bore"], "bore"),
                 (OD / 2, 0, OD / 2, "y", p["belt_w"], "groove"),
                 (0, W / 2, OD / 2, "x", p["setscrew_d"])]
        return boxes, holes
    if fam == "leveling_foot":
        PD, PT, SD, SL = p["pad_od"], p["pad_th"], p["stem_d"], p["stem_len"]
        boxes = [(PD / 2, 0, 0, PD, PT, PD),
                 ((0, 0, 0, SD, PT + SL, SD))]
        holes = [(PD / 2 + SL / 2, PT / 2, PD / 2, "x", 3.0, "swivel")]
        return boxes, holes
    if fam == "port_block":
        L, W, T = p["length"], p["width"], p["thickness"]
        boxes = [(0, 0, 0, L, W, T)]
        holes = []
        n = max(1, int(p["port_count"]))
        x0 = L / n / 2
        for i in range(n):
            holes.append((x0 + i * L / n, W / 2, T, "z", p["port_d"], "blind-port"))
        return boxes, holes
    return [], []


# ---------- SVG isometric render ----------
def render_svg(p, scale=1.6):
    boxes, holes = _boxes(p)
    # iso projection
    ux, uy, uz = (math.cos(math.radians(30)), -math.sin(math.radians(30)), 0.8)
    def proj(x, y, z):
        return ((x - y) * 0.866) * scale, ((x + y) * 0.5 - z) * scale
    W = 480; H = 340
    shades = ["#c8ccd4", "#9aa0aa", "#b6bcc6"]
    raw = []
    for bi, (x, y, z, w, d, h) in enumerate(boxes):
        c = [(x, y, z), (x + w, y, z), (x + w, y + d, z), (x, y + d, z),
             (x, y, z + h), (x + w, y, z + h), (x + w, y + d, z + h), (x, y + d, z + h)]
        raw.append([proj(*pt) for pt in c])
    hpts = [proj(hx, hy, hz) for hl in holes for hx, hy, hz, *_ in [hl]]
    allpts = [p for b in raw for p in b] + hpts
    minx = min(p[0] for p in allpts); maxx = max(p[0] for p in allpts)
    miny = min(p[1] for p in allpts); maxy = max(p[1] for p in allpts)
    spanx = max(maxx - minx, 1); spany = max(maxy - miny, 1)
    fx = (W - 60) / spanx; fy = (H - 70) / spany
    f = min(fx, fy)
    ox = (W - spanx * f) / 2 - minx * f
    oy = 40 + (H - 50 - spany * f) / 2 - miny * f
    def P(pt):
        return (pt[0] * f + ox, pt[1] * f + oy)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="monospace">']
    parts.append(f'<rect width="{W}" height="{H}" fill="#f4f4f0"/>')
    dimx = max(b[3] for b in boxes) if boxes else 0
    dimy = max(b[4] for b in boxes) if boxes else 0
    dimz = max(h for _, _, _, _, _, h in boxes) if boxes else 0
    parts.append(f'<text x="12" y="20" font-size="11" fill="#555">FERRO · {p["family"]} · iso · {dimx:.0f} × {dimy:.0f} × {dimz:.0f} mm</text>')
    holes_n = len([h for h in holes if not (len(h) > 5)])
    parts.append(f'<text x="{W-12}" y="20" font-size="11" fill="#555" text-anchor="end">{holes_n} holes</text>')
    def face(pts, fill, stroke="#333"):
        pp = " ".join(f"{a:.1f},{b:.1f}" for a, b in pts)
        return f'<polygon points="{pp}" fill="{fill}" stroke="{stroke}" stroke-width="0.8"/>'
    for Pi, pts in enumerate(raw):
        top = [pts[4], pts[5], pts[6], pts[7]]
        front = [pts[0], pts[1], pts[5], pts[4]]
        side = [pts[1], pts[2], pts[6], pts[5]]
        parts.append(face([P(q) for q in front], shades[0]))
        parts.append(face([P(q) for q in side], shades[1]))
        parts.append(face([P(q) for q in top], shades[2]))
    # holes as markers
    for hl in holes:
        hx, hy, hz, ax, *rest = hl
        X, Y = P(proj(hx, hy, hz + (8 if ax == "z" else 0)))
        dia = rest[0]
        parts.append(f'<circle cx="{X:.1f}" cy="{Y:.1f}" r="{dia * f / 2:.1f}" fill="#fff" stroke="#333" stroke-width="0.8"/>')
        parts.append(f'<line x1="{X:.1f}" y1="{Y:.1f}" x2="{X + dia * f / 2:.1f}" y2="{Y:.1f}" stroke="#333" stroke-width="0.5" stroke-dasharray="3,2"/>')
    parts.append(f'<text x="12" y="{H-10}" font-size="10" fill="#444">dims in mm — parametric, OpenSCAD source attached</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------- OpenSCAD source ----------
def render_scad(p):
    fam = p["family"]
    if fam == "bracket":
        return f"""// Ferro parametric L-bracket — generated {p}
$fn = 64;
module bracket() {{
  // base
  translate([0,0,{p['base_th']/2}]) cube([{p['base_len']},{p['base_wid']},{p['base_th']}], center=true);
  // vertical
  translate([{p['base_len']/2 - p['vert_th']/2},0,{p['base_th'] + p['vert_len']/2}])
    cube([{p['vert_th']},{p['base_wid']},{p['vert_len']}], center=true);
  // holes
}}
module holes() {{
  hole_y = {p['base_wid']}/2;
  for (i = [0:{max(1, int(round(p['hole_count'])))-1}])
    translate([i*{p['base_len']}/{max(1, int(round(p['hole_count'])))} + {p['base_len']}/{max(1,int(round(p['hole_count'])))*2}, hole_y, 0])
      cylinder(d={p['hole_d']}, h={p['base_th']*3}, center=true, $fn=64);
}}
difference() {{ bracket(); holes(); }}
"""
    if fam == "plate":
        return f"""// Ferro parametric plate — dims mm
$fn = 64;
difference() {{
  cube([{p['length']},{p['width']},{p['thickness']}]);
  for (i = [0:{max(1,int(round(p['hole_count'])))-1}])
    translate([i*{p['length']}/{max(1,int(round(p['hole_count'])))} + {p['length']}/{max(1,int(round(p['hole_count'])))*2}, {p['width']}/2, -1])
      cylinder(d={p['hole_d']}, h={p['thickness']+2}, $fn=64);
}}
"""
    if fam == "enclosure":
        return f"""// Ferro parametric enclosure — dims mm
$fn = 64;
W={p['width']}; D={p['depth']}; H={p['height']}; T={p['wall']};
difference() {{
  cube([W, D, H]);
  translate([T, T, T]) cube([W-2*T, D-2*T, H]);
  // front port
  translate([W/2 - {p['port_w']}/2, -1, H/2 - {p['port_h']}/2])
    cube([{p['port_w']}, 2*T+2, {p['port_h']}]);
}}
{"// lid" if p.get('lid') else ""}
module lid() {{ translate([0, D, 0]) cube([W, T, H]); }}
{"lid();" if p.get('lid') else "// no lid"}
"""
    return "// unknown family"


def spec_sheet(p, notes):
    lines = [f"FERRO DESIGN PACKET — {p['family'].upper()}",
             "=" * 46, "Parameters (mm):"]
    for k in sorted(p):
        if k in ("family",):
            continue
        lines.append(f"  {k:12s} = {p[k]}")
    lines.append(f"Material: {p.get('material','-')}")
    if notes:
        lines.append("DFM notes:")
        lines += [f"  - {n}" for n in notes]
    return "\n".join(lines)


def mass_kg(p):
    dens = MATERIALS.get(p.get("material"), 2.70e-6)
    boxes, _ = _boxes(p)
    vol = sum(w * d * h for _, _, _, w, d, h in boxes)
    return round(vol * dens * 0.55, 3)  # 45% rough stock removal allowance