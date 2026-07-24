from django.db import migrations

# Seed vendors and link existing distribution portals to them
VENDORS = [
    ('SL_BIGDATA',  'SL BIGDATA',       'Big Data platform destination'),
    ('BILLING',     'Billing System',   'Billing mediation destination'),
    ('ARCHIVE',     'Archive',          'Long-term CDR archive destination'),
]

# portal name → vendor code
PORTAL_VENDOR_MAP = {
    'SL_BIGDATA_HMSC':   'SL_BIGDATA',
    'SL_BIGDATA_HPGW':   'SL_BIGDATA',
    'SL_BIGDATA_HSGW':   'SL_BIGDATA',
    'SL_BIGDATA_HSGSN':  'SL_BIGDATA',
    'BILLING_HMSC':      'BILLING',
    'BILLING_HPGW':      'BILLING',
    'BILLING_HSGSN':     'BILLING',
    'ARCHIVE_MSC':       'ARCHIVE',
    'ARCHIVE_DATA':      'ARCHIVE',
}


def link_portals(apps, schema_editor):
    Vendor = apps.get_model('reference', 'Vendor')
    DistributionPortal = apps.get_model('collection', 'DistributionPortal')

    # Create vendors
    vendor_objs = {}
    for code, name, description in VENDORS:
        v, _ = Vendor.objects.get_or_create(
            code=code,
            defaults={'name': name, 'description': description, 'enabled': True}
        )
        vendor_objs[code] = v

    # Link existing portals
    for portal_name, vendor_code in PORTAL_VENDOR_MAP.items():
        DistributionPortal.objects.filter(name=portal_name).update(
            vendor=vendor_objs[vendor_code]
        )


def unlink_portals(apps, schema_editor):
    Vendor = apps.get_model('reference', 'Vendor')
    DistributionPortal = apps.get_model('collection', 'DistributionPortal')
    DistributionPortal.objects.filter(name__in=PORTAL_VENDOR_MAP.keys()).update(vendor=None)
    Vendor.objects.filter(code__in=[v[0] for v in VENDORS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('collection', '0004_add_vendor_fk_to_distribution_portal'),
        ('reference', '0007_add_vendor'),
    ]

    operations = [
        migrations.RunPython(link_portals, unlink_portals),
    ]
