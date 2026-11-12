import hashlib, io, json
from pypdf import PdfReader, PdfWriter
from app.db import get_db
from app.pdf_export import displayed_to_pdf, build_annotated_pdf
from tests.test_foundation import Base


def blank_pdf(rotate=0):
    w = PdfWriter(); p = w.add_blank_page(612, 792)
    if rotate: p.rotate(rotate)
    b = io.BytesIO(); w.write(b); return b.getvalue()


class AnnotationTests(Base):
    def make_doc(self, owner="f1@t", data=None, name="s.pdf"):
        f = self.login(owner)
        r = self.post(f, "/api/documents", data={"title": "Syllabus", "category_id": 1, "school_year": "2026-2027", "semester": "1st",
                      "document_type": "syllabus", "file": (io.BytesIO(data or blank_pdf()), name)}, content_type="multipart/form-data")
        return f, r.get_json()["document_id"], r.get_json()["version_id"]

    def annotate(self, c, vid, **kw):
        body = {"page": 1, "rects": [[0.1, 0.2, 0.3, 0.05]], "quote": "hello", "comment": "fix this"}; body.update(kw)
        return self.post(c, f"/api/versions/{vid}/annotations", json=body)

    def test_visibility_chair_and_owner_only(self):
        f1, did, vid = self.make_doc(); ch, f2, ev = self.login("chair@t"), self.login("f2@t"), self.login("ev@t")
        self.assertEqual(self.annotate(ch, vid).status_code, 201)
        self.assertEqual(len(f1.get(f"/api/versions/{vid}/annotations").get_json()["annotations"]), 1)   # owner sees chair's comment
        self.post(ch, "/api/versions/1/approve")
        j = f2.get(f"/api/versions/{vid}/annotations").get_json()                                           # other faculty: can open, no comments
        self.assertEqual((j["annotations"], j["can_annotate"], j["can_view_comments"]), ([], False, False))
        self.assertEqual(self.annotate(f2, vid).status_code, 403)
        self.assertEqual(ev.get(f"/api/versions/{vid}/view").status_code, 403)                               # evaluator not granted
        with self.app.app_context():
            db = get_db(); db.execute("INSERT INTO access_requests (evaluator_id, document_id, status) VALUES (4,?, 'Approved')", (did,)); db.commit()
        self.assertEqual(ev.get(f"/api/versions/{vid}/view").status_code, 200)
        self.assertEqual(ev.get(f"/api/versions/{vid}/annotations").get_json()["annotations"], [])
        self.assertEqual(self.annotate(ev, vid).status_code, 403)

    def test_pending_version_not_viewable_by_others(self):
        f1, did, vid = self.make_doc(); f2 = self.login("f2@t")
        self.assertEqual(f2.get(f"/api/versions/{vid}/view").status_code, 403)
        self.assertEqual(f2.get(f"/api/documents/{did}/download?version_no=1").status_code, 403)  # pre-existing leak closed

    def test_validation(self):
        ch = self.login("chair@t"); f, did, vid = self.make_doc()
        for bad in ({"rects": []}, {"rects": [[2, 0, 1, 1]]}, {"rects": [[0.1, 0.1, 0, 0.1]]}, {"rects": "x"}, {"page": 0}, {"comment": "x" * 4001}):
            self.assertEqual(self.annotate(ch, vid, **bad).status_code, 400, bad)

    def test_edit_delete_reply_resolve_rules(self):
        f, did, vid = self.make_doc(); ch = self.login("chair@t")
        aid = self.annotate(ch, vid).get_json()["id"]
        self.assertEqual(self.post(f, f"/api/annotations/{aid}/replies", json={"comment": "done"}).status_code, 201)
        self.assertEqual(self.post(f, f"/api/annotations/{aid}/replies", json={"comment": " "}).status_code, 400)
        self.assertEqual(self.post(f, f"/api/annotations/{aid}", json={}, headers={"X-HTTP-Method-Override": "PATCH"}).status_code, 405)
        self.assertEqual(f.patch(f"/api/annotations/{aid}", json={"comment": "x"}, headers={"X-CSRF-Token": f.csrf}).status_code, 403)  # not author
        self.assertEqual(ch.patch(f"/api/annotations/{aid}", json={"comment": "new"}, headers={"X-CSRF-Token": ch.csrf}).get_json()["comment"], "new")
        self.assertEqual(self.post(f, f"/api/annotations/{aid}/resolve", json={"resolved": True}).status_code, 200)
        a = f.get(f"/api/versions/{vid}/annotations").get_json()["annotations"][0]
        self.assertTrue(a["resolved"]); self.assertEqual(len(a["replies"]), 1)
        self.assertEqual(f.delete(f"/api/annotations/{aid}", headers={"X-CSRF-Token": f.csrf}).status_code, 403)  # owner can't delete chair's
        self.assertEqual(ch.delete(f"/api/annotations/{aid}", headers={"X-CSRF-Token": ch.csrf}).status_code, 200)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM annotations").fetchone()[0], 0)  # reply cascaded

    def test_csrf_required(self):
        ch = self.login("chair@t"); f, did, vid = self.make_doc()
        self.assertEqual(ch.post(f"/api/versions/{vid}/annotations", json={"page": 1, "rects": [[.1, .1, .1, .1]]}).status_code, 400)

    def test_file_untouched_and_export(self):
        f, did, vid = self.make_doc(); ch = self.login("chair@t")
        with self.app.app_context():
            path = get_db().execute("SELECT stored_path, sha256 FROM document_versions WHERE id=?", (vid,)).fetchone()
        aid = self.annotate(ch, vid, comment="Missing signatures").get_json()["id"]
        self.post(f, f"/api/annotations/{aid}/replies", json={"comment": "Added"})
        r = ch.get(f"/api/versions/{vid}/annotated.pdf")
        self.assertEqual((r.status_code, r.mimetype), (200, "application/pdf"))
        an = [x.get_object() for x in PdfReader(io.BytesIO(r.data)).pages[0]["/Annots"]]
        self.assertEqual(an[0]["/Subtype"], "/Highlight")
        self.assertIn("Missing signatures", an[0]["/Contents"]); self.assertIn("Added", an[0]["/Contents"])
        q = [float(x) for x in an[0]["/QuadPoints"]]
        self.assertAlmostEqual(q[0], 61.2, 1); self.assertAlmostEqual(q[1], 792 - 0.2 * 792, 1)   # top-left of the rect
        self.assertEqual(len(PdfReader(io.BytesIO(open(path["stored_path"], "rb").read())).pages[0].get("/Annots") or []), 0)  # original clean
        self.assertEqual(hashlib.sha256(open(path["stored_path"], "rb").read()).hexdigest(), path["sha256"])                      # BR-5
        self.assertEqual(f.get(f"/api/versions/{vid}/annotated.pdf").status_code, 200)             # owner may export
        self.assertEqual(self.login("f2@t").get(f"/api/versions/{vid}/annotated.pdf").status_code, 403)

    def test_non_previewable_files(self):
        f, did, vid = self.make_doc(data=b"hello", name="notes.txt")   # converter disabled in tests
        self.assertEqual(f.get(f"/api/versions/{vid}/annotations").get_json()["version"]["preview"]["status"], "unavailable")
        self.assertEqual(f.get(f"/api/versions/{vid}/view").status_code, 409)
        f2, did2, vid2 = self.make_doc(data=b"not really a pdf", name="fake.pdf")
        self.assertEqual(f2.get(f"/api/versions/{vid2}/annotations").get_json()["version"]["preview"]["status"], "unsupported")
        self.assertEqual(f2.get(f"/api/versions/{vid2}/view").status_code, 415)

    def test_rotation_mapping_corners(self):
        box = (0, 0, 612, 792)   # displayed top-left must land on these unrotated corners
        self.assertEqual(displayed_to_pdf(box, 0, 0, 0), (0, 792))
        self.assertEqual(displayed_to_pdf(box, 90, 0, 0), (0, 0))
        self.assertEqual(displayed_to_pdf(box, 180, 0, 0), (612, 0))
        self.assertEqual(displayed_to_pdf(box, 270, 0, 0), (612, 792))
        for r in (0, 90, 180, 270):  # bottom-right of display is the opposite corner of top-left
            a, b = displayed_to_pdf(box, r, 0, 0), displayed_to_pdf(box, r, 1, 1)
            self.assertEqual((abs(a[0] - b[0]), abs(a[1] - b[1])), (612, 792))

    def test_export_rotated_page(self):
        out, skipped = build_annotated_pdf(blank_pdf(90), [dict(page=1, rects=[[0, 0, .5, .5]], text="t", author="a", resolved=False),
                                                           dict(page=9, rects=[[0, 0, .5, .5]], text="x", author="a", resolved=False)])
        self.assertEqual(skipped, 1)
        self.assertEqual(len(PdfReader(io.BytesIO(out)).pages[0]["/Annots"]), 1)
