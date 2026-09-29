"""
Technology Analysis & Operator Comparison.

Orchestrates DriveTestKpiService's own formulas across operators/technologies/regions,
using Measurement querysets that can span multiple sessions. No KPI math is duplicated
here: _signal_kpis() / _voice_kpis() / _mobility_kpis() are the exact same (class/static)
methods DriveTestKpiService.compute() itself uses — just called on a differently scoped
queryset instead of "this one session".

This module is read-only aggregation. It never ranks, scores or judges an operator or
technology; it returns the same measured numbers, presented per group, in a neutral
(alphabetical / technology-generation) order. No regulatory pass/fail is computed here.
"""
from __future__ import annotations

from django.db.models import Count, Q

from .kpi import DriveTestKpiService as KPI

ORDER_TECH = {'2G': 0, '3G': 1, '4G': 2, '5G': 3}

METRICS = (
    ('measurements', 'Measurements'),
    ('coverage', 'Coverage'),
    ('cssr', 'CSSR'),
    ('dcr', 'DCR'),
    ('mos', 'MOS'),
    ('speed', 'Average Speed'),
)
METRIC_LABELS = dict(METRICS)


def _tech_sort(techs):
    return sorted(techs, key=lambda t: ORDER_TECH.get(t, 99))


def filter_measurements(GET):
    """Server-side filters shared by the whole comparison workspace.

    Returns (Measurement queryset, filters dict, errors list). Region/District only ever
    come from Measurement -> matched_cell -> sector -> site -> chiefdom -> district ->
    region; an unmatched measurement is never assigned a region or district.
    """
    from django.core.exceptions import ValidationError

    from ..models import DriveTestSession, Measurement

    f = {k: GET.get(k, '').strip() for k in
         ('operator', 'technology', 'session', 'date_from', 'date_to', 'region', 'district')}
    errors = []

    sessions = DriveTestSession.objects.all()
    if f['operator']:
        sessions = sessions.filter(operator__code=f['operator'])
    if f['session']:
        sessions = sessions.filter(session_ref__icontains=f['session'])
    try:
        if f['date_from']:
            sessions = sessions.filter(test_date__gte=f['date_from'])
        if f['date_to']:
            sessions = sessions.filter(test_date__lte=f['date_to'])
    except (ValueError, ValidationError):
        errors.append('Date must look like 2026-09-24.')
        sessions = DriveTestSession.objects.none()

    meas = Measurement.objects.filter(is_valid=True, drive_file__session__in=sessions)
    if f['technology']:
        meas = meas.filter(radio__technology=f['technology'])
    for key, path in (('region', 'matched_cell__sector__site__chiefdom__district__region_id'),
                      ('district', 'matched_cell__sector__site__chiefdom__district_id')):
        if f[key]:
            if f[key].isdigit():
                meas = meas.filter(**{path: int(f[key])})
            else:
                errors.append('Unrecognised %s.' % key)
                meas = meas.none()

    return meas, f, errors


def scope_summary(meas_qs):
    """Analysis Scope card — real counts only, over the currently filtered measurements."""
    from django.db.models import Max, Min

    agg = meas_qs.aggregate(
        measurements=Count('id'),
        sessions=Count('drive_file__session_id', distinct=True),
        operators=Count('drive_file__session__operator_id', distinct=True),
        first_date=Min('drive_file__session__test_date'),
        last_date=Max('drive_file__session__test_date'),
    )
    agg['technologies'] = (
        meas_qs.exclude(radio__technology='').values('radio__technology').distinct().count()
    )
    return agg


def operator_summary(meas_qs):
    """One row per operator actually present in the filtered measurements."""
    rows = (
        meas_qs.values('drive_file__session__operator_id', 'drive_file__session__operator__name',
                       'drive_file__session__operator__code')
        .annotate(
            measurements=Count('id', distinct=True),
            sessions=Count('drive_file__session_id', distinct=True),
            technologies=Count('radio__technology', distinct=True, filter=~Q(radio__technology='')),
            calls=Count('services', filter=Q(services__service_type='VOICE'), distinct=True),
        )
        .order_by('drive_file__session__operator__name')
    )
    return [{
        'operator_id': r['drive_file__session__operator_id'],
        'name': r['drive_file__session__operator__name'],
        'code': r['drive_file__session__operator__code'],
        'measurements': r['measurements'], 'sessions': r['sessions'],
        'technologies': r['technologies'], 'calls': r['calls'],
    } for r in rows]


def technology_summary(meas_qs):
    """Measurement counts per technology actually observed — never a fabricated 0 row."""
    rows = (
        meas_qs.exclude(radio__technology='')
        .values('radio__technology').annotate(measurements=Count('id')).order_by()
    )
    by_tech = {r['radio__technology']: r['measurements'] for r in rows}
    return [{'technology': t, 'measurements': by_tech[t]} for t in _tech_sort(by_tech.keys())]


def operator_kpis(meas_qs):
    """Side-by-side operator KPI table — alphabetical order, never sorted by KPI value."""
    from reference.models import Operator

    op_ids = meas_qs.values_list('drive_file__session__operator_id', flat=True).distinct()
    operators = Operator.objects.filter(pk__in=op_ids).order_by('name')
    rows = []
    for op in operators:
        op_meas = meas_qs.filter(drive_file__session__operator_id=op.pk)
        total = op_meas.count()
        rows.append({
            'operator': op, 'measurements': total,
            'signal': KPI._signal_kpis(op_meas, total),
            'voice': KPI._voice_kpis(op_meas),
            'mobility': KPI._mobility_kpis(op_meas),
        })
    return rows


def overall_kpis(meas_qs):
    """The whole scope combined — no operator/technology split. Same KPI helpers as
    everywhere else in this module; used by the report generator's Executive Summary
    so it never has to recompute a single number itself."""
    total = meas_qs.count()
    return {
        'measurements': total,
        'signal': KPI._signal_kpis(meas_qs, total),
        'voice': KPI._voice_kpis(meas_qs),
        'mobility': KPI._mobility_kpis(meas_qs),
        'network': KPI._network_kpis(meas_qs),
    }


def technology_kpis(meas_qs):
    """Side-by-side technology KPI table — 2G/3G/4G/5G order, never sorted by KPI value."""
    techs = _tech_sort(set(meas_qs.exclude(radio__technology='')
                           .values_list('radio__technology', flat=True).distinct()))
    rows = []
    for tech in techs:
        tech_meas = meas_qs.filter(radio__technology=tech)
        total = tech_meas.count()
        rows.append({
            'technology': tech, 'measurements': total,
            'signal': KPI._signal_kpis(tech_meas, total),
            'voice': KPI._voice_kpis(tech_meas),
            'mobility': KPI._mobility_kpis(tech_meas),
        })
    return rows


def _metric_value(meas_qs, total, metric):
    if metric == 'measurements':
        return total
    if metric == 'coverage':
        return KPI._signal_kpis(meas_qs, total)['coverage']['coverage_percent']
    if metric == 'cssr':
        return KPI._voice_kpis(meas_qs)['cssr_percent']
    if metric == 'dcr':
        return KPI._voice_kpis(meas_qs)['dcr_percent']
    if metric == 'mos':
        return KPI._voice_kpis(meas_qs)['mean_mos']
    if metric == 'speed':
        return KPI._mobility_kpis(meas_qs)['mean_speed_kmh']
    return None


def extract_metric(row, metric):
    """Read one already-computed metric out of an operator_kpis()/technology_kpis() row
    (trivial dict field access for charting — the value itself was computed once, above,
    by _signal_kpis()/_voice_kpis()/_mobility_kpis())."""
    if metric == 'measurements':
        return row['measurements']
    if metric == 'coverage':
        return row['signal']['coverage']['coverage_percent']
    if metric == 'cssr':
        return row['voice']['cssr_percent']
    if metric == 'dcr':
        return row['voice']['dcr_percent']
    if metric == 'mos':
        return row['voice']['mean_mos']
    if metric == 'speed':
        return row['mobility']['mean_speed_kmh']
    return None


def operator_technology_matrix(meas_qs, metric='measurements'):
    """Operator x Technology grid for one selected metric.

    A cell is None (not 0) when that operator has no measurements at all for that
    technology — there is no drive-test data for the combination, which is not the
    same as a measured zero.
    """
    from reference.models import Operator

    if metric not in METRIC_LABELS:
        metric = 'measurements'

    techs = _tech_sort(set(meas_qs.exclude(radio__technology='')
                           .values_list('radio__technology', flat=True).distinct()))
    op_ids = meas_qs.values_list('drive_file__session__operator_id', flat=True).distinct()
    operators = list(Operator.objects.filter(pk__in=op_ids).order_by('name'))

    rows = []
    for op in operators:
        op_meas = meas_qs.filter(drive_file__session__operator_id=op.pk)
        cells = []
        for tech in techs:
            cell_meas = op_meas.filter(radio__technology=tech)
            total = cell_meas.count()
            cells.append({
                'technology': tech, 'measurements': total,
                'value': _metric_value(cell_meas, total, metric) if total else None,
            })
        rows.append({'operator': op, 'cells': cells})
    return {'technologies': techs, 'metric': metric, 'rows': rows}


def geographic_breakdown(meas_qs):
    """Region-level breakdown via the matched cell's site only. Unmatched measurements
    are excluded — a region is never guessed for them."""
    from ..models import Region

    matched = meas_qs.filter(matched_cell__isnull=False,
                             matched_cell__sector__site__chiefdom__isnull=False)
    region_ids = matched.values_list(
        'matched_cell__sector__site__chiefdom__district__region_id', flat=True).distinct()
    regions = Region.objects.filter(pk__in=list(region_ids)).order_by('name')

    rows = []
    for region in regions:
        region_meas = matched.filter(
            matched_cell__sector__site__chiefdom__district__region_id=region.pk)
        total = region_meas.count()
        rows.append({
            'region': region, 'measurements': total,
            'signal': KPI._signal_kpis(region_meas, total),
        })
    return rows


def data_quality_context(meas_qs):
    """Compact, real warning drawn from the existing Data Quality roll-up — never a
    second quality score."""
    from . import data_quality as dq

    session_ids = list(meas_qs.values_list('drive_file__session_id', flat=True).distinct())
    if not session_ids:
        return None
    summary = dq.workspace_summary(session_ids)
    if not summary['sessions_assessed']:
        return None
    return summary
