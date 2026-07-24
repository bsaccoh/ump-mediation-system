"""
Regulatory Service Models
==========================
Tables backing the NATCOM regulatory workflows: periodic reports, levy/USF
computation, manual retail revenue entry, lawful-intercept (LEA) request
tracking + evidentiary export logs, and QoS metric snapshots.

Money fields use ``Decimal`` (max_digits=18) to avoid float drift.  All
period-keyed tables use ``(period_year, period_month)`` for natural unique
keys.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# 1. RegulatoryProfile — singleton config
# ---------------------------------------------------------------------------

class RegulatoryProfile(models.Model):
    """NATCOM contact + current levy / USF percentages.

    Enforced singleton: ``Meta.constraints`` allows only one row.  Use
    :py:meth:`get_or_create_default` to fetch it.
    """

    regulator_name = models.CharField(max_length=120, default='NATCOM')
    contact_email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=40, blank=True)

    levy_pct = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal('0.5000'),
        help_text='Regulatory levy as % of gross revenue (e.g. 0.5 = 0.5%)',
    )
    usf_pct = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal('1.0000'),
        help_text='Universal Service Fund contribution as % of gross revenue',
    )
    home_currency = models.CharField(max_length=3, default='SLE')

    # Single-row enforcement
    singleton = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )

    class Meta:
        db_table = 'reg_profile'
        verbose_name = 'Regulatory Profile'
        verbose_name_plural = 'Regulatory Profile'

    def __str__(self):
        return f'{self.regulator_name} (levy {self.levy_pct}% / USF {self.usf_pct}%)'

    @classmethod
    def get_or_create_default(cls) -> 'RegulatoryProfile':
        obj, _ = cls.objects.get_or_create(singleton=1)
        return obj


# ---------------------------------------------------------------------------
# 2. RetailRevenue — manual retail figures per period
# ---------------------------------------------------------------------------

class RetailRevenue(models.Model):
    """Manual entry of retail revenue per month.

    Retail billing lives in a separate system; finance keys figures in here
    monthly so they feed into the levy/USF calculation alongside the
    interconnect Invoice totals.
    """

    period_year = models.IntegerField(db_index=True)
    period_month = models.IntegerField(db_index=True,
                                        help_text='1-12')

    voice_revenue = models.DecimalField(max_digits=18, decimal_places=2,
                                          default=Decimal('0'))
    sms_revenue = models.DecimalField(max_digits=18, decimal_places=2,
                                        default=Decimal('0'))
    data_revenue = models.DecimalField(max_digits=18, decimal_places=2,
                                         default=Decimal('0'))
    other_revenue = models.DecimalField(max_digits=18, decimal_places=2,
                                          default=Decimal('0'),
                                          help_text='Roaming retail, value-added services, etc.')
    currency = models.CharField(max_length=3, default='SLE')

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )

    class Meta:
        db_table = 'reg_retail_revenue'
        ordering = ['-period_year', '-period_month']
        unique_together = [('period_year', 'period_month')]
        verbose_name = 'Retail Revenue'
        verbose_name_plural = 'Retail Revenue'

    def __str__(self):
        return f'{self.period_year}-{self.period_month:02d}: {self.total} {self.currency}'

    @property
    def total(self) -> Decimal:
        return (self.voice_revenue + self.sms_revenue +
                self.data_revenue + self.other_revenue)


# ---------------------------------------------------------------------------
# 3. RegulatoryReport — registry of generated NATCOM reports
# ---------------------------------------------------------------------------

class RegulatoryReport(models.Model):
    """One generated regulatory report (PDF + Excel artefacts attached)."""

    class ReportType(models.TextChoices):
        TRAFFIC               = 'TRAFFIC',               'Traffic Summary'
        REVENUE               = 'REVENUE',               'Revenue Summary'
        SUBSCRIBER            = 'SUBSCRIBER',            'Subscriber Summary'
        INTERCONNECT_SUMMARY  = 'INTERCONNECT_SUMMARY',  'Interconnect Summary'

    class Status(models.TextChoices):
        DRAFT     = 'DRAFT',     'Draft'
        FINAL     = 'FINAL',     'Final'
        SUBMITTED = 'SUBMITTED', 'Submitted to NATCOM'

    report_type = models.CharField(max_length=24, choices=ReportType.choices,
                                     db_index=True)
    period_start = models.DateField(db_index=True)
    period_end = models.DateField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices,
                                default=Status.DRAFT, db_index=True)

    pdf_file = models.FileField(upload_to='regulatory/reports/',
                                  blank=True, null=True)
    xlsx_file = models.FileField(upload_to='regulatory/reports/',
                                   blank=True, null=True)

    summary_json = models.JSONField(default=dict, blank=True,
                                      help_text='Aggregated report data (for re-rendering)')
    notes = models.TextField(blank=True)

    generated_at = models.DateTimeField(auto_now_add=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'reg_reports'
        ordering = ['-generated_at']
        verbose_name = 'Regulatory Report'
        verbose_name_plural = 'Regulatory Reports'

    def __str__(self):
        return (f'{self.get_report_type_display()} '
                f'{self.period_start}..{self.period_end} [{self.status}]')


# ---------------------------------------------------------------------------
# 4. LeviedPeriod — computed levy + USF for a period
# ---------------------------------------------------------------------------

class LeviedPeriod(models.Model):
    """Levy + USF computation for one billing period."""

    class Status(models.TextChoices):
        DRAFT     = 'DRAFT',     'Draft'
        COMPUTED  = 'COMPUTED',  'Computed'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        PAID      = 'PAID',      'Paid'

    period_year = models.IntegerField(db_index=True)
    period_month = models.IntegerField(db_index=True)

    # Snapshots taken at compute time
    interconnect_inbound = models.DecimalField(max_digits=18, decimal_places=2,
                                                  default=Decimal('0'))
    interconnect_outbound = models.DecimalField(max_digits=18, decimal_places=2,
                                                   default=Decimal('0'))
    retail_total = models.DecimalField(max_digits=18, decimal_places=2,
                                         default=Decimal('0'))
    gross_revenue = models.DecimalField(max_digits=18, decimal_places=2,
                                          default=Decimal('0'))

    levy_pct = models.DecimalField(max_digits=6, decimal_places=4,
                                     default=Decimal('0'))
    usf_pct = models.DecimalField(max_digits=6, decimal_places=4,
                                    default=Decimal('0'))
    levy_amount = models.DecimalField(max_digits=18, decimal_places=2,
                                        default=Decimal('0'))
    usf_amount = models.DecimalField(max_digits=18, decimal_places=2,
                                       default=Decimal('0'))
    total_payable = models.DecimalField(max_digits=18, decimal_places=2,
                                          default=Decimal('0'))
    currency = models.CharField(max_length=3, default='SLE')

    status = models.CharField(max_length=10, choices=Status.choices,
                                default=Status.DRAFT, db_index=True)
    due_date = models.DateField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=120, blank=True)

    computed_at = models.DateTimeField(null=True, blank=True)
    computed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'reg_levies'
        ordering = ['-period_year', '-period_month']
        unique_together = [('period_year', 'period_month')]
        verbose_name = 'Levied Period'
        verbose_name_plural = 'Levied Periods'

    def __str__(self):
        return (f'{self.period_year}-{self.period_month:02d} '
                f'{self.total_payable} {self.currency} [{self.status}]')


# ---------------------------------------------------------------------------
# 5. LEARequest — Lawful Intercept request from a law-enforcement agency
# ---------------------------------------------------------------------------

class LEARequest(models.Model):
    """A police/court request for CDR evidence on a target subscriber."""

    class Status(models.TextChoices):
        OPEN        = 'OPEN',        'Open'
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        FULFILLED   = 'FULFILLED',   'Fulfilled'
        DENIED      = 'DENIED',      'Denied'

    case_number = models.CharField(max_length=60, unique=True,
                                     help_text='Court ref / police case ID')
    requesting_agency = models.CharField(max_length=120,
                                           help_text='e.g. Sierra Leone Police, ACC')
    officer_name = models.CharField(max_length=120, blank=True)
    officer_contact = models.CharField(max_length=120, blank=True)
    legal_basis = models.CharField(max_length=200, blank=True,
                                     help_text='Court order / warrant reference')

    # Scope filters — any combination
    filter_msisdn = models.CharField(max_length=20, blank=True, db_index=True)
    filter_imsi = models.CharField(max_length=20, blank=True, db_index=True)
    filter_imei = models.CharField(max_length=20, blank=True, db_index=True)
    filter_cell_id = models.CharField(max_length=20, blank=True)

    filter_start = models.DateTimeField(help_text='Inclusive lower bound')
    filter_end = models.DateTimeField(help_text='Exclusive upper bound')

    status = models.CharField(max_length=12, choices=Status.choices,
                                default=Status.OPEN, db_index=True)

    opened_at = models.DateTimeField(auto_now_add=True)
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lea_requests_opened',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    fulfilled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lea_requests_fulfilled',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )

    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'reg_lea_requests'
        ordering = ['-opened_at']
        verbose_name = 'LEA Request'
        verbose_name_plural = 'LEA Requests'

    def __str__(self):
        return f'{self.case_number} ({self.requesting_agency}) [{self.status}]'


# ---------------------------------------------------------------------------
# 6. LEAExtractionLog — chain-of-custody log per export
# ---------------------------------------------------------------------------

class LEAExtractionLog(models.Model):
    """Audit trail of CDR pulls executed against an LEA request."""

    request = models.ForeignKey(LEARequest, on_delete=models.CASCADE,
                                 related_name='extractions')
    executed_at = models.DateTimeField(auto_now_add=True)
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )
    record_count = models.IntegerField(default=0)
    criteria_json = models.JSONField(default=dict, blank=True,
                                       help_text='Exact filter applied at run time')
    export_file = models.FileField(upload_to='regulatory/lea/',
                                     blank=True, null=True)
    sha256 = models.CharField(max_length=64, blank=True,
                                help_text='SHA-256 of the export file (chain of custody)')
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'reg_lea_extractions'
        ordering = ['-executed_at']
        verbose_name = 'LEA Extraction'
        verbose_name_plural = 'LEA Extractions'

    def __str__(self):
        return f'{self.request.case_number} @ {self.executed_at:%Y-%m-%d %H:%M} ({self.record_count} records)'


# ---------------------------------------------------------------------------
# 7. QoSMetric — daily / monthly KPI snapshot
# ---------------------------------------------------------------------------

class QoSMetric(models.Model):
    """One QoS / KPI snapshot.  Daily rows roll up to monthly."""

    class Granularity(models.TextChoices):
        DAILY   = 'DAILY',   'Daily'
        MONTHLY = 'MONTHLY', 'Monthly'

    class Source(models.TextChoices):
        COMPUTED = 'COMPUTED', 'Computed from CDRs'
        MANUAL   = 'MANUAL',   'Manual entry'

    metric_date = models.DateField(db_index=True)
    granularity = models.CharField(max_length=8, choices=Granularity.choices,
                                     default=Granularity.DAILY, db_index=True)

    total_calls = models.IntegerField(default=0)
    successful_calls = models.IntegerField(default=0)
    dropped_calls = models.IntegerField(default=0)
    failed_calls = models.IntegerField(default=0)

    asr_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'),
                                    help_text='Answer Seizure Ratio %')
    acd_seconds = models.DecimalField(max_digits=8, decimal_places=2,
                                        default=Decimal('0'),
                                        help_text='Average Call Duration (seconds)')
    drop_rate_pct = models.DecimalField(max_digits=5, decimal_places=2,
                                          default=Decimal('0'))
    availability_pct = models.DecimalField(max_digits=5, decimal_places=2,
                                              default=Decimal('100'))

    source = models.CharField(max_length=10, choices=Source.choices,
                                default=Source.COMPUTED)
    notes = models.TextField(blank=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'reg_qos_metrics'
        ordering = ['-metric_date', 'granularity']
        unique_together = [('metric_date', 'granularity')]
        verbose_name = 'QoS Metric'
        verbose_name_plural = 'QoS Metrics'

    def __str__(self):
        return f'{self.metric_date} [{self.granularity}] ASR={self.asr_pct}% Drop={self.drop_rate_pct}%'
