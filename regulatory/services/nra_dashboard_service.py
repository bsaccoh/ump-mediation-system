from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Sum

from reference.models import Operator
from regulatory.models import AuditCase, OperatorDeclaration, ReconciliationResult, ReconciliationRun, RevenueSnapshot, RiskAlert


class NRADashboardService:
    def __init__(self, operator=None, service_type=None, start_date=None, end_date=None):
        self.operator, self.service_type = operator or '', service_type or ''
        self.end_date = self._date(end_date) or date.today()
        self.start_date = self._date(start_date) or self.end_date - timedelta(days=30)

    @staticmethod
    def _date(value):
        try: return datetime.strptime(value, '%Y-%m-%d').date() if value else None
        except ValueError: return None

    def _snapshots(self, start=None, end=None, period='DAILY'):
        query = RevenueSnapshot.objects.filter(
            period_type=period,
            period_date__range=(start or self.start_date, end or self.end_date),
        )
        return query.filter(operator_code=self.operator) if self.operator else query

    def _declarations(self, start=None, end=None):
        query = OperatorDeclaration.objects.filter(
            period_start__range=(start or self.start_date, end or self.end_date),
            status__in=['ACCEPTED', 'RECONCILED', 'CLOSED'],
        )
        return query.filter(operator_code=self.operator) if self.operator else query

    @staticmethod
    def _sum(query, field):
        return query.aggregate(total=Sum(field))['total'] or Decimal('0')

    @staticmethod
    def _change(current, prior):
        return round(float((current - prior) * 100 / prior), 1) if prior else 0

    @staticmethod
    def _auto_scale(value, digits=2):
        """Pick the right unit for the new SLE currency based on actual value."""
        abs_val = abs(value) if value else Decimal('0')
        if abs_val >= Decimal('1000000000'):
            return {'value': f'{value / Decimal("1000000000"):.{digits}f}', 'unit': 'B'}
        if abs_val >= Decimal('1000000'):
            return {'value': f'{value / Decimal("1000000"):.{digits}f}', 'unit': 'M'}
        if abs_val >= Decimal('1000'):
            return {'value': f'{value / Decimal("1000"):.{digits}f}', 'unit': 'K'}
        return {'value': f'{value:.{digits}f}', 'unit': ''}

    def get_dashboard_data(self):
        return {
            'kpis': self.get_kpis(),
            'gst_trend': self.get_gst_trend(),
            'taxable_revenue': self.get_taxable_revenue(),
            'revenue_comparison': self.get_revenue_comparison(),
            'gst_variance': self.get_gst_variance(),
            'reconciliation': self.get_reconciliation(),
            'risk_indicators': self.get_risk_indicators(),
            'declarations': self.get_declarations(),
            'operator_summary': self.get_operator_summary(),
            'last_updated': datetime.now().strftime('%d %b %Y %H:%M'),
        }

    def get_kpis(self):
        previous_end = self.start_date - timedelta(days=1)
        previous_start = previous_end - timedelta(days=(self.end_date - self.start_date).days)

        expected = self._sum(self._snapshots(), 'expected_revenue')
        declared = self._sum(self._declarations(), 'declared_taxable_revenue')
        gst = self._sum(self._snapshots(), 'expected_tax')
        declared_gst = self._sum(self._declarations(), 'total_declared_tax')

        old_expected = self._sum(self._snapshots(previous_start, previous_end), 'expected_revenue')
        old_declared = self._sum(self._declarations(previous_start, previous_end), 'declared_taxable_revenue')

        alerts = RiskAlert.objects.filter(status='OPEN', alert_type__in=['TAX', 'GST'])
        cases = AuditCase.objects.filter(
            status__in=['OPEN', 'ASSIGNED', 'INVESTIGATION',
                        'AWAITING_OPERATOR', 'OPERATOR_RESPONSE', 'REGULATORY_REVIEW'],
        )

        exp_scaled = self._auto_scale(expected)
        decl_scaled = self._auto_scale(declared)
        gst_scaled = self._auto_scale(gst)
        decl_gst_scaled = self._auto_scale(declared_gst)
        gst_var_scaled = self._auto_scale(gst - declared_gst)
        exposure_scaled = self._auto_scale(self._sum(alerts, 'potential_exposure'))

        return {
            'expected_taxable_revenue': {
                'value': exp_scaled['value'], 'unit': exp_scaled['unit'],
                'currency': 'SLE', 'change': self._change(expected, old_expected),
            },
            'declared_taxable_revenue': {
                'value': decl_scaled['value'], 'unit': decl_scaled['unit'],
                'currency': 'SLE', 'change': self._change(declared, old_declared),
            },
            'expected_gst': {
                'value': gst_scaled['value'], 'unit': gst_scaled['unit'],
                'currency': 'SLE', 'change': 0,
            },
            'declared_gst': {
                'value': decl_gst_scaled['value'], 'unit': decl_gst_scaled['unit'],
                'currency': 'SLE', 'change': 0,
            },
            'gst_variance': {
                'value': gst_var_scaled['value'], 'unit': gst_var_scaled['unit'],
                'currency': 'SLE', 'change': self._change(gst - declared_gst, gst),
            },
            'tax_exposure': {
                'value': exposure_scaled['value'], 'unit': exposure_scaled['unit'],
                'currency': 'SLE', 'status': 'Live',
            },
            'open_audit_cases': {'value': cases.count(), 'change': 0},
            'high_risk_operators': {
                'value': RiskAlert.objects.filter(
                    status='OPEN', severity__in=['HIGH', 'CRITICAL'],
                ).values('operator_code').distinct().count(),
                'status': 'Live',
            },
        }

    def get_gst_trend(self):
        labels, expected, declared = [], [], []
        for offset in range(11, -1, -1):
            end = self.end_date.replace(day=1) - timedelta(days=offset * 1)
            start = end.replace(day=1)
            month_end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            labels.append(start.strftime('%b %Y'))
            monthly_tax = self._sum(self._snapshots(start, month_end, 'MONTHLY'), 'expected_tax')
            if not monthly_tax:
                monthly_tax = self._sum(self._snapshots(start, month_end, 'DAILY'), 'expected_tax')
            expected.append(float(monthly_tax))
            declared.append(float(self._sum(self._declarations(start, month_end), 'total_declared_tax')))
        return {
            'labels': labels,
            'expected': expected,
            'declared': declared,
            'variance': [a - b for a, b in zip(expected, declared)],
        }

    def get_taxable_revenue(self):
        rows = self._snapshots().values('operator_code').annotate(amount=Sum('expected_revenue'))
        total = sum((r['amount'] or 0 for r in rows), Decimal('0'))
        names = dict(Operator.objects.values_list('code', 'name'))
        result = []
        for r in rows:
            amt = r['amount'] or Decimal('0')
            scaled = self._auto_scale(amt)
            result.append({
                'operator': names.get(r['operator_code'], r['operator_code']),
                'amount': float(amt),
                'amount_display': f"{scaled['value']} {scaled['unit']}".strip(),
                'percentage': round(float(amt * 100 / total), 1) if total else 0,
            })
        return result

    def get_revenue_comparison(self):
        labels, expected, declared = [], [], []
        for offset in range(3, -1, -1):
            q_end = self.end_date.replace(day=1) - timedelta(days=offset * 90)
            q_start = q_end - timedelta(days=90)
            q_label = q_start.strftime('%b %Y')
            labels.append(q_label)
            q_rev = self._sum(self._snapshots(q_start, q_end, 'DAILY'), 'expected_revenue')
            expected.append(float(q_rev))
            q_decl = self._sum(self._declarations(q_start, q_end), 'declared_taxable_revenue')
            declared.append(float(q_decl))
        return {'labels': labels, 'expected': expected, 'declared': declared}

    def get_gst_variance(self):
        names = dict(Operator.objects.values_list('code', 'name'))
        expected = {
            r['operator_code']: r['amount'] or 0
            for r in self._snapshots().values('operator_code').annotate(amount=Sum('expected_tax'))
        }
        declared = {
            r['operator_code']: r['amount'] or 0
            for r in self._declarations().values('operator_code').annotate(amount=Sum('total_declared_tax'))
        }
        codes = sorted(set(expected) | set(declared))
        return {
            'labels': [names.get(c, c) for c in codes],
            'expected': [float(expected.get(c, 0)) for c in codes],
            'declared': [float(declared.get(c, 0)) for c in codes],
        }

    def get_reconciliation(self):
        results = ReconciliationResult.objects.filter(
            run__status='COMPLETED',
            run__period_start__range=(self.start_date, self.end_date),
        )
        counts = dict(results.values_list('match_status').annotate(value=Count('id')))
        return {
            key: {'value': counts.get(code, 0), 'change': 0}
            for key, code in [
                ('matched', 'MATCHED'), ('minor', 'MINOR'),
                ('major', 'MAJOR'), ('critical', 'CRITICAL'),
            ]
        }

    def get_risk_indicators(self):
        return [
            {
                'indicator': r['alert_type'] or 'Other',
                'count': r['count'],
                'trend': 0,
                'severity': r['severity'] or 'Low',
            }
            for r in RiskAlert.objects.filter(status='OPEN').values('alert_type', 'severity').annotate(count=Count('id'))
        ]

    def get_declarations(self):
        names = dict(Operator.objects.values_list('code', 'name'))
        return [
            {
                'operator': names.get(d.operator_code, d.operator_code),
                'period': d.period_start.strftime('%b %Y'),
                'type': d.get_declaration_type_display(),
                'status': d.get_status_display(),
                'submitted': d.submitted_at.strftime('%d %b %Y') if d.submitted_at else '—',
            }
            for d in OperatorDeclaration.objects.order_by('-submitted_at')[:10]
        ]

    def get_operator_summary(self):
        names = dict(Operator.objects.values_list('code', 'name'))
        rows = self._snapshots().values('operator_code').annotate(
            revenue=Sum('expected_revenue'), tax=Sum('expected_tax'),
        )
        decl_map = {}
        for d in self._declarations().values('operator_code').annotate(
            revenue=Sum('declared_taxable_revenue'), tax=Sum('total_declared_tax'),
        ):
            decl_map[d['operator_code']] = d

        result = []
        for row in rows:
            rev = row['revenue'] or Decimal('0')
            tax = row['tax'] or Decimal('0')
            decl = decl_map.get(row['operator_code'], {})
            decl_rev = decl.get('revenue', Decimal('0')) or Decimal('0')
            decl_tax = decl.get('tax', Decimal('0')) or Decimal('0')
            gst_var = tax - decl_tax
            rev_s = self._auto_scale(rev)
            tax_s = self._auto_scale(tax)
            decl_rev_s = self._auto_scale(decl_rev)
            decl_tax_s = self._auto_scale(decl_tax)
            gst_var_s = self._auto_scale(gst_var)
            risk = 'Low'
            if gst_var and tax:
                pct = abs(gst_var / tax * 100)
                if pct > 15:
                    risk = 'Critical'
                elif pct > 5:
                    risk = 'High'
                elif pct > 1:
                    risk = 'Medium'
            result.append({
                'operator': names.get(row['operator_code'], row['operator_code']),
                'expected_revenue': f"{rev_s['value']} {rev_s['unit']}".strip(),
                'declared_revenue': f"{decl_rev_s['value']} {decl_rev_s['unit']}".strip() if decl_rev else '—',
                'expected_gst': f"{tax_s['value']} {tax_s['unit']}".strip(),
                'declared_gst': f"{decl_tax_s['value']} {decl_tax_s['unit']}".strip() if decl_tax else '—',
                'gst_variance': f"{gst_var_s['value']} {gst_var_s['unit']}".strip(),
                'risk': risk,
            })
        return result
