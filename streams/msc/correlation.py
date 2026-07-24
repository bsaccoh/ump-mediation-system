"""
MSC CDR-pair correlation logic.

Originating and terminating legs of the same call share the same
``call_reference`` (Huawei msCallRef / network-call-reference).  This module
links them via ``MSCRecord.paired_record``.

Used by:
  * ``MSCProcessor.post_process`` — end-of-file pairing for recent records.
  * Management command ``correlate_orphans`` — periodic full re-scan.
"""
import logging
from datetime import timedelta
from typing import Optional

from django.utils import timezone

logger = logging.getLogger(__name__)

# Which record types form valid pairs with which.
OPPOSITES = {
    'MOC':    {'MTC'},
    'MTC':    {'MOC'},
    'SMSMO':  {'SMSMT'},
    'SMSMT':  {'SMSMO'},
    'GWIN':   {'GWOUT'},
    'GWOUT':  {'GWIN'},
    'CF':     {'MOC', 'MTC'},   # forwarded leg can pair with the original
    'RCF':    {'MTC'},
}


def correlate_unpaired_msc(
    unpaired_qs,
    candidate_window_days: Optional[int] = None,
    chunk_size: int = 2000,
) -> int:
    """Link unpaired MSC records by matching call_reference + opposite record_type.

    Materializes the input queryset (chunked) before issuing any writes so
    SQLite isn't stuck holding a read lock during the save loop.

    Args:
        unpaired_qs:               queryset of MSCRecord with paired_record IS NULL
        candidate_window_days:     time-cutoff for candidate pairs.  None = no
                                    cutoff (used by the orphan re-scan).
        chunk_size:                how many orphans to materialize at a time.

    Returns the number of new pair-links created.
    """
    from streams.msc.models import MSCRecord

    cutoff = (timezone.now() - timedelta(days=candidate_window_days)
              if candidate_window_days else None)

    # Materialize orphan IDs upfront so we drop the read cursor before writing.
    orphan_ids = list(unpaired_qs.values_list('id', flat=True))
    if not orphan_ids:
        return 0

    pair_count = 0
    modified = {}    # id → record (deduped so we never bulk_update the same row twice)

    for i in range(0, len(orphan_ids), chunk_size):
        chunk_ids = orphan_ids[i:i + chunk_size]
        chunk = list(MSCRecord.objects.filter(id__in=chunk_ids).only(
            'id', 'call_reference', 'record_type', 'paired_record_id'
        ))
        call_refs = [r.call_reference for r in chunk if r.call_reference]
        if not call_refs:
            continue
        cand_qs = MSCRecord.objects.filter(call_reference__in=call_refs).only(
            'id', 'call_reference', 'record_type', 'paired_record_id'
        )
        candidates_by_ref = {}
        for cand in cand_qs:
            candidates_by_ref.setdefault(cand.call_reference, []).append(cand)

        for rec in chunk:
            rt = (rec.record_type or '').upper()
            opposite_set = OPPOSITES.get(rt)
            if not opposite_set or not rec.call_reference:
                continue
            cands = candidates_by_ref.get(rec.call_reference, [])
            mate = None
            for c in cands:
                if c.id == rec.id:
                    continue
                if (c.record_type or '').upper() in opposite_set:
                    mate = c
                    break
            if not mate:
                continue
            rec.paired_record_id = mate.id
            modified[rec.id] = rec
            if mate.paired_record_id is None:
                mate.paired_record_id = rec.id
                modified[mate.id] = mate
            pair_count += 1

    # Single batched UPDATE for all pair-links — orders of magnitude faster
    # than per-record save() calls (especially on SQLite).
    if modified:
        MSCRecord.objects.bulk_update(
            list(modified.values()), ['paired_record'], batch_size=500
        )

    return pair_count
