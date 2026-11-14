from django.apps import AppConfig


class AiToolsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai_tools"
    verbose_name = "AI Tools"

    def ready(self):
        # Import the native tool modules to trigger @register_tool decorators,
        # then add the OpenAPI-generated catalog shared with the MCP server.
        import ai_tools.tools  # noqa: F401
        from ai_tools.generated import register_generated_tools

        register_generated_tools()
