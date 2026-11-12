"""
Passkeys on the deployment's public app address.

With no passkey address configured (WEBAUTHN_RP_ID / WEBAUTHN_ORIGIN unset or
left at the built-in "localhost" defaults), the RP ID and the expected origin
come from the public app address (FRONTEND_BASE_URL, else APP_BASE_URL).
Explicit settings always win, and a local or missing address keeps today's
localhost values.

py_webauthn's generate/verify functions are mocked, as in test_passkeys.py.
"""

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from accounts.services import webauthn_service

DEFAULT_RP_ID = "localhost"
DEFAULT_ORIGIN = "http://localhost:3031"

FAKE_CHALLENGE = b"test-challenge-bytes-abcdef1234"
FAKE_CHALLENGE_B64 = "dGVzdC1jaGFsbGVuZ2UtYnl0ZXMtYWJjZGVmMTIzNA"


@dataclass
class _FakeRegistrationVerification:
    credential_id: bytes = b"public-address-credential-id"
    credential_public_key: bytes = b"public-address-public-key"
    sign_count: int = 0
    aaguid: str = "00000000-0000-0000-0000-000000000000"
    credential_backed_up: bool = False


def _address_settings(
    frontend="",
    app="",
    rp_id=DEFAULT_RP_ID,
    origin=DEFAULT_ORIGIN,
):
    """All four settings the rule reads, pinned so the test environment's own
    values cannot change the outcome."""
    return override_settings(
        WEBAUTHN_RP_ID=rp_id,
        WEBAUTHN_ORIGIN=origin,
        FRONTEND_BASE_URL=frontend,
        APP_BASE_URL=app,
    )


def _passkey_address():
    return webauthn_service._get_rp_id(), webauthn_service._get_origin()


def _fake_generate(**kwargs):
    """Mimic generate_*_options, keeping the RP ID it was given."""
    options = MagicMock()
    options.challenge = FAKE_CHALLENGE
    options.rp_id = kwargs.get("rp_id")
    return options


def _fake_options_to_json(options):
    """Mimic options_to_json: the RP ID the browser receives is the one the
    service passed to generate_*_options."""
    return json.dumps(
        {
            "challenge": FAKE_CHALLENGE_B64,
            "rp": {"name": "FutureAGI", "id": options.rp_id},
            "timeout": 60000,
        }
    )


# ---------------------------------------------------------------------------
# A. The RP ID and origin rule
# ---------------------------------------------------------------------------


class TestPasskeyAddressRule:
    @pytest.mark.parametrize(
        ("rp_id", "origin"),
        [(DEFAULT_RP_ID, DEFAULT_ORIGIN), ("", "")],
        ids=["builtin-defaults", "empty"],
    )
    def test_frontend_address_gives_rp_id_and_origin(self, rp_id, origin):
        with _address_settings(
            frontend="https://app.example.com", rp_id=rp_id, origin=origin
        ):
            assert _passkey_address() == (
                "app.example.com",
                "https://app.example.com",
            )

    def test_port_and_trailing_slash(self):
        with _address_settings(frontend="https://app.example.com:8443/"):
            assert _passkey_address() == (
                "app.example.com",
                "https://app.example.com:8443",
            )

    def test_host_is_lower_cased_and_path_dropped(self):
        with _address_settings(frontend="https://App.Example.COM/dashboard/"):
            assert _passkey_address() == (
                "app.example.com",
                "https://app.example.com",
            )

    @pytest.mark.parametrize(
        ("address", "origin"),
        [
            ("https://app.example.com:443/", "https://app.example.com"),
            ("http://app.example.com:80", "http://app.example.com"),
        ],
        ids=["https-443", "http-80"],
    )
    def test_default_port_is_left_out_like_the_browser(self, address, origin):
        with _address_settings(frontend=address):
            assert _passkey_address() == ("app.example.com", origin)

    def test_explicit_settings_win_over_public_address(self):
        with _address_settings(
            frontend="https://app.example.com",
            app="https://app.example.org",
            rp_id="example.com",
            origin="https://login.example.com",
        ):
            assert _passkey_address() == (
                "example.com",
                "https://login.example.com",
            )

    def test_explicit_rp_id_alone_keeps_derived_origin(self):
        with _address_settings(frontend="https://app.example.com", rp_id="example.com"):
            assert _passkey_address() == (
                "example.com",
                "https://app.example.com",
            )

    @pytest.mark.parametrize(
        ("frontend", "app"),
        [
            ("", ""),
            ("http://localhost:3000", ""),
            ("http://localhost:3031/", "http://localhost:3000"),
            ("http://127.0.0.1:3031", ""),
            ("", "http://localhost:3000"),
            ("app.example.com", ""),
            ("localhost:3031", ""),
            ("app.example.com", "https://app.example.org"),
            ("https://app.example.com:notaport", ""),
            ("ftp://app.example.com", ""),
        ],
        ids=[
            "no-address",
            "frontend-localhost",
            "both-localhost",
            "frontend-127",
            "app-localhost",
            "host-only",
            "localhost-host-port-only",
            "frontend-host-only-no-fallback",
            "bad-port",
            "not-http",
        ],
    )
    def test_local_or_unusable_address_keeps_todays_values(self, frontend, app):
        with _address_settings(frontend=frontend, app=app):
            assert _passkey_address() == (DEFAULT_RP_ID, DEFAULT_ORIGIN)

    def test_app_base_url_used_when_frontend_empty(self):
        with _address_settings(frontend="", app="https://app.example.org/"):
            assert _passkey_address() == (
                "app.example.org",
                "https://app.example.org",
            )


# ---------------------------------------------------------------------------
# B. What the browser receives and what the server checks
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyCeremonyUsesPublicAddress:
    @patch(
        "accounts.services.webauthn_service.options_to_json",
        side_effect=_fake_options_to_json,
    )
    @patch(
        "accounts.services.webauthn_service.generate_registration_options",
        side_effect=_fake_generate,
    )
    def test_registration_options_carry_derived_rp_id(
        self, mock_gen, mock_json, auth_client, user
    ):
        with _address_settings(frontend="https://app.example.com"):
            response = auth_client.post("/accounts/passkey/register/options/")

        assert response.status_code == 200
        assert response.json()["rp"]["id"] == "app.example.com"
        mock_gen.assert_called_once()
        assert mock_gen.call_args.kwargs["rp_id"] == "app.example.com"

    @patch(
        "accounts.services.webauthn_service.verify_registration_response",
        return_value=_FakeRegistrationVerification(),
    )
    @patch(
        "accounts.services.webauthn_service.base64url_to_bytes",
        return_value=FAKE_CHALLENGE,
    )
    def test_registration_verify_expects_derived_address(
        self, mock_b64, mock_verify, auth_client, user
    ):
        cache.set(f"webauthn_reg_challenge:{user.id}", FAKE_CHALLENGE_B64, timeout=120)

        with _address_settings(frontend="https://app.example.com:8443/"):
            response = auth_client.post(
                "/accounts/passkey/register/verify/",
                {
                    "credential": {"id": "test", "type": "public-key"},
                    "name": "Work Laptop",
                },
                format="json",
            )

        assert response.status_code == 201
        mock_verify.assert_called_once()
        assert mock_verify.call_args.kwargs["expected_rp_id"] == "app.example.com"
        assert (
            mock_verify.call_args.kwargs["expected_origin"]
            == "https://app.example.com:8443"
        )

    @patch(
        "accounts.services.webauthn_service.options_to_json",
        side_effect=_fake_options_to_json,
    )
    @patch(
        "accounts.services.webauthn_service.generate_authentication_options",
        side_effect=_fake_generate,
    )
    def test_authentication_options_carry_derived_rp_id(self, mock_gen, mock_json):
        with _address_settings(frontend="", app="https://app.example.org"):
            response = APIClient().post("/accounts/passkey/authenticate/options/")

        assert response.status_code == 200
        mock_gen.assert_called_once()
        assert mock_gen.call_args.kwargs["rp_id"] == "app.example.org"
