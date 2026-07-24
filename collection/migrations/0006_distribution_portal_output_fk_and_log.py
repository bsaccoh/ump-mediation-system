from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('collection', '0005_link_portals_to_vendors'),
        ('portals', '0004_outputschema_distributionrule'),
    ]

    operations = [
        migrations.AddField(
            model_name='distributionportal',
            name='output_portal',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='distribution_portals',
                to='portals.outputportal',
                help_text='Delivery connection used to actually send files for this leaf',
            ),
        ),
        migrations.CreateModel(
            name='DistributionLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('filename', models.CharField(blank=True, max_length=500)),
                ('record_count', models.IntegerField(default=0)),
                ('file_size', models.BigIntegerField(default=0)),
                ('status', models.CharField(
                    choices=[('SUCCESS', 'Success'), ('FAILED', 'Failed'), ('SKIPPED', 'Skipped')],
                    db_index=True, default='SUCCESS', max_length=10,
                )),
                ('error', models.TextField(blank=True)),
                ('delivered_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('cdr_file', models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name='distribution_logs',
                    to='collection.cdrfile',
                )),
                ('output_portal', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='delivery_logs',
                    to='portals.outputportal',
                )),
                ('rule', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='logs',
                    to='portals.distributionrule',
                )),
            ],
            options={
                'verbose_name': 'Distribution Log',
                'verbose_name_plural': 'Distribution Logs',
                'db_table': 'distribution_logs',
                'ordering': ['-delivered_at'],
            },
        ),
    ]
