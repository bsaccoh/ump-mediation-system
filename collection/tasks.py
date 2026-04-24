"""
Collection Celery Tasks
========================
Periodic and on-demand tasks for collecting CDR files from remote sources.
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def poll_sftp_sources(self):
    """Poll all enabled SFTP data sources for new CDR files.

    Designed to run on a schedule via Celery Beat (e.g. every 5 minutes).
    Can also be triggered manually.
    """
    from collection.models import DataSource

    sources = DataSource.objects.filter(
        source_type=DataSource.SourceType.SFTP,
        enabled=True
    )

    if not sources.exists():
        logger.info('No enabled SFTP sources to poll')
        return {'sources': 0}

    results = {}
    for source in sources:
        try:
            stats = poll_single_source(source.pk)
            results[source.name] = stats
        except Exception as e:
            logger.error(f'Error polling {source.name}: {e}')
            results[source.name] = {'error': str(e)}

    total_collected = sum(
        r.get('collected', 0) for r in results.values() if isinstance(r, dict)
    )
    logger.info(f'SFTP poll complete: {len(results)} sources, {total_collected} files collected')
    return results


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def poll_single_source(self, source_id: int) -> dict:
    """Poll a single SFTP data source.

    Can be called directly or from poll_sftp_sources.
    """
    from collection.models import DataSource
    from collection.services.sftp_collector import poll_source

    try:
        source = DataSource.objects.get(pk=source_id)
    except DataSource.DoesNotExist:
        logger.error(f'DataSource #{source_id} not found')
        return {'error': 'Source not found'}

    if source.source_type != DataSource.SourceType.SFTP:
        logger.warning(f'DataSource {source.name} is not SFTP type')
        return {'error': 'Not an SFTP source'}

    try:
        return poll_source(source)
    except Exception as e:
        logger.error(f'SFTP poll failed for {source.name}: {e}', exc_info=True)
        raise self.retry(exc=e)


def poll_source_sync(source_id: int) -> dict:
    """Synchronous SFTP poll for use without Celery."""
    from collection.models import DataSource
    from collection.services.sftp_collector import poll_source

    source = DataSource.objects.get(pk=source_id)
    return poll_source(source)
