"""Authenticated-principal binding for user-relative telemetry filters.

``my_annotations`` is intentionally a user-relative filter.  The browser does
not own (and must not be trusted to provide) the user id used by the query.
Bind it at the authenticated request edge before filters are normalized,
hashed for a cursor/cache identity, persisted on an eval task, or compiled.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class FilterPrincipalContextError(ValueError):
    """A user-relative filter could not be bound to an authenticated user."""


def request_principal_id(request: Any) -> str | None:
    """Return the authenticated request user's stable id, if one is present."""

    user = getattr(request, "user", None)
    if user is None or getattr(user, "is_authenticated", True) is False:
        return None
    value = getattr(user, "pk", None) or getattr(user, "id", None)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def bind_my_annotations_principal(
    filter_payload: Any,
    *,
    principal_id: Any,
) -> Any:
    """Return a copy with every ``my_annotations`` filter server-bound.

    The filter payload can be a plain Observe filter list or an eval-task
    envelope containing nested ``filters``/``span_attributes_filters`` lists.
    Client-provided ``user_id`` values are always overwritten.  If a matching
    filter is present but no authenticated principal can be bound, this raises
    instead of allowing the query to broaden silently.
    """

    bound = deepcopy(filter_payload)
    normalized_principal = str(principal_id).strip() if principal_id is not None else ""
    found = False

    def _walk(value: Any) -> None:
        nonlocal found
        if isinstance(value, list):
            for item in value:
                _walk(item)
            return
        if not isinstance(value, dict):
            return

        column_id = value.get("column_id", value.get("columnId"))
        if column_id == "my_annotations":
            found = True
            config_key = "filter_config" if "filter_config" in value else "filterConfig"
            config = value.get(config_key)
            if not isinstance(config, dict):
                raise FilterPrincipalContextError(
                    "My annotations filter configuration is invalid"
                )
            if not normalized_principal:
                raise FilterPrincipalContextError(
                    "My annotations filter requires an authenticated user"
                )
            # The backend compiler consumes the canonical snake-case field.
            # Do not preserve or trust a client-selected principal.
            config["user_id"] = normalized_principal

        for item in value.values():
            _walk(item)

    _walk(bound)
    if found and not normalized_principal:
        # Defensive belt-and-braces: malformed shapes already raise above, but
        # keep the invariant explicit if traversal evolves.
        raise FilterPrincipalContextError(
            "My annotations filter requires an authenticated user"
        )
    return bound


def bind_request_my_annotations_principal(request: Any, filter_payload: Any) -> Any:
    """Bind a filter payload to ``request.user`` without trusting the client."""

    return bind_my_annotations_principal(
        filter_payload,
        principal_id=request_principal_id(request),
    )


def bound_my_annotations_principal(filter_payload: Any) -> str | None:
    """Read the single server-bound principal from a persisted filter payload.

    Conflicting or absent bindings fail closed by returning ``None``.  This is
    used only by legacy PostgreSQL task execution; CH queries consume the
    canonical ``filter_config.user_id`` directly.
    """

    principals: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                _walk(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("column_id", value.get("columnId")) == "my_annotations":
            config = value.get("filter_config", value.get("filterConfig"))
            if isinstance(config, dict):
                principal = config.get("user_id")
                if principal is not None and str(principal).strip():
                    principals.add(str(principal).strip())
        for item in value.values():
            _walk(item)

    _walk(filter_payload)
    if len(principals) != 1:
        return None
    return next(iter(principals))
