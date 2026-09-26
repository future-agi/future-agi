import pyotp
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from accounts.services.totp_service import confirm_totp_device, create_totp_device
from accounts.services.two_factor_challenge import create_challenge, validate_challenge


@pytest.mark.django_db
class TestLoginWithout2FA:
    def test_login_without_2fa_unchanged(self, user):
        """Users without 2FA get tokens directly."""
        client = APIClient()
        response = client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert "access" in data
        assert "refresh" in data
        assert "requires_two_factor" not in data


@pytest.mark.django_db
class TestLoginWith2FA:
    def _setup_totp_for_user(self, user):
        """Helper: set up and confirm TOTP for a user, return secret."""
        device, uri, secret = create_totp_device(user)
        totp = pyotp.TOTP(secret)
        confirm_totp_device(user, totp.now())
        return secret

    def test_login_with_2fa_returns_challenge(self, user):
        """Users with 2FA get challenge token, not JWT."""
        self._setup_totp_for_user(user)

        client = APIClient()
        response = client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["requires_two_factor"] is True
        assert "challenge_token" in data
        assert "totp" in data["methods"]
        assert "recovery" in data["methods"]
        # Should NOT contain JWT tokens
        assert "access" not in data

    def test_2fa_challenge_expires(self, user):
        """Challenge token expires after TTL."""
        methods = ["totp", "recovery"]
        challenge_id = create_challenge(user, methods)

        # Delete from cache to simulate expiry
        cache.delete(f"2fa_challenge:{challenge_id}")

        result = validate_challenge(challenge_id)
        assert result is None

    def test_2fa_challenge_single_use(self, user):
        """Challenge token can't be reused after consumption."""
        secret = self._setup_totp_for_user(user)

        client = APIClient()
        login_resp = client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        challenge_token = login_resp.json()["challenge_token"]

        # First verification should succeed
        totp = pyotp.TOTP(secret)
        verify_resp = client.post(
            "/accounts/2fa/verify/totp/",
            {"challenge_token": challenge_token, "code": totp.now()},
            format="json",
        )
        assert verify_resp.status_code == 200

        # Second attempt with same challenge should fail
        verify_resp2 = client.post(
            "/accounts/2fa/verify/totp/",
            {"challenge_token": challenge_token, "code": totp.now()},
            format="json",
        )
        assert verify_resp2.status_code == 400

    def test_2fa_challenge_rate_limit(self, user):
        """Max 5 attempts per challenge."""
        methods = ["totp", "recovery"]
        challenge_id = create_challenge(user, methods)

        # Use up all attempts
        for _ in range(5):
            validate_challenge(challenge_id)

        # 6th attempt should return None (rate limited)
        result = validate_challenge(challenge_id)
        assert result is None

    def test_full_login_with_totp(self, user):
        """Full login flow: email+password -> challenge -> TOTP -> tokens."""
        secret = self._setup_totp_for_user(user)

        client = APIClient()

        # Phase 1: credentials
        login_resp = client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        assert login_resp.status_code == 200
        challenge_token = login_resp.json()["challenge_token"]

        # Phase 2: TOTP verification
        totp = pyotp.TOTP(secret)
        verify_resp = client.post(
            "/accounts/2fa/verify/totp/",
            {"challenge_token": challenge_token, "code": totp.now()},
            format="json",
        )
        assert verify_resp.status_code == 200
        tokens = verify_resp.json()
        assert "access" in tokens
        assert "refresh" in tokens

    def test_full_login_with_recovery(self, user, auth_client):
        """Full login flow: email+password -> challenge -> recovery -> tokens."""
        # Setup TOTP via API to get recovery codes
        setup_resp = auth_client.post("/accounts/2fa/totp/setup/")
        secret = setup_resp.json()["secret"]
        totp = pyotp.TOTP(secret)
        confirm_resp = auth_client.post(
            "/accounts/2fa/totp/confirm/", {"code": totp.now()}
        )
        recovery_codes = confirm_resp.json()["recovery_codes"]

        # Login
        client = APIClient()
        login_resp = client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        challenge_token = login_resp.json()["challenge_token"]

        # Verify with recovery code
        verify_resp = client.post(
            "/accounts/2fa/verify/recovery/",
            {"challenge_token": challenge_token, "code": recovery_codes[0]},
            format="json",
        )
        assert verify_resp.status_code == 200
        assert "access" in verify_resp.json()

    def test_verify_totp_invalid_code(self, user):
        """Wrong TOTP code during login verify is rejected."""
        self._setup_totp_for_user(user)
        client = APIClient()
        login_resp = client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        assert login_resp.status_code == 200
        challenge_token = login_resp.json()["challenge_token"]

        verify_resp = client.post(
            "/accounts/2fa/verify/totp/",
            {"challenge_token": challenge_token, "code": "000001"},
            format="json",
        )
        assert verify_resp.status_code == 400
        body = verify_resp.json()
        assert body["status"] is False
        assert body["code"] == "invalid"
        assert body["message"] == "Invalid code. Please try again."
        assert "access" not in body

    def test_verify_totp_missing_challenge(self, user):
        """Missing/invalid challenge token is rejected."""
        self._setup_totp_for_user(user)
        client = APIClient()
        verify_resp = client.post(
            "/accounts/2fa/verify/totp/",
            {
                "challenge_token": "00000000-0000-0000-0000-000000000000",
                "code": "123456",
            },
            format="json",
        )
        assert verify_resp.status_code == 400
        body = verify_resp.json()
        assert body["status"] is False
        assert body["code"] == "invalid"
        assert body["message"] == "Invalid or expired verification session."
        assert "access" not in body

    def test_verify_recovery_invalid_code(self, user, auth_client):
        """Wrong recovery code during login verify is rejected."""
        setup_resp = auth_client.post("/accounts/2fa/totp/setup/")
        secret = setup_resp.json()["secret"]
        totp = pyotp.TOTP(secret)
        auth_client.post("/accounts/2fa/totp/confirm/", {"code": totp.now()})

        client = APIClient()
        login_resp = client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        challenge_token = login_resp.json()["challenge_token"]
        verify_resp = client.post(
            "/accounts/2fa/verify/recovery/",
            {"challenge_token": challenge_token, "code": "WRONGCODE1"},
            format="json",
        )
        assert verify_resp.status_code == 400
        body = verify_resp.json()
        assert "access" not in body
        assert body["status"] is False
        assert body["code"] == "invalid"
        assert body["message"] == "Invalid or already used recovery code."
