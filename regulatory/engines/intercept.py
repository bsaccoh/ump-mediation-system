"""Lawful-intercept query + evidentiary CSV export.

``run_lea_query`` returns up to ``limit`` preview rows for the UI.
``export_evidentiary`` runs the full query, writes a CSV, computes a
SHA-256 over the file bytes for chain-of-custody, and persists a
:class:`regulatory.models.LEAExtractionLog` row.

The query scope is built from the ``LEARequest`` filters: any of MSISDN,
IMSI, IMEI, cell-id may be set; ``filter_start`` / ``filter_end`` define
the time window.  At least one identifier (or cell-id) must be present.
"""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime
from typing import List

from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils import timezone

from core.models import AuditLog

from ..models import LEARequest, LEAExtractionLog


# CSV column order — keep stable so investigators always see the same layout
CSV_COLUMNS = [
    'start_time', 'end_time', 'duration',
    'record_type', 'service_type',
    'calling_number', 'called_number', 'dialed_number',
    'charged_msisdn', 'imsi', 'imei',
    'cell_id', 'lac', 'tac',
    'result_code', 'roaming_indicator',
    'msc_id', 'originating_trunk', 'terminating_trunk',
]


def _criteria_q(req: LEARequest) -> Q:
    """Build the Q() expression from the request's scope filters."""
    q = Q(start_time__gte=req.filter_start, start_time__lt=req.filter_end)

    sub = Q()
    has_any = False
    if req.filter_msisdn:
        sub |= (Q(calling_number__icontains=req.filter_msisdn) |
                Q(called_number__icontains=req.filter_msisdn) |
                Q(charged_msisdn__icontains=req.filter_msisdn))
        has_any = True
    if req.filter_imsi:
        sub |= Q(imsi=req.filter_imsi)
        has_any = True
    if req.filter_imei:
        sub |= Q(imei=req.filter_imei)
        has_any = True
    if req.filter_cell_id:
        sub |= Q(cell_id=req.filter_cell_id)
        has_any = True
    if not has_any:
        raise ValueError(
            'LEA request must include at least one of: MSISDN, IMSI, IMEI, cell_id.'
        )
    return q & sub


def run_lea_query(request: LEARequest, limit: int = 100) -> List[dict]:
    """Return up to ``limit`` preview rows (list-of-dicts)."""
    from streams.msc.models import MSCRecord
    q = _criteria_q(request)
    qs = (MSCRecord.objects.filter(q)
          .order_by('start_time')
          .values(*CSV_COLUMNS)[:limit])
    return [_row_to_serializable(r) for r in qs]


def _row_to_serializable(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if hasattr(v, 'isoformat'):
            out[k] = v.isoformat()
        elif v is None:
            out[k] = ''
        else:
            out[k] = v
    return out


def export_evidentiary(request: LEARequest, user=None) -> LEAExtractionLog:
    """Run the full query, write a CSV, hash it, persist LEAExtractionLog,
    and write an audit-log entry."""
    from streams.msc.models import MSCRecord

    q = _criteria_q(request)
    qs = (MSCRecord.objects.filter(q)
          .order_by('start_time')
          .values(*CSV_COLUMNS)
          .iterator(chunk_size=2000))

    buf = io.StringIO()
    w = csv.writer(buf)
    # Header block
    w.writerow(['# Case', request.case_number])
    w.writerow(['# Agency', request.requesting_agency])
    w.writerow(['# Officer', request.officer_name])
    w.writerow(['# Legal basis', request.legal_basis])
    w.writerow(['# Scope', request.filter_start.isoformat(),
                request.filter_end.isoformat()])
    w.writerow(['# Filters',
                f'msisdn={request.filter_msisdn}',
                f'imsi={request.filter_imsi}',
                f'imei={request.filter_imei}',
                f'cell_id={request.filter_cell_id}'])
    w.writerow(['# Exported at', timezone.now().isoformat()])
    w.writerow(['# Exported by', getattr(user, 'username', '') or ''])
    w.writerow([])
    w.writerow(CSV_COLUMNS)

    record_count = 0
    for row in qs:
        w.writerow([_csv_cell(row.get(c)) for c in CSV_COLUMNS])
        record_count += 1

    payload = buf.getvalue().encode('utf-8')
    digest = hashlib.sha256(payload).hexdigest()

    # Persist log + file
    ext = LEAExtractionLog.objects.create(
        request=request,
        executed_by=user if user and user.is_authenticated else None,
        record_count=record_count,
        criteria_json={
            'msisdn': request.filter_msisdn,
            'imsi': request.filter_imsi,
            'imei': request.filter_imei,
            'cell_id': request.filter_cell_id,
            'start': request.filter_start.isoformat(),
            'end': request.filter_end.isoformat(),
        },
        sha256=digest,
    )
    ext.export_file.save(
        f'LEA_{request.case_number}_{ext.pk}.csv',
        ContentFile(payload), save=True,
    )

    # Promote request status if it was still OPEN
    if request.status in (LEARequest.Status.OPEN, LEARequest.Status.IN_PROGRESS):
        request.status = LEARequest.Status.FULFILLED
        request.fulfilled_at = timezone.now()
        request.fulfilled_by = user if user and user.is_authenticated else None
        request.save(update_fields=['status', 'fulfilled_at', 'fulfilled_by'])

    # Audit
    try:
        AuditLog.objects.create(
            user=user if user and user.is_authenticated else None,
            action='LEA_EXPORT',
            entity_type='LEAExtractionLog',
            entity_id=str(ext.pk),
            description=(f'Case {request.case_number}: {record_count} records exported, '
                          f'sha256={digest[:16]}…'),
            extra_data={'sha256': digest, 'record_count': record_count},
        )
    except Exception:
        pass

    return ext


def _csv_cell(v):
    if v is None:
        return ''
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    return str(v)
