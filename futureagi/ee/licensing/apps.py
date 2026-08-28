from django.apps import AppConfig


class LicensingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ee.licensing"
    label = "ee_licensing"
    verbose_name = "EE Licensing"

    def ready(self) -> None:
        from ee.licensing.startup import validate_on_startup

        validate_on_startup()
