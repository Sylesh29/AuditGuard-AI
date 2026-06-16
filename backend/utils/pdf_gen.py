"""Convert narrator markdown output to a professional PDF using reportlab."""
import io
import re
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, black, white
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

BLUE = HexColor('#185FA5')
BLUE_DARK = HexColor('#0C447C')
BLUE_LIGHT = HexColor('#E6F1FB')
RED = HexColor('#C0392B')
RED_LIGHT = HexColor('#FDEDEC')
AMBER = HexColor('#E67E22')
AMBER_LIGHT = HexColor('#FEF9E7')
GREEN = HexColor('#27AE60')
GREEN_LIGHT = HexColor('#EAFAF1')
GRAY = HexColor('#7F8C8D')
LIGHT_GRAY = HexColor('#F8F9FA')
TEXT = HexColor('#2C3E50')
BORDER = HexColor('#E0E0E0')


def _build_styles():
    styles = getSampleStyleSheet()
    custom = {}

    custom['title'] = ParagraphStyle(
        'DocTitle',
        fontName='Helvetica-Bold',
        fontSize=18,
        textColor=BLUE,
        spaceAfter=4,
        leading=22
    )
    custom['subtitle'] = ParagraphStyle(
        'DocSubtitle',
        fontName='Helvetica',
        fontSize=10,
        textColor=GRAY,
        spaceAfter=2
    )
    custom['h2'] = ParagraphStyle(
        'H2',
        fontName='Helvetica-Bold',
        fontSize=13,
        textColor=BLUE,
        spaceBefore=14,
        spaceAfter=6,
        leading=16
    )
    custom['body'] = ParagraphStyle(
        'Body',
        fontName='Helvetica',
        fontSize=10,
        textColor=TEXT,
        leading=14,
        spaceAfter=4
    )
    custom['bullet'] = ParagraphStyle(
        'Bullet',
        fontName='Helvetica',
        fontSize=10,
        textColor=TEXT,
        leading=14,
        leftIndent=16,
        spaceAfter=3,
        bulletIndent=4
    )
    custom['cert'] = ParagraphStyle(
        'Cert',
        fontName='Helvetica',
        fontSize=10,
        textColor=TEXT,
        leading=16,
        spaceAfter=4
    )
    custom['mono'] = ParagraphStyle(
        'Mono',
        fontName='Courier',
        fontSize=9,
        textColor=TEXT,
        leading=12
    )
    return custom


def _severity_color(severity: str):
    s = severity.upper()
    if s == "HIGH" or s == "CRITICAL":
        return RED, RED_LIGHT
    elif s == "MED" or s == "MODERATE":
        return AMBER, AMBER_LIGHT
    else:
        return GRAY, LIGHT_GRAY


def _parse_table(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        if line.strip().startswith('|') and not re.match(r'\|[-| ]+\|', line.strip()):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            rows.append(cells)
    return rows


def _bold(text: str) -> str:
    """Convert **text** to <b>text</b> for reportlab."""
    return re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)


def _add_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(GRAY)
    canvas.drawString(inch * 0.75, 0.4 * inch, "AuditGuard AI — Confidential Audit Document")
    canvas.drawRightString(
        letter[0] - inch * 0.75, 0.4 * inch,
        f"Page {doc.page}"
    )
    canvas.restoreState()


def markdown_to_pdf(markdown_text: str, filename: str = "audit_narrative.pdf") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        rightMargin=inch * 0.75,
        leftMargin=inch * 0.75,
        topMargin=inch * 0.9,
        bottomMargin=inch * 0.75,
        title="AuditGuard AI — Data Integrity Correction Summary"
    )

    styles = _build_styles()
    story = []

    # Header banner
    header_data = [["AuditGuard AI  |  Data Integrity Correction Summary"]]
    header_table = Table(header_data, colWidths=[doc.width])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BLUE),
        ('TEXTCOLOR', (0, 0), (-1, -1), white),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 14),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 12))

    lines = markdown_text.split('\n')
    i = 0
    in_table = False
    table_lines = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Section heading ##
        if stripped.startswith('## '):
            if in_table:
                story.extend(_render_table(table_lines, styles, doc))
                table_lines = []
                in_table = False
            heading_text = stripped[3:].strip()
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=1, color=BLUE_LIGHT))
            story.append(Paragraph(heading_text, styles['h2']))
            i += 1
            continue

        # Top-level heading #
        if stripped.startswith('# '):
            text = stripped[2:].strip()
            story.append(Paragraph(text, styles['title']))
            i += 1
            continue

        # Bold metadata lines like **Dataset:** value
        if stripped.startswith('**') and ':' in stripped:
            story.append(Paragraph(_bold(stripped), styles['body']))
            i += 1
            continue

        # Table row
        if stripped.startswith('|'):
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(line)
            i += 1
            continue
        else:
            if in_table:
                story.extend(_render_table(table_lines, styles, doc))
                table_lines = []
                in_table = False

        # Bullet point
        if stripped.startswith('- ') or stripped.startswith('* '):
            text = stripped[2:].strip()
            story.append(Paragraph(f"• {_bold(text)}", styles['bullet']))
            i += 1
            continue

        # Certification block
        if 'certify' in stripped.lower() or 'Signed:' in stripped or 'Date:' in stripped:
            story.append(Paragraph(_bold(stripped), styles['cert']))
            i += 1
            continue

        # Normal paragraph
        if stripped:
            story.append(Paragraph(_bold(stripped), styles['body']))

        i += 1

    if in_table:
        story.extend(_render_table(table_lines, styles, doc))

    # Signature block at end
    story.append(Spacer(1, 20))
    sig_data = [
        ["Signed: _________________________ ", "Title: _________________________"],
        ["Date: __________________________", "Facility: ______________________"]
    ]
    sig_table = Table(sig_data, colWidths=[doc.width / 2, doc.width / 2])
    sig_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (-1, -1), TEXT),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
    ]))
    story.append(sig_table)

    doc.build(story, onFirstPage=_add_footer, onLaterPages=_add_footer)
    return buf.getvalue()


def _render_table(table_lines: list[str], styles: dict, doc) -> list:
    rows = _parse_table(table_lines)
    if not rows:
        return []

    story_items = []
    col_count = len(rows[0]) if rows else 1
    col_width = doc.width / col_count

    table_data = []
    for row_idx, row in enumerate(rows):
        # Pad/trim to col_count
        while len(row) < col_count:
            row.append("")
        row = row[:col_count]
        if row_idx == 0:
            # Header row
            table_data.append([Paragraph(f"<b>{c}</b>", styles['body']) for c in row])
        else:
            table_data.append([Paragraph(_bold(c), styles['body']) for c in row])

    if not table_data:
        return []

    t = Table(table_data, colWidths=[col_width] * col_count, repeatRows=1)
    ts = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BLUE),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ])
    # Alternating row colors
    for row_idx in range(1, len(table_data)):
        bg = LIGHT_GRAY if row_idx % 2 == 0 else white
        ts.add('BACKGROUND', (0, row_idx), (-1, row_idx), bg)

        # Color severity column if present (usually col index 2)
        row = rows[row_idx]
        for col_idx, cell in enumerate(row):
            cell_upper = cell.upper()
            if cell_upper in ("HIGH", "CRITICAL"):
                ts.add('BACKGROUND', (col_idx, row_idx), (col_idx, row_idx), RED_LIGHT)
                ts.add('TEXTCOLOR', (col_idx, row_idx), (col_idx, row_idx), RED)
            elif cell_upper in ("MED", "MODERATE", "MEDIUM"):
                ts.add('BACKGROUND', (col_idx, row_idx), (col_idx, row_idx), AMBER_LIGHT)
                ts.add('TEXTCOLOR', (col_idx, row_idx), (col_idx, row_idx), AMBER)
            elif cell_upper in ("LOW", "MINOR"):
                ts.add('BACKGROUND', (col_idx, row_idx), (col_idx, row_idx), LIGHT_GRAY)

    t.setStyle(ts)
    story_items.append(t)
    story_items.append(Spacer(1, 8))
    return story_items
