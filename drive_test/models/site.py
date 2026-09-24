from django.db import models

from .geography import Chiefdom
from .network import NetworkVendor


class Site(models.Model):
    """A physical base-station site owned by an operator."""

    class SiteType(models.TextChoices):
        MACRO = 'macro', 'Macro'
        MICRO = 'micro', 'Micro'
        PICO = 'pico', 'Pico'
        FEMTO = 'femto', 'Femto'
        INDOOR = 'indoor', 'Indoor'
        ROOFTOP = 'rooftop', 'Rooftop'
        MONOPOLE = 'monopole', 'Monopole'
        TOWER = 'tower', 'Tower'
        OTHER = 'other', 'Other'

    # Identity
    site_id = models.CharField(max_length=50)
    operator = models.ForeignKey(
        'reference.Operator', on_delete=models.PROTECT, related_name='drive_test_sites'
    )
    name = models.CharField(max_length=200)
    site_type = models.CharField(max_length=20, choices=SiteType.choices, default=SiteType.MACRO)

    # Geography
    chiefdom = models.ForeignKey(
        Chiefdom, on_delete=models.SET_NULL, null=True, blank=True, related_name='sites'
    )
    address = models.CharField(max_length=500, blank=True)

    # Coordinates (raw from reference import — A-tier: never overwritten by derived data)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    altitude_m = models.FloatField(null=True, blank=True)

    # Equipment
    vendor = models.ForeignKey(
        NetworkVendor, on_delete=models.SET_NULL, null=True, blank=True, related_name='sites'
    )

    # Status
    is_active = models.BooleanField(default=True)
    commissioned_date = models.DateField(null=True, blank=True)
    decommissioned_date = models.DateField(null=True, blank=True)

    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('operator', 'site_id')]
        ordering = ['operator', 'site_id']
        indexes = [
            models.Index(fields=['operator', 'site_id']),
            models.Index(fields=['latitude', 'longitude']),
        ]

    def __str__(self):
        return f'{self.site_id} – {self.name}'


class Sector(models.Model):
    """A directional sector on a site (one site may have 3 sectors: 0°/120°/240°)."""

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='sectors')
    sector_id = models.CharField(max_length=50)   # operator sector identifier
    azimuth_deg = models.FloatField(null=True, blank=True)
    height_m = models.FloatField(null=True, blank=True)
    tilt_deg = models.FloatField(null=True, blank=True)  # mechanical tilt
    electrical_tilt_deg = models.FloatField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('site', 'sector_id')]
        ordering = ['site', 'sector_id']

    def __str__(self):
        return f'{self.site.site_id} / {self.sector_id}'
