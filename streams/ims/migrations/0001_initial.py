from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('collection', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='IMSRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                # Identity
                ('record_type',    models.CharField(db_index=True, max_length=50)),
                ('service_type',   models.CharField(db_index=True, default='VOICE', max_length=30)),
                ('role_of_node',   models.CharField(blank=True, max_length=20)),
                ('sip_method',     models.CharField(blank=True, max_length=20)),
                ('session_id',     models.CharField(blank=True, db_index=True, max_length=255)),
                ('icid',           models.CharField(blank=True, db_index=True, max_length=255)),
                # Parties
                ('calling_number', models.CharField(blank=True, db_index=True, max_length=100)),
                ('called_number',  models.CharField(blank=True, db_index=True, max_length=100)),
                ('dialed_number',  models.CharField(blank=True, max_length=100)),
                ('charged_party',  models.CharField(blank=True, max_length=100)),
                ('calling_sip_uri', models.CharField(blank=True, max_length=255)),
                ('called_sip_uri',  models.CharField(blank=True, max_length=255)),
                # Subscriber
                ('imsi',        models.CharField(blank=True, db_index=True, max_length=20)),
                ('msisdn',      models.CharField(blank=True, db_index=True, max_length=20)),
                ('prepaid_flag', models.CharField(blank=True, db_index=True, max_length=10)),
                # Timing
                ('request_time',     models.DateTimeField(blank=True, null=True)),
                ('start_time',       models.DateTimeField(blank=True, db_index=True, null=True)),
                ('end_time',         models.DateTimeField(blank=True, null=True)),
                ('duration',         models.IntegerField(default=0)),
                ('ringing_duration', models.IntegerField(default=0)),
                # Network / routing
                ('node_address',        models.CharField(blank=True, max_length=100)),
                ('msc_number',          models.CharField(blank=True, max_length=50)),
                ('vlr_number',          models.CharField(blank=True, max_length=50)),
                ('originating_ioi',     models.CharField(blank=True, max_length=100)),
                ('terminating_ioi',     models.CharField(blank=True, max_length=100)),
                ('np_routing_number',   models.CharField(blank=True, max_length=50)),
                ('carrier_code',        models.CharField(blank=True, max_length=50)),
                ('call_property',       models.CharField(blank=True, max_length=30)),
                ('roaming_indicator',   models.CharField(blank=True, max_length=30)),
                ('call_category',       models.CharField(blank=True, max_length=50)),
                # Charging / service
                ('charging_category',    models.CharField(blank=True, max_length=30)),
                ('service_context_id',   models.CharField(blank=True, max_length=100)),
                ('sequence_number',      models.BigIntegerField(blank=True, null=True)),
                ('cause_for_closing',    models.CharField(blank=True, max_length=100)),
                ('service_reason_code',  models.CharField(blank=True, max_length=50)),
                ('online_charging_flag', models.CharField(blank=True, max_length=10)),
                ('supplementary_service', models.CharField(blank=True, max_length=100)),
                ('diversion_reason',     models.CharField(blank=True, max_length=50)),
                ('diversion_count',      models.IntegerField(default=0)),
                # Raw / status
                ('raw_data',   models.JSONField(blank=True, default=dict)),
                ('status',     models.CharField(
                    choices=[('VALID', 'Valid'), ('INVALID', 'Invalid'), ('DUPLICATE', 'Duplicate')],
                    default='VALID', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                # Foreign keys
                ('file', models.ForeignKey(
                    db_constraint=False,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='ims_records',
                    to='collection.cdrfile',
                )),
                ('source', models.ForeignKey(
                    blank=True, null=True, db_constraint=False,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to='collection.datasource',
                )),
            ],
            options={
                'verbose_name': 'IMS CDR Record',
                'verbose_name_plural': 'IMS CDR Records',
                'db_table': 'ims_records',
                'ordering': ['-start_time'],
                'indexes': [
                    models.Index(fields=['start_time', 'record_type'],
                                 name='ims_records_start_t_idx'),
                    models.Index(fields=['calling_number', 'start_time'],
                                 name='ims_records_calling_idx'),
                    models.Index(fields=['called_number', 'start_time'],
                                 name='ims_records_called_idx'),
                    models.Index(fields=['imsi', 'start_time'],
                                 name='ims_records_imsi_idx'),
                    models.Index(fields=['session_id'],
                                 name='ims_records_session_idx'),
                    models.Index(fields=['file', 'record_type'],
                                 name='ims_records_file_rt_idx'),
                ],
            },
        ),
    ]
