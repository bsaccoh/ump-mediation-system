from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Script',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150, unique=True)),
                ('script_type', models.CharField(
                    choices=[('PYTHON', 'Python'), ('SHELL', 'Shell / Bash'), ('SQL', 'SQL')],
                    default='PYTHON', max_length=10)),
                ('status', models.CharField(
                    choices=[('ACTIVE', 'Active'), ('INACTIVE', 'Inactive'), ('DRAFT', 'Draft')],
                    default='DRAFT', max_length=10)),
                ('description', models.TextField(blank=True)),
                ('content', models.TextField(help_text='Script source code')),
                ('tags', models.CharField(blank=True, help_text='Comma-separated tags', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='scripts', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'scripts_script', 'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='ScriptExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('run_status', models.CharField(
                    choices=[('PENDING', 'Pending'), ('RUNNING', 'Running'), ('SUCCESS', 'Success'), ('FAILED', 'Failed')],
                    default='PENDING', max_length=10)),
                ('output', models.TextField(blank=True)),
                ('error', models.TextField(blank=True)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('duration_ms', models.IntegerField(blank=True, null=True, help_text='Duration in milliseconds')),
                ('script', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='executions', to='scripts.script')),
                ('run_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'scripts_execution', 'ordering': ['-started_at']},
        ),
    ]
