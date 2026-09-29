"""Ingest orchestrator: parse → normalize → validate → persist.

Runs in one streaming pass so memory stays bounded on large logs. Reprocessing
a file is idempotent — its previous samples are cleared first, so counts never
double. Progress is reported to the linked core.JobRecord when present.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from drive_test.models import Sample
from drive_test.models.enums import FileStatus, Technology, ValidationStatus
from drive_test.parsers import detect_parser
from drive_test.services.normalizer import OperatorResolver, build_sample
from drive_test.services.quality import QualityAccumulator

logger = logging.getLogger('drive_test')

_VALID_TECHS = {t.value for t in Technology}


def _batch_size() -> int:
    return int(getattr(settings, 'CDR_BATCH_SIZE', 2000))


def _set_progress(job, pct):
    if job is None:
        return
    try:
        job.progress_pct = max(0, min(99, int(pct)))
        job.save(update_fields=['progress_pct'])
    except Exception:  # pragma: no cover - progress is best-effort
        pass


def process_file(file_id: int) -> dict:
    """Parse and persist one DriveTestFile's samples. Returns a result dict
    (also stored on the JobRecord).
    """
    from drive_test.models import DriveTestFile

    dtf = DriveTestFile.objects.select_related('campaign').get(pk=file_id)
    job = dtf.job
    campaign = dtf.campaign

    parser = detect_parser(dtf.stored_path)
    if parser is None:
        dtf.status = FileStatus.FAILED
        dtf.error_message = 'Unsupported or unrecognised file format.'
        dtf.validation_status = ValidationStatus.INVALID
        dtf.save(update_fields=['status', 'error_message', 'validation_status'])
        return {'message': 'Unsupported format', 'samples': 0}

    dtf.status = FileStatus.PROCESSING
    dtf.error_message = ''
    dtf.save(update_fields=['status', 'error_message'])

    # Idempotent reprocess: drop this file's prior samples.
    Sample.objects.filter(drive_file=dtf).delete()

    resolver = OperatorResolver()
    quality = QualityAccumulator()
    tech_counts: Counter = Counter()
    op_counts: Counter = Counter()
    expected = (dtf.profile or {}).get('sample_count') or 0

    base_ts = dtf.uploaded_at or timezone.now()
    if timezone.is_aware(base_ts):
        base_ts = timezone.make_naive(base_ts)

    batch = []
    bsize = _batch_size()
    total = 0

    try:
        for idx, parsed in enumerate(parser.parse(dtf.stored_path)):
            is_valid, flags = quality.evaluate(parsed)
            operator = resolver.resolve(parsed)
            fallback_ts = base_ts + timedelta(milliseconds=idx)
            sample = build_sample(
                parsed, campaign=campaign, drive_file=dtf,
                operator=operator, timestamp=fallback_ts,
            )
            sample.is_valid = is_valid
            sample.quality_flags = flags
            batch.append(sample)

            tech = parsed.technology if parsed.technology in _VALID_TECHS else ''
            if tech:
                tech_counts[tech] += 1
            if operator:
                op_counts[operator.id] += 1

            total += 1
            if len(batch) >= bsize:
                Sample.objects.bulk_create(batch, batch_size=bsize)
                batch.clear()
                if expected:
                    _set_progress(job, 100 * total / expected)

        if batch:
            Sample.objects.bulk_create(batch, batch_size=bsize)
    except Exception as exc:
        logger.exception('Processing failed for file %s', file_id)
        dtf.status = FileStatus.FAILED
        dtf.error_message = f'{type(exc).__name__}: {exc}'
        dtf.save(update_fields=['status', 'error_message'])
        raise

    score, report = quality.summary()
    dominant_tech = tech_counts.most_common(1)[0][0] if tech_counts else ''

    with transaction.atomic():
        dtf.sample_count = total
        dtf.gps_available = quality.gps_present > 0 if total else None
        dtf.detected_technology = dominant_tech or dtf.detected_technology
        dtf.quality_score = score
        dtf.quality_report = report
        dtf.processed_at = timezone.now()
        dtf.validation_status = (
            ValidationStatus.VALID if total and (score or 0) >= 60
            else ValidationStatus.WARNING if total else ValidationStatus.INVALID
        )
        dtf.status = FileStatus.COMPLETED if total else FileStatus.FAILED
        if not total:
            dtf.error_message = 'No samples could be parsed from this file.'
        dtf.save()

    # Refresh campaign-scope KPI roll-ups so analytics pages stay fast.
    if total:
        try:
            from drive_test.services.analytics import store_campaign_rollups
            store_campaign_rollups(campaign)
        except Exception:  # pragma: no cover - roll-ups must not fail an ingest
            logger.exception('Roll-up computation failed for campaign %s', campaign.pk)

    logger.info('Processed file %s: %d samples, quality=%s', file_id, total, score)
    return {
        'message': f'Ingested {total} samples',
        'samples': total,
        'quality_score': score,
        'technology': dominant_tech,
        'result_entity_type': 'DriveTestFile',
        'result_entity_id': str(dtf.pk),
        'result_url': f'/drive-test/campaigns/{campaign.pk}/',
    }
