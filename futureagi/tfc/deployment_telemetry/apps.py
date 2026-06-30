from django.apps import AppConfig


class DeploymentTelemetryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tfc.deployment_telemetry"
    verbose_name = "Deployment telemetry"

    # No ``ready()``: the scheduled cycle calls ``_log_disclosure`` itself
    # (deduped per process), and a boot-time call could kill Django startup
    # on a half-installed EE.
