from django.db import models


class TrafficSummary(models.Model):
    """Hourly traffic aggregate bucket — the core NatCA monitoring table.

    Each row represents one (operator, service, traffic_type, direction,
    technology, hour) combination. Populated by the regulatory tap during
    CDR processing. Daily/weekly/monthly/quarterly/annual views are computed
    via SQL GROUP BY on this hourly base grain.
    """

    class ServiceType(models.TextChoices):
        VOICE = 'VOICE', 'Voice'
        SMS = 'SMS', 'SMS'
        DATA = 'DATA', 'Data'

    class TrafficType(models.TextChoices):
        ON_NET = 'ON_NET', 'On-Net'
        OFF_NET = 'OFF_NET', 'Off-Net'
        INTERNATIONAL = 'INTERNATIONAL', 'International'
        ROAMING = 'ROAMING', 'Roaming'
        INTERCONNECT = 'INTERCONNECT', 'Interconnect'
        INBOUND = 'INBOUND', 'Inbound'
        OUTBOUND = 'OUTBOUND', 'Outbound'
        LOCAL = 'LOCAL', 'Local'

    class Direction(models.TextChoices):
        ORIGINATING = 'ORIGINATING', 'Originating'
        TERMINATING = 'TERMINATING', 'Terminating'
        BOTH = 'BOTH', 'Both / Unknown'

    operator_code = models.SlugField(max_length=30, db_index=True)
    period_start = models.DateTimeField(db_index=True)
    period_end = models.DateTimeField()

    service_type = models.CharField(max_length=10, choices=ServiceType.choices, db_index=True)
    traffic_type = models.CharField(max_length=20, choices=TrafficType.choices, db_index=True)
    direction = models.CharField(
        max_length=12, choices=Direction.choices, default=Direction.BOTH,
    )
    subscriber_category = models.CharField(
        max_length=10, blank=True, db_index=True,
        help_text='PREPAID, POSTPAID, or blank for unknown/mixed',
    )
    network_technology = models.CharField(
        max_length=10, blank=True,
        help_text='2G, 3G, 4G, VOLTE, or blank',
    )
    source_stream = models.CharField(
        max_length=10, blank=True,
        help_text='MSC, IMS, PGW, SGSN, SGW, CBS',
    )
    destination_country = models.CharField(max_length=100, blank=True)
    destination_operator = models.CharField(max_length=100, blank=True)

    call_count = models.BigIntegerField(default=0)
    total_duration_seconds = models.BigIntegerField(default=0)
    sms_count = models.BigIntegerField(default=0)
    data_volume_bytes_up = models.BigIntegerField(default=0)
    data_volume_bytes_down = models.BigIntegerField(default=0)

    record_count = models.BigIntegerField(default=0)
    cdr_file_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'regulatory_traffic_summary'
        ordering = ['-period_start']
        indexes = [
            models.Index(fields=['operator_code', 'period_start', 'service_type']),
            models.Index(fields=['period_start', 'traffic_type']),
            models.Index(fields=['operator_code', 'traffic_type', 'period_start']),
        ]

    def __str__(self):
        return (
            f'{self.operator_code} | {self.service_type} | {self.traffic_type} '
            f'| {self.period_start:%Y-%m-%d %H:00}'
        )

    @property
    def total_duration_minutes(self):
        return self.total_duration_seconds / 60.0

    @property
    def data_volume_mb(self):
        return (self.data_volume_bytes_up + self.data_volume_bytes_down) / (1024 * 1024)

    @property
    def data_volume_total_bytes(self):
        return self.data_volume_bytes_up + self.data_volume_bytes_down
