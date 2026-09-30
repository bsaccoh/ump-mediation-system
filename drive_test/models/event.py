"""Automatically detected network events and their spatial clusters.

An Event is a single detected problem instant (poor RSRP, call drop, handover
failure, …). A ProblemArea clusters nearby events of the same kind so the map
shows a handful of areas, not thousands of pins. Both are network findings;
data-quality problems live elsewhere and are never mixed in here.
"""
from django.db import models

from .enums import EventStatus, Severity, Technology
from .cell import Cell
from .project import Campaign
from .sample import Sample


class ProblemArea(models.Model):
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='problem_areas',
    )
    area_type = models.CharField(max_length=60, help_text='e.g. Poor LTE Coverage')
    label = models.CharField(max_length=120, blank=True, help_text='e.g. locality name')

    centroid_lat = models.FloatField(null=True, blank=True)
    centroid_lon = models.FloatField(null=True, blank=True)
    min_lat = models.FloatField(null=True, blank=True)
    min_lon = models.FloatField(null=True, blank=True)
    max_lat = models.FloatField(null=True, blank=True)
    max_lon = models.FloatField(null=True, blank=True)

    affected_distance_m = models.FloatField(null=True, blank=True)
    sample_count = models.IntegerField(default=0)
    stats = models.JSONField(default=dict, blank=True, help_text='e.g. mean RSRP/SINR')

    dominant_cell = models.ForeignKey(
        Cell, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='dt_problem_areas',
    )
    severity = models.CharField(max_length=8, choices=Severity.choices, default=Severity.MEDIUM)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-severity', '-sample_count']

    def __str__(self):
        return f'{self.area_type} ({self.label or self.pk})'


class Event(models.Model):
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='events',
    )
    sample = models.ForeignKey(
        Sample, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='events',
    )
    event_type = models.CharField(max_length=40, db_index=True)
    severity = models.CharField(
        max_length=8, choices=Severity.choices, default=Severity.MEDIUM, db_index=True,
    )
    timestamp = models.DateTimeField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    operator = models.ForeignKey(
        'reference.Operator', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='dt_events',
    )
    technology = models.CharField(max_length=8, choices=Technology.choices, blank=True)
    cell = models.ForeignKey(
        Cell, null=True, blank=True, on_delete=models.SET_NULL, related_name='dt_events',
    )

    kpi = models.CharField(max_length=40, blank=True)
    measured_value = models.FloatField(null=True, blank=True)
    threshold_value = models.FloatField(null=True, blank=True)

    status = models.CharField(
        max_length=12, choices=EventStatus.choices, default=EventStatus.OPEN,
    )
    problem_area = models.ForeignKey(
        ProblemArea, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='events',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-severity', '-timestamp']
        indexes = [models.Index(fields=['campaign', 'event_type'])]

    def __str__(self):
        return f'{self.event_type} ({self.severity})'
