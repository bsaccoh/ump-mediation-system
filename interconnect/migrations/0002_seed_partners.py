"""Seed default InterconnectPartner rows + starter ExchangeRate set.

Local SL operators come from ``core.utils.operators.SL_OPERATOR_PREFIX_MAP``;
a small set of common foreign carriers + default USD/EUR/GBP→SLE FX rates
are seeded so the UI has data to display on first boot.  Finance team
extends the foreign list via the Partners UI.
"""
from decimal import Decimal
from datetime import date

from django.db import migrations


LOCAL_PARTNERS = [
    # (code, name, is_home, country_code, mcc, mnc)
    ('ORANG', 'Orange Sierra Leone', True,  '232', '619', '03'),
    ('AFRIC', 'Africell SL',         False, '232', '619', '04'),
    ('QCELL', 'Qcell SL',            False, '232', '619', '07'),
    ('SMART', 'Smart Mobile SL',     False, '232', '619', '02'),
    ('SIERR', 'Sierratel',           False, '232', '619', '01'),
]

FOREIGN_PARTNERS = [
    # (code, name, country, country_code, currency)
    ('VODAUK', 'Vodafone UK',           'United Kingdom', '44',  'GBP'),
    ('BTUK',   'BT Group',              'United Kingdom', '44',  'GBP'),
    ('MTNGH',  'MTN Ghana',             'Ghana',          '233', 'USD'),
    ('OCI',    "Orange Côte d'Ivoire",  "Côte d'Ivoire",  '225', 'EUR'),
    ('MTNNG',  'MTN Nigeria',           'Nigeria',        '234', 'USD'),
    ('ATTUS',  'AT&T',                  'United States',  '1',   'USD'),
]

EXCHANGE_RATES = [
    # (from, to, rate, source)
    ('USD', 'SLE', Decimal('22.50'),  'MANUAL'),
    ('EUR', 'SLE', Decimal('24.50'),  'MANUAL'),
    ('GBP', 'SLE', Decimal('28.75'),  'MANUAL'),
    ('SLE', 'USD', Decimal('0.0444'), 'MANUAL'),
]


def seed_partners(apps, schema_editor):
    Partner = apps.get_model('interconnect', 'InterconnectPartner')
    FX = apps.get_model('interconnect', 'ExchangeRate')

    today = date.today()

    for code, name, is_home, cc, mcc, mnc in LOCAL_PARTNERS:
        Partner.objects.update_or_create(
            code=code,
            defaults=dict(
                name=name,
                country='Sierra Leone',
                country_code=cc,
                mcc=mcc, mnc=mnc,
                is_local=True,
                is_home=is_home,
                default_currency='SLE',
                is_active=True,
            ),
        )

    for code, name, country, cc, currency in FOREIGN_PARTNERS:
        Partner.objects.update_or_create(
            code=code,
            defaults=dict(
                name=name,
                country=country,
                country_code=cc,
                is_local=False,
                is_home=False,
                default_currency=currency,
                is_active=True,
            ),
        )

    for frm, to, rate, source in EXCHANGE_RATES:
        FX.objects.update_or_create(
            from_currency=frm, to_currency=to, effective_date=today,
            defaults=dict(rate=rate, source=source),
        )


def unseed_partners(apps, schema_editor):
    Partner = apps.get_model('interconnect', 'InterconnectPartner')
    FX = apps.get_model('interconnect', 'ExchangeRate')
    codes = [p[0] for p in LOCAL_PARTNERS] + [p[0] for p in FOREIGN_PARTNERS]
    Partner.objects.filter(code__in=codes).delete()
    pairs = [(p[0], p[1]) for p in EXCHANGE_RATES]
    for frm, to in pairs:
        FX.objects.filter(from_currency=frm, to_currency=to).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('interconnect', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_partners, unseed_partners),
    ]
