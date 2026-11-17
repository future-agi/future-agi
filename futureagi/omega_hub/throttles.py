"""Hub throttles (SPEC §9.6). Rates are class attributes, so settings.py is not edited.

Every Hub throttle identifies an anonymous client by `client_ip.client_ip`, the one trusted address
(HUB-04), never by DRF's `get_ident`, which believes a client-written `X-Forwarded-For` when
`NUM_PROXIES` is unset.
"""

import hashlib

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from omega_hub.client_ip import client_ip


class _HashedIdent:
    """Throttle keys carry a salted hash of the client address, never the address (SPEC §13)."""

    def get_ident(self, request):  # type: ignore[override]
        salt = settings.SECRET_KEY if isinstance(settings.SECRET_KEY, str) else settings.SECRET_KEY.decode()
        return hashlib.sha256(f"{salt}|throttle|{client_ip(request)}".encode()).hexdigest()[:16]


class HubAnonReadThrottle(_HashedIdent, AnonRateThrottle):
    scope = "omega_hub_anon"
    rate = "120/min"


class HubUserReadThrottle(_HashedIdent, UserRateThrottle):
    scope = "omega_hub_user"
    rate = "600/min"


class HubLikeThrottle(_HashedIdent, UserRateThrottle):
    scope = "omega_hub_like"
    rate = "200/day"


class HubReportThrottle(_HashedIdent, UserRateThrottle):
    scope = "omega_hub_report"
    rate = "20/day"


class HubTokenCreateThrottle(_HashedIdent, UserRateThrottle):
    scope = "omega_hub_token_create"
    rate = "10/day"

    def allow_request(self, request, view):
        if request.method != "POST":
            return True
        return super().allow_request(request, view)


class _WriteOnly:
    def allow_request(self, request, view):  # type: ignore[override]
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return super().allow_request(request, view)  # type: ignore[misc]


class HubLikeWriteThrottle(_WriteOnly, HubLikeThrottle):
    pass


class HubReportWriteThrottle(_WriteOnly, HubReportThrottle):
    pass


class HubSubmitThrottle(_WriteOnly, _HashedIdent, UserRateThrottle):
    """Per account, before the upload is parsed (HUB-17); intake's per-publisher daily limit still applies."""

    scope = "omega_hub_submit"
    rate = "30/hour"


class HubVerifyEmailThrottle(_WriteOnly, _HashedIdent, UserRateThrottle):
    """Confirmation emails per account (HUB-25): enough to retry, too few to mail-bomb an address."""

    scope = "omega_hub_verify_email"
    rate = "5/hour"
