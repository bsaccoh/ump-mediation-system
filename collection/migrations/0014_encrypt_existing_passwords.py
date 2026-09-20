from django.db import migrations


def encrypt_plaintext_passwords(apps, schema_editor):
    from core.crypto import encrypt_value, is_encrypted

    DataSource = apps.get_model('collection', 'DataSource')
    for ds in DataSource.objects.exclude(sftp_password=''):
        if not is_encrypted(ds.sftp_password):
            ds.sftp_password = encrypt_value(ds.sftp_password)
            ds.save(update_fields=['sftp_password'])

    try:
        InputPortal = apps.get_model('portals', 'InputPortal')
        for p in InputPortal.objects.exclude(password=''):
            if not is_encrypted(p.password):
                p.password = encrypt_value(p.password)
                p.save(update_fields=['password'])
    except LookupError:
        pass

    try:
        OutputPortal = apps.get_model('portals', 'OutputPortal')
        for p in OutputPortal.objects.exclude(password=''):
            if not is_encrypted(p.password):
                p.password = encrypt_value(p.password)
                p.save(update_fields=['password'])
    except LookupError:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('collection', '0013_encrypted_password_fields'),
        ('portals', '0011_encrypted_password_fields'),
    ]

    operations = [
        migrations.RunPython(
            encrypt_plaintext_passwords,
            migrations.RunPython.noop,
        ),
    ]
