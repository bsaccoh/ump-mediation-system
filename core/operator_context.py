"""Thread-local "active operator" used to route data-plane queries.

The ServiceRouter reads :func:`get_operator` to decide which ``mediation_{op}``
database a decoded-record query (msc/ims/pgw/sgsn/sgw/cbs) hits. The supervisor
sets it from ``CDRFile.operator_code`` while processing a file; a web request
sets it from the selected operator (Phase 5 middleware). When nothing is set it
falls back to ``settings.DEFAULT_OPERATOR`` so shells, admin, and migrations
still resolve to a real database.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

from django.conf import settings

_state = threading.local()


def set_operator(code: str | None) -> None:
    _state.operator = (code or '').lower() or None


def get_operator() -> str:
    """Active operator code, or the configured default."""
    code = getattr(_state, 'operator', None)
    if code:
        return code
    return getattr(settings, 'DEFAULT_OPERATOR', 'orange')


def clear_operator() -> None:
    _state.operator = None


def all_operator_codes() -> list[str]:
    """Every configured operator code (for cross-operator aggregate views)."""
    return list(getattr(settings, 'OPERATORS', []))


@contextmanager
def operator_context(code: str | None):
    """Set the active operator for the duration of the block."""
    previous = getattr(_state, 'operator', None)
    _state.operator = (code or '').lower() or None
    try:
        yield
    finally:
        _state.operator = previous
