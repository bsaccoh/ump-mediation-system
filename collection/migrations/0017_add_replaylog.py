from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('collection', '0016_add_staging_fields'),
        ('portals', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReplayLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('requested_by', models.CharField(blank=True, max_length=150)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('task_id', models.CharField(blank=True, max_length=100)),
                ('status', models.CharField(
                    choices=[('PENDING', 'Pending'), ('SUCCESS', 'Success'), ('FAILED', 'Failed')],
                    default='PENDING', max_length=10,
                )),
                ('records_delivered', models.IntegerField(default=0)),
                ('error', models.TextField(blank=True)),
                ('cdr_file', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='replay_logs',
                    to='collection.cdrfile',
                )),
                ('output_portal', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to='portals.outputportal',
                )),
            ],
            options={
                'verbose_name': 'Replay Log',
                'verbose_name_plural': 'Replay Logs',
                'db_table': 'collection_replay_logs',
                'ordering': ['-requested_at'],
            },
        ),
    ]
