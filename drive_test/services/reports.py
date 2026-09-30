"""Report generation orchestrator.

Assembles a report's data (report_data) then renders it to the requested format,
writing the artifact under MEDIA_ROOT and flipping the Report row to READY. All
KPI math is reused from the analytics/comparison services — never re-derived.
"""
from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from drive_test.models.enums import ReportStatus

logger = logging.getLogger('drive_test')

PDF = 'pdf'
XLSX = 'xlsx'
CSV = 'csv'
GEOJSON = 'geojson'
KML = 'kml'
FORMATS = {PDF, XLSX, CSV, GEOJSON, KML}

_EXT = {PDF: 'pdf', XLSX: 'xlsx', CSV: 'csv', GEOJSON: 'geojson', KML: 'kml'}


def reports_dir() -> Path:
    d = Path(settings.MEDIA_ROOT) / 'drive_test' / 'reports'
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate(report) -> dict:
    """Render one Report to disk. Returns a result dict for the JobRecord."""
    from drive_test.services import report_data, report_pdf, report_excel, report_exports

    fmt = (report.params or {}).get('format', PDF)
    if fmt not in FORMATS:
        fmt = PDF
    technology = (report.params or {}).get('technology', '')
    metric = (report.params or {}).get('metric', '')

    report.status = ReportStatus.GENERATING
    report.save(update_fields=['status'])

    try:
        if fmt == PDF:
            content = report_pdf.render_pdf(report_data.assemble(report))
        elif fmt == XLSX:
            content = report_excel.render_excel(report_data.assemble(report))
        elif fmt == CSV:
            content = report_exports.csv_export(report.campaign, technology)
        elif fmt == GEOJSON:
            content = report_exports.geojson_export(report.campaign, metric, technology)
        else:  # KML
            content = report_exports.kml_export(report.campaign, metric, technology)

        path = reports_dir() / f'{report.ref}.{_EXT[fmt]}'
        path.write_bytes(content)

        report.artifact_path = str(path)
        report.artifact_format = fmt
        report.status = ReportStatus.READY
        report.error_message = ''
        report.save(update_fields=['artifact_path', 'artifact_format', 'status', 'error_message'])
    except Exception as exc:
        logger.exception('Report %s generation failed', report.ref)
        report.status = ReportStatus.FAILED
        report.error_message = f'{type(exc).__name__}: {exc}'
        report.save(update_fields=['status', 'error_message'])
        raise

    logger.info('Generated report %s (%s, %d bytes)', report.ref, fmt, len(content))
    return {
        'message': f'Generated {fmt.upper()} report',
        'bytes': len(content),
        'result_entity_type': 'Report',
        'result_entity_id': str(report.pk),
        'result_url': f'/drive-test/reports/{report.ref}/',
    }
