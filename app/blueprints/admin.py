"""FR-11 / FR-12 / BR-2 / BR-8 / BR-10: accounts, evaluator access, backup & recovery."""
import shutil, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import Blueprint, abort, current_app, g, jsonify, request
from werkzeug.security import generate_password_hash
from ..db import get_db, audit
from ..security import permission_required, new_totp_secret, provisioning_uri

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.post("/users")
@permission_required("admin.users")
def create_user():
    d = request.get_json(silent=True) or request.form
    role = d.get("role")
    if role not in ("faculty", "dept_chair", "external_evaluator") or not (d.get("email") and d.get("full_name") and d.get("password")):
        abort(400, "email, full_name, password and a valid role are required.")
    if len(d["password"]) < 10:
        abort(400, "Password must be at least 10 characters.")
    expires = None
    if role == "external_evaluator":  # BR-10: temporary accounts only
        expires = (datetime.now(timezone.utc) + timedelta(days=int(d.get("valid_days", 14)))).replace(tzinfo=None).isoformat()
    secret = new_totp_secret()
    db = get_db()
    try:
        uid = db.execute("INSERT INTO users (email, full_name, password_hash, role, totp_secret, expires_at) VALUES (?,?,?,?,?,?)",
                         (d["email"].lower(), d["full_name"], generate_password_hash(d["password"]), role, secret, expires)).lastrowid
    except sqlite3.IntegrityError:
        abort(400, "An account with that email already exists.")
    if role == "faculty":
        db.execute("INSERT INTO faculty_profiles (user_id) VALUES (?)", (uid,))
    audit("user.create", "user", uid, {"role": role})
    db.commit()
    # One-time enrolment URI to render as a QR code in the authenticator app.
    return jsonify(id=uid, totp_uri=provisioning_uri(d["email"], secret)), 201


@bp.post("/users/<int:uid>/deactivate")
@permission_required("admin.users")
def deactivate(uid):  # BR-2 / Account Deactivation Policy (soft delete keeps audit history)
    if uid == g.user["id"]:
        abort(400, "You cannot deactivate your own account.")
    db = get_db()
    if not db.execute("UPDATE users SET is_active=0, deactivated_at=datetime('now') WHERE id=?", (uid,)).rowcount:
        abort(404)
    audit("user.deactivate", "user", uid)
    db.commit()
    return jsonify(ok=True)


@bp.get("/users")
@permission_required("admin.users")
def users():
    rows = get_db().execute("SELECT id, email, full_name, role, is_active, expires_at FROM users ORDER BY full_name").fetchall()
    return jsonify([dict(r) for r in rows])


@bp.post("/access-requests/<int:rid>/decide")
@permission_required("access_requests.decide")
def decide(rid):
    status = (request.get_json(silent=True) or request.form).get("status")
    if status not in ("Approved", "Denied"):
        abort(400, "status must be Approved or Denied.")
    db = get_db()
    if not db.execute("UPDATE access_requests SET status=?, decided_by=?, decided_at=datetime('now') WHERE id=?",
                      (status, g.user["id"], rid)).rowcount:
        abort(404)
    audit("access_request.decide", "access_request", rid, {"status": status})
    db.commit()
    return jsonify(status=status)


# ---- Backup & recovery (FR-11, BR-8) ----
def make_backup(kind="scheduled", user_id=None) -> str:
    """Consistent snapshot of the database + uploaded files. Callable from cron for scheduled runs."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    dest = Path(current_app.config["BACKUP_DIR"]) / f"backup_{stamp}"
    dest.mkdir(parents=True)
    src = sqlite3.connect(current_app.config["DATABASE"])
    dst = sqlite3.connect(dest / "accredisys.sqlite")
    with dst:
        src.backup(dst)  # online-safe SQLite backup API
    src.close(); dst.close()
    shutil.copytree(current_app.config["UPLOAD_DIR"], dest / "uploads")
    db = get_db()
    db.execute("INSERT INTO backups (path, kind, created_by) VALUES (?,?,?)", (str(dest), kind, user_id))
    db.commit()
    return str(dest)


@bp.post("/backups")
@permission_required("admin.backup")
def backup_now():
    path = make_backup("manual", g.user["id"])
    audit("backup.create", "backup", None, {"path": path}); get_db().commit()
    return jsonify(path=path), 201


@bp.get("/backups")
@permission_required("admin.backup")
def list_backups():
    return jsonify([dict(r) for r in get_db().execute("SELECT * FROM backups ORDER BY id DESC")])


@bp.post("/backups/<int:bid>/restore")
@permission_required("admin.backup")
def restore(bid):  # FR-11.2: Chair-initiated restoration
    row = get_db().execute("SELECT path FROM backups WHERE id=?", (bid,)).fetchone() or abort(404)
    src = Path(row["path"])
    if not (src / "accredisys.sqlite").exists():
        abort(400, "Backup files are missing.")
    # Safety net: snapshot current state before overwriting.
    pre = make_backup("manual", g.user["id"])
    db_path = current_app.config["DATABASE"]
    get_db().close()
    g.pop("db", None)
    shutil.copy2(src / "accredisys.sqlite", db_path)
    up = Path(current_app.config["UPLOAD_DIR"])
    shutil.rmtree(up, ignore_errors=True)
    shutil.copytree(src / "uploads", up)
    return jsonify(restored_from=str(src), pre_restore_snapshot=pre)


# ---- Evaluator access requests (BR-10) ----
@bp.get("/access-requests")
@permission_required("access_requests.decide")
def list_requests():
    rows = get_db().execute(
        "SELECT r.id, r.status, u.full_name AS evaluator, d.title FROM access_requests r "
        "JOIN users u ON u.id=r.evaluator_id JOIN documents d ON d.id=r.document_id ORDER BY r.status, r.id DESC").fetchall()
    return jsonify([dict(r) for r in rows])
