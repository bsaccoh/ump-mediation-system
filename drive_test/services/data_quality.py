"""
Data Quality workspace — read-only aggregation over data the pipeline already computes.

This module runs NO new quality checks and stores nothing. It only:
  - rolls DataQualityResult (written by services.analysis.QualityAssessor, once per
    DriveTestFile) up to session level, by summing its own stored integer fields
  - re-applies QualityAssessor's own completeness/accuracy/overall formula at that
    rolled-up level (same arithmetic, summed inputs — not a new scoring model)
  - answers a handful of dimensions DataQualityResult does not persist (live GPS/
    radio-field/cell-match presence) with the same field definitions and (0, 0) /
    matched-unmatched-unknown conventions already used by the Measurements, GIS and
    Cell Matching pages (views.py: _match_state, cell_reference.has_valid_coords)

No new models. No changes to QualityAssessor, AnalysisEngine or CellReferenceMatcher.
"""
from __future__ import annotations

from django.db.models import Count, Q, Sum

# Display-only classification of the existing 0-100 overall_score, the same kind of
# banding DriveTestKpiService already applies to RSSI (kpi.py: RSSI_GOOD/FAIR/POOR).
# It does not change, store or invent a new score — the real number is always shown too.
GOOD, WARNING, POOR = 'good', 'warning', 'poor'
BAND_LABELS = {GOOD: 'Good', WARNING: 'Warning', POOR: 'Poor'}
_BAND_FLOORS = ((GOOD, 80.0), (WARNING, 50.0), (POOR, 0.0))

# Cell-matching states, exactly as views._match_state / cell_matching.html define them.
# There is no AMBIGUOUS state: the matcher never records one.
Q_MATCHED = Q(matched_cell__isnull=False)
Q_UNMATCHED = Q(matched_cell__isnull=True, drive_file__status='COMPLETED')
Q_UNKNOWN = Q(matched_cell__isnull=True) & ~Q(drive_file__status='COMPLETED')

# Observed radio identifiers that actually exist on Measurement/RadioMeasurement.
# Deliberately excludes NCI/BSIC/PSC/etc. — this data model does not carry them.
RADIO_SIGNAL_FIELDS = (
    ('rssi', 'RSSI', 'radio__rssi'), ('rsrp', 'RSRP', 'radio__rsrp'),
    ('rsrq', 'RSRQ', 'radio__rsrq'), ('sinr', 'SINR', 'radio__sinr'),
)
OBSERVED_ID_FIELDS = (
    ('pci', 'PCI', 'obs_pci'), ('earfcn', 'EARFCN', 'obs_earfcn'), ('nrarfcn', 'NR-ARFCN', 'obs_nrarfcn'),
    ('lac', 'LAC', 'obs_lac'), ('ci', 'CI', 'obs_ci'), ('tac', 'TAC', 'obs_tac'), ('eci', 'ECI', 'obs_eci'),
)


def quality_band(score):
    """(key, label) for a 0-100 score, or (None, '—') when there is no score yet."""
    if score is None:
        return None, '—'
    for key, floor in _BAND_FLOORS:
        if score >= floor:
            return key, BAND_LABELS[key]
    return POOR, BAND_LABELS[POOR]


def recompute_overall(total, invalid, missing_gps, valid, matched):
    """QualityAssessor.assess()'s own formula, reapplied to summed (session-level) inputs."""
    if not total:
        return None
    completeness = (total - invalid - missing_gps) / total * 100
    accuracy = (matched / valid * 100) if valid else 0.0
    return round(completeness * 0.5 + accuracy * 0.5, 2)


def filter_sessions(GET):
    """Server-side filters for the Data Quality workspace. Returns (queryset, filters, errors)."""
    from django.core.exceptions import ValidationError
    from django.db.models import Exists, OuterRef
    from ..models import DriveTestFile, DriveTestSession, RadioMeasurement

    f = {k: GET.get(k, '').strip() for k in
         ('q', 'operator', 'technology', 'session', 'date_from', 'date_to')}
    # ?status=WARNING is the documented filter name for this page's own quality banding
    # (GOOD/WARNING/POOR/pending) — kept apart from DriveTestSession.status (processing state).
    f['quality'] = GET.get('status', '').strip().lower()
    qs = DriveTestSession.objects.all()

    if f['q']:
        q = f['q']
        qs = qs.filter(
            Q(session_ref__icontains=q) | Q(title__icontains=q) | Q(operator__name__icontains=q)
            | Exists(DriveTestFile.objects.filter(session=OuterRef('pk'), original_filename__icontains=q))
        )
    if f['session']:
        qs = qs.filter(session_ref__icontains=f['session'])
    if f['operator']:
        qs = qs.filter(operator__code=f['operator'])
    if f['technology']:
        qs = qs.filter(Exists(RadioMeasurement.objects.filter(
            technology=f['technology'], measurement__drive_file__session=OuterRef('pk'))))
    errors = []
    try:
        if f['date_from']:
            qs = qs.filter(test_date__gte=f['date_from'])
        if f['date_to']:
            qs = qs.filter(test_date__lte=f['date_to'])
    except (ValueError, ValidationError):
        errors.append('Date must look like 2026-09-24.')
        qs = DriveTestSession.objects.none()
    if f['quality'] and f['quality'] not in (GOOD, WARNING, POOR, 'pending'):
        errors.append('Unrecognised quality status.')
        qs = DriveTestSession.objects.none()
    return qs, f, errors


def annotate_rollup(qs):
    """Attach session-level DataQualityResult roll-ups. One extra join; no per-measurement work."""
    return qs.select_related('operator').annotate(
        qr_files=Count('files__quality_result', distinct=True),
        qr_total_files=Count('files', distinct=True),
        qr_total=Sum('files__quality_result__total_records'),
        qr_valid=Sum('files__quality_result__valid_records'),
        qr_invalid=Sum('files__quality_result__invalid_records'),
        qr_missing_gps=Sum('files__quality_result__missing_gps'),
        qr_missing_cell=Sum('files__quality_result__missing_cell_id'),
        qr_matched=Sum('files__quality_result__matched_cells'),
        qr_unmatched=Sum('files__quality_result__unmatched_cells'),
    )


def build_rows(qs):
    """List of sessions (already annotate_rollup()'d) with derived score/band attached."""
    rows = list(qs)
    for s in rows:
        s.qr_has_result = bool(s.qr_files)
        s.qr_pending_files = max((s.qr_total_files or 0) - (s.qr_files or 0), 0)
        s.qr_is_processing = not s.qr_has_result and s.status in ('UPLOADING', 'PENDING', 'PROCESSING')
        s.qr_is_failed = not s.qr_has_result and s.status == 'FAILED'
        if s.qr_has_result:
            s.qr_score = recompute_overall(s.qr_total or 0, s.qr_invalid or 0, s.qr_missing_gps or 0,
                                           s.qr_valid or 0, s.qr_matched or 0)
        else:
            s.qr_score = None
        s.qr_band, s.qr_band_label = quality_band(s.qr_score)
    return rows


def filter_rows_by_quality(rows, quality):
    if not quality:
        return rows
    if quality == 'pending':
        return [s for s in rows if not s.qr_has_result]
    return [s for s in rows if s.qr_band == quality]


def workspace_summary(session_ids):
    """Real, backend-derived totals for the summary cards. '—'-worthy values return None."""
    from ..models import DataQualityResult, Measurement

    qr = DataQualityResult.objects.filter(drive_file__session_id__in=session_ids)
    qr_agg = qr.aggregate(
        total=Sum('total_records'), invalid=Sum('invalid_records'),
        missing_gps=Sum('missing_gps'), missing_cell=Sum('missing_cell_id'),
        unmatched=Sum('unmatched_cells'), n_files=Count('id'),
    )
    issue_count = sum(len(r) for r in qr.values_list('issues', flat=True) if isinstance(r, list))
    return {
        'sessions_assessed': qr.values('drive_file__session_id').distinct().count(),
        'measurements_assessed': qr_agg['total'],
        'quality_issues': issue_count,
        'invalid_records': qr_agg['invalid'],
        'unmatched_measurements': qr_agg['unmatched'],
    }


def measurement_quality_chart(session_ids):
    """Valid / Invalid / Without GPS / Unmatched counts across the filtered sessions, live."""
    from ..models import Measurement

    base = Measurement.objects.filter(drive_file__session_id__in=session_ids)
    agg = base.aggregate(
        valid=Count('id', filter=Q(is_valid=True)),
        invalid=Count('id', filter=Q(is_valid=False)),
        without_gps=Count('id', filter=Q(is_valid=True, latitude=0.0, longitude=0.0)),
        unmatched=Count('id', filter=Q(is_valid=True) & Q_UNMATCHED),
    )
    return agg


def category_breakdown(session_ids):
    """Measurement / Location / Radio / Cell Reference quality, computed live for the filtered
    sessions (not stored on DataQualityResult), using the same conventions as the
    Measurements / GIS / Cell Matching pages.
    """
    from ..models import Measurement, RadioMeasurement

    base = Measurement.objects.filter(drive_file__session_id__in=session_ids)
    valid = base.filter(is_valid=True)
    total = base.count()
    total_valid = valid.count()

    # Out-of-range coordinates that are not the (0, 0) "no fix" sentinel — a distinct,
    # genuinely invalid case, using the same range has_valid_coords() checks elsewhere
    # in this module. Computed in the database, not by loading rows into Python.
    _out_of_range = ~Q(latitude=0.0, longitude=0.0) & (
        ~Q(latitude__gte=-90, latitude__lte=90) | ~Q(longitude__gte=-180, longitude__lte=180))
    location = valid.aggregate(
        with_gps=Count('id', filter=~Q(latitude=0.0, longitude=0.0)),
        no_gps=Count('id', filter=Q(latitude=0.0, longitude=0.0)),
        invalid_coords=Count('id', filter=_out_of_range),
    )
    invalid_coords = location['invalid_coords']

    radio_present = valid.filter(radio__isnull=False).count()
    # (label, count) pairs, in a fixed order the template can render directly.
    signal_counts = [(label, valid.filter(**{field + '__isnull': False}).count())
                      for _key, label, field in RADIO_SIGNAL_FIELDS]
    id_counts = [(label, valid.filter(**{field + '__isnull': False}).count())
                 for _key, label, field in OBSERVED_ID_FIELDS]

    match = valid.aggregate(
        matched=Count('id', filter=Q_MATCHED),
        unmatched=Count('id', filter=Q_UNMATCHED),
        unknown=Count('id', filter=Q_UNKNOWN),
    )
    methods = list(valid.filter(Q_MATCHED).exclude(match_method='').values('match_method')
                   .annotate(n=Count('id')).order_by('-n'))

    missing_technology = valid.filter(Q(radio__isnull=True) | Q(radio__technology='')).count()

    return {
        'measurement': {
            'total': total, 'valid': total_valid, 'invalid': total - total_valid,
            'missing_gps': location['no_gps'], 'invalid_coords': invalid_coords,
            'missing_technology': missing_technology, 'missing_radio': total_valid - radio_present,
        },
        'location': {
            'with_gps': location['with_gps'], 'without_gps': location['no_gps'],
            'invalid_coords': invalid_coords,
        },
        'radio': {'signal': signal_counts, 'identifiers': id_counts, 'total_valid': total_valid},
        'reference': {
            'matched': match['matched'], 'unmatched': match['unmatched'], 'unknown': match['unknown'],
            'methods': methods,
        },
    }


# Issue rows are built directly from DataQualityResult's own stored integer fields — never
# by parsing its free-text `issues` strings. Category labels mirror the ones this workspace
# already uses for the live breakdown above.
_ISSUE_DEFS = (
    ('missing_gps', 'Missing GPS coordinates', 'Location Quality'),
    ('missing_cell_id', 'Missing cell identifiers', 'File / Parser Quality'),
    ('invalid_records', 'Invalid records', 'File / Parser Quality'),
    ('unmatched_cells', 'Unmatched measurements', 'Cell Reference Quality'),
)


def issue_rows(session_ids):
    """One row per non-zero issue type, summed across the filtered sessions' DataQualityResults."""
    from ..models import DataQualityResult

    qr = DataQualityResult.objects.filter(drive_file__session_id__in=session_ids)
    total_records = qr.aggregate(t=Sum('total_records'))['t'] or 0
    rows = []
    for field, label, category in _ISSUE_DEFS:
        agg = qr.aggregate(n=Sum(field), sessions=Count('drive_file__session_id', filter=Q(**{field + '__gt': 0}), distinct=True))
        count = agg['n'] or 0
        if count <= 0:
            continue
        rows.append({
            'issue': label, 'category': category, 'count': count,
            'pct': round(count / total_records * 100, 1) if total_records else None,
            'sessions': agg['sessions'],
        })
    rows.sort(key=lambda r: r['count'], reverse=True)
    return rows


def session_detail(session):
    """Full quality picture for one session — used by the drawer/detail endpoint."""
    files = list(session.files.select_related('quality_result', 'parser_profile').order_by('-uploaded_at'))
    session_ids = [session.pk]
    return {
        'files': files,
        'category': category_breakdown(session_ids),
        'issues': issue_rows(session_ids),
        'chart': measurement_quality_chart(session_ids),
    }
