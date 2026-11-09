from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from accounts.services.hard_erase import install_hard_erase_guard

        install_hard_erase_guard()
        # Initialize relay replay protection before the web process accepts its
        # first request (URL modules can otherwise be loaded lazily).
        import accounts.signals.auth_cache_signals  # noqa: F401
        import accounts.signals.host_event_signals  # noqa: F401
        import accounts.signals.membership_signals  # noqa: F401
        import tfc.websocket_relay  # noqa: F401
