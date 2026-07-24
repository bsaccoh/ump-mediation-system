"""Distribution dispatcher.

Triggered after a CDR file finishes processing. Iterates over every active
DistributionRule that targets the file's stream type, applies the rule's
JSON filter to the record queryset, maps fields per the linked OutputSchema,
renders to the OutputPortal's format (CSV/JSON/XML), and delivers via the
appropriate transport. Each delivery writes a DistributionLog row.
"""
import csv
import io
import json
import logging
import os
import time
from collections import OrderedDict

DELIVERY_MAX_ATTEMPTS = 3              # default; overridable per DistributionRule.max_retries
DELIVERY_BACKOFF_SECONDS = (2, 5)       # default; overridable per DistributionRule.retry_backoff_seconds
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from core.enums import DecoderType
from core.transports import get_transport

logger = logging.getLogger(__name__)


def _get_record_model(decoder_type: str):
    if decoder_type == DecoderType.MSC:
        from streams.msc.models import MSCRecord
        return MSCRecord
    if decoder_type == DecoderType.IMS:
        from streams.ims.models import IMSRecord
        return IMSRecord
    if decoder_type == DecoderType.PGW:
        from streams.pgw.models import PGWRecord
        return PGWRecord
    if decoder_type == DecoderType.SGSN:
        from streams.sgsn.models import SGSNRecord
        return SGSNRecord
    if decoder_type == DecoderType.SGW:
        from streams.sgw.models import SGWRecord
        return SGWRecord
    if decoder_type == DecoderType.CBS:
        from streams.cbs.models import CBSRecord
        return CBSRecord
    return None


def _get_record_queryset(cdr_file):
    model = _get_record_model(cdr_file.decoder_type)
    if model is None:
        return None
    return model.objects.filter(file=cdr_file)


def _apply_filter(qs, filter_kwargs: dict):
    if not filter_kwargs:
        return qs
    return qs.filter(**filter_kwargs)


def _match_lookup(actual, lookup: str, expected) -> bool:
    """Evaluate one Django-style field lookup against an in-memory value."""
    a_str = '' if actual is None else str(actual)
    if lookup in ('', 'exact'):
        return a_str == str(expected)
    if lookup == 'iexact':
        return a_str.lower() == str(expected).lower()
    if lookup == 'in':
        exp = expected if isinstance(expected, (list, tuple, set)) else [expected]
        return a_str in {str(x) for x in exp}
    if lookup == 'contains':
        return str(expected) in a_str
    if lookup == 'icontains':
        return str(expected).lower() in a_str.lower()
    if lookup == 'startswith':
        return a_str.startswith(str(expected))
    if lookup == 'isnull':
        is_null = actual in (None, '')
        return is_null == bool(expected)
    if lookup in ('gt', 'gte', 'lt', 'lte'):
        try:
            an, en = float(actual), float(expected)
        except (TypeError, ValueError):
            return False
        return {'gt': an > en, 'gte': an >= en,
                'lt': an < en, 'lte': an <= en}[lookup]
    # Unknown lookup → be permissive (don't drop records on a filter we can't model).
    return True


def _record_matches(record, filter_kwargs: dict) -> bool:
    """True if an in-memory record satisfies every filter clause (AND).

    Supports single-field lookups (field, field__in, field__icontains, …) read
    from the model instance or its raw_data dict. Used by decode-only dispatch
    so per-rule filters are honoured without a database query.
    """
    for key, expected in filter_kwargs.items():
        field, _, lookup = key.partition('__')
        if not _match_lookup(_resolve(record, field), lookup, expected):
            return False
    return True


def _resolve(record, source_field: str):
    """Read a field from the model, falling back to raw_data dict."""
    if hasattr(record, source_field):
        return getattr(record, source_field)
    raw = getattr(record, 'raw_data', None) or {}
    return raw.get(source_field, '')


_EXCLUDED_DEFAULT_FIELDS = {'id', 'raw_data', 'created_at', 'updated_at', 'file', 'file_id'}


def _default_field_names(record) -> list:
    """All concrete model fields except internal ones, in model declaration order."""
    return [
        f.name for f in record._meta.concrete_fields
        if f.name not in _EXCLUDED_DEFAULT_FIELDS
    ]


def _apply_mapping(record, mapping) -> 'OrderedDict[str, object]':
    """Apply a column mapping to a record.

    Accepts two shapes:

    * **dict** ``{output_header: source_field}`` — legacy.  Column order in
      the rendered CSV follows whatever order PostgreSQL's JSONB returns
      (which is **NOT** insertion order — JSONB sorts by key length then
      alphabetically).  Use the list form below to lock column positions.
    * **list of pairs** ``[[header, source], …]`` — order-preserving.
      JSONB arrays preserve order, so appending a new column at the end
      of the list keeps all existing column positions stable for any
      downstream consumer.

    Empty mapping = dump all model fields in declaration order.
    """
    if not mapping:
        keys = _default_field_names(record)
        return OrderedDict((k, _resolve(record, k)) for k in keys)

    if isinstance(mapping, list):
        pairs = ((p[0], p[1]) for p in mapping if isinstance(p, (list, tuple)) and len(p) >= 2)
    else:
        pairs = mapping.items()
    return OrderedDict((header, _resolve(record, source)) for header, source in pairs)


def _stream_render_csv(queryset, mapping: dict, schema) -> tuple:
    """Stream-render a queryset to CSV bytes without materializing full list.

    Returns:
        (payload_bytes, record_count)
    """
    delimiter = (schema.delimiter or ',') if schema else ','
    include_header = bool(schema.include_header) if schema else True
    quote_all = bool(schema.quote_all) if schema else False
    line_terminator = (schema.line_terminator or '\n').replace('\\n', '\n').replace('\\r', '\r')

    quoting = csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL

    buf = io.StringIO()
    writer = csv.writer(
        buf,
        delimiter=delimiter,
        lineterminator=line_terminator,
        quoting=quoting,
    )

    header_written = False
    record_count = 0

    for rec in queryset.iterator(chunk_size=2000):
        row = _apply_mapping(rec, mapping)
        if not header_written:
            if include_header:
                writer.writerow(list(row.keys()))
            header_written = True
        writer.writerow(['' if v is None else str(v) for v in row.values()])
        record_count += 1

    return buf.getvalue().encode('utf-8'), record_count

def _render_records_csv(records, mapping, schema) -> tuple:
    """Stream-render an in-memory iterable of records (saved or UNSAVED model
    instances) to CSV bytes. Mirrors _stream_render_csv but iterates a plain
    list instead of a queryset — used by decode-only mode (no DB)."""
    delimiter = (schema.delimiter or ',') if schema else ','
    include_header = bool(schema.include_header) if schema else True
    quote_all = bool(schema.quote_all) if schema else False
    line_terminator = (schema.line_terminator or '\n').replace('\\n', '\n').replace('\\r', '\r')
    quoting = csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator=line_terminator, quoting=quoting)
    header_written = False
    record_count = 0
    for rec in records:
        row = _apply_mapping(rec, mapping)
        if not header_written:
            if include_header:
                writer.writerow(list(row.keys()))
            header_written = True
        writer.writerow(['' if v is None else str(v) for v in row.values()])
        record_count += 1
    return buf.getvalue().encode('utf-8'), record_count


def _render_csv(rows, schema) -> bytes:
    delimiter = (schema.delimiter or ',') if schema else ','
    include_header = bool(schema.include_header) if schema else True
    quote_all = bool(schema.quote_all) if schema else False
    line_terminator = (schema.line_terminator or '\n').replace('\\n', '\n').replace('\\r', '\r')
    
    quoting = csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL
    
    buf = io.StringIO()
    if not rows:
        return buf.getvalue().encode('utf-8')
    
    writer = csv.writer(
        buf, 
        delimiter=delimiter, 
        lineterminator=line_terminator,
        quoting=quoting
    )
    
    if include_header:
        writer.writerow(list(rows[0].keys()))
    for r in rows:
        writer.writerow(['' if v is None else str(v) for v in r.values()])
    return buf.getvalue().encode('utf-8')


def _render_text(rows, schema) -> bytes:
    """Simple text dump: each row is joined by delimiter (or space) and ended with terminator."""
    delimiter = (schema.delimiter or ' ') if schema else ' '
    line_terminator = (schema.line_terminator or '\n').replace('\\n', '\n').replace('\\r', '\r')
    
    lines = []
    for r in rows:
        line = delimiter.join('' if v is None else str(v) for v in r.values())
        lines.append(line)
    
    content = line_terminator.join(lines)
    if content:
        content += line_terminator
    return content.encode('utf-8')


def _render_json(rows) -> bytes:
    payload = [{k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in r.items()} for r in rows]
    return json.dumps(payload, default=str).encode('utf-8')


def _render_xml(rows) -> bytes:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<records>']
    for r in rows:
        parts.append('  <record>')
        for k, v in r.items():
            tag = ''.join(c if c.isalnum() or c in '_-' else '_' for c in str(k))
            text = '' if v is None else xml_escape(str(v))
            parts.append(f'    <{tag}>{text}</{tag}>')
        parts.append('  </record>')
    parts.append('</records>')
    return '\n'.join(parts).encode('utf-8')


def _render(rows, output_format: str, schema) -> bytes:
    fmt = (output_format or 'CSV').upper()
    if fmt == 'JSON':
        return _render_json(rows)
    if fmt == 'XML':
        return _render_xml(rows)
    if fmt == 'TEXT':
        return _render_text(rows, schema)
    return _render_csv(rows, schema)


def _build_filename(cdr_file, portal, output_format: str) -> str:
    """Output filename = the ORIGINAL input filename, with the extension swapped
    for the output format. Vendor/operator/network-element appear only in the
    directory path, never in the filename."""
    original = os.path.basename(cdr_file.filename or f'cdr_{cdr_file.pk}')
    base = os.path.splitext(original)[0]
    fmt = (output_format or 'CSV').upper()

    if fmt == 'RAW':
        # Keep original filename/extension for RAW
        return original

    ext = {
        'CSV': 'csv',
        'JSON': 'json',
        'XML': 'xml',
        'TEXT': 'txt'
    }.get(fmt, 'csv')

    return f'{base}.{ext}'


def _stream_matches(rule_stream: str, cdr_stream: str) -> bool:
    return rule_stream in ('ALL', cdr_stream)


def dispatch_cdr_file(cdr_file_id: int) -> list:
    """Run dispatch for a completed CDRFile. Returns a list of summary dicts."""
    from collection.models import CDRFile, DistributionLog
    from portals.models import DistributionRule

    cdr_file = CDRFile.objects.filter(pk=cdr_file_id).first()
    if not cdr_file:
        logger.warning(f'dispatch_cdr_file: CDRFile {cdr_file_id} not found')
        return []

    # Read decoded records from this file's per-operator database. (When called
    # from BaseProcessor the context is already set; this makes the standalone
    # dispatch task self-sufficient too.)
    from core.operator_context import set_operator
    set_operator(cdr_file.operator_code)

    rules = (
        DistributionRule.objects
        .filter(is_active=True)
        .select_related('output_portal', 'output_schema')
        .order_by('priority', 'name')
    )

    summaries = []
    for rule in rules:
        if not _stream_matches(rule.stream_type, cdr_file.decoder_type):
            continue

        portal = rule.output_portal
        schema = rule.output_schema

        if not portal or not portal.is_active:
            DistributionLog.objects.create(
                cdr_file=cdr_file, rule=rule, output_portal=portal,
                filename='', record_count=0, file_size=0,
                status=DistributionLog.Status.SKIPPED,
                error='Output portal is missing or inactive',
            )
            summaries.append({'rule': rule.name, 'status': 'SKIPPED'})
            continue

        try:
            if portal.output_format == 'RAW':
                # RAW format: deliver the original source file directly
                if not os.path.exists(cdr_file.file_path):
                    raise FileNotFoundError(f"Source file not found: {cdr_file.file_path}")
                
                with open(cdr_file.file_path, 'rb') as f:
                    payload = f.read()
                
                filename = _build_filename(cdr_file, portal, 'RAW')
                record_count = cdr_file.records_total
            else:
                # Normal format: filter, map, and render records
                qs = _get_record_queryset(cdr_file)
                if qs is None:
                    logger.info(f'dispatch_cdr_file: no record model for decoder_type={cdr_file.decoder_type}, skipping rule {rule.name}')
                    continue

                filtered = _apply_filter(qs, rule.filter_kwargs())
                mapping = (schema.mapping_json if schema else {}) or {}
                if isinstance(mapping, str):
                    import json
                    try:
                        mapping = json.loads(mapping)
                    except Exception:
                        mapping = {}

                # Stream-render: write rows as we iterate instead of
                # materializing the full list for large files.
                fmt = (portal.output_format or 'CSV').upper()
                if fmt == 'CSV':
                    payload, record_count = _stream_render_csv(
                        filtered, mapping, schema,
                    )
                else:
                    rows = [_apply_mapping(rec, mapping) for rec in filtered.iterator(chunk_size=2000)]
                    payload = _render(rows, portal.output_format, schema)
                    record_count = len(rows)
                filename = _build_filename(cdr_file, portal, portal.output_format)

            transport = get_transport(portal.portal_type)

            # Per-operator routing context (directory segments only).
            deliver_context = {
                'operator': getattr(cdr_file, 'operator_code', None),
                'vendor': getattr(cdr_file, 'vendor', None),
                'network_element': getattr(cdr_file, 'network_element', None),
            }

            # Per-rule retry policy (falls back to module defaults)
            max_attempts = max(1, int(getattr(rule, 'max_retries', None) or DELIVERY_MAX_ATTEMPTS))
            backoff = rule.backoff_schedule() if hasattr(rule, 'backoff_schedule') else []
            if not backoff:
                backoff = list(DELIVERY_BACKOFF_SECONDS)

            last_error = None
            attempts = 0
            for attempt in range(1, max_attempts + 1):
                attempts = attempt
                try:
                    transport.deliver(payload, filename, portal, deliver_context)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning(f'Delivery attempt {attempt}/{max_attempts} failed for rule {rule.name}: {exc}')
                    if attempt < max_attempts and backoff:
                        time.sleep(backoff[min(attempt - 1, len(backoff) - 1)])
            if last_error is not None:
                raise last_error

            DistributionLog.objects.create(
                cdr_file=cdr_file, rule=rule, output_portal=portal,
                filename=filename, record_count=record_count,
                file_size=len(payload),
                status=DistributionLog.Status.SUCCESS,
                retry_count=attempts - 1,
            )
            summaries.append({
                'rule': rule.name, 'portal': portal.name,
                'records': record_count, 'bytes': len(payload),
                'attempts': attempts, 'status': 'SUCCESS',
            })
        except Exception as e:
            logger.error(f'Dispatch failed for rule {rule.name}: {e}', exc_info=True)
            DistributionLog.objects.create(
                cdr_file=cdr_file, rule=rule, output_portal=portal,
                filename='', record_count=0, file_size=0,
                status=DistributionLog.Status.FAILED,
                error=str(e)[:1000],
                retry_count=max(0, int(getattr(rule, 'max_retries', None) or DELIVERY_MAX_ATTEMPTS) - 1),
            )
            summaries.append({'rule': rule.name, 'status': 'FAILED', 'error': str(e)[:200]})

    return summaries


def dispatch_in_memory(cdr_file, records: list) -> list:
    """Decode-only dispatch: render + deliver output directly from in-memory
    decoded records (saved=False), with NO database round-trip.

    Used when settings.CDR_PERSIST_RECORDS is False. Per-rule ``filter_logic``
    is applied in memory via :func:`_record_matches` (e.g. a postpaid-only
    downstream gets only its rows). CSV/JSON/TEXT/XML are supported; RAW
    delivers the original source file (unfiltered).
    """
    from collection.models import DistributionLog
    from portals.models import DistributionRule

    rules = (
        DistributionRule.objects
        .filter(is_active=True)
        .select_related('output_portal', 'output_schema')
        .order_by('priority', 'name')
    )
    deliver_context = {
        'operator': getattr(cdr_file, 'operator_code', None),
        'vendor': getattr(cdr_file, 'vendor', None),
        'network_element': getattr(cdr_file, 'network_element', None),
    }

    summaries = []
    for rule in rules:
        if not _stream_matches(rule.stream_type, cdr_file.decoder_type):
            continue
        portal = rule.output_portal
        schema = rule.output_schema
        if not portal or not portal.is_active:
            summaries.append({'rule': rule.name, 'status': 'SKIPPED'})
            continue
        try:
            fkw = rule.filter_kwargs()
            if portal.output_format == 'RAW':
                if fkw:
                    logger.warning(
                        f'dispatch_in_memory: rule {rule.name} has a filter but '
                        f'RAW output delivers the source file unfiltered.')
                with open(cdr_file.file_path, 'rb') as f:
                    payload = f.read()
                record_count = len(records)
            else:
                # Apply the rule's filter in memory (e.g. prepaid_flag=POSTPAID).
                rule_records = (
                    [r for r in records if _record_matches(r, fkw)] if fkw else records
                )
                mapping = (schema.mapping_json if schema else {}) or {}
                if isinstance(mapping, str):
                    try:
                        mapping = json.loads(mapping)
                    except Exception:
                        mapping = {}
                fmt = (portal.output_format or 'CSV').upper()
                if fmt == 'CSV':
                    payload, record_count = _render_records_csv(rule_records, mapping, schema)
                else:
                    rows = [_apply_mapping(rec, mapping) for rec in rule_records]
                    payload = _render(rows, portal.output_format, schema)
                    record_count = len(rows)
            filename = _build_filename(cdr_file, portal, portal.output_format)
            get_transport(portal.portal_type).deliver(payload, filename, portal, deliver_context)
            DistributionLog.objects.create(
                cdr_file=cdr_file, rule=rule, output_portal=portal,
                filename=filename, record_count=record_count, file_size=len(payload),
                status=DistributionLog.Status.SUCCESS,
            )
            summaries.append({'rule': rule.name, 'records': record_count, 'status': 'SUCCESS'})
        except Exception as e:
            logger.error(f'dispatch_in_memory failed for rule {rule.name}: {e}', exc_info=True)
            summaries.append({'rule': rule.name, 'status': 'FAILED', 'error': str(e)[:200]})
    return summaries
