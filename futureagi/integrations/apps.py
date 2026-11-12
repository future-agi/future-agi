from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "integrations"

    def ready(self):
        import structlog

        from integrations.services.endpoints import log_endpoint_overrides

        log_endpoint_overrides(structlog.get_logger("integrations.endpoints"))
