"""
SGSN CDR Record Model
======================
Stores decoded and processed SGSN CDR records (2G/3G GPRS data sessions and SMS).
Each record has dedicated columns for key fields and a JSONField for full raw data.
"""
from django.db import models


class SGSNRecord(models.Model):
    """A single decoded SGSN S-CDR record (2G/3G GPRS data session or SMS)."""

    class Status(models.TextChoices):
        VALID = 'VALID', 'Valid'
        INVALID = 'INVALID', 'Invalid'
        DUPLICATE = 'DUPLICATE', 'Duplicate'

    # Foreign keys
    file = models.ForeignKey(
        'collection.CDRFile', on_delete=models.CASCADE,
        related_name='sgsn_records', db_index=True
    )
    source = models.ForeignKey(
        'collection.DataSource', on_delete=models.SET_NULL,
        null=True, blank=True, db_index=True
    )

    # Record identity
    record_type = models.CharField(max_length=50, db_index=True)   # SGSN-CDR, SGSN-SMOMO, SGSN-SMTC
    service_type = models.CharField(max_length=20, default='DATA')  # DATA or SMS
    charging_id = models.CharField(max_length=50, null=True, blank=True)

    # Subscriber
    calling_number = models.CharField(max_length=50, blank=True, db_index=True)  # MSISDN
    called_number = models.CharField(max_length=100, blank=True)                 # APN (data) or dest (SMS)
    imsi = models.CharField(max_length=20, blank=True, db_index=True)
    imei = models.CharField(max_length=20, blank=True)

    # Timing
    start_time = models.DateTimeField(null=True, blank=True, db_index=True)
    end_time = models.DateTimeField(null=True, blank=True)
    duration = models.IntegerField(default=0)  # seconds

    # Data volumes (bytes) — 0 for SMS records
    data_volume_up = models.CharField(max_length=50, default='0')
    data_volume_down = models.CharField(max_length=50, default='0')

    # APN / Session
    apn = models.CharField(max_length=100, blank=True, db_index=True)
    pdp_type = models.CharField(max_length=20, blank=True)    # PPP, IPv4, IPv6, IPv4v6

    # Network nodes
    sgsn_address = models.CharField(max_length=50, blank=True)  # Serving GPRS Support Node IP
    ggsn_address = models.CharField(max_length=50, blank=True)  # Gateway GPRS Support Node IP
    node_id = models.CharField(max_length=100, blank=True)

    # Radio / Technology
    rat_type = models.CharField(max_length=20, blank=True)  # GERAN (2G), UTRAN (3G)

    # Location
    cell_id = models.CharField(max_length=50, blank=True)
    lac = models.CharField(max_length=20, blank=True)   # Location Area Code
    rac = models.CharField(max_length=20, blank=True)   # Routing Area Code
    serving_plmn = models.CharField(max_length=10, blank=True)

    # Cause / Diagnostics
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
        db_table = 'sgsn_records'
        ordering = ['-start_time']
        indexes = [
            models.Index(fields=['imsi', 'start_time']),
            models.Index(fields=['calling_number', 'start_time']),
            models.Index(fields=['apn', 'start_time']),
            models.Index(fields=['file', 'record_type']),
        ]
        verbose_name = 'SGSN CDR Record'
        verbose_name_plural = 'SGSN CDR Records'

    def __str__(self):
        return f'{self.record_type} | {self.calling_number} -> {self.apn or self.called_number} | {self.start_time}'

    @property
    def data_volume_total(self):
        up = int(self.data_volume_up) if self.data_volume_up and str(self.data_volume_up).strip() else 0
        down = int(self.data_volume_down) if self.data_volume_down and str(self.data_volume_down).strip() else 0
        return up + down

    @property
    def data_volume_mb(self):
        total = self.data_volume_total
        return round(total / (1024 * 1024), 3) if total else 0
