from django.apps import AppConfig


class PgwConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'streams.pgw'
    verbose_name = 'PGW Stream (4G Data)'
