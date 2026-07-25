"""
UMP Mediation System - Django Settings
=======================================
Single settings file for development. Split into base/dev/prod when deploying.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-dev-only-change-in-production-ump2026!'
)

DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver,0.0.0.0').split(',')

# =============================================================================
# APPLICATION DEFINITION
# =============================================================================

INSTALLED_APPS = [
    # Django built-in
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    # Third party
    'rest_framework',
    'django_celery_beat',

    # Project apps
    'core',
    'collection',
    'streams.msc',
    'streams.ims',
    'streams.pgw',
    'streams.sgsn',
    'streams.sgw',
    'streams.cbs',
    'processing',
    'reference',
    'interconnect',
    'regulatory',
    'roaming',
    'dashboard',
    'api',
    'portals',
    'scripts',
    'businesslogic',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.OperatorContextMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.operators',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# =============================================================================
# DATABASE
# =============================================================================

# Per-service database split (see C:\Users\…\plans\sprightly-launching-bachman.md).
# All four DBs share host/user/password via env vars; only NAME differs.
_DB_ENGINE   = os.environ.get('DB_ENGINE',   'django.db.backends.postgresql')
_DB_USER     = os.environ.get('DB_USER',     'ump_user')
_DB_PASSWORD = os.environ.get('DB_PASSWORD', 'ump_pass_2026')
_DB_HOST     = os.environ.get('DB_HOST',     'localhost')
_DB_PORT     = os.environ.get('DB_PORT',     '5432')


def _db(name_env_var: str, default_name: str) -> dict:
    return {
        'ENGINE': _DB_ENGINE,
        'NAME':   os.environ.get(name_env_var, default_name),
        'USER':   _DB_USER,
        'PASSWORD': _DB_PASSWORD,
        'HOST':   _DB_HOST,
        'PORT':   _DB_PORT,
    }


DATABASES = {
    'default':       _db('DB_NAME',              'ump_mediation'),
    'interconnect':  _db('DB_NAME_INTERCONNECT', 'ump_interconnect'),
    'regulatory':    _db('DB_NAME_REGULATORY',   'ump_regulatory'),
    'roaming':       _db('DB_NAME_ROAMING',      'ump_roaming'),
}

# -----------------------------------------------------------------------------
# Multi-operator data-plane databases
# -----------------------------------------------------------------------------
# Decoded CDR records (streams: msc/ims/pgw/sgsn/sgw/cbs) are isolated per
# operator in a `mediation_{code}` database. The set of operators is declared
# here (env-driven) because DATABASES is built before the DB is reachable — keep
# this list in sync with the reference.Operator registry (provision_operator
# does both). The control plane (auth, collection, reference, portals, core,
# dashboard, …) stays in `default`.
OPERATORS = [
    o.strip().lower()
    for o in os.environ.get('OPERATORS', 'orange,africell,qcell').split(',')
    if o.strip()
]
# Active operator used by the router when no per-request/per-file context is set.
DEFAULT_OPERATOR = os.environ.get('DEFAULT_OPERATOR', OPERATORS[0] if OPERATORS else 'orange')

for _op in OPERATORS:
    DATABASES[f'mediation_{_op}'] = _db(
        f'DB_NAME_MEDIATION_{_op.upper()}', f'ump_mediation_{_op}'
    )

DATABASE_ROUTERS = ['config.db_router.ServiceRouter']

# =============================================================================
# AUTH
# =============================================================================

AUTH_USER_MODEL = 'core.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# =============================================================================
# INTERNATIONALIZATION
# =============================================================================

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = False  # CDR streams store naive timestamps; keep DB + ORM in lock-step.

# =============================================================================
# STATIC & MEDIA FILES
# =============================================================================

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# =============================================================================
# REST FRAMEWORK
# =============================================================================

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_FILTER_BACKENDS': [
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
}

# =============================================================================
# CELERY (async task processing)
# =============================================================================

CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# Default beat schedule (can be overridden via django_celery_beat admin)
# COLLECTION_INTERVAL_SECONDS controls how often input trees are scanned + decoded.
_COLLECTION_INTERVAL = float(os.environ.get('COLLECTION_INTERVAL_SECONDS', '600'))
CELERY_BEAT_SCHEDULE = {
    'poll-sftp-sources': {
        'task': 'collection.tasks.poll_sftp_sources',
        'schedule': 300.0,  # Every 5 minutes
    },
    'scheduled-collection': {
        'task': 'collection.tasks.scheduled_collection',
        'schedule': _COLLECTION_INTERVAL,  # default every 10 minutes
    },
}

# Celery fallback — set USE_CELERY=True when broker is available
USE_CELERY = os.environ.get('USE_CELERY', 'False').lower() in ('true', '1', 'yes')

# =============================================================================
# MEDIATION SETTINGS
# =============================================================================

# Data directories
DATA_DIR = BASE_DIR / 'data'
INCOMING_DIR = DATA_DIR / 'incoming'
DECODED_DIR = DATA_DIR / 'decoded'
PROCESSED_DIR = DATA_DIR / 'processed'
FAILED_DIR = DATA_DIR / 'failed'
ARCHIVE_DIR = DATA_DIR / 'archive'

# Processing
CDR_BATCH_SIZE = 2000         # Records per DB commit batch (was 500, increased for PostgreSQL)
CDR_MAX_RETRIES = 3           # Max processing retries per file
CDR_RAW_DATA_MAX_LEN = 4000  # Max chars for raw_data JSON field

# Persist decoded records to the database?
#   False           — DECODE-ONLY: decode -> render output CSV straight from
#                     memory, NO DB inserts. ~10x faster; the DB write was ~90%
#                     of processing time. (No dashboard data / CDR-pair
#                     correlation while off — those read the DB.)
#   True  (default) — decode -> store records in DB -> dispatch output from DB
#                     (enables dashboards/search/correlation).
# Toggle without code changes:  set env CDR_PERSIST_RECORDS=False
CDR_PERSIST_RECORDS = os.environ.get('CDR_PERSIST_RECORDS', 'True').lower() in (
    '1', 'true', 'yes', 'on',
)

# File upload
FILE_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024  # 100MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 1000  # Support bulk drive test folder uploads (up to 1000 files)
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000  # 1000 files × 2 fields (file + relative_path) + form fields

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
