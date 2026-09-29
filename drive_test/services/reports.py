"""
Regulatory Reports & Compliance Reporting.

This module computes NO KPI, evaluates NO regulatory rule and generates NO finding
itself. It only:
  - resolves a session scope (backend-authoritative — the browser never determines
    the final dataset),
  - reads what the existing pipeline already computed/stored
    (DriveTestKpiService via services.comparison, services.data_quality,
    Finding rows written by services.analysis.AnalysisEngine, RegulatoryRule /
    RegulatoryThreshold),
  - and formats that into a report (HTML context, Excel via openpyxl, PDF via
    reportlab — the same two libraries session_report_excel / regulatory's
    report_generator.py already use).

Region/District always come from the real matched-cell -> sector -> site ->
chiefdom -> district -> region chain; an unmatched measurement is never assigned
a geography.
"""
from __future__ import annotations

from django.db.models import Count, Exists, OuterRef, Q

from . import comparison
from . import data_quality as dq
from . import rules as rl


def resolve_scope(GET):
    """Backend-authoritative session scope. Returns (sessions_qs, filters, errors).

    An explicit list of session references (?session=A&session=B) always wins over
    the filter fields when given — the two are never silently merged.
    """
    from django.core.exceptions import ValidationError

    from ..models import DriveTestSession, Measurement

    f = {k: GET.get(k, '').strip() for k in
         ('operator', 'technology', 'region', 'district', 'date_from', 'date_to')}
    session_refs = [s.strip() for s in GET.getlist('session')] if hasattr(GET, 'getlist') else \
                   ([GET['session'].strip()] if GET.get('session') else [])
    session_refs = [s for s in session_refs if s]
    f['sessions'] = ', '.join(session_refs)
    errors = []

    if session_refs:
        sessions = DriveTestSession.objects.filter(session_ref__in=session_refs)
    else:
        sessions = DriveTestSession.objects.all()
        if f['operator']:
            sessions = sessions.filter(operator__code=f['operator'])
        if f['technology']:
            from ..models import RadioMeasurement
            sessions = sessions.filter(Exists(RadioMeasurement.objects.filter(
                technology=f['technology'], measurement__drive_file__session=OuterRef('pk'))))
        for key, field in (('region', 'matched_cell__sector__site__chiefdom__district__region_id'),
                           ('district', 'matched_cell__sector__site__chiefdom__district_id')):
            if f[key]:
                if f[key].isdigit():
                    sessions = sessions.filter(Exists(Measurement.objects.filter(
                        drive_file__session=OuterRef('pk'), **{field: int(f[key])})))
                else:
                    errors.append('Unrecognised %s.' % key)
                    sessions = DriveTestSession.objects.none()
        try:
            if f['date_from']:
                sessions = sessions.filter(test_date__gte=f['date_from'])
            if f['date_to']:
                sessions = sessions.filter(test_date__lte=f['date_to'])
        except (ValueError, ValidationError):
            errors.append('Date must look like 2026-09-24.')
            sessions = DriveTestSession.objects.none()

    return sessions.distinct(), f, errors


def scope_measurements(sessions_qs):
    from ..models import Measurement
    return Measurement.objects.filter(is_valid=True, drive_file__session__in=sessions_qs)


def report_scope_summary(sessions_qs, meas_qs):
    """Report Scope card — the exact fields the spec asks for, all real aggregates."""
    from ..models import Finding

    session_ids = list(sessions_qs.values_list('pk', flat=True))
    agg = meas_qs.aggregate(
        measurements=Count('id'),
        matched=Count('id', filter=Q(matched_cell__isnull=False)),
        voice_events=Count('services', filter=Q(services__service_type='VOICE'), distinct=True),
    )
    total = agg['measurements'] or 0
    matched = agg['matched'] or 0
    return {
        'sessions': len(session_ids),
        'measurements': total,
        'voice_events': agg['voice_events'] or 0,
        'matched_measurements': matched,
        'unmatched_measurements': max(total - matched, 0),
        'findings': Finding.objects.filter(session_id__in=session_ids).count(),
    }


def applicable_rules(sessions_qs, meas_qs):
    """RegulatoryThreshold rows whose rule/operator scope overlaps the report's real
    scope — the same fields AnalysisEngine._load_rules() itself reads. For each,
    the real count of Finding rows it produced within this scope (never a computed
    "pass/fail": that judgement does not exist unless the engine already made it)."""
    from ..models import Finding, RegulatoryThreshold

    technologies = set(meas_qs.exclude(radio__technology='')
                        .values_list('radio__technology', flat=True).distinct())
    operator_ids = set(sessions_qs.values_list('operator_id', flat=True).distinct())
    session_ids = list(sessions_qs.values_list('pk', flat=True))

    thresholds = (
        RegulatoryThreshold.objects
        .filter(rule__is_active=True)
        .filter(Q(rule__technology='') | Q(rule__technology__in=technologies))
        .filter(Q(operator__isnull=True) | Q(operator_id__in=operator_ids))
        .select_related('rule', 'operator')
        .order_by('rule__name', 'operator__name')
    )
    rows = []
    for t in thresholds:
        status = rl.rule_status(t.rule)
        rows.append({
            'threshold': t, 'rule': t.rule,
            'status': status, 'status_label': rl.STATUS_LABELS[status],
            'condition_label': rl.CONDITION_LABELS.get(t.rule.condition, t.rule.condition),
            'findings_in_scope': Finding.objects.filter(session_id__in=session_ids, threshold=t).count(),
        })
    return rows


def report_findings(sessions_qs, limit=None):
    from ..models import Finding

    qs = (Finding.objects.filter(session__in=sessions_qs)
          .select_related('session__operator', 'cell__sector__site', 'threshold__rule')
          .order_by('-created_at'))
    return list(qs[:limit]) if limit else list(qs)


def build_limitations(scope, dq_summary):
    """Factual limitation sentences — each only appears when the underlying number is
    actually nonzero for this scope. Never a generic boilerplate disclaimer."""
    notes = []
    if scope['unmatched_measurements'] > 0:
        notes.append('Some measurements could not be matched to the network reference database.')
    if dq_summary and (dq_summary.get('invalid_records') or 0) > 0:
        notes.append('Some parser records were invalid and excluded from the normalized measurement dataset.')
    if scope['voice_events'] == 0:
        notes.append('No voice service measurements were available for this scope; voice KPIs could not be calculated.')
    return notes


def build_context(sessions_qs, *, report=None, filters=None):
    """Everything the HTML/Excel/PDF renderers need, computed exactly once."""
    sessions = list(sessions_qs.select_related('operator').order_by('test_date'))
    session_ids = [s.pk for s in sessions]
    meas_qs = scope_measurements(sessions_qs)

    scope = report_scope_summary(sessions_qs, meas_qs)
    overall = comparison.overall_kpis(meas_qs) if scope['measurements'] else None
    dq_summary = dq.workspace_summary(session_ids) if session_ids else None
    dq_category = dq.category_breakdown(session_ids) if session_ids else None
    tech_summary = comparison.technology_summary(meas_qs)
    all_findings = report_findings(sessions_qs)

    return {
        'report': report,
        'filters': filters or {},
        'sessions': sessions,
        'session_ids': session_ids,
        'scope': scope,
        'overall': overall,
        'technology_summary': tech_summary,
        'technology_kpis': comparison.technology_kpis(meas_qs),
        'operator_kpis': comparison.operator_kpis(meas_qs) if len({s.operator_id for s in sessions}) > 1 else [],
        'geographic': comparison.geographic_breakdown(meas_qs),
        'data_quality': dq_summary,
        'data_quality_category': dq_category,
        'rules': applicable_rules(sessions_qs, meas_qs),
        'findings': all_findings[:5000],
        'findings_truncated': len(all_findings) > 5000,
        'limitations': build_limitations(scope, dq_summary),
        'period_from': min((s.test_date for s in sessions), default=None),
        'period_to': max((s.test_date for s in sessions), default=None),
        'operator_names': sorted({s.operator.name for s in sessions}),
        'technology_names': [r['technology'] for r in tech_summary],
    }


def report_title(report_type, ctx):
    from ..models import RegulatoryReport

    label = dict(RegulatoryReport.ReportType.choices).get(report_type, report_type)
    scope_bit = ' / '.join(ctx['operator_names']) or 'All Operators'
    return f'{label} — {scope_bit}'


# ---------------------------------------------------------------------------
# Excel export — same openpyxl conventions as views.session_report_excel
# ---------------------------------------------------------------------------

def generate_excel(ctx) -> bytes:
    import io

    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    HDR_FILL = PatternFill('solid', fgColor='1A237E')
    HDR_FONT = Font(color='FFFFFF', bold=True, size=10)
    HDR_ALIGN = Alignment(horizontal='center', vertical='center', wrap_text=True)

    def header_row(ws, cols):
        ws.append(cols)
        for cell in ws[ws.max_row]:
            cell.fill, cell.font, cell.alignment = HDR_FILL, HDR_FONT, HDR_ALIGN

    def auto_width(ws, extra=2):
        for col in ws.columns:
            max_len = max((len(str(c.value or '')) for c in col), default=8)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + extra, 40)

    def v(val, fallback='—'):
        return val if val is not None else fallback

    wb = openpyxl.Workbook()

    # ── Summary ──────────────────────────────────────────────────────────
    ws = wb.active
    ws.title = 'Summary'
    report = ctx['report']
    rows = [
        ('Report Reference', report.report_ref if report else '—'),
        ('Report Type', report.get_report_type_display() if report else '—'),
        ('Reporting Period', f"{ctx['period_from']} to {ctx['period_to']}" if ctx['period_from'] else '—'),
        ('Operator Scope', ', '.join(ctx['operator_names']) or 'All'),
        ('Technology Scope', ', '.join(ctx['technology_names']) or 'All'),
        ('Generated At', str(report.generated_at) if report else '—'),
        ('Generated By', (report.generated_by.username if report and report.generated_by_id else 'System')),
        ('', ''),
        ('── SCOPE ──', ''),
        ('Sessions', ctx['scope']['sessions']),
        ('Measurements', ctx['scope']['measurements']),
        ('Voice Events', ctx['scope']['voice_events']),
        ('Matched Measurements', ctx['scope']['matched_measurements']),
        ('Unmatched Measurements', ctx['scope']['unmatched_measurements']),
        ('Findings', ctx['scope']['findings']),
    ]
    if ctx['overall']:
        signal, voice, mob = ctx['overall']['signal'], ctx['overall']['voice'], ctx['overall']['mobility']
        rows += [
            ('', ''), ('── EXECUTIVE SUMMARY (ALL TECHNOLOGIES) ──', ''),
            ('Coverage %', v(signal.get('coverage', {}).get('coverage_percent'))),
            ('Mean RSSI (dBm)', v(signal.get('mean_rssi'))),
            ('CSSR (%)', v(voice.get('cssr_percent'))),
            ('DCR (%)', v(voice.get('dcr_percent'))),
            ('Mean MOS', v(voice.get('mean_mos'))),
            ('Mean Speed (km/h)', v(mob.get('mean_speed_kmh'))),
        ]
    if ctx['data_quality']:
        d = ctx['data_quality']
        rows += [
            ('', ''), ('── DATA QUALITY ──', ''),
            ('Sessions Assessed', v(d.get('sessions_assessed'))),
            ('Measurements Assessed', v(d.get('measurements_assessed'))),
            ('Invalid Records', v(d.get('invalid_records'))),
            ('Unmatched Measurements', v(d.get('unmatched_measurements'))),
            ('Quality Issues', v(d.get('quality_issues'))),
        ]
    header_row(ws, ['Parameter', 'Value'])
    for label, val in rows:
        ws.append([label, val])
    ws.column_dimensions['A'].width = 34
    ws.column_dimensions['B'].width = 24
    ws.freeze_panes = 'A2'

    # ── Sessions ─────────────────────────────────────────────────────────
    ws_s = wb.create_sheet('Sessions')
    header_row(ws_s, ['Session', 'Operator', 'Test Date', 'Status', 'Measurements', 'Findings'])
    for s in ctx['sessions']:
        ws_s.append([s.session_ref, s.operator.name, str(s.test_date), s.status,
                     s.total_measurements, s.finding_count])
    auto_width(ws_s)
    ws_s.freeze_panes = 'A2'

    # ── KPIs by Technology ───────────────────────────────────────────────
    ws_k = wb.create_sheet('KPIs')
    header_row(ws_k, ['Technology', 'Measurements', 'Coverage %', 'Mean RSSI', 'CSSR %', 'DCR %', 'Mean MOS'])
    for row in ctx['technology_kpis']:
        sig, voice = row['signal'], row['voice']
        ws_k.append([row['technology'], row['measurements'],
                     v(sig.get('coverage', {}).get('coverage_percent')), v(sig.get('mean_rssi')),
                     v(voice.get('cssr_percent')), v(voice.get('dcr_percent')), v(voice.get('mean_mos'))])
    auto_width(ws_k)
    ws_k.freeze_panes = 'A2'

    # ── Rules & Thresholds ───────────────────────────────────────────────
    ws_r = wb.create_sheet('Rules & Thresholds')
    header_row(ws_r, ['Rule', 'Metric', 'Condition', 'Critical Value', 'Warning Value', 'Unit',
                      'Operator', 'Technology', 'Status', 'Findings In Scope'])
    for row in ctx['rules']:
        t, rule = row['threshold'], row['rule']
        ws_r.append([rule.name, rule.metric.upper(), row['condition_label'], t.critical_value,
                     v(t.warning_value), t.unit, t.operator.name if t.operator_id else 'All Operators',
                     rule.technology or 'All', row['status_label'], row['findings_in_scope']])
    auto_width(ws_r)
    ws_r.freeze_panes = 'A2'

    # ── Findings ─────────────────────────────────────────────────────────
    ws_f = wb.create_sheet('Findings')
    header_row(ws_f, ['Session', 'Severity', 'Type', 'Description', 'Cell', 'Site', 'Created'])
    for f in ctx['findings']:
        site = f.cell.sector.site.name if f.cell_id and f.cell.sector_id else ''
        ws_f.append([f.session.session_ref, f.get_severity_display(), f.get_finding_type_display(),
                     f.description, f.cell.cell_id if f.cell_id else '', site,
                     f.created_at.strftime('%Y-%m-%d %H:%M:%S')])
    auto_width(ws_f)
    ws_f.freeze_panes = 'A2'
    ws_f.auto_filter.ref = ws_f.dimensions

    # ── Geographic ───────────────────────────────────────────────────────
    if ctx['geographic']:
        ws_g = wb.create_sheet('Geographic')
        header_row(ws_g, ['Region', 'Measurements', 'Coverage %', 'Mean RSSI'])
        for row in ctx['geographic']:
            sig = row['signal']
            ws_g.append([row['region'].name, row['measurements'],
                         v(sig.get('coverage', {}).get('coverage_percent')), v(sig.get('mean_rssi'))])
        auto_width(ws_g)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF export — reportlab, the same library regulatory/services/report_generator.py
# already uses
# ---------------------------------------------------------------------------

def generate_pdf(ctx) -> bytes:
    import io

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    styles = getSampleStyleSheet()
    story = []
    report = ctx['report']

    def v(val, fallback='—'):
        return str(val) if val is not None else fallback

    def kv_table(rows):
        t = Table([[str(k), str(v)] for k, v in rows], colWidths=[6 * cm, 10 * cm])
        t.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#475569')),
            ('LINEBELOW', (0, 0), (-1, -1), 0.25, colors.HexColor('#e3e6ea')),
        ]))
        return t

    def data_table(header, rows, col_widths=None):
        data = [header] + rows
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1A237E')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#e3e6ea')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        return t

    # ── Cover ────────────────────────────────────────────────────────────
    story.append(Paragraph('Regulatory Drive Test Report', styles['Title']))
    story.append(Spacer(1, 10))
    story.append(kv_table([
        ('Report Reference', report.report_ref if report else '—'),
        ('Report Type', report.get_report_type_display() if report else '—'),
        ('Reporting Period', f"{ctx['period_from']} to {ctx['period_to']}" if ctx['period_from'] else '—'),
        ('Operator', ', '.join(ctx['operator_names']) or 'All'),
        ('Technology', ', '.join(ctx['technology_names']) or 'All'),
        ('Generated Date', str(report.generated_at)[:19] if report else '—'),
        ('Generated By', (report.generated_by.username if report and report.generated_by_id else 'System')),
    ]))
    story.append(PageBreak())

    # ── Executive Summary ────────────────────────────────────────────────
    story.append(Paragraph('Executive Summary', styles['Heading1']))
    scope_rows = [
        ('Sessions', ctx['scope']['sessions']), ('Measurements', ctx['scope']['measurements']),
        ('Voice Events', ctx['scope']['voice_events']),
        ('Matched Measurements', ctx['scope']['matched_measurements']),
        ('Unmatched Measurements', ctx['scope']['unmatched_measurements']),
        ('Findings', ctx['scope']['findings']),
    ]
    story.append(kv_table(scope_rows))
    if ctx['overall']:
        signal, voice, mob = ctx['overall']['signal'], ctx['overall']['voice'], ctx['overall']['mobility']
        story.append(Spacer(1, 8))
        story.append(kv_table([
            ('Coverage %', v(signal.get('coverage', {}).get('coverage_percent'))),
            ('Mean RSSI (dBm)', v(signal.get('mean_rssi'))),
            ('CSSR (%)', v(voice.get('cssr_percent'))),
            ('DCR (%)', v(voice.get('dcr_percent'))),
            ('Mean MOS', v(voice.get('mean_mos'))),
            ('Mean Speed (km/h)', v(mob.get('mean_speed_kmh'))),
        ]))
    story.append(Spacer(1, 14))

    # ── Data Quality ─────────────────────────────────────────────────────
    story.append(Paragraph('Data Quality Summary', styles['Heading1']))
    if ctx['data_quality']:
        d = ctx['data_quality']
        story.append(kv_table([
            ('Sessions Assessed', v(d.get('sessions_assessed'))),
            ('Measurements Assessed', v(d.get('measurements_assessed'))),
            ('Invalid Records', v(d.get('invalid_records'))),
            ('Unmatched Measurements', v(d.get('unmatched_measurements'))),
            ('Quality Issues', v(d.get('quality_issues'))),
        ]))
    else:
        story.append(Paragraph('No data quality assessment is available for this scope.', styles['Normal']))
    story.append(Spacer(1, 14))

    # ── KPI by Technology ────────────────────────────────────────────────
    story.append(Paragraph('KPI Analysis by Technology', styles['Heading1']))
    if ctx['technology_kpis']:
        header = ['Technology', 'Measurements', 'Coverage %', 'Mean RSSI', 'CSSR %', 'DCR %', 'Mean MOS']
        rows = []
        for row in ctx['technology_kpis']:
            sig, voice = row['signal'], row['voice']
            rows.append([row['technology'], row['measurements'],
                         v(sig.get('coverage', {}).get('coverage_percent')), v(sig.get('mean_rssi')),
                         v(voice.get('cssr_percent')), v(voice.get('dcr_percent')), v(voice.get('mean_mos'))])
        story.append(data_table(header, rows))
    else:
        story.append(Paragraph('No technology-level measurements are available for this scope.', styles['Normal']))
    story.append(Spacer(1, 14))

    # ── Regulatory Rules ─────────────────────────────────────────────────
    story.append(Paragraph('Applicable Regulatory Rules & Thresholds', styles['Heading1']))
    if ctx['rules']:
        header = ['Rule', 'Metric', 'Condition', 'Critical', 'Unit', 'Operator', 'Status', 'Findings']
        rows = [[r['rule'].name, r['rule'].metric.upper(), r['condition_label'], r['threshold'].critical_value,
                 r['threshold'].unit, r['threshold'].operator.name if r['threshold'].operator_id else 'All',
                 r['status_label'], r['findings_in_scope']] for r in ctx['rules']]
        story.append(data_table(header, rows))
    else:
        story.append(Paragraph('No configured regulatory rules apply to this scope.', styles['Normal']))
    story.append(Spacer(1, 14))

    # ── Findings ─────────────────────────────────────────────────────────
    story.append(Paragraph('Findings', styles['Heading1']))
    if ctx['findings']:
        shown = ctx['findings'][:100]
        header = ['Session', 'Severity', 'Type', 'Description']
        rows = [[f.session.session_ref, f.get_severity_display(), f.get_finding_type_display(),
                 Paragraph(f.description, styles['Normal'])] for f in shown]
        story.append(data_table(header, rows, col_widths=[3 * cm, 2.2 * cm, 3.3 * cm, 8 * cm]))
        if len(ctx['findings']) > 100:
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"Showing the first 100 of {len(ctx['findings'])} findings. "
                                    'The full list is included in the Excel export.', styles['Italic']))
    else:
        story.append(Paragraph('No findings were recorded for the selected scope.', styles['Normal']))
    story.append(Spacer(1, 14))

    # ── Limitations ──────────────────────────────────────────────────────
    if ctx['limitations']:
        story.append(Paragraph('Data Quality & Limitations', styles['Heading1']))
        for note in ctx['limitations']:
            story.append(Paragraph(f'• {note}', styles['Normal']))

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#6c757d'))
        canvas.drawString(2 * cm, 1.2 * cm,
                           f"Generated {str(report.generated_at)[:19] if report else ''} — "
                           f"{report.report_ref if report else ''}")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f'Page {doc.page}')
        canvas.restoreState()

    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return out.getvalue()
