"""FR-01 / FR-02 / BR-6: password + authenticator (TOTP) login."""
from datetime import datetime, timedelta, timezone
from flask import Blueprint, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash
from ..db import get_db, audit
from ..security import verify_totp, csrf_token, login_required

bp = Blueprint("auth", __name__)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@bp.post("/login")
def login():
    d = request.get_json(silent=True) or request.form
    email, pw = (d.get("email") or "").strip().lower(), d.get("password") or ""
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    generic = (jsonify(error="Invalid email or password."), 401)  # NFR-11: no account enumeration
    if not u or not u["is_active"]:
        return generic
    if u["locked_until"] and datetime.fromisoformat(u["locked_until"]) > _now():
        return jsonify(error="Too many attempts. Try again later."), 429
    if not check_password_hash(u["password_hash"], pw):
        n = u["failed_logins"] + 1
        lock = None
        if n >= current_app.config["MAX_FAILED_LOGINS"]:
            lock = (_now() + timedelta(minutes=current_app.config["LOCKOUT_MINUTES"])).isoformat()
            n = 0
        db.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", (n, lock, u["id"]))
        db.commit()
        return generic
    if current_app.config["DISABLE_TOTP"]:
        session.clear()
        session["uid"], session["mfa_ok"] = u["id"], True
        g.user = u
        audit("login", "user", u["id"])
        db.commit()
        return jsonify(ok=True, role=u["role"], csrf=csrf_token())
    # Step 1 passed; session is NOT authenticated until MFA succeeds (BR-6).
    session.clear()
    session["pending_uid"] = u["id"]
    return jsonify(mfa_required=True)


@bp.post("/login/verify")
def verify_mfa():
    uid = session.get("pending_uid")
    if not uid:
        return jsonify(error="Start by entering your credentials."), 401
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    code = (request.get_json(silent=True) or request.form).get("code", "")
    if not u or not verify_totp(u["totp_secret"], code):
        return jsonify(error="Invalid verification code."), 401
    session.clear()
    session["uid"], session["mfa_ok"] = u["id"], True
    db.execute("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=?", (u["id"],))
    g.user = u
    audit("login", "user", u["id"])
    db.commit()
    return jsonify(ok=True, role=u["role"], csrf=csrf_token())


@bp.post("/logout")
@login_required
def logout():
    audit("logout", "user", g.user["id"])
    get_db().commit()
    session.clear()
    return jsonify(ok=True)


@bp.get("/me")
@login_required
def me():
    return jsonify(id=g.user["id"], name=g.user["full_name"], role=g.user["role"], csrf=csrf_token())
