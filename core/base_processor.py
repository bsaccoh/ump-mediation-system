"""
Base Processor
===============
Abstract base class for all stream processors.
Defines the standard pipeline: decode -> create -> validate -> enrich -> normalize -> persist.
"""
import json
import logging
from abc import ABC, abstractmethod
from django.utils import timezone
from typing import Tuple, List, Optional

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


class BaseProcessor(ABC):
    """Abstract base for CDR stream processors.

    Subclasses implement stream-specific logic for each pipeline step.
    The process() method orchestrates the full pipeline.

    Usage::

        processor = MSCProcessor()
        success, message = processor.process(cdr_file_id=42)
    """

    BATCH_SIZE = getattr(settings, 'CDR_BATCH_SIZE', 500)

    def __init__(self):
        self.records_total = 0
        self.records_valid = 0
        self.records_invalid = 0
        self.records_duplicate = 0
        self.records_by_type = {}
        self.errors = []

    # -------------------------------------------------------------------------
    # Abstract methods - implement per stream
    # -------------------------------------------------------------------------

    @abstractmethod
    def decode(self, file_path: str) -> Tuple[bool, str, int]:
        """Decode binary file to CSV or return decoded records.

        Returns:
            Tuple of (success, decoded_path_or_error, record_count)
        """
        ...

    @abstractmethod
    def parse_records(self, file_path: str):
        """Generator yielding parsed record dicts from decoded file."""
        ...

    @abstractmethod
    def create_record(self, raw: dict, cdr_file):
        """Create a Django model instance from a parsed record dict."""
        ...

    @abstractmethod
    def validate_record(self, record, raw: dict) -> List[str]:
        """Validate a record. Return list of error strings (empty = valid)."""
        ...

    @abstractmethod
    def enrich_record(self, record, raw: dict) -> None:
        """Enrich record with derived/computed fields. Mutates in place."""
        ...

    def normalize_record(self, record) -> None:
        """Normalize field values. Default implementation handles common cases."""
        if hasattr(record, 'record_type') and record.record_type:
            record.record_type = record.record_type.strip().upper()
        if hasattr(record, 'service_type') and record.service_type:
            record.service_type = record.service_type.strip().upper()
        if hasattr(record, 'calling_number') and record.calling_number:
            record.calling_number = record.calling_number.strip()
        if hasattr(record, 'called_number') and record.called_number:
            record.called_number = record.called_number.strip()
        if hasattr(record, 'imsi') and record.imsi:
            record.imsi = record.imsi.strip()
        if hasattr(record, 'duration') and record.duration is not None:
            if record.duration < 0:
                record.duration = 0

    # -------------------------------------------------------------------------
    # Pipeline orchestration
    # -------------------------------------------------------------------------

    def process(self, cdr_file_id: int) -> Tuple[bool, str]:
        """Run the full processing pipeline for a CDR file.

        1. Load CDRFile, set status=PROCESSING
        2. Decode binary if needed
        3. Parse each record: create -> validate -> enrich -> normalize
        4. Batch insert to DB
        5. Update CDRFile status

        Returns:
            Tuple of (success: bool, message: str)
        """
        from collection.models import CDRFile

        cdr_file = CDRFile.objects.filter(pk=cdr_file_id).first()
        if not cdr_file:
            return False, 'CDR file not found'

        try:
            # Mark as processing
            cdr_file.status = CDRFile.Status.PROCESSING
            cdr_file.processing_started = timezone.now()
            cdr_file.save(update_fields=['status', 'processing_started'])

            # Decode binary if needed
            file_to_process = cdr_file.file_path
            if self._needs_decoding(file_to_process):
                success, result, count = self.decode(file_to_process)
                if not success:
                    cdr_file.status = CDRFile.Status.FAILED
                    cdr_file.error_message = f'Decoding failed: {result}'
                    cdr_file.save(update_fields=['status', 'error_message'])
                    return False, f'Decoding failed: {result}'
                file_to_process = result  # decoded CSV path

            # Process records
            batch = []
            for raw in self.parse_records(file_to_process):
                self.records_total += 1
                try:
                    record = self.create_record(raw, cdr_file)
                    if record is None:
                        self.records_invalid += 1
                        continue

                    errors = self.validate_record(record, raw)
                    if errors:
                        record.status = 'INVALID'

                    self.enrich_record(record, raw)
                    self.normalize_record(record)

                    batch.append(record)
                    self.records_valid += 1

                    # Track by service type
                    svc = getattr(record, 'service_type', 'UNKNOWN')
                    self.records_by_type[svc] = self.records_by_type.get(svc, 0) + 1

                    if len(batch) >= self.BATCH_SIZE:
                        self._flush_batch(batch)
                        batch = []

                except Exception as e:
                    self.records_invalid += 1
                    if len(self.errors) < 10:
                        self.errors.append(f'Row {self.records_total}: {str(e)[:200]}')

            # Flush remaining
            if batch:
                self._flush_batch(batch)

            # Build summary
            type_summary = ', '.join(f'{k}:{v}' for k, v in self.records_by_type.items())
            summary = f'Processed {self.records_valid} records'
            if type_summary:
                summary += f' ({type_summary})'
            if self.records_invalid:
                summary += f' [{self.records_invalid} invalid]'

            # Update file status
            cdr_file.status = CDRFile.Status.COMPLETED
            cdr_file.records_total = self.records_total
            cdr_file.records_valid = self.records_valid
            cdr_file.records_invalid = self.records_invalid
            cdr_file.records_duplicate = self.records_duplicate
            cdr_file.processing_completed = timezone.now()
            if self.errors:
                cdr_file.error_message = '; '.join(self.errors[:5])
            cdr_file.save()

            logger.info(f'{self.__class__.__name__}: {summary} from {cdr_file.filename}')
            return True, summary

        except Exception as e:
            cdr_file.status = CDRFile.Status.FAILED
            cdr_file.error_message = str(e)[:500]
            cdr_file.save(update_fields=['status', 'error_message'])
            logger.error(f'{self.__class__.__name__} error: {e}', exc_info=True)
            return False, str(e)

    def _flush_batch(self, batch):
        """Bulk insert a batch of records."""
        if batch:
            model_class = type(batch[0])
            try:
                model_class.objects.bulk_create(batch, ignore_conflicts=False)
            except Exception as e:
                # Debug: print details about first record that might be causing the issue
                print(f"\n[FLUSH_BATCH ERROR] {str(e)}")
                if batch:
                    first_record = batch[0]
                    print(f"[DEBUG] First record fields:")
                    for field_name in dir(first_record):
                        if not field_name.startswith('_') and not callable(getattr(first_record, field_name, None)):
                            try:
                                val = getattr(first_record, field_name)
                                if val is not None and not isinstance(val, (bytes, type)):
                                    print(f"  {field_name}: {type(val).__name__} = {repr(val)[:100]}")
                            except:
                                pass
                raise

    def _needs_decoding(self, file_path: str) -> bool:
        """Check if file needs binary decoding based on extension."""
        import os
        ext = os.path.splitext(file_path)[1].lower()
        return ext in ('.dat', '.bin', '.cdr', '.ber', '.asn')
