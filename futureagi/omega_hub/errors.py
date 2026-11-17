"""Hub errors: `{"error": {"code", "message", "fix"}}` (SPEC §9.1), with texts from err_dict."""

from __future__ import annotations

from rest_framework import exceptions as drf
from rest_framework.response import Response

from tfc.utils.error_codes import err_dict


class HubError(Exception):
    """Raised anywhere in the Hub; the views turn it into the error envelope."""

    def __init__(self, code: str, status: int = 400, message: str | None = None, extra: dict | None = None,
                 headers: dict | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.message = message
        self.extra = extra or {}
        self.headers = headers or {}


class HubMoved(HubError):
    """A moved reference (SPEC §3.4, §9.2): 301 with Location and `{movedTo}` for clients that do not follow."""

    def __init__(self, *, old_prefix: str, new_prefix: str, moved_to: str):
        super().__init__("OMEGA_HUB_MOVED", 301)
        self.old_prefix = old_prefix
        self.new_prefix = new_prefix
        self.moved_to = moved_to

    def location(self, request) -> str:
        path = request.get_full_path()
        return path.replace(self.old_prefix, self.new_prefix, 1)


def error_body(code: str, message: str | None = None, extra: dict | None = None) -> dict:
    texts = err_dict.get(code) or [code.replace("_", " ").capitalize(), ""]
    body = {"error": {"code": code, "message": message or texts[0], "fix": texts[1] if len(texts) > 1 else ""}}
    body.update(extra or {})
    return body


def hub_response(error: HubError) -> Response:
    return Response(error_body(error.code, error.message, error.extra), status=error.status, headers=error.headers)


_DRF_CODES = (
    (drf.NotAuthenticated, "OMEGA_HUB_UNAUTHENTICATED"),
    (drf.AuthenticationFailed, "OMEGA_HUB_UNAUTHENTICATED"),
    (drf.PermissionDenied, "OMEGA_HUB_FORBIDDEN"),
    (drf.NotFound, "OMEGA_HUB_NOT_FOUND"),
    (drf.Throttled, "OMEGA_HUB_THROTTLED"),
    (drf.ParseError, "OMEGA_HUB_INVALID"),
    (drf.ValidationError, "OMEGA_HUB_INVALID"),
    (drf.UnsupportedMediaType, "OMEGA_HUB_INVALID"),
)


def hub_exception_handler(exc, context):
    """Exception handler for every Hub view (set per view, not in settings)."""
    from django.http import Http404

    if isinstance(exc, HubMoved):
        request = context.get("request")
        location = exc.location(request) if request is not None else exc.new_prefix
        return Response({"movedTo": exc.moved_to}, status=301, headers={"Location": location})
    if isinstance(exc, HubError):
        return hub_response(exc)
    from omega_rollout.enforce import ProductUnavailable

    if isinstance(exc, ProductUnavailable):
        # The rollout codes are the same on every wall (READINESS 3.13): no OMEGA_HUB_ prefix.
        headers = {"Retry-After": "30"} if exc.status_code == 503 else {}
        return Response(error_body(exc.reason), status=exc.status_code, headers=headers)
    if isinstance(exc, Http404):
        return Response(error_body("OMEGA_HUB_NOT_FOUND"), status=404)
    if isinstance(exc, drf.APIException):
        code = next((value for kind, value in _DRF_CODES if isinstance(exc, kind)), "OMEGA_HUB_INVALID")
        detail = exc.detail if isinstance(exc.detail, str) else None
        headers = {}
        if isinstance(exc, drf.Throttled) and exc.wait:
            headers["Retry-After"] = str(int(exc.wait))
        status = exc.status_code
        if isinstance(exc, (drf.NotAuthenticated, drf.AuthenticationFailed)):
            status = 401
        extra = {} if isinstance(exc.detail, str) else {"details": exc.detail}
        return Response(error_body(code, detail, extra), status=status, headers=headers)
    return None
