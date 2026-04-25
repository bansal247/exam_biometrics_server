"""CSV and PDF generation, with optional embedded images for matching reports."""

import csv
import html
import io
import base64

from PIL import Image as PilImage
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def generate_csv(headers: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def generate_pdf(title: str, headers: list[str], rows: list[list]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    table_data = [headers] + rows
    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
    ]))
    elements.append(table)
    doc.build(elements)
    return buf.getvalue()


# ── Matching reports with embedded images ─────────────────────────────────────

def _bytes_to_rl_image(raw: bytes | None, w_cm: float = 1.2, h_cm: float = 1.5):
    """Convert raw image bytes (JPEG, JPG, PNG, BMP, TIFF) to ReportLab Image.
    Default 1.2 × 1.5 cm fits inside a 1.5 cm column with 2 pt padding on each side.
    """
    if not raw:
        return ""

    try:
        pil = PilImage.open(io.BytesIO(raw))
        pil.load()

        if getattr(pil, "is_animated", False):
            pil.seek(0)

        if pil.mode not in ("RGB", "L"):
            pil = pil.convert("RGB")
        elif pil.mode == "L":
            pil = pil.convert("RGB")

        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=60)
        buf.seek(0)

        return RLImage(buf, width=w_cm * cm, height=h_cm * cm)

    except Exception as e:
        print("Image conversion error:", e)
        return "err"


def generate_matching_csv(
    headers: list[str],
    rows: list[list],
    image_col_indices: list[int],
) -> bytes:
    """
    Generate CSV for matching report.
    Cells at image_col_indices contain raw bytes or None; they are base64-encoded.
    UTF-8 BOM is prepended so Excel opens the file without garbled characters.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        out = []
        for i, cell in enumerate(row):
            if i in image_col_indices:
                if cell and isinstance(cell, (bytes, bytearray)):
                    out.append(base64.b64encode(cell).decode())
                else:
                    out.append("")
            else:
                out.append(str(cell) if cell is not None else "")
        writer.writerow(out)
    return buf.getvalue().encode("utf-8-sig")


def generate_matching_pdf(
    title: str,
    headers: list[str],
    rows: list[list],
    image_col_indices: list[int],
    exam_name: str | None = None,
    col_widths: list[float] | None = None,
) -> bytes:
    """
    Generate PDF for matching report with embedded thumbnail images.
    col_widths: list of widths in cm, one per column. If None, equal distribution is used.
    """
    buf = io.BytesIO()
    left_margin = right_margin = 0.8 * cm
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=left_margin, rightMargin=right_margin,
        topMargin=1.2 * cm, bottomMargin=1.2 * cm,
    )
    styles = getSampleStyleSheet()

    heading = f"{html.escape(exam_name)} — {title}" if exam_name else title
    elements = [Paragraph(heading, styles["Title"]), Spacer(1, 6)]

    img_set = set(image_col_indices)
    n_cols = len(headers)

    # Resolve column widths in ReportLab points
    if col_widths is not None:
        rl_col_widths = [w * cm for w in col_widths]
    else:
        page_w, _ = landscape(A4)
        usable_w = page_w - left_margin - right_margin
        img_w = 1.5 * cm
        n_img = len(image_col_indices)
        n_text = n_cols - n_img
        text_w = (usable_w - n_img * img_w) / max(n_text, 1)
        rl_col_widths = [img_w if i in img_set else text_w for i in range(n_cols)]

    # Paragraph styles
    header_style = ParagraphStyle(
        "MatchHeader",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
        textColor=colors.white,
        fontName="Helvetica-Bold",
        wordWrap="LTR",
    )
    cell_style = ParagraphStyle(
        "MatchCell",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
        wordWrap="LTR",
    )

    # Header row — wrap each header in Paragraph so long names break
    header_row = [Paragraph(html.escape(h), header_style) for h in headers]

    table_data = [header_row]
    for row in rows:
        out = []
        for i, cell in enumerate(row):
            if i in img_set:
                out.append(_bytes_to_rl_image(cell))
            else:
                text = html.escape(str(cell) if cell is not None else "")
                out.append(Paragraph(text, cell_style))
        table_data.append(out)

    table = Table(table_data, repeatRows=1, colWidths=rl_col_widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 2),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 2),
    ]))
    elements.append(table)
    doc.build(elements)
    return buf.getvalue()
