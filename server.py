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

You have a parametric engine with these part families and parameters (mm):
- bracket: base_len, base_wid, base_th, vert_len, vert_th, hole_d, hole_count, fillet_r
- plate: length, width, thickness, hole_d, hole_count, corner_r
- enclosure: width, depth, height, wall, lid, vents, port_w, port_h
Materials in stock: 6061-T6, 7075-T6, steel-1018, 304-SS, delrin.

RULES:
1. Reply conversationally — brief, concrete, engineer-to-engineer. No fluff.
2. Whenever the design is concrete enough, END your message with a fenced block:
```design
{"family": "<bracket|plate|enclosure>", "params": {...only keys you want to set...}, "material": "6061-T6"}
```
Include the params you are setting given the conversation so far (absolute values, not deltas).
3. On edits ("make it wider", "thicker"), update params and re-emit the design block with the FULL updated param set.
4. You may add a "dfm_comment" string inside the design JSON with one manufacturing note.
5. Never emit parameters outside the families above. Pick the closest family if unsure and say so.
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
    if not ok:
        reply, design, extra = fallback_reply(message)
    else:
        reply, design = out, None
        m = DESIGN_RE.search(out)
        dfm_comment = None
        if m:
            try:
                d = json.loads(m.group(1))
                fam = d.get("family", "").lower()
                if fam in T.SCHEMAS:
                    design = {"family": fam, "params": d.get("params", {}),
                              "material": d.get("material", "6061-T6")}
                    dfm_comment = d.get("dfm_comment")
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
    return reply, clean, notes

app = FastAPI()

@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    st = load_state(body.get("session", "default"))
    reply, design, notes = process(body.get("message", ""), st)
    save_state(body["session"], st)
    resp = {"reply": reply, "dfm_notes": notes}
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