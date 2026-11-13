from django.apps import AppConfig


class SocketsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sockets"

    def ready(self):
        from sockets import signals  # noqa: F401 - registers membership receivers
