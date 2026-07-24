from django.db import migrations, models


def backfill(apps, schema_editor):
    PGWRecord = apps.get_model('pgw', 'PGWRecord')
    for rec in PGWRecord.objects.all().iterator(chunk_size=1000):
        raw = rec.raw_data or {}
        flag = raw.get('PREPAID_FLAG') or raw.get('prepaid_flag') or ''
        if flag in ('0', 0):
            rec.prepaid_flag = 'POSTPAID'
        elif flag in ('1', 1):
            rec.prepaid_flag = 'PREPAID'
        elif flag in ('PREPAID', 'POSTPAID'):
            rec.prepaid_flag = flag
        else:
            rec.prepaid_flag = ''
        rec.save(update_fields=['prepaid_flag'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('pgw', '0004_alter_pgwrecord_rating_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='pgwrecord',
            name='prepaid_flag',
            field=models.CharField(blank=True, db_index=True, max_length=10),
        ),
        migrations.RunPython(backfill, noop),
    ]
