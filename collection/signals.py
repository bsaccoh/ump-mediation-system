"""
Collection Signals
===================
Auto-trigger processing when a CDRFile is created with PENDING status.
Falls back to synchronous processing if Celery is not available.

Uses a single worker thread with a queue to avoid GIL contention
when multiple files are uploaded simultaneously.
"""
import logging
import os
import queue
import threading
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import CDRFile

logger = logging.getLogger(__name__)

# Single worker queue — files are processed one at a time so they don't
# compete for the GIL via parallel CPU-heavy threads.
_process_queue = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


_PROCESSOR_MAP = {
    'MSC':  ('streams.msc.processor', 'MSCProcessor'),
    'IMS':  ('streams.ims.processor', 'IMSProcessor'),
    'PGW':  ('streams.pgw.processor', 'PGWProcessor'),
    'SGSN': ('streams.sgsn.processor', 'SGSNProcessor'),
    'SGW':  ('streams.sgw.processor', 'SGWProcessor'),
    'CBS':  ('streams.cbs.processor', 'CBSProcessor'),
}


def _process_sync(decoder, cdr_file_id, filename):
    """Process a CDR file synchronously, then archive the input file."""
    import importlib
    success = False
    try:
        entry = _PROCESSOR_MAP.get(decoder)
        if entry is None:
            logger.warning(f'No processor for decoder type: {decoder}')
            return
        mod, cls_name = entry
        processor = getattr(importlib.import_module(mod), cls_name)()
        success, message = processor.process(cdr_file_id)
        if success:
            logger.info(f'{decoder} processing complete: {message}')
        else:
            logger.error(f'{decoder} processing failed: {message}')
    except Exception as e:
        logger.error(f'Processing error for {filename}: {e}', exc_info=True)

    if success:
        _archive_after_processing(cdr_file_id, filename)


def _archive_after_processing(cdr_file_id, filename):
    """Archive the processed input file from the processing directory."""
    try:
        from collection.services.storage import archive_file
        cdr = CDRFile.objects.filter(pk=cdr_file_id).first()
        if not cdr or cdr.archive_path:
            return
        if not cdr.file_path or not os.path.isfile(cdr.file_path):
            return
        archived = archive_file(
            cdr.file_path, cdr.operator_code, cdr.vendor,
            cdr.network_element, cdr.decoder_type,
            getattr(cdr, 'cbs_substream', None),
        )
        CDRFile.objects.filter(pk=cdr.pk).update(
            file_path=archived, archive_path=archived, archived_at=timezone.now(),
        )
        logger.info(f'Archived {filename} -> {archived}')
    except Exception as e:
        logger.error(f'Archive failed for {filename}: {e}')


def _queue_worker():
    """Single worker thread that drains the processing queue sequentially."""
    while True:
        item = _process_queue.get()
        if item is None:
            break
        decoder, cdr_file_id, filename = item
        logger.info(f'Queue worker: processing {filename} (decoder={decoder})')
        _process_sync(decoder, cdr_file_id, filename)
        _process_queue.task_done()


def _ensure_worker():
    """Start the queue worker thread once (thread-safe)."""
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        t = threading.Thread(target=_queue_worker, daemon=True, name='cdr-processor')
        t.start()
        _worker_started = True
        logger.info('Started CDR processing worker thread')


@receiver(post_save, sender=CDRFile)
def trigger_processing_on_create(sender, instance, created, **kwargs):
    """When a new CDRFile is created with PENDING status, queue it for processing.

    In SERVICE_MODE the decoder service polls for files itself, so this
    signal is a no-op — each pipeline stage is driven by its own systemd
    service instead.
    """
    if not created:
        return
    if getattr(settings, 'SERVICE_MODE', False):
        return
    if instance.status not in (CDRFile.Status.PENDING, CDRFile.Status.COLLECTED):
        return

    from collection.services.file_detector import detect_decoder_type

    decoder = instance.decoder_type
    if not decoder or decoder == 'AUTO':
        decoder = detect_decoder_type(instance.filename)
        if decoder != instance.decoder_type:
            CDRFile.objects.filter(pk=instance.pk).update(decoder_type=decoder)

    # In development or if CELERY_ALWAYS_EAGER, process synchronously
    use_celery = getattr(settings, 'USE_CELERY', False)

    if use_celery:
        try:
            if decoder == 'MSC':
                from streams.msc.tasks import process_msc_file
                process_msc_file.delay(instance.pk)
                logger.info(f'Queued MSC processing (Celery) for {instance.filename}')
                return
            elif decoder == 'IMS':
                from streams.ims.tasks import process_ims_file
                process_ims_file.delay(instance.pk)
                logger.info(f'Queued IMS processing (Celery) for {instance.filename}')
                return
            elif decoder == 'PGW':
                from streams.pgw.tasks import process_pgw_file
                process_pgw_file.delay(instance.pk)
                logger.info(f'Queued PGW processing (Celery) for {instance.filename}')
                return
            elif decoder == 'SGSN':
                from streams.sgsn.tasks import process_sgsn_file
                process_sgsn_file.delay(instance.pk)
                logger.info(f'Queued SGSN processing (Celery) for {instance.filename}')
                return
            elif decoder == 'SGW':
                from streams.sgw.tasks import process_sgw_file
                process_sgw_file.delay(instance.pk)
                logger.info(f'Queued SGW processing (Celery) for {instance.filename}')
                return
            elif decoder == 'CBS':
                from streams.cbs.tasks import process_cbs_file
                process_cbs_file.delay(instance.pk)
                logger.info(f'Queued CBS processing (Celery) for {instance.filename}')
                return
        except Exception as e:
            logger.warning(f'Celery unavailable ({e}), falling back to sync')

    # Queue for the single worker thread (avoids GIL contention from
    # parallel CPU-heavy processing threads).
    _ensure_worker()
    _process_queue.put((decoder, instance.pk, instance.filename))
    logger.info(f'Queued processing for {instance.filename} (decoder={decoder})')


def dispatch_processing(decoder, cdr_file_id, filename):
    """Route a CDR file to Celery or the queue worker thread.

    Used by views for reprocessing/manual-upload scenarios — avoids
    spawning ad-hoc threads that bypass the queue worker.
    """
    use_celery = getattr(settings, 'USE_CELERY', False)

    if use_celery:
        try:
            from collection.tasks import process_cdr_file
            process_cdr_file.delay(decoder, cdr_file_id)
            logger.info(f'Dispatched {filename} via Celery (decoder={decoder})')
            return
        except Exception as e:
            logger.warning(f'Celery unavailable ({e}), falling back to queue worker')

    _ensure_worker()
    _process_queue.put((decoder, cdr_file_id, filename))
    logger.info(f'Queued {filename} for processing (decoder={decoder})')
