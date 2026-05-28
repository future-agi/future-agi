from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        import accounts.signals.membership_signals  # noqa: F401
        import accounts.signals.onboarding_project_signals  # noqa: F401
