"""A Marketplace subscription identifies a customer organization, not a person."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.test import RequestFactory

from accounts import aws_marketplace_utils as utils
from accounts.views import aws_marketplace as views


@pytest.fixture
def launch_customer(monkeypatch):
    members = MagicMock()
    members.exists.return_value = True
    organization = SimpleNamespace(
        id="FAKE-subscribed-organization", members=members, is_new=False
    )
    # Offboarding retains this legacy FK and role on the globally active person.
    # Their live membership and saved selection can belong to a different tenant.
    former_owner = SimpleNamespace(
        id="FAKE-former-owner",
        is_active=True,
        organization=organization,
        organization_role=utils.OrganizationRoles.OWNER,
        config={"currentOrganizationId": "FAKE-unrelated-organization"},
    )
    members.filter.return_value.first.return_value = former_owner
    members.first.return_value = former_owner
    customer = SimpleNamespace(
        organization=organization,
        subscription_status=utils.SubscriptionStatus.SUBSCRIBE_SUCCESS,
    )
    service = MagicMock()
    service.resolve_customer_token.return_value = (
        "FAKE-customer", "FAKE-account", "FAKE-product"
    )
    monkeypatch.setattr(utils, "aws_marketplace_service", service)
    customer_lookup = MagicMock(return_value=customer)
    monkeypatch.setattr(utils.AWSMarketplaceCustomer.objects, "get", customer_lookup)

    # Exercise the real launch/token helpers but keep all I/O in memory. The
    # spies record any session revocation or issuance for the assertions below.
    sessions = MagicMock()
    sessions.create.return_value.id = "FAKE-session"
    monkeypatch.setattr(utils.AuthToken, "objects", sessions)
    monkeypatch.setattr(
        utils, "generate_encrypted_message", MagicMock(return_value="FAKE-session-value")
    )
    token_cache = MagicMock()
    access_cache = MagicMock()
    monkeypatch.setattr(utils, "cache", token_cache)
    monkeypatch.setattr(utils, "cache_access_token", access_cache)
    handoff = MagicMock(return_value="FAKE-handoff")
    monkeypatch.setattr(views, "issue", handoff)

    return SimpleNamespace(
        customer=customer,
        members=members,
        service=service,
        sessions=sessions,
        token_cache=token_cache,
        access_cache=access_cache,
        handoff=handoff,
    )


def _launch_request():
    return RequestFactory().post(
        "/accounts/aws-marketplace/launch-software/",
        "x-amzn-marketplace-token=FAKE-registration",
        content_type="application/x-www-form-urlencoded",
    )


def _assert_no_person_session(launch_customer):
    launch_customer.members.filter.assert_not_called()
    launch_customer.members.first.assert_not_called()
    launch_customer.sessions.create.assert_not_called()
    launch_customer.sessions.filter.assert_not_called()
    launch_customer.token_cache.set.assert_not_called()
    launch_customer.access_cache.assert_not_called()
    launch_customer.handoff.assert_not_called()


@pytest.mark.parametrize(
    "subscription_status",
    [
        utils.SubscriptionStatus.SUBSCRIBE_SUCCESS,
        utils.SubscriptionStatus.SUBSCRIBE_PENDING,
    ],
)
def test_launch_subscription_does_not_authenticate_former_owner(
    launch_customer, subscription_status
):
    launch_customer.customer.subscription_status = subscription_status

    data, error = utils.process_aws_launch_software("FAKE-registration")

    assert error is None
    assert data == {"valid": True, "action": "login"}
    launch_customer.service.resolve_customer_token.assert_called_once_with(
        "FAKE-registration"
    )
    _assert_no_person_session(launch_customer)


def test_launch_redirect_requires_real_login_without_session_handoff(launch_customer):
    # The actual helper feeds the view: no mocked response can conceal issuance.
    response = views.aws_marketplace_launch_software(_launch_request())

    assert response.status_code == 302
    assert response["Location"] == (
        f"{views.APP_URL}/auth/jwt/login?returnTo=%2Fdashboard%2Fdevelop"
    )
    assert response["Cache-Control"] == "no-store"
    assert response["Referrer-Policy"] == "no-referrer"
    _assert_no_person_session(launch_customer)


def test_launch_login_action_does_not_issue_an_empty_session_handoff(
    launch_customer, monkeypatch
):
    monkeypatch.setattr(
        views,
        "process_aws_launch_software",
        MagicMock(return_value=({"valid": True, "action": "login"}, None)),
    )

    response = views.aws_marketplace_launch_software(_launch_request())

    assert response.status_code == 302
    assert response["Location"] == (
        f"{views.APP_URL}/auth/jwt/login?returnTo=%2Fdashboard%2Fdevelop"
    )
    _assert_no_person_session(launch_customer)


def test_launch_without_users_preserves_onboarding(launch_customer, monkeypatch):
    launch_customer.members.exists.return_value = False
    onboarding = MagicMock(return_value="FAKE-onboarding")
    monkeypatch.setattr(utils, "_create_onboarding_token_for_launch", onboarding)

    data, error = utils.process_aws_launch_software("FAKE-registration")

    assert error is None
    assert data == {
        "valid": True,
        "action": "signup",
        "onboarding_token": "FAKE-onboarding",
    }
    onboarding.assert_called_once_with(
        "FAKE-customer", launch_customer.customer, "FAKE-product"
    )
    _assert_no_person_session(launch_customer)


def test_launch_inactive_subscription_still_refused(launch_customer):
    launch_customer.customer.subscription_status = (
        utils.SubscriptionStatus.UNSUBSCRIBE_SUCCESS
    )

    data, error = utils.process_aws_launch_software("FAKE-registration")

    assert data is None
    assert error == "Your subscription is not active"
    _assert_no_person_session(launch_customer)


def test_launch_invalid_registration_still_refused(launch_customer):
    launch_customer.service.resolve_customer_token.side_effect = ValueError(
        "FAKE-invalid-registration"
    )

    response = views.aws_marketplace_launch_software(_launch_request())

    assert response.status_code == 400
    assert "FAKE-invalid-registration" not in str(response.data)
    _assert_no_person_session(launch_customer)
