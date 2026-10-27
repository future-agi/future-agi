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
from urllib.parse import urlencode, urljoin

import httpx
import structlog
from django.utils import timezone

logger = structlog.get_logger(__name__)

HTTP_TIMEOUT = 15.0


def _encode_token(value: str) -> str:
    """Encode a token value using simple base64 for storage."""
    if not value:
        return ""
    return base64.b64encode(value.encode("utf-8")).decode("utf-8")


def _decode_token(value: str) -> str:
    """Decode a base64-encoded token value."""
    if not value:
        return ""
    try:
        return base64.b64decode(value.encode("utf-8")).decode("utf-8")
    except Exception:
        # If it's not base64-encoded, return as-is (backward compat)
        return value


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

    last_error = None
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
        except Exception as e:
            last_error = e
            logger.debug("oauth_metadata_attempt_failed", url=url, error=str(e))
            continue

    raise RuntimeError(
        f"Could not discover OAuth metadata from {origin}. " f"Last error: {last_error}"
    )


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
    """Build the full OAuth authorization URL with PKCE.

    Discovers metadata if needed, registers client if needed, generates
    PKCE parameters, and saves state to the connector.

    Returns the authorization URL to redirect the user to.
    """
    # Step 1: Discover metadata if not cached
    metadata = connector.oauth_server_metadata
    if not metadata or not metadata.get("authorization_endpoint"):
        metadata = discover_oauth_metadata(connector.server_url)
        connector.oauth_server_metadata = metadata

    # Step 2: Register client if no client_id
    if not connector.oauth_client_id:
        reg_result = register_client(metadata, redirect_uri)
        connector.oauth_client_id = reg_result.get("client_id", "")
        if reg_result.get("client_secret"):
            connector.oauth_client_secret = _encode_token(reg_result["client_secret"])

    # Step 3: Generate PKCE
    pkce = generate_pkce()
    connector.oauth_code_verifier = pkce["code_verifier"]

    # Step 4: Generate state
    state = secrets.token_urlsafe(32)
    connector.oauth_state = state

    # Step 5: Save connector
    connector.save(
        update_fields=[
            "oauth_server_metadata",
            "oauth_client_id",
            "oauth_client_secret",
            "oauth_code_verifier",
            "oauth_state",
            "updated_at",
        ]
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
    """Exchange an authorization code for access and refresh tokens.

    Posts to the token endpoint with the code and PKCE verifier.
    Saves the tokens to the connector and returns the token response.
    """
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

    connector.save(
        update_fields=[
            "oauth_access_token",
            "oauth_refresh_token",
            "oauth_token_expires_at",
            "oauth_code_verifier",
            "oauth_state",
            "is_verified",
            "last_error",
            "updated_at",
        ]
    )

    logger.info(
        "oauth_tokens_exchanged",
        connector=connector.name,
        has_refresh=bool(token_data.get("refresh_token")),
    )
    return token_data


def refresh_access_token(connector) -> dict:
    """Refresh an expired access token using the refresh token.

    Returns the new token response dict.
    """
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
    connector.save(
        update_fields=[
            "oauth_access_token",
            "oauth_refresh_token",
            "oauth_token_expires_at",
            "last_error",
            "updated_at",
        ]
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
            except Exception as e:
                logger.error(
                    "oauth_refresh_failed",
                    connector=connector.name,
                    error=str(e),
                )
                raise RuntimeError(
                    f"OAuth token expired and refresh failed: {e}"
                ) from e

    return access_token
