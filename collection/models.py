"""
Collection Models
==================
DataSource: defines where CDR files come from (SFTP, local dir, manual upload).
CDRFile: tracks each file through the processing lifecycle.
"""
import os
from django.db import models
from django.conf import settings
from core.enums import DecoderType


class DataSource(models.Model):
    """An input portal / source from which CDR files are collected."""

    class SourceType(models.TextChoices):
        SFTP = 'SFTP', 'SFTP Remote'
        LOCAL = 'LOCAL', 'Local Directory'
        API = 'API', 'API Push'
        MANUAL = 'MANUAL', 'Manual Upload'

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    source_type = models.CharField(
        max_length=10, choices=SourceType.choices, default=SourceType.MANUAL
    )
    decoder_type = models.CharField(
        max_length=10, choices=DecoderType.CHOICES, default=DecoderType.AUTO
    )
    vendor = models.CharField(max_length=50, blank=True, help_text='Equipment vendor (e.g. Huawei)')
    network_element = models.CharField(max_length=50, blank=True, help_text='e.g. MSC01, PGW01')
    cbs_type_hint = models.CharField(
        max_length=20, blank=True,
        help_text='CBS sub-type override when filename detection is insufficient '
                  '(RECHARGE, VOICE, SMS, DATA, MON, CM, LOAN, ADJUSTMENT)'
    )

    # SFTP configuration
    sftp_host = models.CharField(max_length=200, blank=True)
    sftp_port = models.IntegerField(default=22, blank=True)
    sftp_username = models.CharField(max_length=100, blank=True)
    sftp_password = models.CharField(max_length=200, blank=True)
    sftp_key_path = models.CharField(max_length=500, blank=True)
    sftp_remote_path = models.CharField(max_length=500, blank=True)
    sftp_file_pattern = models.CharField(
        max_length=200, blank=True, default='*.dat',
        help_text='Glob pattern to match CDR files'
    )

    # Local directory
    local_path = models.CharField(max_length=500, blank=True)

    # Polling
    poll_interval_seconds = models.IntegerField(
        default=300, help_text='Seconds between collection polls'
    )
    enabled = models.BooleanField(default=True)

    # Stats
    last_poll_time = models.DateTimeField(null=True, blank=True)
    last_poll_status = models.CharField(max_length=50, blank=True)
    files_collected = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'data_sources'
        ordering = ['name']
        verbose_name = 'Data Source'
        verbose_name_plural = 'Data Sources'

    def __str__(self):
        return f'{self.name} ({self.get_source_type_display()})'


class CDRFile(models.Model):
    """A CDR file being processed through the mediation pipeline."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PROCESSING = 'PROCESSING', 'Processing'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'
        DUPLICATE = 'DUPLICATE', 'Duplicate'

    source = models.ForeignKey(
        DataSource, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cdr_files'
    )
    filename = models.CharField(max_length=500)
    file_path = models.CharField(max_length=1000)
    file_size = models.BigIntegerField(default=0)
    file_hash = models.CharField(max_length=64, blank=True, db_index=True)
    decoder_type = models.CharField(
        max_length=10, choices=DecoderType.CHOICES, default=DecoderType.AUTO
    )

    # Multi-operator routing tags (directory segments + per-operator DB key).
    # Stored as string codes (not FKs) so CDRFile can live in a per-operator DB
    # without a cross-DB FK to the control-plane Operator table.
    operator_code = models.SlugField(max_length=30, blank=True, db_index=True,
                                     help_text='Operator slug, e.g. orange')
    vendor = models.SlugField(max_length=30, blank=True,
                              help_text='Equipment vendor, e.g. huawei')
    network_element = models.CharField(max_length=10, blank=True,
                                       help_text='e.g. msc, pgw, ims')

    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    error_message = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)

    # Processing stats
    records_total = models.IntegerField(default=0)
    records_valid = models.IntegerField(default=0)
    records_invalid = models.IntegerField(default=0)
    records_duplicate = models.IntegerField(default=0)
    # Per-service-type counts (e.g. {"VOICE": 1200, "SMS": 800}). Captured even
    # in decode-only mode so volume dashboards work without storing records.
    records_by_type = models.JSONField(default=dict, blank=True)

    # Timestamps
    processing_started = models.DateTimeField(null=True, blank=True)
    processing_completed = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # Who uploaded it
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True
    )

    class Meta:
        db_table = 'cdr_files'
        ordering = ['-created_at']
        verbose_name = 'CDR File'
        verbose_name_plural = 'CDR Files'

    def __str__(self):
        return f'{self.filename} [{self.status}]'

    @property
    def processing_duration(self):
        """Return processing duration in seconds, or None."""
        if self.processing_started and self.processing_completed:
            return (self.processing_completed - self.processing_started).total_seconds()
        return None

    @property
    def success_rate(self):
        """Percentage of valid records."""
        if self.records_total > 0:
            return round(self.records_valid / self.records_total * 100, 1)
        return 0


class DistributionPortal(models.Model):
    """A downstream system that receives processed CDR files."""
    """A downstream system that receives processed CDR files.

    Configuring one of these records makes it appear automatically in the
    File Registry → Distribution tree without any code change.
    """

    name = models.CharField(
        max_length=100, unique=True,
        help_text='Short identifier shown in the tree, e.g. SL_BIGDATA_HMSC'
    )
    label = models.CharField(
        max_length=100,
        help_text='Display label, e.g. HMSC'
    )
    vendor = models.ForeignKey(
        'reference.Vendor',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='distribution_portals',
        help_text='Vendor / destination system this portal belongs to'
    )
    # group/group_label kept as fallback when no vendor is set
    group = models.CharField(
        max_length=100, blank=True,
        help_text='Fallback group key when vendor is not set'
    )
    group_label = models.CharField(
        max_length=100, blank=True,
        help_text='Fallback group label when vendor is not set'
    )
    decoder_type = models.CharField(
        max_length=10, choices=DecoderType.CHOICES,
        help_text='Which CDR stream this portal receives (MSC, PGW, SGW, SGSN …)'
    )
    output_portal = models.ForeignKey(
        'portals.OutputPortal',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='distribution_portals',
        help_text='Delivery connection used to actually send files for this leaf'
    )
    enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'distribution_portals'
        ordering = ['group', 'label']
        verbose_name = 'Distribution Portal'
        verbose_name_plural = 'Distribution Portals'

    def __str__(self):
        return f'{self.group} / {self.label} ({self.decoder_type})'


class DistributionLog(models.Model):
    """Audit log of one delivery attempt — one row per (CDRFile, DistributionRule)."""

    class Status(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Success'
        FAILED = 'FAILED', 'Failed'
        SKIPPED = 'SKIPPED', 'Skipped'

    cdr_file = models.ForeignKey(
        'CDRFile', on_delete=models.CASCADE, related_name='distribution_logs'
    )
    rule = models.ForeignKey(
        'portals.DistributionRule', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='logs'
    )
    output_portal = models.ForeignKey(
        'portals.OutputPortal', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='delivery_logs'
    )
    filename = models.CharField(max_length=500, blank=True)
    record_count = models.IntegerField(default=0)
    file_size = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.SUCCESS, db_index=True
    )
    error = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0, help_text='Number of retry attempts performed')
    delivered_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'distribution_logs'
        ordering = ['-delivered_at']
        verbose_name = 'Distribution Log'
        verbose_name_plural = 'Distribution Logs'

    def __str__(self):
        portal = self.output_portal.name if self.output_portal else '?'
        return f'{self.cdr_file_id} -> {portal} [{self.status}]'


class ProcessingError(models.Model):
    """One row per record-level decode/validate/insert failure.

    Captured by the BaseProcessor exception handler so ops can triage
    failures without re-running the file in a debugger.
    """

    class Stage(models.TextChoices):
        DECODE   = 'DECODE',   'Decode'
        CREATE   = 'CREATE',   'Create record'
        VALIDATE = 'VALIDATE', 'Validate'
        ENRICH   = 'ENRICH',   'Enrich'
        PERSIST  = 'PERSIST',  'Persist'
        DISPATCH = 'DISPATCH', 'Dispatch'

    cdr_file = models.ForeignKey(
        'CDRFile', on_delete=models.CASCADE, related_name='processing_errors'
    )
    record_seq = models.IntegerField(
        null=True, blank=True,
        help_text='Sequence number of the record within the file (1-based).'
    )
    stage = models.CharField(max_length=12, choices=Stage.choices, default=Stage.CREATE,
                              db_index=True)
    error_class = models.CharField(max_length=120, blank=True)
    error_message = models.TextField()
    raw_hex = models.CharField(max_length=400, blank=True,
                                 help_text='First ~100 bytes of the failing record in hex.')
    context = models.JSONField(default=dict, blank=True,
                                 help_text='Free-form context (tag offsets, decoder state).')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'processing_errors'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['cdr_file', 'created_at']),
        ]
        verbose_name = 'Processing Error'
        verbose_name_plural = 'Processing Errors'

    def __str__(self):
        return f'#{self.cdr_file_id} seq={self.record_seq} {self.stage}: {self.error_message[:60]}'
