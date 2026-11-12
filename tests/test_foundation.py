import base64, io, os, sqlite3, tempfile, unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
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

    def put(self, c, url, **kw):
        kw.setdefault("headers", {})["X-CSRF-Token"] = c.csrf
        return c.put(url, **kw)

    def upload(self, c, title="Syllabus"):
        return self.post(c, "/api/documents", data={"title": title, "category_id": 1, "school_year": "2026-2027", "semester": "1st",
                                                    "document_type": "syllabus", "file": (io.BytesIO(b"%PDF-1.4 hi"), "s.pdf")},
                         content_type="multipart/form-data")


class SchemaMigrationTests(unittest.TestCase):
    def test_existing_faculty_profile_schema_is_migrated(self):
        tmp = tempfile.mkdtemp()
        database = Path(tmp) / "legacy.sqlite"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE courses (id INTEGER PRIMARY KEY, code TEXT, title TEXT)")
            db.execute(
                "CREATE TABLE faculty_profiles (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, "
                "employment_type TEXT NOT NULL DEFAULT 'Regular', highest_degree TEXT, specialization TEXT)"
            )

        app = create_app({
            "DATABASE": str(database),
            "UPLOAD_DIR": str(Path(tmp) / "uploads"),
            "BACKUP_DIR": str(Path(tmp) / "backups"),
        })
        with app.app_context():
            columns = {
                row["name"] for row in get_db().execute("PRAGMA table_info(faculty_profiles)").fetchall()
            }
            self.assertTrue({
                "photo_path", "address", "contact_number", "facebook_name",
                "ust_email", "faculty_status", "date_started",
            }.issubset(columns))
            self.assertTrue(get_db().execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='faculty_courses'"
            ).fetchone())


class AuthTests(Base):
    def test_totp_rfc6238_vector(self):  # RFC 6238 SHA1 test vector, T=59 -> 94287082 (8 digits)
        import base64
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(_hotp(secret, 59 // 30, 8), "94287082")

    def test_password_alone_is_not_enough(self):
        c = self.app.test_client()
        c.post("/login", json={"email": "chair@t", "password": PW})
        self.assertEqual(c.get("/api/dashboard").status_code, 401)  

    def test_account_profile_includes_email(self):
        chair = self.login("chair@t")
        profile = chair.get("/me").get_json()
        self.assertEqual(profile["email"], "chair@t")
        self.assertEqual(profile["name"], "chair@t")

    def test_totp_can_be_disabled_for_local_demo(self):
        self.app.config["DISABLE_TOTP"] = True
        c = self.app.test_client()
        r = c.post("/login", json={"email": "chair@t", "password": PW})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(c.get("/api/dashboard").status_code, 200)

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

    def test_create_user_returns_local_totp_qr_and_secret(self):
        chair = self.login("chair@t")
        response = self.post(chair, "/api/admin/users", json={
            "email": "new.user+test@example.com",
            "full_name": "New User",
            "password": PW,
            "role": "faculty",
        })
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertRegex(data["totp_secret"], r"^[A-Z2-7]+$")
        self.assertEqual(parse_qs(urlparse(data["totp_uri"]).query)["secret"], [data["totp_secret"]])
        self.assertTrue(data["totp_qr"].startswith("data:image/svg+xml;base64,"))
        svg = base64.b64decode(data["totp_qr"].split(",", 1)[1])
        self.assertIn(b"<svg", svg)
        self.assertIn(b"<path", svg)


class WorkflowTests(Base):
    def version_path(self, document_id, version_no):
        with self.app.app_context():
            path = get_db().execute(
                "SELECT stored_path FROM document_versions WHERE document_id=? AND version_no=?",
                (document_id, version_no),
            ).fetchone()["stored_path"]
            return Path(path)

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

    def test_upload_with_exact_metadata_adds_version_to_existing_entry(self):
        faculty = self.login("f1@t")
        first = self.upload(faculty, "Same syllabus").get_json()
        second = self.upload(faculty, "Same syllabus").get_json()

        self.assertEqual(second["document_id"], first["document_id"])
        self.assertEqual(second["version_no"], 2)
        self.assertTrue(second["matched_existing"])
        with self.app.app_context():
            self.assertEqual(
                get_db().execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                1,
            )
            self.assertEqual(
                get_db().execute(
                    "SELECT COUNT(*) FROM document_versions WHERE document_id=?",
                    (first["document_id"],),
                ).fetchone()[0],
                2,
            )

    def test_upload_with_different_metadata_creates_new_entry(self):
        faculty = self.login("f1@t")
        first = self.upload(faculty, "Different semesters").get_json()
        response = self.post(
            faculty,
            "/api/documents",
            data={
                "title": "Different semesters",
                "category_id": 1,
                "school_year": "2026-2027",
                "semester": "2nd",
                "document_type": "syllabus",
                "file": (io.BytesIO(b"second semester"), "s2.pdf"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201)
        second = response.get_json()
        self.assertNotEqual(second["document_id"], first["document_id"])
        self.assertFalse(second["matched_existing"])

    def test_upload_with_multiple_exact_matches_requires_explicit_selection(self):
        faculty = self.login("f1@t")
        first = self.upload(faculty, "Ambiguous syllabus").get_json()
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO documents (title, category_id, school_year, semester, document_type, owner_id) "
                "VALUES ('Ambiguous syllabus', 1, '2026-2027', '1st', 'syllabus', 2)"
            )
            db.commit()

        response = self.upload(faculty, "Ambiguous syllabus")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Multiple documents match", response.get_json()["error"])
        with self.app.app_context():
            self.assertEqual(
                get_db().execute("SELECT COUNT(*) FROM document_versions").fetchone()[0],
                1,
            )

    def test_only_two_most_recent_approved_files_are_retained(self):
        faculty, chair = self.login("f1@t"), self.login("chair@t")
        did = self.upload(faculty, "Approved history").get_json()["document_id"]
        for version_no in (1, 2, 3):
            if version_no > 1:
                response = self.post(
                    faculty, f"/api/documents/{did}/versions",
                    data={"file": (io.BytesIO(f"v{version_no}".encode()), f"v{version_no}.pdf")},
                    content_type="multipart/form-data",
                )
                self.assertEqual(response.status_code, 201)
            self.assertEqual(self.post(chair, f"/api/versions/{version_no}/approve").status_code, 200)

        self.assertFalse(self.version_path(did, 1).exists())
        self.assertTrue(self.version_path(did, 2).exists())
        self.assertTrue(self.version_path(did, 3).exists())
        history = faculty.get(f"/api/documents/{did}/versions").get_json()
        self.assertEqual([v["file_available"] for v in history], [True, True, False])
        self.assertEqual(chair.get(f"/api/documents/{did}/download?version_no=1").status_code, 410)
        download = chair.get(f"/api/documents/{did}/download?version_no=2")
        self.assertEqual(download.status_code, 200)
        download.close()

    def test_only_latest_pending_upload_file_is_retained_and_reviewed(self):
        faculty, chair = self.login("f1@t"), self.login("chair@t")
        did = self.upload(faculty, "Pending history").get_json()["document_id"]
        for version_no in (2, 3):
            response = self.post(
                faculty, f"/api/documents/{did}/versions",
                data={"file": (io.BytesIO(f"v{version_no}".encode()), f"v{version_no}.pdf")},
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 201)

        self.assertFalse(self.version_path(did, 1).exists())
        self.assertFalse(self.version_path(did, 2).exists())
        self.assertTrue(self.version_path(did, 3).exists())
        history = faculty.get(f"/api/documents/{did}/versions").get_json()
        self.assertEqual([v["file_available"] for v in history], [True, False, False])
        queue = chair.get("/api/review-queue").get_json()
        self.assertEqual([(v["document_id"], v["version_no"]) for v in queue], [(did, 3)])
        self.assertEqual(self.post(chair, "/api/versions/1/approve").status_code, 410)

    def test_replacement_is_pending_until_approved_and_update_action_is_scoped(self):
        faculty, other, chair = self.login("f1@t"), self.login("f2@t"), self.login("chair@t")
        did = self.upload(faculty, "Replaceable document").get_json()["document_id"]
        self.assertEqual(self.post(chair, "/api/versions/1/approve").status_code, 200)

        own_doc = next(row for row in faculty.get("/api/documents").get_json() if row["id"] == did)
        shared_doc = next(row for row in other.get("/api/documents").get_json() if row["id"] == did)
        chair_doc = next(row for row in chair.get("/api/documents").get_json() if row["id"] == did)
        self.assertTrue(own_doc["can_update"])
        self.assertFalse(shared_doc["can_update"])
        self.assertTrue(chair_doc["can_update"])

        response = self.post(
            faculty, f"/api/documents/{did}/versions",
            data={"file": (io.BytesIO(b"replacement"), "replacement.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["status"], "Pending Review")
        with self.app.app_context():
            document = get_db().execute(
                "SELECT current_version_id FROM documents WHERE id=?", (did,)
            ).fetchone()
            self.assertEqual(document["current_version_id"], 1)

        self.assertEqual(self.post(chair, "/api/versions/2/approve").status_code, 200)
        updated_doc = next(row for row in faculty.get("/api/documents").get_json() if row["id"] == did)
        self.assertEqual(updated_doc["current_version_id"], 2)

    def test_only_latest_rejected_upload_file_is_retained(self):
        faculty, chair = self.login("f1@t"), self.login("chair@t")
        did = self.upload(faculty, "Rejected history").get_json()["document_id"]
        self.assertEqual(
            self.post(chair, "/api/versions/1/reject", json={"remarks": "Update this."}).status_code,
            200,
        )
        response = self.post(
            faculty, f"/api/documents/{did}/versions",
            data={"file": (io.BytesIO(b"v2"), "v2.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            self.post(chair, "/api/versions/2/reject", json={"remarks": "Try again."}).status_code,
            200,
        )

        self.assertFalse(self.version_path(did, 1).exists())
        self.assertTrue(self.version_path(did, 2).exists())
        history = faculty.get(f"/api/documents/{did}/versions").get_json()
        self.assertEqual([v["file_available"] for v in history], [True, False])

    def test_faculty_cannot_approve_or_touch_others_docs(self):
        f1, f2 = self.login("f1@t"), self.login("f2@t")
        did = self.upload(f1).get_json()["document_id"]
        self.assertEqual(self.post(f1, "/api/versions/1/approve").status_code, 403)
        r = self.post(f2, f"/api/documents/{did}/versions", data={"file": (io.BytesIO(b"x"), "x.pdf")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 403)

    def test_faculty_can_update_own_teaching_information_and_photo(self):
        faculty, other, chair = self.login("f1@t"), self.login("f2@t"), self.login("chair@t")
        with self.app.app_context():
            db = get_db()
            db.executemany(
                "INSERT INTO courses (code, title) VALUES (?,?)",
                [("IS201", "Systems Analysis"), ("IS202", "Database Design")],
            )
            profile_id = db.execute(
                "SELECT id FROM faculty_profiles WHERE user_id=("
                "SELECT id FROM users WHERE email='f1@t')"
            ).fetchone()["id"]
            course_ids = [r["id"] for r in db.execute(
                "SELECT id FROM courses ORDER BY code"
            ).fetchall()]
            db.commit()

        response = self.put(
            faculty,
            f"/api/faculty/{profile_id}/profile",
            data={
                "full_name": "Faculty One Updated",
                "address": "123 Campus Road",
                "contact_number": "+63 912 345 6789",
                "facebook_name": "Faculty One",
                "ust_email": "faculty.one@ust.edu.ph",
                "faculty_status": "Part-time",
                "date_started": "2022-08-01",
                "course_ids": [str(course_id) for course_id in course_ids],
                "photo": (io.BytesIO(b"\xff\xd8\xfffake-jpeg-data"), "faculty.jpg"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)

        profile = faculty.get("/api/faculty/me").get_json()
        self.assertEqual(profile["profile"]["full_name"], "Faculty One Updated")
        self.assertEqual(profile["profile"]["address"], "123 Campus Road")
        self.assertEqual(profile["profile"]["faculty_status"], "Part-time")
        self.assertEqual(profile["profile"]["date_started"], "2022-08-01")
        self.assertTrue(profile["profile"]["has_photo"])
        self.assertEqual([course["id"] for course in profile["courses"]], course_ids)
        self.assertEqual(chair.get(f"/api/faculty/{profile_id}").get_json()["profile"]["ust_email"],
                         "faculty.one@ust.edu.ph")

        photo = faculty.get(f"/api/faculty/{profile_id}/photo")
        self.assertEqual(photo.status_code, 200)
        photo.close()
        self.assertEqual(other.get(f"/api/faculty/{profile_id}").status_code, 403)
        self.assertEqual(
            self.put(other, f"/api/faculty/{profile_id}/profile", data={"full_name": "Intruder"}).status_code,
            403,
        )

    def test_revision_requests_include_document_title(self):
        f, ch = self.login("f1@t"), self.login("chair@t")
        did = self.upload(f, "Named syllabus").get_json()["document_id"]
        response = self.post(ch, f"/api/documents/{did}/revision-requests",
                             json={"message": "Please update the course outline."})
        self.assertEqual(response.status_code, 201)
        requests = f.get("/api/revision-requests").get_json()
        self.assertEqual(requests[0]["document_title"], "Named syllabus")

    def test_rejected_latest_upload_appears_in_uploader_updates(self):
        faculty, chair = self.login("f1@t"), self.login("chair@t")
        did = self.upload(faculty, "Rejected syllabus").get_json()["document_id"]
        response = self.post(chair, "/api/versions/1/reject", json={"remarks": "Please correct the course outcomes."})
        self.assertEqual(response.status_code, 200)

        updates = faculty.get("/api/revision-requests").get_json()
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["document_id"], did)
        self.assertEqual(updates[0]["document_title"], "Rejected syllabus")
        self.assertIn("Please correct the course outcomes.", updates[0]["message"])
        self.assertEqual(chair.get("/api/revision-requests").get_json(), [])

        self.assertEqual(
            self.post(faculty, f"/api/documents/{did}/versions",
                      data={"file": (io.BytesIO(b"updated"), "updated.pdf")},
                      content_type="multipart/form-data").status_code,
            201,
        )
        self.assertEqual(faculty.get("/api/revision-requests").get_json(), [])

    def test_download_uses_configured_upload_dir_for_stale_stored_path(self):
        f, ch = self.login("f1@t"), self.login("chair@t")
        did = self.upload(f).get_json()["document_id"]
        with self.app.app_context():
            stored_path = Path(get_db().execute(
                "SELECT stored_path FROM document_versions WHERE document_id=?", (did,)
            ).fetchone()["stored_path"])
            relocated_dir = Path(self.tmp) / "relocated-uploads"
            relocated_dir.mkdir()
            relocated_file = relocated_dir / stored_path.name
            relocated_file.write_bytes(stored_path.read_bytes())
            self.app.config["UPLOAD_DIR"] = str(relocated_dir)
            stored_path.unlink()
        try:
            response = ch.get(f"/api/documents/{did}/download?version_no=1")
        finally:
            stored_path.write_bytes(relocated_file.read_bytes())
        body = response.get_data()
        response.close()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, b"%PDF-1.4 hi")

    def test_requirement_completes_on_approval(self):
        f, ch = self.login("f1@t"), self.login("chair@t")
        did = self.upload(f).get_json()["document_id"]
        self.post(f, "/api/requirements/1/evidence", json={"document_id": did})
        self.assertEqual(self.login("chair@t").get("/api/requirements").get_json()[0]["status"], "In Progress")
        self.post(ch, "/api/versions/1/approve")
        self.assertEqual(ch.get("/api/requirements").get_json()[0]["status"], "Completed")

    def test_requirement_evidence_can_be_replaced_and_removed(self):
        f1, f2 = self.login("f1@t"), self.login("f2@t")
        original = self.upload(f1, "Original evidence").get_json()["document_id"]
        replacement = self.upload(f1, "Replacement evidence").get_json()["document_id"]
        other_owner_doc = self.upload(f2, "Other faculty evidence").get_json()["document_id"]
        self.post(f1, "/api/requirements/1/evidence", json={"document_id": original})

        self.assertEqual(f1.get("/api/requirements/1/evidence").get_json()[0]["title"], "Original evidence")
        options = f1.get("/api/requirements/1/evidence/options").get_json()
        self.assertIn(replacement, [doc["id"] for doc in options])
        self.assertNotIn(other_owner_doc, [doc["id"] for doc in options])
        denied = f1.put(f"/api/requirements/1/evidence/{original}",
                        json={"document_id": other_owner_doc},
                        headers={"X-CSRF-Token": f1.csrf})
        self.assertEqual(denied.status_code, 403)

        replaced = f1.put(f"/api/requirements/1/evidence/{original}",
                          json={"document_id": replacement},
                          headers={"X-CSRF-Token": f1.csrf})
        self.assertEqual(replaced.status_code, 200)
        self.assertEqual(f1.get("/api/requirements/1/evidence").get_json()[0]["title"], "Replacement evidence")
        self.assertEqual(f1.get("/api/requirements").get_json()[0]["status"], "In Progress")

        removed = f1.delete(f"/api/requirements/1/evidence/{replacement}",
                            headers={"X-CSRF-Token": f1.csrf})
        self.assertEqual(removed.status_code, 200)
        requirement = f1.get("/api/requirements").get_json()[0]
        self.assertEqual(requirement["status"], "Missing Evidence")
        self.assertEqual(requirement["evidence_count"], 0)

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
        self.upload(f, "Intro to IS Syllabus"); uploaded = self.upload(f, "Exam Set")
        exams = f.get("/api/documents?q=Exam").get_json()
        self.assertEqual(len(exams), 1)
        self.assertEqual(exams[0]["latest_version_id"], uploaded.get_json()["version_id"])
        self.assertEqual(len(f.get("/api/documents?semester=1st&document_type=syllabus").get_json()), 2)

    def test_repository_exposes_review_and_signatory_status(self):
        faculty, signer, chair = self.login("f1@t"), self.login("f2@t"), self.login("chair@t")
        uploaded = self.upload(faculty, "Review status").get_json()
        did = uploaded["document_id"]
        self.assertEqual(self.post(chair, f"/api/versions/{uploaded['version_id']}/approve").status_code, 200)
        next_version = self.post(
            faculty, f"/api/documents/{did}/versions",
            data={"file": (io.BytesIO(b"pending update"), "update.pdf")},
            content_type="multipart/form-data",
        ).get_json()
        vid = next_version["version_id"]
        self.assertEqual(self.post(chair, f"/api/versions/{vid}/signatories", json={"signer_id": 3}).status_code, 201)

        chair_doc = next(row for row in chair.get("/api/documents").get_json() if row["id"] == did)
        signer_doc = next(row for row in signer.get("/api/documents").get_json() if row["id"] == did)
        self.assertEqual(chair_doc["latest_status"], "Pending Review")
        self.assertEqual((chair_doc["sig_total"], chair_doc["sig_done"]), (1, 0))
        self.assertTrue(signer_doc["my_pending_signature"])

        self.assertEqual(self.post(signer, f"/api/versions/{vid}/sign").status_code, 200)
        signed_doc = next(row for row in signer.get("/api/documents").get_json() if row["id"] == did)
        self.assertFalse(signed_doc["my_pending_signature"])
        self.assertEqual(signed_doc["sig_done"], 1)


if __name__ == "__main__":
    unittest.main()
