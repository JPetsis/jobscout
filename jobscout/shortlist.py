"""A shortlist as a PDF you can send to someone.

The app's answer to "can I share this with a friend" is a file: the boards fight
every other route (login walls, session-bound links, client-rendered pages), and
a PDF opens anywhere. It is deliberately plain: one page per handful of adverts,
the facts that decide, the link that still works, and the note you wrote.

Reportlab's built-in fonts are WinAnsi, so text is folded to cp1252. That keeps
German umlauts and the euro sign and replaces anything else with a question
mark rather than failing the whole document.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate,
                                Paragraph, Spacer)

INK = colors.HexColor("#2b2119")
MUTED = colors.HexColor("#6f6156")
LINE = colors.HexColor("#e3d8cb")
ACCENT = colors.HexColor("#a8492b")
PAPER = colors.HexColor("#fffdfa")


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
        return "Closes " + when
    if days < 0:
        return "Closed " + when
    if days == 0:
        return "Closes today (" + when + ")"
    return f"Closes {when} ({days} days)"


def _host(url: str) -> str:
    try:
        from urllib.parse import urlsplit
        host = urlsplit(url).netloc
        return host[4:] if host.startswith("www.") else host
    except Exception:                                    # noqa: BLE001
        return "link"


def build_pdf(rows: list[dict], profile: str, out_path: Path,
              today: date | None = None) -> Path:
    """Write the shortlist. Returns the path, which the caller may open."""
    today = today or date.today()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    title = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=20,
                           leading=24, textColor=INK, spaceAfter=2)
    kicker = ParagraphStyle("kicker", fontName="Helvetica-Bold", fontSize=8,
                            leading=10, textColor=ACCENT, spaceAfter=6)
    lede = ParagraphStyle("lede", fontName="Helvetica", fontSize=9.5, leading=13,
                          textColor=MUTED, spaceAfter=14)
    head = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=12.5,
                          leading=15, textColor=INK, spaceAfter=3)
    sub = ParagraphStyle("sub", fontName="Helvetica", fontSize=9.5, leading=12.5,
                         textColor=MUTED, spaceAfter=4)
    fact = ParagraphStyle("fact", fontName="Helvetica", fontSize=9, leading=12,
                          textColor=INK, spaceAfter=2)
    link = ParagraphStyle("link", fontName="Helvetica", fontSize=8, leading=11,
                          textColor=ACCENT, spaceAfter=2)
    note = ParagraphStyle("note", fontName="Helvetica-Oblique", fontSize=9,
                          leading=12, textColor=INK, spaceBefore=3, spaceAfter=2)
    number = ParagraphStyle("number", fontName="Helvetica-Bold", fontSize=9,
                            leading=11, textColor=ACCENT)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.6)
        canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9 * mm,
                          _safe(f"jobscout · shortlist · {today.isoformat()}"))
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"{doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(str(out_path), pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm,
                          topMargin=18 * mm, bottomMargin=20 * mm,
                          title="jobscout shortlist", author="jobscout")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=footer)])

    story: list = [
        _p("JOBSCOUT · SHORTLIST", kicker),
        _p(f"{len(rows)} advert{'s' if len(rows) != 1 else ''} for {profile}", title),
        _p(f"Picked on {today.strftime('%d %B %Y')}. Links go to the board the advert "
           f"was found on; the salary and the deadline are what the advert itself "
           f"states.", lede),
    ]

    for i, row in enumerate(rows, 1):
        block: list = [
            _p(f"{i}. {row.get('title') or 'Untitled'}", head),
            _p(" · ".join(x for x in ((row.get("company") or "").strip(),
                                      (row.get("location") or "").strip()) if x), sub),
        ]
        facts = [f for f in (_money(row), (row.get("contract") or "").strip(),
                             _deadline(row)) if f]
        if facts:
            block.append(_p("  ·  ".join(facts), fact))
        url = (row.get("url") or "").strip()
        if url:
            block.append(Paragraph(
                f'<link href="{escape(url, {"\"": "&quot;"})}" color="#a8492b">'
                f'{escape(_safe(_host(url)))}</link>'
                f'<font color="#6f6156">  {escape(_safe(url))}</font>', link))
        if (row.get("note") or "").strip():
            block.append(_p("Your note: " + row["note"].strip(), note))
        block.append(Spacer(1, 10))
        story.append(KeepTogether(block))

    doc.build(story)
    return out_path


def default_path(root: Path) -> Path:
    """Where a shared file belongs: Downloads if there is one, else the home."""
    downloads = Path.home() / "Downloads"
    base = downloads if downloads.is_dir() else Path.home()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return base / f"jobscout-shortlist-{stamp}.pdf"
