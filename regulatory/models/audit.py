from django.conf import settings
from django.db import models
from django.db.models import Max
from django.utils import timezone

from regulatory.models.risk import RiskRule

RiskLevel = RiskRule.Severity  # reuse the exact Low/Medium/High/Critical scale + colors from Risk Alerts


class AuditCase(models.Model):
    """A regulatory audit investigation, escalated from a risk alert, reconciliation
    discrepancy, tariff-compliance violation, or opened manually."""

    class CaseType(models.TextChoices):
        GST_VARIANCE = 'GST_VARIANCE', 'GST Variance'
        REVENUE_VARIANCE = 'REVENUE_VARIANCE', 'Revenue Variance'
        TAXABLE_REVENUE_VARIANCE = 'TAXABLE_REVENUE_VARIANCE', 'Taxable Revenue Variance'
        MISSING_CDRS = 'MISSING_CDRS', 'Missing CDRs'
        TARIFF_VIOLATION = 'TARIFF_VIOLATION', 'Tariff Violation'
        TAX_CLASSIFICATION = 'TAX_CLASSIFICATION', 'Tax Classification'
        DECLARATION_DISCREPANCY = 'DECLARATION_DISCREPANCY', 'Declaration Discrepancy'
        DATA_QUALITY = 'DATA_QUALITY', 'Data Quality'
        TRAFFIC_ANOMALY = 'TRAFFIC_ANOMALY', 'Traffic Anomaly'
        REPEATED_COMPLIANCE_FAILURE = 'REPEATED_COMPLIANCE_FAILURE', 'Repeated Compliance Failure'
        OTHER = 'OTHER', 'Other'

    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        ASSIGNED = 'ASSIGNED', 'Assigned'
        INVESTIGATION = 'INVESTIGATION', 'Investigation'
        AWAITING_OPERATOR = 'AWAITING_OPERATOR', 'Awaiting Operator'
        OPERATOR_RESPONSE = 'OPERATOR_RESPONSE', 'Operator Response'
        REGULATORY_REVIEW = 'REGULATORY_REVIEW', 'Regulatory Review'
        RESOLVED = 'RESOLVED', 'Resolved'
        CLOSED = 'CLOSED', 'Closed'

    class ResolutionType(models.TextChoices):
        NO_ISSUE = 'NO_ISSUE', 'No Issue'
        OPERATOR_CORRECTION = 'OPERATOR_CORRECTION', 'Operator Correction'
        DECLARATION_AMENDED = 'DECLARATION_AMENDED', 'Declaration Amended'
        TAX_ADJUSTMENT = 'TAX_ADJUSTMENT', 'Tax Adjustment'
        REVENUE_RECOVERED = 'REVENUE_RECOVERED', 'Revenue Recovered'
        TARIFF_CORRECTED = 'TARIFF_CORRECTED', 'Tariff Corrected'
        DATA_QUALITY_CORRECTED = 'DATA_QUALITY_CORRECTED', 'Data Quality Corrected'
        COMPLIANCE_ACTION = 'COMPLIANCE_ACTION', 'Compliance Action'
        ADMINISTRATIVE_CLOSURE = 'ADMINISTRATIVE_CLOSURE', 'Administrative Closure'
        OTHER = 'OTHER', 'Other'

    class RegulatoryDecision(models.TextChoices):
        NO_FURTHER_ACTION = 'NO_FURTHER_ACTION', 'No Further Action'
        OPERATOR_CORRECTION_REQUIRED = 'OPERATOR_CORRECTION_REQUIRED', 'Operator Correction Required'
        ADDITIONAL_TAX_REVIEW = 'ADDITIONAL_TAX_REVIEW', 'Additional Tax Review Required'
        ADDITIONAL_REVENUE_REVIEW = 'ADDITIONAL_REVENUE_REVIEW', 'Additional Revenue Review Required'
        FORMAL_COMPLIANCE_REVIEW = 'FORMAL_COMPLIANCE_REVIEW', 'Formal Compliance Review'
        REFERRAL_FOR_ENFORCEMENT = 'REFERRAL_FOR_ENFORCEMENT', 'Referral for Enforcement'
        OTHER = 'OTHER', 'Other'

    class Priority(models.TextChoices):
        """Operational urgency for the investigator's queue — distinct from `risk_level`,
        which is the regulatory severity of the underlying finding."""
        LOW = 'LOW', 'Low'
        NORMAL = 'NORMAL', 'Normal'
        HIGH = 'HIGH', 'High'
        URGENT = 'URGENT', 'Urgent'

    RiskLevel = RiskLevel

    # Forward-workflow transitions; a case may also move sideways back into
    # INVESTIGATION from AWAITING_OPERATOR/REGULATORY_REVIEW, and jump straight
    # to RESOLVED from INVESTIGATION — see allowed_next_statuses().
    _FORWARD = {
        Status.OPEN: [Status.ASSIGNED, Status.INVESTIGATION],
        Status.ASSIGNED: [Status.INVESTIGATION],
        Status.INVESTIGATION: [Status.AWAITING_OPERATOR, Status.REGULATORY_REVIEW, Status.RESOLVED],
        Status.AWAITING_OPERATOR: [Status.OPERATOR_RESPONSE, Status.INVESTIGATION],
        Status.OPERATOR_RESPONSE: [Status.REGULATORY_REVIEW],
        Status.REGULATORY_REVIEW: [Status.RESOLVED, Status.INVESTIGATION],
        Status.RESOLVED: [Status.CLOSED],
        Status.CLOSED: [],
    }

    case_number = models.CharField(max_length=50, unique=True, db_index=True)
    title = models.CharField(max_length=300)
    case_type = models.CharField(max_length=30, choices=CaseType.choices, default=CaseType.OTHER)
    operator_code = models.SlugField(max_length=30, db_index=True)
    description = models.TextField(blank=True)
    finding_summary = models.CharField(max_length=255, blank=True, help_text='One-line finding shown in the case list')

    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)

    risk_level = models.CharField(max_length=10, choices=RiskLevel.choices, default=RiskLevel.MEDIUM, db_index=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)

    potential_exposure = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    confirmed_exposure = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    recovered_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    adjustment_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default='SLE')

    risk_alert = models.ForeignKey(
        'regulatory.RiskAlert', on_delete=models.SET_NULL, null=True, blank=True, related_name='primary_audit_cases',
    )
    reconciliation_run = models.ForeignKey(
        'regulatory.ReconciliationRun', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_cases',
    )
    declaration = models.ForeignKey(
        'regulatory.OperatorDeclaration', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_cases',
    )
    tariff_compliance_result = models.ForeignKey(
        'regulatory.TariffComplianceResult', on_delete=models.SET_NULL, null=True, blank=True, related_name='primary_audit_cases',
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assigned_audit_cases',
    )
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    assigned_at = models.DateTimeField(null=True, blank=True)
    assignment_notes = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)

    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    opened_at = models.DateTimeField(auto_now_add=True)

    investigation_started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    investigation_started_at = models.DateTimeField(null=True, blank=True)

    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_type = models.CharField(max_length=25, choices=ResolutionType.choices, blank=True)
    resolution_summary = models.TextField(blank=True)
    regulatory_decision = models.CharField(max_length=30, choices=RegulatoryDecision.choices, blank=True)
    recommendations = models.TextField(blank=True)

    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    closed_at = models.DateTimeField(null=True, blank=True)
    closure_notes = models.TextField(blank=True)
    follow_up_required = models.BooleanField(default=False)
    follow_up_date = models.DateField(null=True, blank=True)

    reopened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopen_reason = models.TextField(blank=True)
    reopen_count = models.PositiveIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'regulatory_audit_cases'
        ordering = ['-opened_at']

    def __str__(self):
        return f'{self.case_number}: {self.title[:60]} [{self.status}]'

    @classmethod
    def generate_case_number(cls):
        """Return the next readable, immutable audit case number for the current year."""
        year = timezone.now().year
        prefix = f'AUD-{year}-'
        latest = cls.objects.filter(case_number__startswith=prefix).aggregate(
            sequence=Max('case_number')
        )['sequence']
        sequence = int(latest.rsplit('-', 1)[-1]) + 1 if latest else 1
        return f'{prefix}{sequence:04d}'

    def allowed_next_statuses(self):
        return self._FORWARD[self.Status(self.status)]

    @property
    def outstanding_exposure(self):
        """Confirmed (or, absent that, potential) exposure net of amounts already recovered/adjusted."""
        base = self.confirmed_exposure if self.confirmed_exposure is not None else self.potential_exposure
        recovered = self.recovered_amount or 0
        adjustment = self.adjustment_amount or 0
        return base - recovered - adjustment


class AuditFinding(models.Model):
    """A specific finding within an audit case."""

    class FindingType(models.TextChoices):
        REVENUE_LEAKAGE = 'REVENUE_LEAKAGE', 'Revenue Leakage'
        TAX_UNDERPAYMENT = 'TAX_UNDERPAYMENT', 'Tax Underpayment'
        TARIFF_VIOLATION = 'TARIFF_VIOLATION', 'Tariff Violation'
        TRAFFIC_ANOMALY = 'TRAFFIC_ANOMALY', 'Traffic Anomaly'
        DATA_QUALITY = 'DATA_QUALITY', 'Data Quality Issue'
        COMPLIANCE = 'COMPLIANCE', 'Compliance Violation'
        OTHER = 'OTHER', 'Other'

    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        CONFIRMED = 'CONFIRMED', 'Confirmed'
        DISPUTED = 'DISPUTED', 'Disputed'
        RESOLVED = 'RESOLVED', 'Resolved'

    case = models.ForeignKey(
        AuditCase, on_delete=models.CASCADE, related_name='findings',
    )
    title = models.CharField(max_length=200, blank=True)
    finding_type = models.CharField(max_length=20, choices=FindingType.choices)
    severity = models.CharField(max_length=10, choices=RiskLevel.choices, default=RiskLevel.MEDIUM)
    description = models.TextField()

    expected_value = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    observed_value = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    variance = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    financial_exposure = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    recommendation = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'regulatory_audit_findings'
        ordering = ['case', '-created_at']

    def __str__(self):
        return f'{self.finding_type}: {self.description[:60]}'

    def save(self, *args, **kwargs):
        if self.expected_value is not None and self.observed_value is not None and self.variance is None:
            self.variance = self.observed_value - self.expected_value
        super().save(*args, **kwargs)


class AuditEvidence(models.Model):
    """Append-only evidence log for a case, optionally tied to a specific finding.
    Keeps the full traceability chain back to source mediation/regulatory data."""

    class EvidenceType(models.TextChoices):
        RISK_ALERT = 'RISK_ALERT', 'Risk Alert'
        RECONCILIATION = 'RECONCILIATION', 'Reconciliation Result'
        OPERATOR_DECLARATION = 'OPERATOR_DECLARATION', 'Operator Declaration'
        TARIFF_COMPLIANCE = 'TARIFF_COMPLIANCE', 'Tariff Compliance Result'
        REVENUE_CALCULATION = 'REVENUE_CALCULATION', 'Revenue Calculation'
        GST_CALCULATION = 'GST_CALCULATION', 'GST Calculation'
        TAX_RULE_VERSION = 'TAX_RULE_VERSION', 'Tax Rule Version'
        TARIFF_VERSION = 'TARIFF_VERSION', 'Tariff Version'
        CDR_SAMPLE = 'CDR_SAMPLE', 'CDR Sample'
        SOURCE_FILE = 'SOURCE_FILE', 'Source File'
        DOCUMENT = 'DOCUMENT', 'Uploaded Document'
        SCREENSHOT = 'SCREENSHOT', 'Screenshot'
        ANALYST_NOTE = 'ANALYST_NOTE', 'Analyst Note'
        TRAFFIC_SUMMARY = 'TRAFFIC_SUMMARY', 'Traffic Summary'

    case = models.ForeignKey(AuditCase, on_delete=models.CASCADE, related_name='evidence')
    finding = models.ForeignKey(
        AuditFinding, on_delete=models.SET_NULL, null=True, blank=True, related_name='evidence_items',
    )
    evidence_type = models.CharField(max_length=25, choices=EvidenceType.choices)
    description = models.TextField(blank=True)
    source_type = models.CharField(max_length=40, blank=True)
    reference_id = models.CharField(
        max_length=100, blank=True,
        help_text='PK/identifier of the referenced object (string to avoid cross-model FK)',
    )
    reference_url = models.CharField(max_length=500, blank=True)
    file = models.FileField(upload_to='regulatory/audit_evidence/%Y/%m/', null=True, blank=True)
    checksum = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'regulatory_audit_evidence'
        ordering = ['case', '-created_at']
        verbose_name_plural = 'Audit evidence'

    def __str__(self):
        return f'{self.evidence_type}: {(self.description or self.notes)[:60]}'


class AuditOperatorResponse(models.Model):
    """One request/response exchange with an operator during a case investigation."""

    class ResponseStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        REJECTED = 'REJECTED', 'Rejected'

    case = models.ForeignKey(AuditCase, on_delete=models.CASCADE, related_name='operator_responses')

    request_subject = models.CharField(max_length=200)
    request_details = models.TextField()
    required_evidence = models.TextField(blank=True)
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
        db_table = 'regulatory_audit_operator_responses'
        ordering = ['-requested_at']

    def __str__(self):
        return f'{self.case.case_number}: {self.request_subject} [{self.response_status}]'


class AuditCaseReport(models.Model):
    """One generated audit-report artifact. Every generation creates a new,
    numbered version — a prior issued report is never overwritten."""

    case = models.ForeignKey(AuditCase, on_delete=models.CASCADE, related_name='reports')
    version = models.PositiveIntegerField()
    file_format = models.CharField(max_length=10, default='XLSX')
    file = models.FileField(upload_to='regulatory/audit_reports/%Y/%m/')
    checksum = models.CharField(max_length=64, blank=True)
    case_status_at_generation = models.CharField(max_length=20, blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'regulatory_audit_case_reports'
        ordering = ['-version']
        unique_together = ('case', 'version')

    def __str__(self):
        return f'{self.case.case_number} report v{self.version}'

    @classmethod
    def next_version(cls, case):
        latest = cls.objects.filter(case=case).order_by('-version').values_list('version', flat=True).first()
        return (latest or 0) + 1


class AuditCaseComment(models.Model):
    """Internal (or, later, operator-visible) commentary on a case."""

    class Visibility(models.TextChoices):
        INTERNAL = 'INTERNAL', 'Internal'
        OPERATOR_VISIBLE = 'OPERATOR_VISIBLE', 'Operator-visible'

    case = models.ForeignKey(AuditCase, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    comment = models.TextField()
    visibility = models.CharField(max_length=20, choices=Visibility.choices, default=Visibility.INTERNAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'regulatory_audit_case_comments'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.case.case_number}: {self.comment[:60]}'
