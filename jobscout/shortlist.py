"""A shortlist as a PDF you can send to someone.

The app's answer to "can I share this with a friend" is a file: the boards fight
every other route (login walls, session-bound links, client-rendered pages), and
a PDF opens anywhere.

Design: the app's own paper and ink, the mark from the app icon, one card per
advert with a clay rule down its left edge, and a link that is a short label
rather than the raw URL. A wrapped URL cannot be a reliable link -- reportlab
anchors each paragraph fragment separately, so only the first line comes out
clickable -- and a 130-character board URL reads as noise on paper anyway.

Reportlab's built-in fonts are WinAnsi, so text is folded to cp1252. That keeps
German umlauts and the euro sign and replaces anything else with a question mark
rather than failing the whole document.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

INK = colors.HexColor("#26201a")
MUTED = colors.HexColor("#746a60")
LINE = colors.HexColor("#e6dbcf")
ACCENT = colors.HexColor("#a8492b")
PAPER = colors.HexColor("#fffdfa")

LOGO = Path(__file__).resolve().parent / "web" / "icon.png"
MARGIN = 18 * mm


def _safe(text: str) -> str:
    """cp1252 or bust: reportlab's default fonts cannot draw anything else."""
    return (text or "").encode("cp1252", "replace").decode("cp1252")


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_safe(text)).replace("\n", "<br/>"), style)


def _money(row: dict) -> str:
    return (row.get("salary") or row.get("salary_raw") or "").strip()


def _deadline(row: dict) -> str:
    when = (row.get("deadline_at") or "").strip()
    days = row.get("closes_in_days")
    if not when:
        return ""
    if days is None:
        return when
    if days < 0:
        return "closed " + when
    if days == 0:
        return "today (" + when + ")"
    return f"{when} ({days} days)"


def _host(url: str) -> str:
    try:
        from urllib.parse import urlsplit
        host = urlsplit(url).netloc
        return host[4:] if host.startswith("www.") else host
    except Exception:                                    # noqa: BLE001
        return "the board"


def _styles() -> dict:
    return {
        "word": ParagraphStyle("word", fontName="Helvetica-Bold", fontSize=17,
                               leading=19, textColor=INK),
        "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=13,
                                leading=15, textColor=INK, spaceBefore=1),
        "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=9.5,
                              leading=12, textColor=MUTED),
        "head": ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=12,
                               leading=14.5, textColor=INK),
        "meta": ParagraphStyle("meta", fontName="Helvetica", fontSize=9.5,
                               leading=12.5, textColor=MUTED),
        "fact": ParagraphStyle("fact", fontName="Helvetica", fontSize=8.8,
                               leading=11.5, textColor=INK),
        "link": ParagraphStyle("link", fontName="Helvetica-Bold", fontSize=8.6,
                               leading=11.5, textColor=ACCENT),
        "note": ParagraphStyle("note", fontName="Helvetica-Oblique", fontSize=9,
                               leading=12, textColor=INK),
        "num": ParagraphStyle("num", fontName="Helvetica-Bold", fontSize=10,
                              leading=12, textColor=ACCENT),
        "empty": ParagraphStyle("empty", fontName="Helvetica", fontSize=9.5,
                                leading=13, textColor=MUTED),
    }


def _fact_paragraph(row: dict, st: dict) -> Paragraph | None:
    parts = []
    salary = _money(row)
    contract = (row.get("contract") or "").strip()
    deadline = _deadline(row)
    label = '<font color="#746a60">{}</font> {}'
    if salary:
        parts.append(label.format("Salary", escape(_safe(salary))))
    if contract:
        parts.append(label.format("Contract", escape(_safe(contract))))
    if deadline:
        parts.append(label.format("Deadline", escape(_safe(deadline))))
    if not parts:
        return None
    return Paragraph("&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts), st["fact"])


def _advert(row: dict, number: int, st: dict) -> Table:
    """One advert as a card: clay rule on the left, facts and link on the right."""
    right: list = [_p(row.get("title") or "Untitled", st["head"])]
    place = " · ".join(x for x in ((row.get("company") or "").strip(),
                                   (row.get("location") or "").strip()) if x)
    if place:
        right.append(_p(place, st["meta"]))
    facts = _fact_paragraph(row, st)
    if facts is not None:
        right.append(Spacer(1, 3))
        right.append(facts)
    url = (row.get("url") or "").strip()
    if url:
        href = escape(url, {'"': "&quot;"})
        right.append(Spacer(1, 4))
        right.append(Paragraph(
            f'<u><link href="{href}" color="#a8492b">'
            f'{escape(_safe(_host(url)))} · open advert</link></u>', st["link"]))
    if (row.get("note") or "").strip():
        right.append(Spacer(1, 4))
        right.append(_p("Note: " + row["note"].strip(), st["note"]))

    table = Table([[Paragraph(str(number), st["num"]), right]],
                  colWidths=[12 * mm, None])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), PAPER),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
        ("LEFTPADDING", (0, 0), (0, 0), 7),
        ("LEFTPADDING", (1, 0), (1, 0), 6),
        ("RIGHTPADDING", (-1, 0), (-1, 0), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return table


def _header(rows: list[dict], profile: str, today: date, st: dict,
            name: str) -> list:
    count = f"{len(rows)} advert" + ("" if len(rows) == 1 else "s")
    bits = [f"{profile} profile"] if profile else []
    bits += [today.strftime("%d %B %Y"), count]
    word = [
        _p("jobscout", st["word"]),
        _p(name or "Shortlist", st["title"]),
        _p(" · ".join(bits), st["sub"]),
    ]
    logo = Image(str(LOGO), width=11 * mm, height=11 * mm) if LOGO.exists() else ""
    # The number column and the logo column are the same width, and the text cell
    # gets the same 6pt padding, so header and card titles share one left edge.
    head = Table([[logo, word]], colWidths=[12 * mm, None])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    rule = Table([[""]], colWidths=[None], rowHeights=[1.6 * mm])
    rule.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [head, Spacer(1, 4 * mm), rule, Spacer(1, 6 * mm)]


def build_pdf(rows: list[dict], profile: str, out_path: Path,
              today: date | None = None, name: str = "Shortlist") -> Path:
    """Write the shortlist. Returns the path, which the caller may open."""
    today = today or date.today()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    st = _styles()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN, 14 * mm, A4[0] - MARGIN, 14 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 9 * mm,
                          _safe(f"jobscout · {name} · {today.strftime('%d %B %Y')}"))
        canvas.drawRightString(A4[0] - MARGIN, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(str(out_path), pagesize=A4,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN, bottomMargin=20 * mm,
                          title=_safe(f"jobscout · {name} · {profile}"),
                          author="jobscout")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=footer)])

    story: list = _header(rows, profile, today, st, name)
    if not rows:
        story.append(_p("This shortlist is empty. Press the + on a card in the app "
                        "to add an advert.", st["empty"]))
    for i, row in enumerate(rows, 1):
        story.append(KeepTogether([_advert(row, i, st), Spacer(1, 5 * mm)]))

    doc.build(story)
    return out_path


def default_path(root: Path) -> Path:
    """Where a shared file belongs: Downloads if there is one, else the home."""
    downloads = Path.home() / "Downloads"
    base = downloads if downloads.is_dir() else Path.home()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return base / f"jobscout-shortlist-{stamp}.pdf"
