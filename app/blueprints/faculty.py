"""FR-08 / BR-7: faculty profile & history with privacy scoping (RA 10173)."""
from flask import Blueprint, abort, g, jsonify, request
from ..db import get_db, audit
from ..security import permission_required, can

bp = Blueprint("faculty", __name__, url_prefix="/api/faculty")
KINDS = {"education", "credential", "award", "research", "activity"}


def _own_profile_id():
    r = get_db().execute("SELECT id FROM faculty_profiles WHERE user_id=?", (g.user["id"],)).fetchone()
    return r["id"] if r else None


def _check_access(profile_id):
    if can(g.user, "faculty.manage") or (can(g.user, "faculty.manage_own") and profile_id == _own_profile_id()):
        return
    abort(403)


@bp.get("")
@permission_required("faculty.view")  # Chair only: list of all faculty (BR-7)
def list_all():
    rows = get_db().execute(
        "SELECT p.id, u.full_name, p.employment_type, p.highest_degree, p.specialization "
        "FROM faculty_profiles p JOIN users u ON u.id=p.user_id WHERE u.is_active=1 ORDER BY u.full_name").fetchall()
    return jsonify([dict(r) for r in rows])


@bp.get("/me")
@permission_required("faculty.view_own")
def mine():
    pid = _own_profile_id() or abort(404)
    return one(pid)


@bp.get("/<int:pid>")
@permission_required("dashboard.view")  # chair + faculty; row-level check below
def one(pid):
    _check_access(pid)
    db = get_db()
    p = db.execute("SELECT p.*, u.full_name FROM faculty_profiles p JOIN users u ON u.id=p.user_id WHERE p.id=?", (pid,)).fetchone() or abort(404)
    recs = db.execute("SELECT * FROM faculty_records WHERE faculty_id=? ORDER BY year DESC", (pid,)).fetchall()
    return jsonify(profile=dict(p), records=[dict(r) for r in recs])


@bp.post("/<int:pid>/records")
@permission_required("dashboard.view")
def add_record(pid):
    _check_access(pid)
    d = request.get_json(silent=True) or request.form
    if d.get("kind") not in KINDS or not d.get("title"):
        abort(400, "Valid kind and title are required.")
    db = get_db()
    rid = db.execute("INSERT INTO faculty_records (faculty_id, kind, title, detail, year, scope) VALUES (?,?,?,?,?,?)",
                     (pid, d["kind"], d["title"], d.get("detail"), d.get("year"), d.get("scope", "local"))).lastrowid
    audit("faculty.record.add", "faculty_profile", pid, {"kind": d["kind"]})
    db.commit()
    return jsonify(id=rid), 201
