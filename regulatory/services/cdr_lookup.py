"""
Shared masked CDR drill-down helper.

Used by both the Audit Case detail workspace and the Risk Alert investigation
page's CDR tabs, so the two don't diverge on pagination/masking behaviour.
"""
from typing import Any, Dict, Optional

from django.db.models import Q

from core.operator_context import operator_context
from core.utils.privacy import mask_msisdn


def get_masked_cdrs(operator_code: str, period_start=None, period_end=None,
                     filters: Optional[Dict[str, Any]] = None, page: int = 1, page_size: int = 25) -> Dict[str, Any]:
    """Server-side paginated, masked CDR drill-down for one operator/period.

    Deliberately avoids COUNT(*) over a potentially huge per-operator CDR
    table: uses limit/offset slicing and a "has_next" probe row instead of
    Paginator's total-count page range.
    """
    filters = filters or {}
    try:
        from streams.msc.models import MSCRecord
    except ImportError:
        return {'records': [], 'has_next': False, 'page': page, 'available': False}

    with operator_context(operator_code):
        qs = MSCRecord.objects.all()
        start = filters.get('date_from') or period_start
        end = filters.get('date_end') or period_end
        if start:
            qs = qs.filter(start_time__date__gte=start)
        if end:
            qs = qs.filter(start_time__date__lte=end)
        if filters.get('service_type'):
            qs = qs.filter(service_type=filters['service_type'])
        if filters.get('record_type'):
            qs = qs.filter(record_type=filters['record_type'])
        if filters.get('direction'):
            qs = qs.filter(call_direction=filters['direction'])
        if filters.get('source_file'):
            qs = qs.filter(file_id=filters['source_file'])
        if filters.get('search'):
            term = filters['search']
            qs = qs.filter(Q(calling_number__icontains=term) | Q(called_number__icontains=term) | Q(call_reference__icontains=term))

        offset = (page - 1) * page_size
        rows = list(qs.order_by('-start_time')[offset:offset + page_size + 1])
        has_next = len(rows) > page_size
        rows = rows[:page_size]

        records = [{
            'event_time': row.start_time, 'service_type': row.service_type,
            'calling_number': mask_msisdn(row.calling_number), 'called_number': mask_msisdn(row.called_number),
            'imsi': mask_msisdn(row.imsi) if row.imsi else '', 'direction': row.call_direction,
            'duration': row.duration, 'record_type': row.record_type,
            'source_file_id': row.file_id, 'status': row.status,
        } for row in rows]
    return {'records': records, 'has_next': has_next, 'page': page, 'available': True}
