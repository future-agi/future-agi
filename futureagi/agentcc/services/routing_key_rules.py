"""Org routing keys the gateway stores but does not run.

The org-config contract accepts five routing keys that the gateway does not
enforce for an org:

- ``routing.scheduled``, ``routing.access_groups`` and ``routing.mirror`` have
  no runtime. A new or changed value is refused with 400
  ``unsupported_routing_key`` (``param`` ``routing.<key>``), so no control
  saves a setting that silently does nothing. A value the org's active version
  already holds is saved unchanged, with a warning.
- ``routing.conditional_routes`` and ``routing.default_model`` are stored with
  a warning.

Removing a key, or clearing it to ``null`` or an empty value, is never
refused. Activating a stored version writes no new value, so it is never
refused either; its response carries the same warnings.
"""

from collections.abc import Mapping
from typing import Any

from agentcc.services.config_push import OrgConfigRejected

UNSUPPORTED_ROUTING_KEY = "unsupported_routing_key"
ROUTING_KEY_IGNORED = "routing_key_ignored"
ROUTING_KEY_NOT_ENFORCED = "routing_key_not_enforced"

# No runtime: refused when new or changed, kept with a warning when held.
UNSUPPORTED_KEYS = ("scheduled", "access_groups", "mirror")
# Stored with a warning.
NOT_ENFORCED_KEYS = ("conditional_routes", "default_model")

IGNORED_MESSAGE = "stored, not supported; ignored"
NOT_ENFORCED_MESSAGE = "stored, not enforced"


class UnsupportedRoutingKey(OrgConfigRejected):
    """A save would store a new or changed value the gateway never runs."""

    code = UNSUPPORTED_ROUTING_KEY

    def __init__(self, key: str):
        self.param = f"routing.{key}"
        super().__init__(
            f"{self.param} is not supported by the gateway, so a new or changed "
            "value cannot be saved. Remove it, or keep the value the active "
            "version already holds."
        )


def check_routing_keys(active: Any, config: Any) -> list[dict[str, str]]:
    """Refuse a new or changed unsupported routing value; return the warnings.

    ``active`` is the org's active version before the save (``None`` when it
    has none) and ``config`` the version about to be saved. Raises
    ``UnsupportedRoutingKey`` for the first such key.
    """
    for key in UNSUPPORTED_KEYS:
        new = _value(config, key)
        if new is not None and new != _value(active, key):
            raise UnsupportedRoutingKey(key)
    return routing_key_warnings(config)


def routing_key_warnings(config: Any) -> list[dict[str, str]]:
    """The warnings for the routing keys ``config`` holds, in a fixed order."""
    warnings = []
    for key in UNSUPPORTED_KEYS:
        if _value(config, key) is not None:
            warnings.append(_warning(ROUTING_KEY_IGNORED, key, IGNORED_MESSAGE))
    for key in NOT_ENFORCED_KEYS:
        if _value(config, key) is not None:
            warnings.append(
                _warning(ROUTING_KEY_NOT_ENFORCED, key, NOT_ENFORCED_MESSAGE)
            )
    return warnings


def with_rejection_param(response: Any, exc: Exception) -> Any:
    """Add the rejected key's ``param`` to a 400 response for ``exc``.

    The error envelope carries the exception's ``code`` and message; a
    rejection that names one config key (``UnsupportedRoutingKey``) also
    names it as ``param``.
    """
    param = getattr(exc, "param", None)
    if param:
        response.data["param"] = param
    return response


def _warning(code: str, key: str, message: str) -> dict[str, str]:
    return {"code": code, "param": f"routing.{key}", "message": message}


def _value(config: Any, key: str) -> Any:
    """The stored value of ``routing.<key>``; ``None`` when absent or empty."""
    routing = getattr(config, "routing", None) if config is not None else None
    if not isinstance(routing, Mapping):
        return None
    value = routing.get(key)
    if value is None or value in ({}, [], ""):
        return None
    return value
