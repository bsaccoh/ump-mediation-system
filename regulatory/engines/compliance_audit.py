from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta
from django.db.models import Avg, Count, Q
from ..models import NetworkKPIDefinition, NetworkKPIEntry, NetworkCellSite


def check_kpi_compliance(kpi_def: NetworkKPIDefinition, value: Decimal) -> bool:
    """Check if a measured KPI value complies with the NatCA regulatory threshold."""
    if value is None or kpi_def is None:
        return True
    try:
        val = Decimal(str(value))
        thresh = Decimal(str(kpi_def.natca_threshold))
        if kpi_def.threshold_direction == NetworkKPIDefinition.Direction.ABOVE:
            return val >= thresh
        else:
            return val <= thresh
    except (InvalidOperation, ValueError, TypeError):
        return True


def run_compliance_audit(start_date=None, end_date=None, operator_code='', region='', district='') -> dict:
    """
    Run comprehensive NatCA Regulatory Compliance Audit & Penalty Assessment across operators.
    
    Returns:
      - operator_summaries: list of per-operator compliance rates, breach counts, and estimated penalties
      - kpi_violations: list of detailed SLA breach events with duration and penalty calculation
      - district_rankings: list of Sierra Leone districts ranked by network quality & SLA compliance
      - audit_metadata: summary stats, audit stamp, date range, total penalties assessed
    """
    kpi_defs = {k.code: k for k in NetworkKPIDefinition.objects.filter(is_active=True)}
    qs = NetworkKPIEntry.objects.select_related('kpi').all()

    if start_date:
        qs = qs.filter(period_date__gte=start_date)
    if end_date:
        qs = qs.filter(period_date__lte=end_date)
    if operator_code:
        qs = qs.filter(operator_code=operator_code)
    if region:
        qs = qs.filter(region__icontains=region)
    if district:
        qs = qs.filter(district__icontains=district)

    operators = ['orange', 'africell', 'qcell', 'sierratel', 'onemobile']
    if operator_code and operator_code in operators:
        operators = [operator_code]

    operator_summaries = []
    kpi_violations = []
    district_scores = {}

    # Fine Schedule: $500 per daily KPI breach, $50 per hourly breach
    FINE_PER_DAILY_BREACH = Decimal('500.00')
    FINE_PER_HOURLY_BREACH = Decimal('50.00')

    total_system_entries = 0
    total_system_breaches = 0
    total_system_penalties = Decimal('0.00')

    for op in operators:
        op_qs = qs.filter(operator_code=op)
        if not op_qs.exists():
            operator_summaries.append({
                'operator_code': op,
                'total_measurements': 0,
                'compliant_measurements': 0,
                'breach_count': 0,
                'compliance_score': 'N/A',
                'total_penalty_fee': '0.00',
                'status': 'NO_DATA',
            })
            continue

        op_total = op_qs.count()
        op_breaches = 0
        op_penalties = Decimal('0.00')
        op_kpi_breakdown = {}

        for entry in op_qs:
            total_system_entries += 1
            k_def = entry.kpi
            is_comp = check_kpi_compliance(k_def, entry.value)

            if not is_comp:
                op_breaches += 1
                total_system_breaches += 1
                penalty = FINE_PER_DAILY_BREACH if entry.granularity == 'DAILY' else FINE_PER_HOURLY_BREACH
                op_penalties += penalty
                total_system_penalties += penalty

                kpi_violations.append({
                    'id': entry.pk,
                    'operator_code': op,
                    'kpi_code': k_def.code,
                    'kpi_name': k_def.name,
                    'period_date': entry.period_date.isoformat(),
                    'region': entry.region or 'NATIONAL',
                    'district': entry.district or 'National',
                    'cell_id': entry.cell_id or 'ALL_CELLS',
                    'measured_value': str(entry.value),
                    'target_threshold': f"{'>=' if k_def.threshold_direction == 'ABOVE' else '<='} {k_def.natca_threshold} {k_def.unit}",
                    'penalty_fee': str(penalty),
                    'severity': 'CRITICAL' if penalty >= Decimal('500.00') else 'MAJOR',
                })

            # Accumulate district quality stats
            d_name = entry.district or entry.region or 'Western Area Urban'
            if d_name not in district_scores:
                district_scores[d_name] = {'total': 0, 'pass': 0, 'region': entry.region or 'Western Area'}
            district_scores[d_name]['total'] += 1
            if is_comp:
                district_scores[d_name]['pass'] += 1

        op_compliance_rate = (Decimal(op_total - op_breaches) / Decimal(op_total)) * Decimal('100.00') if op_total else Decimal('100.00')

        operator_summaries.append({
            'operator_code': op,
            'total_measurements': op_total,
            'compliant_measurements': op_total - op_breaches,
            'breach_count': op_breaches,
            'compliance_score': str(op_compliance_rate.quantize(Decimal('0.01'))),
            'total_penalty_fee': str(op_penalties.quantize(Decimal('0.00'))),
            'status': 'PASS' if op_compliance_rate >= Decimal('90.00') else 'AUDIT_WARNING' if op_compliance_rate >= Decimal('80.00') else 'PENALIZED',
        })

    # Rank Sierra Leone Districts by Compliance Rate
    district_rankings = []
    for d_name, stats in district_scores.items():
        rate = (Decimal(stats['pass']) / Decimal(stats['total'])) * Decimal('100.00') if stats['total'] else Decimal('100.00')
        district_rankings.append({
            'district': d_name,
            'region': stats['region'],
            'total_audited': stats['total'],
            'pass_count': stats['pass'],
            'compliance_rate': str(rate.quantize(Decimal('0.01'))),
            'quality_grade': 'EXCELLENT' if rate >= Decimal('95.00') else 'GOOD' if rate >= Decimal('90.00') else 'NEEDS_IMPROVEMENT',
        })

    district_rankings.sort(key=lambda x: Decimal(x['compliance_rate']), reverse=True)

    audit_metadata = {
        'audit_stamp': f"NATCA-AUDIT-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_system_entries': total_system_entries,
        'total_system_breaches': total_system_breaches,
        'total_penalties_assessed': str(total_system_penalties.quantize(Decimal('0.00'))),
        'start_date': start_date.isoformat() if start_date else None,
        'end_date': end_date.isoformat() if end_date else None,
    }

    return {
        'operator_summaries': operator_summaries,
        'kpi_violations': kpi_violations[:100], # Top 100 violations
        'district_rankings': district_rankings,
        'audit_metadata': audit_metadata,
    }
