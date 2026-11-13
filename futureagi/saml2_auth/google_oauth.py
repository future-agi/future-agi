"""Verify Google ID tokens independently of the legacy Auth0 route name."""

import hashlib

import requests
from django.conf import settings
from django.core.cache import cache
from jose import JWTError, jwt

GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
JWKS_CACHE_SECONDS = 3600


def _fetch_keys(url, cache_key):
    response = requests.get(url, timeout=10, allow_redirects=False)
    response.raise_for_status()
    keys = response.json()["keys"]
    if not isinstance(keys, list) or not keys:
        raise JWTError("Google signing keys unavailable")
    cache.set(cache_key, keys, timeout=JWKS_CACHE_SECONDS)
    return keys


def _signing_key(keys, kid):
    for key in keys:
        if (
            isinstance(key, dict)
            and key.get("kid") == kid
            and key.get("kty") == "RSA"
            and key.get("use", "sig") == "sig"
            and key.get("alg", "RS256") == "RS256"
        ):
            return key
    return None


def verify_google_id_token(id_token, client_id, access_token):
    """Verify signature, required claims, a verified email and the optional
    access-token hash.

    Keys come only from configured Google infrastructure, never a token's jku or
    x5u header. A cache miss fetches once; an unknown kid in a warm cache refreshes
    once so rotation does not lock out users until the TTL expires.
    """
    if not client_id:
        raise JWTError("Google client is not configured")
    header = jwt.get_unverified_header(id_token)
    kid = header.get("kid")
    if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
        raise JWTError("Invalid Google signing key header")

    url = settings.GOOGLE_OAUTH_JWKS_URL
    cache_key = "google-oauth-jwks:" + hashlib.sha256(url.encode()).hexdigest()
    keys = cache.get(cache_key)
    cached = keys is not None
    if keys is None:
        keys = _fetch_keys(url, cache_key)
    key = _signing_key(keys, kid)
    if key is None and cached:
        key = _signing_key(_fetch_keys(url, cache_key), kid)
    if key is None:
        raise JWTError("Unknown Google signing key")

    claims = jwt.decode(
        id_token,
        key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=GOOGLE_ISSUERS,
        access_token=access_token,
        options={
            "require_exp": True,
            "require_aud": True,
            "require_iss": True,
            "require_sub": True,
        },
    )
    # Accounts are matched by email, so only an address Google has verified
    # may sign in or create one.
    verified = claims.get("email_verified")
    if str(verified).lower() != "true":
        raise JWTError("Google email is not verified")
    return claims
