# Hand-crafted initial migration for the portals app.
# Generated for Django 4.x — 2026-04-22

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='InputPortal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('portal_type', models.CharField(
                    choices=[('FTP', 'FTP'), ('SFTP', 'SFTP'), ('LOCAL', 'Local Directory'), ('API', 'REST API')],
                    max_length=10,
                )),
                ('stream_type', models.CharField(
                    choices=[('MSC', 'MSC'), ('PGW', 'PGW'), ('SGSN', 'SGSN'), ('SGW', 'SGW'), ('ALL', 'All Streams')],
                    default='ALL',
                    max_length=10,
                )),
                ('host', models.CharField(blank=True, max_length=255)),
                ('port', models.IntegerField(blank=True, null=True)),
                ('username', models.CharField(blank=True, max_length=100)),
                ('password', models.CharField(blank=True, max_length=255)),
                ('directory', models.CharField(blank=True, max_length=500)),
                ('file_pattern', models.CharField(blank=True, default='*.dat', max_length=200)),
                ('polling_interval', models.IntegerField(default=300, help_text='Polling interval in seconds')),
                ('is_active', models.BooleanField(default=True)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Input Portal',
                'verbose_name_plural': 'Input Portals',
                'db_table': 'portals_input_portal',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='OutputPortal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('portal_type', models.CharField(
                    choices=[('FTP', 'FTP'), ('SFTP', 'SFTP'), ('LOCAL', 'Local Directory'), ('DATABASE', 'Database'), ('API', 'REST API')],
                    max_length=10,
                )),
                ('stream_type', models.CharField(
                    choices=[('MSC', 'MSC'), ('PGW', 'PGW'), ('SGSN', 'SGSN'), ('SGW', 'SGW'), ('ALL', 'All Streams')],
                    default='ALL',
                    max_length=10,
                )),
                ('output_format', models.CharField(
                    choices=[('CSV', 'CSV'), ('JSON', 'JSON'), ('XML', 'XML')],
                    default='CSV',
                    max_length=10,
                )),
                ('host', models.CharField(blank=True, max_length=255)),
                ('port', models.IntegerField(blank=True, null=True)),
                ('username', models.CharField(blank=True, max_length=100)),
                ('password', models.CharField(blank=True, max_length=255)),
                ('directory', models.CharField(blank=True, max_length=500)),
                ('is_active', models.BooleanField(default=True)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Output Portal',
                'verbose_name_plural': 'Output Portals',
                'db_table': 'portals_output_portal',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Plugin',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('plugin_type', models.CharField(
                    choices=[('DECODER', 'Decoder'), ('ENRICHMENT', 'Enrichment'), ('VALIDATION', 'Validation'), ('EXPORT', 'Export'), ('NOTIFICATION', 'Notification')],
                    max_length=20,
                )),
                ('version', models.CharField(default='1.0.0', max_length=20)),
                ('is_active', models.BooleanField(default=True)),
                ('description', models.TextField(blank=True)),
                ('config', models.JSONField(blank=True, default=dict)),
                ('author', models.CharField(blank=True, max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Plugin',
                'verbose_name_plural': 'Plugins',
                'db_table': 'portals_plugin',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Resource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('resource_type', models.CharField(
                    choices=[('WORKER', 'Processing Worker'), ('QUEUE', 'Message Queue'), ('STORAGE', 'Storage'), ('DATABASE', 'Database'), ('CACHE', 'Cache')],
                    max_length=15,
                )),
                ('status', models.CharField(
                    choices=[('ONLINE', 'Online'), ('OFFLINE', 'Offline'), ('DEGRADED', 'Degraded'), ('MAINTENANCE', 'Maintenance')],
                    default='ONLINE',
                    max_length=15,
                )),
                ('host', models.CharField(blank=True, max_length=255)),
                ('config', models.JSONField(blank=True, default=dict)),
                ('description', models.TextField(blank=True)),
                ('is_monitored', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Resource',
                'verbose_name_plural': 'Resources',
                'db_table': 'portals_resource',
                'ordering': ['name'],
            },
        ),
    ]
