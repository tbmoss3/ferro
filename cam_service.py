"""Ferro CAM sidecar — FastAPI wrapping the FreeCAD CAM bridge.

Deployed as a separate service (see Dockerfile.cam) so the main app stays
light while CAM carries the 3.6GB FreeCAD env. The main server calls this
via CAM_URL when set; falls back to local freecadcmd when not.

    POST /generate {"step_b64": ..., "family": "flange", "material": "316L"}
      -> {"ok": true, "gcode": "...", "ops": {...}, "verify": {...}}
"""

import base64
import json
import tempfile
import traceback
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import cam_bridge
import gcode_verify

app = FastAPI(title="ferro-cam")


@app.get("/health")
def health():
    return {"ok": True, "freecad": cam_bridge.FREECADCMD.exists(),
            "version": "0.1"}


@app.post("/generate")
def generate(req: dict):
    step_b64 = req.get("step_b64") or ""
    family = req.get("family") or "flange"
    material = req.get("material") or "316L"
    size_mm = req.get("size_mm") or [90, 90, 12]
    post = req.get("post") or "linuxcnc"
    if post not in cam_bridge.POSTS:
        return JSONResponse(
            {"ok": False, "error": f"unknown post '{post}'",
             "posts": list(cam_bridge.POSTS)}, status_code=400)
    if not step_b64:
        return JSONResponse({"ok": False, "error": "no step_b64"}, status_code=400)
    if family not in cam_bridge.CAM_OK_FAMILIES:
        return {"ok": False, "gated": True,
                "reason": cam_bridge.route(family)["reason"]}
    data = base64.b64decode(step_b64)
    with tempfile.TemporaryDirectory() as td:
        step = Path(td) / "part.step"
        step.write_bytes(data)
        # generate against a temp "session" dir inside td
        art = Path(td) / "art"
        art.mkdir()
        old = cam_bridge.ARTIFACTS
        cam_bridge.ARTIFACTS = art
        try:
            out = art / "part.nc"
            feed, plunge, rpm = cam_bridge.FEEDS.get(material, cam_bridge.FEEDS["316L"])
            script = cam_bridge.CAM_SCRIPT % {
                "step": str(step), "out": str(out), "feed": feed,
                "plunge": plunge, "rpm": rpm, "tool_d": 6.0,
                "drill_d": 5.2, "post": post}
            import re as _re, subprocess as _sp
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
                f.write(script)
                spath = f.name
            r = _sp.run([str(cam_bridge.FREECADCMD), spath],
                        capture_output=True, text=True, timeout=240)
            Path(spath).unlink(missing_ok=True)
            m = _re.search(r"CAM_RESULT ({.*})", r.stdout)
            g = _re.search(r"GCODE_LINES (\d+)", r.stdout)
            if not (m and g and out.exists()):
                return JSONResponse(
                    {"ok": False, "error": "cam failed",
                     "detail": (r.stderr or r.stdout)[-400:]}, status_code=500)
            gcode = out.read_text()
            ops = json.loads(m.group(1))
            stock = [s * 1.15 + 3 for s in size_mm[:3]]
            v = gcode_verify.verify(gcode, stock, material, tool_d=6.0)
            return {"ok": True, "gcode": gcode, "gcode_lines": int(g.group(1)),
                    "ops": ops, "post": "linuxcnc", "machine": "3-axis mill",
                    "feeds": {"feed": feed, "plunge": plunge, "rpm": rpm},
                    "verify": {k: v[k] for k in
                               ("ok", "blocked", "warnings", "stats", "gate")}}
        except Exception as e:
            return JSONResponse(
                {"ok": False, "error": str(e),
                 "trace": traceback.format_exc()[-400:]}, status_code=500)
        finally:
            cam_bridge.ARTIFACTS = old