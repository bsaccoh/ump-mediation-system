"""
Drive Test KPI Service.

Computes analytical KPIs from Measurement / RadioMeasurement /
ServiceMeasurement rows for one DriveTestSession.

Rules:
- NULL is never silently converted to 0.
- Every query is scoped to the given session (no cross-session leakage).
- No new models; no migrations; no mock/demo values.
- This is an analytical layer — no regulatory pass/fail decisions here.

The per-metric helpers (_measurement_summary/_signal_kpis/_voice_kpis/_mobility_kpis/
_network_kpis) are class/static methods precisely so other read-only aggregation layers
(services.comparison, for Technology Analysis & Operator Comparison) can call the exact
same formulas against a differently scoped Measurement queryset — e.g. one operator or
one technology across many sessions — without duplicating any KPI math. compute() itself
is unchanged and remains session-scoped.
"""
from __future__ import annotations

import math
from typing import Any

from django.db.models import Avg, Count, FloatField, Max, Min, Q
from django.db.models.functions import Coalesce


def _safe_div(numerator, denominator, *, pct=False):
    """Return numerator/denominator, or None if denominator is 0/None."""
    if not denominator:
        return None
    result = numerator / denominator
    return result * 100 if pct else result


def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two (lat, lon) points in km."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class DriveTestKpiService:
    """
    Compute KPIs for a single DriveTestSession.

    Usage::

        svc = DriveTestKpiService(session)
        data = svc.compute()
    """

    # Application-level RSSI classification thresholds (not regulatory standards).
    # These are display-only categories used to bucket signal quality for analysis.
    RSSI_GOOD      =  -85.0   # >= this
    RSSI_FAIR      =  -95.0   # >= this (and < GOOD)
    RSSI_POOR      = -105.0   # >= this (and < FAIR)
    # < POOR → Very Poor

    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def compute(self) -> dict[str, Any]:
        from drive_test.models import Measurement, RadioMeasurement, ServiceMeasurement

        # All measurements for this session (across all files)
        meas_qs = Measurement.objects.filter(
            drive_file__session=self.session, is_valid=True
        )
        total = meas_qs.count()

        return {
            'session': self._session_info(),
            'measurements': self._measurement_summary(meas_qs, total),
            'signal': self._signal_kpis(meas_qs, total),
            'voice': self._voice_kpis(meas_qs),
            'mobility': self._mobility_kpis(meas_qs),
            'network': self._network_kpis(meas_qs),
        }

    # ------------------------------------------------------------------
    # Section helpers
    # ------------------------------------------------------------------

    def _session_info(self) -> dict:
        s = self.session
        return {
            'id': s.pk,
            'reference': s.session_ref,
            'title': s.title or '',
            'operator': s.operator.name if s.operator_id else '',
            'test_date': str(s.test_date),
            'test_type': s.get_test_type_display(),
            'status': s.status,
        }

    @staticmethod
    def _measurement_summary(meas_qs, total: int) -> dict:
        with_rssi = meas_qs.filter(radio__rssi__isnull=False).count()
        with_gps = meas_qs.exclude(
            Q(latitude=0.0) & Q(longitude=0.0)
        ).count()
        return {
            'total': total,
            'with_rssi': with_rssi,
            'with_gps': with_gps,
        }

    @classmethod
    def _signal_kpis(cls, meas_qs, total: int) -> dict:
        # Aggregate RSSI from RadioMeasurement (joined via radio OneToOne)
        radio_agg = meas_qs.filter(radio__rssi__isnull=False).aggregate(
            mean_rssi=Avg('radio__rssi'),
            min_rssi=Min('radio__rssi'),
            max_rssi=Max('radio__rssi'),
        )

        rssi_values_qs = meas_qs.filter(radio__rssi__isnull=False)
        rssi_count = rssi_values_qs.count()

        # Distribution buckets (application-level classification)
        good_count      = rssi_values_qs.filter(radio__rssi__gte=cls.RSSI_GOOD).count()
        fair_count      = rssi_values_qs.filter(
            radio__rssi__gte=cls.RSSI_FAIR, radio__rssi__lt=cls.RSSI_GOOD
        ).count()
        poor_count      = rssi_values_qs.filter(
            radio__rssi__gte=cls.RSSI_POOR, radio__rssi__lt=cls.RSSI_FAIR
        ).count()
        very_poor_count = rssi_values_qs.filter(radio__rssi__lt=cls.RSSI_POOR).count()
        no_signal_count = meas_qs.filter(radio__rssi__isnull=True).count() if total else 0

        def _pct(n):
            return round(n / total * 100, 1) if total else None

        rssi_distribution = {
            'good':      {'count': good_count,      'percentage': _pct(good_count)},
            'fair':      {'count': fair_count,       'percentage': _pct(fair_count)},
            'poor':      {'count': poor_count,       'percentage': _pct(poor_count)},
            'very_poor': {'count': very_poor_count,  'percentage': _pct(very_poor_count)},
            'no_signal': {'count': no_signal_count,  'percentage': _pct(no_signal_count)},
        }

        # Coverage: measurements with RSSI >= RSSI_FAIR (not a regulatory threshold)
        covered = good_count + fair_count
        coverage_pct = round(covered / total * 100, 1) if total else None

        return {
            'mean_rssi': round(radio_agg['mean_rssi'], 1) if radio_agg['mean_rssi'] is not None else None,
            'min_rssi':  radio_agg['min_rssi'],
            'max_rssi':  radio_agg['max_rssi'],
            'rssi_sample_count': rssi_count,
            'coverage': {
                'coverage_percent':  coverage_pct,
                'valid_samples':     total,
                'covered_samples':   covered,
            },
            'rssi_distribution': rssi_distribution,
        }

    @staticmethod
    def _voice_kpis(meas_qs) -> dict:
        from drive_test.models import ServiceMeasurement

        voice_qs = ServiceMeasurement.objects.filter(
            measurement__in=meas_qs,
            service_type=ServiceMeasurement.ServiceType.VOICE,
        )

        total_calls = voice_qs.count()
        if total_calls == 0:
            return {
                'total_calls': 0,
                'attempted': 0,
                'connected': 0,
                'dropped': 0,
                'cssr_percent': None,
                'dcr_percent': None,
                'mean_call_duration_s': None,
                'mean_mos': None,
                'mos_sample_count': 0,
            }

        connected = voice_qs.filter(
            outcome__in=[ServiceMeasurement.Outcome.SUCCESS]
        ).count()
        dropped   = voice_qs.filter(outcome=ServiceMeasurement.Outcome.DROPPED).count()
        attempted = total_calls  # every service record is an attempt

        cssr = _safe_div(connected, attempted, pct=True)
        dcr  = _safe_div(dropped, connected, pct=True)

        dur_agg = voice_qs.filter(
            call_duration_s__isnull=False
        ).aggregate(mean_dur=Avg('call_duration_s'))

        mos_qs = voice_qs.filter(mos__isnull=False)
        mos_count = mos_qs.count()
        mos_agg = mos_qs.aggregate(mean_mos=Avg('mos')) if mos_count else {'mean_mos': None}

        return {
            'total_calls': total_calls,
            'attempted': attempted,
            'connected': connected,
            'dropped': dropped,
            'cssr_percent': round(cssr, 1) if cssr is not None else None,
            'dcr_percent':  round(dcr, 1)  if dcr  is not None else None,
            'mean_call_duration_s': round(dur_agg['mean_dur'], 1) if dur_agg['mean_dur'] is not None else None,
            'mean_mos': round(mos_agg['mean_mos'], 2) if mos_agg['mean_mos'] is not None else None,
            'mos_sample_count': mos_count,
        }

    @staticmethod
    def _mobility_kpis(meas_qs) -> dict:
        # Speed from Measurement.speed_kmh (GPS/vehicle speed, not throughput)
        speed_qs = meas_qs.filter(speed_kmh__isnull=False)
        speed_agg = speed_qs.aggregate(
            mean_speed=Avg('speed_kmh'),
            max_speed=Max('speed_kmh'),
        )

        # Haversine distance from ordered GPS fixes
        gps_points = list(
            meas_qs.exclude(Q(latitude=0.0) & Q(longitude=0.0))
            .order_by('drive_file', 'sequence_num')
            .values_list('latitude', 'longitude')
        )
        total_km = 0.0
        for i in range(1, len(gps_points)):
            la1, lo1 = gps_points[i - 1]
            la2, lo2 = gps_points[i]
            total_km += _haversine_km(la1, lo1, la2, lo2)

        return {
            'mean_speed_kmh': round(speed_agg['mean_speed'], 1) if speed_agg['mean_speed'] is not None else None,
            'max_speed_kmh':  round(speed_agg['max_speed'],  1) if speed_agg['max_speed']  is not None else None,
            'total_distance_km': round(total_km, 2) if gps_points else None,
        }

    @staticmethod
    def _network_kpis(meas_qs) -> dict:
        # Unique cells — matched B-tier first, fall back to observed CI
        matched_cells = (
            meas_qs.filter(matched_cell__isnull=False)
            .values('matched_cell_id')
            .distinct()
            .count()
        )
        observed_cells = (
            meas_qs.filter(matched_cell__isnull=True, obs_ci__isnull=False)
            .values('obs_ci')
            .distinct()
            .count()
        )
        unique_cells = matched_cells + observed_cells

        # ARFCN distribution (from Measurement.obs_earfcn)
        arfcn_rows = (
            meas_qs.filter(obs_earfcn__isnull=False)
            .values('obs_earfcn')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        arfcn_distribution = {str(r['obs_earfcn']): r['count'] for r in arfcn_rows}

        # Technology breakdown (from RadioMeasurement.technology)
        tech_rows = (
            meas_qs.filter(radio__technology__isnull=False)
            .exclude(radio__technology='')
            .values('radio__technology')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        technology_breakdown = {r['radio__technology']: r['count'] for r in tech_rows}

        return {
            'unique_cells': unique_cells,
            'arfcn_distribution': arfcn_distribution,
            'technology_breakdown': technology_breakdown,
        }

    # ------------------------------------------------------------------
    # Grouped views — same formulas as compute(), evaluated per technology
    # or per cell instead of once for the whole session. Not a second
    # engine: _signal_kpis()/_voice_kpis() are the exact same methods
    # compute() itself uses, just called with a narrower queryset.
    # ------------------------------------------------------------------

    def compute_by_technology(self) -> list[dict]:
        """One row per technology actually observed in this session's valid measurements."""
        from drive_test.models import Measurement, RadioMeasurement

        session_meas = Measurement.objects.filter(drive_file__session=self.session, is_valid=True)
        techs = list(
            RadioMeasurement.objects.filter(measurement__in=session_meas)
            .exclude(technology='').values_list('technology', flat=True).distinct()
        )
        order = {'2G': 0, '3G': 1, '4G': 2, '5G': 3}
        techs.sort(key=lambda t: order.get(t, 99))

        rows = []
        for tech in techs:
            meas_qs = session_meas.filter(radio__technology=tech)
            total = meas_qs.count()
            rows.append({
                'technology': tech,
                'measurements': total,
                'signal': self._signal_kpis(meas_qs, total),
                'voice': self._voice_kpis(meas_qs),
            })
        return rows

    def compute_by_cell(self) -> list[dict]:
        """One row per matched reference cell with measurements in this session, plus a single
        'unmatched' roll-up row for measurements the matcher could not associate with a cell.
        Unmatched measurements are never presented as a cell."""
        from drive_test.models import Measurement

        session_meas = Measurement.objects.filter(drive_file__session=self.session, is_valid=True)

        cell_rows = (
            session_meas.filter(matched_cell__isnull=False)
            .values('matched_cell_id', 'matched_cell__cell_id', 'matched_cell__technology',
                    'matched_cell__sector__site_id', 'matched_cell__sector__site__name')
            .annotate(measurements=Count('id'), mean_rssi=Avg('radio__rssi'))
            .order_by('-measurements')
        )

        rows = []
        for r in cell_rows:
            cell_meas = session_meas.filter(matched_cell_id=r['matched_cell_id'])
            rows.append({
                'cell_id': r['matched_cell_id'],
                'cell_code': r['matched_cell__cell_id'],
                'technology': r['matched_cell__technology'],
                'site_id': r['matched_cell__sector__site_id'],
                'site_name': r['matched_cell__sector__site__name'],
                'measurements': r['measurements'],
                'mean_rssi': round(r['mean_rssi'], 1) if r['mean_rssi'] is not None else None,
                'voice': self._voice_kpis(cell_meas),
            })

        unmatched_qs = session_meas.filter(matched_cell__isnull=True)
        unmatched_count = unmatched_qs.count()
        if unmatched_count:
            rssi_agg = unmatched_qs.aggregate(mean_rssi=Avg('radio__rssi'))
            rows.append({
                'cell_id': None, 'cell_code': None, 'technology': '', 'site_id': None, 'site_name': '',
                'measurements': unmatched_count,
                'mean_rssi': round(rssi_agg['mean_rssi'], 1) if rssi_agg['mean_rssi'] is not None else None,
                'voice': self._voice_kpis(unmatched_qs),
            })
        return rows
