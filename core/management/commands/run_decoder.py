"""Decoder daemon — polls for COLLECTED files and runs decode/validate/enrich.

Runs as a long-lived process under systemd (mediation-decoder.service).
Each cycle picks up CDRFiles with status=COLLECTED, routes them to the
appropriate stream processor, and sets status to DECODED on success.

    python manage.py run_decoder
    python manage.py run_decoder --batch 20 --interval 5
"""
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import models, transaction

from collection.models import CDRFile
from core.activity import log_activity

import logging

logger = logging.getLogger('mediation.decoder')

PROCESSOR_MAP = {
    'MSC':  'streams.msc.processor.MSCProcessor',
    'IMS':  'streams.ims.processor.IMSProcessor',
    'PGW':  'streams.pgw.processor.PGWProcessor',
    'SGSN': 'streams.sgsn.processor.SGSNProcessor',
    'SGW':  'streams.sgw.processor.SGWProcessor',
    'CBS':  'streams.cbs.processor.CBSProcessor',
}


def _get_processor(decoder_type):
    path = PROCESSOR_MAP.get(decoder_type)
    if not path:
        return None
    module_path, class_name = path.rsplit('.', 1)
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)()


class Command(BaseCommand):
    help = 'Long-running decoder daemon that processes COLLECTED CDR files.'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shutdown = False

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch', type=int, default=10,
            help='Max files to process per cycle (default: 10).',
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

        logger.info(f'Decoder started (batch={batch_size}, interval={interval}s)')
        log_activity('SERVICE_STARTED', 'DECODING',
                     message=f'Decoder started (batch={batch_size}, interval={interval}s)')
        self.stdout.write(self.style.SUCCESS(
            f'Decoder daemon started — batch={batch_size}, poll every {interval}s'))

        while not self._shutdown:
            try:
                processed = self._process_batch(batch_size)
                if processed:
                    logger.info(f'Cycle: decoded {processed} files')
                    continue
            except Exception as e:
                logger.error(f'Decoder cycle error: {e}', exc_info=True)
                log_activity('DECODE_ERROR', 'DECODING', level='ERROR',
                             message=f'Decoder cycle error: {e}')

            for _ in range(interval):
                if self._shutdown:
                    break
                time.sleep(1)

        logger.info('Decoder shutting down')
        log_activity('SERVICE_STOPPED', 'DECODING', message='Decoder shutting down')

    def _process_batch(self, batch_size):
        with transaction.atomic():
            files = list(
                CDRFile.objects.filter(status=CDRFile.Status.COLLECTED)
                .order_by('created_at')[:batch_size]
                .select_for_update(skip_locked=True)
            )
            if not files:
                return 0

            for cdr_file in files:
                cdr_file.status = CDRFile.Status.PROCESSING
                cdr_file.save(update_fields=['status'])

        processed = 0
        for cdr_file in files:
            if self._shutdown:
                break
            try:
                self._decode_file(cdr_file)
                processed += 1
            except Exception as e:
                logger.error(
                    f'Failed to decode {cdr_file.filename}: {e}', exc_info=True)
                cdr_file.status = CDRFile.Status.FAILED
                cdr_file.error_message = str(e)[:500]
                cdr_file.save(update_fields=['status', 'error_message'])
                log_activity('DECODE_FAILED', 'DECODING', level='ERROR',
                             message=f'Failed to decode {cdr_file.filename}: {e}',
                             stream=cdr_file.decoder_type or '',
                             operator=cdr_file.operator_code or '',
                             cdr_file=cdr_file)
        return processed

    def _decode_file(self, cdr_file):
        if not cdr_file.decoder_type or cdr_file.decoder_type == 'AUTO':
            from collection.services.file_detector import detect_decoder_type
            cdr_file.decoder_type = detect_decoder_type(cdr_file.filename)
            cdr_file.save(update_fields=['decoder_type'])

        if cdr_file.decoder_type == 'FORWARD':
            cdr_file.status = CDRFile.Status.DECODED
            cdr_file.save(update_fields=['status'])
            logger.info(f'Pass-through: {cdr_file.filename} (forwarding without decoding)')
            log_activity('DECODE_PASSTHROUGH', 'DECODING',
                         message=f'Pass-through: {cdr_file.filename}',
                         operator=cdr_file.operator_code or '',
                         cdr_file=cdr_file)
            return

        processor = _get_processor(cdr_file.decoder_type)
        if not processor:
            raise ValueError(f'No processor for decoder type: {cdr_file.decoder_type}')

        success, message = processor.process(cdr_file.pk)
        if success:
            logger.info(f'Decoded {cdr_file.filename}: {message}')
            log_activity('DECODE_COMPLETED', 'DECODING',
                         message=f'Decoded {cdr_file.filename}: {message}',
                         stream=cdr_file.decoder_type or '',
                         operator=cdr_file.operator_code or '',
                         cdr_file=cdr_file)
        else:
            raise RuntimeError(f'Processor returned failure: {message}')

    def _handle_signal(self, signum, frame):
        self._shutdown = True
