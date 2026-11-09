"""OAuth 2.1 client utilities for MCP connector authentication.

Implements:
- OAuth server metadata discovery (RFC 8414)
- Dynamic Client Registration (RFC 7591)
- PKCE (RFC 7636) with S256 challenge method
- Token exchange and refresh flows
"""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
import structlog
from django.utils import timezone

from ee.falcon_ai.credentials import (
    FalconCredentialError,
    decrypt_falcon_token,
    encrypt_falcon_token,
    require_persistent_key,
)
from ee.falcon_ai.oauth_registration import registration_operation, save_registration

logger = structlog.get_logger(__name__)

HTTP_TIMEOUT = 15.0


def _encode_token(value: str) -> str:
    """Encrypt a provider token for storage using the existing Fernet key."""
    return encrypt_falcon_token(value)


def _decode_token(value: str) -> str:
    """Read encrypted storage only; legacy rows must be migrated first."""
    return decrypt_falcon_token(value)


def discover_oauth_metadata(server_url: str) -> dict:
    """Fetch OAuth 2.0 Authorization Server Metadata (RFC 8414).

    Tries /.well-known/oauth-authorization-server first, then falls
    back to /.well-known/openid-configuration.

    Returns the metadata dict or raises an exception.
    """
    # Normalize the base URL
    base_url = server_url.rstrip("/")
    # Strip any path beyond the origin for well-known discovery
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    well_known_paths = [
        "/.well-known/oauth-authorization-server",
        "/.well-known/openid-configuration",
    ]

    for path in well_known_paths:
        url = origin + path
        try:
            response = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
            if response.status_code == 200:
                metadata = response.json()
                logger.info(
                    "oauth_metadata_discovered",
                    url=url,
                    issuer=metadata.get("issuer"),
                )
                return metadata
        except Exception:
            logger.debug("oauth_metadata_attempt_failed")
            continue

    raise RuntimeError("Could not discover OAuth metadata")


def register_client(metadata: dict, redirect_uri: str) -> dict:
    """Dynamic Client Registration (RFC 7591).

    Registers this application as an OAuth client with the authorization
    server. Returns the registration response containing client_id and
    optionally client_secret.
    """
    registration_endpoint = metadata.get("registration_endpoint")
    if not registration_endpoint:
        raise RuntimeError(
            "OAuth server does not support Dynamic Client Registration "
            "(no registration_endpoint in metadata)"
        )

    registration_payload = {
        "client_name": "Future AGI Falcon AI",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }

    response = httpx.post(
        registration_endpoint,
        json=registration_payload,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()

    logger.info(
        "oauth_client_registered",
        client_id=result.get("client_id"),
        endpoint=registration_endpoint,
    )
    return result


def generate_pkce() -> dict:
    """Generate PKCE code_verifier and code_challenge (S256).

    Returns:
        {"code_verifier": str, "code_challenge": str}
    """
    # code_verifier: 43-128 chars of unreserved URI characters
    code_verifier = secrets.token_urlsafe(64)[:96]

    # code_challenge: BASE64URL(SHA256(code_verifier))
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    return {
        "code_verifier": code_verifier,
        "code_challenge": code_challenge,
    }


def build_authorization_url(connector, redirect_uri: str) -> str:
    """Read fresh registration, perform HTTP unlocked, then generation-CAS save."""
    require_persistent_key()
    with registration_operation(connector) as current:
        return _build_authorization_url(current, redirect_uri)


def _build_authorization_url(connector, redirect_uri: str) -> str:
    """Prepare registration/PKCE; a superseding writer makes persistence fail."""
    needs_registration = not connector.oauth_client_id
    if connector.oauth_client_secret:
        try:
            _decode_token(connector.oauth_client_secret)
        except FalconCredentialError:
            needs_registration = True

    # Step 1: Discover metadata if not cached
    metadata = connector.oauth_server_metadata
    if not metadata or not metadata.get("authorization_endpoint"):
        metadata = discover_oauth_metadata(connector.server_url)
        connector.oauth_server_metadata = metadata

    # Step 2: Replace an unreadable confidential registration, not just its
    # authorization grant. A public replacement must also clear the old secret.
    if needs_registration:
        reg_result = register_client(metadata, redirect_uri)
        client_id = reg_result.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise RuntimeError("OAuth registration failed")
        client_secret = _encode_token(reg_result.get("client_secret", ""))
        connector.oauth_client_id = client_id
        connector.oauth_client_secret = client_secret
        # Never pair a replacement identity with the superseded grant.
        connector.oauth_access_token = ""
        connector.oauth_refresh_token = ""
        connector.oauth_token_expires_at = None
        connector.is_verified = False

    # Step 3: Generate PKCE
    pkce = generate_pkce()
    connector.oauth_code_verifier = pkce["code_verifier"]

    # Step 4: Generate state
    state = secrets.token_urlsafe(32)
    connector.oauth_state = state

    # Step 5: Save connector
    save_registration(
        connector,
        update_fields=[
            "oauth_server_metadata",
            "oauth_client_id",
            "oauth_client_secret",
            "oauth_code_verifier",
            "oauth_state",
            "updated_at",
        ]
        + (
            [
                "oauth_access_token",
                "oauth_refresh_token",
                "oauth_token_expires_at",
                "is_verified",
            ]
            if needs_registration
            else []
        ),
    )

    # Step 6: Build the URL
    authorization_endpoint = metadata["authorization_endpoint"]
    scopes = metadata.get("scopes_supported", [])
    scope_str = " ".join(scopes) if scopes else ""

    params = {
        "response_type": "code",
        "client_id": connector.oauth_client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": pkce["code_challenge"],
        "code_challenge_method": "S256",
    }
    if scope_str:
        params["scope"] = scope_str

    url = f"{authorization_endpoint}?{urlencode(params)}"
    logger.info("oauth_authorization_url_built", connector=connector.name)
    return url


def exchange_code_for_tokens(connector, code: str, redirect_uri: str) -> dict:
    """Validate before provider exchange and again at guarded persistence."""
    with registration_operation(connector, match_registration=True) as current:
        return _exchange_code_for_tokens(current, code, redirect_uri)


def _exchange_code_for_tokens(connector, code: str, redirect_uri: str) -> dict:
    """Exchange without a row lock; discard responses to superseded attempts."""
    metadata = connector.oauth_server_metadata
    token_endpoint = metadata.get("token_endpoint")
    if not token_endpoint:
        raise RuntimeError("No token_endpoint in OAuth metadata")

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": connector.oauth_client_id,
        "code_verifier": connector.oauth_code_verifier,
    }

    # Include client_secret if we have one
    client_secret = _decode_token(connector.oauth_client_secret)
    if client_secret:
        payload["client_secret"] = client_secret

    response = httpx.post(
        token_endpoint,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    token_data = response.json()

    # Save tokens
    connector.oauth_access_token = _encode_token(token_data.get("access_token", ""))
    connector.oauth_refresh_token = _encode_token(token_data.get("refresh_token", ""))

    expires_in = token_data.get("expires_in")
    if expires_in:
        connector.oauth_token_expires_at = timezone.now() + timedelta(
            seconds=int(expires_in)
        )
    else:
        connector.oauth_token_expires_at = None

    # Clear PKCE and state (single-use)
    connector.oauth_code_verifier = ""
    connector.oauth_state = ""
    connector.is_verified = True
    connector.last_error = ""

    save_registration(
        connector,
        update_fields=[
            "oauth_access_token",
            "oauth_refresh_token",
            "oauth_token_expires_at",
            "oauth_code_verifier",
            "oauth_state",
            "is_verified",
            "last_error",
            "updated_at",
        ],
    )

    logger.info(
        "oauth_tokens_exchanged",
        connector=connector.name,
        has_refresh=bool(token_data.get("refresh_token")),
    )
    return token_data


def refresh_access_token(connector) -> dict:
    """Validate a live generation before HTTP and CAS before saving rotation."""
    with registration_operation(connector, match_registration=True) as current:
        return _refresh_access_token(current)


def _refresh_access_token(connector) -> dict:
    """No retries after a conflict: a provider may have consumed the old token."""
    metadata = connector.oauth_server_metadata
    token_endpoint = metadata.get("token_endpoint")
    if not token_endpoint:
        raise RuntimeError("No token_endpoint in OAuth metadata")

    refresh_token = _decode_token(connector.oauth_refresh_token)
    if not refresh_token:
        raise RuntimeError("No refresh token available; re-authorization required")

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": connector.oauth_client_id,
    }

    client_secret = _decode_token(connector.oauth_client_secret)
    if client_secret:
        payload["client_secret"] = client_secret

    response = httpx.post(
        token_endpoint,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    token_data = response.json()

    # Update stored tokens
    connector.oauth_access_token = _encode_token(token_data.get("access_token", ""))

    # Some servers rotate refresh tokens
    if token_data.get("refresh_token"):
        connector.oauth_refresh_token = _encode_token(token_data["refresh_token"])

    expires_in = token_data.get("expires_in")
    if expires_in:
        connector.oauth_token_expires_at = timezone.now() + timedelta(
            seconds=int(expires_in)
        )
    else:
        connector.oauth_token_expires_at = None

    connector.last_error = ""
    save_registration(
        connector,
        update_fields=[
            "oauth_access_token",
            "oauth_refresh_token",
            "oauth_token_expires_at",
            "last_error",
            "updated_at",
        ],
    )

    logger.info("oauth_token_refreshed", connector=connector.name)
    return token_data


def get_valid_access_token(connector) -> str:
    """Get a valid access token, refreshing if expired.

    Returns the plaintext access token string.
    Raises RuntimeError if no token is available and refresh fails.
    """
    access_token = _decode_token(connector.oauth_access_token)

    if not access_token:
        raise RuntimeError("No OAuth access token; connector requires authorization")

    # Check expiration (with 60-second buffer)
    if connector.oauth_token_expires_at:
        if timezone.now() >= connector.oauth_token_expires_at - timedelta(seconds=60):
            logger.info("oauth_token_expired_refreshing", connector=connector.name)
            try:
                refresh_access_token(connector)
                access_token = _decode_token(connector.oauth_access_token)
            except FalconCredentialError:
                raise
            except Exception:
                logger.error("oauth_refresh_failed", connector_id=str(connector.pk))
                raise RuntimeError("OAuth token expired and refresh failed") from None

    return access_token
