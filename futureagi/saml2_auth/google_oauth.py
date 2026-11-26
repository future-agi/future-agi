"""Verify Google ID tokens independently of the legacy Auth0 route name."""

import base64
import hashlib
import hmac

import jwt
import requests
from django.conf import settings
from django.core.cache import cache

GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
JWKS_CACHE_SECONDS = 3600
# Google signs ID tokens with RS256. Decoding accepts only this algorithm; it
# is never read from the token's header.
SIGNING_ALGORITHM = "RS256"
# Clock difference tolerated on iat, nbf and exp, so a token Google issued a
# moment "in the future" by this server's clock still verifies.
CLOCK_SKEW_SECONDS = 10


def _fetch_keys(url, cache_key):
    response = requests.get(url, timeout=10, allow_redirects=False)
    response.raise_for_status()
    keys = response.json()["keys"]
    if not isinstance(keys, list) or not keys:
        raise jwt.InvalidTokenError("Google signing keys unavailable")
    cache.set(cache_key, keys, timeout=JWKS_CACHE_SECONDS)
    return keys


def _signing_key(keys, kid):
    for key in keys:
        if (
            isinstance(key, dict)
            and key.get("kid") == kid
            and key.get("kty") == "RSA"
            and key.get("use", "sig") == "sig"
            and key.get("alg", SIGNING_ALGORITHM) == SIGNING_ALGORITHM
        ):
            return key
    return None


def _access_token_hash(access_token):
    # OpenID Connect at_hash for RS256: the left half of SHA-256 over the
    # access token, base64url-encoded without padding.
    digest = hashlib.sha256(access_token.encode()).digest()
    return base64.urlsafe_b64encode(digest[: len(digest) // 2]).rstrip(b"=")


def _check_access_token_hash(claims, access_token):
    """A token that carries at_hash must match the access token it came with.

    The claim is optional: without it there is nothing to compare.
    """
    if "at_hash" not in claims:
        return
    if not isinstance(access_token, str) or not access_token:
        raise jwt.InvalidTokenError("No access token to compare with at_hash")
    at_hash = claims["at_hash"]
    if not isinstance(at_hash, str) or not hmac.compare_digest(
        at_hash.encode(), _access_token_hash(access_token)
    ):
        raise jwt.InvalidTokenError("Access token does not match at_hash")


def _decode_claims(id_token, key, client_id):
    """Check the RS256 signature and the standard claims against one JWK."""
    try:
        signing_key = jwt.PyJWK(key, algorithm=SIGNING_ALGORITHM)
    except (jwt.PyJWKError, jwt.InvalidKeyError) as error:
        raise jwt.InvalidTokenError("Invalid Google signing key") from error
    return jwt.decode(
        id_token,
        signing_key,
        algorithms=[SIGNING_ALGORITHM],
        audience=client_id,
        issuer=GOOGLE_ISSUERS,
        leeway=CLOCK_SKEW_SECONDS,
        options={"require": ["exp", "aud", "iss", "sub"]},
    )


def verify_google_id_token(id_token, client_id, access_token):
    """Verify signature, required claims, a verified email and the optional
    access-token hash.

    Keys come only from configured Google infrastructure, never a token's jku or
    x5u header. A cache miss fetches once; an unknown kid in a warm cache refreshes
    once so rotation does not lock out users until the TTL expires.
    """
    if not client_id:
        raise jwt.InvalidTokenError("Google client is not configured")
    header = jwt.get_unverified_header(id_token)
    kid = header.get("kid")
    if header.get("alg") != SIGNING_ALGORITHM or not isinstance(kid, str) or not kid:
        raise jwt.InvalidTokenError("Invalid Google signing key header")

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
        raise jwt.InvalidTokenError("Unknown Google signing key")

    claims = _decode_claims(id_token, key, client_id)
    _check_access_token_hash(claims, access_token)
    # Accounts are matched by email, so only an address Google has verified
    # may sign in or create one.
    verified = claims.get("email_verified")
    if str(verified).lower() != "true":
        raise jwt.InvalidTokenError("Google email is not verified")
    return claims
