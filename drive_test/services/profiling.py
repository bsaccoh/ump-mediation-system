"""Pre-ingest profiling — detect a file's format and summarise its content
without committing anything to the sample store.
"""
from __future__ import annotations

import logging

from drive_test.models.enums import FileStatus, Technology, ValidationStatus
from drive_test.parsers import detect_parser, detect_format

logger = logging.getLogger('drive_test')

_VALID_TECHS = {t.value for t in Technology}


def profile_file(dtf) -> dict:
    """Profile one DriveTestFile in place; persist the summary and return it."""
    path = dtf.stored_path
    fmt, confidence = detect_format(path)
    parser = detect_parser(path)

    if parser is None:
        dtf.detected_format = fmt
        dtf.validation_status = ValidationStatus.INVALID
        dtf.status = FileStatus.FAILED
        dtf.error_message = 'Unsupported or unrecognised file format.'
        dtf.profile = {'format': fmt, 'confidence': confidence}
        dtf.save(update_fields=['detected_format', 'validation_status', 'status',
                                'error_message', 'profile'])
        return dtf.profile

    try:
        summary = parser.profile(path)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception('Profiling failed for %s', path)
        dtf.validation_status = ValidationStatus.INVALID
        dtf.status = FileStatus.FAILED
        dtf.error_message = f'Could not read file: {exc}'
        dtf.save(update_fields=['validation_status', 'status', 'error_message'])
        return {'error': str(exc)}

    summary['confidence'] = confidence
    techs = [t for t in summary.get('technologies', []) if t in _VALID_TECHS]

    dtf.detected_format = parser.name
    dtf.detected_technology = techs[0] if len(techs) == 1 else ''
    dtf.gps_available = summary.get('gps_available')
    if not summary.get('truncated'):
        dtf.sample_count = summary.get('sample_count')
    dtf.profile = summary
    dtf.validation_status = (
        ValidationStatus.VALID if summary.get('sample_count') else ValidationStatus.WARNING
    )
    if dtf.status in (FileStatus.PENDING, FileStatus.PROFILING):
        dtf.status = FileStatus.READY
    dtf.save(update_fields=[
        'detected_format', 'detected_technology', 'gps_available', 'sample_count',
        'profile', 'validation_status', 'status',
    ])
    return summary
