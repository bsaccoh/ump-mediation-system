"""Convenience wrapper for writing ActivityLog entries.

Usage:
    from core.activity import log_activity
    log_activity('FILE_REGISTERED', 'COLLECTION', message='Registered foo.dat',
                 stream='MSC', cdr_file=cdr_file)

Never raises — swallows exceptions so callers in the pipeline are never
interrupted by a logging failure.
"""
import logging

logger = logging.getLogger(__name__)


def log_activity(event_type, stage, *, message='', level='INFO',
                 stream='', operator='', cdr_file=None, source=None,
                 details=None):
    try:
        from core.models import ActivityLog
        ActivityLog.objects.create(
            event_type=event_type,
            stage=stage,
            message=message,
            level=level,
            stream=stream,
            operator=operator,
            cdr_file=cdr_file,
            source=source,
            details=details or {},
        )
    except Exception:
        logger.debug('Failed to write activity log', exc_info=True)
