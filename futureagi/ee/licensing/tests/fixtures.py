"""Test-signed licences for edition and capability tests.

A fresh RSA keypair is generated once per test session and trusted the way an
operator trusts a pre-GA key: through ``EE_LICENSE_PUBLIC_KEY``, which the
keyring honours only while ``_BUNDLED_KEYS`` is empty. No production key is
generated, requested or committed, and ``_BUNDLED_KEYS`` is never touched.

Usage::

    from ee.licensing.tests.fixtures import install_license, test_signing_keypair  # noqa: F401

    pytestmark = pytest.mark.edition_rule

    def test_something(install_license):
        snapshot = install_license("active")
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings

from tfc.licensing.types import DeploymentFlavor, DeploymentLocation, LicenseSnapshot

LICENSE_STATES = (
    "active",
    "grace",
    "trial",
    "expired",
    "trial_expired",
    "not_yet_valid",
    "wrong_key",
    "tampered",
    "missing",
    "no_keys",
)


def generate_keypair() -> tuple[str, str]:
    """Return (private_pem, public_pem) for a throwaway RS256 key."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def license_claims(expires_in_days: float = 365, **overrides) -> dict:
    now = int(time.time())
    exp = now + int(expires_in_days * 86400)
    claims = {
        "typ": "futureagi-enterprise-license",
        "schema_version": 1,
        "license_id": "lic_test_edition_9f2a",
        "customer_id": "cus_test_edition",
        "issued_to": "Edition Test Corp",
        "iss": "https://licenses.futureagi.com",
        "aud": "futureagi-self-hosted",
        "iat": min(now, exp - 86400),
        "nbf": min(now, exp - 86400),
        "exp": exp,
        "license_type": "production",
        "band": "business",
        "features": ["falcon_ai", "turing_models", "protect", "error_feed"],
        "limits": {},
        "max_instances": 1,
        "grace_days": 0,
    }
    claims.update(overrides)
    return claims


def sign_license(private_pem: str, claims: dict, kid: str = "default") -> str:
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


def _tamper(token: str) -> str:
    """Swap the payload for one claiming a far-future expiry, keeping the signature."""
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["exp"] = claims["exp"] + 10 * 365 * 86400
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{forged}.{signature}"


def token_for_state(state: str, private_pem: str) -> str:
    """A licence token that validates to ``state`` against the session key."""
    if state == "active":
        return sign_license(private_pem, license_claims())
    if state == "grace":
        return sign_license(
            private_pem, license_claims(expires_in_days=-1, grace_days=30)
        )
    if state == "trial":
        return sign_license(private_pem, license_claims(license_type="trial"))
    if state == "expired":
        return sign_license(private_pem, license_claims(expires_in_days=-10))
    if state == "trial_expired":
        return sign_license(
            private_pem, license_claims(expires_in_days=-10, license_type="trial")
        )
    if state == "not_yet_valid":
        now = int(time.time())
        return sign_license(private_pem, license_claims(iat=now, nbf=now + 2 * 86400))
    if state == "wrong_key":
        other_private, _ = generate_keypair()
        return sign_license(other_private, license_claims())
    if state == "tampered":
        return _tamper(sign_license(private_pem, license_claims()))
    if state in ("missing", "no_keys"):
        return "" if state == "missing" else sign_license(private_pem, license_claims())
    raise ValueError(f"unknown licence state {state!r}")


@pytest.fixture(scope="session")
def test_signing_keypair() -> tuple[str, str]:
    """Session-scoped throwaway signing keypair (never a production key)."""
    return generate_keypair()


@pytest.fixture
def install_license(test_signing_keypair) -> Callable[[str], LicenseSnapshot]:
    """Validate a test-signed licence through the real keyring and validator,
    publish it as the process snapshot, and wire the capability service's
    licence resolver to it. Everything is restored on teardown."""
    from ee.licensing import keyring, state
    from ee.licensing.validator import validate
    from tfc.capabilities import service

    assert keyring._BUNDLED_KEYS == (), (
        "Test-signed licences rely on EE_LICENSE_PUBLIC_KEY being trusted, "
        "which only holds while _BUNDLED_KEYS is empty."
    )

    saved_ring = dict(keyring._KEY_RING)
    saved_snapshot = state.get_snapshot()
    saved_service = (
        service._deployment_flavor,
        service._deployment_location,
        service._license_resolver,
        service._cloud_plan_resolver,
        service._configured,
    )
    private_pem, public_pem = test_signing_keypair
    overrides: list[override_settings] = []

    def _install(license_state: str = "active") -> LicenseSnapshot:
        public_key = "" if license_state == "no_keys" else public_pem
        override = override_settings(
            EE_LICENSE_PUBLIC_KEY=public_key, EE_LICENSE_PUBLIC_KEYS=""
        )
        override.enable()
        overrides.append(override)
        keyring.load_keyring_from_settings()
        snapshot = validate(token_for_state(license_state, private_pem))
        state.set_snapshot(snapshot)
        service.configure(
            flavor=DeploymentFlavor.SELF_HOSTED_EE,
            location=DeploymentLocation.SELF_HOSTED,
            license_resolver=state.get_resolver(),
        )
        return snapshot

    yield _install

    for override in reversed(overrides):
        override.disable()
    keyring._KEY_RING = saved_ring
    state.set_snapshot(saved_snapshot)
    (
        service._deployment_flavor,
        service._deployment_location,
        service._license_resolver,
        service._cloud_plan_resolver,
        service._configured,
    ) = saved_service
