import structlog
from django.core.cache import cache

logger = structlog.get_logger(__name__)

# Whether the current cache outage has been logged: once per outage, not per
# request.
_cache_outage_logged = False


class AuthMonitoringMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Monitor failed auth attempts
        if hasattr(request, "user") and not request.user.is_authenticated:
            self.count_attempt(self.get_client_ip(request))

        response = self.get_response(request)

        if response.status_code in [401, 403]:
            logger.warning(
                f"Auth failure: {request.path} - "
                f"User: {getattr(request.user, 'username', 'anonymous')} - "
                f"IP: {self.get_client_ip(request)}"
            )

        return response

    def count_attempt(self, client_ip):
        """Fails open: this runs on every anonymous request, /health/ and the
        setup screen's checks included, and a cache that is down (Standalone's
        Redis restarting, or full) must not turn each of them into a 500."""
        global _cache_outage_logged
        try:
            failed_attempts = cache.get(f"failed_auth_{client_ip}", 0)
            cache.set(f"failed_auth_{client_ip}", failed_attempts + 1, 300)  # 5 minutes
        except Exception as exc:
            if not _cache_outage_logged:
                _cache_outage_logged = True
                logger.warning("auth_monitoring_cache_unavailable", error=str(exc))
            return
        _cache_outage_logged = False

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        return (
            x_forwarded_for.split(",")[0]
            if x_forwarded_for
            else request.META.get("REMOTE_ADDR")
        )
