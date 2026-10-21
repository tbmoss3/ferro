# Ferro — Front Door Prototype v0.1

Natural-language design intake for a bespoke CNC shop. Customer describes a part →
a **live Hermes agent** interrogates/iterates → parametric design → SVG preview +
OpenSCAD source + spec sheet → "Approve for CAD" (CAD→CAM deferred).

## Run locally
```
cd ~/ferro-prototype
python3 -m uvicorn server:app --host 127.0.0.1 --port 8811
open http://127.0.0.1:8811
```
Requires `hermes` on PATH (the agent brain shells out to `hermes chat --query-file
… --oneshot -Q`). Falls back to canned replies if Hermes is unavailable.

## Parts
- `server.py` — FastAPI app. POST /chat {session, message} → {reply, design, svg,
  scad, sheet, dfm_notes, mass_kg}. Per-session state in ./sessions/*.json.
- `templates.py` — parametric families (bracket / plate / enclosure), DFM guardrail
  clamps, isometric SVG renderer (auto-fit), OpenSCAD emitter, spec sheet, mass est.
- `static/` — single-page chat UI, demo chips for the three families.

## Design doctrine (from the Ferro spec)
Agents orchestrate, kernels calculate: every number in the design packet traces to
deterministic parametric code, not model invention. The agent's job is interrogation
and parameter translation; the geometry engine owns the math. DFM rules clamp
parameters (edge distance, wall thickness, port fit) and log notes — the agent can
reason but cannot bypass the clamps.

## Deploy to Railway (TODO — needs Benton)
- Dockerfile included. **Blocker:** the agent brain requires a Hermes install plus
  provider credentials (OpenRouter key) inside the container. Two options:
  1. Inject the key as a Railway env var and point `HERMES_HOME` at a volume with
     a minimal config.yaml + .env (profile-less default).
  2. Swap `ask_hermes()` for a direct OpenAI-compatible API call (simpler container,
     loses Hermes skills/memory). Prototype currently uses option "local Hermes".
- Railway deploy steps once creds decided: `railway init && railway up` from this dir.
- NOTE: this host's server binds 127.0.0.1 — for a public URL Railway or a reverse
  proxy is required; do not expose this dev process directly.

## Roadmap (deferred by design)
- CAD step: APS Design Automation (AutoCAD/Fusion WorkItems) or AccoreConsole on a
  Windows box for on-prem/ITAR jobs.
- CAM step: postprocessors per controller family, simulation gate before G-code ships.
- Learning loop: sessions/*.json + estimate-vs-actual → question templates, presets,
  calibration. This is the compounding asset.