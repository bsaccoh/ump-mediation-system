"""Test settings override.

Inherits from the main settings but swaps in an in-memory SQLite database so
the Postgres user doesn't need ``CREATE DATABASE`` permission to run the
test suite locally.  Migrations still run, so any test-only schema mismatch
will surface immediately.

Run with::

    python manage.py test --settings=config.test_settings
"""
from .settings import *  # noqa: F401,F403

# Multi-DB mirror of production: one SQLite per service for test isolation.
# The ServiceRouter from config.db_router routes app queries to the right alias.
def _sqlite_test_db():
    return {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'TEST': {'NAME': ':memory:'},
    }


DATABASES = {
    'default':      _sqlite_test_db(),
    'interconnect': _sqlite_test_db(),
    'regulatory':   _sqlite_test_db(),
    'roaming':      _sqlite_test_db(),
}

# Per-operator mediation databases (mirror the dynamic aliases settings.py
# builds from OPERATORS) so the router can resolve mediation_{op} in tests.
for _op in OPERATORS:  # noqa: F405  (OPERATORS comes from `from .settings import *`)
    DATABASES[f'mediation_{_op}'] = _sqlite_test_db()

# Skip Celery and external broker concerns in tests
USE_CELERY = False
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Keep DB persistence ON in tests so the DB-backed pipeline stays covered
# (production runs default to decode-only via CDR_PERSIST_RECORDS=False).
CDR_PERSIST_RECORDS = True

# Faster password hashing in tests
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
