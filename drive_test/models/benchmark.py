import uuid

from django.conf import settings
from django.db import models

from .geography import Region


def _generate_campaign_ref():
    return 'BM-' + uuid.uuid4().hex[:10].upper()


class BenchmarkCampaign(models.Model):
    """
    A benchmark campaign groups multiple drive-test sessions (potentially
    different operators, same route/day) for side-by-side comparison and
    weighted scoring.
    """

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        ACTIVE = 'ACTIVE', 'Active'
        SCORING = 'SCORING', 'Scoring'
        COMPLETED = 'COMPLETED', 'Completed'
        ARCHIVED = 'ARCHIVED', 'Archived'

    campaign_ref = models.CharField(max_length=32, unique=True, default=_generate_campaign_ref)
    name = models.CharField(max_length=300)
    description = models.TextField(blank=True)

    region = models.ForeignKey(
        Region, on_delete=models.SET_NULL, null=True, blank=True, related_name='benchmark_campaigns',
    )
    date_from = models.DateField(help_text='Benchmark period start')
    date_to = models.DateField(help_text='Benchmark period end')

    sessions = models.ManyToManyField(
        'drive_test.DriveTestSession', blank=True, related_name='benchmark_campaigns',
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    # KPI weight configuration (JSON: {"coverage": 25, "cssr": 20, ...})
    kpi_weights = models.JSONField(
        default=dict, blank=True,
        help_text='KPI weights for composite scoring (must sum to 100)',
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_benchmarks',
    )
    scored_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date_from', '-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['date_from', 'date_to']),
        ]

    def __str__(self):
        return f'{self.campaign_ref} — {self.name}'

    @staticmethod
    def default_weights():
        return {
            'coverage': 25,
            'rssi_mean': 15,
            'cssr': 20,
            'dcr': 15,
            'mos': 15,
            'speed': 10,
        }


class BenchmarkScore(models.Model):
    """
    Computed score for one operator in one benchmark campaign.
    Created/updated when the campaign is scored.
    """

    class Grade(models.TextChoices):
        A = 'A', 'A — Excellent'
        B = 'B', 'B — Good'
        C = 'C', 'C — Satisfactory'
        D = 'D', 'D — Poor'
        F = 'F', 'F — Failing'

    campaign = models.ForeignKey(
        BenchmarkCampaign, on_delete=models.CASCADE, related_name='scores',
    )
    operator = models.ForeignKey(
        'reference.Operator', on_delete=models.PROTECT, related_name='benchmark_scores',
    )

    composite_score = models.FloatField(help_text='Weighted composite 0–100')
    grade = models.CharField(max_length=1, choices=Grade.choices)

    # Per-KPI normalized scores (0–100 each)
    kpi_scores = models.JSONField(
        default=dict, blank=True,
        help_text='Per-KPI normalized scores: {"coverage": 85.0, "cssr": 92.0, ...}',
    )
    # Raw KPI values for reference
    kpi_values = models.JSONField(
        default=dict, blank=True,
        help_text='Raw KPI values: {"coverage": 78.5, "cssr": 98.2, ...}',
    )

    sessions_count = models.IntegerField(default=0)
    measurements_count = models.IntegerField(default=0)

    scored_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('campaign', 'operator')]
        ordering = ['-composite_score']

    def __str__(self):
        return f'{self.operator.name}: {self.grade} ({self.composite_score:.1f})'
