"""Sole storage boundary for access-token cache entries.

No caller constructs this namespace or copies an arbitrary payload back into it.
Legacy/contaminated entries are evicted, never upgraded using cached credentials.
"""

import hashlib
import hmac

from django.core.cache import cache

_PREFIX = "access_token_"
_FIELDS = frozenset({"token_digest", "user", "non_saml_v1"})
_REQUIRED = frozenset({"token_digest", "user"})


def _key(token_id):
    return f"{_PREFIX}{token_id}"


def _backend(cache_backend):
    return cache if cache_backend is None else cache_backend


def is_access_token_cache_key(key):
    """Reserve the namespace against generic cache-management writes."""
    return isinstance(key, str) and key.startswith(_PREFIX)


def _valid_digest(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _valid_payload(payload):
    return (
        isinstance(payload, dict)
        and _REQUIRED <= payload.keys() <= _FIELDS
        and _valid_digest(payload["token_digest"])
        and ("non_saml_v1" not in payload or type(payload["non_saml_v1"]) is bool)
    )


def _store(token_id, payload, *, timeout, cache_backend):
    # Whitelist here, not in each caller: user refresh must never copy a legacy
    # token or an unexpected credential field back into the namespace.
    clean = {field: payload[field] for field in _FIELDS if field in payload}
    if not _valid_payload(clean):
        raise ValueError("Invalid access-cache payload")
    _backend(cache_backend).set(_key(token_id), clean, timeout=timeout)


def cache_access_token(
    token_id, bearer, user, *, timeout, non_saml=False, cache_backend=None
):
    """Keep the caller's TTL; only the authoritative reader classifies sessions."""
    payload = {
        "token_digest": hashlib.sha256(bearer.encode("utf-8")).hexdigest(),
        "user": user,
    }
    if non_saml:
        payload["non_saml_v1"] = True
    _store(token_id, payload, timeout=timeout, cache_backend=cache_backend)


def get_cached_access_token(token_id, *, cache_backend=None):
    """Fail closed and evict any legacy, malformed or contaminated entry."""
    backend = _backend(cache_backend)
    payload = backend.get(_key(token_id))
    if payload is None:
        return None
    if not _valid_payload(payload):
        backend.delete(_key(token_id))
        return None
    return dict(payload)


def refresh_cached_access_user(token_id, user, *, timeout, cache_backend=None):
    """Refresh only accepted snapshots; preserve digest, marker and caller TTL."""
    if user is None:
        revoke_cached_access_token(token_id, cache_backend=cache_backend)
        return
    payload = get_cached_access_token(token_id, cache_backend=cache_backend)
    if payload is not None:
        payload["user"] = user
        _store(token_id, payload, timeout=timeout, cache_backend=cache_backend)


def revoke_cached_access_token(token_id, *, cache_backend=None):
    return _backend(cache_backend).delete(_key(token_id))


def revoke_cached_access_tokens(token_ids, *, cache_backend=None):
    return _backend(cache_backend).delete_many([_key(token_id) for token_id in token_ids])


def matches(cached, bearer):
    """Constant-time bearer verification, separate from snapshot/schema checks."""
    if not isinstance(cached, dict) or cached.keys() - _FIELDS:
        return False
    digest = cached.get("token_digest")
    if not _valid_digest(digest):
        return False
    expected = hashlib.sha256(bearer.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, expected)
