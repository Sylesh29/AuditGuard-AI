"""Render a Report as a PDF. Every string is XML-escaped before it reaches reportlab."""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from agents.narrator import Report

BLUE = HexColor("#185FA5")
BLUE_LIGHT = HexColor("#E6F1FB")
RED = HexColor("#C0392B")
RED_LIGHT = HexColor("#FDEDEC")
AMBER = HexColor("#B9770E")
AMBER_LIGHT = HexColor("#FEF9E7")
GRAY = HexColor("#7F8C8D")
LIGHT_GRAY = HexColor("#F8F9FA")
TEXT = HexColor("#2C3E50")
BORDER = HexColor("#E0E0E0")

SEVERITY_WORD = {"HIGH": "Critical", "MED": "Moderate", "LOW": "Minor"}
SEVERITY_COLORS = {"HIGH": (RED, RED_LIGHT), "MED": (AMBER, AMBER_LIGHT), "LOW": (GRAY, LIGHT_GRAY)}
ACTION_WORD = {"corrected": "Correction proposed", "flagged": "Flagged",
               "escalated": "Escalated", "none": "None"}

STYLES = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=18, textColor=BLUE,
                            leading=22, spaceAfter=6),
    "meta": ParagraphStyle("meta", fontName="Helvetica", fontSize=9, textColor=TEXT, leading=13),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, textColor=BLUE,
                         spaceBefore=12, spaceAfter=6, leading=16),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, textColor=TEXT,
                           leading=14, spaceAfter=4),
    "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=10, textColor=TEXT,
                             leading=14, leftIndent=14, bulletIndent=2, spaceAfter=4),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8, textColor=TEXT, leading=10),
    "head": ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=8, textColor=white,
                           leading=10),
    "small": ParagraphStyle("small", fontName="Helvetica-Oblique", fontSize=8, textColor=GRAY,
                            leading=11),
}


def _p(text: object, style: str = "body") -> Paragraph:
    return Paragraph(escape(str(text)), STYLES[style])


def _labelled(label: str, value: object) -> Paragraph:
    return Paragraph(f"<b>{escape(label)}:</b> {escape(str(value))}", STYLES["meta"])


def _heading(text: str) -> list:
    return [Spacer(1, 4), HRFlowable(width="100%", thickness=1, color=BLUE_LIGHT), _p(text, "h2")]


def _footer(reference_id: str):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(GRAY)
        canvas.drawString(0.75 * inch, 0.4 * inch, f"AuditGuard AI | {reference_id} | Confidential")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.4 * inch, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def _findings_table(findings: list[dict], width: float) -> Table:
    widths = [0.45, 0.55, 1.15, 0.7, 0.5, 0.95]
    widths.append(width / inch - sum(widths))
    header = ["Rank", "ID", "Issue", "Severity", "Rows", "Action", "Why it matters"]
    rows = [[_p(h, "head") for h in header]]
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, f in enumerate(findings, start=1):
        rows.append([
            _p(f["rank"], "cell"), _p(f["finding_id"], "cell"), _p(f["issue"], "cell"),
            _p(SEVERITY_WORD[f["severity"]], "cell"), _p(f["rows"], "cell"),
            _p(ACTION_WORD[f["action"]], "cell"), _p(f["reason"], "cell"),
        ])
        fg, bg = SEVERITY_COLORS[f["severity"]]
        style.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GRAY if i % 2 == 0 else white))
        style.append(("BACKGROUND", (3, i), (3, i), bg))
        style.append(("TEXTCOLOR", (3, i), (3, i), fg))
    table = Table(rows, colWidths=[w * inch for w in widths], repeatRows=1)
    table.setStyle(TableStyle(style))
    return table


def _signature_block(width: float) -> Table:
    table = Table(
        [["Signed: ____________________________", "Title: ____________________________"],
         ["Date: _____________________________", "Facility: _________________________"]],
        colWidths=[width / 2, width / 2],
    )
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), TEXT),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GRAY),
    ]))
    return table


PDF_MAX_FINDINGS = 1000  # beyond this, the table points to findings.csv (never silently)


def _bullets(items: list[str]) -> list:
    return [Paragraph(item, STYLES["bullet"], bulletText="•") for item in items] or [_p("None.")]


def render_pdf(report: Report) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.8 * inch, bottomMargin=0.75 * inch,
        title=f"Data Integrity Correction Summary {report.reference_id}", author="AuditGuard AI",
    )
    s = report.stats
    story: list = [
        _p("Data Integrity Correction Summary", "title"),
        _labelled("Reference ID", report.reference_id),
        _labelled("Dataset", report.dataset_name),
        _labelled("Source file SHA-256", report.source_sha256),
        _labelled("Generated", report.generated_at.strftime("%B %d, %Y %H:%M UTC")),
        _labelled("Records scanned", report.rows_scanned),
        Spacer(1, 6),
    ]

    story += _heading("Executive Summary")
    story.append(_p(report.executive_summary))
    story.append(_p(
        f"Findings: {s['total']} ({s['HIGH']} critical, {s['MED']} moderate, {s['LOW']} minor). "
        f"Actions: {s['corrected']} corrections proposed, {s['escalated']} escalated, "
        f"{s['flagged']} flagged. Change-log entries: {report.change_log_entries}."))

    story += _heading(f"Findings and Actions ({len(report.findings)})")
    if not report.findings:
        story.append(_p("No findings."))
    else:
        shown = report.findings[:PDF_MAX_FINDINGS]
        story.append(_findings_table(shown, doc.width))
        if len(report.findings) > len(shown):
            story.append(_p(
                f"This table shows the {len(shown)} highest-ranked of {len(report.findings)} "
                "findings. All findings, with the same IDs and ranks, are listed in findings.csv, "
                f"which is part of this report package ({report.reference_id}).", "small"))

    story += _heading(f"Open Items Requiring Human Sign-Off ({len(report.open_items)})")
    story += _bullets([
        f"<b>{escape(i['finding_id'])}</b> ({escape(i['action'])}, lot "
        f"{escape(', '.join(i['lots']))}, {escape(i['issue'])}): {escape(i['instruction'])}"
        for i in report.open_items[:PDF_MAX_FINDINGS]])
    if len(report.open_items) > PDF_MAX_FINDINGS:
        story.append(_p(f"{len(report.open_items) - PDF_MAX_FINDINGS} more open items are "
                        "listed in findings.csv.", "small"))

    story += _heading(f"Proposed Corrections ({len(report.corrections)})")
    story += _bullets([
        f"<b>{escape(c['finding_id'])}</b> (lot {escape(', '.join(c['lots']))}): "
        f"{escape(c['description'])} {escape(c['reason'])}"
        for c in report.corrections[:PDF_MAX_FINDINGS]])
    if len(report.corrections) > PDF_MAX_FINDINGS:
        story.append(_p(f"{len(report.corrections) - PDF_MAX_FINDINGS} more corrections are "
                        "listed in findings.csv and changelog.csv.", "small"))
    story.append(_p(
        "The uploaded source file was not modified. Each proposed change, with its original "
        "value, is listed in the change log (changelog.csv) issued with this report.", "small"))

    if report.review_suggestions:
        story += _heading(f"Advisory Ranking Suggestions ({len(report.review_suggestions)})")
        story.append(_p("A second-opinion review suggested these rank changes. The ranking above "
                        "was not changed; consider them when prioritising work.", "small"))
        story += _bullets([
            f"<b>{escape(x['finding_id'])}</b>: rank {x['rank']} to {x['suggested_rank']}. "
            f"{escape(x['reason'])}" for x in report.review_suggestions])

    story += _heading("Scope and Configuration")
    if report.skipped_checks:
        story.append(_p("Checks that could not run on this file:"))
        story += _bullets([escape(c) for c in report.skipped_checks])
    if report.notes:
        story.append(_p("About the uploaded file:"))
        story += _bullets([escape(n) for n in report.notes])
    story += [_labelled(k, v) for k, v in report.configuration.items()]
    story.append(Spacer(1, 4))
    story.append(_p(report.disclaimer, "small"))
    if report.summary_source == "template":
        story.append(_p("The executive summary was generated from a fixed template.", "small"))

    story.append(KeepTogether([
        *_heading("Certification"),
        _p(report.certification),
        Spacer(1, 10),
        _signature_block(doc.width),
    ]))

    footer = _footer(report.reference_id)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
