"""
MSC CDR Processor
==================
Processes Huawei MSC CDR files (voice + SMS).
Pipeline: decode binary -> parse CSV -> create -> validate -> enrich -> normalize -> persist.
"""
import os
import csv
import json
import io
import re
import logging
from typing import Tuple, List

from core.base_processor import BaseProcessor
from core.utils.timestamps import parse_mediation_timestamp
from streams.msc.decoder import decode_cdr_file, is_binary_cdr
from streams.msc.cdr_fields import (
    OUTPUT_FIELDS, VALIDATION_RULES, ENRICHMENT_RULES, FIELD_MAPPING,
)

logger = logging.getLogger(__name__)


class MSCProcessor(BaseProcessor):
    """Processor for Huawei MSC CDR files (voice + SMS)."""

    def decode(self, file_path: str) -> Tuple[bool, str, int]:
        """Decode ASN.1 BER binary to CSV."""
        from django.conf import settings

        base_name = os.path.splitext(os.path.basename(file_path))[0]
        decoded_dir = str(settings.DECODED_DIR / 'msc')
        os.makedirs(decoded_dir, exist_ok=True)
        decoded_path = os.path.join(decoded_dir, f'{base_name}_decoded.csv')

        return decode_cdr_file(file_path, decoded_path)

    def parse_records(self, file_path: str):
        """Generator: parse CSV rows into dicts."""
        with open(file_path, 'r', encoding='utf-8', errors='ignore', newline='') as f:
            content = f.read()

        content = content.replace('\r\n', '\n').replace('\r', '\n')

        # Detect delimiter
        first_line = content.split('\n')[0] if '\n' in content else content[:1000]
        if '|' in first_line:
            delimiter = '|'
        elif ';' in first_line:
            delimiter = ';'
        elif '\t' in first_line:
            delimiter = '\t'
        else:
            delimiter = ','

        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        for row in reader:
            # Normalize keys
            normalized = {}
            for key, value in row.items():
                if key:
                    k = key.strip()
                    v = str(value).strip() if value else None
                    normalized[k] = v
            yield normalized

    def create_record(self, raw: dict, cdr_file):
        """Create MSCRecord from a parsed CSV row."""
        from streams.msc.models import MSCRecord

        # Detect format (mediation uppercase vs legacy lowercase)
        is_mediation = 'ORIGINAL_CALL_TYPE' in raw or 'CALLING_NO' in raw

        if is_mediation:
            record_type = raw.get('ORIGINAL_CALL_TYPE') or 'UNKNOWN'
            service_type = raw.get('SERVICE_TYPE') or raw.get('SERVICE_ID') or 'V'
            service_map = {'V': 'VOICE', 'E': 'SMS', 'D': 'DATA'}
            service_type = service_map.get(service_type, service_type.upper())

            calling = raw.get('CALLING_NO') or raw.get('CHARGED_PARTY_MSISDN') or ''
            called = raw.get('CALLED_NO') or raw.get('DIALED_NO') or ''
            imsi = raw.get('CHARGED_PARTY_IMSI') or raw.get('IMSI_A') or ''
            imei = raw.get('IMEI_A') or ''

            duration_str = raw.get('CALL_DURATION') or '0'
            try:
                duration = int(float(duration_str))
            except (ValueError, TypeError):
                duration = 0

            start_time = parse_mediation_timestamp(raw.get('START_DATETIME'))
            end_time = parse_mediation_timestamp(raw.get('CALL_END_DATETIME'))

            if service_type == 'SMS' and start_time and not end_time:
                end_time = start_time

            record = MSCRecord(
                file=cdr_file,
                source=cdr_file.source,
                record_type=record_type,
                service_type=service_type,
                network_record_id=raw.get('NETWORK_RECORD_ID') or '',
                call_reference=raw.get('CALL_REF') or '',
                call_direction=raw.get('CALL_DIRECTION') or '',
                calling_number=str(calling)[:50] if calling else '',
                called_number=str(called)[:50] if called else '',
                dialed_number=raw.get('DIALED_NO') or '',
                charged_msisdn=raw.get('CHARGED_PARTY_MSISDN') or '',
                imsi=str(imsi)[:20] if imsi else '',
                imei=str(imei)[:20] if imei else '',
                imsi_b=raw.get('IMSI_B') or '',
                imei_b=raw.get('IMEI_B') or '',
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                cell_id=raw.get('CELL_ID_A') or '',
                lac=raw.get('LAC_IDENTIFIER') or '',
                msc_id=raw.get('MSC_ID') or '',
                rat_type=raw.get('RAT_TYPE') or '',
                originating_trunk=raw.get('ORIGINATING_TRUNK') or '',
                terminating_trunk=raw.get('TERMINATING_TRUNK') or '',
                teleservice_code=raw.get('TELESERVICE_CODE') or '',
                bearer_service_code=raw.get('BEARER_SERVICE_CODE') or '',
                result_code=raw.get('RESULT_CODE') or '',
                roaming_indicator=raw.get('ROAMING_ICR_INDICATOR') or '',
                raw_data=raw,
                status=MSCRecord.Status.VALID,
            )
            return record

        # Legacy format not expected for new system, but handle gracefully
        return None

    def validate_record(self, record, raw: dict) -> List[str]:
        """Apply validation rules from cdr_fields.VALIDATION_RULES."""
        errors = []
        for field_name, rules in VALIDATION_RULES.items():
            value = raw.get(field_name, '')
            if not value:
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

        if errors:
            raw['_validation_errors'] = errors[:5]
            record.raw_data = raw

        return errors

    def enrich_record(self, record, raw: dict) -> None:
        """Apply enrichment rules from cdr_fields.ENRICHMENT_RULES."""
        modified = False

        for target_field, rule in ENRICHMENT_RULES.items():
            current_value = raw.get(target_field, '')
            condition = rule.get('condition', 'if_empty')

            if condition == 'if_empty' and current_value:
                continue
            elif condition == 'if_sms_and_empty':
                call_type = raw.get('ORIGINAL_CALL_TYPE', '')
                if call_type not in ('SMSMO', 'SMSMT', 'SMSMO_IW', 'SMSMT_GW',
                                      'SIP_SMSMO', 'SIP_SMSMT'):
                    continue
                if current_value:
                    continue

            transform = rule.get('transform')
            source_field = rule.get('source_field')

            if transform == 'first_n_digits':
                source_val = raw.get(source_field, '')
                n = rule.get('n', 5)
                if source_val and len(source_val) >= n:
                    raw[target_field] = source_val[:n]
                    modified = True
            elif transform == 'copy':
                source_val = raw.get(source_field, '')
                if source_val:
                    raw[target_field] = source_val
                    modified = True
            elif transform == 'default_value':
                raw[target_field] = rule.get('default', '')
                modified = True

        if modified:
            record.raw_data = raw
