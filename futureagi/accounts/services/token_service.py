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


def issue_tokens(user):
    """Create access + refresh tokens for the user.
    Extracted from CustomTokenObtainPairView.post().
    Returns: {'access': encrypted_token, 'refresh': encrypted_token}
    """
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
