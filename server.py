"""Ferro front door — FastAPI prototype.
Agent brain = live Hermes one-shot calls (hermes chat -q) with conversation + design state.
"""
import json, re, os, subprocess, tempfile
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
import templates as T

ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("FERRO_DATA_DIR", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR = DATA_DIR / "sessions"
STATE_DIR.mkdir(exist_ok=True)

import sqlite3, threading as _th
DB_LOCK = _th.Lock()
DB = str(DATA_DIR / "ferro.db")

def db():
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    return con

def db_init():
    with DB_LOCK:
        con = db()
        con.execute("""CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            session TEXT, created TEXT, updated TEXT,
            family TEXT, request TEXT, reply TEXT,
            design TEXT, kernel TEXT, quote TEXT
        )""")
        con.commit()
        con.close()

db_init()

def order_upsert(order_id, session, family, request, reply, design, kernel, quote):
    import time as _t, json as _j
    now = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    with DB_LOCK:
        con = db()
        row = con.execute("SELECT order_id FROM orders WHERE session=?", (session,)).fetchone()
        if row:
            con.execute("""UPDATE orders SET updated=?, family=?, request=?, reply=?,
                design=?, kernel=?, quote=? WHERE order_id=?""",
                (now, family, request, reply, _j.dumps(design), _j.dumps(kernel),
                 _j.dumps(quote), row[0]))
            order_id = row[0]
        else:
            con.execute("""INSERT INTO orders (order_id, session, created, updated, family,
                request, reply, design, kernel, quote) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (order_id, session, now, now, family, request, reply,
                 _j.dumps(design), _j.dumps(kernel), _j.dumps(quote)))
        con.commit()
        con.close()
    return order_id

def order_get(order_id):
    with DB_LOCK:
        con = db()
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        con.close()
    return dict(row) if row else None

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
{"family": "<bracket|plate|enclosure|flange|spacer|shaft_collar|tube_rack|tube_clamp|grid_plate|extrusion_bracket|pulley|leveling_foot|port_block>", "params": {...}, "material": "316L"}
```
Include absolute values for the keys you're setting. On edits, re-emit the FULL set.
DEFAULT MATERIAL: if the customer does not name a material, use 316L — and SAY so:
"Defaulting to 316L stainless (pharma-safe) — 6061 aluminum is cheaper if corrosion isn't a factor."
CHEAPEST ROUTE RULE (critical): if the product is an assembly or has long thin members —
IV stands, frames, tables, weldments, tube + base constructions — NEVER machine it from solid billet.
Propose the fabrication route instead: standard tube/stock members + machined plates/joints,
welded or bolted. Quote only the machined components through Ferro, describe the rest as
fabrication, and ask before hogging anything expensive out of solid. For multi-piece products,
ask about assembly preferences (weld vs bolt) and filler material if welding. State plainly
which pieces Ferro will model and which are stock lengths/fabrication.

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

def build_prompt(st, message, file_ctx=None):
    hist = st["history"][-14:]
    lines = [AGENT_SYS, "", "== CONVERSATION SO FAR =="]
    for h in hist:
        lines.append(f"{'CUSTOMER' if h['role']=='user' else 'FERRO AGENT'}: {h['text'][:700]}")
    if st.get("design"):
        lines.append(f"== CURRENT DESIGN STATE == {json.dumps(st['design'])}")
    if file_ctx:
        lines.append(f"== FILE INTAKE (NEW INPUT — use it) ==\n{file_ctx}")
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
    design = {"family": fam, "params": params, "material": "316L"}
    return (f"Let's start with a parametric {fam} in 316L — preview and packet below. "
            "(Offline-mode defaults; agent unavailable.)"), design, []

def _parse_agent_reply(st, out, ok):
    """Parse a brain reply into (reply, design, notes, gen) + update history/design state."""
    gen = None
    design = None
    if not ok:
        reply = out  # fallback text path handled by caller
        notes = []
    else:
        reply, design = out, None
        cad_src, cad_header = parse_cad_block(out)
        if cad_src:
            gen = {"cad": cad_src, "header": cad_header, "notes": []}
            reply = CAD_RE.sub("", out).strip()
            st["design"] = None
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
                                  "material": d.get("material", "316L")}
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

def process(message, st):
    prompt = build_prompt(st, message)
    st["history"].append({"role": "user", "text": message})
    out, ok = ask_hermes(prompt)
    if not ok:
        reply, design, extra = fallback_reply(message)
        st["history"].append({"role": "agent", "text": reply})
        clean = st.get("design")
        return reply, clean, [], None
    return _parse_agent_reply(st, out, True)

app = FastAPI()

def kernel_verify(session, clean=None, gen_src=None, header=None):
    """Run the build123d kernel. Returns (metrics, checks, mass, preview_ok) or
    (None, reason) if the CAD python is unavailable on this host."""
    import time as _time
    try:
        import cad_kernel as K
    except Exception:
        return None, "kernel not installed on this host"
    material = (header or {}).get("material") or (clean or {}).get("material", "316L")
    material = material.split(",")[0].strip()
    if material not in K.DENSITIES:
        material = "316L"
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
    drawing = K.render_drawing(name)
    m["mass_kg"] = mass
    m["material"] = material
    m["checks"] = checks
    m["rev"] = int(_time.time())          # cache-bust: force clients to refetch artifacts
    m["preview_url"] = f"/artifacts/{name}/preview.png" if png else None
    m["glb_url"] = f"/artifacts/{name}/part.glb" if m.get("glb") else None
    if drawing:
        m["drawing_svg_url"] = f"/artifacts/{name}/drawing.svg"
        m["drawing_dxf_url"] = f"/artifacts/{name}/drawing.dxf"
        m["drawing_png_url"] = f"/artifacts/{name}/drawing.png"
    return m, checks

import threading

RESULTS = {}   # session -> {"status": "working"/"done"/"error", "result": resp}
LOCKS = {}

def _build_reply(session, message, file=None):
    st = load_state(session)

    # ---- file ingestion: build a context block for the brain ----
    file_ctx = None
    file_kernel = None
    stored = None
    if file:
        try:
            import file_ingest as FI
            data = file.get("data", b"")
            if isinstance(data, str):
                data = data.encode("latin-1", "replace")
            okf, stored = FI.save_upload(session, file.get("name", "upload.bin"), data)
            if okf:
                file_ctx, file_kernel = FI.analyze_upload(session, stored, message)
            else:
                file_ctx = f"== ATTACHED FILE REJECTED ==\n{stored}\nTell the customer politely."
        except Exception as e:
            file_ctx = f"== ATTACHED FILE ERROR ==\n{e}\nTell the customer to re-send or describe in words."

    if file_ctx:
        st["history"].append({"role": "user",
                               "text": (message or "") + (f" [attached: {file.get('name')}]" if file else "")})
        out, ok = ask_hermes(build_prompt(st, message, file_ctx))
        if ok:
            reply, design, notes, gen = _parse_agent_reply(st, out, True)
        else:
            reply, design, notes, gen = fallback_reply(message)
            st["history"].append({"role": "agent", "text": reply})
            design = None
    else:
        reply, design, notes, gen = process(message, st)

    # deterministic STEP-import metrics become the kernel result
    if file_kernel and file_kernel.get("ok"):
        import cad_kernel as K, time as _t, shutil
        png, _ = K.render_png(session)
        drawing = K.render_drawing(session)
        fk = dict(file_kernel)
        fk["mass_kg"] = round(fk["volume_mm3"] * K.DENSITIES.get("316L", 8000) * 1e-9, 3)
        fk["material"] = "316L (assumed — confirm)"
        fk["checks"] = []
        fk["rev"] = int(_t.time())
        fk["preview_url"] = f"/artifacts/{session}/preview.png" if png else None
        fk["glb"] = False
        if drawing:
            fk["drawing_svg_url"] = f"/artifacts/{session}/drawing.svg"
            fk["drawing_dxf_url"] = f"/artifacts/{session}/drawing.dxf"
            fk["drawing_png_url"] = f"/artifacts/{session}/drawing.png"
        fk["step_url"] = f"/artifacts/{session}/part.step"
        fk["stl_url"] = f"/artifacts/{session}/part.stl"
        try:
            up = K.ARTIFACTS.parent / "uploads" / session / file.get("name", "")
            if up.exists():
                shutil.copy(up, K.ARTIFACTS / session / "part.step")
        except Exception:
            pass
        file_kernel = fk

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
            "rev": kernel.get("rev"),
            "preview_url": kernel.get("preview_url"),
            "glb_url": kernel.get("glb_url"),
            "drawing_svg_url": kernel.get("drawing_svg_url"),
            "drawing_dxf_url": kernel.get("drawing_dxf_url"),
            "drawing_png_url": kernel.get("drawing_png_url"),
            "step_url": f"/artifacts/{session}/part.step",
            "stl_url": f"/artifacts/{session}/part.stl",
        }
    if file_kernel:
        fk = file_kernel
        resp["kernel"] = {
            "ok": True,
            "volume_mm3": fk.get("volume_mm3"),
            "area_mm2": fk.get("area_mm2"),
            "size_mm": fk.get("size_mm"),
            "mass_kg": fk.get("mass_kg"),
            "material": fk.get("material"),
            "solids_n": fk.get("solids_n"),
            "breps_valid": fk.get("breps_valid"),
            "checks": [],
            "rev": fk.get("rev"),
            "preview_url": fk.get("preview_url"),
            "glb_url": None,
            "drawing_svg_url": fk.get("drawing_svg_url"),
            "drawing_dxf_url": fk.get("drawing_dxf_url"),
            "drawing_png_url": fk.get("drawing_png_url"),
            "step_url": fk.get("step_url"),
            "stl_url": fk.get("stl_url"),
            "source": "imported STEP (deterministic)",
        }
    if file_ctx:
        resp["file_intake_debug"] = file_ctx[:600]
    if design:
        resp.update(design={
            "family": design["family"], "params": design},
            svg=T.render_svg(design), scad=T.render_scad(design),
            sheet=T.spec_sheet(design, notes),
            mass_kg=design.get("mass_kg", T.mass_kg(design)))

    # ---- quote + persistent order (kernel-measured or STEP-imported) ----
    kx = resp.get("kernel") or {}
    if kx.get("ok") and kx.get("volume_mm3"):
        import quotes, time as _t, secrets as _sec
        q = quotes.quote(
            kx["volume_mm3"], kx.get("area_mm2") or 0,
            material=kx.get("material") or "316L",
            family=(resp.get("design") or {}).get("family"),
            solids=kx.get("solids_n") or 1)
        oid = order_upsert(
            _sec.token_hex(4), session,
            (resp.get("design") or {}).get("family") or kx.get("source") or "part",
            message[:500], resp.get("reply", ""),
            resp.get("design") or {}, kx, q)
        resp["quote"] = q
        resp["order_id"] = oid
        resp["order_url"] = f"/order/{oid}"
    return resp

def _worker(session, message, file=None):
    try:
        RESULTS[session] = {"status": "working"}
        RESULTS[session] = {"status": "done", "result": _build_reply(session, message, file)}
    except Exception as e:
        RESULTS[session] = {"status": "error", "result": {"reply": f"[error] {e}"}}

@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    session = body.get("session", "default")
    message = body.get("message", "")
    file = body.get("file")  # {"name": ..., "data_b64": ...}
    if file and file.get("data_b64"):
        import base64 as _b64
        file["data"] = _b64.b64decode(file["data_b64"])
    lock = LOCKS.setdefault(session, threading.Lock())
    with lock:
        cur = RESULTS.get(session, {})
        if cur.get("status") == "working":
            return {"accepted": True, "status": "working", "session": session}
        threading.Thread(target=_worker, args=(session, message, file), daemon=True).start()
    return {"accepted": True, "status": "queued", "session": session}

@app.get("/result/{session}")
def result(session: str):
    cur = RESULTS.get(session)
    if not cur:
        # restart recovery: latest order for this session, from SQLite
        with DB_LOCK:
            con = db()
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT * FROM orders WHERE session=? ORDER BY updated DESC LIMIT 1", (session,)).fetchone()
            con.close()
        if row:
            import json as _j
            r = {
                "reply": row["reply"] or "",
                "design": _j.loads(row["design"] or "null"),
                "kernel": _j.loads(row["kernel"] or "null"),
                "quote": _j.loads(row["quote"] or "null"),
                "order_id": row["order_id"],
                "order_url": f"/order/{row['order_id']}",
                "dfm_notes": [],
            }
            return {"status": "done", "result": r}
        return {"status": "none"}
    return {"status": cur["status"], "result": cur.get("result")}

from fastapi.responses import FileResponse

@app.get("/artifacts/{name}/{fn}")
def artifact(name: str, fn: str):
    if "/" in fn or ".." in fn or "/" in name or ".." in name:
        return {"error": "bad path"}
    p = DATA_DIR / "artifacts" / name / fn
    if not p.exists():
        return {"error": "not found"}
    media = {"part.step": "application/step", "part.stl": "model/stl",
             "part.glb": "model/gltf-binary",
             "preview.png": "image/png", "drawing.png": "image/png",
             "drawing.svg": "image/svg+xml",
             "drawing.dxf": "application/dxf"}.get(fn, "application/octet-stream")
    return FileResponse(p, media_type=media, filename=fn)

ORDER_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Ferro order __OID__</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font-family:system-ui,sans-serif;background:#0d0f13;color:#ecebe6;margin:0;padding:24px;}
 .card{max-width:640px;margin:0 auto;background:#171a20;border:1px solid #2e343f;border-radius:14px;padding:22px;}
 h1{font-size:20px;margin:0 0 4px;} .dim{color:#a3aab6;} .acc{color:#e8853d;}
 .row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #232830;font-size:14px;}
 .big{font-size:22px;font-weight:800;}
 img{max-width:100%;border-radius:10px;margin:10px 0;}
 a{color:#e8853d;} .badge{display:inline-block;border:1px solid #e8853d55;color:#e8853d;
   border-radius:99px;padding:3px 10px;font-size:12px;margin-right:6px;}
</style></head><body><div class="card">
<h1>FERRO <span class="acc">.</span> order <span class="acc">__OID__</span></h1>
<div class="dim">__FAMILY__ · created __CREATED__ · updated __UPDATED__</div>
<div class="row"><span class="dim">Est. price</span><span class="big acc">$__PRICE__</span></div>
<div class="row"><span class="dim">Lead time</span><span>~__LEAD__ days</span></div>
<div class="row"><span class="dim">Machine time</span><span>__HRS__ hr est.</span></div>
<div class="row"><span class="dim">Stock</span><span>__STOCK__ kg __MAT__</span></div>
<div class="row"><span class="dim">Status</span><span class="badge">awaiting design approval</span></div>
<img src="__PREVIEW__" alt="part render">
<p class="dim">__BASIS__</p>
<p><a href="/">← back to the design agent</a></p>
</div></body></html>"""

@app.get("/order/{oid}")
def order_page(oid: str):
    o = order_get(oid)
    if not o:
        return {"error": "order not found"}
    import json as _j
    q = _j.loads(o.get("quote") or "{}")
    k = _j.loads(o.get("kernel") or "{}")
    page = (ORDER_PAGE
        .replace("__OID__", oid)
        .replace("__FAMILY__", str(o.get("family") or "part"))
        .replace("__CREATED__", str(o.get("created") or ""))
        .replace("__UPDATED__", str(o.get("updated") or ""))
        .replace("__PRICE__", str(q.get("price", "-")))
        .replace("__LEAD__", str(q.get("lead_days", "-")))
        .replace("__HRS__", str(q.get("machine_hrs", "-")))
        .replace("__STOCK__", str(q.get("stock_kg", "-")))
        .replace("__MAT__", str(k.get("material") or ""))
        .replace("__PREVIEW__", k.get("preview_url") or "")
        .replace("__BASIS__", str(q.get("basis") or "")))
    from fastapi.responses import HTMLResponse
    return HTMLResponse(page)

@app.get("/orders")
def orders_list():
    with DB_LOCK:
        con = db()
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT order_id, session, family, created, quote FROM orders ORDER BY updated DESC LIMIT 100").fetchall()
        con.close()
    import json as _j
    return [{"order_id": r["order_id"], "session": r["session"], "family": r["family"],
             "created": r["created"], "quote": _j.loads(r["quote"] or "{}")}
            for r in rows]

@app.get("/health")
def health():
    return {"ok": True}

app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

@app.get("/")
def index():
    from fastapi.responses import FileResponse
    return FileResponse(ROOT / "static" / "index.html")