"""
PostHog API analytics middleware.

Captures authenticated API requests and allowlisted authentication outcomes:
- Endpoint, method, status code, response time (p50/p95/p99 tracking in PostHog)
- User & organization & workspace context (group analytics)
- No manual instrumentation needed per view

Add AFTER authentication middleware so user context is available.

Usage in settings.py:
    MIDDLEWARE = [
        ...
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        ...
        'tfc.middleware.posthog_middleware.PostHogMiddleware',
    ]

Configuration via env vars:
    POSTHOG_API_KEY - required, your project API key
    POSTHOG_HOST - optional, defaults to https://us.i.posthog.com
"""

import os
import time

import structlog
from django.http import HttpRequest, HttpResponse

from tfc.settings.settings import default_error_next_url

logger = structlog.get_logger(__name__)

# Paths to exclude from tracking (health checks, static, admin, docs, etc.)
EXCLUDED_PREFIXES = (
    "/health",
    "/ready",
    "/static/",
    "/admin/",
    "/favicon.ico",
    "/__debug__/",
    "/swagger/",
    "/redoc/",
)

# These resolved views can emit a minimal outcome even before login.
AUTH_OUTCOME_VIEWS = frozenset(
    {
        "user-signup",
        "token_obtain_pair",
        "activate-account",
        "initiate-password-reset",
        "reset-password-confirm",
        "accept_invitation_email",
        "login",
        "auth0_callback",
        "github-callback",
        "microsoft-callback",
    }
)


class PostHogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # Lazy import to avoid circular imports at module level
        self._tracker = None

    @property
    def tracker(self):
        if self._tracker is None:
            from analytics.posthog_util import posthog_tracker

            self._tracker = posthog_tracker
        return self._tracker

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Skip excluded paths
        if request.path.startswith(EXCLUDED_PREFIXES):
            return self.get_response(request)

        start_time = time.time()
        response = self.get_response(request)
        duration_ms = round((time.time() - start_time) * 1000, 2)

        # The key-gated PostHog integration is optional.  The self-hosted
        # sales stream is independent and records only an allowlisted,
        # actor-aware request envelope with a durable local outbox.
        try:
            from tfc.deployment_telemetry.events import record_request_event

            record_request_event(request, response, duration_ms)
        except Exception:
            logger.debug("deployment_telemetry_request_event_failed", exc_info=True)

        if not self.tracker.is_enabled:
            return response

        try:
            self._capture_api_event(request, response, duration_ms)
        except Exception:
            # Never let analytics break the request
            logger.debug("posthog_middleware_error", exc_info=True)

        return response

    def _capture_api_event(self, request, response, duration_ms):
        user = getattr(request, "user", None)
        user_id = None
        if user and getattr(user, "is_authenticated", False):
            user_id = user.id
        match = getattr(request, "resolver_match", None)
        # Never fall back to the raw path: even an unmatched URL can contain a token.
        path = f"/{match.route}" if match and match.route is not None else "unresolved"
        if match and match.view_name in AUTH_OUTCOME_VIEWS:
            status = response.status_code
            error_code = None
            if status >= 400:
                error_code = "auth_request_failed"
            elif status == 302:
                location = response.get("Location", "")
                if location.startswith(f"{default_error_next_url}&"):
                    error_code = "auth_denied"
            self.tracker.capture(
                user_id=user_id,
                event_name="api_request",
                properties={
                    "path": path,
                    "status_code": status,
                    "error_code": error_code,
                },
            )
            return
        if user_id is None:
            return

        properties = {
            "method": request.method,
            "path": path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "content_type": response.get("Content-Type", ""),
            "environment": os.getenv("ENV_TYPE", "local"),
        }

        # Add query params count (not values, for privacy)
        if request.GET:
            properties["query_param_count"] = len(request.GET)

        # Resolve view name for cleaner grouping
        if match:
            properties["view_name"] = match.view_name or ""
            properties["url_name"] = match.url_name or ""

        # Groups for org + workspace analytics
        groups = {}
        org = getattr(user, "organization", None)
        org_id = getattr(org, "id", None) or getattr(user, "organization_id", None)
        if org_id:
            groups["organization"] = str(org_id)
            properties["organization_id"] = str(org_id)

        workspace_id = getattr(request, "workspace_id", None) or getattr(
            user, "default_workspace_id", None
        )
        if workspace_id:
            groups["workspace"] = str(workspace_id)
            properties["workspace_id"] = str(workspace_id)

        self.tracker.capture(
            user_id=user_id,
            event_name="api_request",
            properties=properties,
            groups=groups,
        )
