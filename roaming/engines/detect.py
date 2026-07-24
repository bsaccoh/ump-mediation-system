"""Inbound-roamer detection.

An IMSI is structured as ``MCC (3) + MNC (2-3) + MSIN``.  For Orange SL the
home MCC is **619**; any IMSI on a CDR with MCC ≠ 619 is an inbound roamer
(a visitor whose home operator is identified by the foreign MCC+MNC).

``detect_inbound_roamers(start, end)`` scans MSC + IMS + PGW within the
window and returns one row per distinct (MCC, MNC) prefix with:

  * record_count  — total CDRs from that prefix
  * voice_minutes — sum of MSC + IMS durations / 60
  * sms_count     — sum of SMSMO / SMSMT records
  * data_mb       — sum of PGW bytes / 1MB
  * partner_code  — attributed InterconnectPartner.code or '' if unmatched
  * partner_name  — partner display name
  * sample_imsis  — up to 5 representative IMSIs for verification
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal


HOME_MCC = '619'  # Sierra Leone (any MNC)


def _prefix(imsi: str) -> tuple[str, str] | None:
    """Return ``(mcc, mnc_2digit)`` from an IMSI string, or None if invalid.

    We pick the 2-digit MNC because most operators use 2 digits — short of
    a proper PLMN table, this is the safest default and still uniquely
    identifies the partner in practice.  (3-digit MNC = North-American
    operators; we treat those as 2+last-digit-merged into MSIN.)
    """
    if not imsi or len(imsi) < 5:
        return None
    s = ''.join(c for c in imsi if c.isdigit())
    if len(s) < 5:
        return None
    return s[:3], s[3:5]


def detect_inbound_roamers(start, end) -> list[dict]:
    """Aggregate inbound-roamer traffic per (MCC, MNC) for the window."""
    from streams.msc.models import MSCRecord
    from streams.ims.models import IMSRecord
    from streams.pgw.models import PGWRecord

    if isinstance(start, str):
        from datetime import date as _d
        start = _d.fromisoformat(start)
    if isinstance(end, str):
        from datetime import date as _d
        end = _d.fromisoformat(end)

    s = datetime.combine(start, time.min)
    e = datetime.combine(end + timedelta(days=1), time.min)

    # buckets[(mcc, mnc)] = {record_count, voice_minutes, sms_count, data_mb, imsis}
    buckets: dict[tuple[str, str], dict] = defaultdict(lambda: {
        'record_count': 0,
        'voice_minutes': Decimal('0'),
        'sms_count': 0,
        'data_mb': Decimal('0'),
        'imsis': set(),
    })

    # ---- MSC ----
    msc_qs = (MSCRecord.objects
              .filter(start_time__gte=s, start_time__lt=e)
              .exclude(imsi='')
              .values('record_type', 'imsi', 'duration')
              .iterator(chunk_size=5000))
    for rec in msc_qs:
        pre = _prefix(rec['imsi'])
        if not pre or pre[0] == HOME_MCC:
            continue
        b = buckets[pre]
        b['record_count'] += 1
        rt = (rec.get('record_type') or '').upper()
        if rt in ('MOC', 'MTC'):
            b['voice_minutes'] += Decimal(str(rec.get('duration') or 0)) / Decimal('60')
        elif rt in ('SMSMO', 'SMSMT'):
            b['sms_count'] += 1
        if len(b['imsis']) < 5:
            b['imsis'].add(rec['imsi'])

    # ---- IMS (VoLTE) ----
    ims_qs = (IMSRecord.objects
              .filter(start_time__gte=s, start_time__lt=e)
              .exclude(imsi='')
              .values('imsi', 'duration')
              .iterator(chunk_size=5000))
    for rec in ims_qs:
        pre = _prefix(rec['imsi'])
        if not pre or pre[0] == HOME_MCC:
            continue
        b = buckets[pre]
        b['record_count'] += 1
        b['voice_minutes'] += Decimal(str(rec.get('duration') or 0)) / Decimal('60')
        if len(b['imsis']) < 5:
            b['imsis'].add(rec['imsi'])

    # ---- PGW (data) ----
    pgw_qs = (PGWRecord.objects
              .filter(start_time__gte=s, start_time__lt=e)
              .exclude(imsi='')
              .values('imsi', 'data_volume_up', 'data_volume_down')
              .iterator(chunk_size=5000))
    for rec in pgw_qs:
        pre = _prefix(rec['imsi'])
        if not pre or pre[0] == HOME_MCC:
            continue
        b = buckets[pre]
        b['record_count'] += 1
        bytes_total = int(rec.get('data_volume_up') or 0) + int(rec.get('data_volume_down') or 0)
        b['data_mb'] += Decimal(bytes_total) / Decimal('1048576')
        if len(b['imsis']) < 5:
            b['imsis'].add(rec['imsi'])

    # Attribute each prefix to a partner (if seeded)
    rows = []
    for (mcc, mnc), b in sorted(buckets.items(), key=lambda kv: -kv[1]['record_count']):
        partner = attribute_to_partner(mcc, mnc)
        rows.append({
            'mcc': mcc,
            'mnc': mnc,
            'plmn': f'{mcc}{mnc}',
            'partner_code': partner.code if partner else '',
            'partner_name': partner.name if partner else '',
            'partner_id': partner.pk if partner else None,
            'record_count': b['record_count'],
            'voice_minutes': float(b['voice_minutes'].quantize(Decimal('0.001'))),
            'sms_count': b['sms_count'],
            'data_mb': float(b['data_mb'].quantize(Decimal('0.001'))),
            'sample_imsis': sorted(b['imsis']),
        })
    return rows


def attribute_to_partner(mcc: str, mnc: str):
    """Return the InterconnectPartner whose (mcc, mnc) matches, or None.

    Tries an exact (mcc, mnc) match first, then falls back to (mcc, '') —
    a partner row that lists only an MCC catches any visitor from that
    country regardless of MNC.
    """
    from interconnect.models import InterconnectPartner
    qs = InterconnectPartner.objects.filter(is_roaming_partner=True,
                                              is_active=True,
                                              mcc=mcc)
    exact = qs.filter(mnc=mnc).first()
    if exact:
        return exact
    # Country-level fallback: partner with this MCC and blank MNC
    return qs.filter(mnc='').first()
