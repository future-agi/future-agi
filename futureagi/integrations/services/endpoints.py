"""Server-side endpoint overrides of the export services.

Each variable replaces one vendor's API host for the service that exports to
it, and nothing else reads it. It is a deployment setting for tests and for a
self-hosted proxy in front of the vendor: unset (the default) the service calls
the vendor's real host; it is never shown in the UI and never set per
organization or per request. deploy/env-reference.toml documents each one.
"""

import os
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

# Variable -> the destination whose host it replaces.
ENDPOINT_OVERRIDES = {
    "INTEGRATIONS_DATADOG_URL": "Datadog",
    "INTEGRATIONS_MIXPANEL_URL": "Mixpanel",
    "INTEGRATIONS_S3_ENDPOINT_URL": "Amazon S3",
    "INTEGRATIONS_LINEAR_URL": "Linear",
}


def _refuse(variable: str, rule: str) -> ImproperlyConfigured:
    # Names the variable and the rule, never the value: a value can carry a
    # credential.
    return ImproperlyConfigured(f"{variable} {rule}")


def _checked_override(variable: str) -> tuple[str, str]:
    """Return only a valid value or a fixed rule; never raise with URL locals."""
    value = os.environ.get(variable) or ""
    if not value:
        return "", ""
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return "", "must carry no whitespace or control characters."
    value = value.rstrip("/")
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018
    except ValueError:
        return "", "must be an http or https URL with a valid host and port."
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "", "must be an http or https URL with a host."
    if "@" in parts.netloc:
        return "", "must carry no credentials: leave user info out of the URL."
    if parts.query or parts.fragment or "?" in value or "#" in value:
        return "", "must carry no query or fragment."
    return value, ""


def endpoint_override(variable: str) -> str:
    """The base URL set in `variable`, without a trailing slash; "" when unset.

    A set value is an http or https URL with a host (and optionally a port and
    a path) and nothing else: no user info, no query, no fragment. Any other
    value raises ImproperlyConfigured, which names the variable but not its
    value, so a process with a malformed override does not start.
    """
    if variable not in ENDPOINT_OVERRIDES:
        raise KeyError(f"{variable} is not an integration endpoint override")
    value, rule = _checked_override(variable)
    if rule:
        # The helper has returned: neither the raw value nor urlsplit's parts
        # can enter exception frames (including frame-local error reporting).
        raise _refuse(variable, rule)
    return value


def _override_host(variable: str) -> str:
    value = endpoint_override(variable)
    if not value:
        return ""
    parts = urlsplit(value)
    host = parts.hostname or ""
    return f"{host}:{parts.port}" if parts.port else host


def log_endpoint_overrides(logger) -> None:
    """One line per override that is set, naming the variable and its host.

    Called once at process start (IntegrationsConfig.ready), so a value left
    set in production shows in the logs, and a malformed value stops the
    process there. Only the host and port are logged, never the path.
    """
    for variable, destination in ENDPOINT_OVERRIDES.items():
        host = _override_host(variable)
        if not host:
            continue
        logger.warning(
            "integration_endpoint_override",
            variable=variable,
            destination=destination,
            host=host,
        )
