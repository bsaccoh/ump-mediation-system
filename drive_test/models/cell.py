"""Network reference cells.

A Cell is authoritative reference data (from an operator CM dump or manual
entry). Samples record what the device *observed* (obs_* on Sample); matching
links an observation to a reference Cell with a method + confidence. The two
are never conflated.
"""
from django.db import models

from .enums import Technology


class Cell(models.Model):
    operator = models.ForeignKey(
        'reference.Operator', null=True, blank=True,
        on_delete=models.PROTECT, related_name='dt_cells',
    )
    technology = models.CharField(max_length=8, choices=Technology.choices, db_index=True)
    cell_id = models.CharField(max_length=40, help_text='ECI / NCI / CI / UC-Id', db_index=True)
    site_name = models.CharField(max_length=120, blank=True)

    # Physical-layer identifiers (per-RAT; all optional).
    pci = models.IntegerField(null=True, blank=True)     # LTE/NR
    psc = models.IntegerField(null=True, blank=True)     # UMTS
    bsic = models.IntegerField(null=True, blank=True)    # GSM
    earfcn = models.IntegerField(null=True, blank=True)  # LTE
    uarfcn = models.IntegerField(null=True, blank=True)  # UMTS
    arfcn = models.IntegerField(null=True, blank=True)   # GSM
    nrarfcn = models.IntegerField(null=True, blank=True)  # NR
    band = models.CharField(max_length=20, blank=True)

    # Location + antenna (optional; feed sector geometry / overshoot later).
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    azimuth = models.FloatField(null=True, blank=True)
    beamwidth = models.FloatField(null=True, blank=True)
    height_m = models.FloatField(null=True, blank=True)

    source = models.CharField(max_length=60, blank=True, help_text='e.g. CM dump, manual')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['operator', 'technology', 'cell_id']
        constraints = [
            models.UniqueConstraint(
                fields=['operator', 'technology', 'cell_id'],
                name='dt_cell_unique_per_operator_tech',
            ),
        ]
        indexes = [
            models.Index(fields=['technology', 'pci']),
            models.Index(fields=['latitude', 'longitude']),
        ]

    def __str__(self):
        return f'{self.technology} {self.cell_id}'
