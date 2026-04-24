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
