"""Project and Campaign — the top of the drive-test hierarchy.

A Project is a drive-test initiative (e.g. "2026 National Network Quality
Assessment"). A Campaign is one exercise within it (e.g. "Freetown LTE
Benchmark — September 2026"). Files, routes, samples, events and reports all
hang off a Campaign.
"""
from django.conf import settings
from django.db import models

from .enums import ProjectStatus, Technology


class Project(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    region = models.CharField(max_length=120, blank=True)
    district = models.CharField(max_length=120, blank=True)

    # Operators under test — reuse the platform operator registry.
    operators = models.ManyToManyField(
        'reference.Operator', blank=True, related_name='dt_projects',
    )
    # Technologies under test, e.g. ["LTE", "NR"]. A list, because a project
    # commonly benchmarks several RATs at once.
    technologies = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=12, choices=ProjectStatus.choices, default=ProjectStatus.DRAFT,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='dt_projects_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class Campaign(models.Model):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='campaigns',
    )
    name = models.CharField(max_length=200)
    operator = models.ForeignKey(
        'reference.Operator', null=True, blank=True,
        on_delete=models.PROTECT, related_name='dt_campaigns',
    )
    # A single primary RAT; blank when a campaign spans several.
    technology = models.CharField(
        max_length=8, choices=Technology.choices, blank=True,
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    device = models.CharField(max_length=120, blank=True, help_text='Vehicle / test device')
    tester = models.CharField(max_length=120, blank=True)
    region = models.CharField(max_length=120, blank=True)
    district = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='dt_campaigns_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['project', 'operator'])]

    def __str__(self):
        return self.name


class Route(models.Model):
    """A named drive route within a campaign.

    ``geometry`` is an ordered list of ``[lat, lon]`` pairs (a polyline). We
    store JSON rather than a PostGIS LINESTRING because GDAL/PostGIS is not
    installed; the GeoQueryService seam lets this become a real geometry later
    without changing callers.
    """
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='routes',
    )
    name = models.CharField(max_length=200, blank=True)
    geometry = models.JSONField(default=list, blank=True)
    distance_m = models.FloatField(null=True, blank=True)

    # Bounding box, for cheap spatial filtering without PostGIS.
    min_lat = models.FloatField(null=True, blank=True)
    min_lon = models.FloatField(null=True, blank=True)
    max_lat = models.FloatField(null=True, blank=True)
    max_lon = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name or f'Route {self.pk}'
