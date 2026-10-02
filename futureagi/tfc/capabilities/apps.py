from django.apps import AppConfig


class CapabilitiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tfc.capabilities"
    verbose_name = "Capabilities"

    def ready(self) -> None:
        from tfc.capabilities import _bootstrap

        _bootstrap.wire_resolvers()
