from django.db import migrations


_PROFILES = [
    {
        'name': 'TEMS Pocket TRP',
        'parser_class': 'drive_test.parsers.trp_parser.TrpDriveTestParser',
        'vendor': 'TEMS',
        'file_extensions': ['.trp'],
        'magic_bytes': '504b',  # PK ZIP magic
    },
    {
        'name': 'Generic CSV',
        'parser_class': 'drive_test.parsers.csv_parser.CsvDriveTestParser',
        'vendor': '',
        'file_extensions': ['.csv'],
        'magic_bytes': '',
    },
]


def seed_trp_profile(apps, schema_editor):
    ParserProfile = apps.get_model('drive_test', 'ParserProfile')
    for p in _PROFILES:
        ParserProfile.objects.get_or_create(
            name=p['name'],
            defaults={**p, 'header_signature': '', 'default_config': {}, 'is_active': True},
        )


def remove_trp_profile(apps, schema_editor):
    ParserProfile = apps.get_model('drive_test', 'ParserProfile')
    ParserProfile.objects.filter(name__in=[p['name'] for p in _PROFILES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('drive_test', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_trp_profile, remove_trp_profile),
    ]
