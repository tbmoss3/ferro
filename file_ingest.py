"""Ferro file ingestion — Phase B.
- Images (png/jpg/jpeg/webp) -> Kimi vision pass -> structured extraction
- PDF -> rasterize pages (PyMuPDF) -> Kimi vision pass
- STEP/STP -> deterministic kernel import (exact geometry, no vision)
- DXF -> ezdxf entity/bbox hints
- DWG -> rejected with a clear message
Feeds the extraction into the normal NL pipeline (GLM brain) as context.
"""
import base64, json, os, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).parent
UPLOADS = Path(os.environ.get("FERRO_DATA_DIR", ROOT / "data")) / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)

ALLOWED = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".step", ".stp", ".dxf"}
MAX_BYTES = 15 * 1024 * 1024

VISION_MODEL = os.environ.get("FERRO_VISION_MODEL", "moonshotai/kimi-k3.1-pro")

VISION_PROMPT = """You are Ferro's computer-vision intake engineer for a CNC machine shop.
Analyze the attached file(s) (photo of a part, sketch, or engineering drawing) and extract a technical spec.

Output STRICT JSON (and nothing else):
{
  "part_type_guess": "one line: what this part probably is (bracket, plate, flange, ...)",
  "overall_dims_mm": {"x": <num or null>, "y": <num or null>, "z": <num or null>},
  "features": ["each visible feature with its measured/legible dimension, e.g. '6x round holes ~5.5mm dia in 2 rows of 3'"],
  "material_guess": "string or null",
  "readable_dims": ["every dimension you can actually read off the image/drawing"],
  "unreadable": ["what you cannot determine and the customer must supply"],
  "notes": "one line on probable machining approach"
}
Rules:
- Only report numbers you can actually see or reasonably scale. Never invent precision.
- If the image is a photo with no scale reference, say so in unreadable and estimate proportions only.
- Dimensions likely in mm unless visibly inch-marked; flag ambiguity.
"""

STEP_HARNESS = r'''
import json, sys
result = {"ok": False}
try:
    from build123d import import_step, export_stl, export_step
    src, stl_out = sys.argv[1], sys.argv[2]
    shapes = import_step(src)
    s = shapes if not isinstance(shapes, (list, tuple)) else shapes[0]
    bb = s.bounding_box()
    export_stl(s, stl_out)
    result.update({
        "ok": True,
        "volume_mm3": round(s.volume, 2),
        "area_mm2": round(s.area, 2),
        "size_mm": [round(bb.size.X, 2), round(bb.size.Y, 2), round(bb.size.Z, 2)],
        "solids_n": len(s.solids()),
        "breps_valid": all((v.is_valid if isinstance(getattr(v, "is_valid", True), bool) else v.is_valid())
                            for v in s.solids()),
    })
except Exception as e:
    result["error"] = f"{type(e).__name__}: {e}"
print("__FERRO_RESULT__")
print(json.dumps(result))
'''


def save_upload(session, filename, data):
    """Persist upload; returns (ok, stored_path_or_error)."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        return False, f"Unsupported format '{ext}'. Send PNG/JPEG/WebP photos or sketches, a PDF drawing, STEP/STP, or DXF. (DWG: export to STEP or PDF first.)"
    if len(data) > MAX_BYTES:
        return False, "File too large (15MB max)."
    d = UPLOADS / session
    d.mkdir(parents=True, exist_ok=True)
    p = d / Path(filename).name
    p.write_bytes(data)
    return True, p


def _data_urls_image(p):
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}[p.suffix.lstrip(".").lower()]
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def _pdf_pages_as_data_urls(p, max_pages=3, dpi=130):
    import fitz
    doc = fitz.open(p)
    urls = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        pix = page.get_pixmap(dpi=dpi)
        urls.append("data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode())
    n = len(doc)
    doc.close()
    return urls, n


def _dxf_hints(p):
    import ezdxf
    doc = ezdxf.readfile(p)
    msp = doc.modelspace()
    from collections import Counter
    ents = Counter(e.dxftype() for e in msp)
    xs, ys = [], []
    for e in msp:
        if e.dxftype() in ("LWPOLYLINE", "LINE", "CIRCLE", "ARC", "POINT"):
            bb = None
            try:
                bb = e.dxf.extents
            except Exception:
                pass
            if bb is None:
                continue
    # extents via ezdxf bbox tool
    try:
        from ezdxf import bbox
        extents = bbox.extents(msp, fast=True)
        return {"entities": dict(ents), "size": [round(extents.size.x, 2), round(extents.size.y, 2)]}
    except Exception:
        return {"entities": dict(ents)}


def _get_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    # local dev fallback: profile .env
    try:
        for line in open(os.path.expanduser(
                "~/.hermes/profiles/simmons-harris-executive/.env")):
            if line.startswith("OPENROUTER_API_KEY="):
                return line.strip().split("=", 1)[1]
    except Exception:
        pass
    return None


def ask_vision(image_urls, prompt, timeout=240):
    """Multimodal call via OpenRouter (Kimi K3.1 Pro default). Returns (text, ok)."""
    import urllib.request
    key = _get_key()
    if not key:
        return None, False
    content = [{"type": "text", "text": prompt}]
    for u in image_urls:
        content.append({"type": "image_url", "image_url": {"url": u}})
    body = json.dumps({
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=timeout))
        return r["choices"][0]["message"]["content"].strip(), True
    except Exception as e:
        return f"(vision model error: {e})", False


def import_step_metrics(name, step_path, py):
    """Deterministic STEP ingestion: exact metrics + STL for render/drawing."""
    import cad_kernel as K
    d = K.ARTIFACTS / name
    d.mkdir(parents=True, exist_ok=True)
    stl = d / "part.stl"
    with tempfile.TemporaryDirectory() as td:
        h = Path(td) / "step_import.py"
        h.write_text(STEP_HARNESS)
        r = subprocess.run([py, h, str(step_path), str(stl)], capture_output=True, text=True, timeout=180)
    if "__FERRO_RESULT__" in r.stdout:
        m = json.loads(r.stdout.split("__FERRO_RESULT__\n", 1)[1].strip().splitlines()[0])
        return m
    return {"ok": False, "error": r.stderr[-300:] or "STEP import produced no result"}


def analyze_upload(session, path, message):
    """Returns (context_block, extra_kernel_metrics) to feed the NL pipeline."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".step", ".stp"):
        import cad_kernel as K
        m = import_step_metrics(session, p, K.PY)
        if m.get("ok"):
            return (
                f"== ATTACHED FILE: {p.name} (STEP — imported deterministically by the CAD kernel) ==\n"
                f"Measured geometry: bbox {m['size_mm']} mm, volume {m['volume_mm3']} mm³, "
                f"surface {m['area_mm2']} mm², solids {m['solids_n']}, BRep valid {m['breps_valid']}.\n"
                f"Summarize this measured spec for the customer in natural language and ask for anything "
                f"the file cannot tell us (material, tolerances, finish, quantity). The STL is already on disk — "
                f"the preview and drawing will render from it.",
                m)
        return (f"== ATTACHED FILE: {p.name} (STEP import FAILED) ==\n{m.get('error')}\n"
                f"Tell the customer we could not open the STEP file and ask what the part is.", None)
    if ext == ".dxf":
        try:
            h = _dxf_hints(p)
            return (
                f"== ATTACHED FILE: {p.name} (DXF 2D drawing) ==\n"
                f"Entity summary: {json.dumps(h.get('entities'))}; extents ~{h.get('size')} mm.\n"
                f"Use these as hints. Ask the customer for the critical dimensions you cannot read from a 2D drawing, "
                f"then build the spec conversationally.", None)
        except Exception as e:
            return (f"== ATTACHED FILE: {p.name} (DXF could not be parsed: {e}) ==\nAsk the customer to describe the part or send a PDF/PNG export.", None)
    # raster / pdf -> vision
    if ext == ".pdf":
        urls, npages = _pdf_pages_as_data_urls(p)
        desc = f"PDF drawing, {npages} page(s), first {len(urls)} analyzed"
    else:
        urls = [_data_urls_image(p)]
        desc = f"image ({ext[1:]})"
    out, ok = ask_vision(urls, VISION_PROMPT)
    if not ok:
        return (f"== ATTACHED FILE: {p.name} ({desc}) ==\nVision model unavailable: {out}\nAsk the customer to describe the part in words instead.", None)
    # pretty-print the extraction JSON for the GLM context
    try:
        j = json.loads(out)
        pretty = json.dumps(j, indent=1)
    except Exception:
        pretty = out
    return (
        f"== ATTACHED FILE: {p.name} ({desc}) ==\n"
        f"Computer-vision extraction (Kimi, may contain estimates — verify with the customer):\n{pretty}\n"
        f"Customer's accompanying message: \"{message or '(none)'}\"\n"
        f"Turn this extraction into a natural-language spec for the customer to confirm or correct. "
        f"Ask sharp clarifying questions for anything in 'unreadable'. Only then emit a design block if it fits a family.",
        None)