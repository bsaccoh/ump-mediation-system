# UMP Mediation System — Features

A Django-based CDR mediation platform for Orange Sierra Leone: collects binary/text
CDRs from network elements, decodes them, classifies and enriches records, and
distributes formatted output to downstream systems — with multi-operator and
multi-vendor support.

---

## 1. CDR Collection & Ingestion
- **Manual upload** — web UI file upload (single or folder) with allowed-extension filtering.
- **SFTP polling** — scheduled pull from remote `DataSource`s (per-source host/credentials/glob/interval).
- **Local directory collection** — `collect_local` scans the per-operator input tree.
- **CDRFile tracking** — every file recorded with status (PENDING / PROCESSING / COMPLETED / FAILED / DUPLICATE), record counts, content hash, timestamps, and operator / vendor / network-element tags.
- **Duplicate detection** — by SHA-256 content hash, both within a batch and across prior runs; duplicates are flagged (`status=DUPLICATE`) and moved to a per-operator `duplicates/` directory.
- **Filename classification** — filename → operator + vendor + network element + decoder, driven by configurable `SourcePattern` rules (substring or regex, priority-ordered).

## 2. Decoders (multi-vendor / multi-network-element)
- **MSC** — Huawei MSOFTX3000 ASN.1/BER (voice + SMS; ~26 record types).
- **IMS** — Huawei ATS9900 (VoLTE / VoBB).
- **PGW / SGSN / SGW** — 3GPP PS-domain data CDRs.
- **CBS / OCS** — Huawei charging records (pipe-delimited).
- Shared primitives: TBCD / GSM address / BCD-timestamp / location decoding and CAMEL IN-trigger parsing.

## 3. Processing Pipeline
- Orchestrated **decode → create → validate → enrich → normalize → output**.
- **Decode-only mode** (default, `CDR_PERSIST_RECORDS=False`) — renders output files with **no DB writes** (~10× faster; the DB insert was the bottleneck).
- **Parallel batch processing** (`process_batch`) — multiprocessing pool (one worker per core); skip-already-done by hash; archive-on-success; `--reprocess` / `--no-archive` flags.
- **Prepaid / postpaid classification**
  - MSC: from CAMEL serviceKey / camelPhase (MOC, SMSMO, CF), **gated to home subscribers** — international/foreign originators are left blank, not falsely POSTPAID.
  - PGW / SGSN / SGW: from 3GPP `chargingCharacteristics` P-flag (bit 3 of octet 1).
- Validation rules, enrichment rules, business rules, and custom Python scripts.
- CDR-pair correlation (MOC↔MTC, ORIG↔TERM) when persistence is enabled.

## 4. Multi-Operator / Multi-Vendor
- **Operator registry** — per-operator home identity (home PLMN / MCC / MNC, country code, enabled).
- **Per-operator databases** — decoded records isolated per operator in `mediation_{code}`; the home operator (`DEFAULT_OPERATOR`) uses `default`; provisioned with `provision_operator <code>`.
- **Per-operator directory layout** — `{operator}/{input|output}/{vendor}/{network_element}/`; vendor/operator/NE are directory segments only — filenames are never rewritten.
- **Operator context + selector** — request middleware sets the active operator (top-bar dropdown, session-backed) so dashboard/search queries hit the right database.
- **Per-operator home identity** — roaming detection and operator classification are correct for each operator (no hardcoded Orange identity).
- **UI configuration** — Operators and Source Patterns pages (plus Django admin).

## 5. Output & Distribution
- **Output portals** — LOCAL / SFTP / FTP / API destinations; CSV / JSON / XML / TEXT / RAW formats.
- **Output schemas** — configurable column **field mapping** + header names, delimiter, quoting, line terminator.
- **Distribution rules** — route by stream/operator with **filters** (e.g. postpaid-only), priority and retry policy.
- **In-memory filtering** — per-downstream record filtering in decode-only mode, so each consumer (e.g. BigData vs IPACS) gets exactly its subset.
- **Per-operator output tree**, original filenames preserved, atomic temp-then-replace writes.
- **Output record-type allow-list** (MSC default: CF, GWI, GWO, MOC, MTC, SMSMT, SMSMO; configurable via `MSC_OUTPUT_RECORD_TYPES`).
- Distribution logs / delivery audit trail.
- Directory placeholders: `{operator} {vendor} {ne} {stream} {portal} {YYYY} {MM} {DD}`.

## 6. Reference Data Management (UI)
CRUD pages with search (and CSV import where applicable) for:
- **Operators**, **Source Patterns** (filename classification rules), MCC/MNC, IMSI prefixes, Numbering Plan, Trunk Groups, Vendors.

## 7. Business Modules
- **Interconnect billing** — partners, rates, billing cycles, invoices, exchange rates, receivables ageing.
- **Regulatory (NatCA Compliance)** — NATCOM/NatCA periodic reports, Levy & USF computation, Retail revenue entry, Lawful Intercept (LEA), QoS/KPI snapshots, **Network Performance Monitoring (12 PM KPIs)**, and **Drive Test Management (11 Drive Test metrics)** with interactive Leaflet.js signal mapping & multi-format support (`.trp`, `.lpg`, `.nmf`, `.csv`, `.zip`, `.tar.gz`).
- **Roaming** — inbound-roamer detection, per-partner roaming settlement files, disputes.

## 8. Dashboards & Analytics
- **Processing Volume** — files/records by operator, stream and service type (works in decode-only mode, sourced from `CDRFile`).
- **KPI dashboard** — call records by type, incoming/outgoing/transit, data usage by technology (GB), subscriber growth, inter-operator traffic, call drop rate, international traffic trend; each chart has a per-chart date filter.
- Processing queue, unified CDR search + detail, subscriber view, file registry, traffic matrix, interconnect reports.

## 9. Platform / Operations
- **Background jobs** — Celery tracked tasks + `JobRecord`; `/jobs/` page with live status polling.
- **Scheduled collection** — Celery-beat task (`scheduled_collection`, interval via `COLLECTION_INTERVAL_SECONDS`) plus a **"Run collection now"** dashboard button (runs `process_batch` in a detached subprocess — no nested multiprocessing).
- **Per-service + per-operator database routing** via `config.db_router.ServiceRouter`; cross-DB FKs use `db_constraint=False`.
- Authentication (login, users/roles), audit logging, alarms/alerts.
- **Management commands:** `process_batch`, `collect_local`, `clear_cdr`, `provision_operator`, `seed_operators`, `backfill_prepaid_flag`, demo seeders.
- **Tests:** run with `python manage.py test --settings=config.test_settings` (SQLite in-memory + eager Celery — no Postgres/Redis required).

---

## Key directory layout
```
data/{operator}/input/{vendor}/{ne}/      # incoming CDR files (original names)
data/{operator}/output/{vendor}/{ne}/...  # decoded output (per downstream)
data/{operator}/archive/{vendor}/{ne}/    # processed inputs
data/{operator}/duplicates/{vendor}/{ne}/ # duplicate inputs
```

## Onboarding a new operator
1. Add the operator on the **Operators** page (or `seed_operators`).
2. Add its filename rules on the **Source Patterns** page.
3. Add `OPERATORS=...` env entry and run `python manage.py provision_operator <code>`.
