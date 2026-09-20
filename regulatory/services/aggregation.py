"""
Aggregation Engine (Regulatory Tap)

Orchestrates classification → rating → tax → aggregation for a batch of
in-memory CDR records. Writes summary rows to the database.

This is the main entry point called from the dispatcher.
"""
import logging
from typing import List, Dict, Any
from datetime import datetime, timedelta
from decimal import Decimal
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from regulatory.models.traffic import TrafficSummary
from regulatory.models.aggregates import RatedAggregate, RevenueSnapshot
from regulatory.services.traffic_classifier import TrafficClassifier
from regulatory.services.rating import RatingEngine
from regulatory.services.tax_calculator import TaxCalculator

logger = logging.getLogger(__name__)


class AggregationEngine:
    """Aggregates CDR records into hourly buckets for regulatory reporting."""

    def __init__(self):
        """Initialize aggregation engine with sub-services."""
        self.classifier = TrafficClassifier()
        self.rating_engine = RatingEngine()
        self.tax_calculator = TaxCalculator()

    def process_batch(self, cdr_file, records: List[Any]) -> Dict[str, Any]:
        """
        Process a batch of CDR records through the regulatory tap.

        Args:
            cdr_file: CDRFile model instance
            records: List of normalized CDR record dicts (in memory)

        Returns:
            Summary dict with processing statistics
        """
        start_time = timezone.now()
        operator_code = getattr(cdr_file, 'operator_code', 'UNKNOWN')

        stats = {
            'total_records': len(records),
            'rated_records': 0,
            'unrated_records': 0,
            'error_records': 0,
            'traffic_summary_created': 0,
            'traffic_summary_updated': 0,
            'rated_aggregate_created': 0,
            'errors': [],
        }

        if not records:
            logger.info(f"Regulatory tap: No records to process for {cdr_file.filename}")
            return stats

        # Aggregate buckets: keyed by (operator, date_hour, service_type, traffic_type,
        # network_technology, traffic_direction, source_stream)
        buckets = defaultdict(lambda: {
            'call_count': 0,
            'total_duration_seconds': 0,
            'sms_count': 0,
            'data_volume_bytes_up': 0,
            'data_volume_bytes_down': 0,
            'record_count': 0,
            'rated_amount': Decimal('0'),
            'unrated_count': 0,
            'tax_amount': Decimal('0'),
            'expected_revenue': Decimal('0'),
        })

        # Process each record
        for i, record in enumerate(records):
            try:
                # Step 1: Classify
                classification = self.classifier.classify(record, operator_code)
                dimensions = classification.get('dimensions', {})

                # Extract timestamp for bucket key
                record_timestamp = self._extract_timestamp(record)
                date_hour = record_timestamp.replace(minute=0, second=0, microsecond=0)

                # Step 2: Rate
                rating = self.rating_engine.rate_record(record, classification, record_timestamp)

                # Step 3: Calculate tax
                if not rating.get('unrated'):
                    tax_result = self.tax_calculator.calculate_tax(
                        rating['rated_amount'],
                        dimensions.get('service_type'),
                        operator_code,
                        record_timestamp
                    )
                    tax_amount = tax_result['total_tax']
                else:
                    tax_amount = Decimal('0')

                # Step 4: Update bucket
                bucket_key = (
                    operator_code,
                    date_hour,
                    dimensions.get('service_type', 'UNKNOWN'),
                    dimensions.get('traffic_type', 'UNKNOWN'),
                    dimensions.get('network_technology', 'UNKNOWN'),
                    dimensions.get('traffic_direction', 'BOTH'),
                    dimensions.get('source_stream', 'UNKNOWN'),
                )

                bucket = buckets[bucket_key]
                bucket['record_count'] += 1

                # Update service-specific metrics
                service_type = dimensions.get('service_type')
                if service_type == 'VOICE':
                    bucket['call_count'] += 1
                    duration = classification.get('raw_fields', {}).get('duration') or 0
                    try:
                        bucket['total_duration_seconds'] += int(duration)
                    except (ValueError, TypeError):
                        pass
                elif service_type == 'SMS':
                    bucket['sms_count'] += 1
                elif service_type == 'DATA':
                    # Data volume - extract from record if available
                    data_up = self._extract_data_volume(record, 'up')
                    data_down = self._extract_data_volume(record, 'down')
                    bucket['data_volume_bytes_up'] += data_up
                    bucket['data_volume_bytes_down'] += data_down

                # Update financial metrics
                record_rated = Decimal('0')
                if rating.get('unrated'):
                    bucket['unrated_count'] += 1
                    stats['unrated_records'] += 1
                else:
                    record_rated = rating['rated_amount']
                    bucket['rated_amount'] += record_rated
                    stats['rated_records'] += 1

                bucket['tax_amount'] += tax_amount
                bucket['expected_revenue'] += record_rated + tax_amount

            except Exception as e:
                logger.error(f"Regulatory tap: Error processing record {i}: {e}")
                stats['error_records'] += 1
                stats['errors'].append(str(e)[:200])

        # Step 5: Write buckets to database
        try:
            with transaction.atomic():
                self._write_buckets(buckets, cdr_file, stats)
                self._write_revenue_snapshots(buckets, operator_code)
        except Exception as e:
            logger.error(f"Regulatory tap: Error writing buckets to database: {e}")
            stats['errors'].append(f"Database write error: {e}")

        # Log execution time
        execution_time = (timezone.now() - start_time).total_seconds()
        logger.info(
            f"Regulatory tap: Processed {stats['total_records']} records in "
            f"{execution_time:.2f}s. "
            f"Rated: {stats['rated_records']}, Unrated: {stats['unrated_records']}, "
            f"Errors: {stats['error_records']}"
        )

        return stats

    def _extract_timestamp(self, record: Any) -> datetime:
        """Extract timestamp from record (model instance or dict)."""
        # For model instances, check the dedicated datetime fields first
        for attr in ('start_time', 'end_time', 'created_at'):
            if hasattr(record, attr):
                value = getattr(record, attr)
                if isinstance(value, datetime):
                    return value

        # Fall back to searching dicts (raw_data or plain dict records)
        raw_data = {}
        if hasattr(record, 'raw_data') and record.raw_data:
            raw_data = record.raw_data
        elif isinstance(record, dict):
            raw_data = record

        for field in ('START_DATETIME', 'START_TIME', 'CALL_START_TIME',
                       'call_start_date', 'start_time', 'timestamp'):
            value = raw_data.get(field)
            if value:
                if isinstance(value, datetime):
                    return value
                try:
                    return datetime.fromisoformat(str(value))
                except (ValueError, TypeError):
                    pass

        return datetime.now()

    def _extract_data_volume(self, record: Any, direction: str) -> int:
        """Extract data volume in bytes from record."""
        # PGW/SGSN/SGW model instances use data_volume_up / data_volume_down.
        # IMS/other variants may use data_volume_uplink / data_volume_downlink.
        if direction == 'up':
            model_attrs = ('data_volume_up', 'data_volume_uplink')
        else:
            model_attrs = ('data_volume_down', 'data_volume_downlink')

        for attr in model_attrs:
            if hasattr(record, attr):
                val = getattr(record, attr, None)
                if val:
                    try:
                        return int(val)
                    except (ValueError, TypeError):
                        pass

        # Fall back to dict search (raw_data or plain dict records)
        raw_data = {}
        if hasattr(record, 'raw_data') and record.raw_data:
            raw_data = record.raw_data
        elif isinstance(record, dict):
            raw_data = record

        if direction == 'up':
            fields = ['data_volume_up', 'DATA_VOLUME_UP', 'uplink_volume',
                       'UPLINK_VOLUME', 'DATA_VOLUME_UPLINK', 'data_volume_uplink']
        else:
            fields = ['data_volume_down', 'DATA_VOLUME_DOWN', 'downlink_volume',
                       'DOWNLINK_VOLUME', 'DATA_VOLUME_DOWNLINK', 'data_volume_downlink']

        for field in fields:
            value = raw_data.get(field)
            if value:
                try:
                    return int(value)
                except (ValueError, TypeError):
                    pass

        return 0

    def _write_revenue_snapshots(self, buckets: Dict, operator_code: str) -> None:
        """Roll up hourly buckets into daily RevenueSnapshot rows."""
        daily: Dict[tuple, Dict[str, Any]] = defaultdict(lambda: {
            'total_traffic_records': 0,
            'total_call_count': 0,
            'total_duration_seconds': 0,
            'total_sms_count': 0,
            'total_data_bytes': 0,
            'expected_revenue': Decimal('0'),
            'expected_tax': Decimal('0'),
        })

        for bucket_key, metrics in buckets.items():
            op_code, date_hour = bucket_key[0], bucket_key[1]
            day_key = (op_code, date_hour.date())
            d = daily[day_key]
            d['total_traffic_records'] += metrics['record_count']
            d['total_call_count'] += metrics['call_count']
            d['total_duration_seconds'] += metrics['total_duration_seconds']
            d['total_sms_count'] += metrics['sms_count']
            d['total_data_bytes'] += metrics['data_volume_bytes_up'] + metrics['data_volume_bytes_down']
            d['expected_revenue'] += metrics['rated_amount']
            d['expected_tax'] += metrics['tax_amount']

        for (op_code, period_date), metrics in daily.items():
            expected_total = metrics['expected_revenue'] + metrics['expected_tax']
            obj, created = RevenueSnapshot.objects.update_or_create(
                operator_code=op_code,
                period_type='DAILY',
                period_date=period_date,
                defaults={
                    'total_traffic_records': metrics['total_traffic_records'],
                    'total_call_count': metrics['total_call_count'],
                    'total_duration_seconds': metrics['total_duration_seconds'],
                    'total_sms_count': metrics['total_sms_count'],
                    'total_data_bytes': metrics['total_data_bytes'],
                    'expected_revenue': metrics['expected_revenue'],
                    'expected_tax': metrics['expected_tax'],
                    'expected_total': expected_total,
                },
            )
            if not created:
                obj.total_traffic_records += metrics['total_traffic_records']
                obj.total_call_count += metrics['total_call_count']
                obj.total_duration_seconds += metrics['total_duration_seconds']
                obj.total_sms_count += metrics['total_sms_count']
                obj.total_data_bytes += metrics['total_data_bytes']
                obj.expected_revenue += metrics['expected_revenue']
                obj.expected_tax += metrics['expected_tax']
                obj.expected_total = obj.expected_revenue + obj.expected_tax
                obj.save()

        logger.info(f"Regulatory tap: wrote {len(daily)} daily RevenueSnapshot rows")

    def _write_buckets(self, buckets: Dict, cdr_file, stats: Dict) -> None:
        """Write aggregation buckets to database."""
        if not buckets:
            return

        traffic_summaries_to_create = []
        traffic_summaries_to_update = []
        rated_aggregates_to_create = []
        new_summary_aggregates = []
        now = timezone.now()

        # Pre-fetch all existing TrafficSummary rows for these buckets in ONE query
        # instead of N individual .get() calls (one per bucket).
        period_hours = list({k[1] for k in buckets})
        operator_codes = list({k[0] for k in buckets})
        existing_rows = TrafficSummary.objects.filter(
            operator_code__in=operator_codes,
            period_start__in=period_hours,
        )
        existing_map = {
            (ts.operator_code, ts.period_start, ts.service_type,
             ts.traffic_type, ts.network_technology, ts.direction, ts.source_stream): ts
            for ts in existing_rows
        }

        for bucket_key, metrics in buckets.items():
            operator_code, date_hour, service_type, traffic_type, \
                network_technology, traffic_direction, source_stream = bucket_key

            period_end = date_hour + timedelta(hours=1)
            existing = existing_map.get(bucket_key)

            if existing is not None:
                existing.call_count += metrics['call_count']
                existing.total_duration_seconds += metrics['total_duration_seconds']
                existing.sms_count += metrics['sms_count']
                existing.data_volume_bytes_up += metrics['data_volume_bytes_up']
                existing.data_volume_bytes_down += metrics['data_volume_bytes_down']
                existing.record_count += metrics['record_count']
                existing.cdr_file_count += 1
                existing.updated_at = now
                traffic_summaries_to_update.append(existing)
                stats['traffic_summary_updated'] += 1

                rated_aggregates_to_create.append(RatedAggregate(
                    traffic_summary=existing,
                    tariff=None,
                    tariff_rate_applied=Decimal('0'),
                    charging_unit='',
                    rated_amount=metrics['rated_amount'],
                    tax_rate_percent=Decimal('0'),
                    tax_amount=metrics['tax_amount'],
                    total_amount=metrics['expected_revenue'],
                    currency='SLE',
                    unrated_count=metrics['unrated_count'],
                    zero_rated_count=0,
                ))
            else:
                summary = TrafficSummary(
                    operator_code=operator_code,
                    period_start=date_hour,
                    period_end=period_end,
                    service_type=service_type,
                    traffic_type=traffic_type,
                    network_technology=network_technology,
                    direction=traffic_direction,
                    subscriber_category='',
                    source_stream=source_stream,
                    destination_country='',
                    destination_operator='',
                    call_count=metrics['call_count'],
                    total_duration_seconds=metrics['total_duration_seconds'],
                    sms_count=metrics['sms_count'],
                    data_volume_bytes_up=metrics['data_volume_bytes_up'],
                    data_volume_bytes_down=metrics['data_volume_bytes_down'],
                    record_count=metrics['record_count'],
                    cdr_file_count=1,
                )
                traffic_summaries_to_create.append(summary)
                stats['traffic_summary_created'] += 1

                agg = RatedAggregate(
                    tariff=None,
                    tariff_rate_applied=Decimal('0'),
                    charging_unit='',
                    rated_amount=metrics['rated_amount'],
                    tax_rate_percent=Decimal('0'),
                    tax_amount=metrics['tax_amount'],
                    total_amount=metrics['expected_revenue'],
                    currency='SLE',
                    unrated_count=metrics['unrated_count'],
                    zero_rated_count=0,
                )
                rated_aggregates_to_create.append(agg)
                new_summary_aggregates.append(agg)

        # Bulk create TrafficSummary and link their RatedAggregates
        if traffic_summaries_to_create:
            created_summaries = TrafficSummary.objects.bulk_create(traffic_summaries_to_create)
            for i, summary in enumerate(created_summaries):
                if i < len(new_summary_aggregates):
                    new_summary_aggregates[i].traffic_summary = summary

        # Bulk update TrafficSummary
        if traffic_summaries_to_update:
            TrafficSummary.objects.bulk_update(
                traffic_summaries_to_update,
                ['call_count', 'total_duration_seconds', 'sms_count',
                 'data_volume_bytes_up', 'data_volume_bytes_down',
                 'record_count', 'cdr_file_count', 'updated_at']
            )

        # Bulk create RatedAggregate
        if rated_aggregates_to_create:
            RatedAggregate.objects.bulk_create(rated_aggregates_to_create)
            stats['rated_aggregate_created'] = len(rated_aggregates_to_create)


def process_regulatory_tap(cdr_file, records: List[Any]) -> Dict[str, Any]:
    """
    Main entry point for the regulatory tap.

    Called from dispatch_in_memory() after distribution rules.

    This function is fail-safe - it never raises to the caller.
    """
    try:
        engine = AggregationEngine()
        return engine.process_batch(cdr_file, records)
    except Exception as e:
        logger.exception(f"Regulatory tap failed: {e}")
        return {
            'total_records': len(records) if records else 0,
            'rated_records': 0,
            'unrated_records': 0,
            'error_records': len(records) if records else 0,
            'errors': [str(e)],
        }
