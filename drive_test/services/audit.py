"""
Audit Trail — a read-only view over the platform's *existing* audit mechanisms,
scoped to Drive Test's own entities. No parallel audit system is created here:

  - core.AuditLog already exists and is used by the `regulatory` app
    (audit_case_service.py, risk_engine.py, ...) via
    ``AuditLog.objects.create(user=..., action=..., entity_type=..., entity_id=...,
    description=..., extra_data=...)``. drive_test's own state-changing views now
    call the same table through :func:`log` below — same model, same call shape,
    just a thin, DRY wrapper.
  - drive_test.models.CellHistory is *already* an "immutable audit trail for cell
    attribute changes" (its own docstring) written by views._cell_form_view. Cell
    changes are read from there, not duplicated into a second AuditLog row.

This page merges those two real sources into one read-only, filterable feed. It
writes nothing itself except via :func:`log`, which drive_test's views call at the
moment of a real state change — never a timer, never a synthetic backfill.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Q

# Same intent as views._safe_error (strip filesystem paths / tracebacks before an
# error reaches an audit record); kept as its own tiny copy here so tasks.py (which
# writes processing-failure audit entries) does not need to import the large views
# module just for a two-line regex.
_PATH_RE = re.compile(r'[A-Za-z]:\\[^\s]+|/[\w.\-]+(?:/[\w.\-]+)+')


def sanitize_error(text):
    first = (text or '').strip().splitlines()[0] if (text or '').strip() else ''
    return _PATH_RE.sub('[path]', first)[:200] or 'The operation failed.'

# The only entity_type values drive_test ever writes to AuditLog — also used to keep
# this page scoped to Drive Test's own rows within the shared, cross-app AuditLog
# table (regulatory's LEA/levy/report events use entirely different entity_type
# values and are never mixed in here). 'Cell' is deliberately absent: Cell changes
# come from CellHistory, never from AuditLog.
DRIVE_TEST_ENTITY_TYPES = (
    'DriveTestSession', 'DriveTestFile', 'RegulatoryRule', 'RegulatoryThreshold',
    'Site', 'Sector', 'Finding', 'ReferenceImport', 'RegulatoryReport',
)

MODULE_LABELS = {
    'DriveTestSession': 'Sessions', 'DriveTestFile': 'Processing',
    'RegulatoryRule': 'Rules & Thresholds', 'RegulatoryThreshold': 'Rules & Thresholds',
    'Site': 'Network Reference', 'Sector': 'Network Reference', 'Cell': 'Network Reference',
    'Finding': 'Findings', 'ReferenceImport': 'Network Reference', 'RegulatoryReport': 'Reports',
}

_SENSITIVE_KEYS = {'password', 'token', 'secret', 'api_key', 'apikey', 'credential', 'authorization'}
_MASK = '•' * 8


def _jsonable(v):
    """Make a value safe for a plain (un-encoded) JSONField, and mask anything that
    looks like a credential — defence in depth; none of the fields this module
    actually logs (rule/threshold/site/sector/finding attributes) carry secrets."""
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {
            k: (_MASK if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS else _jsonable(x))
            for k, x in v.items()
        }
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    # A model instance, or any other object a caller passed by mistake — never let
    # an unrecognised type reach json.dumps() and silently drop the whole audit row.
    return str(v)


def log(*, request=None, user=None, action, obj=None, entity_type=None, entity_id='', description, **extra):
    """Write one real AuditLog row — the same call shape regulatory's audit_case_service
    already uses. `obj` supplies entity_type/entity_id automatically. Never raises:
    a logging failure must never break the real operation that triggered it."""
    from core.models import AuditLog

    try:
        if obj is not None:
            entity_type = entity_type or type(obj).__name__
            entity_id = entity_id or str(obj.pk)
        ip = None
        if request is not None:
            if user is None and getattr(request, 'user', None) is not None and request.user.is_authenticated:
                user = request.user
            ip = request.META.get('REMOTE_ADDR')
        AuditLog.objects.create(
            user=user, action=action, entity_type=entity_type or '', entity_id=entity_id or '',
            description=description, ip_address=ip, extra_data=_jsonable(extra),
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Audit log write failed (action=%s, entity_type=%s)', action, entity_type)


def changes_dict(before: dict, after: dict) -> dict:
    """{field: {'from': x, 'to': y}} for only the fields that actually changed."""
    out = {}
    for k, v in after.items():
        old = before.get(k)
        if old != v:
            out[k] = {'from': _jsonable(old), 'to': _jsonable(v)}
    return out


# ---------------------------------------------------------------------------
# Unified feed: AuditLog rows + CellHistory rows, adapted to one common shape
# ---------------------------------------------------------------------------

class Entry:
    """One row in the unified feed — either a real AuditLog row or a real
    CellHistory row, never a fabricated one."""

    __slots__ = ('id', 'source', 'timestamp', 'user', 'action', 'action_label',
                 'entity_type', 'entity_id', 'description', 'extra_data', 'status', 'raw', 'link')

    def __init__(self, *, id, source, timestamp, user, action, action_label,
                 entity_type, entity_id, description, extra_data, status=None, raw=None):
        self.id, self.source = id, source
        self.timestamp, self.user = timestamp, user
        self.action, self.action_label = action, action_label
        self.entity_type, self.entity_id = entity_type, entity_id
        self.description, self.extra_data = description, (extra_data or {})
        self.status = status
        self.raw = raw
        self.link = None  # set by the view once a detail_url() lookup has run

    @property
    def module(self):
        return MODULE_LABELS.get(self.entity_type, self.entity_type)

    @property
    def changes(self):
        return self.extra_data.get('changes')

    @property
    def fields(self):
        return self.extra_data.get('fields')


def _from_auditlog(row):
    from core.models import AuditLog

    extra = row.extra_data if isinstance(row.extra_data, dict) else {}
    return Entry(
        id=f'log-{row.pk}', source='log', timestamp=row.timestamp, user=row.user,
        action=row.action, action_label=dict(AuditLog.ACTION_CHOICES).get(row.action, row.action),
        entity_type=row.entity_type, entity_id=row.entity_id,
        description=row.description, extra_data=extra, status=extra.get('status'), raw=row,
    )


_CHANGE_TYPE_ACTION = {'created': 'CREATE', 'updated': 'UPDATE', 'deactivated': 'UPDATE'}
_CHANGE_TYPE_LABEL = {'created': 'Create', 'updated': 'Update', 'deactivated': 'Deactivate'}


def _from_cell_history(row):
    snap = row.snapshot if isinstance(row.snapshot, dict) else {}
    cell_id_code = snap.get('cell_id', f'#{row.cell_id}')
    return Entry(
        id=f'cell-{row.pk}', source='cell', timestamp=row.changed_at, user=row.changed_by,
        action=_CHANGE_TYPE_ACTION.get(row.change_type, 'UPDATE'),
        action_label=_CHANGE_TYPE_LABEL.get(row.change_type, row.change_type.title()),
        entity_type='Cell', entity_id=str(row.cell_id),
        description=f'Cell {row.change_type}: {cell_id_code}',
        extra_data=({'fields': snap} if row.change_type == 'created' else {'snapshot_after': snap}),
        raw=row,
    )


def filter_entries(GET):
    """Server-side filters across both real sources. Returns (entries: list[Entry], filters, errors).

    Both sources are fetched newest-first, bounded, then merged and re-sorted in
    Python — the dataset this platform actually has is small enough that this is
    simpler, and just as correct, as a SQL UNION across two differently-shaped
    tables, and it keeps each source's own model untouched.
    """
    from django.core.exceptions import ValidationError
    from core.models import AuditLog
    from ..models import CellHistory

    f = {k: GET.get(k, '').strip() for k in
         ('q', 'user', 'module', 'action', 'object_type', 'date_from', 'date_to')}
    errors = []

    log_qs = AuditLog.objects.filter(entity_type__in=DRIVE_TEST_ENTITY_TYPES).select_related('user')
    cell_qs = CellHistory.objects.select_related('changed_by', 'cell')

    if f['q']:
        q = f['q']
        log_qs = log_qs.filter(Q(description__icontains=q) | Q(entity_id__icontains=q)
                                | Q(user__username__icontains=q))
        cell_qs = cell_qs.filter(Q(cell__cell_id__icontains=q) | Q(changed_by__username__icontains=q))
    if f['user']:
        log_qs = log_qs.filter(user__username=f['user'])
        cell_qs = cell_qs.filter(changed_by__username=f['user'])
    if f['module']:
        types = tuple(t for t, m in MODULE_LABELS.items() if m == f['module'])
        if not types:
            errors.append('Unrecognised module.')
            log_qs, cell_qs = AuditLog.objects.none(), CellHistory.objects.none()
        else:
            log_types = tuple(t for t in types if t != 'Cell')
            log_qs = log_qs.filter(entity_type__in=log_types) if log_types else log_qs.none()
            cell_qs = cell_qs if 'Cell' in types else CellHistory.objects.none()
    if f['action']:
        if f['action'] not in dict(AuditLog.ACTION_CHOICES):
            errors.append('Unrecognised action.')
            log_qs, cell_qs = AuditLog.objects.none(), CellHistory.objects.none()
        else:
            log_qs = log_qs.filter(action=f['action'])
            matching_change_types = [ct for ct, a in _CHANGE_TYPE_ACTION.items() if a == f['action']]
            cell_qs = cell_qs.filter(change_type__in=matching_change_types) if matching_change_types else cell_qs.none()
    if f['object_type']:
        if f['object_type'] not in DRIVE_TEST_ENTITY_TYPES + ('Cell',):
            errors.append('Unrecognised object type.')
            log_qs, cell_qs = AuditLog.objects.none(), CellHistory.objects.none()
        elif f['object_type'] == 'Cell':
            log_qs = log_qs.none()
        else:
            log_qs = log_qs.filter(entity_type=f['object_type'])
            cell_qs = cell_qs.none()
    try:
        if f['date_from']:
            log_qs = log_qs.filter(timestamp__date__gte=f['date_from'])
            cell_qs = cell_qs.filter(changed_at__date__gte=f['date_from'])
        if f['date_to']:
            log_qs = log_qs.filter(timestamp__date__lte=f['date_to'])
            cell_qs = cell_qs.filter(changed_at__date__lte=f['date_to'])
    except (ValueError, ValidationError):
        errors.append('Date must look like 2026-09-24.')
        log_qs, cell_qs = AuditLog.objects.none(), CellHistory.objects.none()

    BOUND = 2000
    entries = [_from_auditlog(r) for r in log_qs.order_by('-timestamp')[:BOUND]]
    entries += [_from_cell_history(r) for r in cell_qs.order_by('-changed_at')[:BOUND]]
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return entries, f, errors


def has_any_entries():
    from core.models import AuditLog
    from ..models import CellHistory
    return (AuditLog.objects.filter(entity_type__in=DRIVE_TEST_ENTITY_TYPES).exists()
            or CellHistory.objects.exists())


def summary_counts(entries):
    """Real counts over the already-filtered, in-memory entry list."""
    from django.utils import timezone

    # This project runs with USE_TZ=False (naive datetimes throughout), so plain
    # timezone.now() — not localdate()/localtime(), which require an aware value.
    today = timezone.now().date()
    return {
        'total': len(entries),
        'today': sum(1 for e in entries if e.timestamp and e.timestamp.date() == today),
        'config_changes': sum(1 for e in entries if e.entity_type in ('RegulatoryRule', 'RegulatoryThreshold')),
        'user_actions': sum(1 for e in entries if e.user is not None),
    }


def used_users():
    from django.contrib.auth import get_user_model
    from core.models import AuditLog
    from ..models import CellHistory

    log_ids = set(AuditLog.objects.filter(entity_type__in=DRIVE_TEST_ENTITY_TYPES)
                  .exclude(user__isnull=True).values_list('user_id', flat=True).distinct())
    cell_ids = set(CellHistory.objects.exclude(changed_by__isnull=True)
                   .values_list('changed_by_id', flat=True).distinct())
    return get_user_model().objects.filter(pk__in=log_ids | cell_ids).order_by('username')


def object_type_choices():
    from core.models import AuditLog
    from ..models import CellHistory

    types = set(AuditLog.objects.filter(entity_type__in=DRIVE_TEST_ENTITY_TYPES)
                .values_list('entity_type', flat=True).distinct())
    if CellHistory.objects.exists():
        types.add('Cell')
    return sorted(types)


def action_choices():
    from core.models import AuditLog
    from ..models import CellHistory

    labels = dict(AuditLog.ACTION_CHOICES)
    codes = set(AuditLog.objects.filter(entity_type__in=DRIVE_TEST_ENTITY_TYPES)
                .values_list('action', flat=True).distinct())
    if CellHistory.objects.exists():
        codes |= set(_CHANGE_TYPE_ACTION.values())
    return sorted(((c, labels.get(c, c)) for c in codes), key=lambda x: x[1])


def module_choices():
    from core.models import AuditLog
    from ..models import CellHistory

    used_types = set(AuditLog.objects.filter(entity_type__in=DRIVE_TEST_ENTITY_TYPES)
                      .values_list('entity_type', flat=True).distinct())
    mods = {MODULE_LABELS[t] for t in used_types}
    if CellHistory.objects.exists():
        mods.add(MODULE_LABELS['Cell'])
    return sorted(mods)


def detail_url(entry):
    """A safe link from an audit entry to its real, currently-existing object — or
    None. entity_type values with no single object (e.g. 'ReferenceImport') or whose
    object was not found are never turned into a link."""
    from django.urls import reverse
    from ..models import (
        Cell, DriveTestFile, DriveTestSession, Finding, RegulatoryReport,
        RegulatoryRule, RegulatoryThreshold, Sector, Site,
    )

    if not entry.entity_id:
        return None
    lookups = {
        'DriveTestSession': (DriveTestSession, 'session_ref', 'drive_test:session_detail'),
        'DriveTestFile': (DriveTestFile, 'pk', 'drive_test:processing_detail'),
        'RegulatoryRule': (RegulatoryRule, 'pk', 'drive_test:rule_detail'),
        'RegulatoryThreshold': (RegulatoryThreshold, 'pk', 'drive_test:threshold_detail'),
        'Site': (Site, 'pk', 'drive_test:site_detail'),
        'Sector': (Sector, 'pk', 'drive_test:sector_detail'),
        'Cell': (Cell, 'pk', 'drive_test:cell_detail'),
        'Finding': (Finding, 'pk', 'drive_test:finding_detail'),
        'RegulatoryReport': (RegulatoryReport, 'report_ref', 'drive_test:report_detail'),
    }
    row = lookups.get(entry.entity_type)
    if not row:
        return None
    model, lookup_field, url_name = row
    try:
        if lookup_field in ('session_ref', 'report_ref'):
            obj = model.objects.filter(**{lookup_field: entry.entity_id}).first()
        else:
            obj = model.objects.filter(pk=entry.entity_id).first()
    except (ValueError, TypeError):
        return None
    if not obj:
        return None
    return reverse(url_name, args=[getattr(obj, lookup_field)])


def get_entry(entry_id: str):
    """Resolve one 'log-<pk>' or 'cell-<pk>' id back to its real row, or None."""
    from core.models import AuditLog
    from ..models import CellHistory

    if not entry_id or '-' not in entry_id:
        return None
    source, _, raw_pk = entry_id.partition('-')
    if not raw_pk.isdigit():
        return None
    if source == 'log':
        row = (AuditLog.objects.filter(pk=int(raw_pk), entity_type__in=DRIVE_TEST_ENTITY_TYPES)
               .select_related('user').first())
        return _from_auditlog(row) if row else None
    if source == 'cell':
        row = CellHistory.objects.filter(pk=int(raw_pk)).select_related('changed_by', 'cell').first()
        return _from_cell_history(row) if row else None
    return None
