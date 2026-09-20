"""
Tax Calculator Service

Applies active tax rules to rated amounts.
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from decimal import Decimal

from regulatory.models import TaxType, TaxRate

logger = logging.getLogger(__name__)


# Cache active tax rates at module level
_TAX_RATE_CACHE = []
_TAX_CACHE_LOADED = False
_CACHE_TIMESTAMP = None


def _load_tax_cache():
    """Load active tax rates into memory cache."""
    global _TAX_RATE_CACHE, _TAX_CACHE_LOADED, _CACHE_TIMESTAMP

    if _TAX_CACHE_LOADED:
        # Refresh cache if older than 5 minutes
        from django.utils import timezone
        if _CACHE_TIMESTAMP and (timezone.now() - _CACHE_TIMESTAMP).seconds < 300:
            return

    try:
        _TAX_RATE_CACHE = list(
            TaxRate.objects.filter(status__in=['ACTIVE', 'SCHEDULED'])
            .select_related('tax_type')
            .all()
        )
        _TAX_CACHE_LOADED = True
        from django.utils import timezone
        _CACHE_TIMESTAMP = timezone.now()
        logger.info(f"Loaded tax cache: {len(_TAX_RATE_CACHE)} active tax rates")
    except Exception as e:
        logger.error(f"Failed to load tax cache: {e}")


class TaxCalculator:
    """Calculates taxes on rated amounts using active tax rules."""

    def __init__(self):
        """Initialize tax calculator."""
        # Load cache on first instantiation
        if not _TAX_CACHE_LOADED:
            _load_tax_cache()

    def calculate_tax(self, rated_amount: Decimal, service_type: str,
                      operator_code: str, timestamp: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Calculate tax for a rated amount.

        Args:
            rated_amount: The rated amount before tax
            service_type: Service type (VOICE, SMS, DATA)
            operator_code: Operator code
            timestamp: Timestamp for tax rate matching

        Returns:
            Tax calculation result with tax breakdown
        """
        if rated_amount <= 0:
            return {
                'taxes': [],
                'total_tax': Decimal('0'),
                'taxable_amount': Decimal('0'),
            }

        # Get applicable tax rates
        tax_rates = self._get_applicable_tax_rates(
            service_type, operator_code, timestamp or datetime.now()
        )

        taxes = []
        total_tax = Decimal('0')

        for tax_rate in tax_rates:
            tax_amount = self._calculate_tax_amount(
                rated_amount, tax_rate.rate_percent, tax_rate.treatment
            )

            taxes.append({
                'tax_type_id': tax_rate.tax_type.id,
                'tax_rate_id': tax_rate.id,
                'tax_code': tax_rate.tax_type.code,
                'rate_percent': tax_rate.rate_percent,
                'tax_amount': tax_amount,
                'treatment': tax_rate.treatment,
            })

            total_tax += tax_amount

        return {
            'taxes': taxes,
            'total_tax': total_tax,
            'taxable_amount': rated_amount,
        }

    def _get_applicable_tax_rates(self, service_type: str,
                                   operator_code: str,
                                   timestamp: datetime) -> List[TaxRate]:
        """
        Get all applicable tax rates for the service/operator from cache.

        Returns all active tax rates that match the criteria.
        """
        applicable_rates = []

        for tax_rate in _TAX_RATE_CACHE:
            # Check status
            if tax_rate.status not in {'ACTIVE', 'SCHEDULED'}:
                continue

            # Check effective date range
            if tax_rate.effective_from > timestamp.date():
                continue
            if tax_rate.effective_to and tax_rate.effective_to < timestamp.date():
                continue

            tax_type = tax_rate.tax_type
            if not tax_type.is_active:
                continue

            # Check if tax type applies to this service
            if tax_type.applicable_services:
                if service_type not in tax_type.applicable_services:
                    continue

            # Check if tax type applies to this operator
            if tax_type.applicable_operators:
                if operator_code not in tax_type.applicable_operators:
                    continue

            applicable_rates.append(tax_rate)

        return applicable_rates

    def _calculate_tax_amount(self, rated_amount: Decimal,
                               rate_percent: Decimal,
                               treatment: str) -> Decimal:
        """
        Calculate tax amount based on treatment (exclusive vs inclusive).

        Exclusive: tax = rated_amount × rate / 100
        Inclusive: tax = rated_amount - (rated_amount / (1 + rate/100))
        """
        if treatment == 'INCLUSIVE':
            # Tax is included in rated amount
            divisor = Decimal('1') + (rate_percent / Decimal('100'))
            pre_tax_amount = rated_amount / divisor
            tax_amount = rated_amount - pre_tax_amount
        else:
            # Tax is exclusive (added on top)
            tax_amount = rated_amount * (rate_percent / Decimal('100'))

        return tax_amount.quantize(Decimal('0.01'))


def calculate_tax(rated_amount: Decimal, service_type: str,
                  operator_code: str, timestamp: Optional[datetime] = None) -> Dict[str, Any]:
    """Convenience function to calculate tax for a single amount."""
    calculator = TaxCalculator()
    return calculator.calculate_tax(rated_amount, service_type, operator_code, timestamp)
