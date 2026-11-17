from accounts.services.auth_cache import refresh_cached_access_user

import base64
import io

import pyotp
import qrcode
import structlog
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from accounts.authentication import decrypt_message, generate_encrypted_message
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    REFRESH_TOKEN_EXPIRATION_TIME_IN_SECONDS,
    AuthToken,
    AuthTokenType,
)
from accounts.models.totp_device import UserTOTPDevice
from accounts.models.user import User

logger = structlog.get_logger(__name__)


def create_totp_device(user):
    """Create or replace an unconfirmed TOTP device for the user.
    Returns: (device, provisioning_uri, base32_secret)
    Raises ValueError if a confirmed TOTP device already exists.
    """
    # Reject if user already has a confirmed device
    if UserTOTPDevice.objects.filter(user=user, confirmed=True).exists():
        raise ValueError("A confirmed TOTP device already exists for this user.")

    # Delete any existing unconfirmed device
    UserTOTPDevice.objects.filter(user=user, confirmed=False).delete()

    # Generate a random base32 secret
    secret = pyotp.random_base32()

    # Encrypt the secret for storage
    encrypted = generate_encrypted_message({"secret": secret})

    # Create the device
    device = UserTOTPDevice.objects.create(
        user=user,
        secret_encrypted=encrypted,
        confirmed=False,
    )

    # Generate provisioning URI
    issuer = getattr(settings, "WEBAUTHN_RP_NAME", "FutureAGI")
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=user.email, issuer_name=issuer)

    return device, provisioning_uri, secret


def confirm_totp_device(user, code):
    """Verify a TOTP code against the user's unconfirmed device.
    If valid: mark device as confirmed, generate recovery codes, return True.
    """
    try:
        device = UserTOTPDevice.objects.get(user=user, confirmed=False)
    except UserTOTPDevice.DoesNotExist:
        return False

    # Decrypt the secret
    decrypted = decrypt_message(device.secret_encrypted)
    secret = decrypted.get("secret")
    if not secret:
        return False

    # Verify the code
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        return False

    # Mark as confirmed (don't set last_used_at — that's for login verification only,
    # and setting it here would trigger the replay-prevention window)
    device.confirmed = True
    device.save(update_fields=["confirmed", "updated_at"])

    return True


def verify_totp_code(user, code):
    """Verify a TOTP code against the user's confirmed device.
    Used during login 2FA verification.
    """
    try:
        device = UserTOTPDevice.objects.get(user=user, confirmed=True)
    except UserTOTPDevice.DoesNotExist:
        return False

    # Prevent replay: reject if last_used_at is within the current TOTP period (30s)
    if (
        device.last_used_at
        and (timezone.now() - device.last_used_at).total_seconds() < 30
    ):
        return False

    decrypted = decrypt_message(device.secret_encrypted)
    secret = decrypted.get("secret")
    if not secret:
        return False

    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        return False

    device.last_used_at = timezone.now()
    device.save(update_fields=["last_used_at", "updated_at"])
    return True


def _refresh_user_in_cached_tokens(user_id):
    """Update the cached User object in all active tokens for this user.

    Instead of deleting cached tokens (which forces logout), this fetches
    a fresh User from DB and replaces the stale User in each cached token.
    If the user no longer exists, deletes all their cached tokens.
    """
    try:
        fresh_user = User.objects.select_related("organization").get(id=user_id)
    except User.DoesNotExist:
        fresh_user = None

    active_tokens = AuthToken.objects.filter(user_id=user_id, is_active=True)

    for token in active_tokens.filter(auth_type=AuthTokenType.ACCESS.value):
        refresh_cached_access_user(
            token.id,
            fresh_user,
            timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
            cache_backend=cache,
        )

    for token in active_tokens.filter(auth_type=AuthTokenType.REFRESH.value):
        cache_key = f"refresh_token_{token.id}"
        if fresh_user is None:
            cache.delete(cache_key)
            continue
        cached = cache.get(cache_key)
        if cached:
            cached["user"] = fresh_user
            cache.set(
                cache_key, cached, timeout=REFRESH_TOKEN_EXPIRATION_TIME_IN_SECONDS
            )


def disable_totp(user):
    """Delete the user's TOTP device.
    If no other 2FA methods remain, also delete recovery codes.
    Refreshes the cached User in existing tokens so the stale
    totp_device relation is removed without invalidating the session.
    """
    UserTOTPDevice.objects.filter(user=user).delete()

    # If no other 2FA methods, clean up recovery codes
    has_passkeys = user.webauthn_credentials.exists()
    if not has_passkeys:
        from accounts.models.recovery_code import RecoveryCode

        RecoveryCode.objects.filter(user=user).delete()

    _refresh_user_in_cached_tokens(user.id)


def generate_qr_code_base64(provisioning_uri):
    """Generate a QR code image as a base64-encoded PNG data URI."""
    img = qrcode.make(provisioning_uri, box_size=6, border=4)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    encoded = base64.b64encode(buffer.read()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"
