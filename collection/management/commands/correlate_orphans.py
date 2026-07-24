"""
Re-scan orphan CDR records and link them to their pairs.

Each call/SMS pair (originating + terminating) is supposed to be linked
automatically at end-of-file via the processor's post_process hook.  But
late-arriving pairs — where the term leg ships in a file delivered hours
after the orig leg, or where one node's CDR upload stalls — won't pair
during their owning file's run.

This command scans every unpaired record in a sliding time window and
attempts re-pairing across the full table.  Run it on a Celery beat
schedule (e.g. every 30 minutes) to keep correlation health high.

Usage
-----
    # Re-pair both IMS + MSC orphans from the last 7 days (default)
    python manage.py correlate_orphans

    # Only IMS, last 24 hours
    python manage.py correlate_orphans --stream IMS --days 1

    # Wide sweep across the last 30 days
    python manage.py correlate_orphans --days 30

    # Dry-run — count orphans, don't link
    python manage.py correlate_orphans --dry-run
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Re-pair orphan CDR records (IMS / MSC) across files.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--stream', default='ALL', choices=['ALL', 'IMS', 'MSC'],
            help='Which stream to re-correlate (default: ALL).',
        )
        parser.add_argument(
            '--days', type=int, default=7,
            help='Look-back window for orphan records, in days (default: 7).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report orphan counts without modifying any records.',
        )
        parser.add_argument(
            '--limit', type=int, default=20000,
            help='Maximum number of orphan records to process per stream per run '
                 '(default: 20000).  Keeps each run bounded on busy systems.',
        )

    def handle(self, *args, **opts):
        stream = opts['stream'].upper()
        days = max(1, int(opts['days']))
        dry = bool(opts['dry_run'])
        limit = max(1, int(opts['limit']))
        cutoff = timezone.now() - timedelta(days=days)

        self.stdout.write(
            self.style.HTTP_INFO(
                f'Orphan re-correlation: stream={stream} window={days}d '
                f'limit={limit} dry_run={dry}'
            )
        )

        if stream in ('ALL', 'IMS'):
            self._run_ims(cutoff, dry, limit)
        if stream in ('ALL', 'MSC'):
            self._run_msc(cutoff, dry, limit)

    # ----------------------------------------------------------------------
    # Per-stream handlers
    # ----------------------------------------------------------------------

    def _run_ims(self, cutoff, dry: bool, limit: int) -> None:
        from streams.ims.models import IMSRecord
        from streams.ims.correlation import correlate_unpaired_ims

        orphans = (IMSRecord.objects
                   .filter(paired_record__isnull=True, created_at__gte=cutoff)
                   .exclude(icid='')
                   .exclude(icid__isnull=True))
        n = orphans.count()
        self.stdout.write(f'  IMS  orphans in window: {n} (processing up to {limit})')
        if dry or n == 0:
            return
        linked = correlate_unpaired_ims(orphans[:limit], candidate_window_days=None)
        self.stdout.write(self.style.SUCCESS(
            f'  IMS  linked {linked} pair(s)'
        ))

    def _run_msc(self, cutoff, dry: bool, limit: int) -> None:
        from streams.msc.models import MSCRecord
        from streams.msc.correlation import correlate_unpaired_msc

        orphans = (MSCRecord.objects
                   .filter(paired_record__isnull=True, created_at__gte=cutoff)
                   .exclude(call_reference='')
                   .exclude(call_reference__isnull=True))
        n = orphans.count()
        self.stdout.write(f'  MSC  orphans in window: {n} (processing up to {limit})')
        if dry or n == 0:
            return
        linked = correlate_unpaired_msc(orphans[:limit], candidate_window_days=None)
        self.stdout.write(self.style.SUCCESS(
            f'  MSC  linked {linked} pair(s)'
        ))
