"""Write annotations into a COPY of a PDF as standard highlight annotations (pypdf).
Anchors are stored as fractions of the *displayed* page (origin top-left, after /Rotate),
so they are converted back to PDF user space here. The stored original is never touched."""
from io import BytesIO


def displayed_to_pdf(box, rotate, u, v):
    """(u,v) in [0,1] of the displayed page, origin top-left -> PDF user-space (x,y)."""
    x0, y0, x1, y1 = box
    W, H = x1 - x0, y1 - y0
    r = rotate % 360
    if r == 0:
        return x0 + u * W, y1 - v * H
    if r == 90:
        return x0 + v * W, y0 + u * H
    if r == 180:
        return x1 - u * W, y0 + v * H
    return x1 - v * W, y1 - u * H  # 270


def build_annotated_pdf(data: bytes, items: list[dict]) -> tuple[bytes, int]:
    """items: [{page, rects, text, author, resolved}]. Returns (pdf_bytes, skipped_count)."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import Highlight
    from pypdf.generic import ArrayObject, FloatObject, NameObject, TextStringObject

    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("This PDF is password-protected.")
    writer = PdfWriter(clone_from=reader)
    skipped = 0
    for it in items:
        n = it["page"] - 1
        if not (0 <= n < len(writer.pages)):
            skipped += 1
            continue
        page = writer.pages[n]
        cb = page.cropbox
        box = (float(cb.left), float(cb.bottom), float(cb.right), float(cb.top))
        rot = int(page.rotation or 0)
        quads, xs, ys = [], [], []
        for x, y, w, h in it["rects"]:
            for u, v in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):  # TL, TR, BL, BR
                px, py = displayed_to_pdf(box, rot, u, v)
                quads += [round(px, 3), round(py, 3)]; xs.append(px); ys.append(py)
        a = Highlight(rect=(min(xs), min(ys), max(xs), max(ys)),
                      quad_points=ArrayObject([FloatObject(q) for q in quads]),
                      highlight_color="c8c8c8" if it["resolved"] else "ffe14d")
        a[NameObject("/Contents")] = TextStringObject(it["text"])
        a[NameObject("/T")] = TextStringObject(it["author"])
        writer.add_annotation(page_number=n, annotation=a)
    out = BytesIO()
    writer.write(out)
    return out.getvalue(), skipped
