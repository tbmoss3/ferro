// Ferro front door — chat UI
const $ = (id) => document.getElementById(id);
let sessionId = Math.random().toString(36).slice(2, 10);
let busy = false;

function addMsg(role, text) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.innerHTML = text.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/\n/g,"<br>");
  $("chat").appendChild(el);
  $("chat").scrollTop = 1e9;
}

async function send() {
  if (busy) return;
  const text = $("input").value.trim();
  if (!text) return;
  $("input").value = "";
  addMsg("user", text);
  busy = true;
  $("status").textContent = "agent working…";
  try {
    const r = await fetch("/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session: sessionId, message: text}),
    });
    const j = await r.json();
    addMsg("agent", j.reply || "(no reply)");
    if (j.generative) {
      const g = j.generative;
      $("preview").innerHTML = `<div style="text-align:left;font-size:13px;line-height:1.6;padding:10px 4px">
        <div style="color:#e07b39;font-weight:700;letter-spacing:1px">GENERATIVE DRAFT — NOT MACHINABLE UNTIL REVIEWED</div>
        <div style="margin-top:6px">Bbox: ${g.bbox||"—"} · Material: ${g.material||"—"}</div>
        <div style="color:#8b93a1;margin-top:6px">Agent-authored OpenSCAD below. Kernel compile + DFM + CAM review required before this touches a machine — that gate is the product.</div>
      </div>`;
      $("scad").textContent = g.scad || "";
      $("sheet").textContent = [
        "FERRO GENERATIVE DESIGN PACKET","=".repeat(46),
        "Mode: generative (agent-authored OpenSCAD)",
        `Approx bbox: ${g.bbox||"—"}`,
        `Material: ${g.material||"—"}`,
        "Mass/stock: TBD after kernel evaluation (openscad render + mesh)",
        "", "Verification status:",
        ...(g.notes&&g.notes.length?g.notes.map(n=>`  ⚠ ${n}`):["  static checks passed — kernel verification pending"]),
        "", "Route: ENGINEER REVIEW QUEUE → CAD → CAM (gated)",
      ].join("\n");
      $("packet").style.display = "block";
      $("approve").textContent = "Send to engineer review";
      $("dfmbox").style.display = "none";
    } else {
      $("approve").textContent = "Approve for CAD";
    }
    if (j.design) {
      $("preview").innerHTML = j.svg || "";
      $("scad").textContent = j.scad || "";
      $("sheet").textContent = j.sheet || "";
      $("packet").style.display = "block";
    }
    if (j.dfm_notes && j.dfm_notes.length) {
      $("dfm").innerHTML = j.dfm_notes.map(n => `<li>${n}</li>`).join("");
      $("dfmbox").style.display = "block";
    } else { $("dfmbox").style.display = "none"; }
  } catch (e) { addMsg("agent", "⚠ error: " + e); }
  $("status").textContent = "";
  busy = false;
}
$("input").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

async function loadDemo(fam) {
  const presets = {
    bracket: "I need an L-bracket to mount a 3HP motor to a steel frame, 6061, 4 mounting holes",
    plate: "Make me a base plate 200mm x 120mm, 12mm thick 6061, 6 holes",
    enclosure: "An electronics enclosure for a Raspberry Pi stack, 6061, with a front USB port cutout",
  };
  $("input").value = presets[fam];
  send();
}