from django.conf import settings
from django.db import models


class RegulatoryRule(models.Model):
    """
    A regulatory KPI rule defined by the authority (NATCA, NRA, etc.).
    Thresholds are stored separately so they can vary by operator and date
    without modifying the rule itself.
    """

    rule_code = models.CharField(max_length=50, unique=True)  # e.g. 'NATCA-RSRP-4G-001'
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    # Scope
    technology = models.CharField(max_length=5, blank=True)    # empty = all technologies
    service_type = models.CharField(max_length=10, blank=True) # empty = all services

    # What to measure
    metric = models.CharField(max_length=50)    # 'rsrp', 'call_setup_time_ms', 'mos', …
    condition = models.CharField(max_length=3)  # 'lt'/'le'/'gt'/'ge'/'eq'/'ne'

    # Source
    regulatory_reference = models.CharField(max_length=200, blank=True)
    authority = models.CharField(max_length=100, blank=True)  # 'NATCA', 'NRA', …

    is_active = models.BooleanField(default=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['rule_code']

    def __str__(self):
        return f'{self.rule_code} – {self.name}'


class RegulatoryThreshold(models.Model):
    """
    Numeric threshold value for a RegulatoryRule.
    operator=None means the threshold applies to all operators.
    """

    rule = models.ForeignKey(
        RegulatoryRule, on_delete=models.CASCADE, related_name='thresholds'
    )
    operator = models.ForeignKey(
        'reference.Operator', on_delete=models.CASCADE,
        null=True, blank=True, related_name='regulatory_thresholds',
    )
    warning_value = models.FloatField(null=True, blank=True)
    critical_value = models.FloatField()
    unit = models.CharField(max_length=20, blank=True)  # 'dBm', 'ms', '%', …
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['rule', 'operator']

    def __str__(self):
        op = self.operator.code if self.operator else 'ALL'
        return f'{self.rule.rule_code} / {op}: critical={self.critical_value}'


class Finding(models.Model):
    """
    A quality or compliance issue identified during drive-test analysis.
    Findings are generated automatically by the analysis engine or raised manually.
    """

    class Severity(models.TextChoices):
        INFO = 'INFO', 'Info'
        LOW = 'LOW', 'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH = 'HIGH', 'High'
        CRITICAL = 'CRITICAL', 'Critical'

    class FindingType(models.TextChoices):
        COVERAGE_HOLE = 'COVERAGE_HOLE', 'Coverage Hole'
        WEAK_SIGNAL = 'WEAK_SIGNAL', 'Weak Signal'
        HIGH_INTERFERENCE = 'HIGH_INTERFERENCE', 'High Interference'
        DROP_CALL = 'DROP_CALL', 'Dropped Call'
        FAILED_CALL = 'FAILED_CALL', 'Failed Call Setup'
        POOR_DATA = 'POOR_DATA', 'Poor Data Service'
        LOW_THROUGHPUT = 'LOW_THROUGHPUT', 'Low Throughput'
        HIGH_LATENCY = 'HIGH_LATENCY', 'High Latency'
        THRESHOLD_BREACH = 'THRESHOLD_BREACH', 'Threshold Breach'
        ANOMALY = 'ANOMALY', 'Data Anomaly'

    session = models.ForeignKey(
        'DriveTestSession', on_delete=models.CASCADE, related_name='findings'
    )
    measurement = models.ForeignKey(
        'Measurement', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='findings',
    )
    finding_type = models.CharField(max_length=30, choices=FindingType.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices)

    # Location of the finding
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    cell = models.ForeignKey(
        'Cell', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='findings', db_constraint=False,
    )

    description = models.TextField()
    measured_value = models.FloatField(null=True, blank=True)
    threshold_value = models.FloatField(null=True, blank=True)
    threshold = models.ForeignKey(
        RegulatoryThreshold, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='findings',
    )

    # Resolution
    is_resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='resolved_dt_findings',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['session', 'severity']),
            models.Index(fields=['is_resolved']),
        ]

    def __str__(self):
        return f'{self.severity} {self.finding_type} in {self.session.session_ref}'


class DataQualityResult(models.Model):
    """Quality assessment computed after a DriveTestFile is processed."""

    drive_file = models.OneToOneField(
        'DriveTestFile', on_delete=models.CASCADE, related_name='quality_result'
    )
    total_records = models.IntegerField(default=0)
    valid_records = models.IntegerField(default=0)
    invalid_records = models.IntegerField(default=0)
    missing_gps = models.IntegerField(default=0)
    missing_cell_id = models.IntegerField(default=0)
    missing_signal = models.IntegerField(default=0)

    # Cell matching
    matched_cells = models.IntegerField(default=0)
    unmatched_cells = models.IntegerField(default=0)
    match_rate_pct = models.FloatField(default=0.0)

    # Quality scores (0–100)
    completeness_score = models.FloatField(default=0.0)
    accuracy_score = models.FloatField(default=0.0)
    overall_score = models.FloatField(default=0.0)

    # Structured list of issue descriptions
    issues = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Quality: {self.overall_score:.1f}% — {self.drive_file.original_filename}'
