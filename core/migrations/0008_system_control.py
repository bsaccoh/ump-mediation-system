from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_add_alert_threshold'),
    ]

    operations = [
        migrations.CreateModel(
            name='SystemControl',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('intake_paused', models.BooleanField(default=False)),
                ('intake_paused_reason', models.CharField(blank=True, max_length=500)),
                ('intake_paused_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('intake_paused_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'System Control',
                'db_table': 'system_control',
            },
        ),
    ]
