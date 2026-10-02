from __future__ import annotations

import structlog
from django.http import JsonResponse

logger = structlog.get_logger(__name__)

RATE_LIMIT_SKIP_PREFIXES = (
    "/health",
    "/ready",
    "/v1/health",
    "/admin/",
    "/api/health/",
    "/api/ready/",
    "/api/public/health",
    "/api/usage/webhooks/stripe/",
    "/swagger/",
    "/redoc/",
    "/static/",
)


class RateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "OPTIONS":
            return self.get_response(request)

        if any(request.path.startswith(p) for p in RATE_LIMIT_SKIP_PREFIXES):
            return self.get_response(request)

        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return self.get_response(request)

        org = getattr(request, "organization", None)
        if not org:
            org = getattr(request.user, "organization", None)
        if not org:
            return self.get_response(request)

        try:
            from ee.usage.services.rate_limiter import RateLimiter

            result = RateLimiter.check(str(org.id), "api")
            if not result.allowed:
                response = JsonResponse(
                    {
                        "status": False,
                        "result": {
                            "code": "RATE_LIMITED",
                            "message": result.reason,
                            "retry_after": result.retry_after,
                        },
                    },
                    status=429,
                )
                response["Retry-After"] = str(result.retry_after)
                return response
        except ImportError:
            pass

        return self.get_response(request)
