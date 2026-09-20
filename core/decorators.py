from django.contrib.auth.decorators import user_passes_test


def _check(test_fn, login_url=None):
    return user_passes_test(test_fn, login_url=login_url)


staff_required = _check(lambda u: u.is_active and (u.is_staff or u.is_superuser))

operator_required = _check(
    lambda u: u.is_active and (u.is_superuser or u.is_staff or getattr(u, 'is_operator', False)),
)

analyst_required = _check(
    lambda u: u.is_active and (u.is_superuser or u.is_staff or getattr(u, 'is_analyst', False)),
)

lea_required = _check(
    lambda u: u.is_active and (u.is_superuser or getattr(u, 'can_lawful_intercept', False)),
)

superuser_required = _check(lambda u: u.is_active and u.is_superuser)

regulator_required = _check(
    lambda u: u.is_active and (
        u.is_superuser or getattr(u, 'is_regulator', False)
        or getattr(u, 'is_regulatory_admin', False)
    ),
)

auditor_required = _check(
    lambda u: u.is_active and (
        u.is_superuser or getattr(u, 'is_auditor', False)
        or getattr(u, 'is_regulatory_admin', False)
    ),
)

regulatory_admin_required = _check(
    lambda u: u.is_active and (
        u.is_superuser or getattr(u, 'is_regulatory_admin', False)
    ),
)
