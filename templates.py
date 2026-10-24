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
}

PARAM_LIMITS = {  # (min, max) per key, mm — DFM guardrails
    "base_len": (20, 400), "base_wid": (20, 300), "base_th": (3, 25),
    "vert_len": (20, 300), "vert_th": (3, 25),
    "hole_d": (2, 20), "hole_count": (1, 12), "fillet_r": (0, 10),
    "length": (20, 600), "width": (20, 400), "thickness": (2, 40),
    "corner_r": (0, 20),
    "wall": (1.5, 12), "lid": (0, 1), "vents": (0, 1),
    "port_w": (5, 80), "port_h": (5, 60),
    "w": None, "d": None, "h": None,  # unused keys allowed
    # enclosure dims handled via width/depth/height keys; alias below
}

ALIAS = {"w": "width", "d": "depth", "h": "height", "t": "thickness",
         "th": "thickness", "len": "length", "wid": "width"}

MATERIALS = {"6061-T6": 2.70e-6, "7075-T6": 2.81e-6, "steel-1018": 7.87e-6,
             "304-SS": 7.90e-6, "delrin": 1.41e-6}  # kg/mm^3


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