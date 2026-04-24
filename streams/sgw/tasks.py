"""SGW Celery Tasks"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_sgw_file(self, cdr_file_id: int):
    """Process an SGW CDR file asynchronously via Celery."""
    from streams.sgw.processor import SGWProcessor

    processor = SGWProcessor()
    success, message = processor.process(cdr_file_id)

    if not success:
        logger.error(f'SGW processing failed for file {cdr_file_id}: {message}')
        raise self.retry(exc=Exception(message))

    logger.info(f'SGW processing complete for file {cdr_file_id}: {message}')
    return {'success': True, 'message': message}
