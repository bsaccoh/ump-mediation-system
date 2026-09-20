from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_jobrecord'),
        ('collection', '0012_cdrfile_records_by_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='ActivityLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('timestamp', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('event_type', models.CharField(db_index=True, max_length=60)),
                ('stage', models.CharField(choices=[('COLLECTION', 'Collection'), ('DECODING', 'Decoding'), ('DISTRIBUTION', 'Distribution'), ('SYSTEM', 'System')], db_index=True, max_length=15)),
                ('stream', models.CharField(blank=True, max_length=10)),
                ('operator', models.CharField(blank=True, max_length=30)),
                ('level', models.CharField(choices=[('INFO', 'Info'), ('WARNING', 'Warning'), ('ERROR', 'Error')], default='INFO', max_length=10)),
                ('message', models.TextField()),
                ('details', models.JSONField(blank=True, default=dict)),
                ('cdr_file', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='collection.cdrfile')),
                ('source', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='collection.datasource')),
            ],
            options={
                'verbose_name': 'Activity Log',
                'verbose_name_plural': 'Activity Logs',
                'db_table': 'activity_logs',
                'ordering': ['-timestamp'],
            },
        ),
    ]
