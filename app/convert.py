"""Phase 2: PDF previews for Word / PowerPoint / Excel / text / image files, made with LibreOffice.

Why: the viewer and comments work on PDF. A converted preview is stored ONCE per version and never
regenerated while comments exist, because comment positions are anchored to its layout. The original
file is untouched (BR-5). Previews live under UPLOAD_DIR/previews so backups include them."""
import hashlib, os, queue, shutil, subprocess, tempfile, threading
from pathlib import Path
from flask import current_app
from .db import get_db

CONVERTIBLE = {"docx", "pptx", "xlsx", "txt", "png", "jpg", "jpeg"}
FILTERS = {  # Excel: one page per sheet, so wide sheets aren't sliced across pages
    "xlsx": 'pdf:calc_pdf_Export:{"SinglePageSheets":{"type":"boolean","value":"true"}}'}
_jobs: queue.Queue = queue.Queue()
_worker = None
_wlock = threading.Lock()
_version_cache: dict = {}


def ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_pdf(path) -> bool:
    try:
        with open(path, "rb") as f:
            return b"%PDF-" in f.read(1024)
    except OSError:
        return False


def soffice_path():
    env = os.environ.get("ACCREDISYS_SOFFICE")
    if env and Path(env).exists():
        return env
    for n in ("soffice", "libreoffice"):
        p = shutil.which(n)
        if p:
            return p
    for p in (r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(p).exists():
            return p
    return None


def converter_info() -> dict:
    exe = soffice_path()
    if not exe:
        return {"available": False}
    if exe not in _version_cache:
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            out = "LibreOffice"
        _version_cache[exe] = out or "LibreOffice"
    return {"available": True, "version": _version_cache[exe]}


def convert_file(src, ext, outdir, timeout=120) -> Path:
    """Convert one file to PDF; returns the output path. Uses a throw-away LibreOffice profile (no shared locks)."""
    exe = soffice_path()
    if not exe:
        raise RuntimeError("LibreOffice is not installed.")
    src, outdir = Path(src), Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / (src.stem + ".pdf")
    out.unlink(missing_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="accredisys_lo_"))
    try:
        cmd = [exe, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore", "--nolockcheck",
               "--convert-to", FILTERS.get(ext, "pdf"), "--outdir", str(outdir), str(src)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError((r.stderr or r.stdout or "LibreOffice produced no output.")[:300])
        return out
    finally:
        shutil.rmtree(profile, ignore_errors=True)


# ---------- job handling ----------
def _run(app, vid, src, ext):
    with app.app_context():
        db = get_db()
        try:
            out = convert_file(src, ext, Path(app.config["UPLOAD_DIR"]) / "previews", app.config["CONVERT_TIMEOUT"])
            sha = hashlib.sha256(out.read_bytes()).hexdigest()
            db.execute("UPDATE version_previews SET status='ready', path=?, sha256=?, error=NULL, updated_at=datetime('now') WHERE version_id=?",
                       (str(out), sha, vid))
        except Exception as e:  # never let a bad file take the worker down
            db.execute("UPDATE version_previews SET status='failed', error=?, updated_at=datetime('now') WHERE version_id=?",
                       (str(e)[:300], vid))
        db.commit()


def _loop():
    while True:
        job = _jobs.get()
        try:
            _run(*job)
        except Exception:
            pass
        finally:
            _jobs.task_done()


def _enqueue(app, vid, src, ext):
    if app.config.get("CONVERT_SYNC"):
        return _run(app, vid, src, ext)
    global _worker
    with _wlock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, daemon=True, name="preview-converter")
            _worker.start()
    _jobs.put((app, vid, src, ext))


def _set(db, vid, status):
    db.execute("INSERT INTO version_previews (version_id, status) VALUES (?, ?) ON CONFLICT(version_id) DO UPDATE SET "
               "status=excluded.status, path=NULL, sha256=NULL, error=NULL, updated_at=datetime('now')", (vid, status))
    db.commit()


def ensure_preview(vid, stored_path, original_name, force=False):
    """Make sure a preview exists or is being made. Cheap and safe to call repeatedly."""
    ext = ext_of(original_name)
    if ext not in CONVERTIBLE or not current_app.config["CONVERT_ENABLED"]:
        return
    db = get_db()
    row = db.execute("SELECT *, (updated_at < datetime('now','-10 minutes')) AS stale FROM version_previews WHERE version_id=?", (vid,)).fetchone()
    if row and not force:
        if row["status"] == "ready" and row["path"] and Path(row["path"]).exists():
            return
        if row["status"] == "pending" and not row["stale"]:
            return
        if row["status"] == "failed":
            return  # only an explicit retry re-runs a failed conversion
    if not soffice_path():
        return _set(db, vid, "unavailable")
    _set(db, vid, "pending")
    _enqueue(current_app._get_current_object(), vid, stored_path, ext)


def preview_info(vid, stored_path, original_name):
    """-> (status, pdf_path, converted). status: ready | pending | failed | unavailable | unsupported"""
    ext = ext_of(original_name)
    if ext == "pdf":
        return ("ready", stored_path, False) if is_pdf(stored_path) else ("unsupported", None, False)
    if ext not in CONVERTIBLE:
        return ("unsupported", None, False)
    if not current_app.config["CONVERT_ENABLED"]:
        return ("unavailable", None, True)
    row = get_db().execute("SELECT * FROM version_previews WHERE version_id=?", (vid,)).fetchone()
    if not row:
        return ("pending", None, True)
    if row["status"] == "ready":
        if row["path"] and Path(row["path"]).exists():
            return ("ready", row["path"], True)
        return ("pending", None, True)  # file went missing; ensure_preview will rebuild it
    return (row["status"], None, True)
