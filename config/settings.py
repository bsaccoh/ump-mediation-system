"""
UMP Mediation System - Django Settings
=======================================
Single settings file for development. Split into base/dev/prod when deploying.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-only-not-for-production'
    else:
        raise ImproperlyConfigured(
            'DJANGO_SECRET_KEY environment variable is required. '
            'Generate one with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"'
        )

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')

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
    'dashboard',
    'api',
    'portals',
    'scripts',
    'businesslogic',
    'regulatory',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
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
if DEBUG:
    _DB_ENGINE   = os.environ.get('DB_ENGINE',   'django.db.backends.sqlite3')
    _DB_USER     = os.environ.get('DB_USER',     '')
    _DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
    _DB_HOST     = os.environ.get('DB_HOST',     '')
    _DB_PORT     = os.environ.get('DB_PORT',     '')
else:
    _DB_ENGINE   = os.environ.get('DB_ENGINE',   'django.db.backends.postgresql')
    _DB_USER     = os.environ.get('DB_USER',     'ump_user')
    _DB_PASSWORD = os.environ.get('DB_PASSWORD')
    if not _DB_PASSWORD:
        raise ImproperlyConfigured('DB_PASSWORD environment variable is required')
    _DB_HOST     = os.environ.get('DB_HOST',     'localhost')
    _DB_PORT     = os.environ.get('DB_PORT',     '5432')


def _db(name_env_var: str, default_name: str) -> dict:
    name = os.environ.get(name_env_var, default_name)
    if _DB_ENGINE.endswith('sqlite3'):
        name = str(BASE_DIR / f'{name}.sqlite3')
    return {
        'ENGINE': _DB_ENGINE,
        'NAME':   name,
        'USER':   _DB_USER,
        'PASSWORD': _DB_PASSWORD,
        'HOST':   _DB_HOST,
        'PORT':   _DB_PORT,
        'CONN_MAX_AGE': 600,
        'CONN_HEALTH_CHECKS': True,
    }


DATABASES = {
    'default':       _db('DB_NAME',              'ump_mediation'),
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
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# =============================================================================
# SECURITY (production hardening when DEBUG=False)
# =============================================================================

if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'True').lower() in ('true', '1', 'yes')
    SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
    CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
    SECURE_HSTS_SECONDS = 31536000 if SECURE_SSL_REDIRECT else 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_SSL_REDIRECT
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True

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

# Tariff compliance classification is service-driven, not UI-driven.
TARIFF_COMPLIANCE_TOLERANCE_PCT = os.environ.get('TARIFF_COMPLIANCE_TOLERANCE_PCT', '1.00')

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
    'evaluate-risk-rules': {
        'task': 'regulatory.tasks.evaluate_risk_rules_task',
        'schedule': 1800.0,  # Every 30 minutes
    },
    'rescue-stuck-files': {
        'task': 'collection.tasks.rescue_stuck_files',
        'schedule': 60.0,  # Every minute — re-queues COLLECTED files stuck >2 min
    },
}

# Celery fallback — set USE_CELERY=True when broker is available
USE_CELERY = os.environ.get('USE_CELERY', 'False').lower() in ('true', '1', 'yes')

# =============================================================================
# CACHING
# =============================================================================

_CACHE_URL = os.environ.get('CACHE_URL', '')
if _CACHE_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': _CACHE_URL,
            'TIMEOUT': 300,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'TIMEOUT': 300,
        }
    }

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

# Canonical mediation storage layout.  These values deliberately remain
# environment-configurable so development and production use the same code
# without embedding production mount points in the application.
UMP_STORAGE_ROOT = Path(os.environ.get('UMP_STORAGE_ROOT', str(DATA_DIR)))
UMP_INPUT_ROOT = Path(os.environ.get(
    'UMP_INPUT_ROOT', str(UMP_STORAGE_ROOT / 'landing' / 'input')
))
UMP_PROCESSING_ROOT = Path(os.environ.get(
    'UMP_PROCESSING_ROOT', str(UMP_STORAGE_ROOT / 'processing')
))
UMP_ARCHIVE_ROOT = Path(os.environ.get(
    'UMP_ARCHIVE_ROOT', str(UMP_STORAGE_ROOT / 'archive')
))
UMP_OUTPUT_ROOT = Path(os.environ.get(
    'UMP_OUTPUT_ROOT', str(UMP_STORAGE_ROOT / 'landing' / 'output')
))
UMP_ERROR_ROOT = Path(os.environ.get(
    'UMP_ERROR_ROOT', str(UMP_STORAGE_ROOT / 'error')
))
UMP_QUARANTINE_ROOT = Path(os.environ.get(
    'UMP_QUARANTINE_ROOT', str(UMP_STORAGE_ROOT / 'quarantine')
))
UMP_APPROVED_SCRIPTS_ROOT = Path(os.environ.get(
    'UMP_APPROVED_SCRIPTS_ROOT', str(BASE_DIR / 'approved_scripts')
))
UMP_SCRIPT_TIMEOUT_SECONDS = int(os.environ.get('UMP_SCRIPT_TIMEOUT_SECONDS', '60'))

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
CDR_PERSIST_RECORDS = os.environ.get('CDR_PERSIST_RECORDS', 'False').lower() in (
    '1', 'true', 'yes', 'on',
)

# =============================================================================
# REGULATORY TAP
# =============================================================================
# Controls whether the NatCA/NRA aggregation tap runs after dispatch.
#
# REGULATORY_TAP_ENABLED — default False (off in production).
#   Set to True only in test/staging environments where NatCA/NRA data is
#   needed.  At scale (100k+ files/day, 60M+ records/day) this must stay
#   False; analytics will be served by ClickHouse in a separate pipeline.
#
# When True, the tap always runs ASYNC (background thread) so it NEVER
# adds latency to the decode → dispatch pipeline.
REGULATORY_TAP_ENABLED = os.environ.get('REGULATORY_TAP_ENABLED', 'False').lower() in (
    '1', 'true', 'yes', 'on',
)

# =============================================================================
# SERVICE MODE
# =============================================================================
# When True, the pipeline runs as independent systemd services:
#   mediation-collector  → scans input dirs, registers CDRFile(COLLECTED)
#   mediation-decoder    → picks up COLLECTED files, decodes → DECODED
#   mediation-distributor→ picks up DECODED files, dispatches → COMPLETED
#   mediation-api        → Gunicorn web dashboard + REST API
#
# When False (legacy), creating a CDRFile(PENDING) auto-triggers processing
# via the post_save signal (monolithic mode).
SERVICE_MODE = os.environ.get('SERVICE_MODE', 'False').lower() in (
    '1', 'true', 'yes', 'on',
)

# Polling interval for service daemons (seconds)
SERVICE_POLL_INTERVAL = int(os.environ.get('SERVICE_POLL_INTERVAL', '10'))

# File upload
FILE_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024  # 100MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 1000  # Support bulk drive test folder uploads (up to 1000 files)
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000  # 1000 files × 2 fields (file + relative_path) + form fields

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# =============================================================================
# LOGGING — per-service log files
# =============================================================================
# Log directory: /opt/ump/logs on production, BASE_DIR/logs locally.
LOG_DIR = os.environ.get('LOG_DIR', str(BASE_DIR / 'logs'))
os.makedirs(LOG_DIR, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'service': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'service',
        },
        'file_collection': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOG_DIR, 'collection.log'),
            'maxBytes': 50 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'service',
        },
        'file_decoder': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOG_DIR, 'decoder.log'),
            'maxBytes': 50 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'service',
        },
        'file_distributor': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOG_DIR, 'distributor.log'),
            'maxBytes': 50 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'service',
        },
        'file_api': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOG_DIR, 'api.log'),
            'maxBytes': 50 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'service',
        },
    },
    'loggers': {
        'mediation.collector': {
            'handlers': ['console', 'file_collection'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'mediation.decoder': {
            'handlers': ['console', 'file_decoder'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'mediation.distributor': {
            'handlers': ['console', 'file_distributor'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'mediation.api': {
            'handlers': ['console', 'file_api'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'django': {
            'handlers': ['console', 'file_api'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}
