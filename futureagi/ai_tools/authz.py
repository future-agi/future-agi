"""Phase 3B — per-call authorization for the DRF bridge path.

The bridge invokes view action methods directly with a synthetic request
(`drf_bridge.py`: view built without ``dispatch()``), so DRF's
``check_permissions`` / ``check_throttles`` never run. Until 3B,
authorization on the bridge path was data-scoping only (``request.user``
stamping + ``get_queryset`` workspace scoping + ``workspace_context``
ContextVars). This module restores the authorization half:

- **Authenticated-user floor**: the project sets no
  ``DEFAULT_PERMISSION_CLASSES`` (DRF default = ``AllowAny``), so the bridge
  enforces an authenticated user regardless of what the view declares. Falcon
  always runs as the connected user; an anonymous context is a programming
  error or an attack, never a legitimate call.
- **View-level permissions**: every permission class the real view declares
  (via ``get_permissions()``, honoring overrides) is evaluated against the
  synthetic request with ``has_permission(request, view)`` — the same checks
  ``APIView.check_permissions`` would run under ``dispatch()``.
- **Fail closed**: a permission class that raises on the synthetic request
  denies the call instead of being skipped.

Object-level permissions are NOT re-implemented here: handlers that use
``GenericAPIView.get_object()`` already call ``check_object_permissions``
(works without ``dispatch()`` because the bridge sets
``view.request/.action/.kwargs``); handlers that query manually are covered
by workspace/org queryset scoping. The authz audit
(``ai_tools/tests/audit_authz.py`` → ``ai_tools/tests/authz_audit_report.md``)
documents per-view where that scoping comes from.

Throttle classes are deliberately not evaluated: Falcon's consumer applies
its own per-frame rate limit, and DRF throttles share cache keys with the
HTTP API — evaluating them on the bridge path would double-bill interactive
API usage.

This module is OSS-side and must not import from ``ee/``.
"""

from __future__ import annotations

import logging
from typing import Optional

from ai_tools.base import ToolResult

logger = logging.getLogger(__name__)

# Escape hatch (design §2.1) for tools whose view's permission classes are
# structurally incompatible with the bridge's synthetic request (e.g. a
# class that requires a live HTTP credential such as APIKeyPermission).
# SHIPS EMPTY and the 3B audit (ai_tools/tests/audit_authz.py) proves no
# bridged view needs it. Every future entry REQUIRES an inline justification
# comment naming the incompatible permission class and why it cannot be
# fixed at the view; `test_authz_negative.py::test_authz_exempt_ships_empty`
# pins the empty state so additions are a deliberate, reviewed act.
AUTHZ_EXEMPT: set[str] = set()


def permission_denied_result(
    tool_name: str,
    message: str,
    denied_by: Optional[str] = None,
) -> ToolResult:
    """Canonical clean PermissionDenied shape for the bridge path.

    ``content`` is what the LLM sees — actionable, no tracebacks/URLs.
    ``data`` carries the structured fields for the tool-call log / audit.
    """
    return ToolResult(
        content=(
            f"**Permission Denied:** You are not authorized to run "
            f"`{tool_name}`. {message} No action was taken."
        ),
        data={
            "tool": tool_name,
            "denied_by": denied_by,
            "reason": message,
        },
        is_error=True,
        error_code="PERMISSION_DENIED",
    )


def enforce_view_permissions(view, request, tool_name: str) -> Optional[ToolResult]:
    """Evaluate the view's real permission classes against the bridge request.

    Returns a PERMISSION_DENIED ``ToolResult`` when the call must not
    proceed, or ``None`` when authorized. Called by
    ``DRFBridgeTool.execute`` after the view is instantiated (so
    ``view.request/.action/.kwargs`` are set, matching what permission
    classes see under a real ``dispatch()``) and before the action method
    is invoked (zero side effects on deny).
    """
    if tool_name in AUTHZ_EXEMPT:
        # Justified, reviewed bypass (see AUTHZ_EXEMPT above) — the design's
        # escape hatch skips the whole evaluation, matching what an explicit
        # `if self.name not in AUTHZ_EXEMPT: check_permissions()` would do.
        logger.info("authz exempt tool %s — skipping permission evaluation", tool_name)
        return None

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return permission_denied_result(
            tool_name,
            "This tool requires an authenticated user and the request "
            "context has none.",
            denied_by="bridge_authentication_floor",
        )

    try:
        permissions = view.get_permissions()
    except Exception:
        logger.exception(
            "authz get_permissions failed for tool %s (view %s) — failing closed",
            tool_name,
            type(view).__name__,
        )
        return permission_denied_result(
            tool_name,
            "Authorization could not be evaluated for this action.",
            denied_by="get_permissions_error",
        )

    for permission in permissions:
        try:
            allowed = permission.has_permission(request, view)
        except Exception:
            # Fail CLOSED: a permission class that crashes on the synthetic
            # request must deny, not silently allow.
            logger.exception(
                "authz permission %s raised for tool %s (view %s) — failing closed",
                type(permission).__name__,
                tool_name,
                type(view).__name__,
            )
            return permission_denied_result(
                tool_name,
                "Authorization could not be evaluated for this action.",
                denied_by=type(permission).__name__,
            )
        if not allowed:
            message = getattr(permission, "message", None) or (
                "You do not have permission to perform this action."
            )
            logger.warning(
                "authz denied tool %s for user %s by %s",
                tool_name,
                getattr(user, "id", None),
                type(permission).__name__,
            )
            return permission_denied_result(
                tool_name,
                str(message),
                denied_by=type(permission).__name__,
            )

    return None


def describe_view_authz(viewset_cls) -> dict:
    """Static authz posture of a view class — used by the 3B audit script.

    Returns::

        {
            "permission_classes": ["IsAuthenticated", ...],
            "get_permissions_overridden": bool,   # custom get_permissions()
            "object_permissions": ["ClassName", ...],  # classes defining
                                                       # has_object_permission
            "queryset_scope_owner": "module.Class" | None,  # who defines
                                                            # get_queryset
        }
    """
    perms = []
    object_perms = []
    for p in getattr(viewset_cls, "permission_classes", ()) or ():
        name = getattr(p, "__name__", str(p))
        perms.append(name)
        # has_object_permission defined below rest_framework's no-op base?
        for klass in getattr(p, "__mro__", ()):
            if "has_object_permission" in klass.__dict__ and not klass.__module__.startswith(
                "rest_framework"
            ):
                object_perms.append(name)
                break

    get_permissions_overridden = any(
        "get_permissions" in klass.__dict__
        for klass in viewset_cls.__mro__
        if not klass.__module__.startswith("rest_framework")
    )

    queryset_scope_owner = None
    for klass in viewset_cls.__mro__:
        if "get_queryset" in klass.__dict__:
            queryset_scope_owner = f"{klass.__module__}.{klass.__name__}"
            break

    return {
        "permission_classes": perms,
        "get_permissions_overridden": get_permissions_overridden,
        "object_permissions": object_perms,
        "queryset_scope_owner": queryset_scope_owner,
    }
