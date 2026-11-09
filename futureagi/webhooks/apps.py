from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    name = "webhooks"
    label = "webhooks"
    verbose_name = "Webhooks"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        import os

        # G1 (WH-2): a member who leaves or loses the level turns their endpoints off (webhooks.api_offboarding).
        from webhooks import api_offboarding

        api_offboarding.connect()

        # R10: FI_WEBHOOKS_TIME_SCALE on hosted or with DEBUG off logs an error at startup (and is ignored).
        if os.environ.get("FI_WEBHOOKS_TIME_SCALE"):
            from webhooks import config

            config.time_scale()
