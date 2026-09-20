from datetime import date

from django.conf import settings
from django.db import models
from django.db.models import Max
from django.utils import timezone


class AlertType(models.TextChoices):
    """Shared alert-type taxonomy for RiskRule and RiskAlert."""
    TAX = 'TAX', 'Tax'
    GST = 'GST', 'GST'
    REVENUE = 'REVENUE', 'Revenue'
    DATA_QUALITY = 'DATA_QUALITY', 'Data Quality'
    COMPLIANCE = 'COMPLIANCE', 'Compliance'
    TARIFF = 'TARIFF', 'Tariff'
    TRAFFIC = 'TRAFFIC', 'Traffic'
    DECLARATION = 'DECLARATION', 'Declaration'
    RECONCILIATION = 'RECONCILIATION', 'Reconciliation'
    OTHER = 'OTHER', 'Other'


class SourceType(models.TextChoices):
    """Where a risk alert originated from."""
    RECONCILIATION = 'RECONCILIATION', 'Reconciliation'
    GST_ENGINE = 'GST_ENGINE', 'GST Engine'
    REVENUE_ASSURANCE = 'REVENUE_ASSURANCE', 'Revenue Assurance'
    TARIFF_COMPLIANCE = 'TARIFF_COMPLIANCE', 'Tariff Compliance'
    OPERATOR_DECLARATIONS = 'OPERATOR_DECLARATIONS', 'Operator Declarations'
    MEDIATION_DATA_QUALITY = 'MEDIATION_DATA_QUALITY', 'Mediation Data Quality'
    TRAFFIC_MONITORING = 'TRAFFIC_MONITORING', 'Traffic Monitoring'
    MANUAL_RULE = 'MANUAL_RULE', 'Manual Rule'
    AUDIT_FINDING = 'AUDIT_FINDING', 'Audit Finding'


class RiskRule(models.Model):
    """Configurable threshold / anomaly detection rule."""

    class RuleType(models.TextChoices):
        THRESHOLD = 'THRESHOLD', 'Threshold'
        ANOMALY = 'ANOMALY', 'Anomaly Detection'
        TREND = 'TREND', 'Trend Analysis'

    class Severity(models.TextChoices):
        LOW = 'LOW', 'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH = 'HIGH', 'High'
        CRITICAL = 'CRITICAL', 'Critical'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'
        ARCHIVED = 'ARCHIVED', 'Archived'

    class ComparisonOperator(models.TextChoices):
        GT = 'GT', '>'
        GTE = 'GTE', '>='
        LT = 'LT', '<'
        LTE = 'LTE', '<='
        EQ = 'EQ', '='
        NE = 'NE', '!='
        PCT_VARIANCE = 'PCT_VARIANCE', '% variance greater than'
        ABS_VARIANCE = 'ABS_VARIANCE', 'absolute variance greater than'

    class ThresholdUnit(models.TextChoices):
        PERCENTAGE = 'PERCENTAGE', 'Percentage'
        COUNT = 'COUNT', 'Count'
        CURRENCY = 'CURRENCY', 'Currency'
        DURATION = 'DURATION', 'Duration'
        RATE = 'RATE', 'Rate'
        RATIO = 'RATIO', 'Ratio'

    AlertType = AlertType

    name = models.CharField(max_length=200, unique=True)
    code = models.CharField(max_length=40, blank=True)
    alert_type = models.CharField(max_length=30, choices=AlertType.choices, default=AlertType.OTHER)
    threshold_unit = models.CharField(max_length=20, choices=ThresholdUnit.choices, default=ThresholdUnit.COUNT)
    version = models.PositiveIntegerField(default=1)
    rule_type = models.CharField(max_length=12, choices=RuleType.choices, default=RuleType.THRESHOLD)
    comparison_operator = models.CharField(max_length=15, choices=ComparisonOperator.choices, default=ComparisonOperator.GT)
    description = models.TextField(blank=True)
    metric = models.CharField(
        max_length=100,
        help_text='Metric to evaluate (e.g. revenue_variance_pct, traffic_drop_pct)',
    )
    operator_code = models.SlugField(
        max_length=30, blank=True,
        help_text='Blank = applies to all operators',
    )
    service_scope = models.CharField(max_length=60, blank=True, help_text='Blank = applies to all services')
    threshold_value = models.DecimalField(max_digits=12, decimal_places=4)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MEDIUM)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    enabled = models.BooleanField(default=True)

    min_duration = models.PositiveIntegerField(null=True, blank=True, help_text='Minutes the condition must persist')
    min_occurrences = models.PositiveIntegerField(null=True, blank=True)
    lookback_period = models.PositiveIntegerField(null=True, blank=True, help_text='Days of history to evaluate')
    auto_create_alert = models.BooleanField(default=True)
    auto_escalate = models.BooleanField(default=False)
    escalation_threshold = models.PositiveIntegerField(null=True, blank=True, help_text='Occurrence count that triggers auto audit-case escalation')

    effective_from = models.DateField(default=date.today)
    effective_to = models.DateField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'regulatory_risk_rules'
        ordering = ['name']

    def __str__(self):
        return f'{self.name} [{self.severity}]'

    def save(self, *args, **kwargs):
        self.enabled = self.status == self.Status.ACTIVE
        super().save(*args, **kwargs)


class RiskRuleVersion(models.Model):
    """Historical snapshot of a RiskRule prior to a material change.

    Preserves the exact configuration (threshold, operator, severity, etc.) that was
    active when a given RiskAlert was triggered, even after the live rule is edited.
    """

    rule = models.ForeignKey(RiskRule, on_delete=models.CASCADE, related_name='versions')
    version = models.PositiveIntegerField()

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=40, blank=True)
    alert_type = models.CharField(max_length=30)
    metric = models.CharField(max_length=100)
    comparison_operator = models.CharField(max_length=15)
    threshold_value = models.DecimalField(max_digits=12, decimal_places=4)
    threshold_unit = models.CharField(max_length=20)
    severity = models.CharField(max_length=10)
    operator_code = models.SlugField(max_length=30, blank=True)
    service_scope = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)

    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'regulatory_risk_rule_versions'
        ordering = ['-version']
        unique_together = ('rule', 'version')

    def __str__(self):
        return f'{self.name} v{self.version}'

    @classmethod
    def snapshot(cls, rule, effective_to=None, user=None):
        """Freeze the current state of `rule` as a version record."""
        return cls.objects.create(
            rule=rule, version=rule.version, name=rule.name, code=rule.code,
            alert_type=rule.alert_type, metric=rule.metric, comparison_operator=rule.comparison_operator,
            threshold_value=rule.threshold_value, threshold_unit=rule.threshold_unit, severity=rule.severity,
            operator_code=rule.operator_code, service_scope=rule.service_scope, description=rule.description,
            effective_from=rule.effective_from, effective_to=effective_to or date.today(), created_by=user,
        )


class RiskAlert(models.Model):
    """An alert triggered by a risk rule, tracked through investigation to closure."""

    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        ASSIGNED = 'ASSIGNED', 'Assigned'
        INVESTIGATING = 'INVESTIGATING', 'Investigating'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
        AWAITING_OPERATOR = 'AWAITING_OPERATOR', 'Awaiting Operator'
        RESOLVED = 'RESOLVED', 'Resolved'
        DISMISSED = 'DISMISSED', 'Dismissed'
        CLOSED = 'CLOSED', 'Closed'

    OPEN_STATUSES = (Status.OPEN, Status.ASSIGNED, Status.INVESTIGATING, Status.UNDER_REVIEW, Status.AWAITING_OPERATOR)

    # Forward-workflow transitions; AWAITING_OPERATOR/UNDER_REVIEW can also move
    # back into INVESTIGATING (operator responds, or review sends it back) —
    # see allowed_next_statuses().
    _FORWARD = {
        Status.OPEN: [Status.ASSIGNED, Status.INVESTIGATING, Status.DISMISSED],
        Status.ASSIGNED: [Status.INVESTIGATING, Status.DISMISSED],
        Status.INVESTIGATING: [Status.AWAITING_OPERATOR, Status.UNDER_REVIEW, Status.RESOLVED, Status.DISMISSED],
        Status.AWAITING_OPERATOR: [Status.UNDER_REVIEW, Status.INVESTIGATING],
        Status.UNDER_REVIEW: [Status.INVESTIGATING, Status.AWAITING_OPERATOR, Status.RESOLVED, Status.DISMISSED],
        Status.RESOLVED: [Status.CLOSED],
        Status.DISMISSED: [],
        Status.CLOSED: [],
    }

    class ResolutionType(models.TextChoices):
        NO_ISSUE = 'NO_ISSUE', 'No Issue'
        OPERATOR_CORRECTED = 'OPERATOR_CORRECTED', 'Operator Corrected'
        DATA_ISSUE_CORRECTED = 'DATA_ISSUE_CORRECTED', 'Data Issue Corrected'
        DECLARATION_AMENDED = 'DECLARATION_AMENDED', 'Declaration Amended'
        TAX_ADJUSTMENT = 'TAX_ADJUSTMENT', 'Tax Adjustment'
        TARIFF_CORRECTED = 'TARIFF_CORRECTED', 'Tariff Corrected'
        SYSTEM_FALSE_POSITIVE = 'SYSTEM_FALSE_POSITIVE', 'System False Positive'
        OTHER = 'OTHER', 'Other'

    class DismissalReason(models.TextChoices):
        DUPLICATE = 'DUPLICATE', 'Duplicate Alert'
        FALSE_POSITIVE = 'FALSE_POSITIVE', 'False Positive'
        INSUFFICIENT_EVIDENCE = 'INSUFFICIENT_EVIDENCE', 'Insufficient Evidence'
        WITHIN_TOLERANCE = 'WITHIN_TOLERANCE', 'Within Approved Tolerance'
        OTHER = 'OTHER', 'Other'

    AlertType = AlertType
    SourceType = SourceType

    rule = models.ForeignKey(
        RiskRule, on_delete=models.SET_NULL, null=True, blank=True, related_name='alerts',
    )
    rule_version = models.PositiveIntegerField(null=True, blank=True, help_text='RiskRule.version active when this alert was triggered')
    operator_code = models.SlugField(max_length=30, db_index=True)
    reference = models.CharField(max_length=24, unique=True, null=True, blank=True, db_index=True)
    alert_type = models.CharField(max_length=30, choices=AlertType.choices, default=AlertType.OTHER)
    threshold_unit = models.CharField(max_length=20, default='COUNT')
    source_type = models.CharField(max_length=40, choices=SourceType.choices, default=SourceType.MANUAL_RULE)
    source_reference = models.CharField(max_length=80, blank=True)
    occurrence_count = models.PositiveIntegerField(default=1)
    potential_exposure = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    severity = models.CharField(max_length=10, choices=RiskRule.Severity.choices)
    metric_value = models.DecimalField(max_digits=18, decimal_places=4)
    threshold_value = models.DecimalField(max_digits=12, decimal_places=4)
    description = models.TextField()

    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    service = models.CharField(max_length=60, blank=True)
    traffic_type = models.CharField(max_length=60, blank=True)

    status = models.CharField(
        max_length=18, choices=Status.choices, default=Status.OPEN, db_index=True,
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=RiskRule.Severity.choices, blank=True)
    due_date = models.DateField(null=True, blank=True)
    assignment_notes = models.TextField(blank=True)

    investigator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    investigation_started_at = models.DateTimeField(null=True, blank=True)
    investigation_notes = models.TextField(blank=True)

    triggered_at = models.DateTimeField(auto_now_add=True, db_index=True)

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_type = models.CharField(max_length=25, choices=ResolutionType.choices, blank=True)
    resolution_notes = models.TextField(blank=True)
    recovered_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    confirmed_exposure = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    dismissed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    dismissed_at = models.DateTimeField(null=True, blank=True)
    dismissal_reason = models.CharField(max_length=25, choices=DismissalReason.choices, blank=True)
    dismissal_notes = models.TextField(blank=True)

    audit_case = models.ForeignKey(
        'regulatory.AuditCase', on_delete=models.SET_NULL, null=True, blank=True, related_name='risk_alerts',
    )

    class Meta:
        db_table = 'regulatory_risk_alerts'
        ordering = ['-triggered_at']

    def __str__(self):
        return f'[{self.severity}] {self.operator_code}: {self.description[:60]}'

    @classmethod
    def generate_reference(cls):
        """Return the next readable alert reference for the current year, e.g. RA-2026-0024."""
        year = timezone.now().year
        prefix = f'RA-{year}-'
        latest = cls.objects.filter(reference__startswith=prefix).aggregate(sequence=Max('reference'))['sequence']
        sequence = int(latest.rsplit('-', 1)[-1]) + 1 if latest else 1
        return f'{prefix}{sequence:04d}'

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self.generate_reference()
        super().save(*args, **kwargs)

    def allowed_next_statuses(self):
        return self._FORWARD[self.Status(self.status)]


class RiskAlertOperatorResponse(models.Model):
    """One request/response exchange with an operator during an alert investigation."""

    class ResponseStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        REJECTED = 'REJECTED', 'Rejected'

    alert = models.ForeignKey(RiskAlert, on_delete=models.CASCADE, related_name='operator_responses')

    request_subject = models.CharField(max_length=200)
    request_details = models.TextField()
    required_evidence = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=RiskRule.Severity.choices, blank=True)
    notes = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    requested_at = models.DateTimeField(auto_now_add=True)
    due_date = models.DateField(null=True, blank=True)

    response_text = models.TextField(blank=True)
    submitted_by = models.CharField(max_length=150, blank=True, help_text='Operator contact who submitted the response')
    submitted_at = models.DateTimeField(null=True, blank=True)
    response_status = models.CharField(max_length=15, choices=ResponseStatus.choices, default=ResponseStatus.PENDING)

    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        db_table = 'regulatory_risk_alert_operator_responses'
        ordering = ['-requested_at']

    def __str__(self):
        return f'{self.alert.reference}: {self.request_subject} [{self.response_status}]'

    @property
    def is_overdue(self):
        return bool(self.due_date and self.response_status == self.ResponseStatus.PENDING and self.due_date < date.today())


class RiskAlertComment(models.Model):
    """Free-form analyst commentary on a risk alert investigation."""

    alert = models.ForeignKey(RiskAlert, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'regulatory_risk_alert_comments'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.alert.reference}: {self.comment[:60]}'
