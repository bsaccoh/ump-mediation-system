from django.db import models


class NetworkVendor(models.Model):
    """Equipment manufacturer / network vendor (Ericsson, Huawei, Nokia, etc.)."""
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class FrequencyBand(models.Model):
    """Radio frequency band definition (Band 3, Band 8, n28, etc.)."""

    class Technology(models.TextChoices):
        GSM = '2G', '2G/GSM'
        UMTS = '3G', '3G/UMTS'
        LTE = '4G', '4G/LTE'
        NR = '5G', '5G/NR'

    band_number = models.CharField(max_length=10)   # '3', '8', 'n28', etc.
    technology = models.CharField(max_length=3, choices=Technology.choices)
    frequency_mhz = models.FloatField()             # centre/downlink centre frequency
    bandwidth_mhz = models.FloatField(null=True, blank=True)
    duplex_mode = models.CharField(max_length=10, blank=True)  # FDD / TDD
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = [('band_number', 'technology')]
        ordering = ['technology', 'band_number']

    def __str__(self):
        return f'{self.technology} Band {self.band_number} ({self.frequency_mhz} MHz)'
