"""Lawful-intercept access-control decorator.

A user must have ``can_lawful_intercept=True`` to see LEA pages or run
queries.  Every successful gated access writes an entry to
``core.AuditLog``.
"""
from functools import wraps

from django.http import HttpResponseForbidden

from core.models import AuditLog


def lawful_intercept_required(view_func):
    """Block non-LEA users with 403 + write an audit log on every hit."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('Authentication required.')
        if not getattr(user, 'can_lawful_intercept', False):
            return HttpResponseForbidden(
                'This area is restricted to authorised lawful-intercept officers.'
            )
        # Audit the access (defensive: must never block the view).
        try:
            AuditLog.objects.create(
                user=user,
                action=_action_for(request),
                entity_type='LEAAccess',
                entity_id=request.path,
                description=f'{request.method} {request.path}',
                ip_address=_client_ip(request),
            )
        except Exception:
            pass
        return view_func(request, *args, **kwargs)
    return _wrapped


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _action_for(request) -> str:
    """Map the request to the most accurate AuditLog action choice."""
    path = request.path
    if request.method != 'GET':
        # Mutations: execute, export, save, delete
        if '/execute/' in path:
            return 'LEA_QUERY_EXECUTED'
        if '/export/' in path:
            return 'LEA_EXPORT'
        return 'LEA_QUERY_EXECUTED'
    # GETs: file downloads vs read-only browsing
    if '/extraction/' in path and '/download/' in path:
        return 'LEA_EXPORT'
    return 'LEA_REQUEST_OPENED'
