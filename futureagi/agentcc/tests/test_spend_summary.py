"""
Tests for the spend-summary endpoint used by the gateway to seed
budget counters on startup.
"""

import secrets
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from agentcc.models import AgentccRequestLog

# Generated per run: a published literal is refused (agentcc/admin_token.py).
ADMIN_TOKEN = secrets.token_hex(32)


@pytest.fixture(autouse=True)
def set_agentcc_admin_token():
    """Ensure AGENTCC_ADMIN_TOKEN is set for permission checks."""
    with (
        patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN),
        patch("agentcc.services.gateway_client.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN),
    ):
        yield


@pytest.fixture
def admin_client():
    """API client authenticated with the gateway admin token."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
    return client


@pytest.fixture
def now():
    return timezone.now()


@pytest.fixture
def request_logs(organization, workspace, now):
    """Create a set of request logs with known costs."""
    logs = []
    base = [
        {
            "model": "gpt-4o",
            "api_key_id": "key-1",
            "user_id": "alice",
            "cost": Decimal("10.50"),
        },
        {
            "model": "gpt-4o",
            "api_key_id": "key-1",
            "user_id": "alice",
            "cost": Decimal("5.25"),
        },
        {
            "model": "claude-3",
            "api_key_id": "key-2",
            "user_id": "bob",
            "cost": Decimal("20.00"),
        },
        {
            "model": "gpt-4o",
            "api_key_id": "key-2",
            "user_id": "bob",
            "cost": Decimal("4.25"),
        },
    ]
    for entry in base:
        logs.append(
            AgentccRequestLog.objects.create(
                organization=organization,
                workspace=workspace,
                request_id=f"req-{len(logs)}",
                started_at=now,
                status_code=200,
                **entry,
            )
        )
    return logs


@pytest.mark.integration
@pytest.mark.api
class TestSpendSummary:
    """Tests for GET /agentcc/spend-summary/."""

    def test_unauthenticated_returns_403(self, db):
        client = APIClient()
        response = client.get("/agentcc/spend-summary/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_wrong_token_returns_403(self, db):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer wrong-token")
        response = client.get("/agentcc/spend-summary/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_empty_response(self, admin_client, db):
        response = admin_client.get("/agentcc/spend-summary/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        assert data["result"]["orgs"] == {}

    def test_total_spend(self, admin_client, request_logs, organization):
        response = admin_client.get("/agentcc/spend-summary/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        org_data = data["result"]["orgs"][str(organization.id)]
        assert org_data["total_spend"] == pytest.approx(40.0, abs=0.01)

    def test_per_key_spend(self, admin_client, request_logs, organization):
        response = admin_client.get("/agentcc/spend-summary/")
        data = response.json()
        per_key = data["result"]["orgs"][str(organization.id)]["per_key"]
        assert per_key["key-1"] == pytest.approx(15.75, abs=0.01)
        assert per_key["key-2"] == pytest.approx(24.25, abs=0.01)

    def test_per_user_spend(self, admin_client, request_logs, organization):
        response = admin_client.get("/agentcc/spend-summary/")
        data = response.json()
        per_user = data["result"]["orgs"][str(organization.id)]["per_user"]
        assert per_user["alice"] == pytest.approx(15.75, abs=0.01)
        assert per_user["bob"] == pytest.approx(24.25, abs=0.01)

    def test_per_model_spend(self, admin_client, request_logs, organization):
        response = admin_client.get("/agentcc/spend-summary/")
        data = response.json()
        per_model = data["result"]["orgs"][str(organization.id)]["per_model"]
        assert per_model["gpt-4o"] == pytest.approx(20.0, abs=0.01)
        assert per_model["claude-3"] == pytest.approx(20.0, abs=0.01)

    def test_period_param(self, admin_client, request_logs):
        for period in ["daily", "weekly", "monthly", "total"]:
            response = admin_client.get(f"/agentcc/spend-summary/?period={period}")
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["result"]["period"] == period
            assert "period_start" in data["result"]

    def test_default_period_is_monthly(self, admin_client, request_logs):
        response = admin_client.get("/agentcc/spend-summary/")
        data = response.json()
        assert data["result"]["period"] == "monthly"

    def test_multi_org_isolation(self, admin_client, request_logs, organization, db):
        """Logs from different orgs are separated."""
        from accounts.models.organization import Organization

        org2 = Organization.objects.create(name="Other Org")
        AgentccRequestLog.objects.create(
            organization=org2,
            request_id="req-other",
            model="gpt-4o",
            cost=Decimal("99.00"),
            started_at=timezone.now(),
            status_code=200,
        )

        response = admin_client.get("/agentcc/spend-summary/")
        data = response.json()
        orgs = data["result"]["orgs"]

        assert orgs[str(organization.id)]["total_spend"] == pytest.approx(
            40.0, abs=0.01
        )
        assert orgs[str(org2.id)]["total_spend"] == pytest.approx(99.0, abs=0.01)

    def test_zero_cost_logs_excluded(
        self, admin_client, organization, workspace, now, db
    ):
        """Logs with zero cost should not appear in the summary."""
        AgentccRequestLog.objects.create(
            organization=organization,
            workspace=workspace,
            request_id="req-free",
            model="gpt-4o",
            cost=Decimal("0"),
            started_at=now,
            status_code=200,
        )
        response = admin_client.get("/agentcc/spend-summary/")
        data = response.json()
        assert data["result"]["orgs"] == {}

    def test_old_logs_excluded_by_period(
        self, admin_client, organization, workspace, db
    ):
        """Logs outside the current period are not counted."""
        from datetime import timedelta

        old_time = timezone.now() - timedelta(days=60)
        AgentccRequestLog.objects.create(
            organization=organization,
            workspace=workspace,
            request_id="req-old",
            model="gpt-4o",
            cost=Decimal("100.00"),
            started_at=old_time,
            status_code=200,
        )
        # daily period should exclude 60-day-old logs
        response = admin_client.get("/agentcc/spend-summary/?period=daily")
        data = response.json()
        assert data["result"]["orgs"] == {}
