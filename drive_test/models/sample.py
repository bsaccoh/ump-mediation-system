"""Sample — the canonical, normalized measurement row (the fact table).

One row per measurement instant, RAT-agnostic. Every RF metric is nullable:
a field absent from the source stays NULL and renders as "—" / "not available
in source data", never as 0. Observed identifiers are kept verbatim in obs_*;
the matched reference cell (if any) is a separate FK with its own confidence.
"""
from django.db import models

from .enums import MatchMethod, Technology
from .cell import Cell
from .file import DriveTestFile
from .project import Campaign, Route


class Sample(models.Model):
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='samples',
    )
    drive_file = models.ForeignKey(
        DriveTestFile, on_delete=models.CASCADE, related_name='samples',
    )
    route = models.ForeignKey(
        Route, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='samples',
    )

    # Naive UTC (USE_TZ is False platform-wide).
    timestamp = models.DateTimeField(db_index=True)

    # Position (nullable — a sample may lack a GPS fix).
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    altitude = models.FloatField(null=True, blank=True)
    speed = models.FloatField(null=True, blank=True)
    heading = models.FloatField(null=True, blank=True)
    hdop = models.FloatField(null=True, blank=True)

    # Network identity.
    operator = models.ForeignKey(
        'reference.Operator', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='dt_samples',
    )
    technology = models.CharField(max_length=8, choices=Technology.choices, blank=True)
    mcc = models.CharField(max_length=3, blank=True)
    mnc = models.CharField(max_length=3, blank=True)
    plmn = models.CharField(max_length=6, blank=True)

    # Serving cell — observed (raw) vs matched reference (authoritative).
    obs_cell_id = models.CharField(max_length=40, blank=True)
    obs_pci = models.IntegerField(null=True, blank=True)
    obs_psc = models.IntegerField(null=True, blank=True)
    obs_bsic = models.IntegerField(null=True, blank=True)
    obs_arfcn = models.IntegerField(null=True, blank=True)
    frequency = models.FloatField(null=True, blank=True)
    band = models.CharField(max_length=20, blank=True)
    channel = models.IntegerField(null=True, blank=True)

    cell = models.ForeignKey(
        Cell, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='serving_samples',
    )
    match_method = models.CharField(
        max_length=10, choices=MatchMethod.choices, default=MatchMethod.NONE,
    )
    match_confidence = models.FloatField(null=True, blank=True)

    # --- RF metrics, all nullable ------------------------------------------
    # LTE
    rsrp = models.FloatField(null=True, blank=True)
    rsrq = models.FloatField(null=True, blank=True)
    sinr = models.FloatField(null=True, blank=True)
    rssi = models.FloatField(null=True, blank=True)
    cqi = models.FloatField(null=True, blank=True)
    # NR (5G)
    ss_rsrp = models.FloatField(null=True, blank=True)
    ss_rsrq = models.FloatField(null=True, blank=True)
    ss_sinr = models.FloatField(null=True, blank=True)
    # UMTS
    rscp = models.FloatField(null=True, blank=True)
    ecno = models.FloatField(null=True, blank=True)
    # GSM
    rxlev = models.FloatField(null=True, blank=True)
    rxqual = models.FloatField(null=True, blank=True)

    # Data / transport (nullable).
    dl_throughput = models.FloatField(null=True, blank=True, help_text='kbps')
    ul_throughput = models.FloatField(null=True, blank=True, help_text='kbps')
    latency_ms = models.FloatField(null=True, blank=True)
    packet_loss = models.FloatField(null=True, blank=True, help_text='percent')

    # Sample-attached event marker (the richer Event model is separate).
    event_type = models.CharField(max_length=40, blank=True)
    event_status = models.CharField(max_length=40, blank=True)

    is_valid = models.BooleanField(default=True)
    quality_flags = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['timestamp']
        indexes = [
            models.Index(fields=['campaign', 'timestamp']),
            models.Index(fields=['latitude', 'longitude']),
            models.Index(fields=['cell']),
            models.Index(fields=['technology']),
        ]

    def __str__(self):
        return f'Sample {self.pk} @ {self.timestamp:%Y-%m-%d %H:%M:%S}'


class NeighborMeasurement(models.Model):
    """One detected neighbour cell for a Sample, ranked by received level."""
    sample = models.ForeignKey(
        Sample, on_delete=models.CASCADE, related_name='neighbors',
    )
    rank = models.IntegerField(default=0, help_text='0 = strongest')
    rat = models.CharField(max_length=8, choices=Technology.choices, blank=True)

    obs_pci = models.IntegerField(null=True, blank=True)
    obs_psc = models.IntegerField(null=True, blank=True)
    obs_bsic = models.IntegerField(null=True, blank=True)
    obs_arfcn = models.IntegerField(null=True, blank=True)

    rsrp = models.FloatField(null=True, blank=True)
    rsrq = models.FloatField(null=True, blank=True)
    rscp = models.FloatField(null=True, blank=True)
    ecno = models.FloatField(null=True, blank=True)
    rssi = models.FloatField(null=True, blank=True)
    ss_rsrp = models.FloatField(null=True, blank=True)

    cell = models.ForeignKey(
        Cell, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='neighbor_measurements',
    )
    match_method = models.CharField(
        max_length=10, choices=MatchMethod.choices, default=MatchMethod.NONE,
    )
    match_confidence = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ['sample', 'rank']
        constraints = [
            models.UniqueConstraint(
                fields=['sample', 'rank'], name='dt_neighbor_unique_rank',
            ),
        ]

    def __str__(self):
        return f'Neighbor #{self.rank} of sample {self.sample_id}'
