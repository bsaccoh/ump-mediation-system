# UMP Mediation System

A Django-based Usage and Mediation Platform (UMP) for telecommunications CDR (Call Detail Records) processing.

## Summary

This system mediates and processes Call Detail Records from multiple network elements in a telecom environment. It collects, decodes, validates, enriches, and processes CDRs from various network streams including:

- **MSC** (Mobile Switching Center) - Voice and SMS records
- **PGW** (Packet Gateway) - 4G data usage records
- **SGSN** (Serving GPRS Support Node) - 2G/3G data records
- **SGW** (Serving Gateway) - 4G S-GW data records

## Key Features

- **Multi-stream CDR processing** with dedicated handlers for each network element
- **SFTP-based file collection** with automated polling
- **Decoding and validation** of binary CDR formats
- **Data enrichment** through reference data lookups
- **Business rule engine** for custom validation and transformation logic
- **Web dashboard** for monitoring and management
- **REST API** for external integrations
- **Audit logging** and alerting system
- **Asynchronous task processing** via Celery

## Technology Stack

- **Backend**: Django + Django REST Framework
- **Database**: SQLite (configurable for PostgreSQL/MySQL)
- **Task Queue**: Celery with Redis
- **Frontend**: Django Templates + Bootstrap

## Architecture

The system is organized into modular Django apps:

| App | Purpose |
|-----|---------|
| `core` | Base models, user management, audit logs, alerts |
| `collection` | SFTP file collection and ingestion |
| `streams/` | MSC, PGW, SGSN, SGW CDR processing |
| `processing` | Validation and enrichment rules |
| `businesslogic` | Business rule engine |
| `reference` | Reference data management (MCC, MNC, SMSC) |
| `dashboard` | Web UI and monitoring |
| `api` | REST API endpoints |
| `portals` | User portal interfaces |

## Development

```bash
# Install dependencies
pip install django djangorestframework django-celery-beat redis

# Run migrations
python manage.py migrate

# Start development server
python manage.py runserver

# Start Celery worker (optional)
celery -A config worker -l info
```

## Environment Variables

- `DJANGO_SECRET_KEY` - Django secret key
- `DJANGO_DEBUG` - Enable debug mode (True/False)
- `DB_ENGINE` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` - Database config
- `CELERY_BROKER_URL` - Redis broker URL
- `USE_CELERY` - Enable async processing

## License

Private project - All rights reserved.
