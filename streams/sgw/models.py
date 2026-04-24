"""
SGW CDR Record Model
=====================
Stores decoded and processed SGW CDR records (4G LTE Serving Gateway mobility records).
Each record has dedicated columns for key fields and a JSONField
for full raw data preservation.

SGW-CDR (record type 78) tracks 4G subscriber mobility and handover events.
The records are decoded from the same binary format as PGW CDRs (PGWDecoder).
"""
from django.db import models


class SGWRecord(models.Model):
    """A single decoded SGW CDR record (4G LTE Serving Gateway)."""

    class Status(models.TextChoices):
        VALID = 'VALID', 'Valid'
        INVALID = 'INVALID', 'Invalid'
        DUPLICATE = 'DUPLICATE', 'Duplicate'

    # Foreign keys
    file = models.ForeignKey(
        'collection.CDRFile', on_delete=models.CASCADE,
        related_name='sgw_records', db_index=True
    )
    source = models.ForeignKey(
        'collection.DataSource', on_delete=models.SET_NULL,
        null=True, blank=True, db_index=True
    )

    # Record identity
    record_type = models.CharField(max_length=50, db_index=True)   # SGW-CDR
    service_type = models.CharField(max_length=20, default='DATA')  # Always DATA
    charging_id = models.CharField(max_length=50, null=True, blank=True)

    # Subscriber
    calling_number = models.CharField(max_length=50, blank=True, db_index=True)  # MSISDN
    called_number = models.CharField(max_length=100, blank=True)                 # APN
    imsi = models.CharField(max_length=20, blank=True, db_index=True)
    imei = models.CharField(max_length=20, blank=True)

    # Timing
    start_time = models.DateTimeField(null=True, blank=True, db_index=True)
    end_time = models.DateTimeField(null=True, blank=True)
    duration = models.IntegerField(default=0)  # seconds

    # Data volumes (bytes)
    data_volume_up = models.CharField(max_length=50, default='0')
    data_volume_down = models.CharField(max_length=50, default='0')

    # APN / Network
    apn = models.CharField(max_length=100, blank=True, db_index=True)
    pdn_type = models.CharField(max_length=20, blank=True)          # IPv4, IPv6, IPv4v6
    rat_type = models.CharField(max_length=20, blank=True)          # EUTRAN, UTRAN, GERAN
    sgw_address = models.CharField(max_length=50, blank=True)       # SGW IP address
    pgw_address = models.CharField(max_length=50, blank=True)       # PGW IP address
    node_id = models.CharField(max_length=100, blank=True)

    # Location
    cell_id = models.CharField(max_length=50, blank=True)
    lac = models.CharField(max_length=20, blank=True)               # TAC for LTE
    serving_plmn = models.CharField(max_length=10, blank=True)

    # Cause
    cause_for_closing = models.CharField(max_length=50, blank=True)

    # Roaming
    is_roaming = models.BooleanField(default=False)

    # Raw data preservation (full decoded record as JSON)
    raw_data = models.JSONField(default=dict, blank=True)

    # Processing status
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.VALID
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sgw_records'
        ordering = ['-start_time']
        indexes = [
            models.Index(fields=['start_time', 'apn']),
            models.Index(fields=['calling_number', 'start_time']),
            models.Index(fields=['imsi', 'start_time']),
            models.Index(fields=['file', 'record_type']),
            models.Index(fields=['apn', 'start_time']),
        ]
        verbose_name = 'SGW CDR Record'
        verbose_name_plural = 'SGW CDR Records'

    def __str__(self):
        return f'{self.record_type} | {self.calling_number} -> {self.apn} | {self.start_time}'

    @property
    def data_volume_total(self):
        up = int(self.data_volume_up) if self.data_volume_up and str(self.data_volume_up).strip() else 0
        down = int(self.data_volume_down) if self.data_volume_down and str(self.data_volume_down).strip() else 0
        return up + down

    @property
    def data_volume_mb(self):
        total = self.data_volume_total
        return round(total / (1024 * 1024), 3) if total else 0
