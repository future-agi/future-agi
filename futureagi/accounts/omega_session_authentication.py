"""Who may exchange for an Omega assertion: a browser access token, or a person's API key.

Two credentials, never combined in one request:

- A browser access token (``Authorization: Bearer``): the existing opaque AuthToken boundary.
- A user API key (``X-Api-Key`` and ``X-Secret-Key``, SB W2-KEYS, READINESS E09): the key a
  person creates on the Keys page, for SDKs and CI. Only ``type="user"`` keys with an owner
  are admitted. An organization's ``system`` key signs in as the organization's first user,
  and an ``mcp`` key belongs to the MCP transport; neither names an accountable person, so
  neither can act in Omega (``omega_key_not_admitted``). The key acts with its owner's
  current role, never more (the view applies ``delegation_role``), in the workspace the key is
  pinned to, else the one selected.

Every key attempt counts before the secret is checked, atomically (``omega_rate_window``), against
the key it names from the caller's source and against the key's ceiling across all sources, so
guessing a secret is bounded while a client that floods a key it does not own never locks the
owner out from elsewhere (``omega_throttles.ApiKeyAttemptThrottle``; READINESS L03). A successful
key exchange also counts against its owner (``OmegaApiKeyOwnerThrottle``, applied by the view), so
many keys never multiply one person's budget. The numbers are provisional and in ``omega_throttles``.
"""

import re
from datetime import timedelta
from uuid import UUID

from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, Throttled

from accounts.api_key_policy import authenticate_api_key
from accounts.authentication import APIKeyAuthentication, decrypt_message
from accounts.models import User
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenType,
)
from accounts.models.user import OrgApiKey
from accounts.omega_throttles import OmegaApiKeyExchangeThrottle

SESSION = "session"
API_KEY = "api_key"
KEY_NOT_ADMITTED = "omega_key_not_admitted"
_MAX_KEY_LENGTH = 255


def _check_selection_headers(request):
    for header in ("X-Organization-Id", "X-Workspace-Id"):
        if header in request.headers:
            try:
                UUID(request.headers[header])
            except (ValueError, AttributeError) as exc:
                raise PermissionDenied(
                    "Invalid organization or workspace selection"
                ) from exc


class OmegaSessionAuthentication(APIKeyAuthentication):
    def authenticate(self, request):
        has_key = "X-Api-Key" in request.headers or "X-Secret-Key" in request.headers
        authorization = request.META.get("HTTP_AUTHORIZATION", "")
        if has_key and authorization:
            raise AuthenticationFailed(
                "Send a browser access token or an API key, not both"
            )
        if has_key:
            return self._authenticate_api_key(request)
        if not authorization:
            return None
        return self._authenticate_browser_token(request, authorization)

    def _authenticate_browser_token(self, request, authorization):
        match = re.fullmatch(r"Bearer ([A-Za-z0-9_=-]{1,8192})", authorization)
        if not match:
            raise AuthenticationFailed("A browser access token is required")
        _check_selection_headers(request)
        try:
            decoded = decrypt_message(match.group(1))
            token = AuthToken.no_workspace_objects.filter(
                pk=UUID(decoded.get("id")),
                user_id=UUID(decoded.get("user_id")),
                auth_type=AuthTokenType.ACCESS.value,
                is_active=True,
                last_used_at__gte=timezone.now()
                - timedelta(minutes=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES),
            ).first()
            if token is None:
                raise AuthenticationFailed("Invalid browser access token")
            user = (
                User.objects.select_related("organization")
                .filter(pk=token.user_id, is_active=True)
                .first()
            )
            if user is None:
                raise AuthenticationFailed("Invalid browser access token")
        except (TypeError, ValueError, AttributeError) as exc:
            raise AuthenticationFailed("Invalid browser access token") from exc
        # Reuse native organization/workspace resolution with the fresh user. Avoid
        # decode_token's cache hit, which updates last_used_at before checking revocation.
        self._bind_user_context(user)
        self._set_workspace_context(request, user)
        request.omega_auth_method = SESSION
        request.omega_credential_id = str(token.pk)
        request.omega_browser_token_id = token.pk
        return user, match.group(1)

    def _authenticate_api_key(self, request):
        api_key = request.headers.get("X-Api-Key", "")
        secret_key = request.headers.get("X-Secret-Key", "")
        if (
            not api_key
            or not secret_key
            or len(api_key) > _MAX_KEY_LENGTH
            or len(secret_key) > _MAX_KEY_LENGTH
        ):
            raise AuthenticationFailed("An API key pair is required")
        throttle = OmegaApiKeyExchangeThrottle()
        if not throttle.allow_request(request, None):
            raise Throttled(
                wait=throttle.wait(),
                detail=(
                    "Too many Omega key requests for this API key from this address. Try again shortly."
                    if throttle.refused_by == 0
                    else "Too many Omega key requests for this API key. Try again shortly."
                ),
            )
        _check_selection_headers(request)
        # Looked up by its public id, the secret compared with its keyed hash in constant
        # time; a miss costs what a wrong secret costs (accounts/api_key_lookup.py, L03).
        key = authenticate_api_key(api_key, secret_key)
        user = key.user
        request.org_api_key = key
        self._bind_user_context(user)
        self._set_workspace_context(request, user)
        request.omega_auth_method = API_KEY
        request.omega_credential_id = str(key.pk)
        request.omega_api_key_id = key.pk
        return user, None

    def _get_or_create_default_workspace(self, user, organization):
        # A delegation request must never create/reactivate host access as a side effect.
        return None
