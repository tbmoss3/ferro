# Ferro — Partner Setup & First Scrap Test

**What you'll do:** (1) design a part end-to-end in the browser, (2) download
G-code and run it on scrap.

CAM is **already live in production** (as of Oct 29, 2026): the CAM service
runs at https://ferro-cam-production.up.railway.app behind the main site,
and the G-code dialect is set to `linuxcnc`. Nothing to configure — skip to
Part 1.

*If the dialect ever needs changing:* Benton's side sets the
`FERRO_CAM_POST` variable on the `ferro` service (options: `linuxcnc`,
`grbl`, `centroid`, `mach3_mach4`, `fanuc`, `generic`).

---

## Part 1 — Design a part end-to-end (~3–5 min)

1. Go to https://ferro.up.railway.app → **Describe your part**.
   Example for a first test — simple, forgiving geometry:

   > a 316L stainless base plate, 150mm x 100mm x 12mm thick, with four
   > 9mm mounting holes at the corners, 15mm in from each edge

2. Answer the agent's questions if it asks any, then wait — the packet
   (spec / DFM checks / 3D preview / downloads) appears when the CAD kernel
   finishes. Complex parts take 2–5 minutes.
3. Check the design packet:
   - **Spec tab** — dimensions match what you asked for
   - **Checks tab** — kernel verification (single valid solid, mass, volume)
   - **3D preview** — spin it, make sure it's the part you meant
4. Look for the **CAM row**: "⚙ CAM · 3-axis mill · linuxcnc · N lines ·
   ⇩ G-code — ✓ sim gate: passed". If it says "gated — turned part" that
   family runs on the lathe and Ferro won't fake it (manual CAM).
5. Click **⇩ G-code** and also **⇩ STEP** (the STEP is the design reference).

## Part 2 — Run it on scrap (the careful part)

The G-code is **PROTOTYPE GRADE**. Treat the first run like any new program:

1. **Read the header.** Line 1 says "NOT MACHINE-READY — VERIFY ON SHOP
   FLOOR BEFORE USE." That's the honest status.
2. **Simulation-gate results** are in the CAM row (rapid distance, cut
   distance, max depth). The program was checked for rapid plunges into
   stock, feed limits, and staying inside the stock envelope — but it has
   NOT been checked against your fixture, your work offsets, or your tool
   table.
3. **Do what you always do with new programs:**
   - Dry run above the part (Z offset +10 or whatever your practice is)
   - Single block / feed hold the first contour pass
   - Verify tool T1 (6mm endmill) and T2 (5.2mm drill) lengths match your
     G43 H1/H2 offsets — **the program assumes the tool table is set up**
   - Work origin: the program is centered on the part XY center, Z0 at the
     TOP of the stock
   - Feeds are conservative (316L: 480 mm/min contour, 280 plunge) — adjust
     to taste after the first pass
4. **Scrap first, always.** Use stock at least ~5mm bigger than the part in
   X/Y and confirm chip load looks sane on the first pass.
5. When it runs clean, tell Benton the actual cycle time + any feed changes —
   that's the calibration data that makes the next program better.

## Known limits (don't get surprised)

- **Lathe parts** (pulleys, shaft collars): gated to manual CAM on purpose.
- **Drill tool**: the drilling cycle uses a 5.2mm drill for 9mm holes in the
  example above — for a real 9mm hole, spot-drill + step up or single-flute;
  treat drill sizes in the program as placeholders unless you tell us your
  actual tool list.
- **Fixture**: programs assume the part is held from outside the contour
  (vise jaws / clamps), no tabs. If you need tabs, say so and we'll add them.
- **5-axis / indexed work**: not supported yet (2.5D only).

## If something breaks

- CAM row says "unavailable in this environment" → CAM_URL isn't set or the
  CAM service is down; check Part 1.
- Build fails on the CAM service → check its Deployments → "View Logs";
  most likely the conda/FreeCAD install; a retry usually clears it.
- Anything else → screenshot the packet and send it to Benton.
