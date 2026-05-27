import pyotp
import pytest
from django.test import override_settings

from accounts.models.recovery_code import RecoveryCode
from accounts.models.totp_device import UserTOTPDevice
from accounts.services.recovery_service import get_remaining_count
from accounts.services.totp_service import (
    confirm_totp_device,
    create_totp_device,
    disable_totp,
    verify_totp_code,
)


@pytest.mark.django_db
class TestTOTPSetup:
    def test_totp_setup_returns_qr_and_secret(self, auth_client, user):
        """Setup endpoint returns QR code and base32 secret."""
        response = auth_client.post("/accounts/2fa/totp/setup/")
        assert response.status_code == 200
        data = response.json()
        assert "qr_code" in data
        assert "secret" in data
        assert "provisioning_uri" in data
        assert data["qr_code"].startswith("data:image/png;base64,")
        assert len(data["secret"]) == 32  # base32 secret length

    def test_totp_setup_rejects_unknown_request_fields(self, auth_client, user):
        response = auth_client.post(
            "/accounts/2fa/totp/setup/",
            {"code": "123456"},
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["details"] == {"code": ["Unknown field."]}

    def test_totp_confirm_with_valid_code(self, auth_client, user):
        """Confirming with correct code activates TOTP and returns recovery codes."""
        # Setup
        response = auth_client.post("/accounts/2fa/totp/setup/")
        secret = response.json()["secret"]

        # Generate valid code
        totp = pyotp.TOTP(secret)
        code = totp.now()

        # Confirm
        response = auth_client.post("/accounts/2fa/totp/confirm/", {"code": code})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "recovery_codes" in data
        assert len(data["recovery_codes"]) == 10

        # Verify device is confirmed
        device = UserTOTPDevice.objects.get(user=user)
        assert device.confirmed is True

    def test_totp_confirm_with_invalid_code(self, auth_client, user):
        """Confirming with wrong code returns error."""
        auth_client.post("/accounts/2fa/totp/setup/")

        response = auth_client.post("/accounts/2fa/totp/confirm/", {"code": "000000"})
        assert response.status_code == 400

    def test_totp_confirm_rejects_unknown_request_fields(self, auth_client, user):
        response = auth_client.post(
            "/accounts/2fa/totp/confirm/",
            {"code": "000000", "totpCode": "000000"},
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["details"] == {"totpCode": ["Unknown field."]}

    def test_totp_confirm_replaces_unconfirmed_device(self, auth_client, user):
        """Starting setup again replaces unconfirmed device."""
        # First setup
        auth_client.post("/accounts/2fa/totp/setup/")
        first_device = UserTOTPDevice.objects.get(user=user)
        first_id = first_device.id

        # Second setup (should replace)
        auth_client.post("/accounts/2fa/totp/setup/")
        assert UserTOTPDevice.objects.filter(user=user).count() == 1
        second_device = UserTOTPDevice.objects.get(user=user)
        assert second_device.id != first_id

    def test_totp_disable_requires_valid_code(self, auth_client, user):
        """Disable requires current TOTP code."""
        # Setup and confirm TOTP
        response = auth_client.post("/accounts/2fa/totp/setup/")
        secret = response.json()["secret"]
        totp = pyotp.TOTP(secret)
        auth_client.post("/accounts/2fa/totp/confirm/", {"code": totp.now()})

        # Try disable with invalid code
        response = auth_client.delete(
            "/accounts/2fa/totp/",
            data={"code": "000000"},
            content_type="application/json",
        )
        assert response.status_code == 400

        # Disable with valid code
        response = auth_client.delete(
            "/accounts/2fa/totp/",
            data={"code": totp.now()},
            content_type="application/json",
        )
        assert response.status_code == 200
        assert not UserTOTPDevice.objects.filter(user=user).exists()

    def test_totp_disable_cleans_up_recovery_codes(self, auth_client, user):
        """If last 2FA method, recovery codes are deleted."""
        # Setup and confirm TOTP
        response = auth_client.post("/accounts/2fa/totp/setup/")
        secret = response.json()["secret"]
        totp = pyotp.TOTP(secret)
        auth_client.post("/accounts/2fa/totp/confirm/", {"code": totp.now()})

        # Verify recovery codes exist
        assert RecoveryCode.objects.filter(user=user).count() == 10

        # Disable TOTP (last 2FA method)
        auth_client.delete(
            "/accounts/2fa/totp/",
            data={"code": totp.now()},
            content_type="application/json",
        )

        # Recovery codes should be cleaned up
        assert RecoveryCode.objects.filter(user=user).count() == 0

    def test_totp_status_reflects_state(self, auth_client, user):
        """Status endpoint shows correct TOTP state."""
        # Initial state - no 2FA
        response = auth_client.get("/accounts/2fa/status/")
        assert response.status_code == 200
        data = response.json()
        assert data["two_factor_enabled"] is False
        assert data["methods"]["totp"]["enabled"] is False

        # After setup and confirm
        setup_resp = auth_client.post("/accounts/2fa/totp/setup/")
        secret = setup_resp.json()["secret"]
        totp = pyotp.TOTP(secret)
        auth_client.post("/accounts/2fa/totp/confirm/", {"code": totp.now()})

        response = auth_client.get("/accounts/2fa/status/")
        data = response.json()
        assert data["two_factor_enabled"] is True
        assert data["methods"]["totp"]["enabled"] is True
        assert data["recovery_codes_remaining"] == 10
