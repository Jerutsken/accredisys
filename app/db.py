import json, sqlite3
from pathlib import Path
import click
from flask import current_app, g

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


def audit(action, entity=None, entity_id=None, detail=None):
    uid = g.user["id"] if getattr(g, "user", None) else None
    get_db().execute(
        "INSERT INTO audit_log (user_id, action, entity, entity_id, detail) VALUES (?,?,?,?,?)",
        (uid, action, entity, entity_id, json.dumps(detail) if detail is not None else None))


def init_app(app):
    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    def _init():
        init_db(); click.echo("Database initialised.")
