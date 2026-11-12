"""Boot-time CORS overrides for the independently claimed request-id surface."""


def request_id_cors_overrides(settings):
    """OFF adds no setting, preserving django-cors-headers' pinned defaults.

    Accept the booting settings module rather than importing Django's lazy
    settings proxy while that same module is still being initialized.
    """
    if getattr(settings, "DSX_ERRORS_REQUEST_ID_EXPOSED", False):
        return {"CORS_EXPOSE_HEADERS": ["X-Request-ID"]}
    return {}
