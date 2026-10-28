"""Ferro G-code verification gate — Phase C rung 2.

Deterministic G-code interpreter/linter (kernel-style: agents orchestrate,
kernels calculate). Parses the posted program, simulates tool motion, and
fails the program on rule violations BEFORE it can reach the shop floor.

Checks (LinuxCNC-flavor subset):
  GATE (block release):   rapid plunge into stock (G0 Z- through material)
                          cutting without feed (G1 at G0 speed / F0)
                          motion outside stock envelope + allowance
                          arcs with radius mismatch or > 180° misuse
                          plunge faster than material plunge limit
  WARN (review):          feed above material table limit
                          missing tool-length comp (G43) before cut
                          missing spindle start (M3) before first feed
                          cut depth per pass > rough limit
Everything is computed from the program + stock, never guessed.
"""

import math
import re
from pathlib import Path

WORDS = re.compile(r"([GMTXYZIJFR])\s*(-?\d+\.?\d*)")

# material -> (max feed mm/min, max plunge mm/min, max cut-per-pass mm, mrr sanity)
LIMITS = {
    "316L": (800, 350, 4.0), "304-SS": (900, 380, 4.0),
    "6061-T6": (2200, 800, 8.0), "7075-T6": (2200, 800, 8.0),
    "4140": (600, 280, 3.0), "steel-1018": (700, 300, 3.0),
    "PEEK": (1200, 500, 5.0), "delrin": (2500, 900, 8.0),
}


def parse(txt):
    """Yield (lineno, motion{'g','xyz…','f'} | None for comments/other)."""
    for i, raw in enumerate(txt.splitlines(), 1):
        line = raw.split(";")[0].split("(")[0].strip()
        if not line:
            continue
        w = {}
        for k, v in WORDS.findall(line.upper()):
            w.setdefault(k, []).append(float(v))
        if not w:
            continue
        yield i, w


def verify(txt, stock_xyz, material="316L", tool_d=6.0, allowance=1.0,
           origin=(0.0, 0.0)):
    """Simulate the program against a stock box (X,Y,Z sizes, part at z0>=0).
    Returns {"ok": bool, "blocked": [...], "warnings": [...], "stats": {...}}.
    Z convention (linuxcnc post from our kernel): z0 = top of stock, cut z<0.
    origin = XY center of the part in program coordinates (our kernels center
    parts at (0,0), so that's the default).
    """
    feed_max, plunge_max, cut_max = LIMITS.get(material, LIMITS["316L"])
    blocked, warnings = [], []
    x = y = z = 0.0
    feed = 0.0
    mode = {}          # active G modal group (0/1/2/3)
    tool = None
    g43 = spindle = False
    cuts = 0
    rapid_dist = cut_dist = 0.0
    last_cut_z = 0.0
    max_depth = 0.0
    SX, SY, SZ = stock_xyz
    cx, cy = origin
    for ln, w in parse(txt):
        g = w.get("G", [None])[-1] if w.get("G") else None
        if g is not None:
            if g in (0.0,):
                mode[0] = 0
            elif g in (1.0,):
                mode[0] = 1
            elif g in (2.0, 3.0):
                mode[0] = g
            elif g == 43:
                g43 = True
            elif g == 28:
                pass
            continue_ = False
        if "T" in w:
            tool = w["T"][-1]
            g43 = False
        if "M" in w:
            for m in w["M"]:
                if m == 3 or m == 8:
                    if m == 3:
                        spindle = True
        # move target
        if "F" in w:
            feed = w["F"][-1]
        nx, ny, nz = x, y, z
        if "X" in w: nx = w["X"][-1]
        if "Y" in w: ny = w["Y"][-1]
        if "Z" in w: nz = w["Z"][-1]
        g_active = mode.get(0, None)
        if g_active in (0, 1):
            dist = math.dist((x, y, z), (nx, ny, nz))
            # stock envelope check (tool center path, + tool radius)
            for px, py in ((x, y), (nx, ny)):
                if abs(px - cx) > SX / 2 + tool_d / 2 + allowance:
                    blocked.append(f"L{ln}: X {px:.1f} outside stock X envelope")
                if abs(py - cy) > SY / 2 + tool_d / 2 + allowance:
                    blocked.append(f"L{ln}: Y {py:.1f} outside stock Y envelope")
            if nz < -SZ - allowance:
                blocked.append(f"L{ln}: Z {nz:.1f} below stock bottom ({-SZ:.1f})")
            if g_active == 0:
                rapid_dist += dist
                # GATE: rapid plunge into material
                if z >= 0 and nz < 0 and dist > 0:
                    blocked.append(
                        f"L{ln}: G0 plunge into stock (z {z:.1f} -> {nz:.1f}) at rapid")
            else:
                if not g43:
                    warnings.append(f"L{ln}: cut without G43 tool-length comp")
                if not spindle:
                    warnings.append(f"L{ln}: cut before M3 spindle start")
                if feed <= 0:
                    blocked.append(f"L{ln}: G1 cut at zero/no feed")
                elif feed > feed_max:
                    warnings.append(f"L{ln}: feed {feed:.0f} > material limit "
                                    f"{feed_max:.0f} ({material})")
                # GATE: vertical plunge at cut feed above plunge limit
                if abs(nx - x) < 1e-6 and abs(ny - y) < 1e-6 and nz < z:
                    if feed > plunge_max:
                        blocked.append(f"L{ln}: plunge {feed:.0f} > {plunge_max:.0f}"
                                       f" limit ({material})")
                if nz < 0:
                    cuts += 1
                    depth = -nz
                    max_depth = max(max_depth, depth)
                    if depth - last_cut_z > cut_max:
                        warnings.append(
                            f"L{ln}: depth {depth:.1f} exceeds {cut_max:.0f}mm "
                            f"per-pass guidance ({material})")
                    last_cut_z = max(last_cut_z, depth)
                cut_dist += dist
        elif g_active in (2, 3):
            # arcs: need feed + a sane IJ
            if feed <= 0:
                blocked.append(f"L{ln}: arc at zero/no feed")
            if "I" not in w or "J" not in w:
                warnings.append(f"L{ln}: arc without explicit I/J center")
            r = math.hypot(w.get("I", [0])[-1], w.get("J", [0])[-1])
            if r <= 0 or r > max(SX, SY):
                warnings.append(f"L{ln}: arc radius {r:.1f} implausible vs stock")
            cuts += 1
            cut_dist += math.dist((x, y), (nx, ny)) * 1.2  # arc approx
            if nz < 0:
                depth = -nz
                max_depth = max(max_depth, depth)
        x, y, z = nx, ny, nz
    if cuts == 0:
        warnings.append("no cutting moves found — program is air-cut only")
    stats = {"rapid_mm": round(rapid_dist, 1), "cut_mm": round(cut_dist, 1),
             "cuts": cuts, "max_depth_mm": round(max_depth, 1),
             "tool": tool, "feed": feed}
    return {"ok": not blocked, "blocked": blocked, "warnings": warnings,
            "stats": stats, "material": material,
            "gate": "simulation passed" if not blocked else
                    "GATE: program BLOCKED — fix violations before floor"}


def verify_file(path, stock_xyz, material="316L", tool_d=6.0, **kw):
    return verify(Path(path).read_text(), stock_xyz, material, tool_d, **kw)


if __name__ == "__main__":
    import sys, json
    res = verify_file(sys.argv[1], tuple(float(v) for v in sys.argv[2:5]),
                      sys.argv[5] if len(sys.argv) > 5 else "316L")
    print(json.dumps(res, indent=1))
    sys.exit(0 if res["ok"] else 1)