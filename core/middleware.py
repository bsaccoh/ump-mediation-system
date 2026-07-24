"""Per-request active-operator middleware.

Reads the operator selected in the session (``active_operator``), validates it
against ``settings.OPERATORS``, and sets the thread-local operator context for
the duration of the request so data-plane queries (dashboard KPIs, CDR search,
record detail) read from that operator's database. Always cleared afterwards so
a pooled worker thread never leaks one request's operator into the next.
"""
from django.conf import settings

from core.operator_context import set_operator, clear_operator

SESSION_KEY = 'active_operator'


def resolve_active_operator(request) -> str:
    code = None
    if hasattr(request, 'session'):
        code = request.session.get(SESSION_KEY)
    if code not in settings.OPERATORS:
        code = settings.DEFAULT_OPERATOR
    return code


class OperatorContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        active = resolve_active_operator(request)
        request.active_operator = active
        set_operator(active)
        try:
            return self.get_response(request)
        finally:
            clear_operator()
