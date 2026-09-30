"""KPI thresholds and pre-aggregated KPI results.

Engineering classification thresholds are never hard-coded in templates or
JavaScript — they live here and drive every legend, colour and event decision.
A threshold's ``bands`` is an ordered list of classes, so the same model serves
RSRP, SINR, throughput, etc. without a column per metric.
"""
from django.db import models

from .enums import Technology
from .project import Campaign


class KpiThreshold(models.Model):
    """A classification band-set for one metric, optionally scoped.

    Scope precedence (most specific wins), resolved in the service layer:
        campaign > operator > region > technology-default.

    ``bands`` example (RSRP)::

        [
          {"label": "Excellent", "min": -80,  "max": null, "rank": 5, "color": "excellent"},
          {"label": "Good",      "min": -90,  "max": -80,  "rank": 4, "color": "good"},
          {"label": "Fair",      "min": -100, "max": -90,  "rank": 3, "color": "fair"},
          {"label": "Poor",      "min": -110, "max": -100, "rank": 2, "color": "poor"},
          {"label": "Critical",  "min": null, "max": -110, "rank": 1, "color": "critical"}
        ]

    ``min``/``max`` are inclusive-lower, exclusive-upper; null means unbounded.
    """
    metric = models.CharField(max_length=40, db_index=True, help_text='e.g. rsrp, sinr, dl_throughput')
    technology = models.CharField(max_length=8, choices=Technology.choices, blank=True)

    operator = models.ForeignKey(
        'reference.Operator', null=True, blank=True,
        on_delete=models.CASCADE, related_name='dt_thresholds',
    )
    campaign = models.ForeignKey(
        Campaign, null=True, blank=True, on_delete=models.CASCADE,
        related_name='thresholds',
    )
    region = models.CharField(max_length=120, blank=True)

    bands = models.JSONField(default=list)
    unit = models.CharField(max_length=20, blank=True, help_text='e.g. dBm, dB, Mbps')

    is_active = models.BooleanField(default=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['metric', 'technology']
        indexes = [models.Index(fields=['metric', 'technology'])]

    def __str__(self):
        scope = self.technology or 'ALL'
        return f'{self.metric} ({scope})'


class KpiResult(models.Model):
    """A pre-aggregated statistic roll-up, so region/cell KPIs need not rescan
    raw samples on every page load.
    """
    class Scope(models.TextChoices):
        CAMPAIGN = 'CAMPAIGN', 'Campaign'
        CELL = 'CELL', 'Cell'
        REGION = 'REGION', 'Region'
        BIN = 'BIN', 'Spatial bin'
        OPERATOR = 'OPERATOR', 'Operator'
        TECHNOLOGY = 'TECHNOLOGY', 'Technology'

    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='kpi_results',
    )
    scope_type = models.CharField(max_length=12, choices=Scope.choices)
    scope_key = models.CharField(max_length=120, blank=True, help_text='cell id / region / bin key')
    metric = models.CharField(max_length=40)

    count = models.IntegerField(default=0)
    min_value = models.FloatField(null=True, blank=True)
    mean_value = models.FloatField(null=True, blank=True)
    median_value = models.FloatField(null=True, blank=True)
    p10_value = models.FloatField(null=True, blank=True)
    p50_value = models.FloatField(null=True, blank=True)
    p90_value = models.FloatField(null=True, blank=True)
    max_value = models.FloatField(null=True, blank=True)

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['scope_type', 'scope_key', 'metric']
        indexes = [models.Index(fields=['campaign', 'scope_type', 'metric'])]

    def __str__(self):
        return f'{self.scope_type}:{self.scope_key} {self.metric}'
