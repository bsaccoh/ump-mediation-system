"""Interconnect rating engine.

Walks MSC / IMS / PGW / SGSN / SGW CDR streams within a BillingCycle window,
classifies each record by partner + direction, looks up the applicable
``InterconnectRate``, and returns aggregate buckets.

Direction convention (from the home network's POV)
---------------------------------------------------
- ``OUTBOUND`` = traffic Orange originated towards the partner.  Partner
  charges us a termination rate → this becomes part of an *Outbound* invoice
  (we owe the partner).
- ``INBOUND``  = traffic the partner originated towards Orange.  We charge
  them a termination rate → *Inbound* invoice (partner owes us).

Per record-type mapping
-----------------------
=========  ================  ==========
Stream     record_type        Direction
=========  ================  ==========
MSC        MOC / GWO          OUTBOUND
MSC        MTC / GWI          INBOUND
MSC        SMSMO              OUTBOUND
MSC        SMSMT              INBOUND
IMS        ORIG               OUTBOUND
IMS        TERM               INBOUND
PGW/SGSN   (any)              OUTBOUND (roaming-out: home subscriber on partner net)
SGW        (any)              OUTBOUND
=========  ================  ==========

Rate lookup precedence (most-specific first)
--------------------------------------------
1. Exact match on ``(partner, direction, service, destination, time_of_day)``
2. Same but with ``time_of_day=ANY``
3. Same but with ``destination=NATIONAL``
4. Same but with both fall-backs
5. Returns ``None`` → record is counted as **unrated** in the bucket totals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Iterable, Optional

from django.db.models import QuerySet

from core.utils.operators import classify_operator
from streams.msc.models import MSCRecord
from streams.ims.models import IMSRecord
from streams.pgw.models import PGWRecord
from streams.sgsn.models import SGSNRecord
from streams.sgw.models import SGWRecord

from ..models import (
    BillingCycle, InterconnectPartner, InterconnectRate,
)


# ---------------------------------------------------------------------------
# Operator-name → partner-code lookup (matches seed data in 0002_seed_partners)
# ---------------------------------------------------------------------------

OPERATOR_TO_PARTNER_CODE = {
    'Orange':    'ORANG',
    'Africell':  'AFRIC',
    'Qcell':     'QCELL',
    'Smart':     'SMART',
    'Sierratel': 'SIERR',
}

# Direction lookup per stream record_type
DIRECTION_OF = {
    # MSC voice
    'MOC': 'OUTBOUND', 'MTC': 'INBOUND',
    # MSC gateway (3-letter + 4/5-letter variants both used historically)
    'GWO': 'OUTBOUND', 'GWOUT': 'OUTBOUND',
    'GWI': 'INBOUND',  'GWIN':  'INBOUND',
    # MSC SMS
    'SMSMO': 'OUTBOUND', 'SMSMT': 'INBOUND',
    # IMS
    'ORIG': 'OUTBOUND', 'TERM': 'INBOUND',
}


# ---------------------------------------------------------------------------
# Bucket dataclass — one (direction, service, destination, time_of_day) cell
# ---------------------------------------------------------------------------

@dataclass
class Bucket:
    direction: str
    service_type: str
    destination_type: str
    time_of_day: str
    rate: Optional[InterconnectRate] = None
    volume: Decimal = Decimal('0')          # minutes / messages / MB
    event_count: int = 0
    amount: Decimal = Decimal('0')
    currency: str = 'SLE'
    unit: str = 'PER_MINUTE'

    @property
    def key(self):
        return (self.direction, self.service_type,
                self.destination_type, self.time_of_day)


@dataclass
class RatingResult:
    cycle_id: int
    buckets: dict = field(default_factory=dict)           # key → Bucket
    unrated_count: int = 0                                # records with no rate match
    skipped_other_partner: int = 0                        # records for other partners
    totals: dict = field(default_factory=dict)            # service → Decimal

    def add(self, bucket: Bucket) -> None:
        existing = self.buckets.get(bucket.key)
        if existing is None:
            self.buckets[bucket.key] = bucket
        else:
            existing.volume += bucket.volume
            existing.event_count += bucket.event_count
            existing.amount += bucket.amount

    def summary(self) -> dict:
        return {
            'cycle_id': self.cycle_id,
            'bucket_count': len(self.buckets),
            'unrated': self.unrated_count,
            'skipped_other_partner': self.skipped_other_partner,
            'event_total': sum(b.event_count for b in self.buckets.values()),
            'volume_total': str(sum((b.volume for b in self.buckets.values()), Decimal('0'))),
            'amount_total': str(sum((b.amount for b in self.buckets.values()), Decimal('0'))),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _time_of_day(ts: Optional[datetime]) -> str:
    """Classify a timestamp into PEAK / OFF_PEAK / WEEKEND."""
    if ts is None:
        return 'ANY'
    # Saturday=5, Sunday=6
    if ts.weekday() >= 5:
        return 'WEEKEND'
    # 08:00 ≤ hour < 18:00 → PEAK
    if time(8, 0) <= ts.time() < time(18, 0):
        return 'PEAK'
    return 'OFF_PEAK'


def _destination_for(partner: InterconnectPartner) -> str:
    """Default destination tier based on whether the partner is foreign."""
    if partner.is_home:
        return InterconnectRate.DestinationType.ON_NET
    if partner.is_local:
        return InterconnectRate.DestinationType.LOCAL
    return InterconnectRate.DestinationType.INTERNATIONAL


def _other_party_msisdn(record_type: str, calling: str, called: str) -> str:
    """Return the MSISDN of the *other* party (i.e. not Orange-side)."""
    direction = DIRECTION_OF.get(record_type, 'OUTBOUND')
    return called if direction == 'OUTBOUND' else calling


def _normalize_intl_msisdn(msisdn: str) -> str:
    """Strip CC prefix indicators (+, 00, leading 0) from an MSISDN so the
    remaining string starts with the country code."""
    s = (msisdn or '').strip()
    if not s:
        return ''
    s = s.lstrip('+')
    if s.startswith('00'):
        s = s[2:]
    elif s.startswith('0'):
        # Local-dial form — has no CC; treat as no-match for foreign attribution.
        return ''
    return ''.join(c for c in s if c.isdigit())


def _record_belongs_to_partner_dict(rec: dict, partner: InterconnectPartner) -> bool:
    """Same logic as :func:`_record_belongs_to_partner` but for a ``.values()``
    dict (no Django-model instantiation overhead)."""
    rt = (rec.get('record_type') or '').upper()
    other = _other_party_msisdn(rt,
                                 rec.get('calling_number') or '',
                                 rec.get('called_number') or '')
    op_name = classify_operator(other)

    mapped_code = OPERATOR_TO_PARTNER_CODE.get(op_name)
    if mapped_code:
        return mapped_code == partner.code
    if op_name != 'International' or partner.is_local:
        return False

    cc = (partner.country_code or '').strip()
    if not cc:
        return False
    normalised = _normalize_intl_msisdn(other)
    if not normalised.startswith(cc):
        return False
    return partner.is_primary_for_country


def _record_belongs_to_partner(record, partner: InterconnectPartner) -> bool:
    """True iff the record's non-Orange party is on this partner's network.

    Local partners (Africell / Qcell / Smart / Sierratel / Orange):
        match on the 2-digit MSISDN prefix via ``classify_operator``.

    Foreign partners:
        match by comparing the other-party MSISDN's leading digits to the
        partner's ``country_code``.  When multiple partners share a CC
        (e.g. VODAUK + BTUK both = 44), only the partner with
        ``is_primary_for_country=True`` is attributed; the rest get nothing.
        This stops the previous "every International matches every foreign"
        double-counting bug.
    """
    rt = (getattr(record, 'record_type', None) or '').upper()
    other = _other_party_msisdn(rt, record.calling_number or '',
                                 record.called_number or '')
    op_name = classify_operator(other)

    # Local-operator routing
    mapped_code = OPERATOR_TO_PARTNER_CODE.get(op_name)
    if mapped_code:
        return mapped_code == partner.code

    # Foreign-operator routing
    if op_name != 'International' or partner.is_local:
        return False

    cc = (partner.country_code or '').strip()
    if not cc:
        return False
    # Strip Sierra-Leone country code from the candidate (already SL-classified
    # in the local branch, so anything still here is foreign).  Then check
    # leading digits.
    normalised = _normalize_intl_msisdn(other)
    if not normalised.startswith(cc):
        return False
    # Multiple partners can share a CC; only the primary takes generic traffic.
    return partner.is_primary_for_country


# ---------------------------------------------------------------------------
# Rate lookup
# ---------------------------------------------------------------------------

def get_applicable_rate(
    partner: InterconnectPartner,
    direction: str,
    service_type: str,
    destination_type: str,
    when: date,
    time_of_day: str = 'ANY',
    call_type: str = None,
) -> Optional[InterconnectRate]:
    """Find the most-specific active rate covering ``when``.

    Order:
      1. exact (dest + tod)
      2. exact dest, tod=ANY
      3. dest=NATIONAL, exact tod
      4. dest=NATIONAL, tod=ANY
    """
    base = InterconnectRate.objects.filter(
        partner=partner,
        direction=direction,
        service_type=service_type,
        is_active=True,
        effective_from__lte=when,
    )
    from django.db.models import Q
    base = base.filter(Q(effective_to__isnull=True) | Q(effective_to__gte=when))

    if call_type:
        call_type_base = base.filter(call_type=call_type)
        if call_type_base.exists():
            base = call_type_base
        else:
            base = base.filter(Q(call_type__isnull=True) | Q(call_type=''))
    else:
        base = base.filter(Q(call_type__isnull=True) | Q(call_type=''))

    for dest, tod in [
        (destination_type, time_of_day),
        (destination_type, 'ANY'),
        ('NATIONAL', time_of_day),
        ('NATIONAL', 'ANY'),
    ]:
        rate = base.filter(destination_type=dest, time_of_day=tod) \
                   .order_by('-effective_from').first()
        if rate:
            return rate
    return None


# ---------------------------------------------------------------------------
# Per-record charge calculation
# ---------------------------------------------------------------------------

def rate_record(volume: Decimal, rate: InterconnectRate) -> Decimal:
    """Apply ``rate`` to a per-record ``volume`` (already in the rate's unit).

    Voice rates use minutes (or seconds), SMS uses count, data uses MB.
    The caller is responsible for converting the raw CDR field to the
    rate's unit before calling this.
    """
    amount = (rate.rate * volume) + rate.setup_fee
    if rate.min_charge and amount < rate.min_charge:
        amount = rate.min_charge
    return amount


def _voice_volume(record, unit: str) -> Decimal:
    """Convert a voice/IMS record's duration → rate-unit volume."""
    secs = Decimal(str(record.duration or 0))
    if unit == InterconnectRate.Unit.PER_SECOND:
        return secs
    if unit == InterconnectRate.Unit.PER_MINUTE:
        return (secs / Decimal('60')).quantize(Decimal('0.001'))
    # FLAT / other
    return Decimal('1')


def _data_volume_mb(record) -> Decimal:
    """Total data volume (up+down) in MB."""
    up = Decimal(str(getattr(record, 'data_volume_up', 0) or 0))
    dn = Decimal(str(getattr(record, 'data_volume_down', 0) or 0))
    bytes_total = up + dn
    return (bytes_total / Decimal('1048576')).quantize(Decimal('0.001'))


# ---------------------------------------------------------------------------
# Main entry — aggregate one cycle
# ---------------------------------------------------------------------------

_SENTINEL = object()  # mark "no rate found" so we cache misses


def _make_rate_cache(partner: InterconnectPartner, when: date):
    """Closure returning a memoised ``get_applicable_rate`` for one cycle.

    The first lookup for a given (direction, service, dest, tod, call_type)
    hits the DB; subsequent calls return the cached InterconnectRate
    instance (or None).  Cuts ~14k DB round-trips per typical cycle.
    """
    cache: dict = {}

    def lookup(direction, service, dest, tod, call_type):
        key = (direction, service, dest, tod, call_type or '')
        hit = cache.get(key, _SENTINEL)
        if hit is _SENTINEL:
            hit = get_applicable_rate(partner, direction, service, dest, when,
                                       time_of_day=tod, call_type=call_type)
            cache[key] = hit
        return hit

    return lookup


def apply_rates(cycle: BillingCycle, persist: bool = True) -> RatingResult:
    """Walk all CDR streams in ``cycle``'s window, classify by partner,
    rate, and aggregate.

    Uses ``.values()`` (raw dict iteration) plus a per-cycle rate cache
    for performance — roughly 5-10× faster than the model-instance version.
    """
    partner = cycle.partner
    result = RatingResult(cycle_id=cycle.pk)
    dest_default = _destination_for(partner)
    rate_for = _make_rate_cache(partner, cycle.period_end)

    start_dt = datetime.combine(cycle.period_start, time.min)
    end_dt = datetime.combine(cycle.period_end + timedelta(days=1), time.min)

    # ------------------------- MSC voice + SMS -------------------------
    msc_fields = ('record_type', 'calling_number', 'called_number',
                   'start_time', 'duration')
    msc_qs = MSCRecord.objects.filter(
        start_time__gte=start_dt, start_time__lt=end_dt,
    ).values(*msc_fields).iterator(chunk_size=5000)

    for rec in msc_qs:
        rt = (rec.get('record_type') or '').upper()
        direction = DIRECTION_OF.get(rt)
        if not direction:
            continue
        if not _record_belongs_to_partner_dict(rec, partner):
            result.skipped_other_partner += 1
            continue

        tod = _time_of_day(rec.get('start_time'))
        if rt in ('SMSMO', 'SMSMT'):
            service = 'SMS'
            rate = rate_for(direction, service, dest_default, tod, rt)
            if rate is None:
                result.unrated_count += 1
                continue
            vol = Decimal('1')
        else:
            service = 'VOICE'
            ct = rt
            if ct == 'GWO':
                ct = 'GWOUT'
            elif ct == 'GWI':
                ct = 'GWIN'
            rate = rate_for(direction, service, dest_default, tod, ct)
            if rate is None:
                result.unrated_count += 1
                continue
            vol = _voice_volume_secs(rec.get('duration'), rate.unit)

        amt = rate_record(vol, rate)
        result.add(Bucket(
            direction=direction, service_type=service,
            destination_type=rate.destination_type, time_of_day=rate.time_of_day,
            rate=rate, volume=vol, event_count=1,
            amount=amt, currency=rate.currency, unit=rate.unit,
        ))

    # ----------------------------- IMS VoLTE -----------------------------
    ims_qs = IMSRecord.objects.filter(
        start_time__gte=start_dt, start_time__lt=end_dt,
    ).values('record_type', 'calling_number', 'called_number',
              'start_time', 'duration').iterator(chunk_size=5000)
    for rec in ims_qs:
        rt = (rec.get('record_type') or '').upper()
        direction = DIRECTION_OF.get(rt)
        if not direction:
            continue
        if not _record_belongs_to_partner_dict(rec, partner):
            result.skipped_other_partner += 1
            continue
        tod = _time_of_day(rec.get('start_time'))
        rate = rate_for(direction, 'VOICE', dest_default, tod, rt)
        if rate is None:
            result.unrated_count += 1
            continue
        vol = _voice_volume_secs(rec.get('duration'), rate.unit)
        amt = rate_record(vol, rate)
        result.add(Bucket(
            direction=direction, service_type='VOICE',
            destination_type=rate.destination_type, time_of_day=rate.time_of_day,
            rate=rate, volume=vol, event_count=1,
            amount=amt, currency=rate.currency, unit=rate.unit,
        ))

    # --------------------- Data: PGW + SGSN + SGW ---------------------
    for Model in (PGWRecord, SGSNRecord, SGWRecord):
        qs = Model.objects.filter(
            start_time__gte=start_dt, start_time__lt=end_dt,
        ).values('calling_number', 'called_number',
                  'start_time', 'data_volume_up', 'data_volume_down').iterator(chunk_size=5000)
        for rec in qs:
            other = rec.get('calling_number') or rec.get('called_number') or ''
            op_name = classify_operator(other)
            mapped = OPERATOR_TO_PARTNER_CODE.get(op_name)
            if mapped == partner.code:
                pass  # local match
            elif op_name == 'International' and not partner.is_local:
                # Foreign: only the primary partner for the CC takes it
                cc = (partner.country_code or '').strip()
                norm = _normalize_intl_msisdn(other)
                if not (cc and norm.startswith(cc) and partner.is_primary_for_country):
                    result.skipped_other_partner += 1
                    continue
            else:
                result.skipped_other_partner += 1
                continue

            tod = _time_of_day(rec.get('start_time'))
            direction = 'OUTBOUND'  # roaming-out default
            rate = rate_for(direction, 'DATA', dest_default, tod, None)
            if rate is None:
                result.unrated_count += 1
                continue
            vol = _data_volume_mb_dict(rec)
            amt = rate_record(vol, rate)
            result.add(Bucket(
                direction=direction, service_type='DATA',
                destination_type=rate.destination_type, time_of_day=rate.time_of_day,
                rate=rate, volume=vol, event_count=1,
                amount=amt, currency=rate.currency, unit=rate.unit,
            ))

    # ----------------------- Persist cycle aggregates -----------------------
    if persist:
        _persist_cycle_aggregates(cycle, result)

    return result


def _voice_volume_secs(duration, unit: str) -> Decimal:
    """Same as :func:`_voice_volume` but takes a raw duration (int/None)."""
    secs = Decimal(str(duration or 0))
    if unit == InterconnectRate.Unit.PER_SECOND:
        return secs
    if unit == InterconnectRate.Unit.PER_MINUTE:
        return (secs / Decimal('60')).quantize(Decimal('0.001'))
    return Decimal('1')


def _data_volume_mb_dict(rec: dict) -> Decimal:
    """data_volume_* are BigIntegerField now — direct arithmetic."""
    bytes_total = int(rec.get('data_volume_up') or 0) + int(rec.get('data_volume_down') or 0)
    return (Decimal(bytes_total) / Decimal('1048576')).quantize(Decimal('0.001'))


def _persist_cycle_aggregates(cycle: BillingCycle, result: RatingResult) -> None:
    """Roll bucket totals into the cycle's `our_*` counters and save."""
    voice_min = Decimal('0')
    voice_calls = 0
    sms_count = 0
    data_mb = Decimal('0')

    for b in result.buckets.values():
        if b.service_type == 'VOICE':
            # Convert to minutes regardless of the rate's unit
            if b.unit == InterconnectRate.Unit.PER_SECOND:
                voice_min += (b.volume / Decimal('60'))
            else:
                voice_min += b.volume
            voice_calls += b.event_count
        elif b.service_type == 'SMS':
            sms_count += b.event_count
        elif b.service_type == 'DATA':
            data_mb += b.volume

    cycle.our_voice_minutes = voice_min.quantize(Decimal('0.001'))
    cycle.our_voice_calls = voice_calls
    cycle.our_sms = sms_count
    cycle.our_data_mb = data_mb.quantize(Decimal('0.001'))
    if cycle.status == BillingCycle.Status.OPEN:
        cycle.status = BillingCycle.Status.CLOSED
    cycle.save(update_fields=[
        'our_voice_minutes', 'our_voice_calls', 'our_sms', 'our_data_mb',
        'status', 'closed_at',
    ] if cycle.closed_at else [
        'our_voice_minutes', 'our_voice_calls', 'our_sms', 'our_data_mb',
        'status',
    ])
