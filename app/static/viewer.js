"use strict";
/* Phase 1: view a PDF in the browser and add highlights/comments (Google-Docs style).
   Annotations are an overlay stored in the database; the original file is never modified. */
let pdfjsLib = null;
async function loadPdfjs() {
  if (!pdfjsLib) {
    pdfjsLib = await import("/static/vendor/pdfjs/pdf.min.mjs");
    pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.min.mjs";
  }
  return pdfjsLib;
}
const V = { vid: 0, info: null, anns: [], pdf: null, pages: [], scale: 1.25, gen: 0, io: null, sel: null, draft: null, active: 0, showResolved: true };
const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5];
const when = ts => { const d = new Date(String(ts).replace(" ", "T") + "Z"); return isNaN(d) ? "" : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); };

VIEWS.view = async (el, arg) => {
  const vid = +arg; if (!vid) throw new Error("No document selected.");
  const info = await api(`/api/versions/${vid}/annotations`);
  Object.assign(V, { vid, info, anns: info.annotations, pdf: null, pages: [], sel: null, draft: null, active: 0 });
  const v = info.version, ok = v.preview.status === "ready", dl = `/api/documents/${v.document_id}/download?version_no=${v.version_no}`;
  el.innerHTML = `<div class="vbar"><button class="ib" id="vback" title="Back">←</button>
    <div class="vtitle"><b>${esc(v.title)}</b><small>Version ${v.version_no} · ${esc(v.status)}${v.preview.converted && ok ? ` · PDF preview of the .${esc(v.preview.source)} file` : ""}</small></div>
    ${ok ? `<button class="ib" id="zout" title="Zoom out">−</button><span id="zlbl" class="muted"></span><button class="ib" id="zin" title="Zoom in">＋</button><span id="pgl" class="muted"></span>` : ""}
    <a href="${dl}"><button class="sec sm">Download original</button></a>
    ${ok && info.can_view_comments ? `<a href="/api/versions/${vid}/annotated.pdf"><button class="sm">${v.preview.converted ? "Download PDF with comments" : "Download with comments"}</button></a><button class="ib" id="tcm" title="Show / hide comments">💬</button>` : ""}</div>
  <div class="vbody"><div class="vpages" id="vpages"></div>${ok && info.can_view_comments ? `<aside class="vside" id="vside"></aside>` : ""}</div>`;
  $("#vback").onclick = () => history.length > 1 ? history.back() : (location.hash = "repository");
  if (!ok) return previewState(info);
  if ($("#tcm")) $("#tcm").onclick = () => $("#vside").classList.toggle("hidden");
  if (innerWidth < 860 && $("#vside")) $("#vside").classList.add("hidden");
  $("#zin").onclick = () => zoom(1); $("#zout").onclick = () => zoom(-1);
  const box = $("#vpages");
  box.addEventListener("mouseup", () => setTimeout(onSelect, 10)); box.addEventListener("keyup", () => setTimeout(onSelect, 10));
  box.addEventListener("touchend", () => setTimeout(onSelect, 300)); box.addEventListener("click", onPageClick);
  box.addEventListener("scroll", () => requestAnimationFrame(updatePageLabel));
  renderSide();
  try {
    const lib = await loadPdfjs();
    V.pdf = await lib.getDocument({ url: `/api/versions/${vid}/view`, standardFontDataUrl: "/static/vendor/pdfjs/standard_fonts/", wasmUrl: "/static/vendor/pdfjs/wasm/" }).promise;
  } catch (e) { box.innerHTML = `<div class="vmsg card"><h3>Couldn’t open this PDF</h3><p class="muted">The file may be damaged or password-protected. You can still download it.</p></div>`; return; }
  await buildPages();
};

/* ----- Phase 2: file is being converted to a PDF preview (or can't be) ----- */
async function previewState(info) {
  const v = info.version, st = v.preview.status, box = $("#vpages"), chairUser = chair();
  const msg = (t, body, extra = "") => box.innerHTML = `<div class="vmsg card"><h3>${t}</h3><p class="muted">${body}</p>${extra}</div>`;
  if (st === "pending") {
    msg("Preparing preview…", "Converting this file to PDF so it can be viewed and commented on. This usually takes a few seconds.");
    for (let i = 0; i < 90; i++) {
      await new Promise(r => setTimeout(r, 2000));
      if (location.hash !== "#view/" + v.id) return;   // user navigated away
      const r = await api(`/api/versions/${v.id}/preview-status`).catch(() => ({ status: "pending" }));
      if (r.status !== "pending") return route();
    }
    return msg("Still working…", "The conversion is taking longer than usual.", `<button id="rr">Check again</button>`), $("#rr").onclick = () => route();
  }
  const retry = v.preview.can_retry ? `<button id="rr">Retry preview</button>` : "";
  if (st === "failed") msg("Couldn’t create a preview", "The file may be damaged or password-protected. You can still download it." + (retry ? " Retry if you think it was a temporary problem." : ""), retry);
  else if (st === "unavailable") msg("Preview isn’t available yet", chairUser ? "LibreOffice isn’t installed on the server, so Word, PowerPoint and Excel files can’t be converted. Install it (libreoffice.org), then press Retry." : "This file type can’t be previewed on this server right now. You can download it instead.", retry);
  else msg("This file can’t be previewed", "Only PDF, Word, PowerPoint, Excel, text and image files can be previewed. Download the file to open it.");
  if ($("#rr")) $("#rr").onclick = run(async () => { await api(`/api/versions/${v.id}/preview/retry`, { json: {} }); route(); });
}

async function buildPages() {
  const box = $("#vpages"); box.innerHTML = ""; V.pages = [];
  for (let n = 1; n <= V.pdf.numPages; n++) {
    const page = await V.pdf.getPage(n);
    const wrap = document.createElement("div"); wrap.className = "pw"; wrap.dataset.p = n;
    wrap.innerHTML = `<canvas></canvas><div class="hl"></div><div class="textLayer"></div>`;
    box.appendChild(wrap);
    V.pages.push({ n, page, el: wrap, canvas: wrap.children[0], hl: wrap.children[1], tl: wrap.children[2], done: false });
  }
  const avail = $("#vpages").clientWidth - 32, w1 = V.pages[0].page.getViewport({ scale: 1 }).width;
  if (avail < w1 * V.scale) V.scale = Math.max(0.4, Math.floor(avail / w1 * 100) / 100);   // fit narrow screens
  layoutPages(); drawHl(); updatePageLabel();
}
function layoutPages() {
  V.gen++; if (V.io) V.io.disconnect();
  $("#zlbl").textContent = Math.round(V.scale * 100) + "%";
  for (const pg of V.pages) {
    const vp = pg.page.getViewport({ scale: V.scale });
    pg.el.style.width = vp.width + "px"; pg.el.style.height = vp.height + "px"; pg.el.style.setProperty("--scale-factor", V.scale);
    pg.canvas.width = pg.canvas.height = 0; pg.tl.innerHTML = ""; pg.done = false;
  }
  V.io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) renderPage(V.pages[e.target.dataset.p - 1]); }), { root: $("#vpages"), rootMargin: "800px 0px" });
  V.pages.forEach(p => V.io.observe(p.el));
}
async function renderPage(pg) {
  if (pg.done) return; pg.done = true; const gen = V.gen;
  const vp = pg.page.getViewport({ scale: V.scale }), dpr = window.devicePixelRatio || 1;
  pg.canvas.width = Math.floor(vp.width * dpr); pg.canvas.height = Math.floor(vp.height * dpr);
  pg.canvas.style.width = vp.width + "px"; pg.canvas.style.height = vp.height + "px";
  try {
    await pg.page.render({ canvasContext: pg.canvas.getContext("2d"), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
    if (gen !== V.gen) return;
    await new pdfjsLib.TextLayer({ textContentSource: pg.page.streamTextContent(), container: pg.tl, viewport: vp }).render();
  } catch (e) { console.error("PDF page render failed:", e); if (gen === V.gen) pg.done = false; }
}
function zoom(dir) {
  const next = dir > 0 ? ZOOMS.find(z => z > V.scale + 0.001) : [...ZOOMS].reverse().find(z => z < V.scale - 0.001);
  if (!next) return; V.scale = next; layoutPages();
}
function updatePageLabel() {
  const box = $("#vpages"), l = $("#pgl"); if (!box || !l || !V.pages.length) return;
  const top = box.getBoundingClientRect().top; let cur = 1;
  for (const p of V.pages) { if (p.el.getBoundingClientRect().top - top < box.clientHeight / 3) cur = p.n; else break; }
  l.textContent = `Page ${cur} / ${V.pages.length}`;
}

/* ----- highlights (percent-positioned, so zoom never needs a redraw) ----- */
function drawHl() {
  for (const pg of V.pages) {
    const mk = (r, cls, id) => `<i class="hb ${cls}" ${id ? `data-a="${id}"` : ""} style="left:${r[0] * 100}%;top:${r[1] * 100}%;width:${r[2] * 100}%;height:${r[3] * 100}%"></i>`;
    pg.hl.innerHTML = V.anns.filter(a => a.page === pg.n && (V.showResolved || !a.resolved)).map(a => a.rects.map(r => mk(r, (a.resolved ? "rs " : "") + (V.active === a.id ? "on" : ""), a.id)).join("")).join("")
      + (V.draft && V.draft.page === pg.n ? V.draft.rects.map(r => mk(r, "draft")).join("") : "");
  }
}

/* ----- selecting text -> floating Comment button ----- */
const dedupe = rs => rs.filter((a, i) => !rs.some((b, j) => j !== i && b.left <= a.left + 1 && b.top <= a.top + 1 && b.right >= a.right - 1 && b.bottom >= a.bottom - 1 && (b.width * b.height > a.width * a.height || j < i)));
function fab() {
  let f = $("#fab");
  if (!f) { f = document.createElement("button"); f.id = "fab"; f.className = "fab hidden"; f.textContent = "💬 Comment"; f.onmousedown = e => e.preventDefault(); f.onclick = startDraft; document.body.appendChild(f); }
  return f;
}
function hideFab() { const f = $("#fab"); if (f) f.classList.add("hidden"); }
function onSelect() {
  if (!V.info || !V.info.can_annotate) return;
  const sel = getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount || !$("#vpages") || !$("#vpages").contains(sel.getRangeAt(0).commonAncestorContainer)) return hideFab();
  const rects = [...sel.getRangeAt(0).getClientRects()].filter(r => r.width > 1 && r.height > 1), hits = new Map();
  for (const r of rects) {
    const pg = V.pages.find(p => { const b = p.el.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2; return cx >= b.left && cx <= b.right && cy >= b.top && cy <= b.bottom; });
    if (pg) (hits.get(pg) || hits.set(pg, []).get(pg)).push(r);
  }
  if (hits.size !== 1) { hideFab(); if (hits.size > 1) toast("Select text within a single page to comment.", true); return; }
  const [[pg, rs]] = hits, b = pg.el.getBoundingClientRect(), clean = dedupe(rs), last = clean[clean.length - 1];
  V.sel = { page: pg.n, quote: sel.toString().replace(/\s+/g, " ").trim().slice(0, 1000),
    rects: clean.map(r => [(r.left - b.left) / b.width, (r.top - b.top) / b.height, r.width / b.width, r.height / b.height].map(v => Math.round(Math.min(1, Math.max(0, v)) * 1e5) / 1e5)) };
  const f = fab(); f.style.left = Math.min(innerWidth - 120, last.right) + "px"; f.style.top = last.bottom + 6 + "px"; f.classList.remove("hidden");
}
document.addEventListener("selectionchange", () => { const s = getSelection(); if (s && s.isCollapsed) hideFab(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && V.draft) { V.draft = null; drawHl(); renderSide(); } });

function startDraft() {
  if (!V.sel) return;
  V.draft = V.sel; hideFab(); if ($("#vside")) $("#vside").classList.remove("hidden"); drawHl(); renderSide();
  const t = $("#dc"); if (t) t.focus();
}
async function saveDraft() {
  const d = V.draft, a = await api(`/api/versions/${V.vid}/annotations`, { json: { page: d.page, rects: d.rects, quote: d.quote, comment: $("#dc").value } });
  V.anns.push(a); V.anns.sort((x, y) => x.page - y.page || x.rects[0][1] - y.rects[0][1]); V.draft = null; V.sel = null; getSelection().removeAllRanges();
  focusAnn(a.id); toast(a.comment ? "Comment added." : "Highlight added.");
}

/* ----- clicking a highlight / thread ----- */
function onPageClick(e) {
  const s = getSelection(); if (s && !s.isCollapsed) return;
  const pw = e.target.closest(".pw"); if (!pw) return;
  const b = pw.getBoundingClientRect(), x = (e.clientX - b.left) / b.width, y = (e.clientY - b.top) / b.height, n = +pw.dataset.p;
  const hit = V.anns.find(a => a.page === n && (V.showResolved || !a.resolved) && a.rects.some(r => x >= r[0] && x <= r[0] + r[2] && y >= r[1] && y <= r[1] + r[3]));
  if (hit) focusAnn(hit.id);
}
function focusAnn(id, scrollPage) {
  V.active = id; drawHl(); renderSide();
  const t = document.querySelector(`.th[data-t="${id}"]`); if (t) t.scrollIntoView({ block: "nearest", behavior: "smooth" });
  if (scrollPage) { const h = document.querySelector(`.hb[data-a="${id}"]`); if (h) h.scrollIntoView({ block: "center", behavior: "smooth" }); }
}

/* ----- comments panel ----- */
function renderSide() {
  const side = $("#vside"); if (!side) return;
  const list = V.anns.filter(a => V.showResolved || !a.resolved), canA = V.info.can_annotate;
  const reply = r => `<div class="rp"><b>${esc(r.author)}</b> <small>${when(r.created_at)}</small><p>${esc(r.comment)}</p>
    ${r.can_edit ? `<button class="link" data-edit="${r.id}" data-text="${esc(r.comment)}">Edit</button>` : ""}${r.can_delete ? `<button class="link" data-del="${r.id}">Delete</button>` : ""}</div>`;
  const thread = a => `<div class="th ${a.resolved ? "rs" : ""} ${V.active === a.id ? "on" : ""}" data-t="${a.id}">
    <div class="thh"><span class="av">${esc(a.author[0])}</span><b>${esc(a.author)}</b><small>${when(a.created_at)}</small></div>
    ${a.quote ? `<blockquote>${esc(a.quote)}</blockquote>` : ""}${a.comment ? `<p class="cm">${esc(a.comment)}</p>` : `<p class="muted">Highlight</p>`}
    ${a.replies.map(reply).join("")}
    ${canA ? `<button class="link" data-reply="${a.id}">Reply</button><button class="link" data-res="${a.id}" data-v="${a.resolved ? 0 : 1}">${a.resolved ? "Reopen" : "Resolve"}</button>` : ""}
    ${a.can_edit ? `<button class="link" data-edit="${a.id}" data-text="${esc(a.comment)}">Edit</button>` : ""}${a.can_delete ? `<button class="link" data-del="${a.id}">Delete</button>` : ""}</div>`;
  side.innerHTML = `<div class="vsh"><b>Comments (${V.anns.length})</b><label class="ck"><input type="checkbox" id="shres" ${V.showResolved ? "checked" : ""}> Show resolved</label></div>
    ${V.draft ? `<div class="th draft"><blockquote>${esc(V.draft.quote)}</blockquote><textarea id="dc" rows="3" maxlength="4000" placeholder="Add a comment (leave empty to just highlight)"></textarea>
      <div class="row" style="margin-top:8px"><button class="sm" id="dsave">Save</button><button class="sm sec" id="dcancel">Cancel</button></div></div>` : ""}
    ${list.length ? list.map(thread).join("") : (V.draft ? "" : `<p class="muted">${canA ? "Select text in the document to add a highlight or comment." : "No comments."}</p>`)}`;
  $("#shres").onchange = e => { V.showResolved = e.target.checked; drawHl(); renderSide(); };
  if ($("#dsave")) { $("#dsave").onclick = run(saveDraft); $("#dcancel").onclick = () => { V.draft = null; drawHl(); renderSide(); }; }
  const find = id => { for (const a of V.anns) { if (a.id == id) return [a, V.anns]; const r = a.replies.find(x => x.id == id); if (r) return [r, a.replies]; } };
  side.querySelectorAll(".th[data-t]").forEach(t => t.onclick = e => { if (!e.target.closest("button")) focusAnn(+t.dataset.t, true); });
  side.querySelectorAll("[data-reply]").forEach(b => b.onclick = () => modal(`<h3>Reply</h3><label>Your reply<textarea name="comment" required maxlength="4000"></textarea></label>`,
    async f => { const r = await api(`/api/annotations/${b.dataset.reply}/replies`, { json: f }); find(b.dataset.reply)[0].replies.push(r); renderSide(); }));
  side.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => modal(`<h3>Edit comment</h3><label>Comment<textarea name="comment" maxlength="4000">${esc(b.dataset.text)}</textarea></label>`,
    async f => { const r = await api(`/api/annotations/${b.dataset.edit}`, { method: "PATCH", json: f }); find(b.dataset.edit)[0].comment = r.comment; renderSide(); }));
  side.querySelectorAll("[data-res]").forEach(b => b.onclick = run(async () => { await api(`/api/annotations/${b.dataset.res}/resolve`, { json: { resolved: b.dataset.v === "1" } }); find(b.dataset.res)[0].resolved = b.dataset.v === "1"; drawHl(); renderSide(); }));
  side.querySelectorAll("[data-del]").forEach(b => b.onclick = run(async () => {
    if (!confirm("Delete this comment? This can't be undone.")) return;
    await api(`/api/annotations/${b.dataset.del}`, { method: "DELETE" });
    const top = V.anns.findIndex(a => a.id == b.dataset.del);
    if (top >= 0) V.anns.splice(top, 1); else V.anns.forEach(a => a.replies = a.replies.filter(r => r.id != b.dataset.del));
    drawHl(); renderSide(); toast("Deleted.");
  }));
}
