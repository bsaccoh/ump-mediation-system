from django.apps import AppConfig


class InterconnectConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'interconnect'
    verbose_name = 'Interconnect Billing'

    def ready(self):
        # Wire up post_save signals (Settlement → Invoice status)
        from . import signals  # noqa: F401
