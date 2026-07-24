"""
IMS CDR-pair correlation logic.

Originating and terminating IMS records that belong to the same call share
the same ICID (`ftpcscf01.<pid>.<ts>`).  This module links them by setting
each record's ``paired_record`` foreign key to the other.

Used by:
  * ``IMSProcessor.post_process`` — runs at end-of-file to pair records
    seen so far (cross-file, recent window).
  * Management command ``correlate_orphans`` — periodic full re-scan that
    picks up late-arriving pairs.
"""
import logging
from datetime import timedelta
from typing import Iterable, Optional

from django.utils import timezone

logger = logging.getLogger(__name__)


def correlate_unpaired_ims(
    unpaired_qs,
    candidate_window_days: Optional[int] = None,
    chunk_size: int = 500,
) -> int:
    """Link unpaired IMS records to their opposite-role counterpart by ICID.

    Chunked materialisation keeps SQLite from holding a read lock during writes.

    Args:
        unpaired_qs:               queryset with paired_record IS NULL
        candidate_window_days:     time-cutoff for candidate pairs (None = scan all)
        chunk_size:                orphan batch size

    Returns the number of new pair-links created.
    """
    from streams.ims.models import IMSRecord

    cutoff = (timezone.now() - timedelta(days=candidate_window_days)
              if candidate_window_days else None)

    orphan_ids = list(unpaired_qs.values_list('id', flat=True))
    if not orphan_ids:
        return 0

    pair_count = 0
    modified = {}     # id → record (deduped before bulk_update)

    for i in range(0, len(orphan_ids), chunk_size):
        chunk_ids = orphan_ids[i:i + chunk_size]
        chunk = list(IMSRecord.objects.filter(id__in=chunk_ids).only(
            'id', 'icid', 'role_of_node', 'paired_record_id'
        ))
        icids = [r.icid for r in chunk if r.icid]
        if not icids:
            continue
        candidates_by_icid = {}
        cand_qs = IMSRecord.objects.filter(icid__in=icids).only(
            'id', 'icid', 'role_of_node', 'paired_record_id'
        )
        if cutoff:
            cand_qs = cand_qs.filter(created_at__gte=cutoff)
        for cand in cand_qs:
            candidates_by_icid.setdefault(cand.icid, []).append(cand)

        for rec in chunk:
            role = (rec.role_of_node or '').upper()
            if 'ORIG' in role:
                opposite = 'TERMINATING'
            elif 'TERM' in role:
                opposite = 'ORIGINATING'
            else:
                continue
            if not rec.icid:
                continue
            mate = None
            for c in candidates_by_icid.get(rec.icid, []):
                if c.id == rec.id:
                    continue
                if opposite in (c.role_of_node or '').upper():
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

    if modified:
        IMSRecord.objects.bulk_update(
            list(modified.values()), ['paired_record'], batch_size=500
        )

    return pair_count
