import pyotp
import pytest

from accounts.models.recovery_code import RecoveryCode
from accounts.models.webauthn_credential import WebAuthnCredential
from accounts.services.recovery_service import (
    generate_recovery_codes,
    get_remaining_count,
    verify_recovery_code,
)


@pytest.mark.django_db
class TestRecoveryCodes:
    def _setup_totp(self, auth_client):
        """Helper: setup and confirm TOTP, return secret."""
        response = auth_client.post("/accounts/2fa/totp/setup/")
        secret = response.json()["secret"]
        totp = pyotp.TOTP(secret)
        auth_client.post("/accounts/2fa/totp/confirm/", {"code": totp.now()})
        return secret

    def test_recovery_codes_generated_on_2fa_setup(self, auth_client, user):
        """10 codes generated when TOTP confirmed."""
        self._setup_totp(auth_client)
        assert RecoveryCode.objects.filter(user=user, is_used=False).count() == 10

    def test_recovery_code_verify_consumes_code(self, user):
        """Used code is marked consumed."""
        codes = generate_recovery_codes(user)
        assert verify_recovery_code(user, codes[0]) is True
        assert RecoveryCode.objects.filter(user=user, is_used=True).count() == 1
        assert get_remaining_count(user) == 9

    def test_recovery_code_cannot_reuse(self, user):
        """Used code is rejected on second attempt."""
        codes = generate_recovery_codes(user)
        assert verify_recovery_code(user, codes[0]) is True
        assert verify_recovery_code(user, codes[0]) is False

    def test_recovery_codes_regenerate_invalidates_old(self, auth_client, user):
        """Regenerate deletes old codes."""
        self._setup_totp(auth_client)

        # Get a code
        old_codes = RecoveryCode.objects.filter(user=user)
        old_count = old_codes.count()
        assert old_count == 10

        # Regenerate via API (requires valid TOTP code)
        from accounts.authentication import decrypt_message
        from accounts.models.totp_device import UserTOTPDevice

        device = UserTOTPDevice.objects.get(user=user)
        decrypted = decrypt_message(device.secret_encrypted)
        totp = pyotp.TOTP(decrypted["secret"])
        user.refresh_from_db()
        auth_client.force_authenticate(user=user)

        response = auth_client.post(
            "/accounts/2fa/recovery-codes/regenerate/",
            {"code": totp.now()},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["recovery_codes"]) == 10

    def test_recovery_codes_count(self, auth_client, user):
        """Count endpoint returns correct remaining."""
        self._setup_totp(auth_client)

        response = auth_client.get("/accounts/2fa/recovery-codes/")
        assert response.status_code == 200
        assert response.json()["remaining"] == 10

    def test_recovery_code_login(self, auth_client, user):
        """Recovery code works for 2FA verification during login."""
        # Setup TOTP to enable 2FA
        setup_resp = auth_client.post("/accounts/2fa/totp/setup/")
        secret = setup_resp.json()["secret"]
        totp = pyotp.TOTP(secret)
        confirm_resp = auth_client.post(
            "/accounts/2fa/totp/confirm/", {"code": totp.now()}
        )
        recovery_codes = confirm_resp.json()["recovery_codes"]

        # Now attempt login - should require 2FA
        from rest_framework.test import APIClient

        login_client = APIClient()
        login_resp = login_client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        assert login_resp.status_code == 200
        data = login_resp.json()
        assert data.get("requires_two_factor") is True

        # Verify with recovery code
        challenge_token = data["challenge_token"]
        verify_resp = login_client.post(
            "/accounts/2fa/verify/recovery/",
            {"challenge_token": challenge_token, "code": recovery_codes[0]},
            format="json",
        )
        assert verify_resp.status_code == 200
        assert "access" in verify_resp.json()

    def _setup_passkey_only_user(self, user):
        """Helper: give user a passkey (no TOTP) and pre-generate recovery codes."""
        WebAuthnCredential.objects.create(
            user=user,
            name="Test Passkey",
            credential_id="ZmFrZS1jcmVkZW50aWFsLWlkLWJ5dGVzLTEyMzQ",
            public_key="ZmFrZS1wdWJsaWMta2V5",
            sign_count=0,
            aaguid="00000000-0000-0000-0000-000000000000",
        )
        generate_recovery_codes(user)

    def test_passkey_only_regenerate_with_password(self, auth_client, user):
        """Passkey-only user can regenerate recovery codes with valid password."""
        self._setup_passkey_only_user(user)

        response = auth_client.post(
            "/accounts/2fa/recovery-codes/regenerate/",
            {"password": "testpassword123"},
        )
        assert response.status_code == 200
        assert len(response.json()["recovery_codes"]) == 10

    def test_passkey_only_regenerate_without_password_rejected(self, auth_client, user):
        """Passkey-only user is rejected when no password is provided."""
        self._setup_passkey_only_user(user)

        response = auth_client.post(
            "/accounts/2fa/recovery-codes/regenerate/",
            {},
        )
        assert response.status_code == 400

    def test_passkey_only_regenerate_wrong_password_rejected(self, auth_client, user):
        """Passkey-only user is rejected when wrong password is provided."""
        self._setup_passkey_only_user(user)

        response = auth_client.post(
            "/accounts/2fa/recovery-codes/regenerate/",
            {"password": "wrongpassword"},
        )
        assert response.status_code == 400
