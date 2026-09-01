"""Authentication identities used by internal simulation services."""

from __future__ import annotations

import secrets

from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header


class InternalServiceUser:
    """Minimal DRF identity for a caller holding ``INTERNAL_API_SECRET``."""

    is_authenticated = True
    is_internal_service = True


class InternalServiceAuthentication(BaseAuthentication):
    """Authenticate a trusted service without binding it to a tenant."""

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if len(parts) != 2 or parts[0].lower() != b"bearer":
            return None

        configured_secret = getattr(settings, "INTERNAL_API_SECRET", "")
        if not configured_secret:
            return None

        try:
            supplied_secret = parts[1].decode("utf-8")
        except UnicodeDecodeError:
            return None

        if not secrets.compare_digest(supplied_secret, configured_secret):
            return None
        # Authentication has already consumed the bearer credential. Do not
        # retain the fleet-wide secret on request.auth for downstream code or
        # exception/logging integrations to accidentally expose.
        return InternalServiceUser(), None
