"""The token endpoint's two grants and the access token.

Access token: an ES256 JWT, ``mcp-access-token-claims-v1`` (``aud`` the one
gateway resource it was granted for, ``sub`` the person, ``org_id`` the
organization, at most 15 minutes). Refresh token: 32 random bytes, stored
only as its SHA-256, rotated on every use. Presenting a used refresh token
(or a used code) revokes the whole grant family: every live refresh token of
that (person, client, organization, resource). Every exchange and refresh
re-checks that the person is still an active member of the organization (and
that its config still holds the virtual server) and that the consent is not
revoked; if not, the family is revoked and the answer is ``invalid_grant``.
"""

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import timedelta

import structlog
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from agentcc.mcp_oauth import authorize, clients, config, keys, resources
from agentcc.models.mcp_oauth import (
    AgentccMCPOAuthConsent,
    AgentccMCPOAuthRefreshToken,
)

logger = structlog.get_logger(__name__)

_VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")


class TokenError(Exception):
    def __init__(self, error, description, status=400):
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def access_token(*, user_id, org_id, client_id, resource, scopes) -> str:
    now = int(timezone.now().timestamp())
    claims = {
        "typ": config.ACCESS_TOKEN_TYPE,
        "iss": config.issuer(),
        "aud": resource,
        "sub": str(user_id),
        "org_id": str(org_id),
        "client_id": client_id,
        "scope": " ".join(scopes),
        "iat": now,
        "nbf": now,
        "exp": now + config.ACCESS_TOKEN_SECONDS,
        "jti": f"mat_{uuid.uuid4().hex}",
    }
    return keys.sign(claims)


def revoke_family(*, user_id, client_pk, org_id, resource) -> int:
    return AgentccMCPOAuthRefreshToken.objects.filter(
        user_id=user_id,
        client_id=client_pk,
        organization_id=org_id,
        resource=resource,
        revoked_at__isnull=True,
    ).update(revoked_at=timezone.now())


def _live_consent(*, user_id, client_pk, org_id, resource):
    return (
        AgentccMCPOAuthConsent.objects.filter(
            user_id=user_id,
            client_id=client_pk,
            organization_id=org_id,
            resource=resource,
            revoked_at__isnull=True,
        )
        .order_by("-granted_at")
        .first()
    )


def _active_user(user_id):
    user = get_user_model().objects.filter(pk=user_id).first()
    return user if user is not None and user.is_active else None


def _issue(*, user, org_id, client, resource, scopes, rotated_from=None) -> dict:
    raw_refresh = secrets.token_urlsafe(32)
    AgentccMCPOAuthRefreshToken.objects.create(
        token_hash=hash_token(raw_refresh),
        user=user,
        client=client.row,
        organization_id=org_id,
        resource=resource,
        expires_at=timezone.now() + timedelta(seconds=config.REFRESH_TOKEN_SECONDS),
        rotated_from=rotated_from,
    )
    token = access_token(
        user_id=user.pk,
        org_id=org_id,
        client_id=client.client_id,
        resource=resource,
        scopes=scopes,
    )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": config.ACCESS_TOKEN_SECONDS,
        "refresh_token": raw_refresh,
        "scope": " ".join(scopes),
    }


def _keep_gateway_keys_current() -> None:
    try:
        keys.push_if_due()
    except Exception:  # noqa: BLE001 -- a push problem never stops a sign-in
        logger.warning("mcp_oauth_keys_push_skipped")


def exchange_code(client: clients.Client, params) -> dict:
    grant, reused = authorize.take_code(params.get("code"))
    if grant is None or grant["client_id"] != client.client_id:
        raise TokenError("invalid_grant", "The code is invalid or expired.")
    family = {
        "user_id": grant["user_id"],
        "client_pk": client.row.pk,
        "org_id": grant["organization_id"],
        "resource": grant["resource"],
    }
    if reused:
        revoke_family(**family)
        logger.warning("mcp_oauth_code_reused", client_id=client.client_id)
        raise TokenError("invalid_grant", "The code was already used.")
    if params.get("redirect_uri") != grant["redirect_uri"]:
        raise TokenError("invalid_grant", "redirect_uri does not match the request.")
    verifier = params.get("code_verifier") or ""
    if not _VERIFIER.fullmatch(verifier) or not hmac.compare_digest(
        _s256(verifier), grant["code_challenge"]
    ):
        raise TokenError("invalid_grant", "code_verifier does not match.")
    if params.get("resource") not in (None, "", grant["resource"]):
        raise TokenError("invalid_target", "resource differs from the grant.")
    user = _active_user(grant["user_id"])
    consent = AgentccMCPOAuthConsent.objects.filter(
        pk=grant["consent_id"], revoked_at__isnull=True
    ).first()
    if (
        user is None
        or consent is None
        or not resources.user_may_use(user, grant["organization_id"], grant["resource"])
    ):
        revoke_family(**family)
        raise TokenError("invalid_grant", "The grant is no longer valid.")
    keys.ensure_active_key()
    issued = _issue(
        user=user,
        org_id=grant["organization_id"],
        client=client,
        resource=grant["resource"],
        scopes=grant["scopes"],
    )
    _keep_gateway_keys_current()
    return issued


def refresh(client: clients.Client, params) -> dict:
    raw = params.get("refresh_token")
    if not isinstance(raw, str) or not raw or len(raw) > 512:
        raise TokenError("invalid_grant", "The refresh token is invalid.")
    # The outcome is decided inside the transaction and raised after it, so a
    # family revocation made on the way to a refusal is committed.
    keys.ensure_active_key()
    with transaction.atomic():
        outcome = _refresh_locked(client, raw, params)
    if isinstance(outcome, TokenError):
        raise outcome
    _keep_gateway_keys_current()
    return outcome


def _refresh_locked(client, raw, params):
    row = (
        AgentccMCPOAuthRefreshToken.objects.select_for_update()
        .filter(token_hash=hash_token(raw))
        .first()
    )
    if row is None or row.client_id != client.row.pk:
        return TokenError("invalid_grant", "The refresh token is invalid.")
    family = {
        "user_id": row.user_id,
        "client_pk": row.client_id,
        "org_id": row.organization_id,
        "resource": row.resource,
    }
    if row.revoked_at is not None:
        revoke_family(**family)
        logger.warning("mcp_oauth_refresh_reused", client_id=client.client_id)
        return TokenError("invalid_grant", "The refresh token was already used.")
    if row.expires_at <= timezone.now():
        return TokenError("invalid_grant", "The refresh token has expired.")
    if params.get("resource") not in (None, "", row.resource):
        return TokenError("invalid_target", "resource differs from the grant.")
    user = _active_user(row.user_id)
    consent = _live_consent(**family)
    if (
        user is None
        or consent is None
        or not resources.user_may_use(user, row.organization_id, row.resource)
    ):
        revoke_family(**family)
        return TokenError("invalid_grant", "The grant is no longer valid.")
    scopes = [s for s in config.SCOPE_ORDER if s in set(consent.scopes or [])]
    asked = params.get("scope")
    if asked:
        wanted = set(asked.split())
        if not wanted <= set(scopes):
            return TokenError("invalid_scope", "Scope exceeds the consent.")
        scopes = [s for s in scopes if s in wanted]
    row.revoked_at = timezone.now()
    row.save(update_fields=["revoked_at"])
    return _issue(
        user=user,
        org_id=row.organization_id,
        client=client,
        resource=row.resource,
        scopes=scopes,
        rotated_from=row,
    )
