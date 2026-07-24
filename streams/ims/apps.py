from django.apps import AppConfig


class ImsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'streams.ims'
    verbose_name = 'IMS Stream (VoLTE / VoBB)'
