"""Roaming-file generator.

Produces a TAP-equivalent CSV for one ``BillingCycle`` (``is_roaming=True``).
The cycle's ``partner`` defines which (MCC, MNC) prefix(es) are in scope:
exact (mcc, mnc) match plus the country-level fallback (mcc, '').

Each CDR becomes one CSV row.  Aggregates + SHA-256 are stored on the
resulting ``RoamingFile``; rating uses ``InterconnectRate`` rows with
``is_roaming=True`` (falling back to non-roaming rates if needed).
"""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from interconnect.models import InterconnectRate, BillingCycle

from ..models import RoamingFile
from .detect import HOME_MCC, _prefix


# Column layout — keeps order stable so partners always see the same file
CSV_COLUMNS = [
    'cdr_source', 'record_type',
    'start_time', 'end_time', 'duration',
    'imsi', 'imei',
    'msisdn', 'calling_number', 'called_number',
    'mcc', 'mnc',
    'cell_id', 'lac',
    'service', 'volume', 'unit',
    'unit_rate', 'amount', 'currency',
]


def _rate_for(partner, direction, service_type, when):
    """Find a roaming rate, falling back to non-roaming for the same partner."""
    base = InterconnectRate.objects.filter(
        partner=partner, direction=direction, service_type=service_type,
        is_active=True, effective_from__lte=when,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=when))
    return (base.filter(is_roaming=True).order_by('-effective_from').first() or
            base.order_by('-effective_from').first())


def _next_file_number(partner_code: str, period_end, existing) -> str:
    """``CDR-{partner}-{YYYYMM}-IN[-N]`` — unique across all RoamingFile rows."""
    base = f'CDR-{partner_code}-{period_end.strftime("%Y%m")}-IN'
    if not existing.filter(file_number=base).exists():
        return base
    i = 2
    while existing.filter(file_number=f'{base}-{i}').exists():
        i += 1
    return f'{base}-{i}'


def _matches_partner(mcc: str, mnc: str, partner) -> bool:
    if not mcc:
        return False
    if partner.mcc and mcc != partner.mcc:
        return False
    if partner.mnc and mnc and mnc != partner.mnc:
        return False
    return True


@transaction.atomic
def generate_roaming_file(cycle: BillingCycle, user=None) -> RoamingFile:
    """Walk CDR streams in ``cycle``'s window, emit one CSV row per record
    whose IMSI prefix matches the cycle's partner, attach the file to a
    new ``RoamingFile`` row with SHA-256."""
    if not cycle.is_roaming:
        raise ValueError('Cycle is not a roaming cycle (is_roaming=False)')
    partner = cycle.partner
    if not partner.is_roaming_partner:
        raise ValueError(f'{partner.code} is not flagged as a roaming partner.')
    if not partner.mcc:
        raise ValueError(f'{partner.code} has no MCC set; cannot identify IMSIs.')

    from streams.msc.models import MSCRecord
    from streams.ims.models import IMSRecord
    from streams.pgw.models import PGWRecord

    s = datetime.combine(cycle.period_start, time.min)
    e = datetime.combine(cycle.period_end + timedelta(days=1), time.min)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f'# Roaming settlement file (TAP-equivalent CSV)'])
    w.writerow([f'# Operator: Orange Sierra Leone'])
    w.writerow([f'# Partner:  {partner.code} ({partner.name})  PLMN={partner.mcc}/{partner.mnc or "*"}'])
    w.writerow([f'# Period:   {cycle.period_start}..{cycle.period_end}'])
    w.writerow([f'# Generated: {timezone.now().isoformat()}'])
    w.writerow([])
    w.writerow(CSV_COLUMNS)

    record_count = 0
    voice_minutes = Decimal('0')
    sms_count = 0
    data_mb = Decimal('0')
    total_amount = Decimal('0')

    # --- MSC voice + SMS ---
    msc_qs = (MSCRecord.objects.filter(start_time__gte=s, start_time__lt=e)
              .exclude(imsi='')
              .values('record_type', 'start_time', 'end_time', 'duration',
                       'imsi', 'imei', 'charged_msisdn',
                       'calling_number', 'called_number',
                       'cell_id', 'lac')
              .iterator(chunk_size=5000))
    for rec in msc_qs:
        pre = _prefix(rec.get('imsi') or '')
        if not pre or pre[0] == HOME_MCC:
            continue
        if not _matches_partner(pre[0], pre[1], partner):
            continue

        rt = (rec.get('record_type') or '').upper()
        if rt in ('MOC', 'MTC'):
            service = 'VOICE'
            volume = Decimal(str(rec.get('duration') or 0)) / Decimal('60')
            unit = 'minute'
            voice_minutes += volume
        elif rt in ('SMSMO', 'SMSMT'):
            service = 'SMS'
            volume = Decimal('1')
            unit = 'message'
            sms_count += 1
        else:
            continue  # skip GW etc.

        # All inbound-roamer charges are INBOUND from a settlement POV
        # (we bill the home network for services rendered to their subscriber).
        direction = 'INBOUND'
        rate = _rate_for(partner, direction, service, cycle.period_end)
        if rate is None:
            unit_rate = Decimal('0')
            amount = Decimal('0')
        else:
            unit_rate = rate.rate
            amount = (unit_rate * volume + rate.setup_fee).quantize(Decimal('0.000001'))
            if rate.min_charge and amount < rate.min_charge:
                amount = rate.min_charge
        total_amount += amount
        record_count += 1

        w.writerow([
            'MSC', rt,
            rec.get('start_time'), rec.get('end_time'), rec.get('duration') or 0,
            rec.get('imsi'), rec.get('imei') or '',
            rec.get('charged_msisdn') or '',
            rec.get('calling_number') or '', rec.get('called_number') or '',
            pre[0], pre[1],
            rec.get('cell_id') or '', rec.get('lac') or '',
            service, str(volume.quantize(Decimal('0.001'))), unit,
            str(unit_rate), str(amount), partner.default_currency,
        ])

    # --- IMS VoLTE ---
    ims_qs = (IMSRecord.objects.filter(start_time__gte=s, start_time__lt=e)
              .exclude(imsi='')
              .values('record_type', 'start_time', 'end_time', 'duration',
                       'imsi', 'imei', 'msisdn',
                       'calling_number', 'called_number',
                       'cell_id', 'lac')
              .iterator(chunk_size=5000))
    for rec in ims_qs:
        pre = _prefix(rec.get('imsi') or '')
        if not pre or pre[0] == HOME_MCC:
            continue
        if not _matches_partner(pre[0], pre[1], partner):
            continue

        rt = (rec.get('record_type') or '').upper()
        service = 'VOICE'
        volume = Decimal(str(rec.get('duration') or 0)) / Decimal('60')
        unit = 'minute'
        voice_minutes += volume

        direction = 'INBOUND'  # All inbound-roamer charges settle INBOUND
        rate = _rate_for(partner, direction, service, cycle.period_end)
        unit_rate = rate.rate if rate else Decimal('0')
        amount = (unit_rate * volume).quantize(Decimal('0.000001'))
        total_amount += amount
        record_count += 1
        w.writerow([
            'IMS', rt,
            rec.get('start_time'), rec.get('end_time'), rec.get('duration') or 0,
            rec.get('imsi'), rec.get('imei') or '',
            rec.get('msisdn') or '',
            rec.get('calling_number') or '', rec.get('called_number') or '',
            pre[0], pre[1],
            rec.get('cell_id') or '', rec.get('lac') or '',
            service, str(volume.quantize(Decimal('0.001'))), unit,
            str(unit_rate), str(amount), partner.default_currency,
        ])

    # --- PGW (data) ---
    pgw_qs = (PGWRecord.objects.filter(start_time__gte=s, start_time__lt=e)
              .exclude(imsi='')
              .values('start_time', 'end_time', 'duration',
                       'imsi', 'imei',
                       'calling_number', 'called_number',
                       'cell_id', 'lac',
                       'data_volume_up', 'data_volume_down')
              .iterator(chunk_size=5000))
    for rec in pgw_qs:
        pre = _prefix(rec.get('imsi') or '')
        if not pre or pre[0] == HOME_MCC:
            continue
        if not _matches_partner(pre[0], pre[1], partner):
            continue

        service = 'DATA'
        bytes_total = int(rec.get('data_volume_up') or 0) + int(rec.get('data_volume_down') or 0)
        volume = (Decimal(bytes_total) / Decimal('1048576')).quantize(Decimal('0.001'))
        unit = 'MB'
        data_mb += volume

        rate = _rate_for(partner, 'INBOUND', 'DATA', cycle.period_end)
        unit_rate = rate.rate if rate else Decimal('0')
        amount = (unit_rate * volume).quantize(Decimal('0.000001'))
        total_amount += amount
        record_count += 1
        w.writerow([
            'PGW', 'DATA',
            rec.get('start_time'), rec.get('end_time'), rec.get('duration') or 0,
            rec.get('imsi'), rec.get('imei') or '',
            '', rec.get('calling_number') or '', rec.get('called_number') or '',
            pre[0], pre[1],
            rec.get('cell_id') or '', rec.get('lac') or '',
            service, str(volume), unit,
            str(unit_rate), str(amount), partner.default_currency,
        ])

    # Trailer
    w.writerow([])
    w.writerow([f'# Total records: {record_count}'])
    w.writerow([f'# Voice min: {voice_minutes.quantize(Decimal("0.001"))}'])
    w.writerow([f'# SMS: {sms_count}'])
    w.writerow([f'# Data MB: {data_mb.quantize(Decimal("0.001"))}'])
    w.writerow([f'# Total amount: {total_amount.quantize(Decimal("0.01"))} {partner.default_currency}'])

    payload = buf.getvalue().encode('utf-8')
    digest = hashlib.sha256(payload).hexdigest()

    rfile = RoamingFile.objects.create(
        partner=partner, billing_cycle=cycle,
        direction=RoamingFile.Direction.INBOUND,
        file_number=_next_file_number(partner.code, cycle.period_end,
                                         RoamingFile.objects.all()),
        record_count=record_count,
        voice_minutes=voice_minutes.quantize(Decimal('0.001')),
        sms_count=sms_count,
        data_mb=data_mb.quantize(Decimal('0.001')),
        total_amount=total_amount.quantize(Decimal('0.01')),
        currency=partner.default_currency,
        status=RoamingFile.Status.DRAFT,
        sha256=digest,
        generated_by=user if user and user.is_authenticated else None,
    )
    rfile.csv_file.save(f'{rfile.file_number}.csv', ContentFile(payload), save=True)

    # Update cycle aggregates
    cycle.our_voice_minutes = voice_minutes.quantize(Decimal('0.001'))
    cycle.our_sms = sms_count
    cycle.our_data_mb = data_mb.quantize(Decimal('0.001'))
    if cycle.status == BillingCycle.Status.OPEN:
        cycle.status = BillingCycle.Status.INVOICED
    cycle.save(update_fields=['our_voice_minutes', 'our_sms', 'our_data_mb', 'status'])

    return rfile
