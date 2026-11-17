"""
Tests for the spend-summary endpoint used by the gateway to seed
budget counters on startup.
"""

import json
import secrets
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from agentcc.models import AgentccRequestLog

# Generated per run: a published literal would now be refused (agentcc/admin_token.py).
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
                # The gateway logs the key's name (its budget key) with its id.
                metadata={"auth_key_name": entry["api_key_id"]},
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


# One period of two organizations' requests and the summary the view writes
# for them, shared with the gateway's restart tests: the gateway seeds its
# budgets from this shape, so the two sides read one file.
RESTART_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "agentcc-gateway/internal/tenant/testdata/spend_summary_restart.json"
)


@pytest.mark.integration
@pytest.mark.api
class TestSpendSummaryBudgetLevels:
    """per_key by the key's name (the gateway's budget key), per_user with
    the key's owner (each user once per request), per_team and per_tag."""

    @staticmethod
    def _log(org, request_id, cost, metadata, **fields):
        return AgentccRequestLog.objects.create(
            organization=org,
            request_id=request_id,
            model=fields.pop("model", "gpt-4o"),
            cost=Decimal(str(cost)),
            started_at=timezone.now(),
            status_code=200,
            metadata=metadata,
            **fields,
        )

    def test_the_summary_is_what_the_gateway_counted(
        self, admin_client, organization, db
    ):
        from accounts.models.organization import Organization
        from agentcc.models import AgentccAPIKey

        fixture = json.loads(RESTART_FIXTURE.read_text())
        other = Organization.objects.create(name="Restart Org B")
        orgs = {"<ORG_A>": organization, "<ORG_B>": other}
        keys = {}
        for k in fixture["keys"]:
            keys[k["gateway_key_id"]] = k
            AgentccAPIKey.no_workspace_objects.create(
                organization=orgs[k["org"]],
                gateway_key_id=k["gateway_key_id"],
                name=k["name"],
                owner=k["owner"],
                key_hash=secrets.token_hex(32),
                key_prefix="sk-agentcc-restart",
            )
        for r in fixture["requests"]:
            key = keys[r["key"]]
            metadata = {}
            if r["logged_key_fields"]:
                metadata = {
                    "auth_key_id": r["key"],
                    "auth_key_name": key["name"],
                    "auth_key_owner": key["owner"],
                }
            if r["team"]:
                metadata["key_team"] = r["team"]
            for name, value in r["tags"].items():
                metadata[f"tag:{name}"] = value
            self._log(
                orgs[r["org"]], r["request_id"], r["cost"], metadata,
                model=r["model"], api_key_id=r["key"], user_id=r["user"],
            )

        response = admin_client.get("/agentcc/spend-summary/?period=monthly")
        assert response.status_code == status.HTTP_200_OK
        got = response.json()["result"]["orgs"]
        for placeholder, expected in fixture["summary"]["orgs"].items():
            org = got[str(orgs[placeholder].id)]
            for field, value in expected.items():
                assert org[field] == pytest.approx(value, abs=0.001), (placeholder, field)

    def test_a_request_user_who_owns_the_key_is_counted_once(
        self, admin_client, organization, db
    ):
        self._log(organization, "own-1", 2, {"auth_key_name": "k", "auth_key_owner": "alice"}, user_id="alice")
        self._log(organization, "own-2", 3, {"auth_key_name": "k", "auth_key_owner": "alice"}, user_id="bob")
        org = admin_client.get("/agentcc/spend-summary/").json()["result"]["orgs"][str(organization.id)]
        assert org["per_user"] == {
            "alice": pytest.approx(5.0, abs=0.001),
            "bob": pytest.approx(3.0, abs=0.001),
        }

    def test_logs_without_a_team_or_tag_add_none(self, admin_client, request_logs, organization):
        org = admin_client.get("/agentcc/spend-summary/").json()["result"]["orgs"][
            str(organization.id)
        ]
        assert org["per_team"] == {}
        assert org["per_tag"] == {}

    def test_a_log_whose_metadata_is_not_an_object_counts_only_its_columns(
        self, admin_client, organization, db
    ):
        self._log(organization, "t-list", 2, ["not", "an", "object"], user_id="alice")
        self._log(organization, "t-tag", 1, {"tag:project": "atlas", "rbac_team": "ops"})
        org = admin_client.get("/agentcc/spend-summary/").json()["result"]["orgs"][
            str(organization.id)
        ]
        assert org["total_spend"] == pytest.approx(3.0, abs=0.001)
        assert org["per_user"] == {"alice": pytest.approx(2.0, abs=0.001)}
        assert org["per_tag"] == {"project:atlas": pytest.approx(1.0, abs=0.001)}
        assert org["per_team"] == {"ops": pytest.approx(1.0, abs=0.001)}

    def test_a_platform_funded_key_counts_in_no_level(self, admin_client, organization, db):
        # A key the platform pays for itself (billing: platform_funded, logged
        # as key_billing): the gateway's budgets never count its calls, so the
        # summary they are seeded from leaves them out of every level, even
        # under a name, user, team or tag that other calls count under.
        counted = {
            "auth_key_name": "svc",
            "auth_key_owner": "alice",
            "key_team": "eng",
            "tag:project": "atlas",
        }
        self._log(organization, "pf-counted", 2, counted, user_id="alice")
        self._log(
            organization, "pf-same-names", 7,
            {**counted, "key_billing": "platform_funded"}, user_id="alice",
        )
        self._log(
            organization, "pf-own-names", 5,
            {
                "auth_key_name": "hosted-run",
                "auth_key_owner": "platform",
                "key_billing": "platform_funded",
                "rbac_team": "ops",
                "tag:run": "r1",
            },
            user_id="bob", model="claude-3",
        )
        org = admin_client.get("/agentcc/spend-summary/").json()["result"]["orgs"][
            str(organization.id)
        ]
        assert org["total_spend"] == pytest.approx(2.0, abs=0.001)
        assert org["per_key"] == {"svc": pytest.approx(2.0, abs=0.001)}
        assert org["per_user"] == {"alice": pytest.approx(2.0, abs=0.001)}
        assert org["per_model"] == {"gpt-4o": pytest.approx(2.0, abs=0.001)}
        assert org["per_team"] == {"eng": pytest.approx(2.0, abs=0.001)}
        assert org["per_tag"] == {"project:atlas": pytest.approx(2.0, abs=0.001)}

    def test_an_org_whose_only_spend_is_platform_funded_is_not_listed(
        self, admin_client, organization, db
    ):
        self._log(
            organization, "pf-only", 4,
            {"auth_key_name": "hosted-run", "key_billing": "platform_funded"},
        )
        assert admin_client.get("/agentcc/spend-summary/").json()["result"]["orgs"] == {}

    def test_old_spend_is_outside_the_period(self, admin_client, organization, db):
        from datetime import timedelta

        log = self._log(
            organization, "t-old", 5,
            {"auth_key_name": "k", "key_team": "eng", "tag:project": "atlas"},
        )
        AgentccRequestLog.objects.filter(pk=log.pk).update(
            started_at=timezone.now() - timedelta(days=60)
        )
        response = admin_client.get("/agentcc/spend-summary/?period=daily")
        assert response.json()["result"]["orgs"] == {}
