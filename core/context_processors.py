"""Template context: expose the operator list + active operator for the selector."""
from django.conf import settings

from core.middleware import resolve_active_operator


def operators(request):
    return {
        'OPERATORS': settings.OPERATORS,
        'ACTIVE_OPERATOR': resolve_active_operator(request),
    }
