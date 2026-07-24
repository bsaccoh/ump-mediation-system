from django.db import migrations

PORTALS = [
    # group,        group_label,       name,                label,              decoder
    ('SL_BIGDATA', 'SL BIGDATA',      'SL_BIGDATA_HMSC',   'HMSC',             'MSC'),
    ('SL_BIGDATA', 'SL BIGDATA',      'SL_BIGDATA_HPGW',   'PGW',              'PGW'),
    ('SL_BIGDATA', 'SL BIGDATA',      'SL_BIGDATA_HSGW',   'HSGW',             'SGW'),
    ('SL_BIGDATA', 'SL BIGDATA',      'SL_BIGDATA_HSGSN',  'HSGSN',            'SGSN'),
    ('BILLING',    'Billing System',  'BILLING_HMSC',      'HMSC (Voice/SMS)', 'MSC'),
    ('BILLING',    'Billing System',  'BILLING_HPGW',      'PGW (4G Data)',    'PGW'),
    ('BILLING',    'Billing System',  'BILLING_HSGSN',     'SGSN (2G/3G)',     'SGSN'),
    ('ARCHIVE',    'Archive',         'ARCHIVE_MSC',       'MSC Archive',      'MSC'),
    ('ARCHIVE',    'Archive',         'ARCHIVE_DATA',      'Data Archive',     'PGW'),
]


def seed_portals(apps, schema_editor):
    DistributionPortal = apps.get_model('collection', 'DistributionPortal')
    for group, group_label, name, label, decoder in PORTALS:
        DistributionPortal.objects.get_or_create(
            name=name,
            defaults={
                'label': label,
                'group': group,
                'group_label': group_label,
                'decoder_type': decoder,
                'enabled': True,
            }
        )


def unseed_portals(apps, schema_editor):
    DistributionPortal = apps.get_model('collection', 'DistributionPortal')
    DistributionPortal.objects.filter(name__in=[p[2] for p in PORTALS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('collection', '0002_distribution_portal'),
    ]

    operations = [
        migrations.RunPython(seed_portals, unseed_portals),
    ]
