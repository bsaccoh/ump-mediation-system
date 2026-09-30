"""Render assembled report data to a professional PDF (reportlab platypus)."""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# Band tokens → hex (mirrors static/drive_test/css/dt-tokens.css light values;
# PDF cannot read CSS, so the mapping is kept here deliberately).
BAND_HEX = {
    'excellent': '#1a9850', 'good': '#91cf60', 'fair': '#fee08b',
    'poor': '#fc8d59', 'critical': '#d73027', 'none': '#9ca3af',
}
ACCENT = colors.HexColor('#0d47a1')
MUTED = colors.HexColor('#64748b')
BORDER = colors.HexColor('#cbd5e1')
HEADER_BG = colors.HexColor('#f1f5f9')


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle('DTTitle', parent=ss['Title'], fontSize=20, textColor=ACCENT, spaceAfter=2))
    ss.add(ParagraphStyle('DTSub', parent=ss['Normal'], fontSize=9, textColor=MUTED, spaceAfter=10))
    ss.add(ParagraphStyle('DTH2', parent=ss['Heading2'], fontSize=12, textColor=ACCENT,
                          spaceBefore=12, spaceAfter=4))
    ss.add(ParagraphStyle('DTNote', parent=ss['Normal'], fontSize=7.5, textColor=MUTED, spaceBefore=2))
    return ss


def _table(columns, rows, col_widths=None):
    data = [columns] + [[('' if c is None else str(c)) for c in r] for r in rows]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), MUTED),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.4, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return t


def _histogram_table(hist):
    rows = [[h['label'], h['count'], f"{h['pct']}%"] for h in hist]
    t = _table(['Class', 'Count', 'Share'], rows, col_widths=[60 * mm, 30 * mm, 30 * mm])
    style = [('BACKGROUND', (0, i + 1), (0, i + 1),
              colors.HexColor(BAND_HEX.get(h['color'], '#9ca3af')))
             for i, h in enumerate(hist)]
    t.setStyle(TableStyle(style))
    return t


def render_pdf(data) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm,
                            title=data.get('title', 'Drive Test Report'))
    ss = _styles()
    story = [Paragraph(data.get('title', 'Drive Test Report'), ss['DTTitle'])]

    meta = data.get('meta') or {}
    if meta:
        story.append(Paragraph('UMP Drive Test Intelligence', ss['DTSub']))
        meta_rows = [
            ['Campaign', meta.get('campaign', '—'), 'Operator', meta.get('operator', '—')],
            ['Project', meta.get('project', '—'), 'Technology', meta.get('technology', '—')],
            ['Region', meta.get('region', '—'), 'Generated', meta.get('generated_at', '—')],
            ['Prepared by', meta.get('prepared_by', '—'), '', ''],
        ]
        mt = _table(['', '', '', ''], meta_rows, col_widths=[28 * mm, 60 * mm, 28 * mm, 52 * mm])
        mt.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('TEXTCOLOR', (0, 0), (0, -1), MUTED),
            ('TEXTCOLOR', (2, 0), (2, -1), MUTED),
            ('BACKGROUND', (0, 0), (-1, 0), colors.white),
        ]))
        story += [mt, Spacer(1, 8)]

    summary = data.get('summary') or []
    if summary:
        story.append(Paragraph('Summary', ss['DTH2']))
        rows = [[s['label'], _fmt(s['value'], s.get('unit'))] for s in summary]
        story.append(_table(['Metric', 'Value'], rows, col_widths=[80 * mm, 60 * mm]))

    for sec in data.get('sections') or []:
        story.append(Paragraph(sec['heading'], ss['DTH2']))
        if sec.get('kind') == 'table' and sec.get('rows'):
            story.append(_table(sec['columns'], sec['rows']))
        if sec.get('histogram'):
            story += [Spacer(1, 4), _histogram_table(sec['histogram'])]
        if sec.get('note'):
            story.append(Paragraph(sec['note'], ss['DTNote']))

    if not (data.get('sections') or summary):
        story.append(Paragraph('No data available for this report.', ss['DTSub']))

    doc.build(story)
    return buf.getvalue()


def _fmt(value, unit):
    if value is None:
        return '—'
    return f'{value} {unit}'.strip() if unit else str(value)
