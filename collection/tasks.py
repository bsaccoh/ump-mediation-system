"""
Collection Celery Tasks
========================
Periodic and on-demand tasks for collecting CDR files from remote sources.
"""
import logging
import os
from celery import shared_task

logger = logging.getLogger(__name__)


def _get_processor(decoder_type: str):
    """Return an instantiated processor for the given decoder type."""
    from core.enums import DecoderType
    if decoder_type == DecoderType.MSC:
        from streams.msc.processor import MSCProcessor
        return MSCProcessor()
    if decoder_type == DecoderType.IMS:
        from streams.ims.processor import IMSProcessor
        return IMSProcessor()
    if decoder_type == DecoderType.PGW:
        from streams.pgw.processor import PGWProcessor
        return PGWProcessor()
    if decoder_type == DecoderType.SGSN:
        from streams.sgsn.processor import SGSNProcessor
        return SGSNProcessor()
    if decoder_type == DecoderType.SGW:
        from streams.sgw.processor import SGWProcessor
        return SGWProcessor()
    if decoder_type == DecoderType.CBS:
        from streams.cbs.processor import CBSProcessor
        return CBSProcessor()
    raise ValueError(f'No processor available for decoder_type={decoder_type!r}')


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def replay_uploaded_file(self, cdr_file_id: int, temp_path: str, portal_id: int,
                         requested_by: str = '') -> dict:
    """Re-decode an uploaded CDR file and deliver it to one output portal only.

    Called by the selective downstream replay upload view. The CDRFile must
    already be COMPLETED. This task:
    - Does NOT change CDRFile.status
    - Does NOT write records to the database
    - Calls dispatch_selective() so only the chosen portal receives the output
    - Always removes the temp file when done
    """
    from collection.models import CDRFile, ReplayLog
    from core.dispatcher import dispatch_selective
    from core.operator_context import set_operator, clear_operator

    replay_log = None
    try:
        cdr_file = CDRFile.objects.get(pk=cdr_file_id)
        from portals.models import OutputPortal
        portal = OutputPortal.objects.get(pk=portal_id)

        replay_log = ReplayLog.objects.create(
            cdr_file=cdr_file,
            output_portal=portal,
            requested_by=requested_by,
            task_id=self.request.id or '',
            status=ReplayLog.Status.PENDING,
        )

        set_operator(cdr_file.operator_code)
        processor = _get_processor(cdr_file.decoder_type)
        records = processor.replay_for_downstream(temp_path, cdr_file)

        summaries = dispatch_selective(cdr_file, records, portal_id)

        delivered = sum(s.get('records', 0) for s in summaries if s.get('status') == 'SUCCESS')
        failed_rules = [s for s in summaries if s.get('status') == 'FAILED']

        if failed_rules:
            errors = '; '.join(s.get('error', '') for s in failed_rules)
            replay_log.status = ReplayLog.Status.FAILED
            replay_log.error = errors[:1000]
        else:
            replay_log.status = ReplayLog.Status.SUCCESS
        replay_log.records_delivered = delivered
        replay_log.save(update_fields=['status', 'records_delivered', 'error'])

        logger.info(
            f'replay_uploaded_file: {cdr_file.filename} → {portal.name}: '
            f'{delivered} records, summaries={summaries}'
        )
        return {'file_id': cdr_file_id, 'portal': portal.name,
                'records': delivered, 'summaries': summaries}

    except Exception as exc:
        logger.error(f'replay_uploaded_file failed for CDRFile {cdr_file_id}: {exc}', exc_info=True)
        if replay_log:
            replay_log.status = ReplayLog.Status.FAILED
            replay_log.error = str(exc)[:1000]
            replay_log.save(update_fields=['status', 'error'])
        raise self.retry(exc=exc)
    finally:
        clear_operator()
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


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


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def process_cdr_file(self, decoder: str, cdr_file_id: int):
    """Process a single CDR file via Celery.

    Generic task that routes to the right stream processor based on decoder type.
    Used by dispatch_processing() for reprocessing and manual upload scenarios.
    """
    from collection.signals import _process_sync
    from collection.models import CDRFile

    try:
        cdr_file = CDRFile.objects.get(pk=cdr_file_id)
        filename = cdr_file.filename
    except CDRFile.DoesNotExist:
        logger.error(f'CDRFile #{cdr_file_id} not found')
        return {'error': 'File not found'}

    logger.info(f'Celery task: processing {filename} (decoder={decoder})')
    _process_sync(decoder, cdr_file_id, filename)
    return {'decoder': decoder, 'file_id': cdr_file_id, 'filename': filename}


@shared_task
def rescue_stuck_files() -> dict:
    """Re-queue CDRFiles stuck in COLLECTED status for more than 2 minutes.

    Runs every minute via Celery Beat. Prevents files from stalling indefinitely
    when Celery workers restart mid-batch or tasks are lost from the queue.
    """
    from datetime import timedelta
    from django.utils import timezone
    from collection.models import CDRFile
    from collection.signals import dispatch_processing

    cutoff = timezone.now() - timedelta(minutes=2)
    stuck = CDRFile.objects.filter(status=CDRFile.Status.COLLECTED, created_at__lte=cutoff)
    count = stuck.count()
    if not count:
        return {'rescued': 0}

    for f in stuck:
        try:
            dispatch_processing(f.decoder_type, f.pk, f.filename)
        except Exception as exc:
            logger.warning(f'rescue_stuck_files: could not re-queue {f.filename}: {exc}')

    logger.info(f'rescue_stuck_files: re-queued {count} stuck file(s)')
    return {'rescued': count}


@shared_task
def scheduled_collection(operator: str | None = None) -> dict:
    """Scan the per-operator input trees and decode new files in parallel.

    Runs the `process_batch` command in a detached subprocess (clean
    multiprocessing pool). Scheduled via Celery Beat; can also be triggered
    from the UI. Idempotent: already-processed files are skipped by hash.
    """
    from collection.services.runner import launch_batch

    info = launch_batch(operator)
    logger.info(f'scheduled_collection launched process_batch pid={info["pid"]} '
                f'operator={info["operator"]} log={info["log"]}')
    return info
