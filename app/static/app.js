"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let me = null, csrf = "", meta = null;

async function api(url, o = {}) {
  o.headers = { ...(o.headers || {}), "X-CSRF-Token": csrf };
  if (o.json) { o.method = o.method || "POST"; o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(url, o);
  if (r.status === 401 && me) { me = null; showLogin(); throw new Error("Session ended. Please sign in again."); }
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : r;
  if (!r.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}
function toast(msg, bad) { const t = $("#toast"); t.textContent = msg; t.style.background = bad ? "var(--bad)" : ""; t.classList.remove("hidden"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add("hidden"), 3500); }
const run = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };
const statusCls = s => ({ "Current Approved Version": "b-ok", "Completed": "b-ok", "Verified": "b-ok", "Pending Verification": "b-warn", "Expired": "b-bad", "Pending Review": "b-warn", "In Progress": "b-warn", "Requires Revision": "b-bad", "Missing Evidence": "b-bad" }[s] || "");
const badge = s => `<span class="badge ${statusCls(s)}">${esc(s || "—")}</span>`;
const opts = (rows, val, lbl, all) => (all ? `<option value="">${all}</option>` : "") + rows.map(r => `<option value="${esc(r[val])}">${esc(typeof lbl === "function" ? lbl(r) : r[lbl])}</option>`).join("");
const table = (heads, rows) => `<div class="tablewrap"><table><thead><tr>${heads.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("") || `<tr><td colspan="${heads.length}" class="muted">Nothing to show.</td></tr>`}</tbody></table></div>`;
function modal(html, onSubmit) {
  const d = $("#dlg"); d.innerHTML = `<form method="dialog">${html}<div class="row" style="margin-top:12px"><button class="sec" value="x" formnovalidate>Close</button>${onSubmit ? "<button value='ok'>Submit</button>" : ""}</div></form>`;
  d.onclose = null; d.showModal();
  if (onSubmit) d.querySelector("form").onsubmit = e => { e.preventDefault(); if (e.submitter?.value === "x") return d.close(); run(async () => { await onSubmit(Object.fromEntries(new FormData(e.target))); d.close(); })(); };
  return d;
}

/* ---------- login (3.1 #1) ---------- */
function setPasswordVisibility(visible) {
  $("#login-password").type = visible ? "text" : "password";
  $("#password-toggle").setAttribute("aria-label", visible ? "Hide password" : "Show password");
  $("#password-toggle").setAttribute("aria-pressed", String(visible));
  $(".password-toggle-slash").hidden = visible;
}
$("#password-toggle").onclick = () => setPasswordVisibility($("#login-password").type === "password");
function showLogin() { setPasswordVisibility(false); $("#shell").classList.add("hidden"); $("#login").classList.remove("hidden"); $("#f-cred").classList.remove("hidden"); $("#f-mfa").classList.add("hidden"); $("#login-err").textContent = ""; }
const lerr = m => $("#login-err").textContent = m;
$("#f-cred").onsubmit = async e => {
  e.preventDefault(); lerr("");
  const r = await fetch("/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(e.target))) });
  const d = await r.json();
  if (!r.ok) return lerr(d.error);
  $("#f-cred").classList.add("hidden"); $("#f-mfa").classList.remove("hidden"); $("#f-mfa [name=code]").focus();
};
$("#f-mfa").onsubmit = async e => {
  e.preventDefault(); lerr("");
  const r = await fetch("/login/verify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(e.target))) });
  const d = await r.json();
  if (!r.ok) return lerr(d.error);
  e.target.reset(); $("#f-cred").reset(); start();
};
$("#mfa-back").onclick = showLogin;
$("#logout").onclick = run(async () => { await api("/logout", { method: "POST" }); me = null; showLogin(); });

/* ---------- shell / routing ---------- */
const SCREENS = {
  home: ["Home", ["dept_chair", "faculty"]],
  repository: ["Evidence Repository", ["dept_chair", "faculty", "external_evaluator"]],
  upload: ["Upload", ["dept_chair", "faculty"]],
  approvals: ["Approvals", ["dept_chair", "faculty"]],
  requirements: ["Requirements", ["dept_chair", "faculty"]],
  dashboard: ["Compliance Dashboard", ["dept_chair", "faculty"]],
  faculty: ["Faculty/Teaching Information", ["dept_chair", "faculty"]],
  certifications: ["Certification Repository", ["dept_chair", "faculty"]],
  profile: ["Profile", ["dept_chair", "faculty", "external_evaluator"]],
  access: ["Access Requests", ["external_evaluator"]],
  admin: ["Administration", ["dept_chair"]],
};
const ICONS = {
  home: "home",
  repository: "evidence-repository",
  certifications: "certification-repository",
  upload: "upload",
  approvals: "approvals",
  requirements: "requirements",
  faculty: "faculty-information",
  dashboard: "compliance-dashboard",
  profile: "faculty-information",
  access: "evidence-repository",
  admin: "administration",
};
$("#burger").onclick = () => document.body.classList.toggle("navopen");
$("#avatar").onclick = e => { e.stopPropagation(); $("#menu").classList.toggle("hidden"); };
document.addEventListener("click", () => $("#menu").classList.add("hidden"));
async function start() {
  const m = await (await fetch("/me")).json(); me = m; csrf = m.csrf;
  meta = me.role === "external_evaluator" ? { categories: [], courses: [] } : await api("/api/documents/meta");
  $("#login").classList.add("hidden"); $("#shell").classList.remove("hidden");
  $("#who").textContent = `${me.name} · ${me.role.replace("_", " ")}`;
  $("#nav").innerHTML = Object.entries(SCREENS).filter(([, v]) => v[1].includes(me.role)).map(([k, v]) => `<a data-k="${k}" href="#${k}"><span class="ic"><svg aria-hidden="true" focusable="false"><use href="/static/icons/sprite.svg#icon-${ICONS[k]}"></use></svg></span>${v[0]}</a>`).join("");
  $("#avatar").textContent = (me.name || "?")[0].toUpperCase(); $("#avatar").className = "avatar"; $("#newbtn").classList.toggle("hidden", !["dept_chair", "faculty"].includes(me.role));
  route();
}
async function route() {
  if (!me) return;
  document.body.classList.remove("navopen");
  let k = location.hash.slice(1); if (!SCREENS[k] || !SCREENS[k][1].includes(me.role)) k = me.role === "external_evaluator" ? "repository" : "home";
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.k === k));
  $("#main").innerHTML = `<p class="muted">Loading…</p>`;
  try { await VIEWS[k]($("#main")); } catch (e) { $("#main").innerHTML = `<div class="panel err">${esc(e.message)}</div>`; }
}
window.onhashchange = route;

/* ---------- views ---------- */
const VIEWS = {};
const chair = () => me.role === "dept_chair";

VIEWS.home = async el => {   // 3.1 #2
  const [d, rr] = await Promise.all([api("/api/dashboard"), api("/api/revision-requests")]);
  el.innerHTML = `<h2>Welcome, ${esc(me.name)}</h2>
  <div class="grid"><div class="card stat"><b>${d.documents}</b>Documents</div><div class="card stat"><b>${d.completion_pct}%</b>Requirements completed</div>
  <div class="card stat"><b>${d.review_queue}</b>Awaiting review</div><div class="card stat"><b>${rr.length}</b>Updates needing attention</div></div>
  <div class="panel"><h3>Updates needing your attention</h3>${rr.length ? table(["Document", "Message"], rr.map(r => `<tr><td><a href="#repository" data-attention-doc="${r.document_id}">${esc(r.document_title)}</a></td><td>${esc(r.message)}</td></tr>`)) : "<p class='muted'>No pending correction requests.</p>"}</div>
  <div class="panel"><h3>Quick links</h3><div class="row"><button onclick="location.hash='repository'">Search documents</button><button onclick="location.hash='upload'">Upload a document</button><button onclick="location.hash='requirements'">View requirements</button></div></div>`;
  el.querySelectorAll("[data-attention-doc]").forEach(link => link.onclick = e => {
    e.preventDefault();
    searchQ = "";
    repositoryFocusDocId = Number(link.dataset.attentionDoc);
    if (location.hash === "#repository") route();
    else location.hash = "repository";
  });
};

let viewMode = "list", searchQ = "", repositoryFocusDocId = null;
const hue = t => [...(t || "?")].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
const fileIcon = t => `<span class="fi" style="--h:${hue(t)}">${esc((t || "?")[0].toUpperCase())}</span>`;
async function docsTable(el, target) {
  const f = new URLSearchParams(new FormData($("#flt", el)));
  [...f.keys()].forEach(k => !f.get(k) && f.delete(k));
  const rows = await api("/api/documents?" + f);
  const focusDocId = repositoryFocusDocId;
  if (focusDocId !== null) {
    rows.sort((a, b) => Number(b.id === focusDocId) - Number(a.id === focusDocId));
    repositoryFocusDocId = null;
    el.scrollTop = 0;
  }
  const acts = d => `<button class="sm" type="button" data-dl="${d.id}">Download</button>${d.can_update ? `<button class="sm sec" type="button" data-update="${d.id}">Replace file</button>` : ""}${chair() ? `<button class="sm" type="button" data-r="${d.id}">Request update</button>` : ""}`;
  target.className = "files" + (viewMode === "grid" ? " grid" : "");
  target.innerHTML = !rows.length ? `<p class="muted empty">No documents match. Try clearing a filter.</p>` : viewMode === "grid"
    ? rows.map(d => `<div class="fcard" data-v="${d.id}" data-updateable="${d.can_update}"><div class="thumb">${fileIcon(d.document_type)}</div><div class="fmeta"><b>${esc(d.title)}</b><small>${esc(d.category)} · ${esc(d.school_year)}</small>${badge(d.latest_status)}</div></div>`).join("")
    : `<div class="frow fhead"><span></span><span>Name</span><span>Category</span><span>Course</span><span>Year / Sem</span><span>Status</span><span></span></div>` +
      rows.map(d => `<div class="frow" data-v="${d.id}">${fileIcon(d.document_type)}<span class="fname">${esc(d.title)}<small>${esc(d.document_type)} · v${d.latest_version}</small></span><span>${esc(d.category)}</span><span>${esc(d.course || "—")}</span><span>${esc(d.school_year)} · ${esc(d.semester)}</span><span>${badge(d.latest_status)}</span><span class="acts">${acts(d)}</span></div>`).join("");
  target.querySelectorAll("[data-v]").forEach(r => r.onclick = run(() => versionsDialog(
    +r.dataset.v, () => docsTable(el, target), r.dataset.updateable === "true"
  )));
  target.querySelectorAll("[data-dl]").forEach(b => b.onclick = e => { e.stopPropagation(); location.href = `/api/documents/${b.dataset.dl}/download`; });
  target.querySelectorAll("[data-update]").forEach(b => b.onclick = run(async e => {
    e.stopPropagation();
    await versionsDialog(+b.dataset.update, () => docsTable(el, target), true);
  }));
  target.querySelectorAll("[data-r]").forEach(b => b.onclick = e => { e.stopPropagation(); modal(`<h3>Request update / correction</h3><label>Message to document owner<textarea name="message" required></textarea></label>`,
    async v => { await api(`/api/documents/${b.dataset.r}/revision-requests`, { json: v }); toast("Request sent."); }); });
}
VIEWS.repository = async el => {   // 3.1 #3
  const sel = (n, label, inner) => `<select name="${n}" class="chip"><option value="">${label}</option>${inner}</select>`;
  el.innerHTML = `<div class="bar2"><h2>Evidence Repository</h2><div class="seg"><button data-m="list" class="${viewMode === "list" ? "on" : ""}" title="List view">☰</button><button data-m="grid" class="${viewMode === "grid" ? "on" : ""}" title="Grid view">▦</button></div></div>
  ${me.role === "external_evaluator" ? `<p class="muted">Read-only view: only documents approved for your access are listed.</p>` : ""}
  <form id="repository-search" class="search repository-search"><input id="gsearch" value="${esc(searchQ)}" placeholder="Search in repository" aria-label="Search documents"><button type="submit">Search</button></form>
  <form id="flt" class="chips"><input type="hidden" name="q" value="${esc(searchQ)}">
    ${sel("category_id", "Category", opts(meta.categories, "id", "name"))}${sel("course_id", "Course", opts(meta.courses, "id", "code"))}
    <input name="school_year" class="chip" placeholder="School year" size="10">${sel("semester", "Semester", "<option>1st</option><option>2nd</option><option>Summer</option>")}
    <input name="document_type" class="chip" placeholder="Type" size="9">${sel("approval_status", "Status", "<option>Pending Review</option><option>Current Approved Version</option><option>Requires Revision</option>")}
    <button type="button" class="link" id="clr">Clear filters</button></form>
  ${searchQ ? `<p class="muted">Results for “${esc(searchQ)}”</p>` : ""}<div id="docs"></div>`;
  const go = run(() => docsTable(el, $("#docs")));
  const fl = $("#flt", el); fl.onchange = go; fl.onsubmit = e => { e.preventDefault(); go(); };
  $("#repository-search", el).onsubmit = e => { e.preventDefault(); searchQ = $("#gsearch", el).value.trim(); route(); };
  $("#clr", el).onclick = () => { searchQ = ""; route(); };
  el.querySelectorAll("[data-m]").forEach(b => b.onclick = () => { viewMode = b.dataset.m; route(); });
  await go();
};

async function versionsDialog(id, after, canUpdate) {   // 3.1 #4 version history
  const v = await api(`/api/documents/${id}/versions`);
  const d = modal(`<h3>Version history</h3>${table(["Ver", "File", "Status", "Uploaded", "Remarks", ""], v.map(x => `<tr><td>${x.version_no}</td><td>${esc(x.original_name)}</td><td>${badge(x.status)}</td><td>${esc(x.uploaded_at)}</td><td>${esc(x.remarks || "")}</td><td>${x.file_available ? `<a class="button-link" href="/api/documents/${id}/download?version_no=${x.version_no}">Download</a>` : `<span class="muted">File no longer retained</span>`}</td></tr>`))}
    ${canUpdate ? `<h3>Replace uploaded file</h3><p class="muted">The replacement is submitted as Pending Review. The current approved version, if any, remains active until approval.</p><input type="file" name="file" id="nv">` : ""}`);
  if ($("#nv")) { const b = document.createElement("button"); b.type = "button"; b.textContent = "Submit replacement for review"; b.onclick = run(async () => {
    const fd = new FormData(); fd.append("file", $("#nv").files[0] || ""); await api(`/api/documents/${id}/versions`, { method: "POST", body: fd }); toast("Uploaded — pending review."); d.close(); after?.(); }); $("#nv").after(b); }
}
/* ---------- Certification Repository (UI preview, sample data only) ---------- */
const CERTS = [
  { title: "Licensure Examination Certificate", faculty: "Dr. Maria Santos", type: "License", issuer: "Professional Regulation Commission", issued: "2019-03-14", status: "Verified" },
  { title: "Certified Information Systems Auditor (CISA)", faculty: "Prof. Juan Dela Cruz", type: "Professional Certification", issuer: "ISACA", issued: "2022-06-20", status: "Verified" },
  { title: "Outstanding Faculty Research Award", faculty: "Dr. Maria Santos", type: "Award", issuer: "University of Santo Tomas", issued: "2024-02-09", status: "Verified" },
  { title: "AWS Certified Solutions Architect – Associate", faculty: "Engr. Ana Reyes", type: "Professional Certification", issuer: "Amazon Web Services", issued: "2023-09-05", status: "Pending Verification" },
  { title: "Best Paper Award, International Conference on IS", faculty: "Prof. Paolo Garcia", type: "Award", issuer: "ICIS Organizing Committee", issued: "2025-11-18", status: "Pending Verification" },
  { title: "Teaching Excellence Seminar Completion", faculty: "Dr. Liza Mendoza", type: "Training Certificate", issuer: "UST Center for Teaching Excellence", issued: "2021-07-30", status: "Verified" },
  { title: "Project Management Professional (PMP)", faculty: "Prof. Juan Dela Cruz", type: "Professional Certification", issuer: "Project Management Institute", issued: "2020-01-15", status: "Expired" },
  { title: "Data Privacy Compliance Training", faculty: "Engr. Ana Reyes", type: "Training Certificate", issuer: "National Privacy Commission", issued: "2025-04-22", status: "Verified" },
];
let certMode = "list";
VIEWS.certifications = async el => {
  const fmt = d => new Date(d + "T00:00:00").toLocaleDateString("en-PH", { year: "numeric", month: "short", day: "numeric" });
  const uniq = k => [...new Set(CERTS.map(c => c[k]))].sort();
  const years = [...new Set(CERTS.map(c => c.issued.slice(0, 4)))].sort().reverse();
  const sel = (n, label, vals) => `<select name="${n}" class="chip" aria-label="Filter by ${label}"><option value="">${label}</option>${vals.map(v => `<option>${esc(v)}</option>`).join("")}</select>`;
  el.innerHTML = `<div class="bar2"><h2>Certification Repository</h2>
    <div class="row-tools"><button type="button" data-soon>＋ Upload certificate</button>
    <div class="seg" role="group" aria-label="Change view"><button type="button" data-m="list" aria-pressed="${certMode === "list"}" class="${certMode === "list" ? "on" : ""}" title="List view" aria-label="List view">☰</button><button type="button" data-m="grid" aria-pressed="${certMode === "grid"}" class="${certMode === "grid" ? "on" : ""}" title="Grid view" aria-label="Grid view">▦</button></div></div></div>
  <p class="muted">A compiled record of faculty official certifications, licenses, trainings, and awards.</p>
  <form id="cert-search" class="search repository-search" role="search"><label for="cq" class="sr-only">Search certificates</label>
    <input id="cq" placeholder="Search by title, faculty, or issuer" autocomplete="off"><button type="submit">Search</button></form>
  <form id="cflt" class="chips" aria-label="Filter certificates">
    ${sel("faculty", "Faculty", uniq("faculty"))}${sel("type", "Type", uniq("type"))}${sel("year", "Year issued", years)}${sel("status", "Status", ["Verified", "Pending Verification", "Expired"])}
    <button type="reset" class="link" id="cclr">Clear filters</button></form>
  <p id="ccount" class="muted" role="status" aria-live="polite"></p><div id="cdocs"></div>`;

  const soon = root => root.querySelectorAll("[data-soon]").forEach(b => b.onclick = () => toast("This action is not available yet."));
  const draw = () => {
    const f = Object.fromEntries(new FormData($("#cflt", el))), q = $("#cq", el).value.trim().toLowerCase();
    const rows = CERTS.filter(c => (!q || [c.title, c.faculty, c.issuer, c.type].join(" ").toLowerCase().includes(q))
      && (!f.faculty || c.faculty === f.faculty) && (!f.type || c.type === f.type) && (!f.status || c.status === f.status) && (!f.year || c.issued.startsWith(f.year)));
    $("#ccount", el).textContent = `${rows.length} of ${CERTS.length} certificates shown`;
    const icon = c => `<span class="fi" aria-hidden="true" style="--h:${hue(c.type)}">${esc(c.type[0].toUpperCase())}</span>`;
    const acts = c => `<button class="sm" type="button" data-soon aria-label="Download ${esc(c.title)}">Download</button>${c.status === "Pending Verification" && chair() ? `<button class="sm sec" type="button" data-soon aria-label="Verify ${esc(c.title)}">Verify</button>` : `<button class="sm sec" type="button" data-soon aria-label="View details of ${esc(c.title)}">Details</button>`}`;
    const t = $("#cdocs", el);
    t.className = "files certs" + (certMode === "grid" ? " grid" : "");
    t.innerHTML = !rows.length ? `<p class="muted empty">No certificates match. Try clearing a filter.</p>`
      : certMode === "grid"
        ? rows.map(c => `<article class="fcard"><div class="thumb">${icon(c)}</div><div class="fmeta"><b>${esc(c.title)}</b><small>${esc(c.faculty)}</small><small>${esc(c.type)} · ${fmt(c.issued)}</small>${badge(c.status)}<div class="acts cardacts">${acts(c)}</div></div></article>`).join("")
        : `<div role="table" aria-label="Faculty certifications and awards"><div role="row" class="frow fhead"><span role="columnheader" aria-label="Type icon"></span><span role="columnheader">Certificate</span><span role="columnheader">Faculty</span><span role="columnheader">Type</span><span role="columnheader">Date issued</span><span role="columnheader">Status</span><span role="columnheader" aria-label="Actions"></span></div>` +
          rows.map(c => `<div role="row" class="frow">${icon(c)}<span role="cell" class="fname">${esc(c.title)}<small>${esc(c.issuer)}</small></span><span role="cell">${esc(c.faculty)}</span><span role="cell">${esc(c.type)}</span><span role="cell">${fmt(c.issued)}</span><span role="cell">${badge(c.status)}</span><span role="cell" class="acts">${acts(c)}</span></div>`).join("") + `</div>`;
    soon(t);
  };
  soon(el);
  $("#cert-search", el).onsubmit = e => { e.preventDefault(); draw(); };
  $("#cq", el).oninput = draw;
  $("#cflt", el).onchange = draw; $("#cflt", el).onsubmit = e => e.preventDefault();
  $("#cflt", el).onreset = () => { $("#cq", el).value = ""; setTimeout(draw); };
  el.querySelectorAll("[data-m]").forEach(b => b.onclick = () => { certMode = b.dataset.m; route(); });
  draw();
};

VIEWS.upload = async el => {
  el.innerHTML = `<h2>Upload Document</h2><form id="up" class="panel"><div class="row">
    <label>Title*<input name="title" required></label><label>Document type*<select name="document_type" required><option value="" disabled selected>Select document type</option><option value="syllabus">Syllabus</option><option value="exam">Exam</option><option value="credential">Credential</option></select></label></div>
    <label>Description<input name="description"></label><div class="row">
    <label>Category*<select name="category_id" required>${opts(meta.categories, "id", c => (c.parent_id ? "— " : "") + c.name)}</select></label>
    <label>Course<select name="course_id">${opts(meta.courses, "id", "code", "None")}</select></label>
    <label>School year*<input name="school_year" required pattern="\\d{4}-\\d{4}" placeholder="2026-2027"></label>
    <label>Semester*<select name="semester"><option>1st</option><option>2nd</option><option>Summer</option></select></label></div>
    <label>File* (pdf, docx, xlsx, pptx, images; max 25 MB)<input type="file" name="file" required></label>
    <button>Upload for review</button></form>
    <p class="muted">Uploads matching an existing document’s title, category, course, school year, semester, and type are added as a new version for review. Otherwise, a new repository entry is created. Version history is retained; files are kept only for the two latest approved versions, latest pending upload, and latest rejected upload.</p>`;
  $("#up").onsubmit = run(async e => {
    e.preventDefault();
    const r = await api("/api/documents", { method: "POST", body: new FormData(e.target) });
    toast(r.matched_existing
      ? `Uploaded as version ${r.version_no} of document #${r.document_id} for review.`
      : `Uploaded as a new document (#${r.document_id}) for review.`);
    e.target.reset();
  });
};

VIEWS.approvals = async el => {   // 3.1 #5
  const sigs = await api("/api/my-signatures");
  let html = `<h2>Approval and Signatories</h2>`;
  if (sigs.length) html += `<div class="panel"><h3>Waiting for your signature</h3>${table(["Document", "Version", ""], sigs.map(s => `<tr><td>${esc(s.title)}</td><td>v${s.version_no}</td><td><button class="sm" data-sign="${s.version_id}">Sign</button></td></tr>`))}</div>`;
  if (chair()) {
    const q = await api("/api/review-queue");
    html += `<div class="panel"><h3>Review queue</h3>${table(["Document", "Ver", "Uploader", "Uploaded", "Signatures", "Actions"], q.map(r => `<tr><td>${esc(r.title)}</td><td>${r.version_no}</td><td>${esc(r.uploader)}</td><td>${esc(r.uploaded_at)}</td><td>${r.sig_done}/${r.sig_total}</td>
      <td>${r.file_available ? `<a href="/api/documents/${r.document_id}/download?version_no=${r.version_no}"><button class="sm sec">Open</button></a> <button class="sm" data-ok="${r.version_id}">Approve</button> <button class="sm bad" data-no="${r.version_id}">Reject</button> <button class="sm sec" data-sg="${r.version_id}">+ Signatory</button>` : `<span class="muted">File no longer available</span>`}</td></tr>`))}</div>`;
  } else if (!sigs.length) html += `<div class="panel muted">Your submissions are reviewed by the Department Chair. Check each document’s status in the Repository.</div>`;
  el.innerHTML = html;
  el.querySelectorAll("[data-sign]").forEach(b => b.onclick = run(async () => { await api(`/api/versions/${b.dataset.sign}/sign`, { method: "POST" }); toast("Signed."); route(); }));
  el.querySelectorAll("[data-ok]").forEach(b => b.onclick = run(async () => { await api(`/api/versions/${b.dataset.ok}/approve`, { method: "POST" }); toast("Approved — now the current version."); route(); }));
  el.querySelectorAll("[data-no]").forEach(b => b.onclick = () => modal(`<h3>Reject document</h3><label>Revision remarks (required)<textarea name="remarks" required></textarea></label>`,
    async v => { await api(`/api/versions/${b.dataset.no}/reject`, { json: v }); toast("Returned to uploader."); route(); }));
  el.querySelectorAll("[data-sg]").forEach(b => b.onclick = run(async () => { const us = (await api("/api/admin/users")).filter(u => u.is_active && u.role !== "external_evaluator");
    modal(`<h3>Add required signatory</h3><label>Signer<select name="signer_id">${opts(us, "id", "full_name")}</select></label>`, async v => { await api(`/api/versions/${b.dataset.sg}/signatories`, { json: v }); toast("Signatory added."); route(); }); }));
};

VIEWS.requirements = async el => {   // 3.1 #6
  const rows = await api("/api/requirements");
  el.innerHTML = `<h2>Accreditation Requirement Tracking</h2><div class="panel">${table(["Body", "Code", "Requirement", "Status", "Evidence", ""], rows.map(r => `<tr><td>${esc(r.body)}</td><td>${esc(r.code)}</td><td>${esc(r.description)}</td><td>${badge(r.status)}</td><td>${r.evidence_count}</td><td><button class="sm sec" data-e="${r.id}">Manage evidence</button> <button class="sm sec" data-l="${r.id}">Link evidence</button></td></tr>`))}</div>
  ${chair() ? `<form id="nr" class="panel"><h3>Add requirement</h3><div class="row"><label>Accrediting body<input name="body" required placeholder="PAASCU"></label><label>Code<input name="code" required></label><label>Description<input name="description" required></label><button>Add</button></div></form>` : ""}`;
  el.querySelectorAll("[data-e]").forEach(b => b.onclick = run(async () => {
    const rid = b.dataset.e;
    const linked = await api(`/api/requirements/${rid}/evidence`);
    const options = await api(`/api/requirements/${rid}/evidence/options`);
    const linkedIds = new Set(linked.map(d => d.id));
    const available = options.filter(d => !linkedIds.has(d.id));
    const dialog = modal(`<h3>Manage linked evidence</h3>${linked.length
      ? table(["Document", "Replace with", "Actions"], linked.map(d => `<tr data-evidence-row="${d.id}"><td>${esc(d.title)}</td><td><select aria-label="Replacement document"><option value="">Select document</option>${opts(available, "id", x => `${x.title} (v${x.latest_version})`)}</select></td><td><button class="sm" type="button" data-replace="${d.id}">Replace</button> <button class="sm bad" type="button" data-unlink="${d.id}">Remove</button></td></tr>`))
      : "<p class='muted'>No linked documents available to manage.</p>"}`);
    dialog.querySelectorAll("[data-replace]").forEach(button => button.onclick = run(async () => {
      const documentId = $("select", button.closest("tr")).value;
      if (!documentId) return toast("Select a replacement document.", true);
      await api(`/api/requirements/${rid}/evidence/${button.dataset.replace}`, {
        method: "PUT", json: { document_id: documentId },
      });
      dialog.close(); toast("Evidence replaced."); route();
    }));
    dialog.querySelectorAll("[data-unlink]").forEach(button => button.onclick = run(async () => {
      if (!confirm("Remove this document from the requirement?")) return;
      await api(`/api/requirements/${rid}/evidence/${button.dataset.unlink}`, { method: "DELETE" });
      dialog.close(); toast("Evidence removed."); route();
    }));
  }));
  el.querySelectorAll("[data-l]").forEach(b => b.onclick = run(async () => {
    const docs = await api(`/api/requirements/${b.dataset.l}/evidence/options`);
    modal(`<h3>Link evidence document</h3><label>Document<select name="document_id">${opts(docs, "id", d => `${d.title} (v${d.latest_version})`)}</select></label>`,
      async v => { await api(`/api/requirements/${b.dataset.l}/evidence`, { json: v }); toast("Evidence linked."); route(); });
  }));
  if ($("#nr", el)) $("#nr", el).onsubmit = run(async e => { e.preventDefault(); await api("/api/requirements", { json: Object.fromEntries(new FormData(e.target)) }); toast("Requirement added."); route(); });
};

const facultyPhoto = (profile, id) => profile.has_photo
  ? `<img class="faculty-photo" src="/api/faculty/${id}/photo" alt="2×2 faculty photo">`
  : `<div class="faculty-photo faculty-photo-empty" role="img" aria-label="No faculty photo uploaded">2×2 photo</div>`;
const facultySummary = d => {
  const p = d.profile, courseNames = d.courses.map(c => `${c.code} — ${c.title}`);
  return `<section class="panel faculty-summary"><h3>Faculty/Teaching Information</h3><div class="faculty-summary-body">${facultyPhoto(p, p.id)}<dl>
    <dt>Full name</dt><dd>${esc(p.full_name)}</dd><dt>Address</dt><dd>${esc(p.address || "—")}</dd>
    <dt>Contact number</dt><dd>${esc(p.contact_number || "—")}</dd><dt>Facebook name</dt><dd>${esc(p.facebook_name || "—")}</dd>
    <dt>UST email</dt><dd>${esc(p.ust_email || "—")}</dd><dt>Faculty status</dt><dd>${esc(p.faculty_status)}</dd>
    <dt>Date started/appointed</dt><dd>${esc(p.date_started || "—")}</dd><dt>Courses handled</dt><dd>${esc(courseNames.join(", ") || "—")}</dd>
    <dt>Highest degree</dt><dd>${esc(p.highest_degree || "—")}</dd><dt>Specialization</dt><dd>${esc(p.specialization || "—")}</dd>
    </dl></div></section>
    <section class="panel"><h3>Faculty records</h3><p class="muted">${esc(p.faculty_status)} · ${esc(p.highest_degree || "degree not set")} ${p.specialization ? "· " + esc(p.specialization) : ""}</p>
    ${table(["Kind", "Title", "Detail", "Year", "Scope"], d.records.map(r => `<tr><td>${esc(r.kind)}</td><td>${esc(r.title)}</td><td>${esc(r.detail || "")}</td><td>${esc(r.year || "")}</td><td>${esc(r.scope)}</td></tr>`))}</section>`;
};

VIEWS.faculty = async el => {   // 3.1 #7
  const show = async id => {
    const d = await api(id ? `/api/faculty/${id}` : "/api/faculty/me");
    $("#fdetail").innerHTML = facultySummary(d);
  };
  el.innerHTML = `<h2>Faculty/Teaching Information</h2>${chair() ? `<div class="panel"><label>Select faculty<select id="fsel"></select></label></div>` : ""}<div id="fdetail"></div>`;
  if (chair()) {
    const list = await api("/api/faculty");
    $("#fsel").innerHTML = opts(list, "id", "full_name");
    $("#fsel").onchange = run(() => show($("#fsel").value));
    if (list.length) await show(list[0].id);
    else $("#fdetail").innerHTML = "<p class='muted'>No faculty accounts yet.</p>";
  } else {
    await show(null);
  }
};

VIEWS.profile = async el => {
  el.innerHTML = `<h2>Profile</h2>
    <section class="panel"><h3>Account information</h3><dl class="account-details">
      <dt>Full name</dt><dd>${esc(me.name)}</dd><dt>Account email</dt><dd>${esc(me.email)}</dd>
      <dt>Account type</dt><dd>${esc(me.role.replaceAll("_", " "))}</dd>
    </dl></section>
    <section class="panel"><h3>Password</h3><p class="muted">Password reset will be available in a future update.</p>
      <form class="account-placeholder-form"><label>Current password<input type="password" autocomplete="current-password" disabled></label>
      <label>New password<input type="password" autocomplete="new-password" disabled></label>
      <button type="button" disabled>Reset password</button></form></section>
    <section class="panel"><h3>Google Authenticator</h3><p class="muted">Authenticator management is not available here yet. Your existing sign-in verification remains unchanged.</p>
      <button type="button" disabled>Set up Google Authenticator</button></section>
    ${me.role === "faculty" ? `<div id="profile-faculty"></div>` : ""}`;

  if (me.role !== "faculty") return;
  const d = await api("/api/faculty/me"), p = d.profile;
  const selectedCourses = new Set(d.courses.map(c => String(c.id)));
  $("#profile-faculty").innerHTML = `<form id="faculty-profile-form" class="panel faculty-profile-form" enctype="multipart/form-data">
    <h3>Faculty/Teaching Information</h3><p class="muted">Update your faculty details and courses handled. This information is visible to you and the Department Chair.</p>
    <div class="faculty-photo-field">${facultyPhoto(p, p.id)}<label>2×2 photo (JPG, PNG, or WebP; up to 5 MB)<input type="file" name="photo" accept="image/jpeg,image/png,image/webp"></label></div>
    <div class="faculty-profile-fields">
      <label>Full name<input name="full_name" value="${esc(p.full_name)}" maxlength="160" required></label>
      <label>UST email<input name="ust_email" type="email" value="${esc(p.ust_email || "")}" maxlength="254"></label>
      <label>Address<textarea name="address" maxlength="1000">${esc(p.address || "")}</textarea></label>
      <label>Contact number<input name="contact_number" type="tel" value="${esc(p.contact_number || "")}" maxlength="40"></label>
      <label>Facebook name<input name="facebook_name" value="${esc(p.facebook_name || "")}" maxlength="120"></label>
      <label>Faculty status<select name="faculty_status"><option value="Full-time" ${p.faculty_status === "Full-time" ? "selected" : ""}>Full-time</option><option value="Part-time" ${p.faculty_status === "Part-time" ? "selected" : ""}>Part-time</option></select></label>
      <label>Date started/appointed<input name="date_started" type="date" value="${esc(p.date_started || "")}"></label>
      <fieldset class="course-checkboxes"><legend>Courses handled</legend><small class="muted">Select all courses you currently handle.</small>
        <div class="course-checkbox-list">${meta.courses.length
          ? meta.courses.map(c => `<label><input type="checkbox" name="course_ids" value="${c.id}" ${selectedCourses.has(String(c.id)) ? "checked" : ""}> <span>${esc(c.code)} — ${esc(c.title)}</span></label>`).join("")
          : `<p class="muted">No courses are currently available.</p>`}
        </div>
      </fieldset>
    </div><button type="submit">Save information</button></form>
    <section class="panel"><h3>Faculty records</h3>${table(["Kind", "Title", "Detail", "Year", "Scope"], d.records.map(r => `<tr><td>${esc(r.kind)}</td><td>${esc(r.title)}</td><td>${esc(r.detail || "")}</td><td>${esc(r.year || "")}</td><td>${esc(r.scope)}</td></tr>`))}
    <form id="faculty-record-form"><h3>Add record</h3><div class="row"><label>Kind<select name="kind"><option>education</option><option>credential</option><option>award</option><option>research</option><option>activity</option></select></label>
    <label>Title<input name="title" required></label><label>Detail<input name="detail"></label><label>Year<input name="year" type="number" min="1950" max="2100"></label>
    <label>Scope<select name="scope"><option>local</option><option>international</option></select></label><button>Add</button></div></form></section>`;
  $("#faculty-profile-form").onsubmit = run(async e => {
    e.preventDefault();
    const formData = new FormData(e.target);
    await api(`/api/faculty/${p.id}/profile`, { method: "PUT", body: formData });
    me.name = formData.get("full_name");
    $("#who").textContent = `${me.name} · ${me.role.replace("_", " ")}`;
    $("#avatar").textContent = (me.name || "?")[0].toUpperCase();
    toast("Faculty/Teaching Information saved.");
    await VIEWS.profile(el);
  });
  $("#faculty-record-form").onsubmit = run(async e => {
    e.preventDefault();
    await api(`/api/faculty/${p.id}/records`, { json: Object.fromEntries(new FormData(e.target)) });
    toast("Record added.");
    await VIEWS.profile(el);
  });
};

VIEWS.dashboard = async el => {   // 3.1 #8
  const d = await api("/api/dashboard"), s = d.requirements, max = Math.max(1, ...d.stats.map(x => x.value));
  el.innerHTML = `<h2>Compliance Dashboard</h2><div class="grid">${["Completed", "In Progress", "Missing Evidence"].map(k => `<div class="card stat"><b class="${statusCls(k)}">${s[k] || 0}</b>${k}</div>`).join("")}<div class="card stat"><b>${d.review_queue}</b>Pending review</div></div>
  <div class="panel"><h3>Overall requirement completion — ${d.completion_pct}%</h3><div class="bar"><i style="width:${d.completion_pct}%"></i></div></div>
  <div class="panel"><h3>Departmental statistics</h3>${d.stats.length ? `<div class="chart" style="margin-bottom:28px">${d.stats.map(x => `<div style="height:${100 * x.value / max}%" title="${x.value}"><small>${esc(x.school_year)}<br>${esc(x.metric)}<br>${x.value}</small></div>`).join("")}</div>` : "<p class='muted'>No statistics recorded yet.</p>"}</div>
  ${chair() ? `<div class="panel"><h3>Report</h3><div class="row"><label>Course<select id="rc">${opts(meta.courses, "id", "code", "All")}</select></label><label>Semester<select id="rs"><option value="">All</option><option>1st</option><option>2nd</option><option>Summer</option></select></label><button id="dl">Download CSV report</button></div></div>` : ""}`;
  if ($("#dl")) $("#dl").onclick = () => { const p = new URLSearchParams({ course_id: $("#rc").value, semester: $("#rs").value }); [...p.keys()].forEach(k => !p.get(k) && p.delete(k)); location.href = "/api/dashboard/report.csv?" + p; };
};

VIEWS.access = async el => {   // evaluator (BR-10)
  const [cat, mine] = await Promise.all([api("/api/access-requests/catalog"), api("/api/access-requests")]);
  el.innerHTML = `<h2>Request Access to Evidence</h2><p class="muted">Your account is temporary and read-only. The Department Chair decides each request.</p>
  <div class="panel">${table(["Document", "Category", "Year", ""], cat.map(d => `<tr><td>${esc(d.title)}</td><td>${esc(d.category)}</td><td>${esc(d.school_year)} ${esc(d.semester)}</td><td><button class="sm" data-q="${d.id}">Request access</button></td></tr>`))}</div>
  <div class="panel"><h3>My requests</h3>${table(["Document", "Status"], mine.map(r => `<tr><td>${esc(r.title)}</td><td>${badge(r.status)}</td></tr>`))}</div>`;
  el.querySelectorAll("[data-q]").forEach(b => b.onclick = run(async () => { await api("/api/access-requests", { json: { document_id: b.dataset.q } }); toast("Request submitted."); route(); }));
};

VIEWS.admin = async el => {   // 3.1 #9
  const [us, bk, ar] = await Promise.all([api("/api/admin/users"), api("/api/admin/backups"), api("/api/admin/access-requests")]);
  el.innerHTML = `<h2>Administration</h2>
  <div class="panel"><h3>Accounts</h3>${table(["Name", "Email", "Role", "Active", "Expires", ""], us.map(u => `<tr><td>${esc(u.full_name)}</td><td>${esc(u.email)}</td><td>${esc(u.role)}</td><td>${u.is_active ? "Yes" : "No"}</td><td>${esc(u.expires_at || "—")}</td><td>${u.is_active && u.id !== me.id ? `<button class="sm bad" data-d="${u.id}">Deactivate</button>` : ""}</td></tr>`))}
  <form id="nu"><h3>Create account</h3><div class="row"><label>Full name<input name="full_name" required></label><label>Email<input name="email" type="email" required></label>
  <label>Temporary password (10+ chars)<input name="password" type="password" minlength="10" required></label>
  <label>Role<select name="role"><option value="faculty">Faculty</option><option value="dept_chair">Department Chair</option><option value="external_evaluator">External evaluator (temporary)</option></select></label><button>Create</button></div></form></div>
  <div class="panel"><h3>Evaluator access requests</h3>${table(["Evaluator", "Document", "Status", ""], ar.map(r => `<tr><td>${esc(r.evaluator)}</td><td>${esc(r.title)}</td><td>${badge(r.status)}</td><td>${r.status === "Pending" ? `<button class="sm" data-a="${r.id}|Approved">Approve</button> <button class="sm bad" data-a="${r.id}|Denied">Deny</button>` : ""}</td></tr>`))}</div>
  <div class="panel"><h3>Backup and recovery</h3><button id="bk">Back up now</button>${table(["#", "Created", "Kind", ""], bk.map(b => `<tr><td>${b.id}</td><td>${esc(b.created_at)}</td><td>${esc(b.kind)}</td><td><button class="sm sec" data-rs="${b.id}">Restore</button></td></tr>`))}</div>`;
  el.querySelectorAll("[data-d]").forEach(b => b.onclick = run(async () => { if (!confirm("Deactivate this account? Access is revoked immediately.")) return; await api(`/api/admin/users/${b.dataset.d}/deactivate`, { method: "POST" }); route(); }));
  el.querySelectorAll("[data-a]").forEach(b => b.onclick = run(async () => { const [id, status] = b.dataset.a.split("|"); await api(`/api/admin/access-requests/${id}/decide`, { json: { status } }); route(); }));
  $("#bk", el).onclick = run(async () => { await api("/api/admin/backups", { method: "POST" }); toast("Backup created."); route(); });
  el.querySelectorAll("[data-rs]").forEach(b => b.onclick = run(async () => { if (!confirm("Restore this backup? Current data is snapshotted first, then replaced.")) return; await api(`/api/admin/backups/${b.dataset.rs}/restore`, { method: "POST" }); toast("Restored."); route(); }));
  $("#nu", el).onsubmit = run(async e => { e.preventDefault(); const r = await api("/api/admin/users", { json: Object.fromEntries(new FormData(e.target)) });
    const dialog = modal(`<h3>Account created</h3><p>Scan this QR code with the user’s authenticator app, or enter the setup key manually. This key is shown only once.</p><img class="totp-qr" src="${esc(r.totp_qr)}" alt="Authenticator setup QR code"><label>Google Authenticator setup key<input id="totp-secret" value="${esc(r.totp_secret)}" readonly></label><button class="sm sec" type="button" id="copy-totp">Copy setup key</button>`);
    $("#copy-totp", dialog).onclick = run(async () => { await navigator.clipboard.writeText(r.totp_secret); toast("Setup key copied."); });
    route(); });
};

/* ---------- boot ---------- */
(async () => { const r = await fetch("/me"); if (r.ok) start(); else showLogin(); })();
