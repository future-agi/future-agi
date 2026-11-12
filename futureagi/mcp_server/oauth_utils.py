"""OAuth token utilities using Fernet encryption."""

import base64
import binascii
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


def generate_refresh_token(user_id, org_id, client_id):
    """Generate encrypted refresh token (no expiry)."""
    payload = {
        "type": "mcp_refresh",
        "user_id": str(user_id),
        "org_id": str(org_id),
        "client_id": client_id,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    f = _get_fernet()
    encrypted = f.encrypt(json.dumps(payload, sort_keys=True).encode())
    return encrypted.decode()


def is_canonical_oauth_token(token: str) -> bool:
    """Require the exact URL-safe base64 text emitted by Fernet.

    This is a format check, not authentication. A single accepted spelling keeps
    bearer-text revocation digests authoritative, including existing tombstones.
    """
    if not isinstance(token, str) or not token:
        return False
    try:
        encoded = token.encode("ascii")
        decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error):
        return False
    return base64.urlsafe_b64encode(decoded) == encoded


def decrypt_oauth_token(token):
    """Decrypt and validate an OAuth token. Returns payload dict or None."""
    if not is_canonical_oauth_token(token):
        return None
    try:
        f = _get_fernet()
        decrypted = f.decrypt(token.encode())
        payload = json.loads(decrypted)

        if payload.get("type") not in ("mcp_oauth", "mcp_refresh"):
            return None

        # Check expiry for access tokens
        if payload.get("type") == "mcp_oauth":
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
