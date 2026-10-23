"""Word document rendering with python-docx."""
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

NAVY = RGBColor(0x1F, 0x3A, 0x5F)


def _shade(cell, hex_fill: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def _page_number_footer(section) -> None:
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run("Page ")
    run = p.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


def _table(doc, block) -> None:
    cols, rows = block["columns"], block["rows"]
    t = doc.add_table(rows=1, cols=len(cols))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, c in enumerate(cols):
        cell = t.rows[0].cells[i]
        cell.text = ""
        r = cell.paragraphs[0].add_run(c)
        r.bold, r.font.color.rgb = True, RGBColor(0xFF, 0xFF, 0xFF)
        _shade(cell, "1F3A5F")
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        is_total = block.get("total_row") and ri >= len(rows) - 3
        for i, v in enumerate(row):
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(str(v))
            run.bold = bool(is_total)
            if ri % 2 == 1:
                _shade(cells[i], "EEF2F7")
    doc.add_paragraph()


def render_docx(path: Path, title: str, doc_type: str, summary: str, assumptions: list[str], blocks: list[dict]) -> None:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    for lvl in ("Heading 1", "Heading 2"):
        doc.styles[lvl].font.color.rgb = NAVY
        doc.styles[lvl].font.name = "Calibri"

    # Title block
    p = doc.add_paragraph()
    r = p.add_run(title)
    r.bold, r.font.size, r.font.color.rgb = True, Pt(26), NAVY
    p = doc.add_paragraph()
    r = p.add_run(f"{doc_type.title()}  |  {date.today():%d %B %Y}  |  Prepared by Autonomous Document Agent")
    r.italic, r.font.size = True, Pt(10)
    if summary:
        doc.add_paragraph(summary)

    for b in blocks:
        doc.add_heading(b["heading"], level=1)
        if b["kind"] == "section":
            for para in b.get("paragraphs", []):
                doc.add_paragraph(para)
            for item in b.get("bullets", []):
                doc.add_paragraph(item, style="List Bullet")
        else:
            if b.get("intro"):
                doc.add_paragraph(b["intro"])
            _table(doc, b)

    if assumptions:
        doc.add_heading("Assumptions", level=1)
        for a in assumptions:
            doc.add_paragraph(a, style="List Bullet")

    _page_number_footer(doc.sections[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
