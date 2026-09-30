"""Assemble report content from existing services.

Each assembler returns a plain dict of sections so the PDF and Excel renderers
share one content model. No KPI math is re-derived here — it calls the same
analytics / comparison services the on-screen pages use, so a report and the UI
never disagree. Absent metrics stay absent (rendered "—" downstream), never 0.
"""
from __future__ import annotations

from datetime import datetime

from drive_test.services import analytics, comparison


def _meta(campaign, prepared_by=''):
    return {
        'campaign': campaign.name,
        'project': campaign.project.name if campaign.project_id else '',
        'operator': campaign.operator.name if campaign.operator_id else '—',
        'technology': campaign.get_technology_display() or '—',
        'region': campaign.region or '—',
        'generated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
        'prepared_by': prepared_by or '—',
    }


def _summary_rows(campaign):
    from drive_test.models import Event, ProblemArea, Sample
    samples = Sample.objects.filter(campaign=campaign)
    n = samples.count()
    cov = analytics.coverage_report(campaign)
    rows = [
        {'label': 'Samples', 'value': n, 'unit': ''},
        {'label': 'Route distance', 'value': cov.get('total_distance_km'), 'unit': 'km'},
        {'label': 'Events', 'value': Event.objects.filter(campaign=campaign).count(), 'unit': ''},
        {'label': 'Problem areas', 'value': ProblemArea.objects.filter(campaign=campaign).count(), 'unit': ''},
    ]
    if cov.get('metric'):
        rows.append({'label': f"Mean {cov['label']}", 'value': cov['stats']['mean'], 'unit': cov['unit']})
    return rows


def _coverage_section(campaign):
    cov = analytics.coverage_report(campaign)
    if not cov.get('metric'):
        return None
    rows = [[h['label'], f"{h['pct']}%",
             '—' if h.get('distance_km') is None else f"{h['distance_km']} km"]
            for h in cov['histogram']]
    return {
        'heading': f"Coverage classes ({cov['label']})",
        'kind': 'table',
        'columns': ['Class', 'Share', 'Distance (est.)'],
        'rows': rows,
        'note': 'Per-class distance is estimated from sample share of the route.',
    }


def _rf_sections(campaign):
    out = []
    for r in analytics.rf_report(campaign):
        s = r['stats']
        out.append({
            'heading': f"{r['label']}" + (f" ({r['unit']})" if r['unit'] else ''),
            'kind': 'table',
            'columns': ['Count', 'Min', 'Mean', 'Median', 'P10', 'P90', 'Max', 'Std'],
            'rows': [[s['count'], s['min'], s['mean'], s['median'], s['p10'], s['p90'], s['max'], s['std']]],
            'histogram': r['histogram'],
        })
    return out


def _problem_area_section(campaign):
    from drive_test.models import ProblemArea
    areas = ProblemArea.objects.filter(campaign=campaign).order_by('-severity', '-sample_count')[:15]
    if not areas:
        return None
    rows = [[a.area_type, a.get_severity_display(), a.sample_count,
             '—' if a.affected_distance_m is None else f"{a.affected_distance_m:.0f} m"]
            for a in areas]
    return {'heading': 'Top problem areas', 'kind': 'table',
            'columns': ['Type', 'Severity', 'Events', 'Affected'], 'rows': rows}


def _events_section(campaign):
    from drive_test.models import Event
    from django.db.models import Count
    rows = (Event.objects.filter(campaign=campaign).values('severity')
            .annotate(n=Count('id')).order_by('severity'))
    if not rows:
        return None
    return {'heading': 'Events by severity', 'kind': 'table',
            'columns': ['Severity', 'Count'],
            'rows': [[r['severity'], r['n']] for r in rows]}


def _cells_section(campaign):
    cells = analytics.cell_report(campaign, limit=20)
    if not cells:
        return None
    rows = [[c['cell_id'], c['technology'], c['samples'],
             '—' if c['avg_rsrp'] is None else c['avg_rsrp'],
             '—' if c['avg_sinr'] is None else c['avg_sinr']] for c in cells]
    return {'heading': 'Top serving cells (observed)', 'kind': 'table',
            'columns': ['Cell', 'Tech', 'Samples', 'Avg RSRP', 'Avg SINR'], 'rows': rows}


def _custody_section(campaign):
    rows = [[f.original_name, f.detected_format or '—',
             (f.sha256[:16] + '…') if f.sha256 else '—',
             f.uploaded_by.username if f.uploaded_by_id else '—',
             f.processed_at.strftime('%Y-%m-%d %H:%M') if f.processed_at else '—']
            for f in campaign.files.all()]
    if not rows:
        return None
    return {'heading': 'Chain of custody (source files)', 'kind': 'table',
            'columns': ['File', 'Format', 'SHA-256', 'Uploaded by', 'Processed'], 'rows': rows}


def assemble(report):
    """Return {title, meta, summary, sections[]} for the report's type."""
    from drive_test.models.enums import ReportType

    campaign = report.campaign
    prepared_by = (report.params or {}).get('prepared_by', '')
    rtype = report.report_type

    if campaign is None:
        return {'title': report.title or 'Drive Test Report', 'meta': {}, 'summary': [], 'sections': []}

    meta = _meta(campaign, prepared_by)
    title = report.title or f'{report.get_report_type_display()} — {campaign.name}'
    summary = _summary_rows(campaign)
    sections = []

    if rtype == ReportType.COVERAGE:
        sections = [s for s in [_coverage_section(campaign)] if s]
    elif rtype == ReportType.TECHNICAL:
        sections = _rf_sections(campaign) + [s for s in [_cells_section(campaign)] if s]
    elif rtype == ReportType.OPERATOR_COMPARISON:
        table = comparison.operator_comparison([campaign.pk])
        sections = [{
            'heading': 'Operator comparison',
            'kind': 'table',
            'columns': ['Metric'] + table['dimensions'],
            'rows': [[m['label']] + [(_dash(m['values'].get(d))) for d in table['dimensions']]
                     for m in table['metrics']],
        }]
    else:  # EXECUTIVE, CAMPAIGN, ROUTE, REGULATORY → a rounded overview
        sections = [s for s in [
            _coverage_section(campaign),
            *_rf_sections(campaign),
            _events_section(campaign),
            _problem_area_section(campaign),
        ] if s]

    # Every report carries provenance.
    custody = _custody_section(campaign)
    if custody:
        sections.append(custody)

    return {'title': title, 'meta': meta, 'summary': summary, 'sections': sections}


def _dash(v):
    return '—' if v is None else v
