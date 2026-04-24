from django.apps import AppConfig


class SgwConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'streams.sgw'
    label = 'sgw'
    verbose_name = 'SGW Stream (4G Serving Gateway)'
