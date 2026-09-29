import uuid

from django.conf import settings
from django.db import models


def _generate_report_ref():
    return 'RPT-' + uuid.uuid4().hex[:12].upper()


class RegulatoryReport(models.Model):
    """
    A generated regulatory report — metadata + a persisted Excel artefact, so a
    previously generated report can be re-downloaded without regenerating it
    (drive_test.services.reports never recalculates a KPI, rule or finding; it only
    reads DriveTestKpiService / services.comparison / services.data_quality / Finding /
    RegulatoryRule / RegulatoryThreshold, already computed by the existing pipeline).

    `sessions` is the backend-resolved scope at generation time — never re-derived
    from the filter fields afterwards, so a report's contents stay stable even if
    new sessions are later uploaded that would also match the same filters.
    """

    class ReportType(models.TextChoices):
        SESSION = 'SESSION', 'Session Report'
        COMPLIANCE = 'COMPLIANCE', 'Regulatory Compliance Report'
        OPERATOR = 'OPERATOR', 'Operator Report'
        PERIODIC = 'PERIODIC', 'Periodic Regulatory Report'

    class Status(models.TextChoices):
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    report_ref = models.CharField(max_length=32, unique=True, default=_generate_report_ref)
    report_type = models.CharField(max_length=12, choices=ReportType.choices)
    title = models.CharField(max_length=200, blank=True)

    # Scope filters, kept for display/history — the authoritative scope is `sessions`.
    operator = models.ForeignKey(
        'reference.Operator', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    technology = models.CharField(max_length=5, blank=True)
    region = models.ForeignKey(
        'Region', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    district = models.ForeignKey(
        'District', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)

    sessions = models.ManyToManyField('DriveTestSession', related_name='regulatory_reports', blank=True)

    # Denormalised at generation time so the report list never re-aggregates per row.
    session_count = models.IntegerField(default=0)
    measurement_count = models.IntegerField(default=0)
    finding_count = models.IntegerField(default=0)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COMPLETED)
    error_message = models.TextField(blank=True)

    file = models.FileField(upload_to='drive_test/reports/%Y/%m/', null=True, blank=True)

    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-generated_at']

    def __str__(self):
        return f'{self.report_ref} ({self.get_report_type_display()})'
