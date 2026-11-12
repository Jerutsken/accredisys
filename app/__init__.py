import mimetypes, os
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, g, jsonify, request, session
from . import db, security


def _expired(user) -> bool:
    if not user["expires_at"]:
        return False
    return datetime.fromisoformat(user["expires_at"]) < datetime.now(timezone.utc).replace(tzinfo=None)


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    inst = Path(app.instance_path)
    app.config.update(
        SECRET_KEY=os.environ.get("ACCREDISYS_SECRET", "dev-only-change-me"),
        DISABLE_TOTP=False,
        DATABASE=str(inst / "accredisys.sqlite"),
        UPLOAD_DIR=str(inst / "uploads"),
        BACKUP_DIR=str(inst / "backups"),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        ALLOWED_EXTENSIONS={"pdf", "docx", "xlsx", "pptx", "png", "jpg", "jpeg", "txt"},
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        MAX_FAILED_LOGINS=5, LOCKOUT_MINUTES=15,
        CONVERT_ENABLED=True, CONVERT_SYNC=False, CONVERT_TIMEOUT=120,
    )
    if config:
        app.config.update(config)
    for d in (Path(app.config["DATABASE"]).parent, app.config["UPLOAD_DIR"], app.config["BACKUP_DIR"]):
        Path(d).mkdir(parents=True, exist_ok=True)
    mimetypes.add_type("text/javascript", ".mjs")
    db.init_app(app)
    with app.app_context():
        db.init_db()

    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("uid")
        if uid and session.get("mfa_ok"):
            row = db.get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            # BR-2: deactivated/expired accounts lose access immediately
            if row and row["is_active"] and not _expired(row):
                g.user = row

    @app.before_request
    def csrf_guard():
        if request.endpoint not in (None, "static", "auth.login", "auth.verify_mfa", "health"):
            security.check_csrf()

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    from .blueprints import auth, repository, approvals, requirements, faculty, admin, dashboard, evaluator, annotations
    for m in (auth, repository, approvals, requirements, faculty, admin, dashboard, evaluator, annotations):
        app.register_blueprint(m.bp)

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.errorhandler(403)
    def forbidden(e):  # NFR-11: understandable message, no technical detail
        return jsonify(error="You do not have permission to do that."), 403

    @app.errorhandler(401)
    def unauth(e):
        return jsonify(error="Please sign in."), 401

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.errorhandler(400)
    def bad(e):
        return jsonify(error=getattr(e, "description", "Bad request")), 400

    @app.errorhandler(415)
    def unsupported(e):
        return jsonify(error="Preview is available for PDF files only."), 415

    @app.errorhandler(404)
    def nf(e):
        return jsonify(error="Record not found."), 404

    return app
