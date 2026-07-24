"""
Collection Signals
===================
Auto-trigger processing when a CDRFile is created with PENDING status.
Falls back to synchronous processing if Celery is not available.
"""
import logging
import threading
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import CDRFile

logger = logging.getLogger(__name__)


def _process_sync(decoder, cdr_file_id, filename):
    """Process a CDR file synchronously in a background thread."""
    try:
        if decoder == 'MSC':
            from streams.msc.processor import MSCProcessor
            processor = MSCProcessor()
            success, message = processor.process(cdr_file_id)
            if success:
                logger.info(f'MSC processing complete: {message}')
            else:
                logger.error(f'MSC processing failed: {message}')
        elif decoder == 'IMS':
            from streams.ims.processor import IMSProcessor
            processor = IMSProcessor()
            success, message = processor.process(cdr_file_id)
            if success:
                logger.info(f'IMS processing complete: {message}')
            else:
                logger.error(f'IMS processing failed: {message}')
        elif decoder == 'PGW':
            from streams.pgw.processor import PGWProcessor
            processor = PGWProcessor()
            success, message = processor.process(cdr_file_id)
            if success:
                logger.info(f'PGW processing complete: {message}')
            else:
                logger.error(f'PGW processing failed: {message}')
        elif decoder == 'SGSN':
            from streams.sgsn.processor import SGSNProcessor
            processor = SGSNProcessor()
            success, message = processor.process(cdr_file_id)
            if success:
                logger.info(f'SGSN processing complete: {message}')
            else:
                logger.error(f'SGSN processing failed: {message}')
        elif decoder == 'SGW':
            from streams.sgw.processor import SGWProcessor
            processor = SGWProcessor()
            success, message = processor.process(cdr_file_id)
            if success:
                logger.info(f'SGW processing complete: {message}')
            else:
                logger.error(f'SGW processing failed: {message}')
        elif decoder == 'CBS':
            from streams.cbs.processor import CBSProcessor
            processor = CBSProcessor()
            success, message = processor.process(cdr_file_id)
            if success:
                logger.info(f'CBS processing complete: {message}')
            else:
                logger.error(f'CBS processing failed: {message}')
        else:
            logger.warning(f'No processor for decoder type: {decoder}')
    except Exception as e:
        logger.error(f'Processing error for {filename}: {e}', exc_info=True)


@receiver(post_save, sender=CDRFile)
def trigger_processing_on_create(sender, instance, created, **kwargs):
    """When a new CDRFile is created with PENDING status, queue it for processing."""
    if not created:
        return
    if instance.status != CDRFile.Status.PENDING:
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

    # Synchronous processing in a thread (so signal returns immediately)
    thread = threading.Thread(
        target=_process_sync,
        args=(decoder, instance.pk, instance.filename),
        daemon=True,
    )
    thread.start()
    logger.info(f'Started sync processing thread for {instance.filename} (decoder={decoder})')
