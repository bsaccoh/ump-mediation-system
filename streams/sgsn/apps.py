from django.apps import AppConfig


class SgsnConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'streams.sgsn'
    label = 'sgsn'
    verbose_name = 'SGSN Stream (2G/3G Data)'
