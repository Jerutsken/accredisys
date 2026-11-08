import io, os, tempfile, unittest
from werkzeug.security import generate_password_hash
from app import create_app
from app.db import init_db, get_db
from app.security import new_totp_secret, totp_now, _hotp

PW = "ChangeMe!12345"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = create_app({"TESTING": True, "DATABASE": f"{self.tmp}/t.sqlite", "UPLOAD_DIR": f"{self.tmp}/up",
                               "BACKUP_DIR": f"{self.tmp}/bk", "SECRET_KEY": "t"})
        with self.app.app_context():
            init_db(); db = get_db()
            self.secrets = {}
            for email, role in [("chair@t", "dept_chair"), ("f1@t", "faculty"), ("f2@t", "faculty"), ("ev@t", "external_evaluator")]:
                s = new_totp_secret(); self.secrets[email] = s
                uid = db.execute("INSERT INTO users (email, full_name, password_hash, role, totp_secret) VALUES (?,?,?,?,?)",
                                 (email, email, generate_password_hash(PW), role, s)).lastrowid
                if role == "faculty":
                    db.execute("INSERT INTO faculty_profiles (user_id) VALUES (?)", (uid,))
            db.execute("INSERT INTO categories (name) VALUES ('Syllabi')")
            db.execute("INSERT INTO accreditation_bodies (name) VALUES ('PAASCU')")
            db.execute("INSERT INTO requirements (body_id, code, description) VALUES (1,'A1','x')")
            db.commit()

    def login(self, email):
        c = self.app.test_client()
        self.assertTrue(c.post("/login", json={"email": email, "password": PW}).get_json()["mfa_required"])
        r = c.post("/login/verify", json={"code": totp_now(self.secrets[email])})
        self.assertEqual(r.status_code, 200)
        c.csrf = r.get_json()["csrf"]
        return c

    def post(self, c, url, **kw):
        kw.setdefault("headers", {})["X-CSRF-Token"] = c.csrf
        return c.post(url, **kw)

    def upload(self, c, title="Syllabus"):
        return self.post(c, "/api/documents", data={"title": title, "category_id": 1, "school_year": "2026-2027", "semester": "1st",
                                                    "document_type": "syllabus", "file": (io.BytesIO(b"%PDF-1.4 hi"), "s.pdf")},
                         content_type="multipart/form-data")


class AuthTests(Base):
    def test_totp_rfc6238_vector(self):  # RFC 6238 SHA1 test vector, T=59 -> 94287082 (8 digits)
        import base64
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(_hotp(secret, 59 // 30, 8), "94287082")

    def test_password_alone_is_not_enough(self):
        c = self.app.test_client()
        c.post("/login", json={"email": "chair@t", "password": PW})
        self.assertEqual(c.get("/api/dashboard").status_code, 401)  

    def test_wrong_code_rejected(self):
        c = self.app.test_client()
        c.post("/login", json={"email": "chair@t", "password": PW})
        self.assertEqual(c.post("/login/verify", json={"code": "000000"}).status_code, 401)

    def test_lockout(self):
        c = self.app.test_client()
        for _ in range(5):
            c.post("/login", json={"email": "chair@t", "password": "bad"})
        self.assertEqual(c.post("/login", json={"email": "chair@t", "password": PW}).status_code, 429)

    def test_csrf_enforced(self):
        c = self.login("chair@t")
        self.assertEqual(c.post("/logout").status_code, 400)


class WorkflowTests(Base):
    def test_version_control_and_approval(self):  # BR-3, BR-4, BR-5
        f, ch = self.login("f1@t"), self.login("chair@t")
        did = self.upload(f).get_json()["document_id"]
        v1 = self.post(ch, "/api/versions/1/reject", json={})
        self.assertEqual(v1.status_code, 400)                                   # BR-4 remarks required
        self.assertEqual(self.post(ch, "/api/versions/1/reject", json={"remarks": "fix"}).status_code, 200)
        r = self.post(f, f"/api/documents/{did}/versions", data={"file": (io.BytesIO(b"v2"), "s2.pdf")}, content_type="multipart/form-data")
        self.assertEqual(r.get_json()["version_no"], 2)
        self.assertEqual(self.post(ch, "/api/versions/2/approve").status_code, 200)
        r = self.post(f, f"/api/documents/{did}/versions", data={"file": (io.BytesIO(b"v3"), "s3.pdf")}, content_type="multipart/form-data")
        self.assertEqual(self.post(ch, "/api/versions/3/approve").status_code, 200)
        with self.app.app_context():
            st = [r["status"] for r in get_db().execute("SELECT status FROM document_versions ORDER BY version_no")]
            self.assertEqual(st, ["Requires Revision", "Superseded", "Current Approved Version"])
            with self.assertRaises(Exception):                                    # BR-5 DB-level
                get_db().execute("DELETE FROM document_versions WHERE id=1")

    def test_faculty_cannot_approve_or_touch_others_docs(self):
        f1, f2 = self.login("f1@t"), self.login("f2@t")
        did = self.upload(f1).get_json()["document_id"]
        self.assertEqual(self.post(f1, "/api/versions/1/approve").status_code, 403)
        r = self.post(f2, f"/api/documents/{did}/versions", data={"file": (io.BytesIO(b"x"), "x.pdf")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 403)

    def test_requirement_completes_on_approval(self):
        f, ch = self.login("f1@t"), self.login("chair@t")
        did = self.upload(f).get_json()["document_id"]
        self.post(f, "/api/requirements/1/evidence", json={"document_id": did})
        self.assertEqual(self.login("chair@t").get("/api/requirements").get_json()[0]["status"], "In Progress")
        self.post(ch, "/api/versions/1/approve")
        self.assertEqual(ch.get("/api/requirements").get_json()[0]["status"], "Completed")

    def test_evaluator_sees_only_approved_and_granted(self):  # BR-10
        f, ch, ev = self.login("f1@t"), self.login("chair@t"), self.login("ev@t")
        did = self.upload(f).get_json()["document_id"]
        self.assertEqual(ev.get("/api/documents").get_json(), [])
        self.post(ch, "/api/versions/1/approve")
        self.assertEqual(ev.get("/api/documents").get_json(), [])              # approved but no access grant
        with self.app.app_context():
            db = get_db(); db.execute("INSERT INTO access_requests (evaluator_id, document_id, status) VALUES (4,?, 'Approved')", (did,)); db.commit()
        self.assertEqual(len(ev.get("/api/documents").get_json()), 1)
        self.assertEqual(self.post(ev, "/api/documents", data={}).status_code, 403)

    def test_deactivation_revokes_access_immediately(self):  # BR-2
        ch, f = self.login("chair@t"), self.login("f1@t")
        self.assertEqual(self.post(ch, "/api/admin/users/2/deactivate").status_code, 200)
        self.assertEqual(f.get("/api/dashboard").status_code, 401)

    def test_faculty_privacy(self):  # BR-7
        f1 = self.login("f1@t")
        self.assertEqual(f1.get("/api/faculty").status_code, 403)               # no directory listing
        self.assertEqual(f1.get("/api/faculty/1").status_code, 200)
        self.assertEqual(f1.get("/api/faculty/2").status_code, 403)

    def test_backup_and_restore(self):  # BR-8, FR-11
        f, ch = self.login("f1@t"), self.login("chair@t")
        self.upload(f)
        bid = self.post(ch, "/api/admin/backups").status_code
        self.assertEqual(bid, 201)
        self.upload(f, "Second")
        self.assertEqual(self.post(ch, "/api/admin/backups/1/restore").status_code, 200)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)

    def test_search_filters(self):  # FR-05
        f = self.login("f1@t")
        self.upload(f, "Intro to IS Syllabus"); self.upload(f, "Exam Set")
        self.assertEqual(len(f.get("/api/documents?q=Exam").get_json()), 1)
        self.assertEqual(len(f.get("/api/documents?semester=1st&document_type=syllabus").get_json()), 2)


if __name__ == "__main__":
    unittest.main()
