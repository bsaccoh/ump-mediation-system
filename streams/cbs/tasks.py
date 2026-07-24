"""
CBS Celery Tasks
=================
Async task wrappers for CBS CDR file processing.
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_cbs_file(self, cdr_file_id: int):
    """Process a CBS CDR file asynchronously (Celery path)."""
    from streams.cbs.processor import CBSProcessor

    logger.info(f'Starting CBS processing for CDRFile #{cdr_file_id}')
    processor = CBSProcessor()
    success, message = processor.process(cdr_file_id)

    if success:
        logger.info(f'CBS processing complete: {message}')
    else:
        logger.error(f'CBS processing failed: {message}')
        raise self.retry(exc=Exception(message))

    return {'success': success, 'message': message}


@shared_task
def process_cbs_file_sync(cdr_file_id: int):
    """Process a CBS CDR file synchronously (manual reprocess / no Celery)."""
    from streams.cbs.processor import CBSProcessor

    processor = CBSProcessor()
    return processor.process(cdr_file_id)
