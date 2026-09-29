"""Seed default KPI classification thresholds.

Idempotent: creates one generic (technology-agnostic) KpiThreshold per metric
from services.thresholds.DEFAULT_BANDS if it does not already exist. Existing
rows are left untouched so operator edits are never overwritten.
"""
from django.core.management.base import BaseCommand

from drive_test.models import KpiThreshold
from drive_test.services.thresholds import DEFAULT_BANDS, METRIC_META


class Command(BaseCommand):
    help = 'Seed default drive-test KPI thresholds (idempotent).'

    def handle(self, *args, **options):
        created = 0
        for metric, bands in DEFAULT_BANDS.items():
            unit = METRIC_META.get(metric, ('', ''))[1]
            obj, was_created = KpiThreshold.objects.get_or_create(
                metric=metric, technology='', operator=None, campaign=None,
                defaults={'bands': bands, 'unit': unit, 'is_active': True},
            )
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(
            f'Seeded {created} threshold(s); {len(DEFAULT_BANDS) - created} already present.'
        ))
