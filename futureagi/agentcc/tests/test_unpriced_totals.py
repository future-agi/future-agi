"""A cost total says how many of its requests had no price.

Model comparison showed o1-mini at
"$0.00" over 1,604 tokens while Logs read its rows "No price found". The
gateway stores an unpriced request's cost as 0 with cost_source
"unpriced", so every sum over it read as free. Each cost total the console
shows now carries ``unpriced_requests``: how many of the requests it sums
had no price, so the console reads the total as unknown (all of them) or
as a floor (some), never as free. The totals themselves are unchanged.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework import status

from agentcc.models import AgentccRequestLog
from agentcc.models.session import AgentccSession

PRICED = "gpt-4o-mini"
UNPRICED = "o1-mini"
MIXED = "gpt-4o"


@pytest.fixture
def cost_logs(organization, workspace):
    """Two priced calls, two unpriced calls, and one model with one of each;
    the mixed model's calls share a session."""
    started = timezone.now() - timedelta(minutes=5)
    rows = [
        # request_id, model, cost, cost_source, session
        ("p-1", PRICED, "0.010000", "catalog", ""),
        ("p-2", PRICED, "0.020000", "provider_reported", ""),
        ("u-1", UNPRICED, "0.000000", "unpriced", ""),
        ("u-2", UNPRICED, "0.000000", "unpriced", ""),
        ("x-1", MIXED, "0.005000", "catalog", "sess-mixed"),
        ("x-2", MIXED, "0.000000", "unpriced", "sess-mixed"),
    ]
    for request_id, model, cost, source, session in rows:
        AgentccRequestLog.objects.create(
            organization=organization,
            workspace=workspace,
            request_id=request_id,
            model=model,
            provider="openai",
            status_code=200,
            latency_ms=100,
            cost=Decimal(cost),
            cost_source=source,
            input_tokens=800,
            output_tokens=2,
            total_tokens=802,
            session_id=session,
            started_at=started,
        )
    AgentccSession.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        session_id="sess-mixed",
        name="sess-mixed",
    )


def _result(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()["result"]


@pytest.mark.integration
@pytest.mark.api
class TestUnpricedTotals:
    def test_overview_total_cost_counts_its_unpriced_requests(
        self, auth_client, cost_logs
    ):
        result = _result(auth_client.get("/agentcc/analytics/overview/"))

        assert result["total_cost"]["unpriced_requests"] == 3
        assert result["total_requests"]["value"] == 6
        assert Decimal(result["total_cost"]["value"]) == Decimal("0.035")

    def test_model_comparison_rows_count_their_unpriced_requests(
        self, auth_client, cost_logs
    ):
        result = _result(auth_client.get("/agentcc/analytics/model-comparison/"))
        rows = {row["model"]: row for row in result["models"]}

        assert rows[UNPRICED]["unpriced_requests"] == 2
        assert rows[UNPRICED]["request_count"] == 2
        assert rows[MIXED]["unpriced_requests"] == 1
        assert rows[PRICED]["unpriced_requests"] == 0

    def test_cost_breakdown_rows_and_total_count_their_unpriced_requests(
        self, auth_client, cost_logs
    ):
        result = _result(
            auth_client.get("/agentcc/analytics/cost-breakdown/?group_by=model")
        )
        rows = {row["name"]: row for row in result["breakdown"]}

        assert result["unpriced_requests"] == 3
        assert rows[UNPRICED]["unpriced_requests"] == 2
        assert rows[MIXED]["unpriced_requests"] == 1
        assert rows[PRICED]["unpriced_requests"] == 0

    def test_cost_breakdown_other_group_sums_its_unpriced_requests(
        self, auth_client, cost_logs
    ):
        result = _result(
            auth_client.get("/agentcc/analytics/cost-breakdown/?group_by=model&top_n=1")
        )
        rows = {row["name"]: row for row in result["breakdown"]}

        assert rows["Other"]["unpriced_requests"] == 3
        assert rows["Other"]["request_count"] == 4

    def test_a_session_counts_its_unpriced_requests(self, auth_client, cost_logs):
        response = auth_client.get("/agentcc/sessions/")
        assert response.status_code == status.HTTP_200_OK, response.content
        body = response.json()
        rows = {r["session_id"]: r for r in body.get("results", body.get("result"))}

        stats = rows["sess-mixed"]["stats"]
        assert stats["request_count"] == 2
        assert stats["unpriced_requests"] == 1
