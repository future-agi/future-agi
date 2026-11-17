"""Boot-time CORS overrides for request-id exposure."""

import os

from django.conf import UserSettingsHolder


def request_id_cors_overrides(settings):
    """Expose request ids unless the environment or a settings override says no.

    Accept the booting settings module without consulting Django's lazy
    settings proxy while that same module is still being initialized.
    For a nonblank environment value, use the module's parsed flag.
    Explicitly disabling exposure adds no setting, preserving the defaults.
    """
    name = "DSX_ERRORS_REQUEST_ID_EXPOSED"
    holder = getattr(settings, "_wrapped", settings)
    while isinstance(holder, UserSettingsHolder):
        if name in holder.__dict__ or name in holder.__dict__.get("_deleted", ()):
            enabled = getattr(settings, name, False) is True
            break
        holder = holder.default_settings
    else:
        value = os.environ.get(name, "").strip()
        enabled = not value or getattr(settings, name, False) is True

    if enabled:
        return {"CORS_EXPOSE_HEADERS": ["X-Request-ID"]}
    return {}
