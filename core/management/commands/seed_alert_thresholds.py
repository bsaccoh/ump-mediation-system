"""Seed initial operational default alert thresholds.

These are starting points — Operations must review and approve them.

    python manage.py seed_alert_thresholds
"""
from django.core.management.base import BaseCommand

from core.models import AlertThreshold


DEFAULTS = [
    {
        'metric': 'cpu_percent',
        'warning_threshold': 75.0,
        'major_threshold': 85.0,
        'critical_threshold': 95.0,
        'evaluation_window_seconds': 300,
        'notes': 'INITIAL OPERATIONAL DEFAULT — subject to Operations approval',
    },
    {
        'metric': 'memory_percent',
        'warning_threshold': 75.0,
        'major_threshold': 85.0,
        'critical_threshold': 95.0,
        'evaluation_window_seconds': 300,
        'notes': 'INITIAL OPERATIONAL DEFAULT — subject to Operations approval',
    },
    {
        'metric': 'disk_percent',
        'warning_threshold': 70.0,
        'major_threshold': 80.0,
        'critical_threshold': 90.0,
        'evaluation_window_seconds': 300,
        'notes': 'INITIAL OPERATIONAL DEFAULT — subject to Operations approval',
    },
    {
        'metric': 'collection_backlog_count',
        'warning_threshold': 100,
        'major_threshold': 500,
        'critical_threshold': 1000,
        'evaluation_window_seconds': 600,
        'notes': 'INITIAL OPERATIONAL DEFAULT — subject to Operations approval. '
                 'Count of files waiting in published input directories.',
    },
    {
        'metric': 'collection_backlog_age',
        'warning_threshold': 1800,
        'major_threshold': 3600,
        'critical_threshold': 7200,
        'evaluation_window_seconds': 600,
        'notes': 'INITIAL OPERATIONAL DEFAULT — subject to Operations approval. '
                 'Age in seconds of the oldest uncollected file.',
    },
    {
        'metric': 'processing_backlog_count',
        'warning_threshold': 50,
        'major_threshold': 200,
        'critical_threshold': 500,
        'evaluation_window_seconds': 600,
        'notes': 'INITIAL OPERATIONAL DEFAULT — subject to Operations approval. '
                 'CDRFiles in COLLECTED/PENDING/PROCESSING states.',
    },
    {
        'metric': 'distribution_failure_count',
        'warning_threshold': 5,
        'major_threshold': 20,
        'critical_threshold': 50,
        'evaluation_window_seconds': 3600,
        'notes': 'INITIAL OPERATIONAL DEFAULT — subject to Operations approval. '
                 'Failed distribution deliveries within the evaluation window.',
    },
    {
        'metric': 'decoder_failure_count',
        'warning_threshold': 5,
        'major_threshold': 20,
        'critical_threshold': 50,
        'evaluation_window_seconds': 3600,
        'notes': 'INITIAL OPERATIONAL DEFAULT — subject to Operations approval. '
                 'Failed CDRFile decode attempts within the evaluation window.',
    },
]


class Command(BaseCommand):
    help = 'Seed initial operational default alert thresholds (skips existing).'

    def handle(self, *args, **opts):
        created = 0
        skipped = 0
        for row in DEFAULTS:
            _, was_created = AlertThreshold.objects.get_or_create(
                metric=row['metric'],
                defaults=row,
            )
            if was_created:
                created += 1
                self.stdout.write(f'  Created: {row["metric"]}')
            else:
                skipped += 1
                self.stdout.write(f'  Exists:  {row["metric"]}')

        self.stdout.write(self.style.SUCCESS(
            f'Done — {created} created, {skipped} already existed'))
