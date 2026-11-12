"""FR-07 / BR-3 / BR-4: review workflow, signatories, revision requests."""
from flask import Blueprint, abort, g, jsonify, request
from ..db import get_db, audit
from ..security import permission_required, login_required

bp = Blueprint("approvals", __name__, url_prefix="/api")


@bp.get("/review-queue")
@permission_required("repo.review")
def queue():
    rows = get_db().execute(
        "SELECT v.id AS version_id, v.version_no, d.id AS document_id, d.title, u.full_name AS uploader, v.uploaded_at, "
        "(SELECT COUNT(*) FROM signatories s WHERE s.version_id=v.id) AS sig_total, "
        "(SELECT COUNT(*) FROM signatories s WHERE s.version_id=v.id AND s.signed_at IS NOT NULL) AS sig_done "
        "FROM document_versions v JOIN documents d ON d.id=v.document_id JOIN users u ON u.id=v.uploaded_by "
        "WHERE v.status='Pending Review' ORDER BY v.uploaded_at").fetchall()
    return jsonify([dict(r) for r in rows])


@bp.post("/versions/<int:vid>/approve")
@permission_required("repo.review")
def approve(vid):
    db = get_db()
    v = db.execute("SELECT * FROM document_versions WHERE id=?", (vid,)).fetchone() or abort(404)
    if v["status"] != "Pending Review":
        abort(400, "Only versions pending review can be approved.")
    # FR-07: all designated signatories must have signed before approval
    unsigned = db.execute("SELECT COUNT(*) FROM signatories WHERE version_id=? AND signed_at IS NULL", (vid,)).fetchone()[0]
    if unsigned:
        abort(400, f"{unsigned} required signatory(ies) have not signed yet.")
    db.execute("UPDATE document_versions SET status='Superseded' WHERE document_id=? AND status='Current Approved Version'", (v["document_id"],))
    db.execute("UPDATE document_versions SET status='Current Approved Version', reviewed_by=?, reviewed_at=datetime('now'), remarks=NULL WHERE id=?",
               (g.user["id"], vid))
    db.execute("UPDATE documents SET current_version_id=? WHERE id=?", (vid, v["document_id"]))
    _refresh_requirements(db, v["document_id"])
    audit("version.approve", "document_version", vid)
    db.commit()
    return jsonify(status="Current Approved Version")


@bp.post("/versions/<int:vid>/reject")
@permission_required("repo.review")
def reject(vid):
    remarks = ((request.get_json(silent=True) or request.form).get("remarks") or "").strip()
    if not remarks:  # BR-4: remarks mandatory
        abort(400, "Revision remarks are required when rejecting a document.")
    db = get_db()
    v = db.execute("SELECT * FROM document_versions WHERE id=?", (vid,)).fetchone() or abort(404)
    if v["status"] != "Pending Review":
        abort(400, "Only versions pending review can be rejected.")
    db.execute("UPDATE document_versions SET status='Requires Revision', remarks=?, reviewed_by=?, reviewed_at=datetime('now') WHERE id=?",
               (remarks, g.user["id"], vid))
    audit("version.reject", "document_version", vid, {"remarks": remarks})
    db.commit()
    return jsonify(status="Requires Revision", remarks=remarks)


@bp.post("/versions/<int:vid>/signatories")
@permission_required("repo.review")
def add_signatory(vid):
    sid = (request.get_json(silent=True) or request.form).get("signer_id") or abort(400, "signer_id required")
    db = get_db()
    v = db.execute("SELECT * FROM document_versions WHERE id=?", (vid,)).fetchone() or abort(404)
    db.execute("INSERT OR IGNORE INTO signatories (document_id, version_id, signer_id) VALUES (?,?,?)", (v["document_id"], vid, sid))
    audit("signatory.add", "document_version", vid, {"signer": sid})
    db.commit()
    return jsonify(ok=True), 201


@bp.post("/versions/<int:vid>/sign")
@login_required
def sign(vid):
    db = get_db()
    cur = db.execute("UPDATE signatories SET signed_at=datetime('now') WHERE version_id=? AND signer_id=? AND signed_at IS NULL",
                     (vid, g.user["id"]))
    if cur.rowcount == 0:
        abort(403)
    audit("signatory.sign", "document_version", vid)
    db.commit()
    return jsonify(ok=True)


@bp.post("/documents/<int:doc_id>/revision-requests")
@permission_required("repo.request_revision")
def request_revision(doc_id):
    msg = ((request.get_json(silent=True) or request.form).get("message") or "").strip() or abort(400, "Message required.")
    db = get_db()
    d = db.execute("SELECT owner_id FROM documents WHERE id=?", (doc_id,)).fetchone() or abort(404)
    rid = db.execute("INSERT INTO revision_requests (document_id, requested_by, assigned_to, message) VALUES (?,?,?,?)",
                     (doc_id, g.user["id"], d["owner_id"], msg)).lastrowid
    audit("revision.request", "document", doc_id)
    db.commit()
    return jsonify(id=rid), 201


@bp.get("/revision-requests")
@login_required
def my_requests():
    rows = get_db().execute(
        "SELECT r.document_id, d.title AS document_title, r.message "
        "FROM revision_requests r JOIN documents d ON d.id=r.document_id "
        "WHERE r.assigned_to=? AND r.resolved=0 "
        "UNION ALL "
        "SELECT d.id AS document_id, d.title AS document_title, "
        "'Version ' || v.version_no || ' rejected: ' || v.remarks AS message "
        "FROM document_versions v JOIN documents d ON d.id=v.document_id "
        "WHERE v.uploaded_by=? AND v.status='Requires Revision' "
        "AND v.version_no=(SELECT MAX(latest.version_no) FROM document_versions latest "
        "WHERE latest.document_id=v.document_id) "
        "ORDER BY document_title",
        (g.user["id"], g.user["id"])).fetchall()
    return jsonify([dict(r) for r in rows])


def _refresh_requirements(db, doc_id):
    """Approving evidence updates linked requirement completion (use case: Review and approve documents)."""
    for r in db.execute("SELECT requirement_id FROM requirement_evidence WHERE document_id=?", (doc_id,)).fetchall():
        _refresh_requirement_status(db, r["requirement_id"])


def _refresh_requirement_status(db, rid):
    total = db.execute("SELECT COUNT(*) FROM requirement_evidence WHERE requirement_id=?", (rid,)).fetchone()[0]
    ok = db.execute("SELECT COUNT(*) FROM requirement_evidence e JOIN documents d ON d.id=e.document_id "
                    "WHERE e.requirement_id=? AND d.current_version_id IS NOT NULL", (rid,)).fetchone()[0]
    status = "Completed" if total and ok == total else ("In Progress" if total else "Missing Evidence")
    db.execute("UPDATE requirements SET status=? WHERE id=?", (status, rid))


@bp.get("/my-signatures")
@login_required
def my_signatures():
    rows = get_db().execute(
        "SELECT s.version_id, d.title, v.version_no FROM signatories s JOIN document_versions v ON v.id=s.version_id "
        "JOIN documents d ON d.id=s.document_id WHERE s.signer_id=? AND s.signed_at IS NULL", (g.user["id"],)).fetchall()
    return jsonify([dict(r) for r in rows])
