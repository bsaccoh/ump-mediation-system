"""
MSC CDR Record Model
=====================
Stores decoded and processed MSC CDR records (voice + SMS).
Each record has dedicated columns for key fields and a JSONField
for the full raw data preservation.
"""
from django.db import models


class MSCRecord(models.Model):
    """A single decoded MSC CDR record (voice or SMS)."""

    class Status(models.TextChoices):
        VALID = 'VALID', 'Valid'
        INVALID = 'INVALID', 'Invalid'
        DUPLICATE = 'DUPLICATE', 'Duplicate'

    # Foreign keys
    file = models.ForeignKey(
        'collection.CDRFile', on_delete=models.CASCADE,
        related_name='msc_records', db_index=True
    )
    source = models.ForeignKey(
        'collection.DataSource', on_delete=models.SET_NULL,
        null=True, blank=True, db_index=True
    )

    # Record identity
    record_type = models.CharField(max_length=50, db_index=True)
    service_type = models.CharField(max_length=20, db_index=True)
    network_record_id = models.CharField(max_length=100, blank=True)
    call_reference = models.CharField(max_length=50, blank=True)
    call_direction = models.CharField(max_length=5, blank=True)

    # Parties
    calling_number = models.CharField(max_length=50, blank=True, db_index=True)
    called_number = models.CharField(max_length=50, blank=True, db_index=True)
    dialed_number = models.CharField(max_length=50, blank=True)
    charged_msisdn = models.CharField(max_length=50, blank=True)
    imsi = models.CharField(max_length=20, blank=True, db_index=True)
    imei = models.CharField(max_length=20, blank=True)
    imsi_b = models.CharField(max_length=20, blank=True)
    imei_b = models.CharField(max_length=20, blank=True)

    # Timing
    start_time = models.DateTimeField(null=True, blank=True, db_index=True)
    end_time = models.DateTimeField(null=True, blank=True)
    duration = models.IntegerField(default=0)

    # Location
    cell_id = models.CharField(max_length=50, blank=True)
    lac = models.CharField(max_length=20, blank=True)
    msc_id = models.CharField(max_length=50, blank=True)
    rat_type = models.CharField(max_length=20, blank=True)

    # Trunk info
    originating_trunk = models.CharField(max_length=100, blank=True)
    terminating_trunk = models.CharField(max_length=100, blank=True)

    # Service codes
    teleservice_code = models.CharField(max_length=10, blank=True)
    bearer_service_code = models.CharField(max_length=10, blank=True)

    # Result
    result_code = models.CharField(max_length=50, blank=True)
    roaming_indicator = models.CharField(max_length=10, blank=True)

    # Raw data preservation (full decoded record as JSON)
    raw_data = models.JSONField(default=dict, blank=True)

    # Processing status
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.VALID
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'msc_records'
        ordering = ['-start_time']
        indexes = [
            models.Index(fields=['start_time', 'service_type']),
            models.Index(fields=['calling_number', 'start_time']),
            models.Index(fields=['called_number', 'start_time']),
            models.Index(fields=['imsi', 'start_time']),
            models.Index(fields=['file', 'record_type']),
        ]
        verbose_name = 'MSC CDR Record'
        verbose_name_plural = 'MSC CDR Records'

    def __str__(self):
        return f'{self.record_type} | {self.calling_number} -> {self.called_number} | {self.start_time}'
