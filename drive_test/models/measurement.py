from django.db import models

from .session import DriveTestFile
from .cell import Cell
from .device import TestDevice


class Measurement(models.Model):
    """
    Core spatiotemporal measurement record from a drive test.

    Data-tier discipline (per spec §6):
        A  (obs_* fields)   — raw observed values from the file; NEVER overwritten
        B  (matched_cell)   — authoritative reference match from Cell table
        C  (match_*)        — derived confidence + method

    Coordinates (latitude / longitude) are A-tier: they come from the GPS log
    in the original file and must never be replaced by anything derived.
    """

    # Source
    drive_file = models.ForeignKey(
        DriveTestFile, on_delete=models.CASCADE, related_name='measurements'
    )
    sequence_num = models.IntegerField()

    # Timestamp
    captured_at = models.DateTimeField(db_index=True)  # UTC
    local_timestamp = models.DateTimeField(null=True, blank=True)

    # GPS position (A-tier — raw from file)
    latitude = models.FloatField()
    longitude = models.FloatField()
    altitude_m = models.FloatField(null=True, blank=True)
    gps_accuracy_m = models.FloatField(null=True, blank=True)
    gps_hdop = models.FloatField(null=True, blank=True)
    speed_kmh = models.FloatField(null=True, blank=True)
    heading_deg = models.FloatField(null=True, blank=True)

    # Cell matching (B + C tier)
    matched_cell = models.ForeignKey(
        Cell, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='measurements', db_constraint=False,
    )
    match_confidence = models.FloatField(null=True, blank=True)  # 0.0 – 1.0
    match_method = models.CharField(max_length=30, blank=True)   # exact/proximity/interpolated

    # Raw observed cell identifiers (A-tier — from file, never overwritten)
    obs_mcc = models.CharField(max_length=3, blank=True)
    obs_mnc = models.CharField(max_length=3, blank=True)
    obs_lac = models.IntegerField(null=True, blank=True)
    obs_ci = models.IntegerField(null=True, blank=True)
    obs_tac = models.IntegerField(null=True, blank=True)
    obs_eci = models.BigIntegerField(null=True, blank=True)
    obs_pci = models.IntegerField(null=True, blank=True)
    obs_earfcn = models.IntegerField(null=True, blank=True)
    obs_nrarfcn = models.IntegerField(null=True, blank=True)

    # Test device
    test_device = models.ForeignKey(
        TestDevice, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='measurements',
    )

    # Validation
    is_valid = models.BooleanField(default=True)
    quality_flags = models.JSONField(default=list, blank=True)  # ['NO_GPS', 'STALE_CELL', …]

    class Meta:
        ordering = ['drive_file', 'sequence_num']
        indexes = [
            models.Index(fields=['drive_file', 'sequence_num']),
            models.Index(fields=['captured_at']),
            models.Index(fields=['matched_cell']),
            models.Index(fields=['latitude', 'longitude']),
        ]

    def __str__(self):
        return f'M{self.sequence_num} @ {self.captured_at:%Y-%m-%d %H:%M:%S}'


class RadioMeasurement(models.Model):
    """
    Radio signal measurements for one Measurement row (1:1).
    Separated from Measurement to keep the core table narrow.
    """

    # Primary key = measurement FK (no separate id column)
    measurement = models.OneToOneField(
        Measurement, on_delete=models.CASCADE, related_name='radio', primary_key=True
    )
    technology = models.CharField(max_length=5, blank=True)  # 2G / 3G / 4G / 5G

    # Signal quality (all dBm/dB unless noted)
    rssi = models.FloatField(null=True, blank=True)              # 2G/3G
    rscp = models.FloatField(null=True, blank=True)              # 3G (dBm)
    ecio = models.FloatField(null=True, blank=True)              # 3G (dB)
    rsrp = models.FloatField(null=True, blank=True)              # 4G/5G (dBm)
    rsrq = models.FloatField(null=True, blank=True)              # 4G/5G (dB)
    sinr = models.FloatField(null=True, blank=True)              # 4G/5G (dB)
    cqi = models.IntegerField(null=True, blank=True)             # 4G: 0–15
    ss_rsrp = models.FloatField(null=True, blank=True)           # 5G SS-RSRP
    ss_rsrq = models.FloatField(null=True, blank=True)           # 5G SS-RSRQ
    ss_sinr = models.FloatField(null=True, blank=True)           # 5G SS-SINR

    # Throughput (kbps)
    dl_throughput_kbps = models.FloatField(null=True, blank=True)
    ul_throughput_kbps = models.FloatField(null=True, blank=True)

    # Handover
    handover_count = models.IntegerField(default=0)
    ho_success_count = models.IntegerField(default=0)
    ho_failure_count = models.IntegerField(default=0)

    # Parser-specific overflow
    raw_data = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f'Radio @ {self.measurement_id}'


class ServiceMeasurement(models.Model):
    """
    Voice / SMS / data service attempt recorded at a measurement point.
    Multiple service measurements may exist per core Measurement row.
    """

    class ServiceType(models.TextChoices):
        VOICE = 'VOICE', 'Voice'
        SMS = 'SMS', 'SMS'
        DATA = 'DATA', 'Data'
        VIDEO = 'VIDEO', 'Video Streaming'

    class Outcome(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Success'
        FAILED = 'FAILED', 'Failed'
        DROPPED = 'DROPPED', 'Dropped'
        BLOCKED = 'BLOCKED', 'Blocked'
        NO_ATTEMPT = 'NO_ATTEMPT', 'No Attempt'

    measurement = models.ForeignKey(
        Measurement, on_delete=models.CASCADE, related_name='services'
    )
    service_type = models.CharField(max_length=10, choices=ServiceType.choices)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, default=Outcome.SUCCESS)

    # Voice
    call_setup_time_ms = models.IntegerField(null=True, blank=True)
    call_duration_s = models.IntegerField(null=True, blank=True)
    mos = models.FloatField(null=True, blank=True)       # Mean Opinion Score 1.0–5.0

    # Data
    throughput_kbps = models.FloatField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    packet_loss_pct = models.FloatField(null=True, blank=True)
    jitter_ms = models.FloatField(null=True, blank=True)

    # Parser overflow
    raw_data = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['measurement', 'service_type']),
        ]

    def __str__(self):
        return f'{self.service_type} / {self.outcome} @ {self.measurement_id}'


class HandoverEvent(models.Model):
    """Individual handover event captured during a drive test."""

    class HOType(models.TextChoices):
        INTRA_FREQ = 'intra_freq', 'Intra-frequency'
        INTER_FREQ = 'inter_freq', 'Inter-frequency'
        INTER_RAT = 'inter_rat', 'Inter-RAT'
        IRAT_TO_2G = 'irat_2g', 'IRAT to 2G'
        IRAT_TO_3G = 'irat_3g', 'IRAT to 3G'
        IRAT_TO_4G = 'irat_4g', 'IRAT to 4G'

    class Result(models.TextChoices):
        SUCCESS = 'success', 'Success'
        FAILURE = 'failure', 'Failure'
        PING_PONG = 'ping_pong', 'Ping-Pong'

    measurement = models.ForeignKey(
        Measurement, on_delete=models.CASCADE, related_name='handovers'
    )
    ho_type = models.CharField(max_length=20, choices=HOType.choices, blank=True)
    result = models.CharField(max_length=20, choices=Result.choices, default=Result.SUCCESS)
    source_cell = models.ForeignKey(
        'Cell', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='handover_sources', db_constraint=False,
    )
    target_cell = models.ForeignKey(
        'Cell', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='handover_targets', db_constraint=False,
    )
    ho_duration_ms = models.IntegerField(null=True, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['occurred_at']
