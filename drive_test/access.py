"""RBAC gates for the Drive Test Intelligence module.

These compose the existing ``core.User`` role flags — they do NOT introduce a
parallel auth system, and they never touch the three shared decorators in
``core.decorators`` (which also gate the regulatory app).

- ``dt_view_required``   — any drive-test user: analyst (RF engineer), regulator,
  auditor, operator, or staff/superuser.
- ``dt_manage_required`` — can create/modify projects, campaigns, upload files:
  operator, analyst, or staff/superuser.
"""
from django.contrib.auth.decorators import user_passes_test


def _view(u):
    return u.is_active and (
        u.is_superuser or u.is_staff
        or getattr(u, 'is_analyst', False)
        or getattr(u, 'is_regulator', False)
        or getattr(u, 'is_regulatory_admin', False)
        or getattr(u, 'is_auditor', False)
        or getattr(u, 'is_operator', False)
    )


def _manage(u):
    return u.is_active and (
        u.is_superuser or u.is_staff
        or getattr(u, 'is_analyst', False)
        or getattr(u, 'is_operator', False)
    )


dt_view_required = user_passes_test(_view)
dt_manage_required = user_passes_test(_manage)


def is_regulator(user) -> bool:
    """True for a regulator-first landing (vs the RF-engineer default)."""
    return bool(
        getattr(user, 'is_regulator', False)
        or getattr(user, 'is_regulatory_admin', False)
        or getattr(user, 'is_auditor', False)
    )
