"""
Reconciliation Service

Compares mediated data (TrafficSummary/RatedAggregate) vs operator declarations
and identifies variances.
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from regulatory.models.traffic import TrafficSummary
from regulatory.models.aggregates import RatedAggregate, RevenueSnapshot
from regulatory.models.declarations import OperatorDeclaration, DeclarationLineItem
from regulatory.models.reconciliation import (
    ReconciliationRun, ReconciliationResult, Discrepancy
)

logger = logging.getLogger(__name__)


class ReconciliationService:

    @staticmethod
    def variance(expected, declared):
        expected, declared = Decimal(expected or 0), Decimal(declared or 0)
        amount = expected - declared
        percent = (amount / expected * Decimal('100')) if expected else Decimal('0')
        return amount, percent

    @staticmethod
    def classify_variance(percent, tolerance=Decimal('1')):
        value = abs(Decimal(percent))
        if value <= tolerance:
            return ReconciliationResult.MatchStatus.MATCHED
        if value <= Decimal('5'):
            return ReconciliationResult.MatchStatus.MINOR_VARIANCE
        if value <= Decimal('15'):
            return ReconciliationResult.MatchStatus.MAJOR_VARIANCE
        return ReconciliationResult.MatchStatus.CRITICAL_VARIANCE

    def run_reconciliation(self, operator_code, level,
                           period_start, period_end,
                           declaration=None):
        run = ReconciliationRun(
            operator_code=operator_code,
            level=level,
            period_start=period_start,
            period_end=period_end,
            declaration=declaration,
            status=ReconciliationRun.Status.RUNNING,
        )
        run.save()
        run.reference = f'REC-{run.created_at.year}-{run.pk:05d}'
        run.save(update_fields=['reference'])

        try:
            mediated = self._get_mediated_data(operator_code, period_start, period_end)
            declared = self._get_declared_data(declaration) if declaration else None
            results = self._compare_data(mediated, declared, run)

            run.status = ReconciliationRun.Status.COMPLETED
            expected_revenue = sum((r.mediated_revenue for r in results), Decimal('0'))
            declared_revenue = sum((r.declared_revenue for r in results), Decimal('0'))
            expected_gst = sum((r.mediated_tax for r in results), Decimal('0'))
            declared_gst = sum((r.declared_tax for r in results), Decimal('0'))
            rev_var, rev_pct = self.variance(expected_revenue, declared_revenue)
            gst_var, gst_pct = self.variance(expected_gst, declared_gst)
            risk_level = self.classify_variance(max(abs(rev_pct), abs(gst_pct)))
            run.expected_revenue = expected_revenue
            run.declared_revenue = declared_revenue
            run.revenue_variance = rev_var
            run.expected_gst = expected_gst
            run.declared_gst = declared_gst
            run.gst_variance = gst_var
            run.risk_level = risk_level
            if declaration:
                run.expected_taxable_revenue = expected_revenue
                run.declared_taxable_revenue = declaration.declared_taxable_revenue
                run.taxable_revenue_variance = expected_revenue - declaration.declared_taxable_revenue
            run.summary = {
                'total_results': len(results),
                'matched': sum(1 for r in results if r.match_status == ReconciliationResult.MatchStatus.MATCHED),
                'minor': sum(1 for r in results if r.match_status == ReconciliationResult.MatchStatus.MINOR_VARIANCE),
                'major': sum(1 for r in results if r.match_status == ReconciliationResult.MatchStatus.MAJOR_VARIANCE),
                'critical': sum(1 for r in results if r.match_status == ReconciliationResult.MatchStatus.CRITICAL_VARIANCE),
                'no_decl': sum(1 for r in results if r.match_status == ReconciliationResult.MatchStatus.NO_DECLARATION),
            }
            run.completed_at = timezone.now()
            run.save()
            logger.info(f"Reconciliation {run.reference} completed for {operator_code}")
            return run

        except Exception as e:
            logger.exception(f"Reconciliation {run.reference} failed: {e}")
            run.status = ReconciliationRun.Status.FAILED
            run.failure_reason = str(e)[:255]
            run.save(update_fields=['status', 'failure_reason', 'updated_at'])
            raise

    def _get_mediated_data(self, operator_code, period_start, period_end):
        traffic = TrafficSummary.objects.filter(
            operator_code=operator_code,
            period_start__date__gte=period_start,
            period_start__date__lte=period_end,
        ).values('service_type').annotate(
            total_count=Sum('record_count'),
            total_duration=Sum('total_duration_seconds'),
            total_sms=Sum('sms_count'),
            total_data_up=Sum('data_volume_bytes_up'),
            total_data_down=Sum('data_volume_bytes_down'),
        )

        financials = RatedAggregate.objects.filter(
            traffic_summary__operator_code=operator_code,
            traffic_summary__period_start__date__gte=period_start,
            traffic_summary__period_start__date__lte=period_end,
        ).values('traffic_summary__service_type').annotate(
            rated=Sum('rated_amount'),
            tax=Sum('tax_amount'),
            total=Sum('total_amount'),
        )
        fin_by_svc = {
            row['traffic_summary__service_type']: row for row in financials
        }

        by_service = {}
        for item in traffic:
            svc = item['service_type']
            fin = fin_by_svc.get(svc, {})
            by_service[svc] = {
                'count': item['total_count'] or 0,
                'duration': item['total_duration'] or 0,
                'sms': item['total_sms'] or 0,
                'data_bytes': (item['total_data_up'] or 0) + (item['total_data_down'] or 0),
                'revenue': fin.get('rated', Decimal('0')) or Decimal('0'),
                'tax': fin.get('tax', Decimal('0')) or Decimal('0'),
            }
        return by_service

    def _get_declared_data(self, declaration):
        line_items = declaration.line_items.all()
        by_service = {}
        for item in line_items:
            by_service[item.service_type] = {
                'count': item.declared_traffic_count or 0,
                'revenue': item.declared_revenue or Decimal('0'),
                'tax': item.declared_tax or Decimal('0'),
            }
        return {
            'by_service': by_service,
            'total_revenue': declaration.total_declared_revenue,
            'total_tax': declaration.total_declared_tax,
            'taxable_revenue': declaration.declared_taxable_revenue,
        }

    def _compare_data(self, mediated, declared, run):
        results = []
        all_services = set(mediated.keys())
        if declared:
            all_services |= set(declared['by_service'].keys())

        for svc in sorted(all_services):
            med = mediated.get(svc, {})
            med_revenue = med.get('revenue', Decimal('0'))
            med_tax = med.get('tax', Decimal('0'))
            med_count = med.get('count', 0)

            if not declared:
                match_status = ReconciliationResult.MatchStatus.NO_DECLARATION
                decl_revenue = Decimal('0')
                decl_tax = Decimal('0')
                decl_count = 0
                var_revenue = med_revenue
                var_tax = med_tax
                var_pct = Decimal('100') if med_revenue else Decimal('0')
            else:
                decl = declared['by_service'].get(svc, {})
                decl_revenue = decl.get('revenue', Decimal('0'))
                decl_tax = decl.get('tax', Decimal('0'))
                decl_count = decl.get('count', 0)
                var_revenue, var_pct = self.variance(med_revenue, decl_revenue)
                var_tax, _ = self.variance(med_tax, decl_tax)
                match_status = self.classify_variance(var_pct)

            result = ReconciliationResult(
                run=run,
                operator_code=run.operator_code,
                service_type=svc,
                mediated_count=med_count,
                mediated_revenue=med_revenue,
                mediated_tax=med_tax,
                declared_count=decl_count,
                declared_revenue=decl_revenue,
                declared_tax=decl_tax,
                variance_revenue=abs(var_revenue),
                variance_tax=abs(var_tax),
                variance_pct=var_pct,
                match_status=match_status,
            )
            result.save()

            if match_status not in (
                ReconciliationResult.MatchStatus.MATCHED,
                ReconciliationResult.MatchStatus.NO_DECLARATION,
            ):
                Discrepancy.objects.create(
                    result=result,
                    severity=self._get_severity(match_status),
                    description=f"{svc} revenue variance of {abs(var_pct):.1f}% between mediated and declared",
                    amount_at_risk=abs(var_revenue),
                    resolution_status=Discrepancy.ResolutionStatus.OPEN,
                )

            results.append(result)

        if not all_services and declared:
            total_rev = declared.get('total_revenue', Decimal('0'))
            total_tax = declared.get('total_tax', Decimal('0'))
            result = ReconciliationResult.objects.create(
                run=run,
                operator_code=run.operator_code,
                service_type='TOTAL',
                mediated_count=0,
                mediated_revenue=Decimal('0'),
                mediated_tax=Decimal('0'),
                declared_count=0,
                declared_revenue=total_rev,
                declared_tax=total_tax,
                variance_revenue=total_rev,
                variance_tax=total_tax,
                variance_pct=Decimal('100'),
                match_status=ReconciliationResult.MatchStatus.CRITICAL_VARIANCE,
            )
            results.append(result)

        return results

    @staticmethod
    def _get_severity(match_status):
        mapping = {
            ReconciliationResult.MatchStatus.MINOR_VARIANCE: Discrepancy.Severity.LOW,
            ReconciliationResult.MatchStatus.MAJOR_VARIANCE: Discrepancy.Severity.MEDIUM,
            ReconciliationResult.MatchStatus.CRITICAL_VARIANCE: Discrepancy.Severity.HIGH,
        }
        return mapping.get(match_status, Discrepancy.Severity.LOW)


def run_reconciliation(operator_code, level, period_start, period_end, declaration=None):
    service = ReconciliationService()
    return service.run_reconciliation(operator_code, level, period_start, period_end, declaration)
