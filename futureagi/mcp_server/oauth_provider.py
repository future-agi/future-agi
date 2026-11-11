"""OAuth 2.0 Authorization Server Provider for MCP SDK integration.

Implements the OAuthAuthorizationServerProvider protocol using Django cache
(Redis-backed) for storage of clients, codes, and tokens.
"""

import hashlib
import secrets
import time
import uuid
from datetime import datetime, timezone

import structlog
from asgiref.sync import sync_to_async
from django.core.cache import cache
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

logger = structlog.get_logger(__name__)

# Cache key prefixes
CLIENT_PREFIX = "mcp_oauth_client:"
CODE_PREFIX = "mcp_oauth_code:"
APPROVE_PREFIX = "mcp_oauth_approve:"
ACCESS_PREFIX = "mcp_oauth_access:"
REFRESH_PREFIX = "mcp_oauth_refresh:"
# Rotated refresh tokens, kept so a replay can be recognised (value: family id).
REFRESH_USED_PREFIX = "mcp_oauth_refresh_used:"
# Revoked refresh-token families.
REFRESH_FAMILY_REVOKED_PREFIX = "mcp_oauth_refresh_family_revoked:"

# TTLs (seconds)
CLIENT_TTL = 30 * 24 * 3600  # 30 days
CODE_TTL = 10 * 60  # 10 minutes
ACCESS_TTL = 3600  # 1 hour
APPROVE_TTL = 10 * 60  # 10 minutes


class FutureAGIAuthorizationCode(AuthorizationCode):
    """Authorization code with Future AGI user context."""

    user_id: str
    organization_id: str
    workspace_id: str | None = None


class FutureAGIAccessToken(AccessToken):
    """Access token with Future AGI user context."""

    user_id: str
    organization_id: str
    workspace_id: str | None = None


class FutureAGIRefreshToken(RefreshToken):
    """Refresh token with Future AGI user context."""

    user_id: str
    organization_id: str
    # Tokens issued before rotation tracking have no family.
    family_id: str | None = None
    workspace_id: str | None = None


def _frontend_base_url() -> str:
    """The UI the consent page lives on: settings.FRONTEND_BASE_URL
    (FRONTEND_URL, else APP_BASE_URL), or the dev UI when neither is set."""
    from django.conf import settings

    return settings.FRONTEND_BASE_URL or "http://localhost:3031"


def _refresh_ttl() -> int:
    from mcp_server.oauth_utils import refresh_token_ttl_seconds

    return refresh_token_ttl_seconds()


async def _revoke_refresh_family(family_id: str | None, reason: str) -> None:
    if not family_id:
        return
    await sync_to_async(cache.set)(
        f"{REFRESH_FAMILY_REVOKED_PREFIX}{family_id}", reason, _refresh_ttl()
    )
    logger.warning("oauth_refresh_family_revoked", family_id=family_id, reason=reason)


async def _store_refresh_token(
    *,
    client_id: str,
    scopes: list[str],
    user_id: str,
    organization_id: str,
    workspace_id: str | None,
    family_id: str,
) -> str:
    from mcp_server.oauth_utils import generate_refresh_token

    ttl = _refresh_ttl()
    expires_at = int(time.time()) + ttl
    token = await sync_to_async(generate_refresh_token)(
        user_id=user_id,
        org_id=organization_id,
        client_id=client_id,
        expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
    )
    data = FutureAGIRefreshToken(
        token=token,
        client_id=client_id,
        scopes=scopes,
        expires_at=expires_at,
        user_id=user_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        family_id=family_id,
    )
    await sync_to_async(cache.set)(
        f"{REFRESH_PREFIX}{token}", data.model_dump(mode="json"), ttl
    )
    return token


class FutureAGIOAuthProvider:
    """OAuth 2.0 provider backed by Django cache (Redis).

    Implements the OAuthAuthorizationServerProvider protocol from the MCP SDK.
    In DEBUG mode, authorization is auto-approved using the first active user.
    In production, users are redirected to a frontend consent page.
    """

    def __init__(self, frontend_url: str | None = None):
        # Normalize to avoid double-slash redirect paths when the URL ends with "/".
        # Example: "https://dev.futureagi.com/" -> "https://dev.futureagi.com"
        self.frontend_url = (frontend_url or _frontend_base_url()).rstrip("/")

    # ── Client Registration ──────────────────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = await sync_to_async(cache.get)(f"{CLIENT_PREFIX}{client_id}")
        if data is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(data)
        except Exception:
            logger.exception("oauth_get_client_failed", client_id=client_id)
            return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        client_id = client_info.client_id
        if not client_id:
            raise ValueError("client_id is required")
        await sync_to_async(cache.set)(
            f"{CLIENT_PREFIX}{client_id}",
            client_info.model_dump(mode="json"),
            CLIENT_TTL,
        )
        logger.info("oauth_client_registered", client_id=client_id)

    # ── Authorization ────────────────────────────────────────────────

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        return await self._redirect_to_consent(client, params)

    async def _auto_approve(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """DEBUG mode: auto-approve using first active user."""
        from accounts.models.user import User
        from accounts.models.workspace import Workspace

        user = await sync_to_async(
            lambda: User.objects.all()
            .select_related("organization")
            .order_by("created_at")
            .first()
        )()

        if not user:
            raise ValueError("No users found for auto-approve")

        org = user.organization
        workspace = await sync_to_async(
            lambda: Workspace.objects.all()
            .filter(organization=org, is_default=True, is_active=True)
            .first()
        )()

        # Generate authorization code
        code_value = secrets.token_urlsafe(32)
        scopes = params.scopes or []

        auth_code = FutureAGIAuthorizationCode(
            code=code_value,
            scopes=scopes,
            expires_at=time.time() + CODE_TTL,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            user_id=str(user.id),
            organization_id=str(org.id),
            workspace_id=str(workspace.id) if workspace else None,
        )

        await sync_to_async(cache.set)(
            f"{CODE_PREFIX}{code_value}",
            auth_code.model_dump(mode="json"),
            CODE_TTL,
        )

        logger.info(
            "oauth_auto_approved",
            user_id=str(user.id),
            client_id=client.client_id,
        )

        redirect_url = construct_redirect_uri(
            str(params.redirect_uri),
            code=code_value,
            state=params.state,
        )
        return redirect_url

    async def _redirect_to_consent(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Production mode: redirect to frontend consent page."""
        request_id = secrets.token_urlsafe(32)

        approve_data = {
            "client_id": client.client_id,
            "client_name": client.client_name or client.client_id,
            "scopes": params.scopes or [],
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": params.resource,
            "state": params.state,
        }

        await sync_to_async(cache.set)(
            f"{APPROVE_PREFIX}{request_id}",
            approve_data,
            APPROVE_TTL,
        )

        return f"{self.frontend_url}/mcp/authorize?request_id={request_id}"

    # ── Authorization Code ───────────────────────────────────────────

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> FutureAGIAuthorizationCode | None:
        data = await sync_to_async(cache.get)(f"{CODE_PREFIX}{authorization_code}")
        if data is None:
            return None
        try:
            code = FutureAGIAuthorizationCode.model_validate(data)
            if code.client_id != client.client_id:
                return None
            if code.expires_at < time.time():
                return None
            return code
        except Exception:
            logger.exception("oauth_load_code_failed", code=authorization_code[:8])
            return None

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: FutureAGIAuthorizationCode,
    ) -> OAuthToken:
        from mcp_server.oauth_utils import generate_oauth_token

        # Delete used code
        await sync_to_async(cache.delete)(f"{CODE_PREFIX}{authorization_code.code}")

        # Generate access token
        access_token_str, expires_at = await sync_to_async(generate_oauth_token)(
            user_id=authorization_code.user_id,
            org_id=authorization_code.organization_id,
            workspace_id=authorization_code.workspace_id,
            client_id=authorization_code.client_id,
            scope=authorization_code.scopes,
        )

        # Store access token metadata in cache
        access_data = FutureAGIAccessToken(
            token=access_token_str,
            client_id=authorization_code.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(expires_at.timestamp()),
            resource=authorization_code.resource,
            user_id=authorization_code.user_id,
            organization_id=authorization_code.organization_id,
            workspace_id=authorization_code.workspace_id,
        )
        await sync_to_async(cache.set)(
            f"{ACCESS_PREFIX}{access_token_str}",
            access_data.model_dump(mode="json"),
            ACCESS_TTL,
        )

        # Issue a refresh token that starts a new rotation family
        refresh_token_str = await _store_refresh_token(
            client_id=authorization_code.client_id,
            scopes=authorization_code.scopes,
            user_id=authorization_code.user_id,
            organization_id=authorization_code.organization_id,
            workspace_id=authorization_code.workspace_id,
            family_id=uuid.uuid4().hex,
        )

        logger.info(
            "oauth_code_exchanged",
            client_id=authorization_code.client_id,
            user_id=authorization_code.user_id,
        )

        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            scope=(
                " ".join(authorization_code.scopes)
                if authorization_code.scopes
                else None
            ),
            refresh_token=refresh_token_str,
        )

    # ── Access Token ─────────────────────────────────────────────────

    async def load_access_token(self, token: str) -> FutureAGIAccessToken | None:
        # Try cache first
        data = await sync_to_async(cache.get)(f"{ACCESS_PREFIX}{token}")
        if data is not None:
            try:
                access = FutureAGIAccessToken.model_validate(data)
                if access.expires_at and access.expires_at < time.time():
                    await sync_to_async(cache.delete)(f"{ACCESS_PREFIX}{token}")
                    return None
                return access
            except Exception:
                logger.exception("oauth_load_access_failed")

        # Fall back to decrypting the token directly (survives cache restart)
        from mcp_server.oauth_utils import decrypt_oauth_token

        payload = await sync_to_async(decrypt_oauth_token)(token)
        if not payload or payload.get("type") != "mcp_oauth":
            return None

        return FutureAGIAccessToken(
            token=token,
            client_id=payload.get("client_id", ""),
            scopes=payload.get("scope", []),
            expires_at=(
                int(
                    __import__("datetime")
                    .datetime.fromisoformat(payload["expires_at"])
                    .timestamp()
                )
                if payload.get("expires_at")
                else None
            ),
            user_id=payload["user_id"],
            organization_id=payload["org_id"],
            workspace_id=payload.get("workspace_id"),
        )

    # ── Refresh Token ────────────────────────────────────────────────

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> FutureAGIRefreshToken | None:
        data = await sync_to_async(cache.get)(f"{REFRESH_PREFIX}{refresh_token}")
        if data is None:
            # A rotated token presented again: revoke its family (reuse detection).
            used_family = await sync_to_async(cache.get)(
                f"{REFRESH_USED_PREFIX}{refresh_token}"
            )
            if used_family:
                await _revoke_refresh_family(used_family, "refresh_token_reuse")
            return None
        try:
            rt = FutureAGIRefreshToken.model_validate(data)
        except Exception:
            logger.exception("oauth_load_refresh_failed")
            return None
        if rt.client_id != client.client_id:
            return None
        if rt.family_id and await sync_to_async(cache.get)(
            f"{REFRESH_FAMILY_REVOKED_PREFIX}{rt.family_id}"
        ):
            return None
        return rt

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: FutureAGIRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        from mcp_server.oauth_utils import generate_oauth_token

        # Tokens cached before families existed get a family id derived from
        # the token, so concurrent consumers of one legacy token agree on it.
        family_id = (
            refresh_token.family_id
            or hashlib.sha256(refresh_token.token.encode()).hexdigest()[:32]
        )
        # Remember the token as used before consuming it, so a concurrent or
        # later replay is recognised as reuse rather than as an unknown token.
        await sync_to_async(cache.set)(
            f"{REFRESH_USED_PREFIX}{refresh_token.token}", family_id, _refresh_ttl()
        )
        # Consume the old token. The delete is atomic and reports whether this
        # caller removed it; a caller that did not lost a race with another
        # use of the same token, which is treated as reuse.
        consumed = await sync_to_async(cache.delete)(
            f"{REFRESH_PREFIX}{refresh_token.token}"
        )
        if not consumed:
            await _revoke_refresh_family(family_id, "refresh_token_reuse")
            raise TokenError(
                error="invalid_grant",
                error_description="refresh token already used",
            )
        if await sync_to_async(cache.get)(
            f"{REFRESH_FAMILY_REVOKED_PREFIX}{family_id}"
        ):
            raise TokenError(
                error="invalid_grant",
                error_description="refresh token revoked",
            )

        effective_scopes = scopes if scopes else refresh_token.scopes

        # Generate new access token
        access_token_str, expires_at = await sync_to_async(generate_oauth_token)(
            user_id=refresh_token.user_id,
            org_id=refresh_token.organization_id,
            workspace_id=refresh_token.workspace_id,
            client_id=refresh_token.client_id,
            scope=effective_scopes,
        )

        # Store new access token
        access_data = FutureAGIAccessToken(
            token=access_token_str,
            client_id=refresh_token.client_id,
            scopes=effective_scopes,
            expires_at=int(expires_at.timestamp()),
            user_id=refresh_token.user_id,
            organization_id=refresh_token.organization_id,
            workspace_id=refresh_token.workspace_id,
        )
        await sync_to_async(cache.set)(
            f"{ACCESS_PREFIX}{access_token_str}",
            access_data.model_dump(mode="json"),
            ACCESS_TTL,
        )

        # Rotate: issue the successor in the same family with a fresh lifetime
        new_refresh_str = await _store_refresh_token(
            client_id=refresh_token.client_id,
            scopes=effective_scopes,
            user_id=refresh_token.user_id,
            organization_id=refresh_token.organization_id,
            workspace_id=refresh_token.workspace_id,
            family_id=family_id,
        )

        logger.info(
            "oauth_refresh_exchanged",
            client_id=refresh_token.client_id,
            user_id=refresh_token.user_id,
        )

        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            scope=" ".join(effective_scopes) if effective_scopes else None,
            refresh_token=new_refresh_str,
        )

    # ── Revocation ───────────────────────────────────────────────────

    async def revoke_token(
        self,
        token: FutureAGIAccessToken | FutureAGIRefreshToken,
    ) -> None:
        if isinstance(token, FutureAGIAccessToken):
            await sync_to_async(cache.delete)(f"{ACCESS_PREFIX}{token.token}")
        elif isinstance(token, FutureAGIRefreshToken):
            await sync_to_async(cache.delete)(f"{REFRESH_PREFIX}{token.token}")
            await _revoke_refresh_family(token.family_id, "revoked_by_client")
        logger.info("oauth_token_revoked", client_id=token.client_id)
