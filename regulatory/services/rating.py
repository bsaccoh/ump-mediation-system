"""
Rating Engine Service

Applies active tariffs to classified CDR records to calculate expected revenue.
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from decimal import Decimal

from regulatory.models import Tariff

logger = logging.getLogger(__name__)


# Cache active tariffs at module level
_TARIFF_CACHE = []
_TARIFF_CACHE_LOADED = False
_CACHE_TIMESTAMP = None


def _load_tariff_cache():
    """Load active tariffs into memory cache."""
    global _TARIFF_CACHE, _TARIFF_CACHE_LOADED, _CACHE_TIMESTAMP

    if _TARIFF_CACHE_LOADED:
        # Refresh cache if older than 5 minutes
        from django.utils import timezone
        if _CACHE_TIMESTAMP and (timezone.now() - _CACHE_TIMESTAMP).seconds < 300:
            return

    try:
        _TARIFF_CACHE = list(
            Tariff.objects.filter(status='ACTIVE')
            .select_related()
            .all()
        )
        _TARIFF_CACHE_LOADED = True
        from django.utils import timezone
        _CACHE_TIMESTAMP = timezone.now()
        logger.info(f"Loaded tariff cache: {len(_TARIFF_CACHE)} active tariffs")
    except Exception as e:
        logger.error(f"Failed to load tariff cache: {e}")


class RatingEngine:
    """Rates CDR records using active tariffs."""

    def __init__(self):
        """Initialize rating engine."""
        # Load cache on first instantiation
        if not _TARIFF_CACHE_LOADED:
            _load_tariff_cache()

    def rate_record(self, record: Any, classification: Dict[str, Any],
                    record_timestamp: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Rate a single CDR record.

        Args:
            record: CDR record (unsaved model instance or dict)
            classification: Classification dict from TrafficClassifier
            record_timestamp: Timestamp of the record (for tariff date matching)

        Returns:
            Rating result dict with rated_amount, tariff info, etc.
        """
        dimensions = classification.get('dimensions', {})
        raw_fields = classification.get('raw_fields', {})

        operator_code = dimensions.get('operator_code')
        service_type = dimensions.get('service_type')
        traffic_type = dimensions.get('traffic_type')
        subscriber_category = dimensions.get('subscriber_category')
        destination_country = dimensions.get('destination_country')

        # Extract usage quantity
        usage_quantity = self._extract_usage(raw_fields, service_type)

        # Find matching tariff
        tariff = self._find_tariff(
            operator_code=operator_code,
            service_type=service_type,
            traffic_type=traffic_type,
            subscriber_category=subscriber_category,
            destination_country=destination_country,
            timestamp=record_timestamp or datetime.now()
        )

        if not tariff:
            # No matching tariff found
            return {
                'rated_amount': Decimal('0'),
                'tariff_id': None,
                'tariff_version': None,
                'rate_applied': Decimal('0'),
                'charging_unit': None,
                'usage_quantity': usage_quantity,
                'currency': 'SLE',
                'unrated': True,
                'reason': 'No matching tariff found',
            }

        # Calculate rated amount
        rated_amount = self._calculate_amount(
            tariff,
            usage_quantity,
            service_type
        )

        return {
            'rated_amount': rated_amount,
            'tariff_id': tariff.id,
            'tariff_version': tariff.version,
            'rate_applied': tariff.rate,
            'charging_unit': tariff.charging_unit,
            'usage_quantity': usage_quantity,
            'currency': tariff.currency,
            'unrated': False,
            'reason': None,
        }

    def _extract_usage(self, raw_fields: Dict, service_type: str) -> Decimal:
        """Extract usage quantity from raw fields based on service type."""
        duration = raw_fields.get('duration') or raw_fields.get('CALL_DURATION') or 0

        if service_type == 'VOICE':
            # Duration in minutes
            try:
                duration = Decimal(str(duration))
                return duration / Decimal('60')  # Convert seconds to minutes
            except (ValueError, TypeError):
                return Decimal('0')

        elif service_type == 'SMS':
            # Count = 1 per record
            return Decimal('1')

        elif service_type == 'DATA':
            # Volume in MB (extract from data volume fields if available)
            # For now, return 0 - will need actual data volume fields from CDR
            return Decimal('0')

        return Decimal('0')

    # INTERCONNECT/ROAMING fall back to OFF_NET when no dedicated tariff exists
    _TRAFFIC_TYPE_FALLBACKS = {
        'INTERCONNECT': 'OFF_NET',
        'ROAMING': 'OFF_NET',
        'INBOUND': 'ON_NET',
        'OUTBOUND': 'OFF_NET',
    }

    def _find_tariff(self, operator_code: str, service_type: str,
                     traffic_type: str, subscriber_category: str,
                     destination_country: Optional[str],
                     timestamp: datetime) -> Optional[Tariff]:
        """
        Find the best matching active tariff from cache.

        Match priority (most specific first):
        1. operator + service + traffic_type + subscriber_type + destination
        2. operator + service + traffic_type + subscriber_type
        3. operator + service + traffic_type
        4. operator + service + fallback traffic_type (e.g. INTERCONNECT → OFF_NET)
        5. operator + service (any traffic type)
        """
        tariff = self._match_tariff(
            operator_code, service_type, traffic_type,
            subscriber_category, destination_country,
        )
        if tariff:
            return tariff

        fallback = self._TRAFFIC_TYPE_FALLBACKS.get(traffic_type)
        if fallback:
            tariff = self._match_tariff(
                operator_code, service_type, fallback,
                subscriber_category, destination_country,
            )
            if tariff:
                return tariff

        return self._match_tariff(
            operator_code, service_type, None,
            subscriber_category, destination_country,
        )

    def _match_tariff(self, operator_code: str, service_type: str,
                      traffic_type: Optional[str], subscriber_category: str,
                      destination_country: Optional[str]) -> Optional[Tariff]:
        """Score and return the best matching tariff for the given criteria."""
        matching_tariffs = []

        for tariff in _TARIFF_CACHE:
            if tariff.operator_code != operator_code:
                continue
            if tariff.service_type != service_type:
                continue

            if traffic_type and traffic_type != 'UNKNOWN':
                if tariff.traffic_type != traffic_type:
                    continue

            if subscriber_category:
                if tariff.subscriber_type not in ('ALL', subscriber_category):
                    continue

            if destination_country and destination_country != 'Sierra Leone':
                if tariff.destination and tariff.destination != destination_country:
                    continue

            score = 0
            if tariff.traffic_type and traffic_type and tariff.traffic_type == traffic_type:
                score += 2
            if tariff.subscriber_type == subscriber_category:
                score += 1
            if tariff.destination and destination_country:
                score += 1

            matching_tariffs.append((score, tariff))

        if matching_tariffs:
            matching_tariffs.sort(key=lambda x: x[0], reverse=True)
            return matching_tariffs[0][1]

        return None

    def _calculate_amount(self, tariff: Tariff, usage_quantity: Decimal,
                         service_type: str) -> Decimal:
        """Calculate rated amount based on tariff and usage."""
        rate = tariff.rate
        charging_unit = tariff.charging_unit

        # Calculate base amount
        if charging_unit == 'PER_MINUTE':
            amount = rate * usage_quantity
        elif charging_unit == 'PER_SECOND':
            amount = rate * (usage_quantity * Decimal('60'))
        elif charging_unit == 'PER_MESSAGE':
            amount = rate * usage_quantity
        elif charging_unit in ('PER_MB', 'PER_GB', 'PER_KB'):
            # Data charging - convert usage to appropriate unit
            if charging_unit == 'PER_MB':
                amount = rate * usage_quantity
            elif charging_unit == 'PER_GB':
                amount = rate * (usage_quantity / Decimal('1024'))
            elif charging_unit == 'PER_KB':
                amount = rate * (usage_quantity * Decimal('1024'))
        elif charging_unit == 'FLAT':
            amount = rate
        else:
            amount = rate * usage_quantity

        # Apply minimum charge
        if amount < tariff.minimum_charge:
            amount = tariff.minimum_charge

        # Apply rounding
        amount = self._apply_rounding(amount, tariff.rounding_rule)

        return amount

    def _apply_rounding(self, amount: Decimal, rounding_rule: str) -> Decimal:
        """Apply rounding rule to amount (all monetary values use 2 decimal places)."""
        two_places = Decimal('0.01')
        if rounding_rule == 'CEIL':
            return amount.quantize(two_places, rounding='ROUND_CEILING')
        elif rounding_rule == 'FLOOR':
            return amount.quantize(two_places, rounding='ROUND_FLOOR')
        elif rounding_rule == 'NEAREST':
            return amount.quantize(two_places, rounding='ROUND_HALF_UP')
        else:  # NONE
            return amount.quantize(two_places)


def rate_record(record: Any, classification: Dict[str, Any],
                timestamp: Optional[datetime] = None) -> Dict[str, Any]:
    """Convenience function to rate a single record."""
    engine = RatingEngine()
    return engine.rate_record(record, classification, timestamp)


def rate_batch(records: list, classifications: list,
               timestamps: list = None) -> list:
    """Rate a batch of records."""
    engine = RatingEngine()
    ratings = []

    for i, (record, classification) in enumerate(zip(records, classifications)):
        try:
            timestamp = timestamps[i] if timestamps else None
            rating = engine.rate_record(record, classification, timestamp)
            ratings.append(rating)
        except Exception as e:
            logger.error(f"Failed to rate record {i}: {e}")
            ratings.append({
                'rated_amount': Decimal('0'),
                'tariff_id': None,
                'unrated': True,
                'reason': str(e),
            })

    return ratings
