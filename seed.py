"""Create the database and load SAMPLE data only (the NDA forbids real/confidential data — SRS 2.5.1).
Usage: python seed.py   -> prints demo credentials and TOTP secrets."""
from werkzeug.security import generate_password_hash
from app import create_app
from app.db import init_db, get_db
from app.security import new_totp_secret

app = create_app()
with app.app_context():
    init_db()
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        raise SystemExit("Database already seeded.")
    people = [("chair@example.test", "Sample Department Chair", "dept_chair"),
              ("faculty1@example.test", "Sample Faculty One", "faculty"),
              ("faculty2@example.test", "Sample Faculty Two", "faculty")]
    print("Demo accounts (password: ChangeMe!12345)")
    for email, name, role in people:
        secret = new_totp_secret()
        uid = db.execute("INSERT INTO users (email, full_name, password_hash, role, totp_secret) VALUES (?,?,?,?,?)",
                         (email, name, generate_password_hash("ChangeMe!12345"), role, secret)).lastrowid
        if role == "faculty":
            db.execute("INSERT INTO faculty_profiles (user_id, highest_degree) VALUES (?, 'Masteral')", (uid,))
        print(f"  {role:10} {email}  TOTP secret: {secret}")
    # Main categories -> subcategories (interview 9/11); names are placeholders to be confirmed with the Chair.
    for main, subs in {"Syllabi": ["Major Courses", "General Education"], "Examination Collections": [],
                       "Faculty Credentials": [], "Accreditation Evidence": ["Area I", "Area II"],
                       "Research & Activities": [], "Awards": []}.items():
        pid = db.execute("INSERT INTO categories (name) VALUES (?)", (main,)).lastrowid
        for s in subs:
            db.execute("INSERT INTO categories (name, parent_id) VALUES (?,?)", (s, pid))
    db.executemany("INSERT INTO courses (code, title) VALUES (?,?)", [("IS101", "Sample Course A"), ("IS102", "Sample Course B")])
    db.executemany("INSERT INTO accreditation_bodies (name) VALUES (?)", [("PAASCU",), ("CHED",)])
    db.executemany("INSERT INTO requirements (body_id, code, description) VALUES (1,?,?)",
                   [("A1", "Sample requirement 1"), ("A2", "Sample requirement 2")])
    db.executemany("INSERT INTO department_stats VALUES (?,?,?,?)",
                   [("2024-2025", "enrollees", 0, 100), ("2024-2025", "graduates", 0, 20)])
    db.commit()
    print("Seeded.")
