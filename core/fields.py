from django.db import models

from .crypto import decrypt_value, encrypt_value


class EncryptedCharField(models.CharField):
    """CharField that encrypts values before storing and decrypts on retrieval."""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_value(value) if value else value

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value) if value else value

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        path = 'core.fields.EncryptedCharField'
        return name, path, args, kwargs
