"""FR-06: accreditation requirement tracking and evidence linking."""
from flask import Blueprint, abort, g, jsonify, request
from ..db import get_db, audit
from ..security import permission_required
from .approvals import _refresh_requirements

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
