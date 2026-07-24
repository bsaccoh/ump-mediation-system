"""
IMS Celery Tasks
=================
Async tasks for IMS CDR file processing.
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_ims_file(self, cdr_file_id: int):
    """Process an IMS CDR file asynchronously.

    Called by the collection signal when a new IMS file is uploaded.
    Retries up to 3 times on failure.
    """
    from streams.ims.processor import IMSProcessor

    logger.info(f'Starting IMS processing for CDRFile #{cdr_file_id}')

    processor = IMSProcessor()
    success, message = processor.process(cdr_file_id)

    if success:
        logger.info(f'IMS processing complete: {message}')
    else:
        logger.error(f'IMS processing failed: {message}')
        raise self.retry(exc=Exception(message))

    return {'success': success, 'message': message}


@shared_task
def process_ims_file_sync(cdr_file_id: int):
    """Process an IMS CDR file synchronously (no retry).

    Used for manual reprocessing or when Celery is not available.
    """
    from streams.ims.processor import IMSProcessor

    processor = IMSProcessor()
    return processor.process(cdr_file_id)
