"""Ferro front door — FastAPI prototype.
Agent brain = live Hermes one-shot calls (hermes chat -q) with conversation + design state.
"""
import json, re, os, subprocess, tempfile
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
import templates as T

ROOT = Path(__file__).parent
STATE_DIR = ROOT / "sessions"
STATE_DIR.mkdir(exist_ok=True)

AGENT_SYS = """You are Ferro's design intake agent for a bespoke CNC machine shop.
Your job: turn a natural-language customer request into a concrete design, ask at most
2-3 sharp clarifying questions when genuinely needed (not on the first response if the
request is already concrete), and iterate fast on edits.

You operate in TWO modes:

MODE 1 — PARAMETRIC (preferred when it fits). Parametric engine families (mm):
- bracket: base_len, base_wid, base_th, vert_len, vert_th, hole_d, hole_count, fillet_r
- plate: length, width, thickness, hole_d, hole_count, corner_r
- enclosure: width, depth, height, wall, lid, vents, port_w, port_h
- flange: od, bore, thickness, bcd (bolt circle), bolt_count, bolt_d
- spacer: od, bore, thickness
- shaft_collar: od, bore, width, set_screw_d
- tube_rack: rows, cols, hole_d, pitch, base_th, hole_depth, wall (lab/pharma racks)
- tube_clamp: tube_od, block_len, block_wid, block_th, hole_d, hole_count (saddle clamps)
- grid_plate: length, width, thickness, rows, cols, hole_d, pitch (breadboards/optics)
- extrusion_bracket: profile_w, arm_len, arm_th, hole_d (2020/2040/4040 T-slot joiners)
- pulley: od, bore, belt_w, groove_depth, width, flange_d, setscrew_d (belt idlers)
- leveling_foot: pad_od, pad_th, stem_d, stem_len (machine feet)
- port_block: length, width, thickness, port_d, port_count, port_depth (pneumatic/manifold blocks)
Stock materials: 6061-T6, 7075-T6, steel-1018, 304-SS, 316L, PEEK, delrin.
Vocabulary note: customers may say "bucky stand mount", "IV-pole base plate", "engine mount",
"NEMA motor mount", "T-slot joiner", "machine foot" — map these to the closest family above.
Multi-part assemblies (hospital beds, engines, robots, tables) are NOT one machined part —
route them to generative mode only for single custom components, else say what we can make.
If the request fits (or nearly fits) a family, END your message with:
```design
{"family": "<bracket|plate|enclosure|flange|spacer|shaft_collar|tube_rack|tube_clamp|grid_plate|extrusion_bracket|pulley|leveling_foot|port_block>", "params": {...}, "material": "6061-T6"}
```
Include absolute values for the keys you're setting. On edits, re-emit the FULL set.
MANDATORY SELF-CHECK before replying: re-read the customer's message and verify every
number they stated (dimensions, hole counts, hole sizes, thicknesses) is reflected
in your design/scad block. If they said six holes, hole_count must be 6. State the
full parameter list in your prose so the customer can catch any drift.

MODE 2 — GENERATIVE (any part that does not fit a family: brackets with gussets,
V-blocks, fixtures, spacers, hinges, custom frames, sculptural parts, etc.).
Write complete build123d Python (the OpenCASCADE CAD kernel, mm units) that builds
the part. Your code runs in a real CAD kernel and is verified automatically — if it
fails, you get the error back and must fix it. END your message with:
```cad
# GENERATIVE DESIGN — REQUIRES CAM REVIEW
# bbox: <approx W x D x H mm>
# material: <stock material>
# notes: <one line on machining approach>
from build123d import *
<complete build123d source>
result = <the finished part>
```
Rules for generative mode:
- Only use build123d algebra/builder API: Box, Cylinder, Pos, Rot, GridLocations,
  extrude, fillet, chamfer, subtract with `-`, etc. mm units. No imports besides
  build123d. Do NOT call export_* — the kernel does that.
- End with `result = <part>` where <part> is a single solid (BuildPart context:
  `result = p.part`).
- Be conservative: simple, machinable geometry. No threads, no text engraving.
- State assumed stock size in the header comment.
- Always say in prose that this draft needs engineer review before machining.

Materials in stock: 6061-T6, 7075-T6, steel-1018, 304-SS, 316L, PEEK, delrin.

RULES (both modes):
1. Reply conversationally — brief, concrete, engineer-to-engineer. No fluff.
2. Pick the closest mode; say which you chose and why in one clause.
3. On edits, regenerate with the FULL updated geometry.
4. Never invent machining parameters (feeds/speeds) — that is the CAM cell's job.
"""

def load_state(session):
    f = STATE_DIR / f"{session}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {"history": [], "design": None}

def save_state(session, st):
    (STATE_DIR / f"{session}.json").write_text(json.dumps(st, indent=1))

def build_prompt(st, message):
    hist = st["history"][-14:]
    lines = [AGENT_SYS, "", "== CONVERSATION SO FAR =="]
    for h in hist:
        lines.append(f"{'CUSTOMER' if h['role']=='user' else 'FERRO AGENT'}: {h['text'][:700]}")
    if st.get("design"):
        lines.append(f"== CURRENT DESIGN STATE == {json.dumps(st['design'])}")
    lines.append(f"== NEW CUSTOMER MESSAGE ==\n{message}\n== YOUR REPLY ==")
    return "\n".join(lines)

def ask_openrouter(prompt, timeout=150):
    """Direct brain: OpenAI-compatible call via OpenRouter. Used when the Hermes
    CLI is unavailable (e.g. container deploy). Same prompt => same behavior."""
    import urllib.request
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None, False
    model = os.environ.get("FERRO_BRAIN_MODEL", "z-ai/glm-5.3")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=timeout))
        return r["choices"][0]["message"]["content"].strip(), True
    except Exception:
        return None, False

def ask_hermes(prompt, timeout=150):
    """Brain: Hermes CLI one-shot if available (local dev), else OpenRouter HTTP."""
    out, ok = ask_openrouter(prompt, timeout)
    if ok:
        return out, True
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(prompt); ppath = f.name
    try:
        r = subprocess.run(
            ["hermes", "chat", "--query-file", ppath, "--oneshot", "-Q"],
            capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "").strip()
        if out:
            return out, True
        return None, False
    except Exception:
        return None, False
    finally:
        try: os.unlink(ppath)
        except OSError: pass

DESIGN_RE = re.compile(r"```design\s*(\{.*?\})\s*```", re.S)
SCAD_RE = re.compile(r"```scad\s*\n(.*?)```", re.S)
CAD_RE = re.compile(r"```cad\s*\n(.*?)```", re.S)


def parse_cad_block(out):
    m = CAD_RE.search(out)
    if not m:
        return None, None
    src = m.group(1).strip()
    header = {}
    for line in src.splitlines()[:6]:
        mm = re.match(r"#\s*(bbox|material|notes)\s*:\s*(.+)", line.strip())
        if mm:
            header[mm.group(1)] = mm.group(2).strip()
    return src, header

def validate_scad(src):
    """Lightweight static checks (real kernel verification needs openscad)."""
    notes = []
    if src.count("{") != src.count("}"):
        notes.append("Unbalanced braces — OpenSCAD will not compile; flagged for review.")
    if src.count("(") != src.count(")"):
        notes.append("Unbalanced parentheses — flagged for review.")
    if not re.search(r"(cube|cylinder|sphere|polyhedron|linear_extrude|rotate_extrude)", src):
        notes.append("No solid primitive found — flagged for review.")
    if "$fn" not in src:
        notes.append("No $fn smoothing set — fine for review, defaults applied at render.")
    return notes

def parse_scad_block(out):
    m = SCAD_RE.search(out)
    if not m:
        return None, None, None
    src = m.group(1).strip()
    header = {}
    for line in src.splitlines()[:6]:
        mm = re.match(r"//\s*(bbox|material|notes)\s*:\s*(.+)", line.strip())
        if mm:
            header[mm.group(1)] = mm.group(2).strip()
    return src, header, validate_scad(src)

def fallback_reply(message):
    m = message.lower()
    if "bracket" in m:
        fam, params = "bracket", {}
    elif "plate" in m:
        fam, params = "plate", {"length": 200, "width": 120, "thickness": 12, "hole_count": 6}
    elif "enclosure" in m or "box" in m or "case" in m:
        fam, params = "enclosure", {}
    else:
        return ("Tell me a bit more: what does the part mount to, rough size, material, "
                "and how many? Or pick a family — bracket, plate, enclosure."), None, []
    design = {"family": fam, "params": params, "material": "6061-T6"}
    return (f"Let's start with a parametric {fam} in 6061-T6 — preview and packet below. "
            "(Offline-mode defaults; agent unavailable.)"), design, []

def process(message, st):
    prompt = build_prompt(st, message)
    st["history"].append({"role": "user", "text": message})
    out, ok = ask_hermes(prompt)
    gen = None
    if not ok:
        reply, design, extra = fallback_reply(message)
    else:
        reply, design = out, None
        # generative mode first: ```cad (build123d) preferred, ```scad legacy
        cad_src, cad_header = parse_cad_block(out)
        if cad_src:
            gen = {"cad": cad_src, "header": cad_header, "notes": []}
            reply = CAD_RE.sub("", out).strip()
            st["design"] = None  # generative replaces parametric state
        else:
            src, header, scad_notes = parse_scad_block(out)
            if src:
                gen = {"scad": src, "header": header, "notes": scad_notes}
                reply = SCAD_RE.sub("", out).strip()
                st["design"] = None
        if not gen:
            m = DESIGN_RE.search(out)
            if m:
                try:
                    d = json.loads(m.group(1))
                    fam = d.get("family", "").lower()
                    if fam in T.SCHEMAS:
                        design = {"family": fam, "params": d.get("params", {}),
                                  "material": d.get("material", "6061-T6")}
                    reply = DESIGN_RE.sub("", out).strip() or "Here you go."
                except json.JSONDecodeError:
                    pass
    st["history"].append({"role": "agent", "text": reply})
    if design:
        clean, notes = T.normalize(design["family"], design["params"])
        clean["material"] = design["material"]
        if design.get("params", {}).get("material"):
            clean["material"] = design["params"]["material"]
        st["design"] = clean
    else:
        clean, notes = st.get("design"), []
    return reply, clean, notes, gen

app = FastAPI()

def kernel_verify(session, clean=None, gen_src=None, header=None):
    """Run the build123d kernel. Returns (metrics, checks, mass, preview_ok) or
    (None, reason) if the CAD python is unavailable on this host."""
    try:
        import cad_kernel as K
    except Exception:
        return None, "kernel not installed on this host"
    material = (header or {}).get("material") or (clean or {}).get("material", "6061-T6")
    material = material.split(",")[0].strip()
    if material not in K.DENSITIES:
        material = "6061-T6"
    name = session
    if gen_src is not None:
        m = K.run_kernel(gen_src, name)
    elif clean is not None:
        try:
            src = K.family_script(clean["family"], clean)
        except KeyError:
            return None, f"no family builder for {clean['family']}"
        m = K.run_kernel(src, name)
    else:
        return None, "nothing to verify"
    if not m.get("ok"):
        return m, m.get("error", "kernel failed")
    checks, mass = K.dfm_checks(m, material)
    png, _ = K.render_png(name)
    m["mass_kg"] = mass
    m["material"] = material
    m["checks"] = checks
    m["preview_url"] = f"/artifacts/{name}/preview.png" if png else None
    return m, checks

import threading

RESULTS = {}   # session -> {"status": "working"/"done"/"error", "result": resp}
LOCKS = {}

def _build_reply(session, message):
    st = load_state(session)
    reply, design, notes, gen = process(message, st)

    # generative: kernel verification with one auto-repair retry
    kernel = None
    if gen and gen.get("scad") is None and gen.get("cad"):
        km, err = kernel_verify(session, gen_src=gen["cad"], header=gen.get("header"))
        if km is not None and not km.get("ok"):
            # auto-repair: feed the kernel error back to the agent once
            fix_prompt = build_prompt(st, (
                f"[KERNEL FEEDBACK — your build123d code failed verification]\n"
                f"{err}\nRewrite the COMPLETE corrected ```cad block. Prose: one line."))
            out2, ok2 = ask_hermes(fix_prompt)
            if ok2:
                src2, header2 = parse_cad_block(out2)
                if src2:
                    gen = {"cad": src2, "header": header2 or gen.get("header"), "notes": []}
                    reply = CAD_RE.sub("", out2).strip() or reply
                    st["history"].append({"role": "agent", "text": reply})
                    km, err = kernel_verify(session, gen_src=src2, header=header2)
        kernel = km
    elif design:
        km, err = kernel_verify(session, clean=design)
        if km is not None:
            kernel = km
            if km.get("ok"):
                design["mass_kg"] = km.get("mass_kg")

    save_state(session, st)
    resp = {"reply": reply, "dfm_notes": notes}
    if gen:
        h = gen.get("header", {})
        resp["generative"] = {
            "cad": gen.get("cad") or gen.get("scad", ""),
            "bbox": h.get("bbox", ""),
            "material": h.get("material", ""),
            "notes": gen.get("notes", []),
        }
    if kernel:
        resp["kernel"] = {
            "ok": kernel.get("ok"),
            "error": kernel.get("error"),
            "volume_mm3": kernel.get("volume_mm3"),
            "area_mm2": kernel.get("area_mm2"),
            "size_mm": kernel.get("size_mm"),
            "mass_kg": kernel.get("mass_kg"),
            "material": kernel.get("material"),
            "solids_n": kernel.get("solids_n"),
            "breps_valid": kernel.get("breps_valid"),
            "checks": kernel.get("checks", []),
            "preview_url": kernel.get("preview_url"),
            "step_url": f"/artifacts/{session}/part.step",
            "stl_url": f"/artifacts/{session}/part.stl",
        }
    if design:
        resp.update(design={
            "family": design["family"], "params": design},
            svg=T.render_svg(design), scad=T.render_scad(design),
            sheet=T.spec_sheet(design, notes),
            mass_kg=design.get("mass_kg", T.mass_kg(design)))
    return resp

def _worker(session, message):
    try:
        RESULTS[session] = {"status": "working"}
        RESULTS[session] = {"status": "done", "result": _build_reply(session, message)}
    except Exception as e:
        RESULTS[session] = {"status": "error", "result": {"reply": f"[error] {e}"}}

@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    session = body.get("session", "default")
    message = body.get("message", "")
    lock = LOCKS.setdefault(session, threading.Lock())
    with lock:
        cur = RESULTS.get(session, {})
        if cur.get("status") == "working":
            return {"accepted": True, "status": "working", "session": session}
        threading.Thread(target=_worker, args=(session, message), daemon=True).start()
    return {"accepted": True, "status": "queued", "session": session}

@app.get("/result/{session}")
def result(session: str):
    cur = RESULTS.get(session)
    if not cur:
        return {"status": "none"}
    return {"status": cur["status"], "result": cur.get("result")}

from fastapi.responses import FileResponse

@app.get("/artifacts/{name}/{fn}")
def artifact(name: str, fn: str):
    if "/" in fn or ".." in fn or "/" in name or ".." in name:
        return {"error": "bad path"}
    p = ROOT / "artifacts" / name / fn
    if not p.exists():
        return {"error": "not found"}
    media = {"part.step": "application/step", "part.stl": "model/stl",
             "preview.png": "image/png"}.get(fn, "application/octet-stream")
    return FileResponse(p, media_type=media, filename=fn)

@app.get("/health")
def health():
    return {"ok": True}

app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

@app.get("/")
def index():
    from fastapi.responses import FileResponse
    return FileResponse(ROOT / "static" / "index.html")