// Ferro front door — chat UI v0.7
const $ = (id) => document.getElementById(id);
let sessionId = Math.random().toString(36).slice(2, 10);
let busy = false;

function esc(t){return t.replace(/&/g,"&amp;").replace(/</g,"&lt;");}
function fmt(t){
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
  return el;
}

function setBusy(b) {
  busy = b;
  $("sendbtn").textContent = busy ? "…" : "Send";
  $("sendbtn").title = busy ? "agent working — your message will queue" : "";
  if (b) {
    $("status").innerHTML = '<span class="dots"><i></i><i></i><i></i></span> agent drafting design';
  }
}

// ---- message queue: Enter while the agent is working queues your message ----
const queue = [];
let attachment = null;   // {name, data_b64}

function setFileChip() {
  const c = $("filechip");
  if (attachment) { c.style.display = "block"; c.textContent = "📎 " + attachment.name + " attached — Enter to analyze (click to remove)"; }
  else c.style.display = "none";
}

function clearAttachment() { attachment = null; setFileChip(); }

function readAttachment(f) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => {
      const b64 = String(r.result).split(",")[1];
      attachment = { name: f.name, data_b64: b64 };
      setFileChip();
      res();
    };
    r.onerror = rej;
    r.readAsDataURL(f);
  });
}

$("attachbtn").onclick = () => $("fileinput").click();
$("fileinput").onchange = async () => {
  const f = $("fileinput").files[0];
  if (f) await readAttachment(f);
  $("fileinput").value = "";
};
["dragover", "dragenter"].forEach(ev => document.addEventListener(ev, e => {
  e.preventDefault(); document.body.classList.add("dragover");
}));
["dragleave", "drop"].forEach(ev => document.addEventListener(ev, e => {
  e.preventDefault(); document.body.classList.remove("dragover");
}));
document.addEventListener("drop", async e => {
  const f = e.dataTransfer?.files?.[0];
  if (f) await readAttachment(f);
});

async function send() {
  const text = $("input").value.trim();
  if (!text && !attachment) return;
  const file = attachment ? { name: attachment.name, data_b64: attachment.data_b64 } : null;
  $("input").value = "";
  attachment = null; setFileChip();
  $("chat").querySelector(".welcome")?.remove();
  if (busy) {
    queue.push({ text: text || "(see attached file)", file });
    const el = addMsg("user", (text || "📎 " + (file ? file.name : "")) + (file ? " 📎" : ""));
    el.classList.add("queued");
    el.title = "queued — sends when the agent finishes";
    $("status").innerHTML = `<span class="qtag">queued ${queue.length} — sends when the current design finishes</span>`;
    return;
  }
  await doSend(text || "Here is a file of the part I need made.", file);
}

async function doSend(text, file) {
  addMsg("user", text + (file ? " 📎" + file.name : ""));
  setBusy(true);
  try {
    const r = await fetch("/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session: sessionId, message: text, file}),
    });
    const q = await r.json();
    let j = q;
    if (q.accepted) {
      for (let i = 0; i < 200; i++) {
        await new Promise(res => setTimeout(res, 2000));
        const pr = await fetch(`/result/${sessionId}`);
        const p = await pr.json();
        if (p.status === "done" || p.status === "error") { j = p.result || {reply: "[no result]"}; break; }
        $("status").innerHTML = '<span class="dots"><i></i><i></i><i></i></span> ' +
          (i % 2 ? "agent drafting design" : "CAD kernel verifying geometry");
      }
      if (j.accepted) { throw new Error("timed out waiting for the design engine"); }
    }
    addMsg("agent", j.reply || "(no reply)");

    if (j.kernel && j.kernel.ok) {
      buildPacket(j);
    } else if (j.design) {
      buildPacket(j);
    } else if (j.generative) {
      buildGenerativeBanner(j);
    }
    if (j.dfm_notes && j.dfm_notes.length) {
      $("dfm").innerHTML = j.dfm_notes.map(n => `<li>${esc(n)}</li>`).join("");
      $("dfmbox").style.display = "block";
    } else { $("dfmbox").style.display = "none"; }
  } catch (e) { addMsg("agent", "[error] " + e + " — the agent is unreachable; try again."); }
  setBusy(false);
  // drain the queue: any messages typed mid-turn fire now, in order
  if (queue.length) {
    const next = queue.shift();
    addMsg("agent", `(queued) running: “${(next.text || "").slice(0, 60)}${(next.text||"").length > 60 ? "…" : ""}”` + (next.file ? " 📎" : ""));
    await doSend(next.text || "Here is a file of the part I need made.", next.file);
  } else {
    $("input").focus();
  }
}

// ---- design packet: dense, tabbed, grouped rows ----
const UNIT_HINT = /(mm|in)$/i;
function paramRow(k, v) {
  const label = k.replace(/_/g, " ");
  const unit = (typeof v === "number" && !UNIT_HINT.test(String(v))) ? " mm" : "";
  return `<div class="prow"><span class="pk">${esc(label)}</span><span class="pv">${esc(String(v))}${unit}</span></div>`;
}

function bust(url, rev) { return url ? url + "?v=" + (rev || Date.now()) : url; }

function buildPacket(j) {
  const k = j.kernel || {};
  const REV = k.rev;
  const d = j.design || {};
  const mode = j.generative ? "Generative" : "Parametric";
  const mat = (d.params && d.params.material) || d.material || k.material || "6061-T6";

  let rows = "";
  const src = (d.params && !Array.isArray(d.params)) ? d.params : d;
  for (const [key, val] of Object.entries(src)) {
    if (key === "family" || key === "material" || typeof val === "object") continue;
    if (["mass_kg", "volume_mm3", "area_mm2", "size_mm", "checks", "solids_n", "breps_valid", "ok", "error", "preview_url", "step_url", "stl_url"].includes(key)) continue;
    rows += paramRow(key, val);
  }
  const metrics = k.ok ? `
    <div class="prow metric"><span class="pk">volume</span><span class="pv">${k.volume_mm3} mm³</span></div>
    <div class="prow metric"><span class="pk">mass</span><span class="pv">${k.mass_kg} kg</span></div>
    <div class="prow metric"><span class="pk">bbox</span><span class="pv">${(k.size_mm||[]).join(" × ")} mm</span></div>` : "";

  const code = (d && j.scad) || (j.generative && (j.generative.cad || j.generative.scad)) || "";
  const checksTxt = (k.checks && k.checks.length)
    ? k.checks.map(c => "⚠ " + esc(c)).join("<br>")
    : "No DFM flags — single valid solid, dimensions within machining floor.";
  const kernelLine = k.ok
    ? `Kernel: build123d (OpenCASCADE) — verified ✓<br>BRep valid: ${k.breps_valid} · Solids: ${k.solids_n}<br><br>`
    : (k.error ? "Kernel verification FAILED:<br>" + esc(k.error) + "<br><br>" : "");

  $("packet").innerHTML = `
    <div class="ptabs">
      <span class="ptab active" data-t="spec">Spec</span>
      <span class="ptab" data-t="checks">Checks${(k.checks&&k.checks.length)?" ⚠":""}</span>
      ${code ? `<span class="ptab" data-t="code">Code</span>` : ""}
    </div>
    <div class="ppane" data-pane="spec">
      <div class="pmeta"><span class="pbadge">${mode}</span><span class="pbadge ghost">${esc(d.family||"part")}</span><span class="pmaterial">${esc(mat)}</span></div>
      ${j.quote ? `
      <div class="quotecard">
        <div class="qprice">$${j.quote.price} <span class="qlead">· ~${j.quote.lead_days} days</span></div>
        <div class="qdim">est. from kernel-measured geometry · ${j.quote.machine_hrs} hr machine · ${j.quote.stock_kg} kg stock</div>
        <div class="qrow">
          <a class="dlbtn ghost" href="${j.order_url}" target="_blank">⇗ order ${j.order_id}</a>
          <a class="dlbtn ghost" href="mailto:?subject=Ferro%20design%20${j.order_id}&body=${encodeURIComponent("My Ferro design packet (order " + j.order_id + "): " + location.origin + (j.order_url||""))}">✉ email my packet</a>
        </div>
      </div>` : ""}
      <div class="pscroll">${rows}${metrics}</div>
      <div class="dlrow">
        ${k.step_url ? `<a class="dlbtn" href="${bust(k.step_url, REV)}" download>⇩ STEP</a>` : ""}
        ${k.glb_url ? `<a class="dlbtn" href="${bust(k.glb_url, REV)}" download>⇩ 3D (GLB)</a>` : ""}
        ${k.drawing_dxf_url ? `<a class="dlbtn" href="${bust(k.drawing_dxf_url, REV)}" download>⇩ Drawing (DXF)</a>` : ""}
        ${k.drawing_svg_url ? `<a class="dlbtn ghost" href="${bust(k.drawing_svg_url, REV)}" download>⇩ 2D (SVG)</a>` : ""}
        ${k.stl_url ? `<a class="dlbtn ghost" href="${bust(k.stl_url, REV)}" download>⇩ STL</a>` : ""}
      </div>
      <button class="approve">Approve for CAD</button>
    </div>
    <div class="ppane" data-pane="checks" style="display:none">
      <div class="pscroll">${kernelLine}${checksTxt}</div>
    </div>
    ${code ? `<div class="ppane" data-pane="code" style="display:none"><pre class="pscroll precode">${esc(code)}</pre></div>` : ""}
  `;
  $("packet").style.display = "block";

  $("packet").onclick = (ev) => {
    const t = ev.target.closest(".ptab");
    if (t) {
      $("packet").querySelectorAll(".ptab").forEach(x => x.classList.toggle("active", x === t));
      $("packet").querySelectorAll(".ppane").forEach(p => {
        p.style.display = (p.dataset.pane === t.dataset.t) ? "" : "none";
      });
      return;
    }
    if (ev.target.classList.contains("approve")) {
      const b2 = ev.target;
      if (b2.classList.contains("approved")) return;
      b2.classList.add("approved");
      b2.textContent = "✓ Approved — rev locked";
      b2.disabled = true;
    }
  };

  if (k.preview_url) {
    if (k.glb_url && document.createElement("model-viewer").constructor !== HTMLElement) {
      $("preview3d").innerHTML = `
        <model-viewer src="${bust(k.glb_url, REV)}" camera-controls touch-action="pan-y"
          auto-rotate ar-status="not-presenting" shadow-intensity="0.6"
          exposure="0.9" environment-image="neutral"
          alt="interactive 3D preview of the part"></model-viewer>
        <div class="mvhint">drag to orbit · pinch/scroll to zoom · two-finger to pan</div>`;
    } else {
      $("preview3d").innerHTML = `<img src="${bust(k.preview_url, REV)}" alt="rendered preview" style="max-width:100%;border-radius:8px">`;
    }
  } else if (j.design && j.svg) {
    $("preview3d").innerHTML = j.svg;
  }
}

function buildGenerativeBanner(j) {
  const g = j.generative;
  $("preview").innerHTML = `<div class="genbanner">
      <div class="gentitle">GENERATIVE DRAFT — NOT MACHINABLE UNTIL REVIEWED</div>
      <div class="genrow">Bbox: ${esc(g.bbox||"—")} · Material: ${esc(g.material||"—")}</div>
      <div class="genbody">Agent-authored CAD code. Kernel compile + DFM + CAM review required before this touches a machine — the gate is the product.</div>
    </div>`;
  $("packet").style.display = "none";
}

function newChat() {
  sessionId = Math.random().toString(36).slice(2, 10);
  $("chat").innerHTML = "";
  $("packet").style.display = "none";
  $("packet").innerHTML = "";
  $("preview").innerHTML = `<div class="empty"><b>Nothing yet.</b><br>Start the conversation — a parametric preview renders here as we talk.</div>`;
  $("status").textContent = "";
}

function startWith(text) {
  $("input").value = text;
  send();
}

$("input").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });