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

from core.activity import log_activity

logger = logging.getLogger(__name__)


class BaseProcessor(ABC):
    """Abstract base for CDR stream processors.

    Subclasses implement stream-specific logic for each pipeline step.
    The process() method orchestrates the full pipeline.

    Usage::

        processor = MSCProcessor()
        success, message = processor.process(cdr_file_id=42)
    """

    BATCH_SIZE = getattr(settings, 'CDR_BATCH_SIZE', 2000)

    def __init__(self):
        self.records_total = 0
        self.records_valid = 0
        self.records_invalid = 0
        self.records_duplicate = 0
        self.records_by_type = {}
        self.errors = []
        self._errors_persisted = 0

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

    def decode_to_records(self, file_path: str):
        """Decode binary file directly to in-memory dicts.

        Override in subclass to skip the CSV round-trip.
        Return None to fall back to the legacy decode() + parse_records() path.

        Returns:
            None (use legacy path) or Tuple[bool, list, int]
        """
        return None

    def post_process(self, cdr_file) -> None:
        """Hook run after all records for a file have been persisted.

        Default implementation is a no-op.  Subclasses use this for tasks
        that need the full record set to be visible (e.g. CDR-pair
        correlation, deduplication, cross-record enrichment).
        """
        pass

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
        2. Decode binary if needed (in-memory preferred, CSV fallback)
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

        # Route all decoded-record writes/reads + dispatch for this file into
        # the file's per-operator database (mediation_{operator_code}).
        from core.operator_context import set_operator, clear_operator
        set_operator(cdr_file.operator_code)

        try:
            # Mark as processing
            cdr_file.status = CDRFile.Status.PROCESSING
            cdr_file.processing_started = timezone.now()
            cdr_file.save(update_fields=['status', 'processing_started'])

            # Move file to the processing directory if not already there
            self._ensure_in_processing(cdr_file)

            # -----------------------------------------------------------
            # Choose decode path: in-memory (fast) or CSV (legacy)
            # -----------------------------------------------------------
            file_to_process = cdr_file.file_path
            in_memory_records = None

            if self._needs_decoding(file_to_process):
                # Try the fast in-memory path first
                mem_result = self.decode_to_records(file_to_process)
                if mem_result is not None:
                    success, records_or_err, count = mem_result
                    if not success:
                        cdr_file.status = CDRFile.Status.FAILED
                        cdr_file.error_message = f'Decoding failed: {records_or_err}'
                        cdr_file.save(update_fields=['status', 'error_message'])
                        return False, f'Decoding failed: {records_or_err}'
                    in_memory_records = records_or_err
                else:
                    # Fallback to CSV path
                    success, result, count = self.decode(file_to_process)
                    if not success:
                        cdr_file.status = CDRFile.Status.FAILED
                        cdr_file.error_message = f'Decoding failed: {result}'
                        cdr_file.save(update_fields=['status', 'error_message'])
                        return False, f'Decoding failed: {result}'
                    file_to_process = result  # decoded CSV path

            # -----------------------------------------------------------
            # Process records inside a single transaction for speed
            # -----------------------------------------------------------
            record_source = (
                iter(in_memory_records)
                if in_memory_records is not None
                else self.parse_records(file_to_process)
            )

            from django.conf import settings as _settings
            persist = getattr(_settings, 'CDR_PERSIST_RECORDS', True)
            in_memory_out = []  # collected (unsaved) records when persist is off

            batch = []
            with transaction.atomic():
                for raw in record_source:
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
                            if persist:
                                self._flush_batch(batch)
                            else:
                                in_memory_out.extend(batch)
                            batch = []

                    except Exception as e:
                        self.records_invalid += 1
                        if len(self.errors) < 10:
                            self.errors.append(f'Row {self.records_total}: {str(e)[:200]}')
                        # Persist the failure so it's visible on File Detail
                        try:
                            self._log_processing_error(
                                cdr_file, exc=e,
                                record_seq=self.records_total,
                                stage='CREATE',
                                raw=raw,
                            )
                        except Exception:
                            # Never let error-logging itself break processing
                            pass

                # Flush remaining
                if batch:
                    if persist:
                        self._flush_batch(batch)
                    else:
                        in_memory_out.extend(batch)

            # --- Record count quality checks ---
            if self.records_total == 0:
                # File decoded successfully but produced zero records — mark EMPTY
                cdr_file.status = CDRFile.Status.EMPTY
                cdr_file.records_total = 0
                cdr_file.records_valid = 0
                cdr_file.records_invalid = 0
                cdr_file.processing_completed = timezone.now()
                cdr_file.save(update_fields=['status', 'records_total', 'records_valid',
                                             'records_invalid', 'processing_completed'])
                logger.warning(
                    f'{self.__class__.__name__}: {cdr_file.filename} produced 0 records — marked EMPTY'
                )
                log_activity('EMPTY_FILE', 'DECODING', level='WARNING',
                             message=f'{cdr_file.filename} produced 0 records',
                             stream=cdr_file.decoder_type or '',
                             operator=cdr_file.operator_code or '',
                             cdr_file=cdr_file)
                return False, '0 records — file is empty'

            if self.records_valid == 0 and self.records_total > 0:
                logger.warning(
                    f'{self.__class__.__name__}: {cdr_file.filename} — all {self.records_total} '
                    f'records invalid'
                )
                log_activity('ALL_RECORDS_INVALID', 'DECODING', level='WARNING',
                             message=f'{cdr_file.filename}: all {self.records_total} records invalid',
                             stream=cdr_file.decoder_type or '',
                             operator=cdr_file.operator_code or '',
                             cdr_file=cdr_file)
            elif self.records_total > 0:
                invalid_rate = self.records_invalid / self.records_total
                if invalid_rate > 0.05:
                    logger.warning(
                        f'{self.__class__.__name__}: {cdr_file.filename} — high invalid rate '
                        f'{invalid_rate:.1%} ({self.records_invalid}/{self.records_total})'
                    )
                    log_activity('HIGH_INVALID_RATE', 'DECODING', level='WARNING',
                                 message=(f'{cdr_file.filename}: {invalid_rate:.1%} invalid '
                                          f'({self.records_invalid}/{self.records_total})'),
                                 stream=cdr_file.decoder_type or '',
                                 operator=cdr_file.operator_code or '',
                                 cdr_file=cdr_file)

            # Min expected records check (if source has a minimum configured)
            if cdr_file.source_id and self.records_valid > 0:
                try:
                    from collection.models import DataSource as _DS
                    src = _DS.objects.filter(pk=cdr_file.source_id).values('min_expected_records').first()
                    if src and src['min_expected_records'] > 0 and self.records_valid < src['min_expected_records']:
                        logger.warning(
                            f'{self.__class__.__name__}: {cdr_file.filename} — below minimum '
                            f'({self.records_valid} < {src["min_expected_records"]})'
                        )
                        log_activity('BELOW_MIN_RECORDS', 'DECODING', level='WARNING',
                                     message=(f'{cdr_file.filename}: {self.records_valid} valid records, '
                                              f'minimum expected {src["min_expected_records"]}'),
                                     stream=cdr_file.decoder_type or '',
                                     operator=cdr_file.operator_code or '',
                                     cdr_file=cdr_file)
                except Exception:
                    pass

            # Hook: per-stream post-processing (CDR-pair correlation, etc.)
            # Skipped in decode-only mode — it queries persisted records.
            if persist:
                try:
                    self.post_process(cdr_file)
                except Exception as e:
                    logger.error(f'{self.__class__.__name__}.post_process error: {e}', exc_info=True)
                    # Non-fatal — keep the file marked COMPLETED

            # Build summary
            type_summary = ', '.join(f'{k}:{v}' for k, v in self.records_by_type.items())
            summary = f'Processed {self.records_valid} records'
            if type_summary:
                summary += f' ({type_summary})'
            if self.records_invalid:
                summary += f' [{self.records_invalid} invalid]'

            # In SERVICE_MODE the distributor service handles dispatch separately
            # UNLESS CDR_PERSIST_RECORDS=False — then records only exist in
            # memory right now, so we must dispatch here before they're lost.
            service_mode = getattr(settings, 'SERVICE_MODE', False)
            defer_dispatch = service_mode and persist
            cdr_file.status = CDRFile.Status.DECODED if defer_dispatch else CDRFile.Status.COMPLETED
            cdr_file.records_total = self.records_total
            cdr_file.records_valid = self.records_valid
            cdr_file.records_invalid = self.records_invalid
            cdr_file.records_duplicate = self.records_duplicate
            cdr_file.records_by_type = dict(self.records_by_type)
            cdr_file.processing_completed = timezone.now()
            if self.errors:
                cdr_file.error_message = '; '.join(self.errors[:5])
            cdr_file.save()

            logger.info(f'{self.__class__.__name__}: {summary} from {cdr_file.filename}')
            log_activity('DECODE_COMPLETED', 'DECODING',
                         message=f'{self.__class__.__name__}: {summary}',
                         stream=cdr_file.decoder_type or '',
                         operator=cdr_file.operator_code or '',
                         cdr_file=cdr_file)

            if not defer_dispatch:
                try:
                    if persist:
                        from core.dispatcher import dispatch_cdr_file
                        summaries = dispatch_cdr_file(cdr_file.id)
                    else:
                        from core.dispatcher import dispatch_in_memory
                        summaries = dispatch_in_memory(cdr_file, in_memory_out)
                    logger.info(f'Dispatcher fired for {cdr_file.id}: {summaries}')
                except Exception as e:
                    logger.error(f'Dispatcher trigger failed for {cdr_file.id}: {e}', exc_info=True)

            return True, summary

        except Exception as e:
            cdr_file.status = CDRFile.Status.FAILED
            cdr_file.error_message = str(e)[:500]
            cdr_file.save(update_fields=['status', 'error_message'])
            logger.error(f'{self.__class__.__name__} error: {e}', exc_info=True)
            log_activity('DECODE_FAILED', 'DECODING', level='ERROR',
                         message=f'{self.__class__.__name__} error: {e}',
                         stream=getattr(cdr_file, 'decoder_type', '') or '',
                         operator=getattr(cdr_file, 'operator_code', '') or '',
                         cdr_file=cdr_file)
            return False, str(e)
        finally:
            clear_operator()

    def replay_for_downstream(self, file_path: str, cdr_file) -> list:
        """Re-decode a CDR file and return processed in-memory records.

        Runs the same decode → create → validate → enrich → normalize pipeline
        as process(), but:
        - Never touches cdr_file.status or any other CDRFile field
        - Never saves records to the database
        - Never calls dispatch_in_memory() — caller handles dispatch

        Used by the selective downstream replay feature.
        """
        from django.conf import settings as _settings

        file_to_process = file_path
        in_memory_records = None

        if self._needs_decoding(file_to_process):
            mem_result = self.decode_to_records(file_to_process)
            if mem_result is not None:
                success, records_or_err, count = mem_result
                if not success:
                    raise RuntimeError(f'Decoding failed: {records_or_err}')
                in_memory_records = records_or_err
            else:
                success, result, count = self.decode(file_to_process)
                if not success:
                    raise RuntimeError(f'Decoding failed: {result}')
                file_to_process = result

        record_source = (
            iter(in_memory_records)
            if in_memory_records is not None
            else self.parse_records(file_to_process)
        )

        out = []
        for raw in record_source:
            try:
                record = self.create_record(raw, cdr_file)
                if record is None:
                    continue
                self.validate_record(record, raw)
                self.enrich_record(record, raw)
                self.normalize_record(record)
                out.append(record)
            except Exception as e:
                logger.debug(f'replay_for_downstream: skipping record — {e}')

        return out

    def _log_processing_error(self, cdr_file, exc: Exception,
                              record_seq=None, stage: str = 'CREATE',
                              raw=None) -> None:
        """Persist a per-record failure to the ProcessingError table.

        Cap to first 50 errors per file so a broken decoder can't flood the
        table.  The first 10 are also surfaced via cdr_file.error_message.
        """
        from collection.models import ProcessingError, CDRFile

        if self._errors_persisted >= 50:
            return

        # Extract a small hex dump of the failing raw row (first 100 bytes)
        raw_hex = ''
        context = {}
        if raw is not None:
            try:
                if isinstance(raw, (bytes, bytearray)):
                    raw_hex = bytes(raw[:100]).hex().upper()
                elif isinstance(raw, dict):
                    # Show a brief summary of the raw record keys for the context
                    context = {k: (str(v)[:80] if v is not None else '')
                               for k, v in list(raw.items())[:20]
                               if not k.startswith('_')}
            except Exception:
                pass

        try:
            ProcessingError.objects.create(
                cdr_file=cdr_file,
                record_seq=record_seq,
                stage=stage,
                error_class=type(exc).__name__[:120],
                error_message=str(exc)[:2000],
                raw_hex=raw_hex,
                context=context,
            )
            self._errors_persisted += 1
        except Exception:
            pass  # never propagate

    def _ensure_in_processing(self, cdr_file) -> None:
        """Move file from input to the processing directory if not already there."""
        import os
        import shutil
        from pathlib import Path
        try:
            from collection.services.storage import processing_storage_dir
            current = cdr_file.file_path
            if not current or not os.path.isfile(current):
                return
            proc_dir = processing_storage_dir(
                cdr_file.operator_code,
                cdr_file.network_element or cdr_file.decoder_type,
                cdr_file.decoder_type,
                getattr(cdr_file, 'cbs_substream', None) or None,
            )
            proc_path = os.path.join(proc_dir, os.path.basename(current))
            if os.path.abspath(current) == os.path.abspath(proc_path):
                return
            # Check if already inside the processing root
            from django.conf import settings as _s
            proc_root = Path(_s.UMP_PROCESSING_ROOT).resolve()
            if Path(current).resolve().is_relative_to(proc_root):
                return
            shutil.move(current, proc_path)
            cdr_file.file_path = proc_path
            cdr_file.save(update_fields=['file_path'])
            logger.info(f'Moved {cdr_file.filename} to processing/')
        except Exception as e:
            logger.warning(f'Could not move to processing: {e}')

    def _flush_batch(self, batch):
        """Bulk insert a batch of records."""
        if batch:
            model_class = type(batch[0])
            try:
                model_class.objects.bulk_create(batch, ignore_conflicts=False)
            except Exception as e:
                logger.error(f'bulk_create failed for {model_class.__name__}: {e}')
                raise

    def _needs_decoding(self, file_path: str) -> bool:
        """Check if file needs binary decoding based on extension."""
        import os
        ext = os.path.splitext(file_path)[1].lower()
        return ext in ('.dat', '.bin', '.cdr', '.ber', '.asn')
