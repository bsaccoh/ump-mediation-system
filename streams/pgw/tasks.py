"""PGW Celery Tasks"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_pgw_file(self, cdr_file_id: int):
    """Process a PGW CDR file asynchronously via Celery."""
    from streams.pgw.processor import PGWProcessor

    processor = PGWProcessor()
    success, message = processor.process(cdr_file_id)

    if not success:
        logger.error(f'PGW processing failed for file {cdr_file_id}: {message}')
        raise self.retry(exc=Exception(message))

    logger.info(f'PGW processing complete for file {cdr_file_id}: {message}')
    return {'success': True, 'message': message}
