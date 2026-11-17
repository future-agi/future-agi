"""Hub authentication (SPEC §9.1).

- HubTokenAuthentication reads `X-Omega-Hub-Token: ohp_<40 base62>`. A Hub token never travels as a
  Bearer value: APIKeyAuthentication treats every Bearer value as a JWT and refuses one that does
  not decode, so `Authorization: Bearer ohp_...` never authenticates.
- CheckerAuthentication reads `X-Omega-Hub-Checker-Token` and compares it in constant time.
- MetricsAuthentication reads a separate `X-Omega-Hub-Metrics-Token`; it cannot claim jobs or post verdicts.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import string

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication

from omega_hub import conf
from omega_hub.errors import HubError

TOKEN_HEADER = "X-Omega-Hub-Token"
CHECKER_HEADER = "X-Omega-Hub-Checker-Token"
METRICS_HEADER = "X-Omega-Hub-Metrics-Token"
TOKEN_PATTERN = re.compile(r"^ohp_[A-Za-z0-9]{40}$")
_BASE62 = string.ascii_letters + string.digits


def new_token() -> str:
    return "ohp_" + "".join(secrets.choice(_BASE62) for _ in range(40))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class HubTokenAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return TOKEN_HEADER

    def authenticate(self, request):
        from omega_hub.models import HubToken

        raw = request.headers.get(TOKEN_HEADER)
        if not raw:
            return None
        if not TOKEN_PATTERN.match(raw):
            raise HubError("OMEGA_HUB_UNAUTHENTICATED", 401, "The Hub token is malformed.")
        token = (
            HubToken.objects.select_related("user", "publisher")
            .filter(token_hash=hash_token(raw))
            .first()
        )
        now = timezone.now()
        if token is None or token.revoked_at is not None or token.expires_at <= now:
            raise HubError("OMEGA_HUB_UNAUTHENTICATED", 401, "The Hub token is unknown, revoked or expired.")
        if not token.user.is_active:
            raise HubError("OMEGA_HUB_UNAUTHENTICATED", 401, "The token's account is inactive.")
        if token.publisher.suspended_at is not None:
            raise HubError("OMEGA_HUB_PUBLISHER_SUSPENDED", 403)
        # A token carries its user's right to act for the publisher, checked again on every request:
        # an organisation member who leaves or is demoted below Admin loses the token's rights with it.
        from omega_hub.permissions import can_act_for_publisher

        if not can_act_for_publisher(token.user, token.publisher):
            raise HubError("OMEGA_HUB_NOT_PUBLISHER_OWNER", 403,
                           "The token's account no longer acts for this publisher.")
        HubToken.objects.filter(pk=token.pk).update(last_used_at=now)
        return token.user, token


class CheckerPrincipal:
    """The checker worker. It is not a user and holds no user permissions."""

    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_superuser = False
    pk = None
    id = None

    def has_perm(self, *args, **kwargs):
        return False

    def __str__(self):
        return "omega-hub-checker"


CHECKER = CheckerPrincipal()


class MetricsPrincipal:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_superuser = False
    pk = None
    id = None

    def has_perm(self, *args, **kwargs):
        return False

    def __str__(self):
        return "omega-hub-metrics"


METRICS = MetricsPrincipal()


class CheckerAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return CHECKER_HEADER

    def authenticate(self, request):
        expected = conf.checker_token()
        if not expected:
            raise HubError("OMEGA_HUB_CHECKER_DISABLED", 503)
        given = request.headers.get(CHECKER_HEADER) or ""
        if not hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
            raise HubError("OMEGA_HUB_CHECKER_AUTH", 401)
        return CHECKER, "checker"


class MetricsAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return METRICS_HEADER

    def authenticate(self, request):
        expected = conf.metrics_token()
        if not expected:
            raise HubError("OMEGA_HUB_METRICS_DISABLED", 503)
        given = request.headers.get(METRICS_HEADER) or ""
        if not hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
            raise HubError("OMEGA_HUB_METRICS_AUTH", 401)
        return METRICS, "metrics"
