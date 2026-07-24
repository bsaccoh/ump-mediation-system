# UMP Mediation System

A Django-based **Usage & Mediation Platform (UMP)** for telecom CDR (Call Detail
Record) processing. It collects binary/text CDRs from network elements, decodes
them, classifies/enriches records, and distributes formatted output to downstream
systems — with **multi-operator** and **multi-vendor** support.

> Full feature list: see **[FEATURES.md](FEATURES.md)**.

---

## What it does

```
collect → classify → decode → (validate/enrich) → render → distribute
```

Supported network elements / decoders:

| Stream | Source | Notes |
|--------|--------|-------|
| **MSC**  | Huawei MSOFTX3000 (ASN.1/BER) | Voice + SMS |
| **IMS**  | Huawei ATS9900 | VoLTE / VoBB |
| **PGW**  | 3GPP PS-domain | 4G data |
| **SGSN** | 3GPP PS-domain | 2G/3G data |
| **SGW**  | 3GPP PS-domain | 4G S-GW |
| **CBS / OCS** | Huawei charging | Pipe-delimited |

## Key features

- **Multi-stream decoding** with a dedicated handler per network element.
- **Multi-operator / multi-vendor** — per-operator databases, directory layout, home identity, and filename-driven classification.
- **Decode-only mode (default)** — render output files with no DB writes (~10× faster); flip on persistence for dashboards/search.
- **Parallel batch processing** across CPU cores, with hash-based duplicate detection and archive-on-success.
- **Configurable output** — portals, field-mapping schemas, and distribution rules with per-downstream filters.
- **Prepaid/postpaid classification** from CAMEL (MSC) and chargingCharacteristics (PGW/SGSN/SGW), gated to home subscribers.
- **Collection** via manual upload, SFTP polling, local folder scan; scheduled or one-click UI trigger.
- **Dashboards** (processing volume + 7 KPI charts), background jobs, audit logging, REST API.

## Technology stack

- **Backend:** Django + Django REST Framework
- **Database:** PostgreSQL (per-service + per-operator); SQLite in-memory for tests
- **Task queue:** Celery + Redis (optional — sync/subprocess fallback when absent)
- **Frontend:** Django templates + Bootstrap 5 + Chart.js

## Apps

| App | Purpose |
|-----|---------|
| `core` | Base models, users, audit, JobRecord, operator context, DB router |
| `collection` | File ingestion (upload/SFTP/local), CDRFile tracking, dispatch, duplicates |
| `streams/{msc,ims,pgw,sgsn,sgw,cbs}` | Per-element decoders + processors |
| `processing` | Validation / enrichment rules |
| `businesslogic` | Business rule engine + scripts |
| `reference` | Operators, Source Patterns, MCC/MNC, IMSI, numbering, trunks, vendors |
| `portals` | Input/Output portals, output schemas, distribution rules |
| `interconnect` | Interconnect partners, rates, billing cycles, invoices |
| `regulatory` | NATCOM reports + LEA extraction |
| `roaming` | Inbound-roamer detection, roaming settlement files |
| `dashboard` | Web UI, KPIs, search, processing queue |
| `api` | REST API endpoints |

## Quick start

```bash
# 1. Install dependencies
pip install django djangorestframework django-celery-beat redis psycopg2-binary

# 2. Configure the database via env vars (see below), then migrate
python manage.py migrate

# 3. Seed operators + classification patterns
python manage.py seed_operators

# 4. (per additional operator) create + migrate its database
python manage.py provision_operator africell

# 5. Run the dev server
python manage.py runserver 0.0.0.0:8000

# 6. (optional) Celery worker + beat for async + scheduled collection
celery -A config worker -l info
celery -A config beat   -l info
```

Default admin: create one with `python manage.py createsuperuser`.

## Processing CDRs

```bash
# Drop files into the per-operator input tree:
#   data/<operator>/input/<vendor>/<network_element>/<file>.dat

# Decode everything in parallel (decode-only, skip-done, archive-on-success):
python manage.py process_batch --workers 8

# Or from the UI: dashboard → "Run collection now"
# Or scheduled: Celery beat task `scheduled_collection`
```

Output lands in `data/<operator>/output/<vendor>/<network_element>/<downstream>/`.

## Directory layout

```
data/{operator}/input/{vendor}/{ne}/        # incoming CDR files (original names)
data/{operator}/output/{vendor}/{ne}/...    # decoded output, per downstream
data/{operator}/archive/{vendor}/{ne}/      # processed inputs
data/{operator}/duplicates/{vendor}/{ne}/   # duplicate inputs
```

## Configuration (UI)

- **Operators** — `/reference/operators/` (or `seed_operators`)
- **Source Patterns** (filename → operator/vendor/NE/decoder) — `/reference/source-patterns/`
- **Input / Output Portals**, schemas, rules — `/portals/`
- Django admin — `/admin/`

## Environment variables

| Variable | Purpose |
|----------|---------|
| `DJANGO_SECRET_KEY` | Django secret key |
| `DJANGO_DEBUG` | Debug mode (`True`/`False`) |
| `DB_ENGINE` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` | Database config |
| `DB_NAME_INTERCONNECT` / `DB_NAME_REGULATORY` / `DB_NAME_ROAMING` | Per-service DB names |
| `OPERATORS` | Comma-separated operator codes (default `orange,africell,qcell`) |
| `DEFAULT_OPERATOR` | Home operator that uses the `default` DB (default `orange`) |
| `DB_NAME_MEDIATION_{CODE}` | Override a per-operator DB name (default `ump_mediation_{code}`) |
| `CDR_PERSIST_RECORDS` | Store decoded records in the DB (default `False` = decode-only) |
| `MSC_OUTPUT_RECORD_TYPES` | Comma-separated MSC types to keep in output |
| `COLLECTION_INTERVAL_SECONDS` | Scheduled-collection interval (default `600`) |
| `CELERY_BROKER_URL` / `USE_CELERY` | Celery broker + enable async |

## Management commands

| Command | Purpose |
|---------|---------|
| `process_batch` | Parallel decode of input trees (dedup + archive) |
| `collect_local` | Register files found in the input tree |
| `clear_cdr [--files]` | Wipe records (and optionally data dirs) for testing |
| `provision_operator <code> [--all]` | Create + migrate an operator's database |
| `seed_operators` | Seed operators + classification patterns |
| `backfill_prepaid_flag` | Recompute prepaid flag from CAMEL on stored records |

## Testing

```bash
python manage.py test --settings=config.test_settings
```
Uses in-memory SQLite + eager Celery, so no Postgres/Redis is required.

## License

Private project — All rights reserved.
