from django.conf import settings
from django.db import models


class ReconciliationRun(models.Model):
    """One reconciliation execution comparing mediated aggregates vs declarations."""

    class Level(models.TextChoices):
        TRAFFIC = 'TRAFFIC', 'Traffic'
        REVENUE = 'REVENUE', 'Revenue'
        TAXABLE_REVENUE = 'TAXABLE_REVENUE', 'Taxable Revenue'
        GST = 'GST', 'GST'
        FULL = 'FULL', 'Full Reconciliation'
        MONTHLY = 'MONTHLY', 'Monthly (legacy)'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        QUEUED = 'QUEUED', 'Queued'
        RUNNING = 'RUNNING', 'Running'
        COMPLETED = 'COMPLETED', 'Completed'
        WARNING = 'WARNING', 'Completed with Warnings'
        FAILED = 'FAILED', 'Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    operator_code = models.SlugField(max_length=30, blank=True, db_index=True)
    reference = models.CharField(max_length=32, unique=True, null=True, blank=True, db_index=True)
    level = models.CharField(max_length=20, choices=Level.choices)
    period_start = models.DateField()
    period_end = models.DateField()

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    declaration = models.ForeignKey(
        'regulatory.OperatorDeclaration', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reconciliation_runs',
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    expected_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    declared_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revenue_variance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    expected_taxable_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    declared_taxable_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    taxable_revenue_variance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    expected_gst = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    declared_gst = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    gst_variance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    risk_level = models.CharField(max_length=10, default='MATCHED')
    tolerance_percent = models.DecimalField(max_digits=6, decimal_places=2, default=1)
    previous_run = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='reruns')
    failure_reason = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'regulatory_reconciliation_runs'
        ordering = ['-created_at']

    def __str__(self):
        op = self.operator_code or 'ALL'
        return f'{op} | {self.level} {self.period_start}→{self.period_end} [{self.status}]'


class ReconciliationResult(models.Model):
    """Per-service result within a reconciliation run."""

    class MatchStatus(models.TextChoices):
        MATCHED = 'MATCHED', 'Matched'
        MINOR_VARIANCE = 'MINOR', 'Minor Variance'
        MAJOR_VARIANCE = 'MAJOR', 'Major Variance'
        CRITICAL_VARIANCE = 'CRITICAL', 'Critical Variance'
        NO_DECLARATION = 'NO_DECL', 'No Declaration'

    run = models.ForeignKey(
        ReconciliationRun, on_delete=models.CASCADE, related_name='results',
    )
    operator_code = models.SlugField(max_length=30)
    service_type = models.CharField(max_length=20)

    mediated_count = models.BigIntegerField(default=0)
    mediated_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    mediated_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    declared_count = models.BigIntegerField(default=0)
    declared_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    declared_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    variance_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    variance_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    variance_pct = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    match_status = models.CharField(
        max_length=10, choices=MatchStatus.choices, default=MatchStatus.MATCHED,
    )

    class Meta:
        db_table = 'regulatory_reconciliation_results'
        ordering = ['run', 'operator_code', 'service_type']

    def __str__(self):
        return f'{self.operator_code} {self.service_type}: {self.match_status}'


class Discrepancy(models.Model):
    """A flagged discrepancy within a reconciliation result."""

    class Severity(models.TextChoices):
        LOW = 'LOW', 'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH = 'HIGH', 'High'
        CRITICAL = 'CRITICAL', 'Critical'

    class ResolutionStatus(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        INVESTIGATING = 'INVESTIGATING', 'Investigating'
        RESOLVED = 'RESOLVED', 'Resolved'
        ACCEPTED = 'ACCEPTED', 'Accepted (within tolerance)'

    result = models.ForeignKey(
        ReconciliationResult, on_delete=models.CASCADE, related_name='discrepancies',
    )
    severity = models.CharField(max_length=10, choices=Severity.choices)
    description = models.TextField()
    amount_at_risk = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    resolution_status = models.CharField(
        max_length=15, choices=ResolutionStatus.choices, default=ResolutionStatus.OPEN,
    )
    resolution_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'regulatory_discrepancies'
        ordering = ['-created_at']
        verbose_name_plural = 'Discrepancies'

    def __str__(self):
        return f'[{self.severity}] {self.description[:80]}'
