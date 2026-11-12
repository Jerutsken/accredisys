import json, sqlite3
from pathlib import Path
import click
from flask import current_app, g
from .version_files import prune_all_version_files

SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def init_db():
    get_db().executescript(SCHEMA.read_text())
    migrate_faculty_profile_schema()


def migrate_faculty_profile_schema():
    db = get_db()
    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "faculty_profiles" not in tables or "courses" not in tables:
        return

    columns = {r["name"] for r in db.execute("PRAGMA table_info(faculty_profiles)").fetchall()}
    additions = {
        "photo_path": "TEXT",
        "address": "TEXT",
        "contact_number": "TEXT",
        "facebook_name": "TEXT",
        "ust_email": "TEXT",
        "faculty_status": "TEXT NOT NULL DEFAULT 'Full-time' CHECK (faculty_status IN ('Full-time','Part-time'))",
        "date_started": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE faculty_profiles ADD COLUMN {name} {definition}")
    db.execute(
        "CREATE TABLE IF NOT EXISTS faculty_courses ("
        "faculty_id INTEGER NOT NULL REFERENCES faculty_profiles(id) ON DELETE CASCADE, "
        "course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE, "
        "PRIMARY KEY (faculty_id, course_id))"
    )
    db.commit()


def audit(action, entity=None, entity_id=None, detail=None):
    uid = g.user["id"] if getattr(g, "user", None) else None
    get_db().execute(
        "INSERT INTO audit_log (user_id, action, entity, entity_id, detail) VALUES (?,?,?,?,?)",
        (uid, action, entity, entity_id, json.dumps(detail) if detail is not None else None))


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        migrate_faculty_profile_schema()

    @app.cli.command("init-db")
    def _init():
        init_db(); click.echo("Database initialised.")

    @app.cli.command("prune-version-files")
    def _prune_version_files():
        """Apply the version-file retention policy to existing documents."""
        removed = prune_all_version_files(get_db(), current_app.config["UPLOAD_DIR"])
        click.echo(f"Removed file contents for {len(removed)} older version(s).")
