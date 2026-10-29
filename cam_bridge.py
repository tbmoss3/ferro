"""Ferro CAM bridge — Phase C: verified STEP -> toolpaths -> machine G-code.

Doctrine: agents orchestrate, kernels calculate. FreeCAD (OpenCASCADE core, same
kernel family as our CAD layer) computes deterministic toolpaths; the LLM never
touches G-code. Output is PROTOTYPE-tagged and gated behind floor verification.

Routing:
  - 2.5D milled families -> profile (contour + through holes) + drilling ops,
    posted to LinuxCNC-flavor G-code. Feeds/speeds from material table.
  - turned families (pulley, shaft_collar, leveling_foot) -> GATED: manual CAM
    on the lathe; Ferro says so instead of faking it.
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
ARTIFACTS = Path(__file__).parent / "data" / "artifacts"
FREECADCMD = Path.home() / "freecad-env" / "bin" / "freecadcmd"

CAM_OK_FAMILIES = {
    "plate", "bracket", "enclosure", "flange", "spacer",
    "tube_rack", "tube_clamp", "grid_plate", "extrusion_bracket", "port_block",
}
GATED_NOTE = {
    "shaft_collar": "turned part — manual CAM on the lathe (Ferro does not generate lathe G-code yet)",
    "pulley": "turned part — manual CAM on the lathe (Ferro does not generate lathe G-code yet)",
    "leveling_foot": "turned part — manual CAM on the lathe (Ferro does not generate lathe G-code yet)",
}

# --- post processor layer: dialects the shop's controllers speak ---
POSTS = {
    "linuxcnc":   "LinuxCNC / LinuxCNC-based controllers (default)",
    "grbl":       "GRBL 1.1 ( hobby routers, many desktop machines)",
    "centroid":   "Centroid CNC",
    "mach3_mach4": "Mach3 / Mach4",
    "fanuc":      "Fanuc (legacy dialect)",
    "generic":    "generic G-code — last resort, edit before use",
}

# conservative prototype feeds (mm/min) — replace with shop calibration data
FEEDS = {
    "6061-T6": (2200, 800, 12000), "7075-T6": (2200, 800, 12000),
    "delrin": (2500, 900, 12000), "PEEK": (1200, 500, 10000),
    "steel-1018": (700, 300, 6000), "4140": (600, 280, 6000),
    "304-SS": (900, 380, 8000), "316L": (800, 350, 8000),
}

CAM_SCRIPT = r'''
import json
import FreeCAD, Import
import Path.Main.Job as PathJob
import Path.Tool.Controller as PathTC
import Path.Op.Profile as PathProfile
import Path.Op.Drilling as PathDrilling
from Path.Post.Processor import PostProcessorFactory

step = r"%(step)s"
out  = r"%(out)s"
feed, plunge, rpm = %(feed).1f, %(plunge).1f, %(rpm).0f
tool_d = %(tool_d).1f
drill_d = %(drill_d).1f
post_name = r"%(post)s"

doc = FreeCAD.newDocument("cam")
Import.insert(step, doc.Name)
base = doc.Objects[0]
job = PathJob.Create("Job", [base])
doc.recompute()

# --- tool library: T1 endmill (contour), T2 drill ---
tc = PathTC.Create("T1: %%.0fmm endmill" %% tool_d)
job.Proxy.addToolController(tc)
tc.HorizFeed = "%%.3f mm/min" %% feed
tc.VertFeed  = "%%.3f mm/min" %% plunge
tc.SpindleSpeed = rpm
tc.ToolNumber = 1
try:
    tc.Tool.Diameter = tool_d
except Exception:
    pass
tc2 = PathTC.Create("T2: %%.0fmm drill" %% drill_d)
job.Proxy.addToolController(tc2)
tc2.HorizFeed = "%%.3f mm/min" %% (feed * 0.6)
tc2.VertFeed  = "%%.3f mm/min" %% (plunge * 0.8)
tc2.SpindleSpeed = rpm
tc2.ToolNumber = 2
try:
    tc2.Tool.Diameter = drill_d
except Exception:
    pass
doc.recompute()

ops = {"tools": {"T1": "endmill d=%%.1f" %% tool_d, "T2": "drill d=%%.1f" %% drill_d}}
op = PathProfile.Create("Profile")
op.Base = [(base, [])]
op.processHoles = True
op.processPerimeter = True
try:
    op.ToolController = tc   # contour MUST run on the endmill, not the drill
except Exception:
    pass
doc.recompute()
ops["profile_cmds"] = len(op.Path.Commands) if op.Path else 0

try:
    dr = PathDrilling.Create("Drilling")
    hole_edges = []
    for i, e in enumerate(base.Shape.Edges):
        try:
            c = e.Curve
            if hasattr(c, "Radius") and abs(
                e.Vertexes[0].Point.z - e.Vertexes[-1].Point.z) < 0.01:
                hole_edges.append("Edge%%d" %% (i + 1))
        except Exception:
            pass
    if hole_edges:
        dr.Base = [(base, tuple(hole_edges[:10]))]
        try:
            dr.ToolController = tc2
        except Exception:
            pass
        doc.recompute()
        ops["drill_holes"] = len(hole_edges)
        ops["drilling_cmds"] = len(dr.Path.Commands) if dr.Path else 0
    else:
        ops["drill_holes"] = 0
except Exception as e:
    ops["drill_err"] = repr(e)[:120]

post = PostProcessorFactory.get_post_processor(job, post_name)
data = post.export()
gcode = data[0][1]
txt = gcode if isinstance(gcode, str) else "\n".join(gcode)
txt = ("(FERRO PROTOTYPE - NOT MACHINE-READY - VERIFY ON SHOP FLOOR BEFORE USE)\n"
       + txt)
open(out, "w").write(txt)
print("CAM_RESULT " + json.dumps(ops))
print("GCODE_LINES", len(txt.splitlines()))
'''


def generate_remote(cam_url, step_b64, family, material, size_mm):
    """Call the CAM sidecar (Dockerfile.cam) instead of local freecadcmd."""
    import urllib.request
    payload = json.dumps({"step_b64": step_b64, "family": family,
                          "material": material, "size_mm": size_mm}).encode()
    req = urllib.request.Request(cam_url.rstrip("/") + "/generate",
                                 data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=280) as r:
        return json.loads(r.read())


def route(family):
    """CAM routing decision for a family."""
    if family in CAM_OK_FAMILIES:
        return {"routed": True, "machine": "3-axis mill", "post": "linuxcnc",
                "reason": f"2.5D milled family ({family})"}
    return {"routed": False, "gated": True,
            "reason": GATED_NOTE.get(
                family, f"family '{family}' not CAM-routed yet — human estimator")}


def generate(session, family="flange", material="316L", tool_d=6.0,
             drill_d=5.2, post="linuxcnc", timeout=240):
    """Generate G-code from the session's verified STEP. Returns dict."""
    step = ARTIFACTS / session / "part.step"
    out = ARTIFACTS / session / "part.nc"
    if not step.exists():
        return {"ok": False, "error": "no verified STEP for session"}
    if family not in CAM_OK_FAMILIES:
        r = route(family)
        return {"ok": False, "gated": True, "reason": r["reason"]}
    if post not in POSTS:
        return {"ok": False, "error": f"unknown post '{post}' — pick one of {list(POSTS)}"}
    feed, plunge, rpm = FEEDS.get(material, FEEDS["316L"])
    script = CAM_SCRIPT % {
        "step": str(step), "out": str(out), "feed": feed, "plunge": plunge,
        "rpm": rpm, "tool_d": tool_d, "drill_d": drill_d, "post": post}
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
        spath = f.name
    try:
        r = subprocess.run([str(FREECADCMD), spath], capture_output=True,
                           text=True, timeout=timeout)
        stdout = r.stdout
        m = re.search(r"CAM_RESULT ({.*})", stdout)
        g = re.search(r"GCODE_LINES (\d+)", stdout)
        if m and g:
            ops = json.loads(m.group(1))
            return {"ok": True, "gcode_url": f"/artifacts/{session}/part.nc",
                    "gcode_lines": int(g.group(1)), "ops": ops,
                    "post": post, "post_note": POSTS[post], "machine": "3-axis mill",
                    "feeds": {"feed": feed, "plunge": plunge, "rpm": rpm},
                    "gated": False,
                    "warning": "PROTOTYPE — NOT MACHINE-READY — verify on shop floor"}
        return {"ok": False, "error": "cam failed", "detail": (r.stderr or stdout)[-400:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        Path(spath).unlink(missing_ok=True)