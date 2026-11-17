from django.apps import AppConfig


class AgentccConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "agentcc"
    # app_label pinned to "prism" so django_migrations / django_content_type
    # rows (recorded pre-rename) keep matching without a data migration.
    # Python import path is agentcc; Django's internal app_label stays prism.
    label = "prism"

    def ready(self):
        from django.conf import settings

        from agentcc.hosted import validate_hosted_configuration

        validate_hosted_configuration()

        # Run for WSGI/ASGI and workers, not only manage.py system checks.
        from agentcc.gateway_configuration import validate_hosted_gateway_public_url

        validate_hosted_gateway_public_url()

        # Register through drf-yasg's generator setting; this changes schema
        # metadata only, not the authenticators or permissions of any view.
        settings.SWAGGER_SETTINGS.setdefault(
            "DEFAULT_GENERATOR_CLASS",
            "agentcc.managed_credit_schema.ManagedCreditSchemaGenerator",
        )
        from agentcc import mcp_vendor_signals  # noqa: F401
