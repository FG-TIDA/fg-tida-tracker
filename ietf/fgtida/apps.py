from django.apps import AppConfig


class FgTidaConfig(AppConfig):
    name = 'ietf.fgtida'
    verbose_name = 'FG-TIDA configuration'

    def ready(self):
        from .admin import configure_admin
        configure_admin()
