"""Server-side refresh-token state for the MCP OAuth token endpoint.

Refresh tokens issued by ``POST /mcp/oauth/token/`` are recorded (as SHA-256
hashes) in families: one family per authorization. Every refresh rotates the
token: the presented token is marked used and a new one with a fresh lifetime
is issued in the same family. Presenting a token that was already used revokes
the whole family, because either the client or an attacker is replaying it
(RFC 9700 section 4.14.2). Refreshes within one family are serialized with a
row lock on the family, so two concurrent refreshes with the same token cannot
both succeed.
"""

from dataclasses import dataclass

import structlog
from django.db import IntegrityError, transaction
from django.utils import timezone

from mcp_server.models.connection import MCPConnection
from mcp_server.models.oauth_refresh_token import (
    MCPOAuthRefreshFamily,
    MCPOAuthRefreshToken,
)
from mcp_server.oauth_utils import (
    generate_refresh_token,
    hash_refresh_token,
    refresh_token_expiry,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RefreshRotation:
    connection: MCPConnection | None = None
    refresh_token: str | None = None
    error_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_reason is None


def _mint(family, *, user_id, org_id, client_id, now=None) -> str:
    expires_at = refresh_token_expiry(now)
    token = generate_refresh_token(
        user_id=user_id, org_id=org_id, client_id=client_id, expires_at=expires_at
    )
    MCPOAuthRefreshToken.objects.create(
        family=family,
        token_hash=hash_refresh_token(token),
        expires_at=expires_at,
    )
    return token


def revoke_family(family, reason: str, now=None) -> None:
    MCPOAuthRefreshFamily.objects.filter(pk=family.pk, revoked_at__isnull=True).update(
        revoked_at=now or timezone.now(), revoked_reason=reason
    )


def issue_refresh_token(connection, *, client_id, user_id, org_id) -> str:
    """Start a new family for a fresh authorization of ``client_id``.

    The client's earlier families on this connection are revoked: before
    families existed, a new authorization overwrote the connection's single
    refresh token, so the previous one stopped working.
    """
    now = timezone.now()
    with transaction.atomic():
        MCPOAuthRefreshFamily.objects.filter(
            connection=connection, client_id=client_id, revoked_at__isnull=True
        ).update(revoked_at=now, revoked_reason="superseded")
        family = MCPOAuthRefreshFamily.objects.create(
            connection=connection, client_id=client_id
        )
        return _mint(
            family, user_id=user_id, org_id=org_id, client_id=client_id, now=now
        )


def _adopt_legacy_token(token, token_hash, *, client_id, user_id):
    """Give a refresh token issued before families existed a record.

    Only the connection's current token qualifies. It gets a full lifetime
    from now, so existing clients keep working instead of being signed out.
    """
    connection = MCPConnection.no_workspace_objects.filter(
        user_id=user_id, oauth_refresh_token_encrypted=token, deleted=False
    ).first()
    if connection is None:
        # Another request may already have adopted and rotated it; return
        # that record so the caller treats this presentation as reuse.
        return (
            MCPOAuthRefreshToken.objects.select_related("family")
            .filter(token_hash=token_hash)
            .first()
        )
    try:
        with transaction.atomic():
            family = MCPOAuthRefreshFamily.objects.create(
                connection=connection, client_id=client_id
            )
            MCPOAuthRefreshToken.objects.create(
                family=family,
                token_hash=token_hash,
                expires_at=refresh_token_expiry(),
            )
    except IntegrityError:
        # A concurrent request adopted it first.
        pass
    return (
        MCPOAuthRefreshToken.objects.select_related("family")
        .filter(token_hash=token_hash)
        .first()
    )


def rotate_refresh_token(token, *, client_id, user_id, org_id) -> RefreshRotation:
    """Consume ``token`` and issue its successor, or explain the refusal."""
    token_hash = hash_refresh_token(token)
    record = MCPOAuthRefreshToken.objects.filter(token_hash=token_hash).first()
    if record is None:
        record = _adopt_legacy_token(
            token, token_hash, client_id=client_id, user_id=user_id
        )
        if record is None:
            return RefreshRotation(error_reason="refresh_token_not_found")

    now = timezone.now()
    with transaction.atomic():
        # Serialize refreshes of this family; the loser re-reads the token
        # after the winner commits and sees it as used.
        family = MCPOAuthRefreshFamily.objects.select_for_update().get(
            pk=record.family_id
        )
        record = MCPOAuthRefreshToken.objects.get(pk=record.pk)

        if family.client_id != client_id:
            return RefreshRotation(error_reason="refresh_token_client_mismatch")
        if family.revoked_at is not None:
            return RefreshRotation(error_reason="refresh_token_revoked")
        if record.used_at is not None:
            revoke_family(family, "refresh_token_reuse", now)
            logger.warning(
                "oauth_refresh_token_reuse_detected",
                client_id=client_id,
                user_id=str(user_id),
                family_id=str(family.pk),
            )
            return RefreshRotation(error_reason="refresh_token_reuse")
        if record.expires_at <= now:
            return RefreshRotation(error_reason="refresh_token_expired")

        connection = MCPConnection.no_workspace_objects.filter(
            pk=family.connection_id, user_id=user_id, deleted=False
        ).first()
        if connection is None:
            return RefreshRotation(error_reason="refresh_connection_not_found")

        MCPOAuthRefreshToken.objects.filter(pk=record.pk).update(used_at=now)
        new_token = _mint(
            family, user_id=user_id, org_id=org_id, client_id=client_id, now=now
        )

    return RefreshRotation(connection=connection, refresh_token=new_token)
