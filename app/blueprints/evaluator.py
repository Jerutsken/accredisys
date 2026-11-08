"""BR-10: external evaluators browse approved documents and file access requests."""
from flask import Blueprint, abort, g, jsonify, request
from ..db import get_db, audit
from ..security import login_required

bp = Blueprint("evaluator", __name__, url_prefix="/api/access-requests")


def _only_evaluator():
    if g.user["role"] != "external_evaluator":
        abort(403)


@bp.get("/catalog")
@login_required
def catalog():
    _only_evaluator()
    rows = get_db().execute("SELECT d.id, d.title, d.school_year, d.semester, c.name AS category FROM documents d "
                            "JOIN categories c ON c.id=d.category_id WHERE d.current_version_id IS NOT NULL ORDER BY d.title").fetchall()
    return jsonify([dict(r) for r in rows])


@bp.get("")
@login_required
def mine():
    _only_evaluator()
    rows = get_db().execute("SELECT r.id, r.status, d.title FROM access_requests r JOIN documents d ON d.id=r.document_id "
                            "WHERE r.evaluator_id=?", (g.user["id"],)).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.post("")
@login_required
def create():
    _only_evaluator()
    doc = (request.get_json(silent=True) or request.form).get("document_id") or abort(400, "document_id required")
    db = get_db()
    if not db.execute("SELECT 1 FROM documents WHERE id=? AND current_version_id IS NOT NULL", (doc,)).fetchone():
        abort(404)
    db.execute("INSERT OR IGNORE INTO access_requests (evaluator_id, document_id) VALUES (?,?)", (g.user["id"], doc))
    audit("access_request.create", "document", int(doc))
    db.commit()
    return jsonify(ok=True), 201
