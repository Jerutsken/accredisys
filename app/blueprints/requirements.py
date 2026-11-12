"""FR-06: accreditation requirement tracking and evidence linking."""
from flask import Blueprint, abort, g, jsonify, request
from ..db import get_db, audit
from ..security import permission_required
from .approvals import _refresh_requirements, _refresh_requirement_status

bp = Blueprint("requirements", __name__, url_prefix="/api/requirements")


@bp.get("")
@permission_required("requirements.view")
def checklist():
    rows = get_db().execute(
        "SELECT r.id, b.name AS body, r.code, r.description, r.status, "
        "(SELECT COUNT(*) FROM requirement_evidence e WHERE e.requirement_id=r.id) AS evidence_count "
        "FROM requirements r JOIN accreditation_bodies b ON b.id=r.body_id ORDER BY b.name, r.code").fetchall()
    return jsonify([dict(r) for r in rows])


@bp.post("")
@permission_required("requirements.manage")
def create():  # requirements change as standards are updated (Mother Nature: changing requirements)
    d = request.get_json(silent=True) or request.form
    if not (d.get("body") and d.get("code") and d.get("description")):
        abort(400, "body, code and description are required.")
    db = get_db()
    db.execute("INSERT OR IGNORE INTO accreditation_bodies (name) VALUES (?)", (d["body"],))
    body = db.execute("SELECT id FROM accreditation_bodies WHERE name=?", (d["body"],)).fetchone()["id"]
    rid = db.execute("INSERT INTO requirements (body_id, code, description) VALUES (?,?,?)",
                     (body, d["code"], d["description"])).lastrowid
    audit("requirement.create", "requirement", rid)
    db.commit()
    return jsonify(id=rid), 201


@bp.post("/<int:rid>/evidence")
@permission_required("requirements.view")
def link(rid):  # FR-06.2: "authorized users" link evidence they own (Chair: any)
    doc_id = (request.get_json(silent=True) or request.form).get("document_id") or abort(400, "document_id required")
    db = get_db()
    d = db.execute("SELECT owner_id FROM documents WHERE id=?", (doc_id,)).fetchone() or abort(404)
    if g.user["role"] == "faculty" and d["owner_id"] != g.user["id"]:
        abort(403)
    db.execute("INSERT OR IGNORE INTO requirement_evidence (requirement_id, document_id, linked_by) VALUES (?,?,?)",
               (rid, doc_id, g.user["id"]))
    _refresh_requirements(db, int(doc_id))
    audit("requirement.link", "requirement", rid, {"document": doc_id})
    db.commit()
    return jsonify(ok=True), 201


@bp.get("/<int:rid>/evidence")
@permission_required("requirements.view")
def linked_evidence(rid):
    db = get_db()
    if not db.execute("SELECT 1 FROM requirements WHERE id=?", (rid,)).fetchone():
        abort(404)
    sql = ("SELECT d.id, d.title, "
           "(SELECT MAX(version_no) FROM document_versions WHERE document_id=d.id) AS latest_version "
           "FROM requirement_evidence e JOIN documents d ON d.id=e.document_id WHERE e.requirement_id=?")
    args = [rid]
    if g.user["role"] == "faculty":
        sql += " AND d.owner_id=?"
        args.append(g.user["id"])
    rows = db.execute(sql + " ORDER BY d.title", args).fetchall()
    return jsonify([dict(row) for row in rows])


@bp.get("/<int:rid>/evidence/options")
@permission_required("requirements.view")
def evidence_options(rid):
    db = get_db()
    if not db.execute("SELECT 1 FROM requirements WHERE id=?", (rid,)).fetchone():
        abort(404)
    sql = ("SELECT d.id, d.title, "
           "(SELECT MAX(version_no) FROM document_versions WHERE document_id=d.id) AS latest_version "
           "FROM documents d")
    args = []
    if g.user["role"] == "faculty":
        sql += " WHERE d.owner_id=?"
        args.append(g.user["id"])
    rows = db.execute(sql + " ORDER BY d.title", args).fetchall()
    return jsonify([dict(row) for row in rows])


@bp.put("/<int:rid>/evidence/<int:doc_id>")
@permission_required("requirements.view")
def replace_evidence(rid, doc_id):
    raw_doc_id = (request.get_json(silent=True) or request.form).get("document_id")
    try:
        new_doc_id = int(raw_doc_id)
    except (TypeError, ValueError):
        abort(400, "A valid document_id is required.")
    if not new_doc_id or new_doc_id == doc_id:
        abort(400, "A different document_id is required.")
    db = get_db()
    if not db.execute("SELECT 1 FROM requirements WHERE id=?", (rid,)).fetchone():
        abort(404)
    linked = db.execute(
        "SELECT d.owner_id FROM requirement_evidence e JOIN documents d ON d.id=e.document_id "
        "WHERE e.requirement_id=? AND e.document_id=?", (rid, doc_id)).fetchone()
    if not linked:
        abort(404)
    replacement = db.execute("SELECT owner_id FROM documents WHERE id=?", (new_doc_id,)).fetchone()
    if not replacement:
        abort(404)
    if g.user["role"] == "faculty" and (
            linked["owner_id"] != g.user["id"] or replacement["owner_id"] != g.user["id"]):
        abort(403)
    if db.execute("SELECT 1 FROM requirement_evidence WHERE requirement_id=? AND document_id=?",
                  (rid, new_doc_id)).fetchone():
        abort(409, "That document is already linked to this requirement.")
    db.execute("UPDATE requirement_evidence SET document_id=?, linked_by=? "
               "WHERE requirement_id=? AND document_id=?", (new_doc_id, g.user["id"], rid, doc_id))
    _refresh_requirement_status(db, rid)
    audit("requirement.evidence.replace", "requirement", rid,
          {"previous_document": doc_id, "document": new_doc_id})
    db.commit()
    return jsonify(ok=True)


@bp.delete("/<int:rid>/evidence/<int:doc_id>")
@permission_required("requirements.view")
def unlink_evidence(rid, doc_id):
    db = get_db()
    if not db.execute("SELECT 1 FROM requirements WHERE id=?", (rid,)).fetchone():
        abort(404)
    linked = db.execute(
        "SELECT d.owner_id FROM requirement_evidence e JOIN documents d ON d.id=e.document_id "
        "WHERE e.requirement_id=? AND e.document_id=?", (rid, doc_id)).fetchone()
    if not linked:
        abort(404)
    if g.user["role"] == "faculty" and linked["owner_id"] != g.user["id"]:
        abort(403)
    db.execute("DELETE FROM requirement_evidence WHERE requirement_id=? AND document_id=?", (rid, doc_id))
    _refresh_requirement_status(db, rid)
    audit("requirement.evidence.unlink", "requirement", rid, {"document": doc_id})
    db.commit()
    return jsonify(ok=True)
