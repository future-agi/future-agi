"""
HTTP middleware that pins reads to the primary Postgres after a mutation.

NOT wired into `MIDDLEWARE` by default. Add to `tfc/settings/settings.py`
only if read-after-write bugs surface on the HTTP path. Non-HTTP entrypoints
(Temporal activities, gRPC, Celery-equivalent tasks) must use
`tfc.routers.force_primary()` directly — a cookie middleware doesn't help
there.

IMPORTANT SCOPE: this middleware only protects ROUTER-MEDIATED reads.
Endpoints that use explicit `.using("replica")` or `db_manager("replica")`
calls (our 9 feature-key-routed endpoints) bypass the router and will
continue to hit replica regardless of `force_primary()`. For those, the
mitigations are: (a) write the read without `.using()` so the router
chooses, or (b) flip `READ_REPLICA_OPT_IN=""` and restart workers.

Two protections layered:

  1. Non-idempotent methods (POST/PUT/PATCH/DELETE) get wrapped in
     `force_primary()` so router-mediated reads INSIDE the mutating
     request hit primary. (Without this, a POST that writes and then
     reads before returning the response could still hit replica.)

  2. A short-lived cookie (`pg_pin_primary`, default 10s TTL) is set on the
     response. Subsequent requests carrying the cookie are also wrapped in
     `force_primary()`, covering the redirect-after-post pattern.
"""

from tfc.routers import force_primary

_PIN_COOKIE = "pg_pin_primary"
_PIN_TTL_SECONDS = 10
_NON_IDEMPOTENT = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class PrimaryPinningMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cookie_pinned = request.COOKIES.get(_PIN_COOKIE) == "1"
        method_pinned = request.method in _NON_IDEMPOTENT

        if cookie_pinned or method_pinned:
            with force_primary():
                response = self.get_response(request)
        else:
            response = self.get_response(request)

        if method_pinned:
            response.set_cookie(
                _PIN_COOKIE,
                "1",
                max_age=_PIN_TTL_SECONDS,
                httponly=True,
                samesite="Lax",
            )

        return response
