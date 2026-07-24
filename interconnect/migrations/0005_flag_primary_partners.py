"""Designate the primary partner per country_code.

When two partners share a CC (e.g. VODAUK + BTUK both 44), the primary
receives uncategorised international traffic for that CC; secondaries
receive nothing automatically.  Finance can re-route specific traffic
later via the Partners UI.

This migration is idempotent — runs ``update`` with explicit ``code``
filters so it's safe to re-apply.
"""
from django.db import migrations


PRIMARY_FOREIGN_CODES = [
    'VODAUK',  # UK
    'MTNGH',   # Ghana
    'OCI',     # Côte d'Ivoire
    'MTNNG',   # Nigeria
    'ATTUS',   # US
]

# Local SL partners are matched by 2-digit MSISDN prefix (not CC), so the
# flag is meaningless for them — but set it True for consistency.
PRIMARY_LOCAL_CODES = ['ORANG', 'AFRIC', 'QCELL', 'SMART', 'SIERR']


def flag_primary(apps, schema_editor):
    Partner = apps.get_model('interconnect', 'InterconnectPartner')
    Partner.objects.filter(
        code__in=PRIMARY_FOREIGN_CODES + PRIMARY_LOCAL_CODES,
    ).update(is_primary_for_country=True)


def unflag(apps, schema_editor):
    Partner = apps.get_model('interconnect', 'InterconnectPartner')
    Partner.objects.all().update(is_primary_for_country=False)


class Migration(migrations.Migration):

    dependencies = [
        ('interconnect', '0004_interconnectpartner_is_primary_for_country'),
    ]

    operations = [
        migrations.RunPython(flag_primary, unflag),
    ]
