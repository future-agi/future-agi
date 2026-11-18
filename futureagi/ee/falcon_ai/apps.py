from django.apps import AppConfig


class FalconAIConfig(AppConfig):
    name = "ee.falcon_ai"
    label = "falcon_ai"
    verbose_name = "Falcon AI"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Register EE-only ai_tools by importing them so @register_tool fires.
        from ee.falcon_ai.tools.context import delete_memory  # noqa: F401
        from ee.falcon_ai.tools.context import list_memories  # noqa: F401
        from ee.falcon_ai.tools.context import save_memory  # noqa: F401
