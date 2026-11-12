"""FR-08 / BR-7: faculty profile & history with privacy scoping (RA 10173)."""
from datetime import date
from pathlib import Path
import re
import uuid
from flask import Blueprint, abort, current_app, g, jsonify, request, send_file
from ..db import get_db, audit
from ..security import permission_required, can

bp = Blueprint("faculty", __name__, url_prefix="/api/faculty")
KINDS = {"education", "credential", "award", "research", "activity"}
PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024


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
        "SELECT p.id, u.full_name, p.faculty_status, p.highest_degree, p.specialization "
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
    courses = db.execute(
        "SELECT c.id, c.code, c.title FROM courses c JOIN faculty_courses fc ON fc.course_id=c.id "
        "WHERE fc.faculty_id=? ORDER BY c.code",
        (pid,),
    ).fetchall()
    profile = dict(p)
    profile["has_photo"] = bool(profile["photo_path"] and _photo_path(profile["photo_path"]).is_file())
    profile.pop("photo_path", None)
    return jsonify(profile=profile, records=[dict(r) for r in recs], courses=[dict(r) for r in courses])


def _photo_path(photo_name):
    return Path(current_app.config["UPLOAD_DIR"]) / Path(photo_name).name


@bp.put("/<int:pid>/profile")
@permission_required("dashboard.view")
def update_profile(pid):
    _check_access(pid)
    db = get_db()
    profile = db.execute(
        "SELECT p.photo_path FROM faculty_profiles p WHERE p.id=?",
        (pid,),
    ).fetchone() or abort(404)
    full_name = request.form.get("full_name", "").strip()
    address = request.form.get("address", "").strip()
    contact_number = request.form.get("contact_number", "").strip()
    facebook_name = request.form.get("facebook_name", "").strip()
    ust_email = request.form.get("ust_email", "").strip()
    faculty_status = request.form.get("faculty_status", "")
    date_started = request.form.get("date_started", "").strip()
    if not full_name:
        abort(400, "Full name is required.")
    if faculty_status not in ("Full-time", "Part-time"):
        abort(400, "Faculty status must be Full-time or Part-time.")
    if ust_email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", ust_email):
        abort(400, "Enter a valid UST email address.")
    if date_started:
        try:
            date.fromisoformat(date_started)
        except ValueError:
            abort(400, "Enter a valid appointment date.")
    if len(full_name) > 160 or len(address) > 1000 or len(contact_number) > 40 or len(facebook_name) > 120 or len(ust_email) > 254:
        abort(400, "One or more profile fields are too long.")

    raw_course_ids = request.form.getlist("course_ids")
    try:
        course_ids = list(dict.fromkeys(int(value) for value in raw_course_ids))
    except ValueError:
        abort(400, "Select valid courses.")
    if course_ids:
        placeholders = ",".join("?" for _ in course_ids)
        found = db.execute(
            f"SELECT COUNT(*) FROM courses WHERE id IN ({placeholders})",
            course_ids,
        ).fetchone()[0]
        if found != len(course_ids):
            abort(400, "One or more selected courses do not exist.")

    photo = request.files.get("photo")
    photo_name = profile["photo_path"]
    new_photo_path = None
    if photo and photo.filename:
        extension = photo.filename.rsplit(".", 1)[-1].lower() if "." in photo.filename else ""
        if extension not in PHOTO_EXTENSIONS:
            abort(400, "Profile photo must be a JPG, PNG, or WebP image.")
        data = photo.read(MAX_PHOTO_BYTES + 1)
        if not data or len(data) > MAX_PHOTO_BYTES:
            abort(400, "Profile photo must be non-empty and no larger than 5 MB.")
        valid_image = (
            (extension in ("jpg", "jpeg") and data.startswith(b"\xff\xd8\xff"))
            or (extension == "png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
            or (extension == "webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        )
        if not valid_image:
            abort(400, "The selected file is not a valid JPG, PNG, or WebP image.")
        photo_name = f"faculty_{pid}_{uuid.uuid4().hex}.{extension}"
        new_photo_path = _photo_path(photo_name)
        new_photo_path.write_bytes(data)

    db.execute(
        "UPDATE users SET full_name=? WHERE id=(SELECT user_id FROM faculty_profiles WHERE id=?)",
        (full_name, pid),
    )
    db.execute(
        "UPDATE faculty_profiles SET photo_path=?, address=?, contact_number=?, facebook_name=?, "
        "ust_email=?, faculty_status=?, date_started=? WHERE id=?",
        (photo_name, address, contact_number, facebook_name, ust_email, faculty_status, date_started or None, pid),
    )
    db.execute("DELETE FROM faculty_courses WHERE faculty_id=?", (pid,))
    db.executemany(
        "INSERT INTO faculty_courses (faculty_id, course_id) VALUES (?,?)",
        [(pid, course_id) for course_id in course_ids],
    )
    audit("faculty.profile.update", "faculty_profile", pid)
    db.commit()

    old_photo = profile["photo_path"]
    if old_photo and old_photo != photo_name:
        old_path = _photo_path(old_photo)
        if old_path.is_file():
            old_path.unlink()
    return jsonify(ok=True)


@bp.get("/<int:pid>/photo")
@permission_required("dashboard.view")
def photo(pid):
    _check_access(pid)
    profile = get_db().execute(
        "SELECT photo_path FROM faculty_profiles WHERE id=?",
        (pid,),
    ).fetchone() or abort(404)
    if not profile["photo_path"]:
        abort(404)
    path = _photo_path(profile["photo_path"])
    if not path.is_file():
        abort(404)
    return send_file(path, conditional=True)


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
