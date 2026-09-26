"""OAuth 2.0 Authorization Server Provider for MCP SDK integration.

Implements the OAuthAuthorizationServerProvider protocol using Django cache
(Redis-backed) for storage of clients, codes, and tokens.
"""

import os
import secrets
import time

import structlog
from asgiref.sync import sync_to_async
from django.core.cache import cache
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
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

# TTLs (seconds)
CLIENT_TTL = 30 * 24 * 3600  # 30 days
CODE_TTL = 10 * 60  # 10 minutes
ACCESS_TTL = 3600  # 1 hour
REFRESH_TTL = 30 * 24 * 3600  # 30 days
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
    workspace_id: str | None = None


def _app_base_url() -> str:
    """The UI's URL when FRONTEND_URL is unset. settings.APP_BASE_URL keeps a
    scheme written into APP_URL and uses http for a loopback host."""
    from django.conf import settings

    return getattr(settings, "APP_BASE_URL", "") or "http://localhost:3031"

class FutureAGIOAuthProvider:
    """OAuth 2.0 provider backed by Django cache (Redis).

    Implements the OAuthAuthorizationServerProvider protocol from the MCP SDK.
    In DEBUG mode, authorization is auto-approved using the first active user.
    In production, users are redirected to a frontend consent page.
    """

    def __init__(self, frontend_url: str | None = None):
        # Normalize to avoid double-slash redirect paths when FRONTEND_URL ends with "/".
        # Example: "https://dev.futureagi.com/" -> "https://dev.futureagi.com"
        resolved_frontend_url = (
            frontend_url or os.environ.get("FRONTEND_URL") or _app_base_url()
        )
        self.frontend_url = resolved_frontend_url.rstrip("/")

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
        from mcp_server.oauth_utils import generate_oauth_token, generate_refresh_token

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

        # Generate refresh token
        refresh_token_str = await sync_to_async(generate_refresh_token)(
            user_id=authorization_code.user_id,
            org_id=authorization_code.organization_id,
            client_id=authorization_code.client_id,
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

        # Store refresh token metadata in cache
        refresh_data = FutureAGIRefreshToken(
            token=refresh_token_str,
            client_id=authorization_code.client_id,
            scopes=authorization_code.scopes,
            user_id=authorization_code.user_id,
            organization_id=authorization_code.organization_id,
            workspace_id=authorization_code.workspace_id,
        )
        await sync_to_async(cache.set)(
            f"{REFRESH_PREFIX}{refresh_token_str}",
            refresh_data.model_dump(mode="json"),
            REFRESH_TTL,
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
            return None
        try:
            rt = FutureAGIRefreshToken.model_validate(data)
            if rt.client_id != client.client_id:
                return None
            return rt
        except Exception:
            logger.exception("oauth_load_refresh_failed")
            return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: FutureAGIRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        from mcp_server.oauth_utils import generate_oauth_token, generate_refresh_token

        # Delete old refresh token
        await sync_to_async(cache.delete)(f"{REFRESH_PREFIX}{refresh_token.token}")

        effective_scopes = scopes if scopes else refresh_token.scopes

        # Generate new access token
        access_token_str, expires_at = await sync_to_async(generate_oauth_token)(
            user_id=refresh_token.user_id,
            org_id=refresh_token.organization_id,
            workspace_id=refresh_token.workspace_id,
            client_id=refresh_token.client_id,
            scope=effective_scopes,
        )

        # Generate new refresh token (rotation)
        new_refresh_str = await sync_to_async(generate_refresh_token)(
            user_id=refresh_token.user_id,
            org_id=refresh_token.organization_id,
            client_id=refresh_token.client_id,
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

        # Store new refresh token
        new_refresh_data = FutureAGIRefreshToken(
            token=new_refresh_str,
            client_id=refresh_token.client_id,
            scopes=effective_scopes,
            user_id=refresh_token.user_id,
            organization_id=refresh_token.organization_id,
            workspace_id=refresh_token.workspace_id,
        )
        await sync_to_async(cache.set)(
            f"{REFRESH_PREFIX}{new_refresh_str}",
            new_refresh_data.model_dump(mode="json"),
            REFRESH_TTL,
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
        logger.info("oauth_token_revoked", client_id=token.client_id)
