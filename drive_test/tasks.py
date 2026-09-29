"""
Drive Test Celery Tasks

process_drive_test_file  — main pipeline: parse → bulk-insert → cell-match → analyse
                           Wrapped with tracked_task so JobRecord tracks progress.
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from core.tasks import tracked_task

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1000  # measurements per bulk_create batch


@tracked_task('drive_test.process_drive_test_file')
def _run_process_drive_test_file(drive_file_id: int):
    """
    Full processing pipeline for one DriveTestFile:
      1. Load file metadata and resolve parser
      2. Parse file → stream of ParsedMeasurement
      3. Bulk-create Measurement + RadioMeasurement + ServiceMeasurement rows
      4. Run CellReferenceMatcher
      5. Run AnalysisEngine (generates Finding rows)
      6. Compute DataQualityResult
      7. Update DriveTestFile.status + DriveTestSession aggregates
    """
    from drive_test.models import DriveTestFile, Measurement, RadioMeasurement, ServiceMeasurement

    try:
        drive_file = DriveTestFile.objects.select_related(
            'session__operator', 'parser_profile'
        ).get(pk=drive_file_id)
    except DriveTestFile.DoesNotExist:
        logger.error('DriveTestFile #%d not found', drive_file_id)
        return {'error': 'File not found', 'file_id': drive_file_id}

    drive_file.status = DriveTestFile.Status.PARSING
    drive_file.processing_started_at = timezone.now()
    drive_file.save(update_fields=['status', 'processing_started_at'])

    session = drive_file.session

    try:
        # ----------------------------------------------------------------
        # Step 1: resolve parser
        # ----------------------------------------------------------------
        parser = _resolve_parser(drive_file)
        file_path = Path(drive_file.file_path)
        if not file_path.exists():
            raise FileNotFoundError(f'Drive test file not found on disk: {file_path}')

        # ----------------------------------------------------------------
        # Step 2: parse → bulk-insert in chunks
        # ----------------------------------------------------------------
        drive_file.status = DriveTestFile.Status.PARSING
        drive_file.save(update_fields=['status'])

        total_inserted = 0
        chunk_measurements = []
        chunk_radio = []
        chunk_service = []
        _test_device = None
        _device_resolved = False

        def _naive(dt):
            """Strip tzinfo when USE_TZ=False so SQLite accepts the value."""
            if dt is None:
                return None
            from django.conf import settings
            if not settings.USE_TZ and dt.tzinfo is not None:
                from datetime import timezone as _tz
                return dt.astimezone(_tz.utc).replace(tzinfo=None)
            return dt

        for pm in parser.parse(file_path):
            # Resolve TestDevice once on first measurement that carries device info
            if not _device_resolved:
                device_info = pm.raw_data.get('__device__')
                if device_info:
                    _test_device = _resolve_test_device(device_info)
                    _device_resolved = True

            m = Measurement(
                drive_file=drive_file,
                sequence_num=pm.sequence_num,
                captured_at=_naive(pm.captured_at),
                local_timestamp=_naive(pm.local_timestamp),
                latitude=pm.latitude,
                longitude=pm.longitude,
                altitude_m=pm.altitude_m,
                gps_accuracy_m=pm.gps_accuracy_m,
                gps_hdop=pm.gps_hdop,
                speed_kmh=pm.speed_kmh,
                heading_deg=pm.heading_deg,
                obs_mcc=pm.obs_mcc,
                obs_mnc=pm.obs_mnc,
                obs_lac=pm.obs_lac,
                obs_ci=pm.obs_ci,
                obs_tac=pm.obs_tac,
                obs_eci=pm.obs_eci,
                obs_pci=pm.obs_pci,
                obs_earfcn=pm.obs_earfcn,
                obs_nrarfcn=pm.obs_nrarfcn,
                is_valid=pm.is_valid,
                quality_flags=pm.quality_flags,
                test_device=_test_device,
            )
            chunk_measurements.append((m, pm))

            if len(chunk_measurements) >= _CHUNK_SIZE:
                total_inserted += _flush_chunk(chunk_measurements, chunk_radio, chunk_service,
                                               drive_file)
                chunk_measurements, chunk_radio, chunk_service = [], [], []

        # Final partial chunk
        if chunk_measurements:
            total_inserted += _flush_chunk(chunk_measurements, chunk_radio, chunk_service,
                                           drive_file)

        drive_file.measurement_count = total_inserted
        drive_file.status = DriveTestFile.Status.MATCHING
        drive_file.save(update_fields=['measurement_count', 'status'])

        # ----------------------------------------------------------------
        # Step 3: cell matching
        # ----------------------------------------------------------------
        from drive_test.services.cell_matcher import CellReferenceMatcher
        matcher = CellReferenceMatcher(operator_id=session.operator_id)
        matcher.match_bulk(Measurement.objects.filter(drive_file=drive_file))

        drive_file.status = DriveTestFile.Status.NORMALIZING
        drive_file.save(update_fields=['status'])

        # ----------------------------------------------------------------
        # Step 4: analysis (generates Finding rows)
        # ----------------------------------------------------------------
        from drive_test.services.analysis import AnalysisEngine, QualityAssessor
        engine = AnalysisEngine(drive_file)
        engine.run()

        # ----------------------------------------------------------------
        # Step 5: data quality result
        # ----------------------------------------------------------------
        QualityAssessor().assess(drive_file)

        # ----------------------------------------------------------------
        # Step 6: mark complete + update session aggregates
        # ----------------------------------------------------------------
        drive_file.status = DriveTestFile.Status.COMPLETED
        drive_file.processing_completed_at = timezone.now()
        drive_file.save(update_fields=['status', 'processing_completed_at'])

        _update_session_aggregates(session)

        logger.info(
            'Drive test file #%d processed: %d measurements, session=%s',
            drive_file_id, total_inserted, session.session_ref,
        )
        _audit_processing_event(drive_file, status='SUCCESS', measurements=total_inserted)
        return {
            'file_id': drive_file_id,
            'session_ref': session.session_ref,
            'measurements': total_inserted,
            'result_entity_type': 'DriveTestFile',
            'result_entity_id': str(drive_file.pk),
            'result_url': f'/drive-test/sessions/{session.session_ref}/',
        }

    except Exception as exc:
        logger.exception('Drive test processing failed for file #%d', drive_file_id)
        drive_file.status = DriveTestFile.Status.FAILED
        drive_file.error_message = f'{type(exc).__name__}: {exc}'
        drive_file.processing_completed_at = timezone.now()
        drive_file.save(update_fields=['status', 'error_message', 'processing_completed_at'])
        _update_session_aggregates(drive_file.session)
        _audit_processing_event(drive_file, status='FAILED', error=f'{type(exc).__name__}: {exc}')
        raise


# ---------------------------------------------------------------------------
# Public dispatch function (called from file_handler.py)
# ---------------------------------------------------------------------------

def process_drive_test_file(drive_file_id: int) -> None:
    """
    Create a JobRecord and enqueue the processing task.
    Falls back to synchronous execution if Celery is unavailable.
    """
    from drive_test.models import DriveTestFile

    try:
        drive_file = DriveTestFile.objects.select_related('session__operator').get(pk=drive_file_id)
    except DriveTestFile.DoesNotExist:
        logger.error('Cannot dispatch: DriveTestFile #%d not found', drive_file_id)
        return

    from core.tasks import enqueue_job
    job = enqueue_job(
        task=_run_process_drive_test_file,
        job_type='drive_test.process_file',
        label=f'Process {drive_file.original_filename}',
        user=None,
        args=(drive_file_id,),
    )
    # Link job to file
    drive_file.job = job
    drive_file.save(update_fields=['job'])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _audit_processing_event(drive_file, *, status: str, **extra) -> None:
    """One real audit record per finished processing attempt (success or failure).
    Uses the existing core.AuditLog via services.audit.log — never a second audit
    system — and never raises: a logging failure must not fail the real pipeline."""
    try:
        from drive_test.services import audit as al

        if 'error' in extra:
            extra['error'] = al.sanitize_error(extra['error'])
        description = (
            f'File processed: {drive_file.original_filename} ({drive_file.session.session_ref})'
            if status == 'SUCCESS' else
            f'File processing failed: {drive_file.original_filename} ({drive_file.session.session_ref})'
        )
        al.log(action='PROCESS', obj=drive_file, description=description, status=status, **extra)
    except Exception:
        logger.exception('Audit log write failed for drive_file #%s', drive_file.pk)


def _resolve_parser(drive_file):
    """Instantiate the correct parser. Falls back to CsvDriveTestParser."""
    if drive_file.parser_profile and drive_file.parser_profile.parser_class:
        try:
            module_path, cls_name = drive_file.parser_profile.parser_class.rsplit('.', 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, cls_name)
            return cls(config=drive_file.parser_profile.default_config)
        except Exception as exc:
            logger.warning('Could not load parser %s: %s — falling back to CSV',
                           drive_file.parser_profile.parser_class, exc)

    from drive_test.parsers.csv_parser import CsvDriveTestParser
    return CsvDriveTestParser()


def _flush_chunk(chunk: list, chunk_radio: list, chunk_service: list, drive_file) -> int:
    """bulk_create one chunk of measurements and their radio/service sub-rows."""
    from drive_test.models import Measurement, RadioMeasurement, ServiceMeasurement

    measurement_objects = [item[0] for item in chunk]
    created = Measurement.objects.bulk_create(measurement_objects)

    radio_rows = []
    service_rows = []

    for m_obj, pm in zip(created, [item[1] for item in chunk]):
        has_radio = any([
            pm.rsrp, pm.rsrq, pm.sinr, pm.rssi, pm.rscp, pm.ecio,
            pm.dl_throughput_kbps, pm.ul_throughput_kbps,
        ])
        if has_radio or pm.technology:
            radio_rows.append(RadioMeasurement(
                measurement=m_obj,
                technology=pm.technology,
                rssi=pm.rssi,
                rscp=pm.rscp,
                ecio=pm.ecio,
                rsrp=pm.rsrp,
                rsrq=pm.rsrq,
                sinr=pm.sinr,
                cqi=pm.cqi,
                dl_throughput_kbps=pm.dl_throughput_kbps,
                ul_throughput_kbps=pm.ul_throughput_kbps,
                raw_data=pm.raw_data,
            ))

        if pm.service_type:
            service_rows.append(ServiceMeasurement(
                measurement=m_obj,
                service_type=pm.service_type,
                outcome=pm.service_outcome or 'SUCCESS',
                call_setup_time_ms=pm.call_setup_time_ms,
                call_duration_s=pm.call_duration_s,
                mos=pm.mos,
                throughput_kbps=pm.throughput_kbps,
                latency_ms=pm.latency_ms,
                packet_loss_pct=pm.packet_loss_pct,
            ))

    if radio_rows:
        RadioMeasurement.objects.bulk_create(radio_rows, ignore_conflicts=True)
    if service_rows:
        ServiceMeasurement.objects.bulk_create(service_rows)

    return len(created)


def _resolve_test_device(device_info: dict):
    """
    Get or create TestDevice / DeviceModel / DeviceManufacturer from parsed device_info dict.
    Returns a TestDevice instance, or None when there is insufficient data.
    """
    from drive_test.models import TestDevice, DeviceModel, DeviceManufacturer

    model_name = (device_info.get('model_name') or '').strip()
    manufacturer_name = (device_info.get('manufacturer') or '').strip()
    imei = (device_info.get('imei') or '').strip()
    label = (device_info.get('label') or '').strip()
    imsi = (device_info.get('imsi') or '').strip()
    sim_msisdn = (device_info.get('sim_msisdn') or '').strip()

    # Need at least one identifier
    serial = imei or model_name
    if not serial:
        return None

    try:
        # DeviceManufacturer
        if manufacturer_name:
            manufacturer, _ = DeviceManufacturer.objects.get_or_create(
                name=manufacturer_name,
                defaults={'name': manufacturer_name},
            )
        else:
            manufacturer, _ = DeviceManufacturer.objects.get_or_create(name='Unknown')

        # DeviceModel
        model_name_key = model_name or 'Unknown'
        device_model, _ = DeviceModel.objects.get_or_create(
            manufacturer=manufacturer,
            model_name=model_name_key,
        )

        # TestDevice (keyed by serial_number = IMEI when available, else model name)
        test_device, created = TestDevice.objects.get_or_create(
            serial_number=serial,
            defaults={
                'device_model': device_model,
                'imei': imei,
                'label': label or model_name_key,
                'sim_imsi': imsi,
                'sim_msisdn': sim_msisdn,
            },
        )
        if not created:
            # Keep device_model, imei, label current if they were empty
            updated_fields = []
            if not test_device.imei and imei:
                test_device.imei = imei
                updated_fields.append('imei')
            if not test_device.label and (label or model_name_key):
                test_device.label = label or model_name_key
                updated_fields.append('label')
            if updated_fields:
                test_device.save(update_fields=updated_fields)

        return test_device
    except Exception:
        logger.exception('Failed to resolve TestDevice from device_info=%s', device_info)
        return None


def _update_session_aggregates(session) -> None:
    """Refresh total_measurements and matched_measurements on the session."""
    from drive_test.models import Measurement
    from django.db.models import Count, Q

    agg = Measurement.objects.filter(drive_file__session=session).aggregate(
        total=Count('id'),
        matched=Count('id', filter=Q(matched_cell__isnull=False)),
    )
    session.total_measurements = agg['total'] or 0
    session.matched_measurements = agg['matched'] or 0

    # Update session status based on file statuses
    from drive_test.models import DriveTestFile
    statuses = set(session.files.values_list('status', flat=True))
    if all(s == 'COMPLETED' for s in statuses):
        session.status = 'COMPLETED'
    elif any(s == 'FAILED' for s in statuses):
        session.status = 'PARTIAL' if any(s == 'COMPLETED' for s in statuses) else 'FAILED'
    elif any(s in ('PARSING', 'MATCHING', 'NORMALIZING') for s in statuses):
        session.status = 'PROCESSING'

    session.save(update_fields=['total_measurements', 'matched_measurements', 'status'])
