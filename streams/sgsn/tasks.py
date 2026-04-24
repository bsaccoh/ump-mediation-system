"""SGSN Celery Tasks"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_sgsn_file(self, cdr_file_id: int):
    """Process an SGSN CDR file asynchronously via Celery."""
    from streams.sgsn.processor import SGSNProcessor

    processor = SGSNProcessor()
    success, message = processor.process(cdr_file_id)

    if not success:
        logger.error(f'SGSN processing failed for file {cdr_file_id}: {message}')
        raise self.retry(exc=Exception(message))

    logger.info(f'SGSN processing complete for file {cdr_file_id}: {message}')
    return {'success': True, 'message': message}
