from django.apps import AppConfig


class IntegracoesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aplicativos.integracoes"
    verbose_name = "Integrações"

    def ready(self):
        from django.db.models.signals import post_migrate
        from .services.geo_configuration import bootstrap_geo_integrations
        post_migrate.connect(bootstrap_geo_integrations, sender=self, dispatch_uid="integracoes.geo_defaults")
