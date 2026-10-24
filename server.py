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
If the request fits (or nearly fits) a family, END your message with:
```design
{"family": "<bracket|plate|enclosure>", "params": {...}, "material": "6061-T6"}
```
Include absolute values for the keys you're setting. On edits, re-emit the FULL set.
MANDATORY SELF-CHECK before replying: re-read the customer's message and verify every
number they stated (dimensions, hole counts, hole sizes, thicknesses) is reflected
in your design/scad block. If they said six holes, hole_count must be 6. State the
full parameter list in your prose so the customer can catch any drift.

MODE 2 — GENERATIVE (any part that does not fit a family: brackets with gussets,
V-blocks, fixtures, spacers, hinges, custom frames, sculptural parts, etc.).
Write complete OpenSCAD source (valid, self-contained, $fn>=64, mm units) that
produces the part. END your message with:
```scad
// GENERATIVE DESIGN — REQUIRES CAM REVIEW
// bbox: <approx W x D x H mm>
// material: <stock material>
// notes: <one line on machining approach>
<complete OpenSCAD source>
```
Rules for generative mode:
- Prefer primitive combinations (cube/cylinder/hull) over obscure functions.
- Put holes/cutouts as difference() from the solid, clean edges.
- State assumed stock size in the header comment.
- Be conservative: simple, machinable geometry. No threads, no text engraving.
- Always say in prose that this draft needs engineer review before machining.

Materials in stock: 6061-T6, 7075-T6, steel-1018, 304-SS, delrin.

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
        # generative mode first: a ```scad block means bespoke geometry
        src, header, scad_notes = parse_scad_block(out)
        if src:
            gen = {"scad": src, "header": header, "notes": scad_notes}
            reply = SCAD_RE.sub("", out).strip()
            st["design"] = None  # generative replaces parametric state
        else:
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

@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    st = load_state(body.get("session", "default"))
    reply, design, notes, gen = process(body.get("message", ""), st)
    save_state(body["session"], st)
    resp = {"reply": reply, "dfm_notes": notes}
    if gen:
        h = gen.get("header", {})
        resp["generative"] = {
            "scad": gen["scad"],
            "bbox": h.get("bbox", ""),
            "material": h.get("material", ""),
            "notes": gen["notes"],
        }
    if design:
        resp.update(design={
            "family": design["family"], "params": design},
            svg=T.render_svg(design), scad=T.render_scad(design),
            sheet=T.spec_sheet(design, notes),
            mass_kg=T.mass_kg(design))
    return resp

@app.get("/health")
def health():
    return {"ok": True}

app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

@app.get("/")
def index():
    from fastapi.responses import FileResponse
    return FileResponse(ROOT / "static" / "index.html")