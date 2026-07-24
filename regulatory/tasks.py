"""Async wrappers for regulatory business engines."""
from __future__ import annotations

from datetime import date, datetime

from core.tasks import tracked_task


def _to_date(value):
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


@tracked_task('regulatory.generate_report')
def task_generate_report(report_type: str, start_iso: str, end_iso: str,
                          user_id: int | None = None):
    """Generate a NATCOM periodic report (PDF + Excel)."""
    from django.contrib.auth import get_user_model
    from regulatory.engines.reports import generate_report

    user = None
    if user_id:
        try:
            user = get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist:
            user = None

    start = _to_date(start_iso)
    end = _to_date(end_iso)
    report = generate_report(report_type, start, end, user=user)
    return {
        'result_entity_type': 'RegulatoryReport',
        'result_entity_id': report.pk,
        'result_url': f'/regulatory/reports/{report.pk}/pdf/',
        'report_type': report.report_type,
        'message': f'Generated {report.report_type} report for {start}..{end}',
    }


@tracked_task('regulatory.intercept_export')
def task_intercept_export(request_id: int, user_id: int | None = None):
    """Run a Lawful-Intercept evidentiary CSV export."""
    from django.contrib.auth import get_user_model
    from regulatory.models import LEARequest
    from regulatory.engines.intercept import export_evidentiary

    req = LEARequest.objects.get(pk=request_id)
    user = None
    if user_id:
        try:
            user = get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist:
            user = None

    ext = export_evidentiary(req, user=user)
    return {
        'result_entity_type': 'LEAExtractionLog',
        'result_entity_id': ext.pk,
        'result_url': f'/regulatory/intercept/extraction/{ext.pk}/download/',
        'record_count': ext.record_count,
        'sha256': ext.sha256,
        'message': f'Exported {ext.record_count} records for case {req.case_number}',
    }


@tracked_task('regulatory.compute_levy')
def task_compute_levy(period_year: int, period_month: int,
                       user_id: int | None = None):
    """Compute the regulatory levy for one period."""
    from django.contrib.auth import get_user_model
    from regulatory.engines.levy import compute_levy

    user = None
    if user_id:
        try:
            user = get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist:
            user = None

    levy = compute_levy(period_year, period_month, user=user)
    return {
        'result_entity_type': 'LeviedPeriod',
        'result_entity_id': levy.pk,
        'result_url': '/regulatory/levy/',
        'total_payable': str(levy.total_payable),
        'currency': levy.currency,
        'message': f'Levy for {period_year}-{period_month:02d}: '
                    f'{levy.total_payable} {levy.currency} payable',
    }
