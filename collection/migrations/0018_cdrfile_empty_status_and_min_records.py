from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('collection', '0017_add_replaylog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cdrfile',
            name='status',
            field=models.CharField(
                choices=[
                    ('COLLECTED', 'Collected'),
                    ('PENDING', 'Pending'),
                    ('PROCESSING', 'Processing'),
                    ('DECODED', 'Decoded'),
                    ('DISPATCHING', 'Dispatching'),
                    ('COMPLETED', 'Completed'),
                    ('FAILED', 'Failed'),
                    ('EMPTY', 'Empty (0 records)'),
                    ('DUPLICATE', 'Duplicate'),
                    ('STAGED', 'Staged (in staging directory)'),
                    ('PUBLISHED', 'Published (in stream root)'),
                ],
                default='COLLECTED',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='datasource',
            name='min_expected_records',
            field=models.IntegerField(
                default=0,
                help_text='Minimum records expected per file from this source. 0 = no minimum check.',
            ),
        ),
    ]
