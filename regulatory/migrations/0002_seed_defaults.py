"""Seed the singleton RegulatoryProfile and back-fill 30 days of QoS metrics
from existing MSC CDRs.

The QoS back-fill is best-effort — if the streams.msc table is empty or the
``compute_daily_qos`` helper raises, we skip silently.  No interconnect
state is touched.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db import migrations


def seed_defaults(apps, schema_editor):
    RegulatoryProfile = apps.get_model('regulatory', 'RegulatoryProfile')
    RegulatoryProfile.objects.update_or_create(
        singleton=1,
        defaults=dict(
            regulator_name='NATCOM',
            contact_email='reports@natcom.gov.sl',
            address='3 Hannah Benka-Coker Drive, Freetown, Sierra Leone',
            phone='+232 22 222 222',
            levy_pct=Decimal('0.5000'),
            usf_pct=Decimal('1.0000'),
            home_currency='SLE',
        ),
    )

    # Back-fill 30 days of QoS metrics from MSC records (if any).
    try:
        from regulatory.engines.qos import compute_daily_qos
        today = date.today()
        for offset in range(30):
            d = today - timedelta(days=offset)
            try:
                compute_daily_qos(d)
            except Exception:
                # Empty days are fine — skip rather than abort the migration.
                continue
    except Exception:
        pass


def unseed_defaults(apps, schema_editor):
    RegulatoryProfile = apps.get_model('regulatory', 'RegulatoryProfile')
    QoSMetric = apps.get_model('regulatory', 'QoSMetric')
    RegulatoryProfile.objects.filter(singleton=1).delete()
    QoSMetric.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('regulatory', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_defaults, unseed_defaults),
    ]
