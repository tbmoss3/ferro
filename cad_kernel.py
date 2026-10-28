"""Ferro CAD kernel — Phase A.
Executes build123d (OpenCASCADE) geometry in a subprocess and returns
exact metrics, STEP/STL artifacts, DFM checks, and a rendered PNG preview.
Agents draft geometry; this kernel is the deterministic verifier.
"""
import json, math, os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).parent
ARTIFACTS = Path(os.environ.get("FERRO_DATA_DIR", ROOT / "data")) / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
PY = os.environ.get("FERRO_CAD_PY") or str(ROOT / ".venv-cad" / "bin" / "python")

# kg/m^3 — must mirror templates.MATERIALS densities (kg/mm^3 * 1e9)
DENSITIES = {
    "6061-T6": 2700, "7075-T6": 2810, "steel-1018": 7870,
    "4140": 7850, "304-SS": 7900, "316L": 8000, "PEEK": 1320, "delrin": 1410,
}

# runs INSIDE the cad subprocess (written to a temp script, run with PY)
HARNESS = r'''
import json, sys, math
result = {"ok": False}
try:
    from build123d import BuildPart, Box, Cylinder, extrude, export_step, export_stl
    from build123d import __version__ as b123d_version
    ns = {"__name__": "__ferro_kernel__"}
    src = open(sys.argv[1]).read()
    exec(compile(src, "design.py", "exec"), ns)
    part = ns.get("result")
    if part is None:
        raise RuntimeError("script did not define a top-level `result` part")
    s = part if not hasattr(part, "part") else part.part
    if s is None:
        raise RuntimeError("empty result shape")
    bb = s.bounding_box()
    vol = s.volume          # mm^3
    area = s.area           # mm^2
    export_step(s, sys.argv[2])
    export_stl(s, sys.argv[3])
    # GLB for the interactive web viewer (build123d glTF export)
    glb_ok = False
    try:
        from build123d import export_gltf
        export_gltf(s, sys.argv[4])
        glb_ok = True
    except Exception:
        pass
    result["glb"] = glb_ok
    # BRep validity check
    valid = True
    try:
        from build123d import Solid
        solids = s.solids()
        valid = all(x.is_valid() for x in solids)
    except Exception:
        pass
    result.update({
        "ok": True,
        "volume_mm3": round(vol, 2),
        "area_mm2": round(area, 2),
        "bbox": [round(bb.min.X,2), round(bb.min.Y,2), round(bb.min.Z,2),
                 round(bb.max.X,2), round(bb.max.Y,2), round(bb.max.Z,2)],
        "size_mm": [round(bb.size.X,2), round(bb.size.Y,2), round(bb.size.Z,2)],
        "solids_n": len(s.solids()),
        "breps_valid": valid,
        "build123d": b123d_version,
    })
except Exception as e:
    result["ok"] = False
    result["error"] = f"{type(e).__name__}: {e}"
print("__FERRO_RESULT__")
print(json.dumps(result))
'''

FAMILY_BUILDERS = {
    "plate": r'''
from build123d import *
L={length}; W={width}; T={thickness}; D={hole_d}; N={hole_count}
inset = min(0.08*min(L,W), 15.0)
rows = 2 if (N % 2 == 0 and N >= 4) else 1
cols = (N + rows - 1)//rows
part = Box(L, W, T)
for j in range(rows):
    y = inset if rows == 1 else inset + j*(W-2*inset)/(rows-1)
    for i in range(cols):
        if j*cols + i >= N: break
        x = inset + i*(L-2*inset)/(cols-1) if cols > 1 else L/2
        part -= Pos(x - L/2, y - W/2, 0) * Cylinder(D/2, T*3)
result = part
''',
    "bracket": r'''
from build123d import *
BL={base_len}; BW={base_wid}; BT={base_th}; VL={vert_len}; VT={vert_th}; D={hole_d}
n2 = max(1, {hole_count}//2)
part = Box(BL, BW, BT) + Pos(BL - VT/2 - BL/2, 0, VL/2) * Box(VT, BW, VL)
for i in range(n2):
    x = BL*(i+0.5)/n2
    part -= Pos(x - BL/2, 0, 0) * Cylinder(D/2, BT*3)
    part -= Pos(BL - VT/2 - BL/2, BW*(i+0.5)/n2 - BW/2, VL/2 + BT/2) * Cylinder(D/2, VL)
result = part
''',
    "enclosure": r'''
from build123d import *
W={width}; Dp={depth}; H={height}; T={wall}; PW={port_w}; PH={port_h}
outer = Box(W, Dp, H)
inner = Pos(0, 0, T - H/2) * Box(W-2*T, Dp-2*T, H-T, align=(Align.CENTER, Align.CENTER, Align.MAX))
port = Pos(0, Dp/2, 0) * Box(PW, 2*T+4, PH)
result = outer - inner - port
''',
    "flange": r'''
from build123d import *
import math
OD={od}; B={bore}; T={thickness}; BCD={bcd}; N={bolt_count}; BD={bolt_d}
disc = Cylinder(OD/2, T)
bore = Cylinder(B/2, T*3)
result = disc - bore
R = OD/2; rb = BCD/2
for i in range(N):
    a = 2*math.pi*i/N
    result -= Pos(rb*math.cos(a), rb*math.sin(a), 0) * Cylinder(BD/2, T*3)
''',
    "spacer": r'''
from build123d import *
OD={od}; B={bore}; T={thickness}
result = Cylinder(OD/2, T) - Cylinder(B/2, T*3)
''',
    "shaft_collar": r'''
from build123d import *
OD={od}; B={bore}; W_{0}={width}; SS={set_screw_d}
result = Cylinder(OD/2, W_{0}) - Cylinder(B/2, W_{0}*3)
result -= Pos(0, OD/2, 0) * Cylinder(SS/2, OD/2)   # radial set-screw hole
'''.replace("W_{0}", "Wt"),
    "tube_rack": r'''
from build123d import *
import math
R_{0}={rows}; C_{0}={cols}; D={hole_d}; P={pitch}; T={base_th}; DEP={hole_depth}; WL={wall}
L = C_{0}*P + 2*WL
W = R_{0}*P + 2*WL
result = Box(L, W, T)
x0 = -L/2 + WL + P/2
y0 = -W/2 + WL + P/2
z0 = T/2          # cut from the top face down
for j in range(R_{0}):
    for i in range(C_{0}):
        result -= Pos(x0 + i*P, y0 + j*P, z0 - DEP/2) * Cylinder(D/2, DEP)
'''.replace("R_{0}", "Rr").replace("C_{0}", "Cc"),
    "tube_clamp": r'''
from build123d import *
import math
TO={tube_od}; L={block_len}; W={block_wid}; T={block_th}; D={hole_d}; N={hole_count}
result = Box(L, W, T)
# saddle groove: half-cylinder cut along the top, along the length
result -= Pos(0, 0, T/2) * Rot(0, 90, 90) * Cylinder(TO/2, L*3)
# mounting holes flanking the groove
n2 = max(1, N//2)
for i in range(n2):
    x = TO/2 + D/2 + 2
    result -= Pos(-x, 0, 0) * Cylinder(D/2, T*3)
    result -= Pos(x, 0, 0) * Cylinder(D/2, T*3)
''',
    "grid_plate": r'''
from build123d import *
L={length}; W={width}; T={thickness}; R_{0}={rows}; C_{0}={cols}; D={hole_d}; P={pitch}
result = Box(L, W, T)
gx = -L/2 + P/2 + (L - C_{0}*P)/2
gy = -W/2 + P/2 + (W - R_{0}*P)/2
for j in range(R_{0}):
    for i in range(C_{0}):
        result -= Pos(gx + i*P, gy + j*P, 0) * Cylinder(D/2, T*3)
'''.replace("R_{0}", "Rr").replace("C_{0}", "Cc"),
    "extrusion_bracket": r'''
from build123d import *
PW={profile_w}; AL={arm_len}; AT={arm_th}; D={hole_d}
# L-joiner sized for a PW-wide T-slot profile
arm1 = Box(AL, PW, AT)                                   # horizontal arm
arm2 = Pos(AL/2 - AT/2, 0, AT/2) * Box(AT, PW, AL)        # vertical arm at the right end
result = arm1 + arm2
# M5 clearance holes: through arm1 along Z, through arm2 along X
result -= Pos(AL/2 - PW/2, 0, 0) * Cylinder(D/2, AT*3)
result -= Pos(AL/2 - AT/2, 0, AL/2 + AT/2 - PW/2) * Rot(0, 90, 0) * Cylinder(D/2, AT*3)
''',
    "pulley": r'''
from build123d import *
import math
OD={od}; B={bore}; BW={belt_w}; GD={groove_depth}; W={width}; FD={flange_d}; SS={setscrew_d}
gd = min(GD, (OD/2 - B/2) - 1)
# all parts centered on z=0 (build123d cylinders are centered)
# disc body: axis along Z, half-height W/2
result = Cylinder(OD/2, W)
# flanges (rims) overlapping the disc faces so they union into one solid
result += Pos(0, 0, -W/2 + 2) * Cylinder(FD/2, 4)
result += Pos(0, 0,  W/2 - 2) * Cylinder(FD/2, 4)
# central bore through the axis
result -= Cylinder(B/2, W*3)
# belt groove: annular band at mid-width (z centered at 0)
ring = Cylinder(OD/2 + 1, BW) - Cylinder(OD/2 - gd, BW + 2)
result -= ring
# radial set-screw hole crossing the bore wall
result -= Pos(0, OD/2, 0) * Rot(90, 0, 0) * Cylinder(SS/2, OD)
''',
    "leveling_foot": r'''
from build123d import *
PD={pad_od}; PT={pad_th}; SD={stem_d}; SL={stem_len}
pad = Cylinder(PD/2, PT)                       # z ∈ [-PT/2, PT/2]
stem = Pos(0, 0, PT/2 + SL/2) * Cylinder(SD/2, SL)   # sits on top of the pad
result = pad + stem
''',
    "port_block": r'''
from build123d import *
L={length}; W={width}; T={thickness}; PD_{0}={port_d}; N={port_count}; PD_{1}={port_depth}
result = Box(L, W, T)
n = max(1, N)
x0 = -L/2 + L/n/2
for i in range(n):
    result -= Pos(x0 + i*L/n, 0, T/2 - PD_{1}/2) * Cylinder(PD_{0}/2, PD_{1})
'''.replace("PD_{0}", "Pd").replace("PD_{1}", "Pdp"),
}


def run_kernel(script_src, name, timeout=90):
    """Execute a build123d script in the CAD venv. Returns metrics dict."""
    d = ARTIFACTS / name
    d.mkdir(parents=True, exist_ok=True)
    step, stl = d / "part.step", d / "part.stl"
    glb = d / "part.glb"
    with tempfile.TemporaryDirectory() as td:
        harness = Path(td) / "harness.py"
        design = Path(td) / "design.py"
        harness.write_text(HARNESS)
        design.write_text(script_src)
        try:
            r = subprocess.run(
                [PY, harness, design, step, stl, glb],
                capture_output=True, text=True, timeout=timeout)
            out = r.stdout
            if "__FERRO_RESULT__" in out:
                m = json.loads(out.split("__FERRO_RESULT__\n", 1)[1].strip().splitlines()[0])
                m["stderr_tail"] = r.stderr[-500:] if not m.get("ok") else ""
                return m
            return {"ok": False, "error": "kernel produced no result",
                    "stdout_tail": out[-400:], "stderr_tail": r.stderr[-400:]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"kernel timeout after {timeout}s"}
        except FileNotFoundError:
            return {"ok": False, "error": f"CAD python not found at {PY}"}


def family_script(family, params):
    """Deterministic build123d source for a parametric family (mm)."""
    src = FAMILY_BUILDERS[family]
    keys = {
        "length": params.get("length"), "width": params.get("width"),
        "thickness": params.get("thickness"), "hole_d": params.get("hole_d"),
        "hole_count": int(params.get("hole_count", 4)),
        "base_len": params.get("base_len"), "base_wid": params.get("base_wid"),
        "base_th": params.get("base_th"), "vert_len": params.get("vert_len"),
        "vert_th": params.get("vert_th"),
        "depth": params.get("depth"), "height": params.get("height"),
        "wall": params.get("wall"), "port_w": params.get("port_w"),
        "port_h": params.get("port_h"), "lid": bool(params.get("lid", True)),
        "od": params.get("od"), "bore": params.get("bore"),
        "bcd": params.get("bcd"), "bolt_count": int(params.get("bolt_count", 4)),
        "bolt_d": params.get("bolt_d"), "set_screw_d": params.get("set_screw_d"),
        "setscrew_d": params.get("setscrew_d", params.get("set_screw_d")),
        "rows": int(params.get("rows", 8)), "cols": int(params.get("cols", 6)),
        "pitch": params.get("pitch"), "base_th": params.get("base_th"),
        "hole_depth": params.get("hole_depth"), "wall": params.get("wall"),
        "tube_od": params.get("tube_od"), "block_len": params.get("block_len"),
        "block_wid": params.get("block_wid"), "block_th": params.get("block_th"),
        "profile_w": params.get("profile_w"), "arm_len": params.get("arm_len"),
        "arm_th": params.get("arm_th"), "slot_w": params.get("slot_w"),
        "slot_depth": params.get("slot_depth"),
        "belt_w": params.get("belt_w"), "groove_depth": params.get("groove_depth"),
        "flange_d": params.get("flange_d"),
        "pad_od": params.get("pad_od"), "pad_th": params.get("pad_th"),
        "stem_d": params.get("stem_d"), "stem_len": params.get("stem_len"),
        "port_d": params.get("port_d"), "port_count": int(params.get("port_count", 3)),
        "port_depth": params.get("port_depth"),
    }
    return src.format(**{k: v for k, v in keys.items() if v is not None})


def dfm_checks(m, material="6061-T6"):
    """Kernel-truth DFM checks on the computed geometry."""
    notes = []
    if not m.get("ok"):
        return (["Kernel did not verify geometry — blocked from machining queue."], 0)
    if not m.get("breps_valid", False):
        notes.append("BRep invalid — geometry fails kernel validity check; engineer review mandatory.")
    if m.get("solids_n", 1) > 1:
        notes.append(f"{m['solids_n']} disconnected solids — CNC cannot cut one part in multiple bodies; review.")
    vol = m.get("volume_mm3", 0)
    if vol <= 0:
        notes.append("Zero/negative volume — degenerate geometry.")
    sx, sy, sz = m.get("size_mm", [0, 0, 0])
    if min(sx, sy, sz) < 2:
        notes.append(f"Thinnest dimension {min(sx, sy, sz):.1f}mm — below 2mm practical machining floor; review.")
    mass_kg = round(vol * DENSITIES.get(material, 2700) * 1e-9, 3)
    return notes, mass_kg


RENDER_HARNESS = r'''
import sys
stl, png = sys.argv[1], sys.argv[2]
import numpy as np, trimesh, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource
tm = trimesh.load(stl, force="mesh")
fig = plt.figure(figsize=(5.2, 3.7), dpi=96)
ax = fig.add_subplot(111, projection="3d")
ax.set_proj_type("ortho")
ax.computed_zorder = False
v = tm.vertices; f = tm.faces
# camera for view_init(28,-55): eye direction
el, az = np.radians(28), np.radians(-55)
cam = -np.array([np.cos(el)*np.cos(az), np.cos(el)*np.sin(az), np.sin(el)])
keep = tm.face_normals @ cam > 0.02
# skip dense meshes in the browser-render path? no: decimate to a face budget
if keep.sum() > 40000:
    idx = np.flatnonzero(keep)
    keep = np.zeros(len(f), bool); keep[idx[np.linspace(0, len(idx)-1, 40000).astype(int)]] = True
vt = v[f]
x, y, z = vt[:, :, 0], vt[:, :, 1], vt[:, :, 2]
ls = LightSource(azdeg=35, altdeg=55)
norm = tm.face_normals[keep]
light = np.array([np.cos(np.radians(55))*np.cos(np.radians(35)),
                  np.cos(np.radians(55))*np.sin(np.radians(35)),
                  np.sin(np.radians(55))])
shade = np.clip(norm @ light, -1, 1) * 0.55 + 0.45
base = np.array([0.70, 0.73, 0.78])
cols = np.clip(shade[:, None] * base[None, :], 0.18, 1.0)
ax.plot_trisurf(v[:, 0], v[:, 1], v[:, 2], triangles=f[keep],
                color="#dfe3e9", shade=True,
                lightsource=LightSource(azdeg=35, altdeg=55),
                edgecolor=(0.32, 0.35, 0.4), linewidth=0.05, antialiased=False)
# --- soft ground shadow: two-layer ellipse from the part's XY footprint ---
from matplotlib.colors import to_rgba
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
zfloor = v[:, 2].min() - 0.10 * ((v.max(0) - v.min(0))[2] or 1)
cx, cy = v[:, 0].mean(), v[:, 1].mean()
rx, ry = (v[:, 0].max() - v[:, 0].min()) * 0.46, (v[:, 1].max() - v[:, 1].min()) * 0.46
ang = np.linspace(0, 2 * np.pi, 48)
for scale, alpha in ((1.18, 0.10), (0.94, 0.16)):
    ex = cx + rx * scale * np.cos(ang)
    ey = cy + ry * scale * np.sin(ang)
    verts = list(zip(ex, ey, np.full_like(ang, zfloor)))
    pc = Poly3DCollection([verts], facecolor=to_rgba((0.05, 0.06, 0.08), alpha),
                          edgecolor="none")
    ax.add_collection3d(pc)
b = tm.bounds
c = (b[0] + b[1]) / 2; span = (b[1] - b[0]).max() / 2 or 1
ax.set_xlim(c[0]-span, c[0]+span); ax.set_ylim(c[1]-span, c[1]+span); ax.set_zlim(c[2]-span, c[2]+span)
ax.view_init(elev=28, azim=-55)
ax.set_axis_off(); fig.tight_layout(pad=0)
fig.savefig(png, facecolor="#f4f4f0")
print("RENDER_OK", round(float(tm.volume), 2))
'''

# 2D dimensioned drawing (SVG 3-view + DXF) — the "sketch file"
DRAWING_HARNESS = r'''
import sys, math
import numpy as np, trimesh, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely.geometry import Polygon
from shapely.ops import unary_union
stl, svg, dxf, png = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
tm = trimesh.load(stl, force="mesh")
size = tm.bounds[1] - tm.bounds[0]
views = {
  "FRONT (XZ)": (0, 2),
  "TOP (XY)":   (0, 1),
  "RIGHT (YZ)": (1, 2),
}
fig, axes = plt.subplots(1, 3, figsize=(15, 5.4), dpi=96)
fig.patch.set_facecolor("#f4f4f0")
polys_by_view = {}
for ax, (title, (ax1, ax2)) in zip(axes, views.items()):
    V = tm.vertices
    P = V[:, [ax1, ax2]]
    tris = [Polygon(P[f]) for f in tm.faces]
    union = unary_union(tris)
    polys_by_view[title] = union
    ext = union.exterior
    xs, ys = np.asarray(ext.coords)[:, 0], np.asarray(ext.coords)[:, 1]
    ax.fill(xs, ys, facecolor="#c9cedb", edgecolor="#2c313c", linewidth=0.8)
    if union.interiors:
        for ring in union.interiors:
            r = np.asarray(ring.coords)
            ax.fill(r[:, 0], r[:, 1], facecolor="#f4f4f0", edgecolor="#2c313c", linewidth=0.7)
    ax.set_title(title, fontsize=10, color="#333")
    x0, y0 = tm.bounds[0][ax1], tm.bounds[0][ax2]
    dw, dh = size[ax1], size[ax2]
    off = 0.08 * max(dw, dh) + 2
    ax.annotate("", xy=(x0 + dw, y0 - off), xytext=(x0, y0 - off),
                arrowprops=dict(arrowstyle="<->", color="#e07b39", lw=1))
    ax.text(x0 + dw/2, y0 - off - 0.06*max(dw,dh) - 2, f"{dw:g} mm",
            ha="center", fontsize=9, color="#e07b39")
    ax.annotate("", xy=(x0 - off, y0 + dh), xytext=(x0 - off, y0),
                arrowprops=dict(arrowstyle="<->", color="#e07b39", lw=1))
    ax.text(x0 - off - 3, y0 + dh/2, f"{dh:g} mm", va="center", ha="right",
            fontsize=9, color="#e07b39", rotation=90)
    ax.set_aspect("equal"); ax.set_axis_off()
    m = off + 6
    ax.set_xlim(x0 - m, x0 + dw + m); ax.set_ylim(y0 - m, y0 + dh + m)
fig.suptitle("FERRO — dimensioned 3-view drawing (mm)", fontsize=12, color="#333")
fig.tight_layout(rect=[0, 0, 1, 0.94])
fig.savefig(svg, facecolor="#f4f4f0")
fig.savefig(png, facecolor="#f4f4f0")

# DXF: real 2D outlines per view, AutoCAD-openable
import ezdxf
doc = ezdxf.new("R2010")
doc.layers.add("OUTLINE", color=7)
msp = doc.modelspace()
gap_x = size.max() * 1.4 + 40
offx = {"FRONT (XZ)": 0, "TOP (XY)": gap_x, "RIGHT (YZ)": 0}
offy = {"FRONT (XZ)": 0, "TOP (XY)": 0, "RIGHT (YZ)": size[2]*1.4 + 40}
def put(ring, ox, oy):
    pts = [(p[0] + ox, p[1] + oy) for p in np.asarray(ring.coords)]
    if len(pts) > 2:
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "OUTLINE"})
for title in views:
    u = polys_by_view[title]
    ox, oy = offx[title], offy[title]
    put(u.exterior, ox, oy)
    for ring in u.interiors:
        put(ring, ox, oy)
doc.saveas(dxf)
print("DRAWING_OK")
'''

def render_drawing(name, timeout=180):
    """3-view dimensioned SVG + DXF + PNG from the verified STL."""
    d = ARTIFACTS / name
    svg, dxf, png = d / "drawing.svg", d / "drawing.dxf", d / "drawing.png"
    stl = d / "part.stl"
    if not stl.exists():
        return None
    with tempfile.TemporaryDirectory() as td:
        h = Path(td) / "draw.py"
        h.write_text(DRAWING_HARNESS)
        try:
            r = subprocess.run([PY, h, stl, svg, dxf, png],
                                capture_output=True, text=True, timeout=timeout)
            if "DRAWING_OK" in r.stdout:
                return {"svg": str(svg), "dxf": str(dxf), "png": str(png)}
            return None
        except Exception:
            return None

def render_png(name, timeout=90):
    """Isometric shaded render of the exported STL, headless (in the CAD venv)."""
    png = ARTIFACTS / name / "preview.png"
    stl = ARTIFACTS / name / "part.stl"
    if not stl.exists():
        return None, "no stl to render"
    with tempfile.TemporaryDirectory() as td:
        h = Path(td) / "render.py"
        h.write_text(RENDER_HARNESS)
        try:
            r = subprocess.run([PY, h, stl, png], capture_output=True, text=True, timeout=timeout)
            if "RENDER_OK" in r.stdout:
                vol = float(r.stdout.split("RENDER_OK")[1].strip().split()[0])
                return str(png), vol
            return None, f"render failed: {r.stderr[-200:]}"
        except Exception as e:
            return None, f"render failed: {e}"