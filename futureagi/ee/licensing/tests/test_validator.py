"""Integration tests for the license validator.

Generates a real RSA keypair and proves signed-token round trips,
wrong key rejection, algorithm checks, expiry/grace/trial states,
and malformed input handling.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings
from ee.licensing import keyring, validator
from ee.licensing.keyring import PublicKeyEntry
from tfc.licensing.types import LicenseState, LicenseType


@pytest.fixture(autouse=True)
def reset_keyring():
    """Reset keyring between tests."""
    keyring._KEY_RING = {}
    yield
    keyring._KEY_RING = {}


@pytest.fixture()
def rsa_keypair():
    """Generate a fresh RSA keypair for testing."""
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


@pytest.fixture()
def loaded_keyring(rsa_keypair):
    """Load the test public key into the keyring."""
    _, public_pem = rsa_keypair
    keyring._KEY_RING = {
        "test-kid-1": PublicKeyEntry(
            kid="test-kid-1",
            algorithm="RS256",
            public_key=public_pem,
        )
    }
    return rsa_keypair


def _sign_license(private_pem: str, claims: dict, kid: str = "test-kid-1") -> str:
    return jwt.encode(
        claims,
        private_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )


def _valid_claims(expires_in_days: int = 365, **overrides) -> dict:
    now = int(time.time())
    claims = {
        "typ": "futureagi-enterprise-license",
        "schema_version": 1,
        "license_id": "lic_test_001",
        "customer_id": "cus_test_001",
        "issued_to": "Test Corp",
        "iss": "https://licenses.futureagi.com",
        "aud": "futureagi-self-hosted",
        "iat": now,
        "nbf": now,
        "exp": now + (expires_in_days * 86400),
        "license_type": "production",
        "band": "business",
        "features": ["voice_sim", "agentic_eval", "falcon_ai"],
        "limits": {
            "traces_monthly": 1_000_000,
            "gateway_requests_monthly": 500_000,
        },
        "max_instances": 3,
        "grace_days": 90,
    }
    claims.update(overrides)
    if isinstance(claims.get("exp"), int):
        if "iat" not in overrides and claims["iat"] > claims["exp"]:
            claims["iat"] = claims["exp"] - 86400
        if "nbf" not in overrides and claims["nbf"] > claims["exp"]:
            claims["nbf"] = claims["iat"]
    return claims


class TestSignedRoundTrip:
    def test_valid_license_returns_active(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        token = _sign_license(private_pem, _valid_claims())

        result = validator.validate(token)

        assert result.state == LicenseState.ACTIVE
        assert result.license_type == LicenseType.PRODUCTION
        assert result.license_id == "lic_test_001"
        assert result.issued_to == "Test Corp"
        assert result.band == "business"
        assert "voice_sim" in result.features
        assert result.limits["traces_monthly"] == 1_000_000
        assert result.max_instances == 3
        assert result.expires_at is not None
        assert result.grace_ends_at is not None

    def test_trial_license_returns_trial_active(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        token = _sign_license(private_pem, _valid_claims(license_type="trial"))

        result = validator.validate(token)

        assert result.state == LicenseState.TRIAL_ACTIVE
        assert result.license_type == LicenseType.TRIAL


class TestWrongKey:
    def test_wrong_key_rejects(self, loaded_keyring):
        # Sign with a different key
        other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_pem = other_private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        token = _sign_license(other_pem, _valid_claims())

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID

    def test_unknown_kid_rejects(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        token = _sign_license(private_pem, _valid_claims(), kid="nonexistent-kid")

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID


class TestAlgorithm:
    def test_disallowed_algorithm(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        # Manually craft a token with HS256 header
        token = jwt.encode(
            _valid_claims(),
            "shared-secret",
            algorithm="HS256",
            headers={"kid": "test-kid-1"},
        )

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID


class TestIssuerAudience:
    def test_wrong_issuer(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        claims = _valid_claims(iss="https://evil.com")
        token = _sign_license(private_pem, claims)

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID

    def test_wrong_audience(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        claims = _valid_claims(aud="wrong-audience")
        token = _sign_license(private_pem, claims)

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID


class TestMalformedClaims:
    def test_missing_license_id(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        claims = _valid_claims()
        del claims["license_id"]
        token = _sign_license(private_pem, claims)

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID

    def test_wrong_schema_version(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        claims = _valid_claims(schema_version=999)
        token = _sign_license(private_pem, claims)

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID

    def test_wrong_type(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        claims = _valid_claims(typ="wrong-type")
        token = _sign_license(private_pem, claims)

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID

    def test_empty_key(self):
        result = validator.validate("")
        assert result.state == LicenseState.MISSING

    def test_garbage_token(self, loaded_keyring):
        result = validator.validate("not.a.valid.jwt.token.at.all")
        assert result.state == LicenseState.INVALID

    @pytest.mark.parametrize(
        ("claim", "value"),
        [
            ("iat", "yesterday"),
            ("nbf", "today"),
            ("exp", "tomorrow"),
            ("band", ["business"]),
            ("features", "voice_sim"),
            ("features", ["voice_sim", 123]),
            ("features", ["a" * 300]),
            ("features", [f"f{i}" for i in range(500)]),
            ("limits", []),
            ("limits", {"traces_monthly": True}),
            ("limits", {"traces_monthly": -2}),
            ("limits", {"traces_monthly": 10**15}),
            ("max_instances", 0),
            ("max_instances", 10_001),
            ("grace_days", -1),
            ("grace_days", 4000),
            ("issued_to", "a" * 300),
            ("min_version", "not-a-version"),
        ],
    )
    def test_malformed_claim_types_return_invalid(self, loaded_keyring, claim, value):
        private_pem, _ = loaded_keyring
        token = _sign_license(private_pem, _valid_claims(**{claim: value}))

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID

    def test_future_issued_at_returns_invalid(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        token = _sign_license(
            private_pem,
            _valid_claims(iat=int(time.time()) + 86400),
        )

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID

    def test_inconsistent_dates_return_invalid(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        now = int(time.time())
        token = _sign_license(
            private_pem,
            _valid_claims(iat=now, nbf=now, exp=now - 60),
        )

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID


class TestExpiry:
    def test_expired_production_enters_grace(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        claims = _valid_claims(
            exp=int(time.time()) - 3600,  # expired 1 hour ago
            grace_days=90,
        )
        token = _sign_license(private_pem, claims)

        result = validator.validate(token)

        assert result.state == LicenseState.GRACE
        assert result.grace_ends_at is not None

    def test_expired_past_grace(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        claims = _valid_claims(
            exp=int(time.time()) - (100 * 86400),  # expired 100 days ago
            grace_days=90,
        )
        token = _sign_license(private_pem, claims)

        result = validator.validate(token)

        assert result.state == LicenseState.EXPIRED

    def test_trial_expired_no_grace(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        claims = _valid_claims(
            exp=int(time.time()) - 3600,
            license_type="trial",
            grace_days=90,  # grace_days ignored for trials
        )
        token = _sign_license(private_pem, claims)

        result = validator.validate(token)

        assert result.state == LicenseState.TRIAL_EXPIRED

    def test_no_grace_days_means_immediate_expiry(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        claims = _valid_claims(
            exp=int(time.time()) - 3600,
            grace_days=0,
        )
        token = _sign_license(private_pem, claims)

        result = validator.validate(token)

        assert result.state == LicenseState.EXPIRED


class TestClockSkew:
    def test_within_skew_tolerance(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        # Expired 2 minutes ago, but 5 min skew tolerance
        claims = _valid_claims(exp=int(time.time()) - 120)
        token = _sign_license(private_pem, claims)

        with patch.object(keyring, "get_clock_skew_seconds", return_value=300):
            result = validator.validate(token)

        assert result.state == LicenseState.ACTIVE

    def test_beyond_skew_tolerance(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        # Expired 10 minutes ago, 5 min skew tolerance
        claims = _valid_claims(
            exp=int(time.time()) - 600,
            grace_days=90,
        )
        token = _sign_license(private_pem, claims)

        with patch.object(keyring, "get_clock_skew_seconds", return_value=300):
            result = validator.validate(token)

        assert result.state == LicenseState.GRACE


class TestNbf:
    def test_nbf_future_rejected(self, loaded_keyring):
        private_pem, _ = loaded_keyring
        claims = _valid_claims(nbf=int(time.time()) + 86400)  # 1 day in future
        token = _sign_license(private_pem, claims)

        result = validator.validate(token)

        assert result.state == LicenseState.INVALID


class TestKeyRotation:
    def test_accepts_multiple_keys(self, rsa_keypair):
        """Simulate key rotation: old key still works alongside new."""
        private_pem_1, public_pem_1 = rsa_keypair

        # Generate second keypair
        private_key_2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem_2 = private_key_2.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        public_pem_2 = (
            private_key_2.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )

        # Load both keys
        keyring._KEY_RING = {
            "kid-v1": PublicKeyEntry(
                kid="kid-v1", algorithm="RS256", public_key=public_pem_1
            ),
            "kid-v2": PublicKeyEntry(
                kid="kid-v2", algorithm="RS256", public_key=public_pem_2
            ),
        }

        # Token signed with old key
        token_1 = _sign_license(private_pem_1, _valid_claims(), kid="kid-v1")
        result_1 = validator.validate(token_1)
        assert result_1.state == LicenseState.ACTIVE

        # Token signed with new key
        token_2 = _sign_license(private_pem_2, _valid_claims(), kid="kid-v2")
        result_2 = validator.validate(token_2)
        assert result_2.state == LicenseState.ACTIVE


class TestMinVersion:
    def test_min_version_missing_env_allows(self, loaded_keyring, monkeypatch):
        monkeypatch.delenv("APP_VERSION", raising=False)
        private_pem, _ = loaded_keyring
        token = _sign_license(private_pem, _valid_claims(min_version="10.0.0"))
        result = validator.validate(token)
        assert result.state == LicenseState.ACTIVE
        assert result.min_version == "10.0.0"

    def test_running_version_below_min_returns_invalid(
        self, loaded_keyring, monkeypatch
    ):
        monkeypatch.setenv("APP_VERSION", "1.5.0")
        private_pem, _ = loaded_keyring
        token = _sign_license(private_pem, _valid_claims(min_version="2.0.0"))
        result = validator.validate(token)
        assert result.state == LicenseState.INVALID

    def test_running_version_at_or_above_min_activates(
        self, loaded_keyring, monkeypatch
    ):
        monkeypatch.setenv("APP_VERSION", "v2.1.3")
        private_pem, _ = loaded_keyring
        token = _sign_license(private_pem, _valid_claims(min_version="2.0.0"))
        result = validator.validate(token)
        assert result.state == LicenseState.ACTIVE


def _make_keypair() -> tuple[str, str]:
    """Generate a fresh RSA keypair, returned as (private_pem, public_pem)."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        priv.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


class TestSelfSignedLicenseRejected:
    """End-to-end: a deployment must not be able to validate a license it
    signed itself by injecting its own public key via env, once a production
    key is bundled. This is the adversarial complement to the keyring
    trust-root unit tests — it forges a real token against a self-provided
    kid and asserts the validator rejects it.
    """

    def test_forged_token_on_fresh_env_kid_rejected_when_bundled_present(self):
        # The real attack: add a brand-new kid (no collision with any bundled
        # kid) via EE_LICENSE_PUBLIC_KEYS, then sign a license with it.
        _, bundled_pub = _make_keypair()
        attacker_priv, attacker_pub = _make_keypair()

        bundled = PublicKeyEntry(
            kid="prod-2026", algorithm="RS256", public_key=bundled_pub
        )
        env_keys = json.dumps(
            [
                {
                    "kid": "attacker-kid",
                    "algorithm": "RS256",
                    "public_key": attacker_pub,
                }
            ]
        )
        with patch.object(keyring, "_BUNDLED_KEYS", (bundled,)):
            with override_settings(
                EE_LICENSE_PUBLIC_KEY="", EE_LICENSE_PUBLIC_KEYS=env_keys
            ):
                keyring.load_keyring_from_settings()
                forged = _sign_license(
                    attacker_priv, _valid_claims(), kid="attacker-kid"
                )
                result = validator.validate(forged)

        assert (
            result.state == LicenseState.INVALID
        ), "self-signed license validated against a self-provided env kid"

    def test_default_kid_env_key_rejected_when_bundled_present(self):
        # EE_LICENSE_PUBLIC_KEY claims the "default" kid — also must not enter
        # the ring while a bundled root exists.
        _, bundled_pub = _make_keypair()
        attacker_priv, attacker_pub = _make_keypair()
        bundled = PublicKeyEntry(
            kid="prod-2026", algorithm="RS256", public_key=bundled_pub
        )
        with patch.object(keyring, "_BUNDLED_KEYS", (bundled,)):
            with override_settings(
                EE_LICENSE_PUBLIC_KEY=attacker_pub, EE_LICENSE_PUBLIC_KEYS=""
            ):
                keyring.load_keyring_from_settings()
                forged = _sign_license(attacker_priv, _valid_claims(), kid="default")
                result = validator.validate(forged)

        assert result.state == LicenseState.INVALID

    def test_same_forged_token_validates_without_bundled_root(self):
        # Control: with NO bundled root (pre-GA escape hatch), the env key IS
        # the trust source, so the same token validates — proving the token is
        # well-formed and only the bundled guard blocks it above.
        attacker_priv, attacker_pub = _make_keypair()
        env_keys = json.dumps(
            [
                {
                    "kid": "attacker-kid",
                    "algorithm": "RS256",
                    "public_key": attacker_pub,
                }
            ]
        )
        with patch.object(keyring, "_BUNDLED_KEYS", ()):
            with override_settings(
                EE_LICENSE_PUBLIC_KEY="", EE_LICENSE_PUBLIC_KEYS=env_keys
            ):
                keyring.load_keyring_from_settings()
                token = _sign_license(
                    attacker_priv, _valid_claims(), kid="attacker-kid"
                )
                result = validator.validate(token)

        assert result.state == LicenseState.ACTIVE
