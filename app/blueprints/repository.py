"""FR-03/04/05/10: repository, upload, versioning (BR-3, BR-5), search & filter."""
import hashlib, uuid
from pathlib import Path
from flask import Blueprint, abort, current_app, g, jsonify, request, send_file
from werkzeug.utils import secure_filename
from .. import convert
from ..db import get_db, audit
from ..security import permission_required, can, login_required
from ..version_files import prune_version_files, version_file_path

bp = Blueprint("repo", __name__, url_prefix="/api/documents")
REQUIRED = ("title", "category_id", "school_year", "semester", "document_type")  # FR-03.2


def _visible_clause():
    """BR-10: evaluators only see documents with an approved access request AND an approved version."""
    if g.user["role"] == "external_evaluator":
        return (" AND d.current_version_id IS NOT NULL AND EXISTS (SELECT 1 FROM access_requests r "
                "WHERE r.document_id=d.id AND r.evaluator_id=? AND r.status='Approved')"), [g.user["id"]]
    if g.user["role"] == "faculty":
        # Faculty: own documents plus approved departmental reference files (read-only, SRS 2.2)
        return " AND (d.owner_id=? OR d.current_version_id IS NOT NULL)", [g.user["id"]]
    return "", []


@bp.get("")
@login_required
def search():
    if not (can(g.user, "repo.view") or can(g.user, "repo.view_approved_only")):
        abort(403)
    q = request.args
    sql = ("SELECT d.id, d.owner_id, d.title, d.description, d.school_year, d.semester, d.document_type, "
           "c.name AS category, co.code AS course, d.current_version_id, "
           "(SELECT id FROM document_versions WHERE document_id=d.id ORDER BY version_no DESC LIMIT 1) AS latest_version_id, "
           "(SELECT MAX(version_no) FROM document_versions WHERE document_id=d.id) AS latest_version, "
           "(SELECT status FROM document_versions WHERE document_id=d.id ORDER BY version_no DESC LIMIT 1) AS latest_status, "
           "(SELECT COUNT(*) FROM signatories s WHERE s.version_id=(SELECT id FROM document_versions WHERE document_id=d.id ORDER BY version_no DESC LIMIT 1)) AS sig_total, "
           "(SELECT COUNT(*) FROM signatories s WHERE s.version_id=(SELECT id FROM document_versions WHERE document_id=d.id ORDER BY version_no DESC LIMIT 1) AND s.signed_at IS NOT NULL) AS sig_done, "
           "EXISTS (SELECT 1 FROM signatories s WHERE s.version_id=(SELECT id FROM document_versions WHERE document_id=d.id ORDER BY version_no DESC LIMIT 1) "
           "AND s.signer_id=? AND s.signed_at IS NULL) AS my_pending_signature "
           "FROM documents d JOIN categories c ON c.id=d.category_id "
           "LEFT JOIN courses co ON co.id=d.course_id WHERE 1=1")
    args: list = []
    if q.get("q"):  # FR-05.1: keyword across title and description
        like = f"%{q['q']}%"
        sql += " AND (d.title LIKE ? OR d.description LIKE ?)"; args += [like, like]
    for param, col in (("category_id", "d.category_id"), ("course_id", "d.course_id"),
                       ("school_year", "d.school_year"), ("semester", "d.semester"),
                       ("document_type", "d.document_type")):
        if q.get(param):
            sql += f" AND {col}=?"; args.append(q[param])
    if q.get("approval_status"):  # FR-05.2
        sql += (" AND EXISTS (SELECT 1 FROM document_versions v WHERE v.document_id=d.id "
                "AND v.version_no=(SELECT MAX(version_no) FROM document_versions WHERE document_id=d.id) AND v.status=?)")
        args.append(q["approval_status"])
    vis, vargs = _visible_clause()
    rows = get_db().execute(sql + vis + " ORDER BY d.school_year DESC, d.title", [g.user["id"]] + args + vargs).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["can_update"] = g.user["role"] == "dept_chair" or item["owner_id"] == g.user["id"]
        del item["owner_id"]
        results.append(item)
    return jsonify(results)


def _store(file, doc_id, version_no):
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        abort(400, "File type not allowed.")
    data = file.read()
    if not data:
        abort(400, "File is empty.")
    name = f"{doc_id}_v{version_no}_{uuid.uuid4().hex}.{ext}"
    path = Path(current_app.config["UPLOAD_DIR"]) / name
    path.write_bytes(data)
    return str(path), secure_filename(file.filename), hashlib.sha256(data).hexdigest(), len(data)


def _add_version(db, doc_id, file, action):
    n = db.execute(
        "SELECT COALESCE(MAX(version_no),0)+1 FROM document_versions WHERE document_id=?",
        (doc_id,),
    ).fetchone()[0]
    path, orig, sha, size = _store(file, doc_id, n)
    vid = db.execute(
        "INSERT INTO document_versions (document_id, version_no, stored_path, original_name, sha256, size_bytes, uploaded_by) "
        "VALUES (?,?,?,?,?,?,?)",
        (doc_id, n, path, orig, sha, size, g.user["id"]),
    ).lastrowid
    db.execute("UPDATE revision_requests SET resolved=1 WHERE document_id=?", (doc_id,))
    audit(action, "document", doc_id, {"version": n})
    db.commit()
    prune_version_files(db, doc_id, current_app.config["UPLOAD_DIR"])
    convert.ensure_preview(vid, path, orig)
    return vid, n


@bp.post("")
@permission_required("repo.upload")
def create():
    f, file = request.form, request.files.get("file")
    missing = [k for k in REQUIRED if not f.get(k)]
    if missing or not file:
        abort(400, "Missing required fields: " + ", ".join(missing + ([] if file else ["file"])))
    db = get_db()
    course_id = f.get("course_id") or None
    course_match = "course_id IS NULL" if course_id is None else "course_id=?"
    match_sql = (
        "SELECT id FROM documents WHERE title=? AND category_id=? AND "
        + course_match
        + " AND school_year=? AND semester=? AND document_type=?"
    )
    match_args = [f["title"], f["category_id"]]
    if course_id is not None:
        match_args.append(course_id)
    match_args.extend([f["school_year"], f["semester"], f["document_type"]])
    if g.user["role"] == "faculty":
        match_sql += " AND owner_id=?"
        match_args.append(g.user["id"])
    matches = db.execute(match_sql, match_args).fetchall()
    if len(matches) > 1:
        return jsonify(error=(
            "Multiple documents match this metadata. Open the intended repository entry "
            "and use Replace file to add a version."
        )), 409
    if matches:
        doc_id = matches[0]["id"]
        vid, version_no = _add_version(db, doc_id, file, "document.version")
        return jsonify(
            document_id=doc_id,
            version_id=vid,
            version_no=version_no,
            status="Pending Review",
            matched_existing=True,
        ), 201

    cur = db.execute(
        "INSERT INTO documents (title, description, category_id, course_id, school_year, semester, document_type, owner_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (f["title"], f.get("description", ""), f["category_id"], course_id,
         f["school_year"], f["semester"], f["document_type"], g.user["id"]))
    doc_id = cur.lastrowid
    vid, version_no = _add_version(db, doc_id, file, "document.create")
    return jsonify(
        document_id=doc_id,
        version_id=vid,
        version_no=version_no,
        status="Pending Review",
        matched_existing=False,
    ), 201


@bp.post("/<int:doc_id>/versions")
@permission_required("repo.upload")
def new_version(doc_id):
    db = get_db()
    d = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone() or abort(404)
    if g.user["role"] == "faculty" and d["owner_id"] != g.user["id"]:
        abort(403)  # BR-9 / Document Authorization Policy
    file = request.files.get("file") or abort(400, "File is required.")
    vid, n = _add_version(db, doc_id, file, "document.version")
    return jsonify(version_id=vid, version_no=n, status="Pending Review"), 201


@bp.get("/<int:doc_id>/versions")
@login_required
def versions(doc_id):
    db = get_db()
    d = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone() or abort(404)
    _assert_can_read(d)
    rows = db.execute("SELECT id, version_no, original_name, size_bytes, status, remarks, uploaded_at, reviewed_at, stored_path "
                      "FROM document_versions WHERE document_id=? ORDER BY version_no DESC", (doc_id,)).fetchall()
    if g.user["role"] == "external_evaluator":
        rows = [r for r in rows if r["id"] == d["current_version_id"]]
    versions = []
    for row in rows:
        version = dict(row)
        version["file_available"] = version_file_path(row, current_app.config["UPLOAD_DIR"]) is not None
        del version["stored_path"]
        versions.append(version)
    return jsonify(versions)


def _assert_can_read(d):
    role = g.user["role"]
    if role == "dept_chair":
        return
    if role == "faculty" and (d["owner_id"] == g.user["id"] or d["current_version_id"]):
        return
    if role == "external_evaluator" and d["current_version_id"] and get_db().execute(
            "SELECT 1 FROM access_requests WHERE document_id=? AND evaluator_id=? AND status='Approved'",
            (d["id"], g.user["id"])).fetchone():
        return
    abort(403)


@bp.get("/<int:doc_id>/download")
@login_required
def download(doc_id):
    db = get_db()
    d = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone() or abort(404)
    _assert_can_read(d)
    vno = request.args.get("version_no", type=int)
    if g.user["role"] == "external_evaluator" or vno is None:
        row = db.execute("SELECT * FROM document_versions WHERE id=?", (d["current_version_id"],)).fetchone() if d["current_version_id"] else None
        if row is None and g.user["role"] != "external_evaluator":  # fall back to latest
            row = db.execute("SELECT * FROM document_versions WHERE document_id=? ORDER BY version_no DESC LIMIT 1", (doc_id,)).fetchone()
    else:
        row = db.execute("SELECT * FROM document_versions WHERE document_id=? AND version_no=?", (doc_id, vno)).fetchone()
    if not row:
        abort(404)
    stored_path = version_file_path(row, current_app.config["UPLOAD_DIR"])
    if stored_path is None:
        abort(410, "This version's file is no longer retained; its history is preserved.")
    audit("document.download", "document", doc_id, {"version": row["version_no"]})
    db.commit()
    return send_file(stored_path, download_name=row["original_name"], as_attachment=True)


@bp.get("/meta")
@login_required
def meta():
    """Dropdown data for upload/filter forms."""
    db = get_db()
    return jsonify(
        categories=[dict(r) for r in db.execute("SELECT * FROM categories ORDER BY name")],
        courses=[dict(r) for r in db.execute("SELECT * FROM courses ORDER BY code")])
