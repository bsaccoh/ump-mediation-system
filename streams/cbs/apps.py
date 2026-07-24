from django.apps import AppConfig


class CbsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'streams.cbs'
    label = 'cbs'
    verbose_name = 'CBS Stream'
