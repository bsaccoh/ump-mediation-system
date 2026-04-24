"""
MSC Celery Tasks
=================
Async tasks for MSC CDR file processing.
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_msc_file(self, cdr_file_id: int):
    """Process an MSC CDR file asynchronously.

    Called by the collection signal when a new MSC file is uploaded.
    Retries up to 3 times on failure.
    """
    from streams.msc.processor import MSCProcessor

    logger.info(f'Starting MSC processing for CDRFile #{cdr_file_id}')

    processor = MSCProcessor()
    success, message = processor.process(cdr_file_id)

    if success:
        logger.info(f'MSC processing complete: {message}')
    else:
        logger.error(f'MSC processing failed: {message}')
        # Retry on failure
        raise self.retry(exc=Exception(message))

    return {'success': success, 'message': message}


@shared_task
def process_msc_file_sync(cdr_file_id: int):
    """Process an MSC CDR file synchronously (no retry).

    Used for manual reprocessing or when Celery is not available.
    """
    from streams.msc.processor import MSCProcessor

    processor = MSCProcessor()
    return processor.process(cdr_file_id)
