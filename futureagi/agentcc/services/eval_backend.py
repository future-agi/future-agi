"""The fa-eval service audience is bound to one operator HTTP(S) origin.

Keep normalization aligned with agentcc-gateway/internal/evaltarget/target.go.
No DNS, database, gateway HTTP call or credential access is needed.
"""

from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured


def normalize_eval_origin(value):
    """Return a normalized origin, or empty for an invalid service destination."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or any(c.isspace() for c in value) or any(c in value for c in "?#\\"):
        return ""
    try:
        url = urlsplit(value)
        scheme = url.scheme.lower()
        host, port = url.hostname, url.port
        if (
            scheme not in ("http", "https")
            or not host
            or url.username is not None
            or url.password is not None
            or url.path.strip("/")
            or url.netloc.endswith(":")
            or (port is not None and port == 0)
        ):
            return ""
        host = host.lower()
        if ":" in host:
            host = f"[{host}]"
        if port is not None and (scheme, port) not in (("http", 80), ("https", 443)):
            host = f"{host}:{port}"
        return f"{scheme}://{host}"
    except ValueError:
        return ""


def check_eval_backend_alignment(django_base_url, gateway_fi_base_url):
    """Fail deploy if Django and the gateway would disagree about service auth.

    gateway_fi_base_url must come from the gateway's effective deployment
    environment, not Django's FI_BASE_URL (which may describe OTLP ingestion).
    Error text names settings only: an invalid URL may contain credentials.
    """
    django_origin = normalize_eval_origin(django_base_url)
    gateway_origin = normalize_eval_origin(gateway_fi_base_url)
    if not django_origin or not gateway_origin or django_origin != gateway_origin:
        raise ImproperlyConfigured(
            "Django BASE_URL and gateway FI_BASE_URL must be equal after "
            "normalization for gateway_service eval authentication. Set both to "
            "the same HTTP(S) origin, without credentials, a path, query or fragment."
        )
