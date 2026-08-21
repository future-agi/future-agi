import os

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

NON_STREAMING_TIMEOUT = 30.0


def resolve_harness_internal_url():
    return os.environ.get("HARNESS_INTERNAL_URL", "http://harness:8777").rstrip("/")


def internal_headers() -> dict[str, str]:
    # Stripped because the harness side strips too; both ends must normalize the
    # same way or a trailing newline from a secret file 401s unexplainably.
    secret = (getattr(settings, "INTERNAL_API_SECRET", "") or "").strip()
    if not secret:
        raise ImproperlyConfigured(
            "INTERNAL_API_SECRET is not set; requests to the harness cannot be authenticated"
        )
    return {"Authorization": f"Bearer {secret}"}
