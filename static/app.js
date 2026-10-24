// Ferro front door — chat UI v0.3
const $ = (id) => document.getElementById(id);
let sessionId = Math.random().toString(36).slice(2, 10);
let busy = false;

function esc(t){return t.replace(/&/g,"&amp;").replace(/</g,"&lt;");}
function fmt(t){
  // minimal markdown: **bold**, `code`, newlines
  return esc(t)
    .replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>")
    .replace(/`([^`]+)`/g,"<code>$1</code>")
    .replace(/\n/g,"<br>");
}

function addMsg(role, text) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.innerHTML = fmt(text);
  $("chat").appendChild(el);
  $("chat").scrollTop = 1e9;
}

function setBusy(b) {
  busy = b;
  $("sendbtn").disabled = b;
  $("sendbtn").textContent = b ? "…" : "Send";
  $("status").innerHTML = b
    ? '<span class="dots"><i></i><i></i><i></i></span> agent drafting design'
    : "";
}

async function send() {
  if (busy) return;
  const text = $("input").value.trim();
  if (!text) return;
  $("input").value = "";
  $("chat").querySelector(".welcome")?.remove();
  addMsg("user", text);
  setBusy(true);
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
      $("preview").innerHTML = `<div class="genbanner">
          <div class="gentitle">GENERATIVE DRAFT — NOT MACHINABLE UNTIL REVIEWED</div>
          <div class="genrow">Bbox: ${esc(g.bbox||"—")} &nbsp;·&nbsp; Material: ${esc(g.material||"—")}</div>
          <div class="genbody">Agent-authored OpenSCAD below. Kernel compile + DFM + CAM review required before this touches a machine — the gate is the product.</div>
        </div>`;
      $("scad").textContent = g.scad || "";
      $("sheet").textContent = [
        "FERRO GENERATIVE DESIGN PACKET","=".repeat(46),
        "Mode: generative (agent-authored OpenSCAD)",
        `Approx bbox: ${g.bbox||"—"}`,
        `Material: ${g.material||"—"}`,
        "Mass/stock: TBD after kernel evaluation (openscad render + mesh)",
        "", "Verification status:",
        ...((g.notes&&g.notes.length)?g.notes.map(n=>`  ! ${n}`):["  static checks passed — kernel verification pending"]),
        "", "Route: ENGINEER REVIEW QUEUE -> CAD -> CAM (gated)",
      ].join("\n");
      $("packet").style.display = "block";
      setApprove("Send to engineer review");
      $("dfmbox").style.display = "none";
    }
    if (j.design) {
      $("preview").innerHTML = j.svg || "";
      $("scad").textContent = j.scad || "";
      $("sheet").textContent = j.sheet || "";
      $("packet").style.display = "block";
      if (!j.generative) setApprove("Approve for CAD");
      $("massline").textContent = j.mass_kg != null
        ? `Approx mass: ${j.mass_kg} kg (est. from parametric volume, 45% removal allowance)` : "";
    }
    if (j.dfm_notes && j.dfm_notes.length) {
      $("dfm").innerHTML = j.dfm_notes.map(n => `<li>${esc(n)}</li>`).join("");
      $("dfmbox").style.display = "block";
    } else { $("dfmbox").style.display = "none"; }
  } catch (e) { addMsg("agent", "[error] " + e + " — the agent is unreachable; try again."); }
  setBusy(false);
  $("input").focus();
}

let approved = false;
function setApprove(label) {
  const b = $("approve");
  approved = false;
  b.textContent = label;
  b.disabled = false;
  b.style.display = "";
}
$("approve").addEventListener("click", () => {
  if (approved) return;
  approved = true;
  const b = $("approve");
  const gen = $("approve").textContent.startsWith("Send");
  b.textContent = "✓ " + (gen ? "Queued for engineer review — rev locked" : "Approved for CAD — rev locked");
  b.classList.add("approved");
  b.disabled = true;
});

function newChat() {
  sessionId = Math.random().toString(36).slice(2, 10);
  $("chat").innerHTML = "";
  location.reload();
}

function startWith(text) {
  $("input").value = text;
  send();
}

$("input").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
