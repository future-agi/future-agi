"""OAuth token utilities using Fernet encryption."""

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from django.conf import settings


def _get_fernet():
    """Get Fernet instance using Django SECRET_KEY (same approach as accounts/authentication.py)."""
    secret_key = settings.SECRET_KEY
    if isinstance(secret_key, bytes):
        secret_key_bytes = secret_key
    else:
        secret_key_bytes = secret_key.encode()
    key = base64.b64encode(hashlib.sha256(secret_key_bytes).digest())
    return Fernet(key)


def generate_authorization_code() -> str:
    """Generate a cryptographically secure authorization code."""
    return secrets.token_urlsafe(32)


def generate_oauth_token(
    user_id, org_id, workspace_id, client_id, scope, expires_in=3600
):
    """Generate encrypted OAuth access token. Returns (token_str, expires_at)."""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    payload = {
        "type": "mcp_oauth",
        "user_id": str(user_id),
        "org_id": str(org_id),
        "workspace_id": str(workspace_id) if workspace_id else None,
        "client_id": client_id,
        "scope": scope,
        "expires_at": expires_at.isoformat(),
    }
    f = _get_fernet()
    encrypted = f.encrypt(json.dumps(payload, sort_keys=True).encode())
    return encrypted.decode(), expires_at


def refresh_token_ttl_seconds() -> int:
    """Configured MCP OAuth refresh-token lifetime in seconds."""
    from mcp_server.constants import DEFAULT_OAUTH_REFRESH_TOKEN_TTL_SECONDS

    ttl = getattr(
        settings,
        "MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS",
        DEFAULT_OAUTH_REFRESH_TOKEN_TTL_SECONDS,
    )
    return max(int(ttl), 1)


def generate_refresh_token(user_id, org_id, client_id, expires_at=None):
    """Generate an encrypted refresh token.

    When ``expires_at`` is given it is carried in the token and enforced by
    decrypt_oauth_token; servers that store refresh tokens also enforce it on
    their own record (see mcp_server.refresh_tokens).
    """
    payload = {
        "type": "mcp_refresh",
        "user_id": str(user_id),
        "org_id": str(org_id),
        "client_id": client_id,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "jti": secrets.token_urlsafe(16),
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at.isoformat()
    f = _get_fernet()
    encrypted = f.encrypt(json.dumps(payload, sort_keys=True).encode())
    return encrypted.decode()


def refresh_token_expiry(now=None):
    """Expiry for a refresh token issued now."""
    now = now or datetime.now(timezone.utc)
    return now + timedelta(seconds=refresh_token_ttl_seconds())


def hash_refresh_token(token: str) -> str:
    """Stable lookup key for a refresh token (the token itself is not stored)."""
    return hashlib.sha256(token.encode()).hexdigest()


def decrypt_oauth_token(token):
    """Decrypt and validate an OAuth token. Returns payload dict or None."""
    try:
        f = _get_fernet()
        decrypted = f.decrypt(token.encode())
        payload = json.loads(decrypted)

        if payload.get("type") not in ("mcp_oauth", "mcp_refresh"):
            return None

        # Check expiry. Access tokens always carry one; refresh tokens issued
        # before expiry was introduced do not, and are bounded by the
        # server-side record instead.
        if payload.get("type") == "mcp_oauth" or payload.get("expires_at"):
            expires_at = datetime.fromisoformat(payload["expires_at"])
            if datetime.now(timezone.utc) > expires_at:
                return None

        return payload
    except Exception:
        return None


def hash_client_secret(secret):
    """Hash a client secret using SHA-256."""
    return hashlib.sha256(secret.encode()).hexdigest()


def verify_client_secret(secret, stored_hash):
    """Verify a client secret against a stored hash."""
    return hashlib.sha256(secret.encode()).hexdigest() == stored_hash
