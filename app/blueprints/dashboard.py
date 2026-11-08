"""FR-09: compliance dashboard + downloadable reports."""
import csv, io
from flask import Blueprint, Response, jsonify, request
from ..db import get_db
from ..security import permission_required

bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")


@bp.get("")
@permission_required("dashboard.view")
def summary():
    db = get_db()
    reqs = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) n FROM requirements GROUP BY status")}
    total = sum(reqs.values())
    done = reqs.get("Completed", 0)
    return jsonify(
        requirements=reqs,
        completion_pct=round(100 * done / total, 1) if total else 0.0,
        review_queue=db.execute("SELECT COUNT(*) FROM document_versions WHERE status='Pending Review'").fetchone()[0],
        documents=db.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        stats=[dict(r) for r in db.execute("SELECT * FROM department_stats ORDER BY school_year, metric, year_level")])


@bp.get("/report.csv")
@permission_required("reports.generate")
def report():
    """FR-09.2: requirement completion & approval status, filterable by course / semester."""
    q = request.args
    sql = ("SELECT d.school_year, d.semester, COALESCE(c.code,'-') course, d.title, d.document_type, "
           "(SELECT status FROM document_versions WHERE document_id=d.id ORDER BY version_no DESC LIMIT 1) status "
           "FROM documents d LEFT JOIN courses c ON c.id=d.course_id WHERE 1=1")
    args = []
    if q.get("course_id"): sql += " AND d.course_id=?"; args.append(q["course_id"])
    if q.get("semester"): sql += " AND d.semester=?"; args.append(q["semester"])
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["School Year", "Semester", "Course", "Title", "Type", "Latest Status"])
    for r in get_db().execute(sql + " ORDER BY d.school_year, d.semester", args):
        w.writerow(list(r))
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=accreditation_report.csv"})
