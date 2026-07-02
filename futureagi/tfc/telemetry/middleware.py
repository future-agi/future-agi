"""
Django middleware for OpenTelemetry context enrichment.

Provides rich context capture:
- Request/response bodies
- Query parameters with values
- Headers (filtered for security)
- User/organization context
- Database query details
- Performance timing

Add this middleware AFTER authentication middleware.

Usage in settings.py:
    MIDDLEWARE = [
        ...
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'tfc.telemetry.middleware.OTelContextMiddleware',  # After auth
        ...
    ]

Configuration in settings.py:
    OTEL_CAPTURE_REQUEST_BODY = True  # Capture request bodies
    OTEL_CAPTURE_RESPONSE_BODY = False  # Capture all response bodies
    OTEL_CAPTURE_RESPONSE_BODY_ON_ERROR = True  # Capture response body on 4xx/5xx
    OTEL_MAX_BODY_SIZE = 8192  # Max size for captured bodies
"""

import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from django.http import HttpRequest, HttpResponse

from .context import set_request_context, set_user_context

logger = structlog.get_logger(__name__)

# Defaults
DEFAULT_MAX_BODY_SIZE = 8192
DEFAULT_MAX_ITEMS = 50
SENSITIVE_BODY_PATH_PREFIXES = ("/telemetry/",)


def _safe_json(value: Any, max_size: int = DEFAULT_MAX_BODY_SIZE) -> str:
    """Safely serialize value to JSON string, truncating if needed."""
    try:
        if value is None:
            return "null"
        if isinstance(value, (str, int, float, bool)):
            s = str(value)
        elif isinstance(value, bytes):
            s = value.decode("utf-8", errors="replace")
        elif isinstance(value, dict):
            # Limit items and serialize
            truncated = dict(list(value.items())[:DEFAULT_MAX_ITEMS])
            s = json.dumps(truncated, default=str, ensure_ascii=False)
        elif isinstance(value, (list, tuple)):
            truncated = list(value[:DEFAULT_MAX_ITEMS])
            s = json.dumps(truncated, default=str, ensure_ascii=False)
        else:
            s = repr(value)

        if len(s) > max_size:
            return s[:max_size] + "...[truncated]"
        return s
    except Exception:
        return f"<{type(value).__name__}>"


def _parse_json_body(body: bytes, max_size: int = DEFAULT_MAX_BODY_SIZE) -> dict | None:
    """Parse JSON body, return None on failure."""
    try:
        if len(body) > max_size * 2:  # Allow some headroom for parsing
            return {"__truncated__": True, "__size__": len(body)}
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _mask_sensitive(data: dict, depth: int = 0) -> dict:
    """Recursively mask sensitive fields in a dictionary."""
    if depth > 3:  # Prevent infinite recursion
        return data

    sensitive = {
        "password",
        "token",
        "secret",
        "key",
        "auth",
        "credential",
        "api_key",
        "apikey",
        "authorization",
    }
    masked = {}

    for key, value in data.items():
        key_lower = key.lower()
        if any(s in key_lower for s in sensitive):
            masked[key] = "[REDACTED]"
        elif isinstance(value, dict):
            masked[key] = _mask_sensitive(value, depth + 1)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            masked[key] = [
                _mask_sensitive(v, depth + 1) if isinstance(v, dict) else v
                for v in value[:10]
            ]
        else:
            masked[key] = value

    return masked


class OTelContextMiddleware:
    """
    Middleware that enriches OpenTelemetry spans AND structlog context.

    Captures:
    - Request body (JSON/form data)
    - Query parameters
    - Headers (safe ones only)
    - User context (id, email, org)
    - Response body (on errors)
    - Timing information
    """

    # Headers safe to capture
    CAPTURE_HEADERS = {
        "content-type",
        "content-length",
        "accept",
        "accept-language",
        "user-agent",
        "referer",
        "origin",
        "x-request-id",
        "x-correlation-id",
        "x-forwarded-for",
        "x-real-ip",
        "x-forwarded-proto",
    }

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

        # Load config from Django settings
        from django.conf import settings

        self.capture_request_body = getattr(settings, "OTEL_CAPTURE_REQUEST_BODY", True)
        self.capture_response_body = getattr(
            settings, "OTEL_CAPTURE_RESPONSE_BODY", False
        )
        self.capture_response_on_error = getattr(
            settings, "OTEL_CAPTURE_RESPONSE_BODY_ON_ERROR", True
        )
        self.max_body_size = getattr(
            settings, "OTEL_MAX_BODY_SIZE", DEFAULT_MAX_BODY_SIZE
        )

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from opentelemetry import trace

        start_time = time.time()

        # Generate/get request ID
        request_id = (
            request.META.get("HTTP_X_REQUEST_ID")
            or request.META.get("HTTP_X_CORRELATION_ID")
            or str(uuid.uuid4())
        )

        # Bind context to structlog (flows through all logs in this request)
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            http_method=request.method,
            http_path=request.path,
            client_ip=self._get_client_ip(request),
        )

        # Set OTel span context
        set_request_context(
            request_id=request_id,
            correlation_id=request.META.get("HTTP_X_CORRELATION_ID"),
        )

        span = trace.get_current_span()

        # Capture request context in span
        if span.is_recording():
            self._capture_request(span, request)

        # Process request
        response = self.get_response(request)

        duration_ms = (time.time() - start_time) * 1000

        # Capture response context
        if span.is_recording():
            self._capture_response(span, request, response, duration_ms)

        # Bind response info to structlog
        structlog.contextvars.bind_contextvars(
            http_status=response.status_code,
            duration_ms=round(duration_ms, 2),
        )

        # Add headers for client correlation
        response["X-Request-ID"] = request_id

        return response

    def _capture_request(self, span, request: HttpRequest) -> None:
        """Capture request details in span."""
        try:
            # Route/path
            span.set_attribute("http.route", request.path)
            span.set_attribute("http.url", request.build_absolute_uri())

            # Query parameters (masked)
            if request.GET:
                params = _mask_sensitive(dict(request.GET.items()))
                span.set_attribute("http.query_params", _safe_json(params))

            # Headers (safe ones)
            headers = self._extract_headers(request)
            if headers:
                span.set_attribute("http.request.headers", _safe_json(headers))

            # Request body
            if (
                self.capture_request_body
                and not request.path.startswith(SENSITIVE_BODY_PATH_PREFIXES)
                and request.body
            ):
                self._capture_request_body(span, request)

            # Client IP
            client_ip = self._get_client_ip(request)
            if client_ip:
                span.set_attribute("http.client_ip", client_ip)

        except Exception as e:
            logger.debug("otel_request_capture_error", error=str(e))

    def _capture_request_body(self, span, request: HttpRequest) -> None:
        """Capture request body based on content type."""
        content_type = request.content_type or ""

        if "application/json" in content_type:
            body = _parse_json_body(request.body, self.max_body_size)
            if body:
                body = _mask_sensitive(body) if isinstance(body, dict) else body
                span.set_attribute("http.request.body", _safe_json(body))

        elif (
            "application/x-www-form-urlencoded" in content_type
            or "multipart/form-data" in content_type
        ):
            # Form data
            form_data = _mask_sensitive(dict(request.POST.items()))
            span.set_attribute("http.request.body", _safe_json(form_data))

            # Log file uploads (names only, not content)
            if request.FILES:
                files = {name: f.name for name, f in request.FILES.items()}
                span.set_attribute("http.request.files", _safe_json(files))

    def _capture_response(
        self,
        span,
        request: HttpRequest,
        response: HttpResponse,
        duration_ms: float,
    ) -> None:
        """Capture response details in span."""
        try:
            span.set_attribute("http.status_code", response.status_code)
            span.set_attribute("http.duration_ms", round(duration_ms, 2))

            # Response headers
            response_headers = {}
            for h in ["content-type", "content-length"]:
                if h in response:
                    response_headers[h] = response[h]
            if response_headers:
                span.set_attribute(
                    "http.response.headers", _safe_json(response_headers)
                )

            # User context (captured here after auth middleware ran)
            self._capture_user_context(span, request)

            # Response body
            is_error = response.status_code >= 400
            should_capture = self.capture_response_body or (
                is_error and self.capture_response_on_error
            )

            if should_capture and hasattr(response, "content"):
                self._capture_response_body(span, response, is_error)

        except Exception as e:
            logger.debug("otel_response_capture_error", error=str(e))

    def _capture_response_body(
        self, span, response: HttpResponse, is_error: bool
    ) -> None:
        """Capture response body."""
        content_type = response.get("Content-Type", "")

        if "application/json" in content_type:
            body = _parse_json_body(response.content, self.max_body_size)
            if body:
                span.set_attribute("http.response.body", _safe_json(body))
        elif is_error:
            # For errors, capture text
            try:
                text = response.content.decode("utf-8", errors="replace")[
                    : self.max_body_size
                ]
                span.set_attribute("http.response.body", text)
            except Exception:
                pass

    def _capture_user_context(self, span, request: HttpRequest) -> None:
        """Capture authenticated user context."""
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return

        user = request.user

        span.set_attribute("enduser.id", str(user.id))

        if hasattr(user, "email") and user.email:
            span.set_attribute("enduser.email", user.email)

        # Organization context
        org_id = None
        org_name = None

        if hasattr(user, "organization_id"):
            org_id = user.organization_id
        if hasattr(user, "organization") and user.organization:
            org_id = getattr(user.organization, "id", org_id)
            org_name = getattr(user.organization, "name", None)

        if org_id:
            span.set_attribute("organization.id", str(org_id))
            # Also bind to structlog
            structlog.contextvars.bind_contextvars(organization_id=str(org_id))
        if org_name:
            span.set_attribute("organization.name", org_name)

        # Also set via helper for consistency
        set_user_context(
            user_id=user.id,
            user_email=getattr(user, "email", None),
            organization_id=org_id,
            organization_name=org_name,
        )

        # Bind to structlog
        structlog.contextvars.bind_contextvars(
            user_id=str(user.id),
            user_email=getattr(user, "email", None),
        )

    def _extract_headers(self, request: HttpRequest) -> dict:
        """Extract safe headers from request."""
        headers = {}

        for header in self.CAPTURE_HEADERS:
            # Django converts headers to META format
            meta_key = f"HTTP_{header.upper().replace('-', '_')}"
            if meta_key in request.META:
                headers[header] = request.META[meta_key]

        # Direct META keys
        if "CONTENT_TYPE" in request.META:
            headers["content-type"] = request.META["CONTENT_TYPE"]
        if "CONTENT_LENGTH" in request.META:
            headers["content-length"] = request.META["CONTENT_LENGTH"]

        return headers

    def _get_client_ip(self, request: HttpRequest) -> str | None:
        """Get client IP, handling proxies."""
        x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded:
            return x_forwarded.split(",")[0].strip()
        return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR")


__all__ = ["OTelContextMiddleware"]
