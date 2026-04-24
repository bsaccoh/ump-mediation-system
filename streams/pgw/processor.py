"""
PGW CDR Processor
==================
Processes Huawei PGW CDR files (4G data sessions).
Pipeline: decode binary -> parse records -> create -> validate -> enrich -> normalize -> persist.
"""
import csv
import json
import os
import re
import logging
from datetime import datetime, timedelta
from typing import Tuple, List

from core.base_processor import BaseProcessor
from streams.pgw.decoder import PGWDecoder
from streams.pgw.cdr_fields import VALIDATION_RULES, ENRICHMENT_RULES

logger = logging.getLogger(__name__)


class PGWProcessor(BaseProcessor):
    """Processor for Huawei PGW CDR files (4G data sessions)."""

    def decode(self, file_path: str) -> Tuple[bool, str, int]:
        """Decode ASN.1 BER binary PGW file directly into memory.

        Unlike the MSC processor which writes to CSV, PGW decoding goes
        straight from binary -> in-memory records because the decoder
        returns structured dicts directly.

        Returns:
            Tuple of (success, file_path_or_error, record_count)
            On success, file_path is returned as-is (records stored in self._decoded_records).
        """
        try:
            decoder = PGWDecoder()
            records = decoder.decode_file(file_path)

            # Filter out SGW records (handled by SGW stream)
            pgw_records = []
            for rec in (records or []):
                rec_type = rec.get('record_type')
                rec_name = rec.get('record_type_name', '').upper()
                if rec_type == PGWDecoder.RECORD_TYPE_SGW or rec_name in ('SGW-CDR', 'SGWRECORD'):
                    continue
                pgw_records.append(rec)

            self._decoded_records = pgw_records
            count = len(pgw_records)
            logger.info(f"PGW decoded {count} records (filtered from {len(records or [])} total)")
            print(f"[PGW PROCESSOR] Decoded {count} records from {file_path}. Decoder errors: {decoder.errors}")
            if decoder.errors:
                for err in decoder.errors[:5]:
                    print(f"[PGW DECODER ERROR] {err}")

            # Write decoded CSV output (mirrors MSC behaviour)
            self._write_decoded_csv(pgw_records, file_path, 'pgw')

            return True, file_path, count

        except Exception as e:
            logger.error(f"PGW decode error: {e}", exc_info=True)
            return False, str(e), 0

    def parse_records(self, file_path: str):
        """Generator: yield decoded PGW record dicts.

        For PGW, records were already decoded in decode() and stored
        in self._decoded_records.
        """
        for record in getattr(self, '_decoded_records', []):
            yield record

    def create_record(self, raw: dict, cdr_file):
        """Create PGWRecord from a decoded PGW record dict."""
        from streams.pgw.models import PGWRecord

        start_time = self._parse_timestamp(
            raw.get('start_time') or raw.get('record_opening_time')
        )
        end_time = self._parse_timestamp(raw.get('stop_time'))

        # Calculate end_time from duration if not available
        duration = raw.get('duration_seconds', 0) or 0
        # Clamp to Django IntegerField safe range to prevent SQLite overflow
        try:
            duration = max(0, min(int(duration), 2_147_483_647))
        except (TypeError, ValueError):
            duration = 0
        if start_time and not end_time and duration > 0:
            end_time = start_time + timedelta(seconds=duration)

        record = PGWRecord(
            file=cdr_file,
            source=cdr_file.source,
            record_type=raw.get('record_type_name', 'PGW-CDR'),
            service_type='DATA',
            charging_id=str(raw.get('charging_id', '')) if raw.get('charging_id') else '',
            calling_number=str(raw.get('msisdn', ''))[:50],
            called_number=str(raw.get('apn', ''))[:100],
            imsi=str(raw.get('imsi', ''))[:20],
            imei=str(raw.get('imei', '') or raw.get('imeisv', ''))[:20],
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            data_volume_up=str(raw.get('total_data_volume_uplink', 0) or 0),
            data_volume_down=str(raw.get('total_data_volume_downlink', 0) or 0),
            apn=str(raw.get('apn', ''))[:100],
            pdn_type=str(raw.get('pdn_type', ''))[:20],
            rat_type=str(raw.get('rat_type', '') if raw.get('rat_type') is not None else '')[:20],
            pgw_address=str(raw.get('pgw_address', ''))[:50],
            sgw_address=str(raw.get('serving_node_address', ''))[:50],
            node_id=str(raw.get('node_id', ''))[:100],
            cell_id=(str(raw['cell_id']) if 'cell_id' in raw else
                     str(raw['eci']) if 'eci' in raw else '')[:50],
            lac=(str(raw['tac']) if 'tac' in raw else '')[:20],
            serving_plmn=str(raw.get('serving_plmn', ''))[:10],
            cause_for_closing=str(raw.get('cause_for_closing_name', ''))[:50],
            is_roaming=raw.get('is_roaming', False),
            raw_data=self._build_raw_data(raw),
            status=PGWRecord.Status.VALID,
        )
        return record

    def validate_record(self, record, raw: dict) -> List[str]:
        """Validate a PGW record against validation rules."""
        errors = []

        for field_name, rules in VALIDATION_RULES.items():
            # Map field names to raw dict keys
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

            # String validations
            max_len = rules.get('max_length')
            if max_len and len(str(value)) > max_len:
                errors.append(f'{field_name}: exceeds {max_len} chars')

            pattern = rules.get('pattern')
            if pattern and value:
                if not re.match(pattern, str(value)):
                    errors.append(f'{field_name}: pattern mismatch')

            # Numeric validations
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
        """Enrich PGW record with derived fields."""
        # Calculate end_time from start_time + duration if missing
        if record.start_time and not record.end_time and record.duration:
            record.end_time = record.start_time + timedelta(seconds=record.duration)

    def _parse_timestamp(self, ts_str) -> datetime:
        """Parse PGW timestamp string to timezone-aware datetime."""
        if not ts_str:
            return None
        try:
            from django.utils import timezone
            import pytz
            s = str(ts_str)
            if len(s) >= 19:
                naive_dt = datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')
                # Make timezone-aware using UTC
                return timezone.make_aware(naive_dt, pytz.UTC)
            return None
        except (ValueError, TypeError):
            return None

    def _build_raw_data(self, raw: dict) -> dict:
        """Build a summary dict for the raw_data JSON field."""
        # Helper to safely convert any value to string for SQLite compatibility
        def safe_str(val):
            if val is None or val == '':
                return ''
            return str(val)
        
        return {
            'record_type': safe_str(raw.get('record_type_name', 'PGW-CDR')),
            'service_type': 'DATA',
            'imsi': safe_str(raw.get('imsi', '')),
            'msisdn': safe_str(raw.get('msisdn', '')),
            'apn': safe_str(raw.get('apn', '')),
            'charging_id': safe_str(raw.get('charging_id', '')),
            'rat_type': safe_str(raw.get('rat_type', '')),
            'rat_type_name': safe_str(raw.get('rat_type_name', '')),
            'pdn_type': safe_str(raw.get('pdn_type', '')),
            'cause': safe_str(raw.get('cause_for_closing_name', '')),
            'pgw_address': safe_str(raw.get('pgw_address', '')),
            'sgw_address': safe_str(raw.get('serving_node_address', '')),
            'serving_plmn': safe_str(raw.get('serving_plmn', '')),
            'data_volume_uplink': safe_str(raw.get('total_data_volume_uplink', 0)),
            'data_volume_downlink': safe_str(raw.get('total_data_volume_downlink', 0)),
            'data_mb': safe_str(raw.get('data_volume_mb', 0)),
            'is_roaming': safe_str(raw.get('is_roaming', False)),
            'start_time': safe_str(raw.get('start_time') or raw.get('record_opening_time') or ''),
            'stop_time': safe_str(raw.get('stop_time') or ''),
            'cell_id': safe_str(raw.get('cell_id') or raw.get('eci') or ''),
            'tac': safe_str(raw.get('tac') or ''),
            'location_plmn': safe_str(raw.get('location_plmn') or ''),
        }

    # PGW CSV output columns (matches PGW_EXPORT_COLUMNS in dashboard)
    CSV_COLUMNS = [
        'PREPAID_FLAG', 'SUBSCRIBER_CATEGORY', 'RECORD_TYPE', 'SERVICE_TYPE',
        'NETWORK_RECORD_ID', 'MSISDN', 'IMSI', 'IMEI', 'APN', 'PDN_TYPE',
        'SGW_ADDR', 'PGW_ADDR', 'RAT_TYPE', 'CELL_ID', 'TAC',
        'DATA_VOL_UP', 'DATA_VOL_DOWN', 'START_DATETIME', 'END_DATETIME',
        'DURATION', 'CAUSE_FOR_REC_CLOSING', 'CHARGING_ID', 'NODE_ID',
        'CHARGING_CHAR', 'SERVING_PLMN',
    ]

    # Map from CSV column name to decoded record dict key
    CSV_FIELD_MAP = {
        'PREPAID_FLAG': 'PREPAID_FLAG',
        'SUBSCRIBER_CATEGORY': 'SUBSCRIBER_CATEGORY',
        'RECORD_TYPE': 'record_type_name',
        'SERVICE_TYPE': 'service_type',
        'NETWORK_RECORD_ID': 'network_record_id',
        'MSISDN': 'msisdn',
        'IMSI': 'imsi',
        'IMEI': 'imei',
        'APN': 'apn',
        'PDN_TYPE': 'pdn_type',
        'SGW_ADDR': 'sgw_address',
        'PGW_ADDR': 'pgw_address',
        'RAT_TYPE': 'rat_type_name',
        'CELL_ID': 'cell_id',
        'TAC': 'tac',
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

            logger.info(f'[PGW] Decoded CSV written: {csv_path} ({len(records)} rows)')
            print(f'[PGW PROCESSOR] Decoded CSV: {csv_path}')
        except Exception as e:
            logger.warning(f'[PGW] Could not write decoded CSV: {e}')
