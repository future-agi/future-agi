from django.apps import AppConfig


class DeploymentTelemetryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tfc.deployment_telemetry"
    verbose_name = "Deployment telemetry"
