from django.apps import AppConfig


class SimulateConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "simulate"
    verbose_name = "Simulate"

    def ready(self):
        """
        Initialize the test executor service when Django starts
        This method is called when Django is ready to serve requests
        """
        # Only start the service if we're in a web server context
        # (not during management commands like migrate, collectstatic, etc.)
        from simulate import signals  # noqa: F401
