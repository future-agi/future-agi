"""Service registry loader — ensures all platform services are imported and registered."""

_loaded = False


def ensure_services_loaded():
    """Import all platform service modules to trigger their register_service() calls."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    import integrations.services.cloud_storage_service  # noqa: F401
    import integrations.services.datadog_service  # noqa: F401
    import integrations.services.langfuse_service  # noqa: F401
    import integrations.services.linear_service  # noqa: F401
    import integrations.services.mixpanel_service  # noqa: F401
    import integrations.services.pagerduty_service  # noqa: F401
    import integrations.services.posthog_service  # noqa: F401
    import integrations.services.queue_service  # noqa: F401
    import integrations.services.slack_service  # noqa: F401
