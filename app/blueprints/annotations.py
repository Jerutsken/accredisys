"""Phase 1 — in-browser PDF viewing with highlights/comments (Google-Docs style).
Annotations are an overlay stored in the database; the uploaded file is never modified (BR-5).
Visibility: the Department Chair and the document's owner. Other faculty and evaluators can open
the approved PDF but see no comments. Authors edit their own comments; Chair may delete any."""
import json
from io import BytesIO
from flask import Blueprint, abort, g, jsonify, request, send_file
from .. import convert
from ..db import get_db, audit
from ..security import can, login_required

bp = Blueprint("annotations", __name__, url_prefix="/api")
MAX_COMMENT, MAX_QUOTE, MAX_RECTS = 4000, 2000, 60


# ---------- access helpers ----------
def _version(vid):
    return get_db().execute(
        "SELECT v.*, d.title, d.owner_id, d.current_version_id AS cur FROM document_versions v "
        "JOIN documents d ON d.id=v.document_id WHERE v.id=?", (vid,)).fetchone() or abort(404)


def _can_open_file(v) -> bool:
    role, uid = g.user["role"], g.user["id"]
    if role == "dept_chair":
        return True
    if role == "faculty":
        return v["owner_id"] == uid or v["id"] == v["cur"]
    if role == "external_evaluator":  # BR-10: only the approved version, only if granted
        return v["id"] == v["cur"] and bool(get_db().execute(
            "SELECT 1 FROM access_requests WHERE document_id=? AND evaluator_id=? AND status='Approved'",
            (v["document_id"], uid)).fetchone())
    return False


def _can_see_comments(v) -> bool:
    return can(g.user, "annotate.view") and (g.user["role"] == "dept_chair" or v["owner_id"] == g.user["id"])


def _can_add(v) -> bool:
    return can(g.user, "annotate.add") and _can_see_comments(v)


def _preview(v):
    """-> (status, pdf_path, converted). Native PDFs are 'ready'; Office files use their stored preview."""
    return convert.preview_info(v["id"], v["stored_path"], v["original_name"])


def _has_comments(vid) -> bool:
    return bool(get_db().execute("SELECT 1 FROM annotations WHERE version_id=? LIMIT 1", (vid,)).fetchone())


def _ser(r, replies=()):
    uid, chair = g.user["id"], g.user["role"] == "dept_chair"
    d = dict(id=r["id"], quote=r["quote"], comment=r["comment"], author=r["author"], author_id=r["author_id"],
             created_at=r["created_at"], updated_at=r["updated_at"], can_edit=r["author_id"] == uid,
             can_delete=chair or r["author_id"] == uid)
    if r["parent_id"] is None:
        d.update(page=r["page"], rects=json.loads(r["rects"]), resolved=bool(r["resolved"]), replies=list(replies))
    return d


_SEL = ("SELECT a.*, u.full_name AS author FROM annotations a JOIN users u ON u.id=a.author_id ")


def _list(vid):
    rows = get_db().execute(_SEL + "WHERE a.version_id=? ORDER BY a.page, a.id", (vid,)).fetchall()
    reps: dict = {}
    for r in rows:
        if r["parent_id"] is not None:
            reps.setdefault(r["parent_id"], []).append(_ser(r))
    return [_ser(r, reps.get(r["id"], [])) for r in rows if r["parent_id"] is None]


def _annotation(aid):
    a = get_db().execute(_SEL + "WHERE a.id=?", (aid,)).fetchone() or abort(404)
    v = _version(a["version_id"])
    if not (_can_open_file(v) and _can_see_comments(v)):
        abort(403)
    return a, v


def _text(field="comment", required=False):
    t = ((request.get_json(silent=True) or {}).get(field) or "").strip()
    if required and not t:
        abort(400, "Please write something first.")
    if len(t) > MAX_COMMENT:
        abort(400, f"Comments are limited to {MAX_COMMENT} characters.")
    return t


def _clean_rects(raw):
    if not isinstance(raw, list) or not (1 <= len(raw) <= MAX_RECTS):
        abort(400, "Select some text to highlight.")
    out = []
    for r in raw:
        if not (isinstance(r, list) and len(r) == 4 and all(isinstance(x, (int, float)) and -0.05 <= x <= 1.05 for x in r)):
            abort(400, "Invalid highlight position.")
        x, y, w, h = (min(1.0, max(0.0, float(x))) for x in r)
        w, h = min(w, 1 - x), min(h, 1 - y)
        if w <= 0 or h <= 0:
            abort(400, "Invalid highlight position.")
        out.append([round(x, 5), round(y, 5), round(w, 5), round(h, 5)])
    return out


# ---------- viewing ----------
@bp.get("/versions/<int:vid>/annotations")
@login_required
def info(vid):
    v = _version(vid)
    if not _can_open_file(v):
        abort(403)
    convert.ensure_preview(v["id"], v["stored_path"], v["original_name"])
    status, _path, converted = _preview(v)
    see = _can_see_comments(v)
    audit("document.view", "document_version", vid)
    get_db().commit()
    return jsonify(
        version=dict(id=v["id"], version_no=v["version_no"], status=v["status"], original_name=v["original_name"],
                     document_id=v["document_id"], title=v["title"],
                     preview=dict(status=status, converted=converted, source=convert.ext_of(v["original_name"]),
                                  can_retry=see and status in ("failed", "unavailable") and not _has_comments(vid))),
        can_view_comments=see, can_annotate=_can_add(v), me=g.user["id"], annotations=_list(vid) if see else [])


@bp.get("/versions/<int:vid>/view")
@login_required
def view(vid):
    v = _version(vid)
    if not _can_open_file(v):
        abort(403)
    status, path, _c = _preview(v)
    if status == "unsupported":
        abort(415)
    if status != "ready":
        return jsonify(error="The preview isn't ready."), 409
    return send_file(path, mimetype="application/pdf", conditional=True)


# ---------- annotations CRUD ----------
@bp.post("/versions/<int:vid>/annotations")
@login_required
def create(vid):
    v = _version(vid)
    if not (_can_open_file(v) and _can_add(v)):
        abort(403)
    if _preview(v)[0] != "ready":
        abort(400, "The preview isn't ready yet.")
    d = request.get_json(silent=True) or {}
    page = d.get("page")
    if not (isinstance(page, int) and 1 <= page <= 5000):
        abort(400, "Invalid page.")
    rects = _clean_rects(d.get("rects"))
    quote = (d.get("quote") or "").strip()[:MAX_QUOTE]
    db = get_db()
    aid = db.execute("INSERT INTO annotations (version_id, page, rects, quote, comment, author_id) VALUES (?,?,?,?,?,?)",
                     (vid, page, json.dumps(rects), quote, _text(), g.user["id"])).lastrowid
    audit("annotation.create", "annotation", aid, {"version": vid, "page": page})
    db.commit()
    return jsonify(_ser(db.execute(_SEL + "WHERE a.id=?", (aid,)).fetchone())), 201


@bp.post("/annotations/<int:aid>/replies")
@login_required
def reply(aid):
    a, v = _annotation(aid)
    if a["parent_id"] is not None or not _can_add(v):
        abort(403)
    db = get_db()
    rid = db.execute("INSERT INTO annotations (version_id, parent_id, comment, author_id) VALUES (?,?,?,?)",
                     (a["version_id"], aid, _text(required=True), g.user["id"])).lastrowid
    audit("annotation.reply", "annotation", rid, {"parent": aid})
    db.commit()
    return jsonify(_ser(db.execute(_SEL + "WHERE a.id=?", (rid,)).fetchone())), 201


@bp.patch("/annotations/<int:aid>")
@login_required
def edit(aid):
    a, v = _annotation(aid)
    if a["author_id"] != g.user["id"]:
        abort(403)  # authors edit only their own comments
    db = get_db()
    db.execute("UPDATE annotations SET comment=?, updated_at=datetime('now') WHERE id=?",
               (_text(required=a["parent_id"] is not None), aid))
    audit("annotation.edit", "annotation", aid)
    db.commit()
    return jsonify(_ser(db.execute(_SEL + "WHERE a.id=?", (aid,)).fetchone()))


@bp.post("/annotations/<int:aid>/resolve")
@login_required
def resolve(aid):
    a, v = _annotation(aid)
    if a["parent_id"] is not None or not _can_add(v):
        abort(403)
    val = 1 if (request.get_json(silent=True) or {}).get("resolved", True) else 0
    db = get_db()
    db.execute("UPDATE annotations SET resolved=? WHERE id=?", (val, aid))
    audit("annotation.resolve" if val else "annotation.reopen", "annotation", aid)
    db.commit()
    return jsonify(resolved=bool(val))


@bp.delete("/annotations/<int:aid>")
@login_required
def delete(aid):
    a, v = _annotation(aid)
    if not (g.user["role"] == "dept_chair" or a["author_id"] == g.user["id"]):
        abort(403)
    db = get_db()
    db.execute("DELETE FROM annotations WHERE id=?", (aid,))  # replies cascade
    audit("annotation.delete", "annotation", aid)
    db.commit()
    return jsonify(ok=True)


# ---------- download with comments ----------
@bp.get("/versions/<int:vid>/annotated.pdf")
@login_required
def annotated(vid):
    v = _version(vid)
    if not (_can_open_file(v) and _can_see_comments(v)):
        abort(403)
    status, pdf_path, _c = _preview(v)
    if status != "ready":
        abort(415 if status == "unsupported" else 409, "The preview isn't ready.")
    try:
        from ..pdf_export import build_annotated_pdf
    except ImportError:
        return jsonify(error="Exporting comments needs the 'pypdf' package (pip install -r requirements.txt)."), 501
    items = []
    for a in _list(vid):
        text = f"{a['author']}: {a['comment']}".strip() if a["comment"] else ""
        for r in a["replies"]:
            text += f"\n  ↳ {r['author']}: {r['comment']}"
        if a["resolved"]:
            text = "[Resolved] " + text
        items.append(dict(page=a["page"], rects=a["rects"], text=text.strip(), author=a["author"], resolved=a["resolved"]))
    with open(pdf_path, "rb") as f:
        data = f.read()
    try:
        out, skipped = build_annotated_pdf(data, items)
    except ValueError as e:
        abort(400, str(e))
    except Exception:
        return jsonify(error="Could not add comments to this PDF. Download the original instead."), 500
    audit("document.export_annotated", "document_version", vid, {"annotations": len(items), "skipped": skipped})
    get_db().commit()
    stem = v["original_name"].rsplit(".", 1)[0]
    return send_file(BytesIO(out), mimetype="application/pdf", as_attachment=True, download_name=f"{stem}_annotated.pdf")


# ---------- Phase 2: preview status / retry ----------
@bp.get("/versions/<int:vid>/preview-status")
@login_required
def preview_status(vid):
    v = _version(vid)
    if not _can_open_file(v):
        abort(403)
    return jsonify(status=_preview(v)[0])


@bp.post("/versions/<int:vid>/preview/retry")
@login_required
def preview_retry(vid):
    v = _version(vid)
    if not (_can_open_file(v) and _can_see_comments(v)):
        abort(403)
    status = _preview(v)[0]
    if status not in ("failed", "unavailable", "pending"):
        abort(400, "Nothing to retry.")
    if _has_comments(vid):
        abort(400, "This preview already has comments, so it can't be rebuilt.")
    convert.ensure_preview(vid, v["stored_path"], v["original_name"], force=True)
    return jsonify(status=_preview(v)[0])
