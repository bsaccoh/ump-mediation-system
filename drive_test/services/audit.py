"""Audit helper — writes drive-test actions into the platform-wide
``core.AuditLog``. There is never a second audit system.
"""
import logging

logger = logging.getLogger('drive_test')


def _client_ip(request):
    if request is None:
        return None
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_action(user, action, entity_type, entity_id='', description='',
               request=None, **extra):
    """Record one auditable action.

    ``action`` is one of core.AuditLog.ACTION_CHOICES
    (CREATE/UPDATE/DELETE/UPLOAD/PROCESS/EXPORT). Failures to audit must never
    break the user's action, so this swallows and logs its own errors.
    """
    from core.models import AuditLog

    try:
        AuditLog.objects.create(
            user=user if getattr(user, 'is_authenticated', False) else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else '',
            description=description,
            ip_address=_client_ip(request),
            extra_data=extra or {},
        )
    except Exception:  # pragma: no cover - audit must not break the request
        logger.exception('Failed to write drive_test audit entry (%s %s)',
                          action, entity_type)
