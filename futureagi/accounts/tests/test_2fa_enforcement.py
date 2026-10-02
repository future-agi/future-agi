from datetime import timedelta

import pyotp
import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import timezone

from accounts.middleware.require_2fa import Require2FAMiddleware
from accounts.services.totp_service import confirm_totp_device, create_totp_device


@pytest.mark.django_db
class TestOrgTwoFactorEnforcement:
    def test_org_2fa_policy_default_off(self, auth_client):
        """Org doesn't require 2FA by default."""
        response = auth_client.get("/accounts/organization/2fa-policy/")
        assert response.status_code == 200
        data = response.json()
        assert data["require_2fa"] is False

    def test_enable_2fa_policy(self, auth_client, user, organization):
        """Admin with 2FA enabled can enable 2FA requirement."""
        from accounts.models.user import User

        # Admin must have 2FA on their own account first
        device, _, secret = create_totp_device(user)
        totp = pyotp.TOTP(secret)
        confirm_totp_device(user, totp.now())

        # Re-fetch user so the cached reverse OneToOneField (totp_device)
        # reflects the confirmed=True state set by confirm_totp_device.
        fresh_user = User.objects.get(pk=user.pk)
        auth_client.force_authenticate(user=fresh_user)

        response = auth_client.put(
            "/accounts/organization/2fa-policy/",
            {"require_2fa": True, "require_2fa_grace_period_days": 14},
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["require_2fa"] is True
        assert data["require_2fa_grace_period_days"] == 14
        assert data["require_2fa_enforced_at"] is not None

    def test_enable_2fa_policy_requires_actor_2fa_envelope(self, auth_client):
        response = auth_client.put(
            "/accounts/organization/2fa-policy/",
            {"require_2fa": True, "require_2fa_grace_period_days": 14},
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert data["status"] is False
        assert data["type"] == "validation_error"
        assert data["code"] == "invalid"
        assert (
            data["detail"]
            == "You must enable two-factor authentication on your own account before requiring it for the organization."
        )
        assert data["message"] == data["detail"]
        assert data["error"] == data["detail"]
        assert data["result"] == data["detail"]

    def test_2fa_policy_rejects_camel_case_request_fields(self, auth_client):
        response = auth_client.put(
            "/accounts/organization/2fa-policy/",
            {"require2fa": True, "gracePeriodDays": 14},
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["details"] == {
            "require_2fa": ["This field is required."],
            "gracePeriodDays": ["Unknown field."],
            "require2fa": ["Unknown field."],
        }

    def test_2fa_policy_rejects_out_of_range_grace_period_before_state_change(
        self, auth_client, organization
    ):
        organization.require_2fa = False
        organization.require_2fa_grace_period_days = 7
        organization.require_2fa_enforced_at = None
        organization.save()

        response = auth_client.put(
            "/accounts/organization/2fa-policy/",
            {"require_2fa": False, "require_2fa_grace_period_days": 31},
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "require_2fa_grace_period_days" in response.json()["details"]
        organization.refresh_from_db()
        assert organization.require_2fa is False
        assert organization.require_2fa_grace_period_days == 7
        assert organization.require_2fa_enforced_at is None

    def test_disable_2fa_policy_clears_enforcement_timestamp(
        self, auth_client, organization
    ):
        organization.require_2fa = True
        organization.require_2fa_grace_period_days = 14
        organization.require_2fa_enforced_at = timezone.now() - timedelta(days=1)
        organization.save()

        response = auth_client.put(
            "/accounts/organization/2fa-policy/",
            {"require_2fa": False, "require_2fa_grace_period_days": 6},
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["require_2fa"] is False
        assert data["require_2fa_grace_period_days"] == 6
        assert data["require_2fa_enforced_at"] is None
        organization.refresh_from_db()
        assert organization.require_2fa is False
        assert organization.require_2fa_grace_period_days == 6
        assert organization.require_2fa_enforced_at is None

    def test_2fa_policy_grace_period(self, user, organization):
        """Access allowed during grace period with headers."""
        organization.require_2fa = True
        organization.require_2fa_enforced_at = timezone.now()
        organization.require_2fa_grace_period_days = 7
        organization.save()

        factory = RequestFactory()
        request = factory.get("/accounts/first-checks/")
        request.user = user
        request.organization = organization

        dummy_response = HttpResponse("OK")
        middleware = Require2FAMiddleware(lambda req: dummy_response)
        response = middleware(request)

        # Within grace period — allowed but with header
        assert response.status_code == 200
        assert response.get("X-2FA-Required") == "grace-period"

    def test_2fa_policy_blocks_after_grace(self, user, organization):
        """Access restricted after grace period."""
        organization.require_2fa = True
        organization.require_2fa_enforced_at = timezone.now() - timedelta(days=30)
        organization.require_2fa_grace_period_days = 7
        organization.save()

        factory = RequestFactory()
        request = factory.get("/accounts/first-checks/")
        request.user = user
        request.organization = organization

        middleware = Require2FAMiddleware(lambda req: HttpResponse("OK"))
        response = middleware(request)

        assert response.status_code == 403
        import json

        data = json.loads(response.content)
        assert data["code"] == "2fa_required"

    def test_2fa_compliant_user_not_blocked(self, user, organization):
        """Users with 2FA are never blocked."""
        # Setup 2FA for user
        device, uri, secret = create_totp_device(user)
        totp = pyotp.TOTP(secret)
        confirm_totp_device(user, totp.now())

        # Enable 2FA with expired grace period
        organization.require_2fa = True
        organization.require_2fa_enforced_at = timezone.now() - timedelta(days=30)
        organization.require_2fa_grace_period_days = 7
        organization.save()

        factory = RequestFactory()
        request = factory.get("/accounts/first-checks/")
        request.user = user
        request.organization = organization

        dummy_response = HttpResponse("OK")
        middleware = Require2FAMiddleware(lambda req: dummy_response)
        response = middleware(request)

        # Should NOT be blocked
        assert response.status_code == 200

    def test_2fa_setup_endpoints_always_accessible(
        self, auth_client, organization, user
    ):
        """2FA setup endpoints exempt from enforcement."""
        # Enable 2FA with expired grace period
        organization.require_2fa = True
        organization.require_2fa_enforced_at = timezone.now() - timedelta(days=30)
        organization.require_2fa_grace_period_days = 7
        organization.save()

        # 2FA setup endpoints should still work
        response = auth_client.get("/accounts/2fa/status/")
        assert response.status_code == 200

        response = auth_client.post("/accounts/2fa/totp/setup/")
        assert response.status_code == 200

    def test_member_list_shows_2fa_status(self, auth_client, user):
        """User-info includes 2FA status."""
        response = auth_client.get("/accounts/user-info/")
        assert response.status_code == 200
        data = response.json()
        assert "has_2fa_enabled" in data
        assert data["has_2fa_enabled"] is False
