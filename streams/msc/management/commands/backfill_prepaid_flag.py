"""Recompute ``MSCRecord.prepaid_flag`` from raw_data's SERVICE_KEY +
CAMEL_PHASE using the CAMEL-IN-trigger rule.

Usage::

    python manage.py backfill_prepaid_flag           # dry-run (no DB writes)
    python manage.py backfill_prepaid_flag --apply   # apply the update

The dry-run is always safe and reports:
  * total records scanned
  * how many would flip PREPAID / POSTPAID / no-change
  * distinct values seen for SERVICE_KEY and CAMEL_PHASE (top 5 each)
"""
from collections import Counter

from django.core.management.base import BaseCommand

from core.utils.prepaid import derive_msc_prepaid_flag
from streams.msc.models import MSCRecord


CHUNK = 5000


class Command(BaseCommand):
    help = ('Backfill MSCRecord.prepaid_flag from raw_data SERVICE_KEY + '
            'CAMEL_PHASE using the CAMEL IN-trigger rule.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Apply updates (default is dry-run).',
        )
        parser.add_argument(
            '--batch', type=int, default=2000,
            help='bulk_update batch size (default 2000).',
        )

    def handle(self, *args, **opts):
        apply_changes = opts['apply']
        batch_size = opts['batch']

        total = MSCRecord.objects.count()
        self.stdout.write(f'Scanning {total:,} MSCRecord rows…')

        # Sample distinct values (informational; surfaces 'CAMEL_PHASE=0' etc.)
        sk_counter = Counter()
        cp_counter = Counter()

        # Accumulator counters
        n_prepaid = 0
        n_postpaid = 0
        n_unchanged = 0
        n_updated = 0

        # Use only_id / values_list to avoid loading the full row twice.
        qs = MSCRecord.objects.only('id', 'prepaid_flag', 'imsi', 'raw_data').iterator(chunk_size=CHUNK)

        pending = []
        for rec in qs:
            raw = rec.raw_data or {}
            # Accept both decoder spellings (with and without underscore).
            sk = (raw.get('SERVICEKEY')
                  or raw.get('SERVICE_KEY')
                  or raw.get('CAMEL_SERVICE_KEY'))
            cp = raw.get('CAMELPHASE') or raw.get('CAMEL_PHASE')
            sk_counter[str(sk) if sk is not None else ''] += 1
            cp_counter[str(cp) if cp is not None else ''] += 1
            # IMSI on the model column trumps CAMEL when in the postpaid block.
            new_flag = derive_msc_prepaid_flag(sk, cp, imsi=rec.imsi)
            if new_flag == 'PREPAID':
                n_prepaid += 1
            else:
                n_postpaid += 1
            if new_flag != rec.prepaid_flag:
                n_updated += 1
                if apply_changes:
                    rec.prepaid_flag = new_flag
                    pending.append(rec)
                    if len(pending) >= batch_size:
                        MSCRecord.objects.bulk_update(pending, ['prepaid_flag'],
                                                      batch_size=batch_size)
                        pending.clear()
            else:
                n_unchanged += 1

        if apply_changes and pending:
            MSCRecord.objects.bulk_update(pending, ['prepaid_flag'],
                                          batch_size=batch_size)
            pending.clear()

        # ---- Report ----
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{"APPLIED" if apply_changes else "DRY-RUN"}'
            f'  scanned={total:,}  would_update={n_updated:,}  unchanged={n_unchanged:,}'
        ))
        self.stdout.write(f'  Will mark PREPAID:   {n_prepaid:,}')
        self.stdout.write(f'  Will mark POSTPAID:  {n_postpaid:,}')

        self.stdout.write('')
        self.stdout.write('  Top SERVICE_KEY values seen (count):')
        for val, n in sk_counter.most_common(5):
            label = repr(val) if val else '(blank)'
            self.stdout.write(f'    {label:>20}  {n:,}')

        self.stdout.write('  Top CAMEL_PHASE values seen (count):')
        for val, n in cp_counter.most_common(5):
            label = repr(val) if val else '(blank)'
            self.stdout.write(f'    {label:>20}  {n:,}')

        if not apply_changes:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'Dry-run only.  Re-run with --apply to commit.'
            ))
