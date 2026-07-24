"""Reconciliation engine.

Parse a partner-supplied CDR/settlement file (CSV or XLSX), aggregate it
into the same ``(service_type, destination_type)`` buckets used by the
rating engine, and produce ``ReconciliationRecord`` rows that diff our
view against the partner's view.

Expected partner-file columns (case-insensitive, flexible — we accept any
of the synonyms below)::

    service_type   | service | type           # VOICE / SMS / DATA
    destination    | destination_type | dest  # ON_NET / LOCAL / NATIONAL / INTERNATIONAL
    volume         | minutes | sms | mb       # numeric — interpreted by service
    amount         | charge | total           # numeric

Anything else is ignored.  Rows that can't be parsed are skipped (counted
in the return summary).
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, IO, List, Optional, Tuple

from django.db import transaction
from django.db.models import Sum, Count
from django.utils import timezone

from ..models import (
    BillingCycle, InvoiceLine, ReconciliationRecord,
)


SERVICE_ALIASES = {
    'voice': 'VOICE', 'call': 'VOICE', 'calls': 'VOICE', 'mou': 'VOICE',
    'sms':   'SMS',   'message': 'SMS', 'messages': 'SMS',
    'data':  'DATA',  'gprs': 'DATA', 'lte': 'DATA',
    'mms':   'MMS',
}
DEST_ALIASES = {
    'on_net': 'ON_NET', 'onnet': 'ON_NET', 'on-net': 'ON_NET',
    'local': 'LOCAL',
    'national': 'NATIONAL', 'nat': 'NATIONAL',
    'international': 'INTERNATIONAL', 'int': 'INTERNATIONAL', 'intl': 'INTERNATIONAL', 'roaming': 'INTERNATIONAL',
    'premium': 'PREMIUM',
}
COLUMN_CANDIDATES = {
    'service':     ['service_type', 'service', 'type', 'product'],
    'destination': ['destination_type', 'destination', 'dest', 'tier'],
    'volume':      ['volume', 'minutes', 'mou', 'sms', 'mb', 'gb', 'count', 'events'],
    'amount':      ['amount', 'charge', 'total', 'value', 'cost'],
}


def _pick_column(header_lc: List[str], candidates: List[str]) -> Optional[int]:
    for cand in candidates:
        if cand in header_lc:
            return header_lc.index(cand)
    # Fuzzy contains
    for cand in candidates:
        for i, h in enumerate(header_lc):
            if cand in h:
                return i
    return None


def _to_decimal(s) -> Decimal:
    if s is None or s == '':
        return Decimal('0')
    try:
        return Decimal(str(s).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return Decimal('0')


def _parse_rows(file_obj: IO) -> List[List[str]]:
    """Return rows (incl. header) from CSV or XLSX file-like."""
    name = getattr(file_obj, 'name', '').lower()
    raw = file_obj.read()
    if name.endswith('.xlsx') or name.endswith('.xls'):
        try:
            import openpyxl
        except ImportError as e:
            raise RuntimeError('openpyxl not installed; xlsx unsupported') from e
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        ws = wb.active
        return [['' if c is None else str(c) for c in row] for row in ws.iter_rows(values_only=True)]
    # CSV (default)
    text = raw.decode('utf-8-sig', errors='replace') if isinstance(raw, bytes) else raw
    reader = csv.reader(io.StringIO(text))
    return [list(r) for r in reader if any(c.strip() for c in r)]


def _normalize_service(value: str) -> str:
    v = (value or '').strip().lower()
    return SERVICE_ALIASES.get(v, value.strip().upper() or 'VOICE')


def _normalize_dest(value: str) -> str:
    v = (value or '').strip().lower().replace(' ', '_')
    return DEST_ALIASES.get(v, value.strip().upper() or 'NATIONAL')


# ---------------------------------------------------------------------------
# Main entry — import partner file → ReconciliationRecord rows
# ---------------------------------------------------------------------------

@transaction.atomic
def import_partner_cdr(cycle: BillingCycle, file_obj: IO) -> Dict[str, Any]:
    """Parse the uploaded partner file and produce/update
    ``ReconciliationRecord`` rows on ``cycle``.

    Returns a summary dict ``{imported, skipped, partner_total_volume,
    partner_total_amount, partner_file_ref}``.
    """
    file_obj.seek(0)
    rows = _parse_rows(file_obj)
    if not rows:
        raise ValueError('Empty file')

    header = [str(c or '').strip().lower() for c in rows[0]]
    col_service = _pick_column(header, COLUMN_CANDIDATES['service'])
    col_dest = _pick_column(header, COLUMN_CANDIDATES['destination'])
    col_volume = _pick_column(header, COLUMN_CANDIDATES['volume'])
    col_amount = _pick_column(header, COLUMN_CANDIDATES['amount'])

    if col_service is None or col_volume is None:
        raise ValueError(
            f'File missing required columns. Header was: {header}.  '
            f'Need at least "service" + "volume" (or aliases).'
        )

    # Aggregate partner-side by (service, destination)
    partner_buckets: Dict[Tuple[str, str], Dict[str, Decimal]] = {}
    skipped = 0
    for row in rows[1:]:
        try:
            service = _normalize_service(row[col_service])
            dest = _normalize_dest(row[col_dest]) if col_dest is not None else 'NATIONAL'
            volume = _to_decimal(row[col_volume])
            amount = _to_decimal(row[col_amount]) if col_amount is not None else Decimal('0')
        except (IndexError, AttributeError):
            skipped += 1
            continue
        if volume == 0 and amount == 0:
            skipped += 1
            continue
        key = (service, dest)
        b = partner_buckets.setdefault(key, {'volume': Decimal('0'), 'amount': Decimal('0')})
        b['volume'] += volume
        b['amount'] += amount

    # Aggregate our-side from posted invoices for this cycle, grouped same way.
    # We sum InvoiceLine rows on every Invoice attached to this cycle.
    our_agg = (InvoiceLine.objects
               .filter(invoice__billing_cycle=cycle)
               .values('service_type', 'destination_type')
               .annotate(volume=Sum('volume'),
                          amount=Sum('amount'),
                          events=Sum('event_count')))
    our_buckets = {
        (r['service_type'], r['destination_type']): {
            'volume': r['volume'] or Decimal('0'),
            'amount': r['amount'] or Decimal('0'),
            'events': r['events'] or 0,
        }
        for r in our_agg
    }

    # Union the keys so any partner-only or our-only bucket also gets a row
    all_keys = set(our_buckets) | set(partner_buckets)
    file_ref = getattr(file_obj, 'name', 'uploaded') or 'uploaded'
    imported = 0

    for service, dest in sorted(all_keys):
        ours = our_buckets.get((service, dest), {'volume': Decimal('0'),
                                                   'amount': Decimal('0')})
        theirs = partner_buckets.get((service, dest), {'volume': Decimal('0'),
                                                         'amount': Decimal('0')})

        var_vol = theirs['volume'] - ours['volume']
        var_amt = theirs['amount'] - ours['amount']
        # Variance % relative to our value (denominator = our value).
        denom = ours['volume'] if ours['volume'] else (
            theirs['volume'] if theirs['volume'] else Decimal('1')
        )
        var_pct = (var_vol / denom) * Decimal('100') if denom else Decimal('0')

        status = (ReconciliationRecord.Status.MATCHED
                  if abs(var_pct) < Decimal('1.00')
                  else ReconciliationRecord.Status.OPEN)

        ReconciliationRecord.objects.update_or_create(
            billing_cycle=cycle,
            service_type=service,
            destination_type=dest,
            defaults=dict(
                partner=cycle.partner,
                our_volume=ours['volume'],
                our_amount=ours['amount'],
                partner_volume=theirs['volume'],
                partner_amount=theirs['amount'],
                variance_volume=var_vol,
                variance_amount=var_amt,
                variance_pct=var_pct.quantize(Decimal('0.01')),
                status=status,
                partner_file_ref=file_ref,
            ),
        )
        imported += 1

    # Persist partner aggregates back onto the cycle for quick reference.
    _persist_partner_aggregates(cycle, partner_buckets)

    return {
        'imported': imported,
        'skipped': skipped,
        'partner_buckets': len(partner_buckets),
        'our_buckets': len(our_buckets),
        'partner_file_ref': file_ref,
    }


def _persist_partner_aggregates(cycle, partner_buckets) -> None:
    voice_min = Decimal('0')
    sms_count = Decimal('0')
    data_mb = Decimal('0')
    for (service, _), b in partner_buckets.items():
        if service == 'VOICE':
            voice_min += b['volume']
        elif service == 'SMS':
            sms_count += b['volume']
        elif service == 'DATA':
            data_mb += b['volume']
    cycle.partner_voice_minutes = voice_min.quantize(Decimal('0.001'))
    cycle.partner_sms = int(sms_count)
    cycle.partner_data_mb = data_mb.quantize(Decimal('0.001'))

    # Variance vs our side
    if cycle.our_voice_minutes and cycle.our_voice_minutes > 0:
        cycle.variance_pct = (((voice_min - cycle.our_voice_minutes) /
                                cycle.our_voice_minutes) * Decimal('100')
                               ).quantize(Decimal('0.01'))
    cycle.save(update_fields=[
        'partner_voice_minutes', 'partner_sms', 'partner_data_mb',
        'variance_pct',
    ])


# ---------------------------------------------------------------------------
# Re-compute helper (used when our-side aggregates change after invoicing)
# ---------------------------------------------------------------------------

def compute_variance(cycle: BillingCycle) -> int:
    """Re-aggregate our-side from InvoiceLine rows and refresh variance.

    Useful after a fresh invoice is generated for a cycle that already has
    partner-side data.
    """
    refreshed = 0
    our_agg = (InvoiceLine.objects
               .filter(invoice__billing_cycle=cycle)
               .values('service_type', 'destination_type')
               .annotate(volume=Sum('volume'), amount=Sum('amount')))
    our_map = {(r['service_type'], r['destination_type']):
               (r['volume'] or Decimal('0'), r['amount'] or Decimal('0'))
               for r in our_agg}

    for rec in cycle.reconciliations.all():
        ours = our_map.get((rec.service_type, rec.destination_type),
                             (Decimal('0'), Decimal('0')))
        rec.our_volume = ours[0]
        rec.our_amount = ours[1]
        rec.variance_volume = rec.partner_volume - rec.our_volume
        rec.variance_amount = rec.partner_amount - rec.our_amount
        denom = rec.our_volume if rec.our_volume else (
            rec.partner_volume if rec.partner_volume else Decimal('1')
        )
        rec.variance_pct = ((rec.variance_volume / denom) * Decimal('100')
                            ).quantize(Decimal('0.01')) if denom else Decimal('0')
        rec.save(update_fields=['our_volume', 'our_amount',
                                  'variance_volume', 'variance_amount',
                                  'variance_pct'])
        refreshed += 1
    return refreshed
