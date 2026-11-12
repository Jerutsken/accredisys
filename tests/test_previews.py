import io, os, shutil, unittest
from pathlib import Path
from unittest import mock
from pypdf import PdfReader, PdfWriter
from app import convert
from app.db import get_db
from tests.test_foundation import Base


def blank_pdf():
    w = PdfWriter(); w.add_blank_page(612, 792); b = io.BytesIO(); w.write(b); return b.getvalue()


def fake_convert(src, ext, outdir, timeout=120):
    out = Path(outdir); out.mkdir(parents=True, exist_ok=True); p = out / (Path(src).stem + ".pdf"); p.write_bytes(blank_pdf()); return p


class PreviewBase(Base):
    def setUp(self):
        super().setUp()
        self.app.config.update(CONVERT_ENABLED=True, CONVERT_SYNC=True)

    def upload(self, who="f1@t", name="report.docx", data=b"PK fake docx"):
        f = self.login(who)
        r = self.post(f, "/api/documents", data={"title": "Report", "category_id": 1, "school_year": "2026-2027", "semester": "1st",
                      "document_type": "report", "file": (io.BytesIO(data), name)}, content_type="multipart/form-data")
        return f, r.get_json()["version_id"]

    def status(self, c, vid):
        return c.get(f"/api/versions/{vid}/annotations").get_json()["version"]["preview"]["status"]

    def comment(self, c, vid):
        return self.post(c, f"/api/versions/{vid}/annotations", json={"page": 1, "rects": [[.1, .1, .2, .05]], "quote": "q", "comment": "note"})


class PreviewLogicTests(PreviewBase):
    def test_docx_gets_preview_comments_and_export(self):
        with mock.patch("app.convert.soffice_path", return_value="/x/soffice"), mock.patch("app.convert.convert_file", fake_convert):
            f, vid = self.upload(); ch = self.login("chair@t")
            j = ch.get(f"/api/versions/{vid}/annotations").get_json()
            self.assertEqual((j["version"]["preview"]["status"], j["version"]["preview"]["converted"], j["version"]["preview"]["source"]), ("ready", True, "docx"))
            self.assertEqual(ch.get(f"/api/versions/{vid}/view").mimetype, "application/pdf")
            self.assertEqual(self.comment(ch, vid).status_code, 201)
            r = ch.get(f"/api/versions/{vid}/annotated.pdf")
            self.assertEqual(len(PdfReader(io.BytesIO(r.data)).pages[0]["/Annots"]), 1)
            with self.app.app_context():   # original untouched, preview hash recorded
                row = get_db().execute("SELECT stored_path, sha256 FROM document_versions WHERE id=?", (vid,)).fetchone()
                self.assertEqual(open(row["stored_path"], "rb").read(), b"PK fake docx")
                self.assertTrue(get_db().execute("SELECT sha256 FROM version_previews WHERE version_id=?", (vid,)).fetchone()["sha256"])

    def test_unavailable_then_retry(self):
        with mock.patch("app.convert.soffice_path", return_value=None):
            f, vid = self.upload(); ch = self.login("chair@t")
            self.assertEqual(self.status(ch, vid), "unavailable")
            self.assertEqual(self.comment(ch, vid).status_code, 400)           # nothing to anchor comments to
            self.assertTrue(ch.get(f"/api/versions/{vid}/annotations").get_json()["version"]["preview"]["can_retry"])
        with mock.patch("app.convert.soffice_path", return_value="/x"), mock.patch("app.convert.convert_file", fake_convert):
            self.assertEqual(self.post(self.login("f2@t"), f"/api/versions/{vid}/preview/retry", json={}).status_code, 403)
            self.assertEqual(self.post(ch, f"/api/versions/{vid}/preview/retry", json={}).get_json()["status"], "ready")

    def test_failed_conversion_is_reported_and_retryable(self):
        def boom(*a, **k): raise RuntimeError("corrupt file")
        with mock.patch("app.convert.soffice_path", return_value="/x"), mock.patch("app.convert.convert_file", boom):
            f, vid = self.upload(); self.assertEqual(self.status(f, vid), "failed")
            self.assertEqual(self.login("chair@t").get(f"/api/versions/{vid}/view").status_code, 409)
            self.assertEqual(self.status(f, vid), "failed")                   # not retried silently on every view
        with mock.patch("app.convert.soffice_path", return_value="/x"), mock.patch("app.convert.convert_file", fake_convert):
            self.assertEqual(self.post(f, f"/api/versions/{vid}/preview/retry", json={}).get_json()["status"], "ready")

    def test_ready_preview_is_never_rebuilt_once_commented(self):
        with mock.patch("app.convert.soffice_path", return_value="/x"), mock.patch("app.convert.convert_file", fake_convert):
            f, vid = self.upload(); ch = self.login("chair@t"); self.status(ch, vid); self.comment(ch, vid)
            self.assertEqual(self.post(ch, f"/api/versions/{vid}/preview/retry", json={}).status_code, 400)

    def test_pending_then_stale_requeue(self):
        calls = []
        with mock.patch("app.convert.soffice_path", return_value="/x"), mock.patch("app.convert._enqueue", lambda *a: calls.append(a)):
            self.app.config["CONVERT_SYNC"] = False
            f, vid = self.upload(); ch = self.login("chair@t")
            self.assertEqual(self.status(ch, vid), "pending"); self.assertEqual(len(calls), 1)
            self.assertEqual(ch.get(f"/api/versions/{vid}/view").status_code, 409)
            self.status(ch, vid); self.assertEqual(len(calls), 1)             # not queued twice
            with self.app.app_context():
                db = get_db(); db.execute("UPDATE version_previews SET updated_at=datetime('now','-30 minutes')"); db.commit()
            self.status(ch, vid); self.assertEqual(len(calls), 2)             # stale job (e.g. server restarted) is re-queued

    def test_access_rules_apply_to_previews(self):
        with mock.patch("app.convert.soffice_path", return_value="/x"), mock.patch("app.convert.convert_file", fake_convert):
            f, vid = self.upload()
            self.assertEqual(self.login("f2@t").get(f"/api/versions/{vid}/view").status_code, 403)   # pending, not the owner's
            self.assertEqual(self.login("ev@t").get(f"/api/versions/{vid}/view").status_code, 403)

    def test_preview_included_in_backup(self):
        with mock.patch("app.convert.soffice_path", return_value="/x"), mock.patch("app.convert.convert_file", fake_convert):
            f, vid = self.upload(); ch = self.login("chair@t"); self.status(ch, vid)
            path = self.post(ch, "/api/admin/backups").get_json()["path"]
            self.assertTrue(list((Path(path) / "uploads" / "previews").glob("*.pdf")))

    def test_system_status_endpoint(self):
        ch = self.login("chair@t")
        with mock.patch("app.convert.soffice_path", return_value=None):
            self.assertEqual(ch.get("/api/admin/system").get_json()["converter"], {"available": False})
        self.assertEqual(self.login("f1@t").get("/api/admin/system").status_code, 403)


@unittest.skipUnless(convert.soffice_path(), "LibreOffice not installed")
class RealLibreOfficeTests(PreviewBase):
    def test_real_docx_xlsx_pptx(self):
        try:
            import docx, openpyxl, pptx
        except ImportError:
            self.skipTest("python-docx / openpyxl / python-pptx not installed")
        buf = io.BytesIO(); d = docx.Document(); d.add_paragraph("Accreditation evidence for the Department Chair."); d.save(buf)
        wb = openpyxl.Workbook(); ws = wb.active; ws.append([f"Col{i}" for i in range(30)]); xb = io.BytesIO(); wb.save(xb)
        pr = pptx.Presentation(); pr.slides.add_slide(pr.slide_layouts[0]).shapes.title.text = "Overview"; pb = io.BytesIO(); pr.save(pb)
        ch = self.login("chair@t")
        for name, data in (("a.docx", buf.getvalue()), ("b.xlsx", xb.getvalue()), ("c.pptx", pb.getvalue())):
            f, vid = self.upload(name=name, data=data)
            self.assertEqual(self.status(ch, vid), "ready", name)
            pdf = ch.get(f"/api/versions/{vid}/view").data
            pages = PdfReader(io.BytesIO(pdf)).pages
            self.assertGreaterEqual(len(pages), 1, name)
            if name.endswith("xlsx"):
                self.assertEqual(len(pages), 1)                                # one page per sheet, not sliced
            self.assertEqual(self.comment(ch, vid).status_code, 201)


if __name__ == "__main__":
    unittest.main()
