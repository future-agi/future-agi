import structlog
from django.core.cache import cache
from django.utils import timezone

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    REFRESH_TOKEN_EXPIRATION_TIME_IN_SECONDS,
    AuthToken,
    AuthTokenType,
)

logger = structlog.get_logger(__name__)

_PRIMARY_ORG = object()


def identify_login_user(user, *, org=_PRIMARY_ORG):
    """Identify at interactive token issue, never at refresh or impersonation."""
    try:
        from analytics.posthog_util import IDENTITY_CACHE_TIMEOUT, posthog_tracker

        if not posthog_tracker.is_enabled:
            return
        key = f"posthog:identified:{user.id}"
        if not cache.add(key, True, timeout=IDENTITY_CACHE_TIMEOUT):
            return
        identified = False
        try:
            if org is _PRIMARY_ORG:
                org = user.organization
            # Only attribute a workspace preference scoped to the login organization.
            config = user.config if isinstance(user.config, dict) else {}
            workspaces = config.get("orgWorkspaceMap")
            workspace_id = None
            if org and isinstance(workspaces, dict):
                workspace_id = workspaces.get(str(org.id))
            identified = posthog_tracker.identify_user(
                user, org=org, workspace_id=workspace_id
            )
        finally:
            if not identified:
                cache.delete(key)  # Retry synchronous failures at the next login.
    except Exception:
        logger.warning("posthog_login_identification_failed", exc_info=True)


def issue_login_access_token(user, *, org=_PRIMARY_ORG):
    """Mint the interactive access token shared by password and OAuth logins."""
    access_token = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.ACCESS.value,
        last_used_at=timezone.now(),
        is_active=True,
    )
    encrypted = generate_encrypted_message(
        {"user_id": str(user.id), "id": str(access_token.id)}
    )
    cache.set(
        f"access_token_{access_token.id}",
        {"token": encrypted, "user": user},
        timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
    )
    identify_login_user(user, org=org)
    return encrypted


def issue_tokens(user, *, org=_PRIMARY_ORG):
    """Create access + refresh tokens for the user."""
    # Deactivate all previous refresh tokens for this user
    AuthToken.objects.filter(
        user=user, auth_type=AuthTokenType.REFRESH.value, is_active=True
    ).update(is_active=False)

    # Create new refresh token
    refresh_token = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.REFRESH.value,
        last_used_at=timezone.now(),
        is_active=True,
    )
    refresh_token_encrypted = generate_encrypted_message(
        {"user_id": str(user.id), "id": str(refresh_token.id)}
    )
    cache.set(
        f"refresh_token_{str(refresh_token.id)}",
        {"token": refresh_token_encrypted, "user": user},
        timeout=7 * 24 * 60 * 60,  # 7 days in seconds
    )

    return {
        "access": issue_login_access_token(user, org=org),
        "refresh": refresh_token_encrypted,
    }


def issue_sos_tokens(user):
    """Create access + refresh tokens for an SOS (support impersonation) session.

    Unlike issue_tokens(), this does NOT deactivate the user's existing refresh
    tokens — impersonating a user must not log them out of their own session.
    Returns: {'access': encrypted_token, 'refresh': encrypted_token}
    """
    refresh_token = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.REFRESH.value,
        last_used_at=timezone.now(),
        is_active=True,
    )
    refresh_token_encrypted = generate_encrypted_message(
        {"user_id": str(user.id), "id": str(refresh_token.id)}
    )
    cache.set(
        f"refresh_token_{str(refresh_token.id)}",
        {"token": refresh_token_encrypted, "user": user},
        timeout=REFRESH_TOKEN_EXPIRATION_TIME_IN_SECONDS,
    )

    access_token = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.ACCESS.value,
        last_used_at=timezone.now(),
        is_active=True,
    )
    access_token_encrypted = generate_encrypted_message(
        {"user_id": str(user.id), "id": str(access_token.id)}
    )
    cache.set(
        f"access_token_{str(access_token.id)}",
        {"token": access_token_encrypted, "user": user},
        timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
    )

    return {
        "access": access_token_encrypted,
        "refresh": refresh_token_encrypted,
    }
