from django.db import models

from .network import FrequencyBand
from .site import Sector


class Cell(models.Model):
    """
    Individual radio cell (carrier) within a sector.

    The three data tiers are kept separate per spec:
        A  — raw observed data from the drive test file (obs_* fields on Measurement)
        B  — authoritative reference data (this model, populated from operator feeds)
        C  — derived / inferred (match_confidence on Measurement)
    B fields here are NEVER overwritten by A or C data.
    """

    class Technology(models.TextChoices):
        GSM = '2G', '2G/GSM'
        UMTS = '3G', '3G/UMTS'
        LTE = '4G', '4G/LTE'
        NR = '5G', '5G/NR'
        CDMA = 'CDMA', 'CDMA'

    # Identity
    cell_id = models.CharField(max_length=50)  # operator's own cell identifier
    operator = models.ForeignKey(
        'reference.Operator', on_delete=models.PROTECT, related_name='drive_test_cells'
    )
    sector = models.ForeignKey(Sector, on_delete=models.CASCADE, related_name='cells')
    technology = models.CharField(max_length=5, choices=Technology.choices)

    # Radio identifiers (authoritative reference — B-tier)
    mcc = models.CharField(max_length=3, blank=True)
    mnc = models.CharField(max_length=3, blank=True)
    lac = models.IntegerField(null=True, blank=True)         # 2G / 3G
    rac = models.IntegerField(null=True, blank=True)         # 3G (routing area)
    tac = models.IntegerField(null=True, blank=True)         # 4G / 5G
    ci = models.IntegerField(null=True, blank=True)          # Cell Identity (2G / 3G)
    eci = models.BigIntegerField(null=True, blank=True)      # E-UTRAN Cell ID (4G)
    nci = models.BigIntegerField(null=True, blank=True)      # NR Cell Identity (5G)
    pci = models.IntegerField(null=True, blank=True)         # Physical Cell ID (4G / 5G)
    earfcn = models.IntegerField(null=True, blank=True)      # 4G frequency channel
    nrarfcn = models.IntegerField(null=True, blank=True)     # 5G frequency channel

    # Composite identifiers (auto-maintained)
    cgi = models.CharField(max_length=30, blank=True, db_index=True)   # MCC-MNC-LAC-CI
    ecgi = models.CharField(max_length=30, blank=True, db_index=True)  # MCC-MNC-ECI

    # Radio
    band = models.ForeignKey(
        FrequencyBand, on_delete=models.SET_NULL, null=True, blank=True, related_name='cells'
    )

    # Antenna coordinates (may differ from site if antenna is offset)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    altitude_m = models.FloatField(null=True, blank=True)

    # Status
    is_active = models.BooleanField(default=True)
    commissioned_date = models.DateField(null=True, blank=True)
    decommissioned_date = models.DateField(null=True, blank=True)

    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('operator', 'cell_id')]
        ordering = ['operator', 'cell_id']
        indexes = [
            models.Index(fields=['operator', 'mcc', 'mnc', 'lac', 'ci']),
            models.Index(fields=['operator', 'mcc', 'mnc', 'tac', 'eci']),
            models.Index(fields=['latitude', 'longitude']),
            models.Index(fields=['pci', 'earfcn']),
        ]

    def __str__(self):
        return f'{self.cell_id} ({self.technology}) – {self.sector}'

    def save(self, *args, **kwargs):
        if self.mcc and self.mnc:
            if self.lac is not None and self.ci is not None:
                self.cgi = f'{self.mcc}-{self.mnc}-{self.lac}-{self.ci}'
            if self.eci is not None:
                self.ecgi = f'{self.mcc}-{self.mnc}-{self.eci}'
        super().save(*args, **kwargs)


class CellHistory(models.Model):
    """Immutable audit trail for cell attribute changes."""
    cell = models.ForeignKey(Cell, on_delete=models.CASCADE, related_name='history')
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(
        'core.User', on_delete=models.SET_NULL, null=True, blank=True
    )
    change_type = models.CharField(max_length=20)  # created / updated / deactivated
    snapshot = models.JSONField()  # full model state at this point

    class Meta:
        ordering = ['-changed_at']
