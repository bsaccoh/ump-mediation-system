"""
SGW CDR Processor
==================
Processes Huawei SGW CDR files (4G LTE Serving Gateway data sessions).
Pipeline: decode binary -> parse records -> create -> validate -> enrich -> persist.

SGW records (type 78 / SGW-CDR) are decoded by the PGWDecoder, which handles
both PGW (type 79) and SGW (type 78) records from the same binary format.
This processor inverts the PGWProcessor filter — it keeps only SGW-CDR records
and discards PGW-CDR records.
"""
import csv
import os
import re
import logging
from datetime import datetime, timedelta
from typing import Tuple, List

from core.base_processor import BaseProcessor
from streams.pgw.decoder import PGWDecoder
from streams.sgw.cdr_fields import VALIDATION_RULES, ENRICHMENT_RULES

logger = logging.getLogger(__name__)


class SGWProcessor(BaseProcessor):
    """Processor for Huawei SGW CDR files (4G LTE Serving Gateway)."""

    def decode(self, file_path: str) -> Tuple[bool, str, int]:
        """
        Decode ASN.1 BER binary SGW file using PGWDecoder.

        SGW records (type 78) share the same binary format as PGW records.
        PGWDecoder decodes both; this processor keeps only SGW-CDR records.

        Returns:
            Tuple of (success, file_path_or_error, record_count)
        """
        try:
            decoder = PGWDecoder()
            records = decoder.decode_file(file_path)

            # Keep only SGW records (type 78), discard PGW records (type 79)
            sgw_records = []
            for rec in (records or []):
                rec_type = rec.get('record_type')
                rec_name = rec.get('record_type_name', '').upper()
                if rec_type == PGWDecoder.RECORD_TYPE_SGW or rec_name in ('SGW-CDR', 'SGWRECORD'):
                    sgw_records.append(rec)

            self._decoded_records = sgw_records
            count = len(sgw_records)
            total_decoded = len(records or [])
            logger.info(
                f"SGW decoded {count} SGW records (from {total_decoded} total) in {file_path}"
            )
            print(
                f"[SGW PROCESSOR] Decoded {count} SGW-CDR records "
                f"(filtered from {total_decoded} total). "
                f"Decoder errors: {len(decoder.errors)}"
            )
            if decoder.errors:
                for err in decoder.errors[:5]:
                    print(f"[SGW DECODER ERROR] {err}")

            # Write decoded CSV output
            self._write_decoded_csv(self._decoded_records, file_path, 'sgw')

            return True, file_path, count

        except Exception as e:
            logger.error(f"SGW decode error: {e}", exc_info=True)
            return False, str(e), 0

    def parse_records(self, file_path: str):
        """Generator: yield decoded SGW record dicts."""
        for record in getattr(self, '_decoded_records', []):
            yield record

    def create_record(self, raw: dict, cdr_file):
        """Create SGWRecord from a decoded SGW record dict."""
        from streams.sgw.models import SGWRecord

        start_time = self._parse_timestamp(
            raw.get('start_time') or raw.get('record_opening_time')
        )
        end_time = self._parse_timestamp(raw.get('stop_time'))

        # Calculate end_time from duration if not available
        duration = raw.get('duration_seconds', 0) or 0
        try:
            duration = max(0, min(int(duration), 2_147_483_647))
        except (TypeError, ValueError):
            duration = 0
        if start_time and not end_time and duration > 0:
            end_time = start_time + timedelta(seconds=duration)

        record = SGWRecord(
            file=cdr_file,
            source=cdr_file.source,
            record_type=raw.get('record_type_name', 'SGW-CDR'),
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
            sgw_address=str(raw.get('sgw_address', '') or raw.get('serving_node_address', ''))[:50],
            pgw_address=str(raw.get('pgw_address', ''))[:50],
            node_id=str(raw.get('node_id', ''))[:100],
            cell_id=str(raw.get('cell_id', '') or raw.get('eci', '') or '')[:50],
            lac=str(raw.get('tac', '') or '')[:20],
            serving_plmn=str(raw.get('serving_plmn', ''))[:10],
            cause_for_closing=str(raw.get('cause_for_closing_name', ''))[:50],
            is_roaming=raw.get('is_roaming', False),
            raw_data=self._build_raw_data(raw),
            status=SGWRecord.Status.VALID,
        )
        return record

    def validate_record(self, record, raw: dict) -> List[str]:
        """Validate an SGW record against validation rules."""
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
        """Enrich SGW record with derived fields."""
        if record.start_time and not record.end_time and record.duration:
            record.end_time = record.start_time + timedelta(seconds=record.duration)

    def _parse_timestamp(self, ts_str) -> datetime:
        """Parse SGW timestamp string to timezone-aware datetime."""
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
        """Build a summary dict for the raw_data JSON field."""
        def safe_str(val):
            if val is None or val == '':
                return ''
            return str(val)

        return {
            'record_type': safe_str(raw.get('record_type_name', 'SGW-CDR')),
            'service_type': 'DATA',
            'imsi': safe_str(raw.get('imsi', '')),
            'msisdn': safe_str(raw.get('msisdn', '')),
            'apn': safe_str(raw.get('apn', '')),
            'charging_id': safe_str(raw.get('charging_id', '')),
            'rat_type': safe_str(raw.get('rat_type', '')),
            'rat_type_name': safe_str(raw.get('rat_type_name', '')),
            'pdn_type': safe_str(raw.get('pdn_type', '')),
            'cause': safe_str(raw.get('cause_for_closing_name', '')),
            'sgw_address': safe_str(raw.get('sgw_address') or raw.get('serving_node_address', '')),
            'pgw_address': safe_str(raw.get('pgw_address', '')),
            'serving_plmn': safe_str(raw.get('serving_plmn', '')),
            'data_volume_uplink': safe_str(raw.get('total_data_volume_uplink', 0)),
            'data_volume_downlink': safe_str(raw.get('total_data_volume_downlink', 0)),
            'data_mb': safe_str(raw.get('data_volume_mb', 0)),
            'is_roaming': safe_str(raw.get('is_roaming', False)),
            'start_time': safe_str(raw.get('start_time') or raw.get('record_opening_time') or ''),
            'stop_time': safe_str(raw.get('stop_time') or ''),
            'PREPAID_FLAG': safe_str(raw.get('PREPAID_FLAG', '1')),
            'SUBSCRIBER_CATEGORY': safe_str(raw.get('SUBSCRIBER_CATEGORY', '2')),
        }

    # SGW CSV output columns
    CSV_COLUMNS = [
        'PREPAID_FLAG', 'SUBSCRIBER_CATEGORY', 'RECORD_TYPE', 'SERVICE_TYPE',
        'NETWORK_RECORD_ID', 'MSISDN', 'IMSI', 'IMEI', 'APN', 'PDN_TYPE',
        'SGW_ADDR', 'PGW_ADDR', 'RAT_TYPE', 'CELL_ID', 'TAC',
        'DATA_VOL_UP', 'DATA_VOL_DOWN', 'START_DATETIME', 'END_DATETIME',
        'DURATION', 'CAUSE_FOR_REC_CLOSING', 'CHARGING_ID', 'NODE_ID',
        'CHARGING_CHAR', 'SERVING_PLMN',
    ]

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

            logger.info(f'[SGW] Decoded CSV written: {csv_path} ({len(records)} rows)')
            print(f'[SGW PROCESSOR] Decoded CSV: {csv_path}')
        except Exception as e:
            logger.warning(f'[SGW] Could not write decoded CSV: {e}')
