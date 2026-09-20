"""Collector daemon — polls input directories and registers new CDR files.

Runs as a long-lived process under systemd (mediation-collector.service).
Each cycle walks DATA_DIR/{operator}/input/{vendor}/{ne}/ and creates a
CDRFile with status=COLLECTED for every new file (deduplicated by hash).

    python manage.py run_collector
    python manage.py run_collector --operator orange --interval 30
"""
import os
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from collection.models import CDRFile
from collection.services.deduplication import get_file_hash, check_duplicate
from collection.services.file_detector import classify_file
from collection.services.paths import PathBuilder
from core.activity import log_activity

import logging

logger = logging.getLogger('mediation.collector')


class Command(BaseCommand):
    help = 'Long-running collector daemon that polls input dirs for new CDR files.'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shutdown = False

    def add_arguments(self, parser):
        parser.add_argument('--operator', help='Limit to one operator code.')
        parser.add_argument(
            '--interval', type=int,
            default=getattr(settings, 'SERVICE_POLL_INTERVAL', 10),
            help='Seconds between collection cycles (default: SERVICE_POLL_INTERVAL).',
        )

    def handle(self, *args, **opts):
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        only = opts.get('operator')
        interval = opts['interval']

        logger.info(f'Collector started (interval={interval}s, operator={only or "all"})')
        log_activity('SERVICE_STARTED', 'COLLECTION',
                     message=f'Collector started (interval={interval}s, operator={only or "all"})')
        self.stdout.write(self.style.SUCCESS(
            f'Collector daemon started — polling every {interval}s'))

        while not self._shutdown:
            try:
                collected, skipped = self._scan(only)
                if collected:
                    logger.info(f'Cycle: {collected} registered, {skipped} duplicates')
                    log_activity('COLLECTION_CYCLE', 'COLLECTION',
                                 message=f'Registered {collected} files, {skipped} duplicates')
            except Exception as e:
                logger.error(f'Collection cycle error: {e}', exc_info=True)
                log_activity('COLLECTION_ERROR', 'COLLECTION', level='ERROR',
                             message=f'Collection cycle error: {e}')

            for _ in range(interval):
                if self._shutdown:
                    break
                time.sleep(1)

        logger.info('Collector shutting down')
        log_activity('SERVICE_STOPPED', 'COLLECTION', message='Collector shutting down')

    def _scan(self, only_operator):
        from core.models import SystemControl
        if SystemControl.is_intake_paused():
            logger.info('Intake paused — scan skipped')
            return 0, 0

        collected = skipped = 0
        input_root = PathBuilder.input_published('unknown', 'unknown').parents[1]
        if not input_root.is_dir():
            return collected, skipped

        for operator in sorted(path.name for path in input_root.iterdir() if path.is_dir()):
            if only_operator and operator != only_operator:
                continue
            operator_root = input_root / operator
            if not operator_root.is_dir():
                continue
            for root, dirs, files in os.walk(operator_root):
                dirs[:] = [directory for directory in dirs if directory != 'staging']
                rel = os.path.relpath(root, operator_root).split(os.sep)
                stream = rel[0] if rel and rel[0] != '.' else ''
                cbs_substream = rel[1] if stream.lower() == 'cbs' and len(rel) > 1 else ''
                for fname in files:
                    if fname.startswith('.'):
                        continue
                    fpath = os.path.join(root, fname)
                    if check_duplicate(fpath):
                        skipped += 1
                        log_activity('DUPLICATE_DETECTED', 'COLLECTION',
                                     message=f'Duplicate skipped: {fname}',
                                     stream=classify_file(fname).decoder_type or '',
                                     operator=operator)
                        continue
                    cls = classify_file(fname)
                    cdr = CDRFile.objects.create(
                        filename=fname,
                        file_path=fpath,
                        file_size=os.path.getsize(fpath),
                        file_hash=get_file_hash(fpath),
                        decoder_type=cls.decoder_type,
                        operator_code=cls.operator or operator,
                        vendor=cls.vendor or '',
                        network_element=cls.network_element or stream,
                        cbs_substream=cbs_substream,
                        status=CDRFile.Status.COLLECTED,
                    )
                    collected += 1
                    log_activity('FILE_REGISTERED', 'COLLECTION',
                                 message=f'Registered {fname}',
                                 stream=cls.decoder_type or '',
                                 operator=cls.operator or operator,
                                 cdr_file=cdr)
                    logger.debug(f'Registered {operator}/{stream}/{fname}')

        return collected, skipped

    def _handle_signal(self, signum, frame):
        self._shutdown = True
