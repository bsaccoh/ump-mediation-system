"""Distributor daemon — polls for DECODED files and dispatches via output portals.

Runs as a long-lived process under systemd (mediation-distributor.service).
Each cycle picks up CDRFiles with status=DECODED, runs dispatch rules, and
sets status to COMPLETED on success.

    python manage.py run_distributor
    python manage.py run_distributor --batch 20 --interval 5
"""
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from collection.models import CDRFile
from core.dispatcher import dispatch_cdr_file
from core.activity import log_activity

import logging

logger = logging.getLogger('mediation.distributor')


class Command(BaseCommand):
    help = 'Long-running distributor daemon that dispatches DECODED CDR files.'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shutdown = False

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch', type=int, default=10,
            help='Max files to dispatch per cycle (default: 10).',
        )
        parser.add_argument(
            '--interval', type=int,
            default=getattr(settings, 'SERVICE_POLL_INTERVAL', 10),
            help='Seconds between poll cycles when idle (default: SERVICE_POLL_INTERVAL).',
        )

    def handle(self, *args, **opts):
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        batch_size = opts['batch']
        interval = opts['interval']

        logger.info(f'Distributor started (batch={batch_size}, interval={interval}s)')
        log_activity('SERVICE_STARTED', 'DISTRIBUTION',
                     message=f'Distributor started (batch={batch_size}, interval={interval}s)')
        self.stdout.write(self.style.SUCCESS(
            f'Distributor daemon started — batch={batch_size}, poll every {interval}s'))

        while not self._shutdown:
            try:
                dispatched = self._dispatch_batch(batch_size)
                if dispatched:
                    logger.info(f'Cycle: dispatched {dispatched} files')
                    continue
            except Exception as e:
                logger.error(f'Distributor cycle error: {e}', exc_info=True)
                log_activity('DISPATCH_ERROR', 'DISTRIBUTION', level='ERROR',
                             message=f'Distributor cycle error: {e}')

            for _ in range(interval):
                if self._shutdown:
                    break
                time.sleep(1)

        logger.info('Distributor shutting down')
        log_activity('SERVICE_STOPPED', 'DISTRIBUTION', message='Distributor shutting down')

    def _dispatch_batch(self, batch_size):
        with transaction.atomic():
            files = list(
                CDRFile.objects.select_for_update(skip_locked=True)
                .filter(status=CDRFile.Status.DECODED)
                .order_by('created_at')[:batch_size]
            )
        if not files:
            return 0

        dispatched = 0
        for cdr_file in files:
            if self._shutdown:
                break
            try:
                cdr_file.status = CDRFile.Status.DISPATCHING
                cdr_file.save(update_fields=['status'])

                summaries = dispatch_cdr_file(cdr_file.id)
                if any(summary.get('status') == 'FAILED' for summary in summaries):
                    raise RuntimeError('One or more downstream deliveries failed')
                from collection.services.storage import archive_file
                archived = archive_file(
                    cdr_file.file_path,
                    cdr_file.operator_code,
                    cdr_file.vendor,
                    cdr_file.network_element,
                    cdr_file.decoder_type,
                    cdr_file.cbs_substream,
                )
                logger.info(f'Dispatched {cdr_file.filename}: {summaries}')
                log_activity('DISPATCH_COMPLETED', 'DISTRIBUTION',
                             message=f'Dispatched {cdr_file.filename}',
                             stream=cdr_file.decoder_type or '',
                             operator=cdr_file.operator_code or '',
                             cdr_file=cdr_file)

                cdr_file.status = CDRFile.Status.COMPLETED
                cdr_file.file_path = archived
                cdr_file.archive_path = archived
                cdr_file.archived_at = timezone.now()
                cdr_file.save(update_fields=['status', 'file_path', 'archive_path', 'archived_at'])
                dispatched += 1
            except Exception as e:
                logger.error(
                    f'Failed to dispatch {cdr_file.filename}: {e}', exc_info=True)
                cdr_file.status = CDRFile.Status.FAILED
                cdr_file.error_message = f'Distribution failed: {str(e)[:480]}'
                cdr_file.save(update_fields=['status', 'error_message'])
                log_activity('DISPATCH_FAILED', 'DISTRIBUTION', level='ERROR',
                             message=f'Failed to dispatch {cdr_file.filename}: {e}',
                             stream=cdr_file.decoder_type or '',
                             operator=cdr_file.operator_code or '',
                             cdr_file=cdr_file)
        return dispatched

    def _handle_signal(self, signum, frame):
        self._shutdown = True
