"""Shared test fixtures for the interconnect tests."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from interconnect.models import (
    InterconnectPartner, InterconnectRate, ExchangeRate, BillingCycle,
)


def make_partner(code, *, name=None, is_local=True, is_home=False, is_primary=True,
                  country_code='232', currency='SLE'):
    p, _ = InterconnectPartner.objects.update_or_create(
        code=code,
        defaults=dict(
            name=name or code,
            country='Sierra Leone' if is_local else 'Test',
            country_code=country_code,
            is_local=is_local,
            is_home=is_home,
            is_primary_for_country=is_primary,
            default_currency=currency,
            is_active=True,
        ),
    )
    return p


def make_rate(partner, direction='INBOUND', service='VOICE', dest='LOCAL',
              tod='ANY', unit='PER_MINUTE', rate='0.10',
              effective_from=None, currency=None,
              setup_fee='0', min_charge='0'):
    return InterconnectRate.objects.create(
        partner=partner,
        direction=direction,
        service_type=service,
        destination_type=dest,
        time_of_day=tod,
        unit=unit,
        rate=Decimal(rate),
        setup_fee=Decimal(setup_fee),
        min_charge=Decimal(min_charge),
        currency=currency or partner.default_currency,
        effective_from=effective_from or date(2025, 1, 1),
        is_active=True,
    )


def make_cycle(partner, period_start=None, period_end=None, status='OPEN'):
    return BillingCycle.objects.create(
        partner=partner,
        period_start=period_start or date(2026, 3, 1),
        period_end=period_end or date(2026, 3, 31),
        status=status,
    )


_test_cdr_file = None


def _get_or_create_cdr_file():
    """Return a singleton CDRFile row for test fixtures (NOT-NULL FK)."""
    from collection.models import CDRFile
    obj, _ = CDRFile.objects.get_or_create(
        filename='test_fixture.bin',
        defaults={'file_path': '/dev/null', 'file_size': 0, 'status': 'COMPLETED'},
    )
    return obj


def make_msc_record(record_type='MOC', calling='23276111111', called='23277222222',
                     duration=60, start_time=None, **extra):
    """Build an MSCRecord; ``start_time`` defaults to mid-March 2026 weekday-peak."""
    from streams.msc.models import MSCRecord
    if start_time is None:
        start_time = datetime(2026, 3, 17, 10, 0)  # Tuesday 10:00 → PEAK
    extra.setdefault('file', _get_or_create_cdr_file())
    return MSCRecord.objects.create(
        record_type=record_type,
        calling_number=calling, called_number=called,
        duration=duration, start_time=start_time,
        **extra,
    )


def make_fx_rate(from_currency='USD', to_currency='SLE', rate='22.00',
                 effective_date=None):
    return ExchangeRate.objects.update_or_create(
        from_currency=from_currency, to_currency=to_currency,
        effective_date=effective_date or date(2026, 1, 1),
        defaults=dict(rate=Decimal(rate), source='MANUAL'),
    )[0]
