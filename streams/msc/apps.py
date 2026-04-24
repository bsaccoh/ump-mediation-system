from django.apps import AppConfig


class MscConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'streams.msc'
    label = 'msc'
    verbose_name = 'MSC Stream'
