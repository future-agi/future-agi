"""Public key management for license verification.

The self-hosted EE image bundles a public keyring (no private keys).
Keys are identified by `kid` in the JWT header. Supports key rotation
by retaining previous public keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

_KEY_RING: dict[str, "PublicKeyEntry"] = {}


@dataclass(frozen=True)
class PublicKeyEntry:
    kid: str
    algorithm: str
    public_key: str


# Official FutureAGI license-verification keys, baked into source so a
# deployment cannot swap the trust root via environment variables (a
# self-signed license against a self-provided env key must not validate
# without a source edit). Public keys only — signing keys live in the
# private cloud control plane. Drop the production key(s) here before GA.
#
# Trust rule: once ANY key is bundled here, environment/settings keys
# (EE_LICENSE_PUBLIC_KEY[S]) are ignored entirely — they can neither add a
# rotation kid nor override a bundled one. Rotation post-GA is therefore a
# source edit (add the new kid to this tuple and rebuild). Env keys are
# honored ONLY while this tuple is empty — the pre-GA / development escape
# hatch, where the deployment necessarily controls its own trust.
_BUNDLED_KEYS: tuple[PublicKeyEntry, ...] = ()


ALLOWED_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512"})

REQUIRED_ISSUER = "https://licenses.futureagi.com"
REQUIRED_AUDIENCE = "futureagi-self-hosted"
REQUIRED_TYPE = "futureagi-enterprise-license"

# 5 minutes default clock skew tolerance
DEFAULT_CLOCK_SKEW_SECONDS = 300


def load_keyring_from_settings() -> None:
    """Rebuild the process keyring.

    Trust model: ``_BUNDLED_KEYS`` is the *only* root of trust once a
    production key is present. When any key is bundled, environment/settings
    keys (``EE_LICENSE_PUBLIC_KEY[S]``) are ignored entirely — the ring is
    exactly the bundled set. This is what makes the module's guarantee real:
    with a bundled key present, a deployment cannot introduce a trusted kid
    via env (neither a fresh kid nor a colliding one), so it cannot validate
    a self-signed license without editing source and rebuilding the image.

    Environment keys are honored ONLY while ``_BUNDLED_KEYS`` is empty — the
    pre-GA / development escape hatch. SECURITY: in that state the deployment
    fully controls the trust set and CAN validate a self-signed license; it
    is logged loudly. Bake a production key into ``_BUNDLED_KEYS`` before GA
    to close it.
    """
    global _KEY_RING
    bundled = {entry.kid: entry for entry in _BUNDLED_KEYS}

    if bundled:
        # Production trust root present → bundled keys ONLY. Env is not
        # consulted, so a self-provided kid can never enter the trust ring.
        _KEY_RING = dict(bundled)
        if _settings_have_env_keys():
            logger.warning(
                "license_keyring_env_keys_ignored_bundled_trust_root_present"
            )
        logger.debug("license_keyring_loaded", key_count=len(_KEY_RING))
        return

    # No bundled trust root (pre-GA / development): env keys are the only
    # available trust source. Malformed config → empty ring (fail closed:
    # validation then rejects every token for "no public keys configured").
    try:
        env_keyring = _parse_env_keys()
    except (TypeError, ValueError, KeyError):
        logger.exception("license_keyring_load_failed")
        _KEY_RING = {}
        return

    _KEY_RING = env_keyring
    if env_keyring:
        logger.warning(
            "license_keyring_env_trust_no_bundled_key",
            key_count=len(env_keyring),
        )


def _settings_have_env_keys() -> bool:
    """True when EE_LICENSE_PUBLIC_KEY[S] is set — used only to warn that a
    bundled trust root is causing those env keys to be ignored."""
    try:
        from django.conf import settings
    except Exception:
        return False
    return bool(
        getattr(settings, "EE_LICENSE_PUBLIC_KEY", "")
        or getattr(settings, "EE_LICENSE_PUBLIC_KEYS", "")
    )


def _parse_env_keys() -> dict[str, "PublicKeyEntry"]:
    """Parse EE_LICENSE_PUBLIC_KEY[S] from settings into keyring entries.

    Only used pre-GA (no bundled trust root). Raises on malformed config so
    the caller can fall back to an empty ring rather than a partial one.
    """
    import json

    from django.conf import settings

    keyring: dict[str, PublicKeyEntry] = {}
    public_key = getattr(settings, "EE_LICENSE_PUBLIC_KEY", "").strip()
    if public_key:
        keyring["default"] = PublicKeyEntry(
            kid="default",
            algorithm="RS256",
            public_key=public_key,
        )

    keys_json = getattr(settings, "EE_LICENSE_PUBLIC_KEYS", "")
    if keys_json:
        keys_list = json.loads(keys_json)
        if not isinstance(keys_list, list):
            raise ValueError("EE_LICENSE_PUBLIC_KEYS must be a JSON list")

        for key_data in keys_list:
            if not isinstance(key_data, dict):
                raise ValueError("Each license public key must be an object")

            kid = key_data.get("kid")
            algorithm = key_data.get("algorithm", "RS256")
            configured_key = key_data.get("public_key")
            if not isinstance(kid, str) or not kid:
                raise ValueError("Each license public key requires a kid")
            if algorithm not in ALLOWED_ALGORITHMS:
                raise ValueError(f"Unsupported license key algorithm: {algorithm}")
            if not isinstance(configured_key, str) or not configured_key.strip():
                raise ValueError(f"License public key {kid} is empty")

            keyring[kid] = PublicKeyEntry(
                kid=kid,
                algorithm=algorithm,
                public_key=configured_key.strip(),
            )

    return keyring


def get_key(kid: str) -> Optional[PublicKeyEntry]:
    return _KEY_RING.get(kid)


def has_any_keys() -> bool:
    return len(_KEY_RING) > 0


def get_clock_skew_seconds() -> int:
    try:
        from django.conf import settings

        return int(
            getattr(
                settings, "EE_LICENSE_CLOCK_SKEW_SECONDS", DEFAULT_CLOCK_SKEW_SECONDS
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_CLOCK_SKEW_SECONDS
