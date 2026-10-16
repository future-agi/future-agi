import structlog
from django.core.cache import cache
from django.utils import timezone

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    PASSWORD_RESET_TOKEN_CACHE_KEY,
    REFRESH_TOKEN_EXPIRATION_TIME_IN_SECONDS,
    AuthToken,
    AuthTokenType,
)

logger = structlog.get_logger(__name__)


def _password_reset_cache_key(user_id):
    return PASSWORD_RESET_TOKEN_CACHE_KEY.format(user_id=user_id)


def invalidate_password_reset_tokens(user):
    """Deactivate outstanding password-reset links for ``user``.

    Only ids recorded when the link was minted are touched. A live session is
    also an ACCESS token, and killing every ACCESS row here would sign the
    account out just for asking for a reset.
    """
    cache_key = _password_reset_cache_key(user.id)
    token_ids = [str(token_id) for token_id in (cache.get(cache_key) or [])]
    if not token_ids:
        return
    for token_id in token_ids:
        cache.delete(f"access_token_{token_id}")
    AuthToken.objects.filter(
        id__in=token_ids,
        user=user,
        auth_type=AuthTokenType.ACCESS.value,
        is_active=True,
    ).update(is_active=False)
    cache.delete(cache_key)


def remember_password_reset_token(user, token_id):
    """Record ``token_id`` as the only live reset link for ``user``."""
    cache.set(
        _password_reset_cache_key(user.id),
        [str(token_id)],
        timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
    )


def issue_tokens(user):
    """Create access + refresh tokens for the user.
    Extracted from CustomTokenObtainPairView.post().
    A password-reset link is also an ACCESS row. Signing in again retires any
    outstanding link, so a reset mailed before this login cannot still change
    the password.
    """
    invalidate_password_reset_tokens(user)

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

    # Create new access token
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
