from pathlib import Path


def version_file_path(version, upload_dir):
    stored_path = Path(version["stored_path"])
    if stored_path.is_file():
        return stored_path
    fallback = Path(upload_dir) / stored_path.name
    return fallback if fallback.is_file() else None


def prune_version_files(db, document_id, upload_dir):
    versions = db.execute(
        "SELECT id, version_no, status, stored_path FROM document_versions "
        "WHERE document_id=? ORDER BY version_no DESC",
        (document_id,),
    ).fetchall()
    keep = set()
    approved = 0
    pending = False
    rejected = False
    for version in versions:
        status = version["status"]
        if status in ("Current Approved Version", "Superseded") and approved < 2:
            keep.add(version["id"])
            approved += 1
        elif status == "Pending Review" and not pending:
            keep.add(version["id"])
            pending = True
        elif status == "Requires Revision" and not rejected:
            keep.add(version["id"])
            rejected = True

    removed = []
    for version in versions:
        if version["id"] in keep:
            continue
        path = version_file_path(version, upload_dir)
        if path is not None:
            path.unlink()
            removed.append(version["id"])
    return removed


def prune_all_version_files(db, upload_dir):
    document_ids = db.execute(
        "SELECT DISTINCT document_id FROM document_versions"
    ).fetchall()
    removed = []
    for row in document_ids:
        removed.extend(prune_version_files(db, row["document_id"], upload_dir))
    return removed
