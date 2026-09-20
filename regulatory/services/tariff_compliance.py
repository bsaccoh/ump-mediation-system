from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Q

from regulatory.models import Tariff, TariffComplianceResult


class TariffComplianceService:
    """Compares operator tariffs with approved NatCA reference rates."""

    @staticmethod
    def tolerance_percent():
        try:
            return Decimal(str(getattr(settings, 'TARIFF_COMPLIANCE_TOLERANCE_PCT', '1.00')))
        except (InvalidOperation, TypeError):
            return Decimal('1.00')

    @classmethod
    def classify_variance(cls, variance_percent, has_reference):
        if not has_reference or variance_percent is None:
            return TariffComplianceResult.Status.UNDER_REVIEW
        if abs(variance_percent) <= cls.tolerance_percent():
            return TariffComplianceResult.Status.COMPLIANT
        return TariffComplianceResult.Status.NON_COMPLIANT

    @classmethod
    def _reference_tariff(cls, applied_tariff, effective_date):
        return Tariff.objects.filter(
            is_regulatory_reference=True,
            operator_code=applied_tariff.operator_code,
            service_type=applied_tariff.service_type,
            traffic_type=applied_tariff.traffic_type,
            subscriber_type=applied_tariff.subscriber_type,
            destination=applied_tariff.destination,
            status__in=[Tariff.Status.APPROVED, Tariff.Status.ACTIVE],
            effective_from__lte=effective_date,
        ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=effective_date)).order_by('-version').first()

    @classmethod
    def run_check(cls, *, operator_code='', service_type='', effective_date=None, scope='ALL_ACTIVE', user=None):
        effective_date = effective_date or date.today()
        applied_tariffs = Tariff.objects.filter(status=Tariff.Status.ACTIVE, is_regulatory_reference=False)
        if operator_code:
            applied_tariffs = applied_tariffs.filter(operator_code=operator_code)
        if service_type:
            applied_tariffs = applied_tariffs.filter(service_type=service_type)

        summary = {'total': 0, 'compliant': 0, 'non_compliant': 0, 'under_review': 0}
        for applied_tariff in applied_tariffs:
            reference_tariff = cls._reference_tariff(applied_tariff, effective_date)
            approved_rate = reference_tariff.rate if reference_tariff else None
            variance = applied_tariff.rate - approved_rate if approved_rate is not None else None
            variance_percent = None
            if approved_rate not in (None, Decimal('0')):
                variance_percent = (variance / approved_rate) * Decimal('100')
            status = cls.classify_variance(variance_percent, reference_tariff is not None)
            result, _ = TariffComplianceResult.objects.update_or_create(
                applied_tariff=applied_tariff,
                effective_date=effective_date,
                defaults={
                    'approved_tariff': reference_tariff,
                    'operator_code': applied_tariff.operator_code,
                    'service_type': applied_tariff.service_type,
                    'traffic_type': applied_tariff.traffic_type,
                    'tariff_name': applied_tariff.name,
                    'applied_rate': applied_tariff.rate,
                    'approved_rate': approved_rate,
                    'variance': variance,
                    'variance_percent': variance_percent,
                    'tolerance_percent': cls.tolerance_percent(),
                    'status': status,
                    'applied_tariff_version': applied_tariff.version,
                    'approved_tariff_version': reference_tariff.version if reference_tariff else None,
                    'checked_by': user,
                },
            )
            summary['total'] += 1
            summary[status.lower()] += 1
        return summary

    @staticmethod
    def summary(results):
        total = results.count()
        compliant = results.filter(status=TariffComplianceResult.Status.COMPLIANT).count()
        non_compliant = results.filter(status=TariffComplianceResult.Status.NON_COMPLIANT).count()
        under_review = results.filter(status=TariffComplianceResult.Status.UNDER_REVIEW).count()
        return {
            'total': total, 'compliant': compliant, 'non_compliant': non_compliant, 'under_review': under_review,
            'compliant_percent': (compliant / total * 100) if total else 0,
            'non_compliant_percent': (non_compliant / total * 100) if total else 0,
            'under_review_percent': (under_review / total * 100) if total else 0,
        }
