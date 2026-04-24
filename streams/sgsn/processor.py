"""
SGSN CDR Processor
==================
Processes Huawei SGSN CDR files (2G/3G GPRS data sessions).
Pipeline: decode binary -> parse records -> create -> validate -> enrich -> persist.
"""
import csv
import os
import re
import logging
from datetime import datetime, timedelta
from typing import Tuple, List

from core.base_processor import BaseProcessor
from streams.sgsn.decoder import SGSNDecoder
from streams.sgsn.cdr_fields import VALIDATION_RULES, ENRICHMENT_RULES

logger = logging.getLogger(__name__)


class SGSNProcessor(BaseProcessor):
    """Processor for Huawei SGSN CDR files (2G/3G GPRS data sessions)."""

    def decode(self, file_path: str) -> Tuple[bool, str, int]:
        """
        Decode ASN.1 BER binary SGSN file directly into memory.

        Like PGW, SGSN decoding goes straight from binary -> in-memory records.
        Decoded records are stored in self._decoded_records.

        Returns:
            Tuple of (success, file_path_or_error, record_count)
        """
        try:
            decoder = SGSNDecoder()
            records = decoder.decode_file(file_path)

            self._decoded_records = records or []
            count = len(self._decoded_records)

            logger.info(f"SGSN decoded {count} records from {file_path}")
            print(f"[SGSN PROCESSOR] Decoded {count} records from {file_path}. "
                  f"Decoder errors: {len(decoder.errors)}")
            if decoder.errors:
                for err in decoder.errors[:5]:
                    print(f"[SGSN DECODER ERROR] {err}")

            # Write decoded CSV output (mirrors MSC behaviour)
            self._write_decoded_csv(self._decoded_records, file_path, 'sgsn')

            return True, file_path, count

        except Exception as e:
            logger.error(f"SGSN decode error: {e}", exc_info=True)
            return False, str(e), 0

    def parse_records(self, file_path: str):
        """Generator: yield decoded SGSN record dicts."""
        for record in getattr(self, '_decoded_records', []):
            yield record

    def create_record(self, raw: dict, cdr_file):
        """Create SGSNRecord from a decoded SGSN record dict."""
        from streams.sgsn.models import SGSNRecord

        start_time = self._parse_timestamp(
            raw.get('start_time') or raw.get('record_opening_time')
        )
        end_time = self._parse_timestamp(raw.get('stop_time'))

        # Compute duration
        duration = raw.get('duration_seconds', 0) or 0
        try:
            duration = max(0, min(int(duration), 2_147_483_647))
        except (TypeError, ValueError):
            duration = 0

        # Derive end_time from start + duration if stop_time is absent
        if start_time and not end_time and duration > 0:
            end_time = start_time + timedelta(seconds=duration)

        # Determine service_type
        service_type = raw.get('service_type', 'DATA')

        record = SGSNRecord(
            file=cdr_file,
            source=cdr_file.source,
            record_type=raw.get('record_type_name', 'SGSN-CDR'),
            service_type=service_type,
            charging_id=str(raw.get('charging_id', '')) if raw.get('charging_id') else '',
            calling_number=str(raw.get('msisdn', '') or raw.get('calling_number', ''))[:50],
            called_number=str(raw.get('apn', '') or raw.get('called_number', ''))[:100],
            imsi=str(raw.get('imsi', ''))[:20],
            imei=str(raw.get('imei', ''))[:20],
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            data_volume_up=str(raw.get('total_data_volume_uplink', 0) or 0),
            data_volume_down=str(raw.get('total_data_volume_downlink', 0) or 0),
            apn=str(raw.get('apn', ''))[:100],
            pdp_type=str(raw.get('pdp_type', ''))[:20],
            rat_type=str(raw.get('rat_type_name') or raw.get('rat_type') or '')[:20],
            sgsn_address=str(raw.get('sgsn_address', ''))[:50],
            ggsn_address=str(raw.get('ggsn_address', ''))[:50],
            node_id=str(raw.get('node_id', ''))[:100],
            cell_id=(str(raw['cell_id']) if 'cell_id' in raw else '')[:50],
            lac=(str(raw['lac']) if 'lac' in raw else '')[:20],
            rac=(str(raw['rac']) if 'rac' in raw else '')[:20],
            serving_plmn=str(raw.get('serving_plmn', '') or raw.get('location_plmn', ''))[:10],
            cause_for_closing=str(raw.get('cause_for_closing_name', ''))[:50],
            is_roaming=raw.get('is_roaming', False),
            raw_data=self._build_raw_data(raw),
            status=SGSNRecord.Status.VALID,
        )
        return record

    def validate_record(self, record, raw: dict) -> List[str]:
        """Validate an SGSN record against validation rules."""
        errors = []

        for field_name, rules in VALIDATION_RULES.items():
            raw_key_map = {
                'calling_number': 'msisdn',
                'apn': 'apn',
                'imsi': 'imsi',
                'imei': 'imei',
                'duration': 'duration_seconds',
                'data_volume_up': 'total_data_volume_uplink',
                'data_volume_down': 'total_data_volume_downlink',
            }
            raw_key = raw_key_map.get(field_name, field_name)
            value = raw.get(raw_key, '')

            if not value and value != 0:
                if rules.get('required', False):
                    errors.append(f'{field_name}: required')
                continue

            max_len = rules.get('max_length')
            if max_len and len(str(value)) > max_len:
                errors.append(f'{field_name}: exceeds {max_len} chars')

            pattern = rules.get('pattern')
            if pattern and value:
                if not re.match(pattern, str(value)):
                    errors.append(f'{field_name}: pattern mismatch')

            rule_type = rules.get('type')
            if rule_type == 'integer':
                try:
                    num_val = int(value) if not isinstance(value, (int, float)) else value
                    min_val = rules.get('min_value')
                    max_val = rules.get('max_value')
                    if min_val is not None and num_val < min_val:
                        errors.append(f'{field_name}: below minimum {min_val}')
                    if max_val is not None and num_val > max_val:
                        errors.append(f'{field_name}: above maximum {max_val}')
                except (ValueError, TypeError):
                    errors.append(f'{field_name}: not a valid integer')

        return errors

    def enrich_record(self, record, raw: dict) -> None:
        """Enrich SGSN record with derived fields."""
        if record.start_time and not record.end_time and record.duration:
            record.end_time = record.start_time + timedelta(seconds=record.duration)

    def _parse_timestamp(self, ts_str) -> datetime:
        """Parse SGSN timestamp string to timezone-aware datetime."""
        if not ts_str:
            return None
        try:
            from django.utils import timezone
            import pytz
            s = str(ts_str)
            if len(s) >= 19:
                naive_dt = datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')
                return timezone.make_aware(naive_dt, pytz.UTC)
            return None
        except (ValueError, TypeError):
            return None

    def _build_raw_data(self, raw: dict) -> dict:
        """Build summary dict for the raw_data JSON field."""
        def safe_str(val):
            if val is None or val == '':
                return ''
            return str(val)

        return {
            'record_type': safe_str(raw.get('record_type_name', 'SGSN-CDR')),
            'service_type': safe_str(raw.get('service_type', 'DATA')),
            'imsi': safe_str(raw.get('imsi', '')),
            'msisdn': safe_str(raw.get('msisdn', '')),
            'imei': safe_str(raw.get('imei', '')),
            'apn': safe_str(raw.get('apn', '')),
            'charging_id': safe_str(raw.get('charging_id', '')),
            'rat_type': safe_str(raw.get('rat_type', '')),
            'rat_type_name': safe_str(raw.get('rat_type_name', '')),
            'pdp_type': safe_str(raw.get('pdp_type', '')),
            'cause': safe_str(raw.get('cause_for_closing_name', '')),
            'sgsn_address': safe_str(raw.get('sgsn_address', '')),
            'ggsn_address': safe_str(raw.get('ggsn_address', '')),
            'serving_plmn': safe_str(raw.get('serving_plmn', '')),
            'lac': safe_str(raw.get('lac', '')),
            'rac': safe_str(raw.get('rac', '')),
            'cell_id': safe_str(raw.get('cell_id', '')),
            'data_volume_uplink': safe_str(raw.get('total_data_volume_uplink', 0)),
            'data_volume_downlink': safe_str(raw.get('total_data_volume_downlink', 0)),
            'data_mb': safe_str(raw.get('data_volume_mb', 0)),
            'is_roaming': safe_str(raw.get('is_roaming', False)),
            'start_time': safe_str(raw.get('start_time') or raw.get('record_opening_time') or ''),
            'stop_time': safe_str(raw.get('stop_time') or ''),
            'PREPAID_FLAG': safe_str(raw.get('PREPAID_FLAG', '1')),
            'SUBSCRIBER_CATEGORY': safe_str(raw.get('SUBSCRIBER_CATEGORY', '2')),
        }

    # SGSN CSV output columns
    CSV_COLUMNS = [
        'PREPAID_FLAG', 'SUBSCRIBER_CATEGORY', 'RECORD_TYPE', 'SERVICE_TYPE',
        'NETWORK_RECORD_ID', 'IMSI', 'IMEI', 'MSISDN', 'APN', 'PDP_TYPE',
        'SGSN_ADDR', 'GGSN_ADDR', 'RAT_TYPE', 'CELL_ID', 'LAC', 'RAC',
        'DATA_VOL_UP', 'DATA_VOL_DOWN', 'START_DATETIME', 'END_DATETIME',
        'DURATION', 'CAUSE_FOR_REC_CLOSING', 'CHARGING_ID', 'NODE_ID',
        'CHARGING_CHAR', 'SERVING_PLMN',
    ]

    CSV_FIELD_MAP = {
        'PREPAID_FLAG': 'PREPAID_FLAG',
        'SUBSCRIBER_CATEGORY': 'SUBSCRIBER_CATEGORY',
        'RECORD_TYPE': 'record_type_name',
        'SERVICE_TYPE': 'service_type',
        'NETWORK_RECORD_ID': 'record_sequence_number',
        'IMSI': 'imsi',
        'IMEI': 'imei',
        'MSISDN': 'msisdn',
        'APN': 'apn',
        'PDP_TYPE': 'pdp_type',
        'SGSN_ADDR': 'sgsn_address',
        'GGSN_ADDR': 'ggsn_address',
        'RAT_TYPE': 'rat_type_name',
        'CELL_ID': 'cell_id',
        'LAC': 'lac',
        'RAC': 'rac',
        'DATA_VOL_UP': 'total_data_volume_uplink',
        'DATA_VOL_DOWN': 'total_data_volume_downlink',
        'START_DATETIME': 'start_time',
        'END_DATETIME': 'stop_time',
        'DURATION': 'duration_seconds',
        'CAUSE_FOR_REC_CLOSING': 'cause_for_closing_name',
        'CHARGING_ID': 'charging_id',
        'NODE_ID': 'node_id',
        'CHARGING_CHAR': 'charging_characteristics',
        'SERVING_PLMN': 'serving_plmn',
    }

    def _write_decoded_csv(self, records: list, source_path: str, stream: str) -> None:
        """Write decoded records to a CSV file in data/decoded/<stream>/."""
        from django.conf import settings
        try:
            decoded_dir = str(settings.DECODED_DIR / stream)
            os.makedirs(decoded_dir, exist_ok=True)
            base_name = os.path.splitext(os.path.basename(source_path))[0]
            csv_path = os.path.join(decoded_dir, f'{base_name}_decoded.csv')

            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.CSV_COLUMNS)
                for rec in records:
                    row = []
                    for col in self.CSV_COLUMNS:
                        key = self.CSV_FIELD_MAP.get(col, col.lower())
                        val = rec.get(key, '')
                        row.append('' if val is None else str(val))
                    writer.writerow(row)

            logger.info(f'[SGSN] Decoded CSV written: {csv_path} ({len(records)} rows)')
            print(f'[SGSN PROCESSOR] Decoded CSV: {csv_path}')
        except Exception as e:
            logger.warning(f'[SGSN] Could not write decoded CSV: {e}')
