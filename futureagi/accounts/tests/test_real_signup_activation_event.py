"""Real DB-backed signup proof (wave-1 design doc 474, section B — Layer A).

This is the authoritative CI gate for the onboarding activation spine. Unlike
the local onboarding API stub (which hands back ``mock-*`` ids and persists
nothing), this test drives the real DRF ``/accounts/signup`` endpoint against
the managed test DB and asserts the verified spine:

* signup creates a real User + Organization + OrganizationMembership with real
  ``uuid4`` primary keys (never ``mock-*`` ids), and persists ZERO
  ``OnboardingActivationEvent`` rows;
* the first activation row is created later by an authenticated POST to
  ``/accounts/activation-events/`` — the default Workspace is created lazily by
  the auth layer on that first authenticated request, not by signup.

Auth runs for real here (a ``Bearer`` access token from ``/accounts/token/``,
not ``force_authenticate``) so the lazy-workspace creation is genuinely
exercised by the auth layer rather than hand-stitched by the test.
"""

import uuid

import pytest
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import OnboardingActivationEvent, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace

# Business email so signup's work-email gate passes (ALLOW_ANY_EMAIL defaults
# to "false" in test settings); mirrors the @futureagi.com addresses used by
# the existing signup suite.
SIGNUP_EMAIL = "real-signup-activation@futureagi.com"
SIGNUP_PASSWORD = "SecurePass123!"


def _assert_real_uuid(value):
    """A real persisted PK parses as a uuid4 and is not a stub ``mock-*`` id."""
    text = str(value)
    assert not text.startswith("mock-"), text
    assert uuid.UUID(text)


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS={"onboarding_activation_state_api": True})
def test_real_signup_persists_first_activation_event_via_auth_layer(api_client):
    # --- 1. Drive the real DRF signup endpoint -----------------------------
    signup = api_client.post(
        "/accounts/signup/",
        {
            "email": SIGNUP_EMAIL,
            "full_name": "Real Signup User",
            "password": SIGNUP_PASSWORD,
            "allow_email": True,
        },
        format="json",
    )

    assert signup.status_code == status.HTTP_200_OK
    assert signup.json()["result"]["message"] == "User Created Successfully"

    # --- 2. Signup created a real User + Organization + OrganizationMembership
    # with real uuid4 PKs, and persisted ZERO activation events. ------------
    user = User.objects.get(email=SIGNUP_EMAIL)
    organization = user.organization
    assert organization is not None
    membership = OrganizationMembership.no_workspace_objects.get(
        user=user, organization=organization, is_active=True
    )

    _assert_real_uuid(user.id)
    _assert_real_uuid(organization.id)
    _assert_real_uuid(membership.id)

    # The verified spine: signup persists no workspace and no activation event.
    assert Workspace.no_workspace_objects.filter(organization=organization).count() == 0
    assert OnboardingActivationEvent.no_workspace_objects.count() == 0

    # --- 3. Authenticate as the new user with a real access token ----------
    login = api_client.post(
        "/accounts/token/",
        {
            "email": SIGNUP_EMAIL,
            "password": SIGNUP_PASSWORD,
            "remember_me": True,
            "recaptcha_response": "",
        },
        format="json",
    )
    assert login.status_code == status.HTTP_200_OK
    access_token = login.json()["access"]
    assert access_token

    # --- 4. Record the first activation event. ``onboarding_home_viewed`` is
    # a registered, non-guarded event (only first_quality_loop_completed on the
    # observe path carries the product-evidence guard), so the row persists. -
    authed_client = APIClient()
    event_response = authed_client.post(
        "/accounts/activation-events/",
        {"event_name": "onboarding_home_viewed", "primary_path": "observe"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )

    assert event_response.status_code == status.HTTP_200_OK
    result = event_response.json()["result"]
    assert result["event_name"] == "onboarding_home_viewed"

    # The auth layer lazily created the default Workspace on this first
    # authenticated request (signup created none).
    workspace = Workspace.no_workspace_objects.get(
        organization=organization, is_default=True, is_active=True
    )
    _assert_real_uuid(workspace.id)
    assert str(workspace.id) == result["activation_state"]["workspace_id"]

    # --- 5. Exactly one activation event is now persisted, scoped to the real
    # signup org / workspace / user (real ids, not stub ids). ---------------
    events = OnboardingActivationEvent.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        user=user,
        event_name="onboarding_home_viewed",
    )
    assert events.count() == 1
    assert OnboardingActivationEvent.no_workspace_objects.count() == 1

    event = events.get()
    _assert_real_uuid(event.id)
    assert event.user_id == user.id
    assert event.organization_id == organization.id
    assert event.workspace_id == workspace.id
    assert event.is_sample is False
