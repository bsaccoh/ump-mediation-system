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


# ---------------------------------------------------------------------------
# 8. NetworkKPIDefinition — Registry of PM KPIs with NatCA thresholds
# ---------------------------------------------------------------------------

class NetworkKPIDefinition(models.Model):
    """Definition and regulatory threshold for a Performance Management (PM) KPI."""

    class Direction(models.TextChoices):
        ABOVE = 'ABOVE', 'Must be Greater/Equal Threshold'
        BELOW = 'BELOW', 'Must be Less/Equal Threshold'

    class Technology(models.TextChoices):
        TECH_2G = '2G', '2G (GSM)'
        TECH_3G = '3G', '3G (UMTS)'
        TECH_4G = '4G', '4G (LTE)'
        TECH_5G = '5G', '5G (NR)'
        ALL     = 'ALL', 'All Technologies / Composite'

    code = models.CharField(max_length=40, unique=True, db_index=True,
                            help_text='Unique identifier e.g. NET_AVAIL, CSSR, CDR')
    name = models.CharField(max_length=120)
    unit = models.CharField(max_length=20, default='%', help_text='%, Mbps, ms, score')
    description = models.TextField(blank=True)

    natca_threshold = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('95.00'),
        help_text='Regulatory target threshold set by NatCA',
    )
    threshold_direction = models.CharField(
        max_length=8, choices=Direction.choices, default=Direction.ABOVE,
    )
    technology = models.CharField(
        max_length=8, choices=Technology.choices, default=Technology.ALL,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'reg_kpi_definitions'
        ordering = ['code']
        verbose_name = 'Network KPI Definition'
        verbose_name_plural = 'Network KPI Definitions'

    def __str__(self):
        dir_str = '>=' if self.threshold_direction == self.Direction.ABOVE else '<='
        return f'{self.name} ({self.code}): target {dir_str} {self.natca_threshold} {self.unit}'


# ---------------------------------------------------------------------------
# 9. NetworkKPIEntry — Measurement entries per period/region
# ---------------------------------------------------------------------------

class NetworkKPIEntry(models.Model):
    """Measurement value for a PM KPI."""

    class Granularity(models.TextChoices):
        HOURLY  = 'HOURLY',  'Hourly'
        DAILY   = 'DAILY',   'Daily'
        WEEKLY  = 'WEEKLY',  'Weekly'
        MONTHLY = 'MONTHLY', 'Monthly'

    class Source(models.TextChoices):
        MANUAL     = 'MANUAL',     'Manual Entry'
        CSV_IMPORT = 'CSV_IMPORT', 'CSV/File Import'
        SFTP       = 'SFTP',       'SFTP Automated Sync'
        API        = 'API',        'REST API Push'

    kpi = models.ForeignKey(NetworkKPIDefinition, on_delete=models.CASCADE,
                             related_name='entries')
    period_date = models.DateField(db_index=True)
    granularity = models.CharField(max_length=8, choices=Granularity.choices,
                                     default=Granularity.DAILY, db_index=True)
    operator_code = models.CharField(max_length=30, default='orange', db_index=True,
                                      help_text='orange, africell, qcell, sierratel, onemobile, ALL')
    region = models.CharField(max_length=80, default='NATIONAL', db_index=True)
    district = models.CharField(max_length=80, blank=True, db_index=True)
    cell_id = models.CharField(max_length=80, blank=True, help_text='Optional specific cell/node ID')

    value = models.DecimalField(max_digits=12, decimal_places=4)
    is_compliant = models.BooleanField(default=True, db_index=True)

    source = models.CharField(max_length=12, choices=Source.choices,
                                default=Source.MANUAL)
    import_log = models.ForeignKey('NetworkKPIImportLog', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='entries',
                                   db_constraint=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'reg_kpi_entries'
        ordering = ['-period_date', 'kpi__code', 'operator_code', 'region', 'district']
        unique_together = [('kpi', 'period_date', 'granularity', 'operator_code', 'region', 'district', 'cell_id')]
        verbose_name = 'Network KPI Entry'
        verbose_name_plural = 'Network KPI Entries'

    def __str__(self):
        status = 'PASS' if self.is_compliant else 'FAIL'
        return f'{self.kpi.code} on {self.period_date} ({self.operator_code} - {self.region}/{self.district}): {self.value} {self.kpi.unit} [{status}]'


# ---------------------------------------------------------------------------
# 10. NetworkKPIImportLog — Bulk import audit trail
# ---------------------------------------------------------------------------

class NetworkKPIImportLog(models.Model):
    """Audit record for bulk KPI uploads or API pushes."""

    filename = models.CharField(max_length=255)
    imported_at = models.DateTimeField(auto_now_add=True)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,
    )
    channel = models.CharField(max_length=20, default='MANUAL')
    record_count = models.IntegerField(default=0)
    error_count = models.IntegerField(default=0)
    status = models.CharField(max_length=20, default='COMPLETED')
    errors_json = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = 'reg_kpi_import_logs'
        ordering = ['-imported_at']
        verbose_name = 'Network KPI Import Log'
        verbose_name_plural = 'Network KPI Import Logs'

    def __str__(self):
        return f'Import {self.filename} @ {self.imported_at:%Y-%m-%d %H:%M} ({self.record_count} recs)'


# ---------------------------------------------------------------------------
# 11. DriveTestCampaign — Drive Test Campaign
# ---------------------------------------------------------------------------

class DriveTestCampaign(models.Model):
    """Drive Test survey event/campaign."""

    class Status(models.TextChoices):
        UPLOADED = 'UPLOADED', 'Uploaded'
        ANALYSED = 'ANALYSED', 'Analysed'
        REPORTED = 'REPORTED', 'Report Generated'

    name = models.CharField(max_length=160)
    test_date = models.DateField(db_index=True)
    operator_code = models.CharField(max_length=30, default='orange', db_index=True)
    region = models.CharField(max_length=80, default='WESTERN_AREA', db_index=True)
    district = models.CharField(max_length=80, blank=True, db_index=True)
    route_description = models.TextField(blank=True, help_text='e.g. Freetown Central - Aberdeen - Lumley Beach Route')

    technology = models.CharField(max_length=10, default='4G', help_text='2G/3G/4G/5G')
    tool_used = models.CharField(max_length=80, default='TEMS', help_text='TEMS, Nemo, SwissQual, XCAL, CSV, etc.')
    operator_name = models.CharField(max_length=80, default='Orange SL')

    status = models.CharField(max_length=12, choices=Status.choices,
                                default=Status.UPLOADED, db_index=True)

    raw_file = models.FileField(upload_to='regulatory/drivetest/', blank=True, null=True)
    file_format = models.CharField(max_length=20, default='csv', help_text='csv, trp, lpg, nmf, zip, tar.gz')
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,
    )

    class Meta:
        db_table = 'reg_drive_campaigns'
        ordering = ['-test_date', '-created_at']
        verbose_name = 'Drive Test Campaign'
        verbose_name_plural = 'Drive Test Campaigns'

    def __str__(self):
        return f'{self.name} ({self.test_date}) [{self.status}]'


# ---------------------------------------------------------------------------
# 12. DriveTestSample — Geolocation measurement points
# ---------------------------------------------------------------------------

class DriveTestSample(models.Model):
    """Single geo-referenced drive test measurement point."""

    campaign = models.ForeignKey(DriveTestCampaign, on_delete=models.CASCADE,
                                  related_name='samples')

    latitude = models.DecimalField(max_digits=10, decimal_places=7)
    longitude = models.DecimalField(max_digits=10, decimal_places=7)
    timestamp = models.DateTimeField(null=True, blank=True)

    # 11 Drive Test KPI fields
    rsrp = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, help_text='RSRP in dBm')
    rsrq = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, help_text='RSRQ in dB')
    sinr = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, help_text='SINR in dB')

    dl_throughput = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text='DL Throughput (Mbps)')
    ul_throughput = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text='UL Throughput (Mbps)')

    cssr_status = models.BooleanField(null=True, blank=True, help_text='True=Call Setup Success, False=Failure')
    drop_status = models.BooleanField(null=True, blank=True, help_text='True=Call Dropped, False=Normal')
    handover_status = models.BooleanField(null=True, blank=True, help_text='True=Handover Success, False=Failure')

    ping_rtt = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text='Ping RTT in ms')
    voice_mos = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True, help_text='Voice MOS score 1.0-5.0')

    # Network identifiers
    technology = models.CharField(max_length=10, default='4G')
    cell_id = models.CharField(max_length=40, blank=True)
    pci = models.IntegerField(null=True, blank=True)
    earfcn = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'reg_drive_samples'
        ordering = ['timestamp', 'id']
        verbose_name = 'Drive Test Sample'
        verbose_name_plural = 'Drive Test Samples'

    def __str__(self):
        return f'Sample {self.pk} @ ({self.latitude}, {self.longitude}) RSRP={self.rsrp}dBm'


# ---------------------------------------------------------------------------
# 13. DriveTestAnalysis — Aggregated drive test results & NatCA compliance
# ---------------------------------------------------------------------------

class DriveTestAnalysis(models.Model):
    """Analysis summary of a drive test campaign against NatCA benchmarks."""

    campaign = models.OneToOneField(DriveTestCampaign, on_delete=models.CASCADE,
                                     related_name='analysis')

    total_samples = models.IntegerField(default=0)
    coverage_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))

    avg_rsrp = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0'))
    avg_rsrq = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0'))
    avg_sinr = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0'))

    avg_dl_throughput = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))
    avg_ul_throughput = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))

    cssr_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    drop_rate_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    handover_sr_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))

    avg_ping_rtt = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))
    avg_voice_mos = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0'))

    natca_compliant = models.BooleanField(default=False)
    analysis_json = models.JSONField(default=dict, blank=True)

    analysed_at = models.DateTimeField(auto_now=True)
    analysed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,
    )

    class Meta:
        db_table = 'reg_drive_analysis'
        verbose_name = 'Drive Test Analysis'
        verbose_name_plural = 'Drive Test Analyses'

    def __str__(self):
        status = 'COMPLIANT' if self.natca_compliant else 'NON-COMPLIANT'
        return f'Analysis for {self.campaign.name}: Coverage={self.coverage_pct}% [{status}]'


# ---------------------------------------------------------------------------
# 14. NetworkCellSite — Operator Cell Site & Geo Dimension (GeoDim) Inventory
# ---------------------------------------------------------------------------

class NetworkCellSite(models.Model):
    """Operator Cell Site & Geo Dimension Reference Data (aligned with Geo-Dimension 2026 V1 spec)."""

    class Status(models.TextChoices):
        ACTIVE   = 'ACTIVE',   'Active'
        INACTIVE = 'INACTIVE', 'Inactive'
        PLANNED  = 'PLANNED',  'Planned'

    operator_code = models.CharField(max_length=30, default='orange', db_index=True,
                                      help_text='orange, africell, qcell, sierratel, onemobile')
    site_id = models.CharField(max_length=80, db_index=True, help_text='Operator Site Identifier e.g. SL0001')
    site_name = models.CharField(max_length=160, help_text='Site Name e.g. SL0001_WILBERFORCE')
    cell_id = models.CharField(max_length=80, blank=True, db_index=True, help_text='Specific Sector / Cell Identifier e.g. 10011')
    cell_name = models.CharField(max_length=160, blank=True, help_text='Cell Name e.g. 2G_WILBERFORCE-11')
    ne_name = models.CharField(max_length=160, blank=True, help_text='Network Element Name e.g. SL0001_WILBERFORCE')
    bts_enodeb_id = models.CharField(max_length=80, blank=True, help_text='BTS / eNodeB ID e.g. 2001')

    mcc = models.CharField(max_length=5, default='619')
    mnc = models.CharField(max_length=5, default='01')
    lac_tac = models.CharField(max_length=20, blank=True, help_text='Location Area Code / Tracking Area Code')
    cgi_ecgi = models.CharField(max_length=40, blank=True, db_index=True, help_text='Cell Global Identity e.g. 619012011810011')
    bsc_rnc_name = models.CharField(max_length=100, blank=True, help_text='BSC / RNC / MME Name e.g. HW_FTBSC02')

    technology = models.CharField(max_length=30, default='4G', db_index=True, help_text='2G, 3G, 4G, 5G, 2G3G4G, 2G_HUAWEI, etc.')
    classification = models.CharField(max_length=50, blank=True, help_text='Platinum, Gold, Silver')
    natca_classification = models.CharField(max_length=50, blank=True, db_index=True, help_text='Urban Area, Rural Area')
    site_owner = models.CharField(max_length=120, blank=True, help_text='Owner / Towerco info')

    region = models.CharField(max_length=80, default='Western Area', db_index=True)
    district = models.CharField(max_length=80, blank=True, db_index=True)
    chiefdom = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=100, blank=True)
    chiefdom_town = models.CharField(max_length=100, blank=True, help_text='Legacy town/city field')

    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    height_m = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text='Tower height in meters')
    azimuth = models.IntegerField(null=True, blank=True, help_text='Antenna direction 0-360 degrees')

    on_air_date = models.DateField(null=True, blank=True)
    site_type = models.CharField(max_length=50, blank=True, help_text='Greenfield, Rooftop')

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'reg_cell_sites'
        ordering = ['operator_code', 'region', 'district', 'site_id']
        unique_together = [('operator_code', 'site_id', 'cell_id')]
        verbose_name = 'Cell Site & Geo Inventory'
        verbose_name_plural = 'Cell Site & Geo Inventories'

    def __str__(self):
        return f'[{self.operator_code.upper()}] {self.site_id} - {self.site_name} ({self.district})'


# ---------------------------------------------------------------------------
# 15. NetworkCounterDefinition — Counter Dictionary / Inventory Catalog
# ---------------------------------------------------------------------------

class NetworkCounterDefinition(models.Model):
    """Network Performance Counter Dictionary / Catalog across Vendors & Network Elements."""

    class FormulaRole(models.TextChoices):
        NUMERATOR   = 'NUMERATOR',   'Numerator (Events/Successes)'
        DENOMINATOR = 'DENOMINATOR', 'Denominator (Attempts/Total)'
        MEASURED    = 'MEASURED',    'Direct Measured Value'
        VALUE       = 'VALUE',       'Raw Gauge / Count Value'

    counter_id = models.CharField(max_length=100, db_index=True, help_text='Vendor Counter Code e.g. L.RRC.ConnReq.Att')
    counter_name = models.CharField(max_length=200, help_text='Full Counter Description')
    vendor = models.CharField(max_length=50, default='Huawei', db_index=True, help_text='Huawei, Ericsson, ZTE, Nokia, etc.')
    network_element = models.CharField(max_length=50, default='eNodeB', db_index=True, help_text='MSC, IMS, PGW, eNodeB, gNodeB, etc.')
    technology = models.CharField(max_length=10, default='4G', db_index=True, help_text='2G/3G/4G/5G')

    kpi_code = models.CharField(max_length=40, blank=True, db_index=True, help_text='Mapped PM KPI code e.g. CSSR, CDR')
    formula_role = models.CharField(max_length=30, choices=FormulaRole.choices, default=FormulaRole.NUMERATOR)

    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'reg_counter_dictionary'
        ordering = ['vendor', 'network_element', 'counter_id']
        unique_together = [('vendor', 'network_element', 'counter_id')]
        verbose_name = 'Counter Dictionary Entry'
        verbose_name_plural = 'Counter Dictionary Entries'

    def __str__(self):
        return f'[{self.vendor}/{self.network_element}] {self.counter_id} - {self.counter_name}'



