"""The Budgets page's bars read each level's spend for its period from
GET /agentcc/spend-summary/.

The gateway reads that endpoint with the admin token (every org, to seed its
budget counters). A console user reads it with their own session and gets their
organization's spend only, in the same shape.
"""

import secrets
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from agentcc.models import AgentccRequestLog

URL = "/agentcc/spend-summary/"
ADMIN_TOKEN = secrets.token_hex(32)
SESSION_TOKEN = "budget-console-session"


@pytest.fixture(autouse=True)
def _admin_token():
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
        yield


@pytest.fixture
def spend(organization, workspace):
    """$12.50 this month in the user's org (alice and a key), $99 in another
    org, and $7 two months ago in the user's org."""
    other = Organization.objects.create(name="Budget Other Org")
    now = timezone.now()
    rows = [
        (organization, "alice", "key-a", Decimal("10.00"), now),
        (organization, "bob", "key-a", Decimal("2.50"), now),
        (other, "mallory", "key-z", Decimal("99.00"), now),
        (organization, "alice", "key-a", Decimal("7.00"), now - timedelta(days=62)),
    ]
    for i, (org, user_id, key, cost, at) in enumerate(rows):
        AgentccRequestLog.objects.create(
            organization=org,
            workspace=workspace if org == organization else None,
            request_id=f"budget-spend-{i}",
            model="gpt-4o",
            user_id=user_id,
            api_key_id=key,
            cost=cost,
            started_at=at,
            status_code=200,
        )
    return other


@pytest.mark.integration
def test_budget_console_user_reads_own_org_period_spend(
    auth_client, organization, spend
):
    response = auth_client.get(URL, {"period": "monthly"})
    assert response.status_code == status.HTTP_200_OK, response.content
    orgs = response.json()["result"]["orgs"]
    assert list(orgs) == [str(organization.id)]
    mine = orgs[str(organization.id)]
    assert mine["total_spend"] == pytest.approx(12.5)
    assert mine["per_user"] == {"alice": pytest.approx(10.0), "bob": pytest.approx(2.5)}
    assert mine["per_key"] == {"key-a": pytest.approx(12.5)}


@pytest.mark.integration
def test_budget_console_user_total_period_counts_older_spend(
    auth_client, organization, spend
):
    response = auth_client.get(URL, {"period": "total"})
    assert response.status_code == status.HTTP_200_OK, response.content
    mine = response.json()["result"]["orgs"][str(organization.id)]
    assert mine["total_spend"] == pytest.approx(19.5)


@pytest.mark.integration
def test_budget_console_session_through_the_real_authenticator(
    user, organization, spend
):
    """No force_authenticate: the session token goes through the view's own
    authenticator (decode_token stubbed for the fixture token only)."""
    seen = []

    def fake_decode(token, **_):
        seen.append(token)
        if token != SESSION_TOKEN:
            raise ValueError("not the fixture token")
        return user, token, None

    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {SESSION_TOKEN}",
        HTTP_X_ORGANIZATION_ID=str(organization.id),
    )
    with patch("accounts.authentication.decode_token", side_effect=fake_decode):
        response = client.get(URL)
    assert response.status_code == status.HTTP_200_OK, response.content
    assert list(response.json()["result"]["orgs"]) == [str(organization.id)]
    assert seen == [SESSION_TOKEN]


@pytest.mark.integration
def test_budget_admin_token_reads_every_org_and_is_never_decoded(organization, spend):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
    with patch("accounts.authentication.decode_token") as decode:
        response = client.get(URL)
    assert response.status_code == status.HTTP_200_OK, response.content
    orgs = response.json()["result"]["orgs"]
    assert set(orgs) == {str(organization.id), str(spend.id)}
    assert orgs[str(spend.id)]["total_spend"] == pytest.approx(99.0)
    decode.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "bearer",
    [None, "wrong-token", secrets.token_hex(32)],
    # Fixed ids: the random token differs per process, and pytest-xdist
    # refuses to run when its workers collect different test ids.
    ids=["no-bearer", "wrong-token", "random-token"],
)
def test_budget_no_session_and_no_admin_token_is_403(db, bearer):
    client = APIClient()
    if bearer:
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {bearer}")
    response = client.get(URL)
    assert response.status_code == status.HTTP_403_FORBIDDEN
