"""
WebAuthn/Passkey backend tests.

Covers registration, authentication, listing, deletion, cross-org isolation,
2FA status integration, and error cases.

All py_webauthn verification functions are mocked since we cannot perform real
browser attestation in tests.
"""

import json
import uuid
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.recovery_code import RecoveryCode
from accounts.models.workspace import WorkspaceMembership
from accounts.models.webauthn_credential import WebAuthnCredential
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

# ---------------------------------------------------------------------------
# Helpers for mocking py_webauthn responses
# ---------------------------------------------------------------------------

FAKE_CREDENTIAL_ID = b"fake-credential-id-bytes-1234"
FAKE_CREDENTIAL_ID_B64 = "ZmFrZS1jcmVkZW50aWFsLWlkLWJ5dGVzLTEyMzQ"
FAKE_PUBLIC_KEY = b"fake-public-key-bytes-5678"
FAKE_AAGUID = "00000000-0000-0000-0000-000000000000"
FAKE_CHALLENGE = b"test-challenge-bytes-abcdef1234"
FAKE_CHALLENGE_B64 = "dGVzdC1jaGFsbGVuZ2UtYnl0ZXMtYWJjZGVmMTIzNA"


@dataclass
class FakeRegistrationVerification:
    credential_id: bytes = FAKE_CREDENTIAL_ID
    credential_public_key: bytes = FAKE_PUBLIC_KEY
    sign_count: int = 0
    aaguid: str = FAKE_AAGUID
    credential_backed_up: bool = False


@dataclass
class FakeAuthenticationVerification:
    new_sign_count: int = 1


def _fake_registration_options():
    """Return a mock object that mimics generate_registration_options output."""
    mock_options = MagicMock()
    mock_options.challenge = FAKE_CHALLENGE
    return mock_options


def _fake_authentication_options():
    """Return a mock object that mimics generate_authentication_options output."""
    mock_options = MagicMock()
    mock_options.challenge = FAKE_CHALLENGE
    return mock_options


def _fake_options_to_json(options):
    """Return a minimal JSON string that mimics options_to_json."""
    return json.dumps(
        {
            "challenge": FAKE_CHALLENGE_B64,
            "rp": {"name": "FutureAGI", "id": "localhost"},
            "timeout": 60000,
        }
    )


def _create_passkey_for_user(user, name="My Passkey", credential_id=None):
    """Directly create a WebAuthnCredential in the database for testing."""
    return WebAuthnCredential.objects.create(
        user=user,
        name=name,
        credential_id=credential_id or FAKE_CREDENTIAL_ID_B64,
        public_key="ZmFrZS1wdWJsaWMta2V5",
        sign_count=0,
        aaguid=FAKE_AAGUID,
    )


def _assert_unknown_field(response, field_name):
    assert response.status_code == 400
    assert field_name in response.json()["details"]


def _create_workspace_viewer(organization, workspace, email):
    viewer = User.objects.create_user(
        email=email,
        password="testpassword123",
        name="Passkey Viewer",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER_VIEW_ONLY,
        is_active=True,
    )
    org_membership = OrganizationMembership.no_workspace_objects.create(
        user=viewer,
        organization=organization,
        role=OrganizationRoles.MEMBER_VIEW_ONLY,
        level=Level.VIEWER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=viewer,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_VIEWER,
        level=Level.WORKSPACE_VIEWER,
        organization_membership=org_membership,
        is_active=True,
    )
    return viewer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_webauthn_cache():
    """Clear WebAuthn-related cache entries between tests."""
    yield
    # Clean up any lingering challenge keys
    # Django cache doesn't support pattern-based delete, but individual tests
    # set known keys that will expire naturally.


# ---------------------------------------------------------------------------
# A. Registration Options
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyRegisterOptions:
    @patch(
        "accounts.services.webauthn_service.options_to_json",
        side_effect=_fake_options_to_json,
    )
    @patch(
        "accounts.services.webauthn_service.generate_registration_options",
        return_value=_fake_registration_options(),
    )
    def test_register_options_returns_webauthn_data(
        self, mock_gen, mock_json, auth_client, user
    ):
        """POST /accounts/passkey/register/options/ returns WebAuthn options."""
        response = auth_client.post("/accounts/passkey/register/options/")
        assert response.status_code == 200
        data = response.json()
        assert "challenge" in data
        assert "rp" in data

    @patch(
        "accounts.services.webauthn_service.options_to_json",
        side_effect=_fake_options_to_json,
    )
    @patch(
        "accounts.services.webauthn_service.generate_registration_options",
        return_value=_fake_registration_options(),
    )
    def test_register_options_allows_workspace_viewer_user_owned_setup(
        self, mock_gen, mock_json, api_client, organization, workspace
    ):
        """Workspace write restrictions must not block user-owned passkey setup."""
        viewer = _create_workspace_viewer(
            organization,
            workspace,
            "passkey-viewer@futureagi.com",
        )
        login = api_client.post(
            "/accounts/token/",
            {"email": viewer.email, "password": "testpassword123"},
            format="json",
        )
        assert login.status_code == 200

        response = api_client.post(
            "/accounts/passkey/register/options/",
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}",
            HTTP_X_ORGANIZATION_ID=str(organization.id),
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )

        assert response.status_code == 200
        assert "challenge" in response.json()

    def test_register_options_unauthenticated(self):
        """Unauthenticated request to register options is rejected."""
        client = APIClient()
        response = client.post("/accounts/passkey/register/options/")
        assert response.status_code in (401, 403)

    def test_register_options_rejects_unknown_request_fields(self, auth_client):
        """Empty request contracts should not accept stray body fields."""
        response = auth_client.post(
            "/accounts/passkey/register/options/",
            {"unexpected": True},
            format="json",
        )
        _assert_unknown_field(response, "unexpected")


# ---------------------------------------------------------------------------
# B. Registration Verify
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyRegisterVerify:
    def _setup_challenge(self, user):
        """Store a fake registration challenge in cache."""
        challenge_key = f"webauthn_reg_challenge:{user.id}"
        cache.set(challenge_key, FAKE_CHALLENGE_B64, timeout=120)

    @patch(
        "accounts.services.webauthn_service.verify_registration_response",
        return_value=FakeRegistrationVerification(),
    )
    @patch(
        "webauthn.helpers.base64url_to_bytes",
        return_value=FAKE_CHALLENGE,
    )
    def test_register_verify_creates_passkey(
        self, mock_b64, mock_verify, auth_client, user
    ):
        """Successful registration creates a WebAuthnCredential."""
        self._setup_challenge(user)

        response = auth_client.post(
            "/accounts/passkey/register/verify/",
            {
                "credential": {"id": "test", "type": "public-key"},
                "name": "My Laptop Key",
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["name"] == "My Laptop Key"

        # Verify credential was stored
        assert WebAuthnCredential.objects.filter(user=user).count() == 1
        cred = WebAuthnCredential.objects.get(user=user)
        assert cred.name == "My Laptop Key"

    @patch(
        "accounts.services.webauthn_service.verify_registration_response",
        return_value=FakeRegistrationVerification(),
    )
    @patch(
        "webauthn.helpers.base64url_to_bytes",
        return_value=FAKE_CHALLENGE,
    )
    def test_register_verify_returns_recovery_codes_on_first_2fa(
        self, mock_b64, mock_verify, auth_client, user
    ):
        """First passkey registration returns recovery codes."""
        self._setup_challenge(user)
        assert RecoveryCode.objects.filter(user=user).count() == 0

        response = auth_client.post(
            "/accounts/passkey/register/verify/",
            {"credential": {"id": "test", "type": "public-key"}, "name": "Key 1"},
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert "recovery_codes" in data
        assert len(data["recovery_codes"]) == 10

    def test_register_verify_expired_challenge(self, auth_client, user):
        """Registration with expired challenge returns error."""
        # Don't set up a challenge — simulate expiry
        response = auth_client.post(
            "/accounts/passkey/register/verify/",
            {"credential": {"id": "test", "type": "public-key"}},
            format="json",
        )
        assert response.status_code == 400

    @patch(
        "accounts.services.webauthn_service.verify_registration_response",
        side_effect=Exception("Invalid credential"),
    )
    @patch(
        "webauthn.helpers.base64url_to_bytes",
        return_value=FAKE_CHALLENGE,
    )
    def test_register_verify_invalid_credential(
        self, mock_b64, mock_verify, auth_client, user
    ):
        """Invalid credential data returns 400."""
        self._setup_challenge(user)

        response = auth_client.post(
            "/accounts/passkey/register/verify/",
            {"credential": {"id": "bad", "type": "public-key"}},
            format="json",
        )
        assert response.status_code == 400

    def test_register_verify_missing_credential_field(self, auth_client, user):
        """Missing credential field returns 400."""
        response = auth_client.post(
            "/accounts/passkey/register/verify/",
            {"name": "key without credential"},
            format="json",
        )
        assert response.status_code == 400

    def test_register_verify_rejects_unknown_request_fields(self, auth_client, user):
        """Registration verify accepts the documented credential/name shape only."""
        self._setup_challenge(user)

        response = auth_client.post(
            "/accounts/passkey/register/verify/",
            {
                "credential": {"id": "test", "type": "public-key"},
                "name": "My Laptop Key",
                "deviceName": "legacy camel alias",
            },
            format="json",
        )
        _assert_unknown_field(response, "deviceName")

    def test_register_verify_unauthenticated(self):
        """Unauthenticated request is rejected."""
        client = APIClient()
        response = client.post(
            "/accounts/passkey/register/verify/",
            {"credential": {"id": "test", "type": "public-key"}},
            format="json",
        )
        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# C. List Passkeys
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyList:
    def test_list_passkeys_empty(self, auth_client, user):
        """Empty list when user has no passkeys."""
        response = auth_client.get("/accounts/passkeys/")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_passkeys_with_data(self, auth_client, user):
        """Returns all passkeys for the authenticated user."""
        _create_passkey_for_user(user, name="Key A", credential_id="cred-a")
        _create_passkey_for_user(user, name="Key B", credential_id="cred-b")

        response = auth_client.get("/accounts/passkeys/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        names = {pk["name"] for pk in data}
        assert names == {"Key A", "Key B"}

    def test_list_passkeys_unauthenticated(self):
        """Unauthenticated request is rejected."""
        client = APIClient()
        response = client.get("/accounts/passkeys/")
        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# D. Delete Passkey
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyDelete:
    def test_delete_passkey(self, auth_client, user):
        """Successfully delete an existing passkey."""
        passkey = _create_passkey_for_user(user)

        response = auth_client.delete(f"/accounts/passkeys/{passkey.id}/")
        assert response.status_code == 204
        assert not WebAuthnCredential.objects.filter(id=passkey.id).exists()

    def test_delete_nonexistent_passkey(self, auth_client, user):
        """Deleting a nonexistent passkey returns 404."""
        fake_id = uuid.uuid4()
        response = auth_client.delete(f"/accounts/passkeys/{fake_id}/")
        assert response.status_code == 404
        data = response.json()
        assert data["status"] is False
        assert data["type"] == "not_found"
        assert data["code"] == "not_found"
        assert data["detail"] == "Passkey not found."
        assert data["message"] == data["detail"]
        assert data["error"] == data["detail"]
        assert data["result"] == data["detail"]

    def test_delete_passkey_cleans_up_recovery_codes_when_last_2fa(
        self, auth_client, user
    ):
        """Deleting the last 2FA method removes recovery codes."""
        passkey = _create_passkey_for_user(user)

        # Create some recovery codes
        from accounts.services.recovery_service import generate_recovery_codes

        generate_recovery_codes(user)
        assert RecoveryCode.objects.filter(user=user).count() == 10

        # Delete the only passkey (last 2FA method)
        response = auth_client.delete(f"/accounts/passkeys/{passkey.id}/")
        assert response.status_code == 204

        # Recovery codes should be cleaned up
        assert RecoveryCode.objects.filter(user=user).count() == 0

    def test_delete_passkey_unauthenticated(self):
        """Unauthenticated request is rejected."""
        client = APIClient()
        fake_id = uuid.uuid4()
        response = client.delete(f"/accounts/passkeys/{fake_id}/")
        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# E. Rename Passkey
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyRename:
    def test_rename_passkey(self, auth_client, user):
        """Successfully rename a passkey."""
        passkey = _create_passkey_for_user(user, name="Old Name")

        response = auth_client.patch(
            f"/accounts/passkeys/{passkey.id}/",
            {"name": "New Name"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

        passkey.refresh_from_db()
        assert passkey.name == "New Name"

    def test_rename_nonexistent_passkey(self, auth_client, user):
        """Renaming a nonexistent passkey returns 404."""
        fake_id = uuid.uuid4()
        response = auth_client.patch(
            f"/accounts/passkeys/{fake_id}/",
            {"name": "Whatever"},
            format="json",
        )
        assert response.status_code == 404
        data = response.json()
        assert data["status"] is False
        assert data["type"] == "not_found"
        assert data["code"] == "not_found"
        assert data["detail"] == "Passkey not found."
        assert data["message"] == data["detail"]
        assert data["error"] == data["detail"]
        assert data["result"] == data["detail"]

    def test_rename_rejects_unknown_request_fields(self, auth_client, user):
        """Passkey rename should reject fields outside the request serializer."""
        passkey = _create_passkey_for_user(user, name="Old Name")

        response = auth_client.patch(
            f"/accounts/passkeys/{passkey.id}/",
            {"name": "New Name", "displayName": "legacy camel alias"},
            format="json",
        )
        _assert_unknown_field(response, "displayName")


# ---------------------------------------------------------------------------
# F. Authenticate Options (passwordless)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyAuthenticateOptions:
    @patch(
        "accounts.services.webauthn_service.options_to_json",
        side_effect=_fake_options_to_json,
    )
    @patch(
        "accounts.services.webauthn_service.generate_authentication_options",
        return_value=_fake_authentication_options(),
    )
    def test_authenticate_options_returns_data(self, mock_gen, mock_json):
        """POST /accounts/passkey/authenticate/options/ returns auth options."""
        client = APIClient()
        response = client.post("/accounts/passkey/authenticate/options/")
        assert response.status_code == 200
        data = response.json()
        assert "challenge" in data
        assert "session_id" in data

    def test_authenticate_options_rejects_unknown_request_fields(self):
        """Passwordless auth options are an empty-body API."""
        client = APIClient()
        response = client.post(
            "/accounts/passkey/authenticate/options/",
            {"email": "someone@example.com"},
            format="json",
        )
        _assert_unknown_field(response, "email")


# ---------------------------------------------------------------------------
# G. Authenticate Verify (passwordless login)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyAuthenticateVerify:
    def _setup_auth_challenge(self, session_id="test-session-123"):
        """Store a fake auth challenge in cache."""
        challenge_data = json.dumps({"challenge": FAKE_CHALLENGE_B64, "user_id": None})
        cache.set(
            f"webauthn_auth_challenge:{session_id}",
            challenge_data,
            timeout=120,
        )
        return session_id

    @patch(
        "accounts.services.webauthn_service.verify_authentication_response",
        return_value=FakeAuthenticationVerification(),
    )
    @patch(
        "webauthn.helpers.base64url_to_bytes",
        return_value=FAKE_CHALLENGE,
    )
    def test_authenticate_verify_returns_tokens(self, mock_b64, mock_verify, user):
        """Valid passkey authentication returns JWT tokens."""
        passkey = _create_passkey_for_user(user)
        session_id = self._setup_auth_challenge()

        client = APIClient()
        response = client.post(
            "/accounts/passkey/authenticate/verify/",
            {
                "session_id": session_id,
                "credential": {
                    "id": passkey.credential_id,
                    "rawId": passkey.credential_id,
                    "type": "public-key",
                    "response": {
                        "authenticatorData": "dGVzdA",
                        "clientDataJSON": "dGVzdA",
                        "signature": "dGVzdA",
                    },
                },
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert "access" in data
        assert "refresh" in data

    def test_authenticate_verify_expired_challenge(self, user):
        """Authentication with expired challenge returns 400."""
        _create_passkey_for_user(user)

        client = APIClient()
        response = client.post(
            "/accounts/passkey/authenticate/verify/",
            {
                "session_id": "expired-session",
                "credential": {
                    "id": FAKE_CREDENTIAL_ID_B64,
                    "rawId": FAKE_CREDENTIAL_ID_B64,
                    "type": "public-key",
                },
            },
            format="json",
        )
        assert response.status_code == 400

    def test_authenticate_verify_missing_credential(self):
        """Missing credential data returns 400."""
        client = APIClient()
        response = client.post(
            "/accounts/passkey/authenticate/verify/",
            {"session_id": "test"},
            format="json",
        )
        assert response.status_code == 400

    def test_authenticate_verify_rejects_unknown_request_fields(self, user):
        """Passwordless auth verify accepts only credential/session_id/name."""
        _create_passkey_for_user(user)

        client = APIClient()
        response = client.post(
            "/accounts/passkey/authenticate/verify/",
            {
                "session_id": "test",
                "credential": {
                    "id": FAKE_CREDENTIAL_ID_B64,
                    "rawId": FAKE_CREDENTIAL_ID_B64,
                    "type": "public-key",
                },
                "sessionId": "legacy camel alias",
            },
            format="json",
        )
        _assert_unknown_field(response, "sessionId")

    @patch(
        "accounts.services.webauthn_service.verify_authentication_response",
        side_effect=Exception("Invalid signature"),
    )
    @patch(
        "webauthn.helpers.base64url_to_bytes",
        return_value=FAKE_CHALLENGE,
    )
    def test_authenticate_verify_invalid_credential(self, mock_b64, mock_verify, user):
        """Invalid credential returns 400."""
        passkey = _create_passkey_for_user(user)
        session_id = self._setup_auth_challenge()

        client = APIClient()
        response = client.post(
            "/accounts/passkey/authenticate/verify/",
            {
                "session_id": session_id,
                "credential": {
                    "id": passkey.credential_id,
                    "rawId": passkey.credential_id,
                    "type": "public-key",
                    "response": {
                        "authenticatorData": "dGVzdA",
                        "clientDataJSON": "dGVzdA",
                        "signature": "dGVzdA",
                    },
                },
            },
            format="json",
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# H. Multiple Passkeys & Cross-org Isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMultiplePasskeys:
    def test_multiple_passkeys_per_user(self, auth_client, user):
        """A user can register and list multiple passkeys."""
        _create_passkey_for_user(user, name="Laptop", credential_id="cred-laptop")
        _create_passkey_for_user(user, name="Phone", credential_id="cred-phone")
        _create_passkey_for_user(user, name="YubiKey", credential_id="cred-yubikey")

        response = auth_client.get("/accounts/passkeys/")
        assert response.status_code == 200
        assert len(response.json()) == 3


@pytest.mark.django_db
class TestPasskeyCrossOrgIsolation:
    def test_cannot_delete_other_users_passkey(self, auth_client, user, organization):
        """User A cannot delete User B's passkey."""
        from accounts.models.user import User

        user_b = User.objects.create_user(
            email="otheruser@futureagi.com",
            password="testpassword123",
            name="Other User",
            organization=organization,
        )
        passkey_b = _create_passkey_for_user(
            user_b, name="B's Key", credential_id="cred-user-b"
        )

        # User A (auth_client) tries to delete User B's passkey
        response = auth_client.delete(f"/accounts/passkeys/{passkey_b.id}/")
        assert response.status_code == 404

        # Passkey should still exist
        assert WebAuthnCredential.objects.filter(id=passkey_b.id).exists()

    def test_cannot_see_other_users_passkeys(self, auth_client, user, organization):
        """User A cannot see User B's passkeys in list."""
        from accounts.models.user import User

        user_b = User.objects.create_user(
            email="otheruser2@futureagi.com",
            password="testpassword123",
            name="Other User 2",
            organization=organization,
        )
        _create_passkey_for_user(user, name="A's Key", credential_id="cred-a-own")
        _create_passkey_for_user(user_b, name="B's Key", credential_id="cred-b-own")

        response = auth_client.get("/accounts/passkeys/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "A's Key"


# ---------------------------------------------------------------------------
# I. 2FA Status Integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskey2FAStatus:
    def test_passkey_counts_toward_2fa_status(self, auth_client, user):
        """Having a passkey sets 2FA status to enabled."""
        # Before: no 2FA
        response = auth_client.get("/accounts/2fa/status/")
        assert response.status_code == 200
        data = response.json()
        assert data["two_factor_enabled"] is False
        assert data["methods"]["passkey"]["enabled"] is False
        assert data["methods"]["passkey"]["count"] == 0

        # Register a passkey
        _create_passkey_for_user(user)

        # After: 2FA enabled via passkey
        response = auth_client.get("/accounts/2fa/status/")
        data = response.json()
        assert data["two_factor_enabled"] is True
        assert data["methods"]["passkey"]["enabled"] is True
        assert data["methods"]["passkey"]["count"] == 1

    def test_2fa_disabled_after_all_passkeys_deleted(self, auth_client, user):
        """Deleting all passkeys (no TOTP) disables 2FA."""
        passkey = _create_passkey_for_user(user)

        # Verify 2FA is enabled
        response = auth_client.get("/accounts/2fa/status/")
        assert response.json()["two_factor_enabled"] is True

        # Delete the passkey
        auth_client.delete(f"/accounts/passkeys/{passkey.id}/")

        # 2FA should now be disabled
        response = auth_client.get("/accounts/2fa/status/")
        assert response.json()["two_factor_enabled"] is False


# ---------------------------------------------------------------------------
# J. Passkey as 2FA (second-factor) verification
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskey2FAVerify:
    """Regression tests for the passkey-as-2FA verification flow.

    The frontend sends `credential` as a JSON-encoded string, and DRF's
    default JSONField(binary=False) does not parse it — so the view must
    handle string input. See TwoFactorVerifyPasskeyView.
    """

    def _setup_2fa_passkey_state(self, user, session_id="2fa-passkey-session"):
        from accounts.services.two_factor_challenge import create_challenge

        challenge_id = create_challenge(user, ["passkey"])
        cache.set(
            f"webauthn_auth_challenge:{session_id}",
            json.dumps(
                {"challenge": FAKE_CHALLENGE_B64, "user_id": str(user.id)}
            ),
            timeout=120,
        )
        return challenge_id, session_id

    @patch(
        "accounts.services.webauthn_service.verify_authentication_response",
        return_value=FakeAuthenticationVerification(),
    )
    @patch(
        "webauthn.helpers.base64url_to_bytes",
        return_value=FAKE_CHALLENGE,
    )
    def test_verify_accepts_stringified_credential(
        self, mock_b64, mock_verify, user
    ):
        """POST /accounts/2fa/verify/passkey/ must accept a JSON-string credential."""
        passkey = _create_passkey_for_user(user)
        challenge_id, session_id = self._setup_2fa_passkey_state(user)

        credential = {
            "id": passkey.credential_id,
            "rawId": passkey.credential_id,
            "type": "public-key",
            "response": {
                "authenticatorData": "dGVzdA",
                "clientDataJSON": "dGVzdA",
                "signature": "dGVzdA",
            },
        }

        client = APIClient()
        response = client.post(
            "/accounts/2fa/verify/passkey/",
            {
                "challenge_token": challenge_id,
                "session_id": session_id,
                "credential": json.dumps(credential),
            },
            format="json",
        )

        assert response.status_code == 200, response.json()
        data = response.json()
        assert "access" in data
        assert "refresh" in data

    @patch(
        "accounts.services.webauthn_service.verify_authentication_response",
        return_value=FakeAuthenticationVerification(),
    )
    @patch(
        "webauthn.helpers.base64url_to_bytes",
        return_value=FAKE_CHALLENGE,
    )
    def test_verify_accepts_dict_credential(self, mock_b64, mock_verify, user):
        """Dict payload (non-stringified) must also work for API parity."""
        passkey = _create_passkey_for_user(user)
        challenge_id, session_id = self._setup_2fa_passkey_state(user)

        client = APIClient()
        response = client.post(
            "/accounts/2fa/verify/passkey/",
            {
                "challenge_token": challenge_id,
                "session_id": session_id,
                "credential": {
                    "id": passkey.credential_id,
                    "rawId": passkey.credential_id,
                    "type": "public-key",
                    "response": {
                        "authenticatorData": "dGVzdA",
                        "clientDataJSON": "dGVzdA",
                        "signature": "dGVzdA",
                    },
                },
            },
            format="json",
        )

        assert response.status_code == 200, response.json()

    def test_verify_rejects_malformed_credential_string(self, user):
        """Non-JSON string credential returns 400, not 500."""
        _create_passkey_for_user(user)
        challenge_id, session_id = self._setup_2fa_passkey_state(user)

        client = APIClient()
        response = client.post(
            "/accounts/2fa/verify/passkey/",
            {
                "challenge_token": challenge_id,
                "session_id": session_id,
                "credential": "not-json-at-all",
            },
            format="json",
        )
        assert response.status_code == 400
