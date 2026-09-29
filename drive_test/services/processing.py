"""
Processing Monitor — read-only visibility into the existing drive-test ingestion
pipeline. This module creates NO new persistent job model. DriveTestFile.status
(set by drive_test.tasks._run_process_drive_test_file) already tracks the
pipeline's real state; this module only reads it, plus the durable side effects
each stage leaves behind (measurement_count, matched_cell rows, DataQualityResult),
to answer "what actually happened" — never a timer, never invented progress.

Pipeline as actually implemented in tasks.py:
    RECEIVED -> PARSING (parse + bulk-insert measurements)
             -> MATCHING (cell reference matching)
             -> NORMALIZING (analysis engine -> data quality assessment -> session aggregates)
             -> COMPLETED
A file can become FAILED from any of those steps. Because the exception handler
in tasks.py overwrites `status` to FAILED (there is no separate "last stage
reached" column), a FAILED file's per-stage completion below is inferred only
from durable evidence that step actually persisted (records inserted, a
matched_cell row, a DataQualityResult row) — never guessed from the final
status alone. Where there is no such evidence, the stage is shown as not
confirmed rather than assumed done or assumed failed.
"""
from __future__ import annotations

from django.db.models import Count, OuterRef, Q, Subquery

from .data_quality import Q_MATCHED, Q_UNMATCHED

PAGE_SIZES = (20, 50, 100)

# In-progress DriveTestFile.Status values — used for the "Processing" summary
# card and to decide whether the page should auto-refresh.
PROCESSING_STATUSES = ('PARSING', 'PARSED', 'MATCHING', 'NORMALIZING')

# Human label per real DriveTestFile.Status value, shown in the job table's
# CURRENT STAGE column. A relabelling of the model's own choices, not a new state.
STAGE_LABEL = {
    'RECEIVED': 'Queued',
    'PARSING': 'Parsing',
    'PARSED': 'Parsed',
    'MATCHING': 'Cell Matching',
    'NORMALIZING': 'Analysis / Data Quality / Aggregation',
    'COMPLETED': 'Completed',
    'FAILED': 'Failed',
    'DUPLICATE': 'Duplicate (rejected)',
    'REJECTED': 'Rejected',
}

# Reuses the session_list/data_quality badge palette; RECEIVED/PARSED/DUPLICATE/
# REJECTED fall back to the neutral default badge — no new colour invented for them.
BADGE_CLASS = {
    'PARSING': 'dt-sl-badge-processing',
    'MATCHING': 'dt-sl-badge-processing',
    'NORMALIZING': 'dt-sl-badge-processing',
    'COMPLETED': 'dt-sl-badge-completed',
    'FAILED': 'dt-sl-badge-failed',
}


def filter_jobs(GET):
    """Server-side filters for the Processing Monitor.

    One row = one DriveTestFile — the actual unit of processing in this system.
    A session may contain more than one file. Returns (queryset, filters, errors).
    """
    from django.core.exceptions import ValidationError
    from ..models import DriveTestFile

    f = {k: GET.get(k, '').strip() for k in ('q', 'operator', 'status', 'format', 'date_from', 'date_to')}
    f['session'] = GET.get('session', '').strip()
    qs = DriveTestFile.objects.all()
    errors = []

    if f['q']:
        q = f['q']
        qs = qs.filter(
            Q(original_filename__icontains=q) | Q(session__session_ref__icontains=q)
            | Q(session__operator__name__icontains=q)
        )
    if f['session']:
        qs = qs.filter(session__session_ref__icontains=f['session'])
    if f['operator']:
        qs = qs.filter(session__operator__code=f['operator'])
    if f['status']:
        if f['status'] not in DriveTestFile.Status.values:
            errors.append('Unrecognised status.')
            qs = DriveTestFile.objects.none()
        else:
            qs = qs.filter(status=f['status'])
    if f['format']:
        qs = qs.filter(Q(parser_profile__name=f['format']) | Q(detected_format=f['format']))
    try:
        if f['date_from']:
            qs = qs.filter(uploaded_at__date__gte=f['date_from'])
        if f['date_to']:
            qs = qs.filter(uploaded_at__date__lte=f['date_to'])
    except (ValueError, ValidationError):
        errors.append('Date must look like 2026-09-24.')
        qs = DriveTestFile.objects.none()

    return qs, f, errors


def annotate_device(qs):
    """Attach the earliest measurement's device label per file via one correlated
    subquery — never by loading every measurement into Python."""
    from ..models import Measurement

    device_sq = (Measurement.objects
                 .filter(drive_file=OuterRef('pk'), test_device__isnull=False)
                 .order_by('sequence_num')
                 .values('test_device__label')[:1])
    return qs.annotate(device_label=Subquery(device_sq))


def summary_counts(qs):
    """Total / Processing / Completed / Failed / Queued — real DriveTestFile.status
    counts only. 'Queued' = RECEIVED (uploaded, task not yet run) — the closest real
    equivalent this pipeline has to a queued state; nothing is invented."""
    return qs.aggregate(
        total=Count('id'),
        processing=Count('id', filter=Q(status__in=PROCESSING_STATUSES)),
        completed=Count('id', filter=Q(status='COMPLETED')),
        failed=Count('id', filter=Q(status='FAILED')),
        queued=Count('id', filter=Q(status='RECEIVED')),
    )


def job_stats(file):
    """Real record counts for one file's processing-detail page. Every number is a
    database aggregate over that file's own Measurement rows — never re-parsed,
    never estimated."""
    from ..models import DataQualityResult, Finding, Measurement

    meas = Measurement.objects.filter(drive_file=file)
    agg = meas.aggregate(
        total=Count('id'),
        valid=Count('id', filter=Q(is_valid=True)),
        invalid=Count('id', filter=Q(is_valid=False)),
        matched=Count('id', filter=Q_MATCHED),
        unmatched=Count('id', filter=Q(is_valid=True) & Q_UNMATCHED),
    )
    return {
        'records_parsed': file.measurement_count or agg['total'],
        'valid_measurements': agg['valid'],
        'invalid_measurements': agg['invalid'],
        'cell_matched': agg['matched'],
        'cell_unmatched': agg['unmatched'],
        'has_quality_result': DataQualityResult.objects.filter(drive_file=file).exists(),
        # Finding is recorded at session level, not per file — shown separately on the
        # detail page, clearly labelled, never implied to belong to this file alone.
        'session_findings': Finding.objects.filter(session=file.session).count(),
    }


# ---------------------------------------------------------------------------
# Processing Detail timeline
# ---------------------------------------------------------------------------
# Each check function returns True only when there is durable, persisted evidence
# that stage actually ran — real counts/rows, never a guess from `status` alone
# (status gets overwritten to FAILED by the pipeline's own exception handler, so
# it cannot by itself say which stage a failed file reached).

def _upload_done(f, s):
    return True  # the DriveTestFile row existing at all *is* the upload record


def _detection_done(f, s):
    return bool(f.parser_profile_id or f.detected_format)


def _parsing_done(f, s):
    return f.status in ('MATCHING', 'NORMALIZING', 'COMPLETED') or s['records_parsed'] > 0


def _insert_done(f, s):
    return s['records_parsed'] > 0


def _matching_done(f, s):
    return f.status in ('NORMALIZING', 'COMPLETED') or s['cell_matched'] > 0


def _analysis_done(f, s):
    return f.status == 'COMPLETED' or s['has_quality_result']


def _quality_done(f, s):
    return s['has_quality_result']


def _aggregation_done(f, s):
    return f.status == 'COMPLETED'


def _completed_done(f, s):
    return f.status == 'COMPLETED'


_STAGES = (
    ('upload', 'Upload', _upload_done),
    ('detection', 'Parser Detection', _detection_done),
    ('parsing', 'Parsing', _parsing_done),
    ('insert', 'Measurement Insert', _insert_done),
    ('matching', 'Cell Matching', _matching_done),
    ('analysis', 'Analysis', _analysis_done),
    ('quality', 'Data Quality', _quality_done),
    ('aggregation', 'Aggregation', _aggregation_done),
    ('completed', 'Completed', _completed_done),
)

_CURRENT_STAGE_KEY = {
    'RECEIVED': 'detection', 'PARSING': 'parsing', 'PARSED': 'insert',
    'MATCHING': 'matching', 'NORMALIZING': 'analysis',
}


def stage_timeline(file, stats):
    """Ordered [{key, label, state}] for the Processing Detail page.

    state is one of:
      'done'    — the backend has persisted real evidence this stage ran
      'current' — DriveTestFile.status says the pipeline is in this stage right now
      'failed'  — the file is FAILED and this is the first stage without done evidence
      'pending' — not reached, or (for a FAILED file past the failed marker) not
                  confirmed — never displayed as done or as failed once one stage
                  has already been marked as the failure point
    """
    rows = []
    is_failed = file.status == 'FAILED'
    current_key = _CURRENT_STAGE_KEY.get(file.status)
    marked_current = False
    marked_failed = False
    for key, label, check in _STAGES:
        if check(file, stats):
            state = 'done'
        elif is_failed:
            state = 'pending' if marked_failed else 'failed'
            marked_failed = True
        elif not marked_current and key == current_key:
            state = 'current'
            marked_current = True
        else:
            state = 'pending'
        rows.append({'key': key, 'label': label, 'state': state})
    return rows
