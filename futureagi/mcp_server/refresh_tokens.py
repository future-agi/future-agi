"""Server-side refresh-token state for the MCP OAuth token endpoint.

Refresh tokens issued by ``POST /mcp/oauth/token/`` are recorded (as SHA-256
hashes) in families: one family per authorization. Every refresh rotates the
token: the presented token is marked used and a new one with a fresh lifetime
is issued in the same family. Presenting a token that was already used revokes
the whole family, because either the client or an attacker is replaying it
(RFC 9700 section 4.14.2). Refreshes within one family are serialized with a
row lock on the family, so two concurrent refreshes with the same token cannot
both succeed.

Each family records the scope (tool groups) the user consented to. An access
token issued by a refresh carries that scope narrowed to the connection's
currently enabled groups, never more (RFC 6749 section 6).

Access tokens name the family they were issued with. When a family is revoked
for any reason other than being superseded by a new authorization, its access
tokens stop working too (see ``family_revocation_ends_access_tokens``).
"""

from dataclasses import dataclass

import structlog
from django.db import IntegrityError, transaction
from django.utils import timezone

from mcp_server.models.connection import MCPConnection
from mcp_server.models.oauth_code import MCPOAuthCode
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


# A new authorization of the same client replaces its previous family. That is
# routine re-connection, not a compromise, so the previous family's access
# tokens keep their remaining lifetime, as before families existed.
SUPERSEDED = "superseded"
REUSE = "refresh_token_reuse"


def family_revocation_ends_access_tokens(revoked_reason: str) -> bool:
    """Whether a family revoked for ``revoked_reason`` also ends the access
    tokens issued from it."""
    return revoked_reason != SUPERSEDED


@dataclass(frozen=True)
class IssuedRefreshToken:
    token: str
    family_id: str


@dataclass(frozen=True)
class RefreshRotation:
    connection: MCPConnection | None = None
    refresh_token: str | None = None
    family_id: str | None = None
    # Tool groups the user consented to for this authorization.
    consented_scope: tuple[str, ...] = ()
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


def issue_refresh_token(
    connection, *, client_id, user_id, org_id, scope
) -> IssuedRefreshToken:
    """Start a new family for a fresh authorization of ``client_id``.

    ``scope`` is the list of tool groups the user consented to; refreshes in
    this family never grant more.

    The client's earlier families on this connection are revoked: before
    families existed, a new authorization overwrote the connection's single
    refresh token, so the previous one stopped working.
    """
    now = timezone.now()
    with transaction.atomic():
        MCPOAuthRefreshFamily.objects.filter(
            connection=connection, client_id=client_id, revoked_at__isnull=True
        ).update(revoked_at=now, revoked_reason=SUPERSEDED)
        family = MCPOAuthRefreshFamily.objects.create(
            connection=connection, client_id=client_id, scope=list(scope)
        )
        token = _mint(
            family, user_id=user_id, org_id=org_id, client_id=client_id, now=now
        )
    return IssuedRefreshToken(token=token, family_id=str(family.pk))


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


def _recorded_consent(family, connection) -> list[str]:
    """Consented scope for a family created before scope was recorded.

    Such a token was issued by the client's latest authorization code on this
    connection (a newer one replaced the token), so that code's scope is the
    consent. If the code is gone, fall back to the groups enabled now; the
    result is stored, so later changes to the groups cannot widen it.
    """
    consented = (
        MCPOAuthCode.objects.filter(
            client__client_id=family.client_id,
            user_id=connection.user_id,
            workspace_id=connection.workspace_id,
            used=True,
        )
        .order_by("-created_at")
        .values_list("scope", flat=True)
        .first()
    )
    if consented is None:
        consented = current_tool_groups(connection)
        logger.info(
            "oauth_refresh_scope_inferred_from_tool_groups",
            client_id=family.client_id,
            family_id=str(family.pk),
        )
    return list(consented or [])


def current_tool_groups(connection) -> list[str]:
    from mcp_server.models.tool_config import MCPToolGroupConfig

    try:
        return list(connection.tool_config.enabled_groups or [])
    except MCPToolGroupConfig.DoesNotExist:
        return []


def refreshed_scope(consented, connection) -> list[str]:
    """Consented groups that are still enabled on the connection, in consent
    order. Never wider than the consent."""
    enabled = set(current_tool_groups(connection))
    return [g for g in dict.fromkeys(consented) if g in enabled]


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
            if record.used_at is not None and family.revoked_reason == SUPERSEDED:
                # A rotated token of a superseded family is still a replay:
                # someone else rotated it. Escalate, so the family's access
                # tokens end as well.
                MCPOAuthRefreshFamily.objects.filter(pk=family.pk).update(
                    revoked_reason=REUSE
                )
                logger.warning(
                    "oauth_refresh_token_reuse_detected",
                    client_id=client_id,
                    user_id=str(user_id),
                    family_id=str(family.pk),
                )
            return RefreshRotation(error_reason="refresh_token_revoked")
        if record.used_at is not None:
            revoke_family(family, REUSE, now)
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

        if family.scope is None:
            family.scope = _recorded_consent(family, connection)
            family.save(update_fields=["scope"])

        MCPOAuthRefreshToken.objects.filter(pk=record.pk).update(used_at=now)
        new_token = _mint(
            family, user_id=user_id, org_id=org_id, client_id=client_id, now=now
        )

    return RefreshRotation(
        connection=connection,
        refresh_token=new_token,
        family_id=str(family.pk),
        consented_scope=tuple(family.scope),
    )
