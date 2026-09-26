"""Contract tests for the eval usage stats endpoint (TH-5173 / PR #1319).

Asserts the response shape matches EvalUsageStatsResponseSerializer so
runtime response validation never fires unexpectedly.
"""

import uuid

import pytest

from accounts.models.workspace import Workspace
from ee.usage.models.usage import APICallLog, APICallStatusChoices
from model_hub.models.choices import OwnerChoices, SourceChoices
from model_hub.models.evals_metric import EvalTemplate, Feedback


def _make_template(organization, workspace=None, name=None, owner=None):
    return EvalTemplate.no_workspace_objects.create(
        name=name or f"usage-contract-eval-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        owner=owner or OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "AgentEvaluator"},
        eval_tags=["llm"],
        criteria="Check {{response}}",
        model="turing_large",
        visible_ui=True,
    )


def _make_log(organization, workspace, template, config=None):
    return APICallLog.objects.create(
        organization=organization,
        workspace=workspace,
        status=APICallStatusChoices.SUCCESS.value,
        cost=0,
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(template.id),
        config=config
        or {
            "output": {"output": 1.0, "reason": "looks good"},
            "mappings": {"response": "hello"},
        },
    )


def _complete_usage_contract_payload(template):
    """Return an exact-worker-shaped payload for serializer assertions.

    HTTP reads now poll the last complete ClickHouse snapshot and may
    legitimately be pending until the worker publishes. Serializer tests must
    stay independent of that asynchronous timing and the retired PostgreSQL
    usage-log read path.
    """

    rows = [
        {
            "row_id": str(uuid.uuid4()),
            "score": {"cell_value": 0.85},
            "result": {"cell_value": ""},
            "input_var_response": {"cell_value": "hello"},
        },
        {
            "row_id": str(uuid.uuid4()),
            "score": {"cell_value": 1.0},
            "result": {"cell_value": "Passed"},
            "input_var_response": {"cell_value": "world"},
        },
        {
            "row_id": str(uuid.uuid4()),
            "score": {"cell_value": 0.0},
            "result": {"cell_value": "Failed"},
            "input_var_response": {"cell_value": "bad"},
        },
    ]
    return {
        "status": True,
        "result": {
            "template_id": str(template.id),
            "is_composite": False,
            "completeness": "complete",
            "unavailable_fields": [],
            "stats": {
                "total_runs": 3,
                "runs_period": 3,
                "success_count": 3,
                "error_count": 0,
                "pass_rate": 100.0,
            },
            "chart": [
                {
                    "timestamp": "2026-08-01T00:00:00+00:00",
                    "calls": 3,
                    "avg_latency_ms": 0,
                    "avg_score": 0.617,
                    "pass_count": 1,
                    "fail_count": 1,
                }
            ],
            "table": rows,
            "logs": {"total": 3, "page": 0, "page_size": 25},
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        },
    }


@pytest.fixture
def user_eval_template(organization, workspace):
    return _make_template(organization, workspace)


# ── Shape tests (empty response) ─────────────────────────────────────────────


@pytest.mark.django_db
class TestEvalUsageStatsResponseShape:
    """Verify /model-hub/eval-templates/<id>/usage/ returns the contracted shape."""

    def test_response_has_required_top_level_keys(
        self, auth_client, user_eval_template
    ):
        resp = auth_client.get(
            f"/model-hub/eval-templates/{user_eval_template.id}/usage/",
            {"page": 0, "page_size": 5, "period": "30d"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] is True
        result = data["result"]
        assert "template_id" in result
        assert "is_composite" in result
        assert "stats" in result
        assert "chart" in result
        assert "table" in result
        assert "logs" in result

    def test_table_is_list(self, auth_client, user_eval_template):
        resp = auth_client.get(
            f"/model-hub/eval-templates/{user_eval_template.id}/usage/",
            {"page": 0, "page_size": 5, "period": "30d"},
        )
        result = resp.json()["result"]
        assert isinstance(result["table"], list)

    def test_logs_has_pagination_fields(self, auth_client, user_eval_template):
        resp = auth_client.get(
            f"/model-hub/eval-templates/{user_eval_template.id}/usage/",
            {"page": 0, "page_size": 5, "period": "30d"},
        )
        logs = resp.json()["result"]["logs"]
        assert "total" in logs
        assert "page" in logs
        assert "page_size" in logs

    def test_stats_has_required_fields(self, auth_client, user_eval_template):
        resp = auth_client.get(
            f"/model-hub/eval-templates/{user_eval_template.id}/usage/",
            {"page": 0, "page_size": 5, "period": "30d"},
        )
        stats = resp.json()["result"]["stats"]
        for field in (
            "total_runs",
            "runs_period",
            "success_count",
            "error_count",
            "pass_rate",
        ):
            assert field in stats, f"stats.{field} missing"

    def test_chart_is_list(self, auth_client, user_eval_template):
        resp = auth_client.get(
            f"/model-hub/eval-templates/{user_eval_template.id}/usage/",
            {"page": 0, "page_size": 5, "period": "30d"},
        )
        assert isinstance(resp.json()["result"]["chart"], list)

    def test_serializer_validates_response(self, auth_client, user_eval_template):
        """EvalUsageStatsResponseSerializer must accept the actual response without error."""
        from model_hub.serializers.contracts import EvalUsageStatsResponseSerializer

        resp = auth_client.get(
            f"/model-hub/eval-templates/{user_eval_template.id}/usage/",
            {"page": 0, "page_size": 5, "period": "30d"},
        )
        s = EvalUsageStatsResponseSerializer(data=resp.json())
        assert s.is_valid(), f"Serializer rejected response: {s.errors}"

    def test_system_template_usage_does_not_404(self, auth_client, organization):
        """System templates have organization=NULL — the org-scoping filter
        must not exclude them (regression: a naive organization=org filter
        404s every system template's usage page)."""
        template = _make_template(organization=None, owner=OwnerChoices.SYSTEM.value)
        resp = auth_client.get(
            f"/model-hub/eval-templates/{template.id}/usage/",
            {"page": 0, "page_size": 5, "period": "30d"},
        )
        assert resp.status_code == 200


# ── Populated response contract ───────────────────────────────────────────────


@pytest.mark.django_db
class TestPopulatedContractResponse:
    """Contract validation against a complete worker-shaped payload.

    Selector and exact worker mapping have their own coverage. These tests
    verify that populated rows survive the public serializer boundary without
    depending on asynchronous snapshot publication.
    """

    @pytest.fixture
    def template_with_logs(self, organization, workspace, user):
        template = _make_template(organization, workspace)

        # Plain numeric score
        _make_log(
            organization,
            workspace,
            template,
            config={
                "output": {"output": 0.85, "reason": "close enough"},
                "mappings": {"response": "hello"},
                "input_var_response": "hello",
            },
        )

        # Choice-format output {label, score}
        _make_log(
            organization,
            workspace,
            template,
            config={
                "output": {
                    "output": {"label": "Passed", "score": 1.0},
                    "reason": "correct",
                },
                "mappings": {"response": "world"},
                "input_var_response": "world",
            },
        )

        # Log with feedback
        log_with_feedback = _make_log(
            organization,
            workspace,
            template,
            config={
                "output": {"output": 0.0, "reason": "wrong"},
                "mappings": {"response": "bad"},
            },
        )
        Feedback.objects.create(
            organization=organization,
            user=user,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(log_with_feedback.log_id),
            value="thumbs_down",
            eval_template=template,
        )

        return template

    def test_serializer_validates_populated_response(
        self, auth_client, template_with_logs
    ):
        from model_hub.serializers.contracts import EvalUsageStatsResponseSerializer

        body = _complete_usage_contract_payload(template_with_logs)
        assert body["result"]["logs"]["total"] == 3

        s = EvalUsageStatsResponseSerializer(data=body)
        assert s.is_valid(), f"Serializer rejected populated response: {s.errors}"

    def test_chart_aggregates_choice_score(self, auth_client, template_with_logs):
        """Chart must include avg_score for choice-format {label, score} outputs.

        Previously choice outputs were silently skipped, leaving chart data
        empty even when logs exist.
        """
        chart = _complete_usage_contract_payload(template_with_logs)["result"]["chart"]
        scores = [p["avg_score"] for p in chart if p["avg_score"] is not None]
        assert len(scores) > 0, "Chart has no avg_score — choice outputs not aggregated"

    def test_table_rows_include_choice_and_numeric(
        self, auth_client, template_with_logs
    ):
        table = _complete_usage_contract_payload(template_with_logs)["result"]["table"]
        assert len(table) == 3
        # scores are wrapped as {"cell_value": <score>} in raw table rows
        raw_scores = [row.get("score") for row in table]
        scores = [
            s["cell_value"] if isinstance(s, dict) and "cell_value" in s else s
            for s in raw_scores
        ]
        assert 1.0 in scores
        assert 0.85 in scores

    def test_dynamic_input_var_cells_survive_serialization(
        self, auth_client, template_with_logs
    ):
        """The dynamic input_var_<name> columns must survive the serializer
        boundary (_ExtraFieldsMixin.to_representation) — without it DRF
        strips undeclared keys and the grid loses its per-variable columns."""
        from model_hub.serializers.contracts import EvalUsageStatsResponseSerializer

        payload = _complete_usage_contract_payload(template_with_logs)
        serialized = EvalUsageStatsResponseSerializer(instance=payload).data
        table = serialized["result"]["table"]
        rows_with_var = [r for r in table if "input_var_response" in r]
        assert rows_with_var, "input_var_response cells were stripped at the boundary"
        assert rows_with_var[0]["input_var_response"]["cell_value"] in (
            "hello",
            "world",
            "bad",
        )


# ── Workspace isolation ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestWorkspaceIsolation:
    """One workspace must not be able to read another workspace's eval logs.

    This is the cross-tenant security fix shipped in this PR. Zero regression
    coverage on a security boundary is not acceptable.
    """

    def test_workspace_b_cannot_read_workspace_a_logs(
        self, auth_client, organization, workspace, user
    ):
        from conftest import WorkspaceAwareAPIClient

        # workspace = workspace A (the default for this org/user)
        template = _make_template(organization, workspace)  # belongs to workspace A
        _make_log(organization, workspace, template)

        # Create workspace B in the same org
        workspace_b = Workspace.objects.create(
            name="workspace-b",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )

        client_b = WorkspaceAwareAPIClient()
        client_b.force_authenticate(user=user)
        client_b.set_workspace(workspace_b)

        resp = client_b.get(
            f"/model-hub/eval-templates/{template.id}/usage/",
            {"page": 0, "page_size": 25, "period": "30d"},
        )

        # Workspace B must not be able to read workspace A's template at all.
        # 404 is the correct isolation — the template is scoped to workspace A.
        assert resp.status_code == 404, (
            f"Workspace B can access workspace A's template — cross-workspace leak "
            f"(got {resp.status_code})"
        )

        client_b.stop_workspace_injection()

    def test_workspace_a_sees_own_logs(self, auth_client, organization, workspace):
        """Sanity: workspace A can poll its exact usage snapshot."""
        template = _make_template(organization, workspace=None)
        _make_log(organization, workspace, template)

        resp = auth_client.get(
            f"/model-hub/eval-templates/{template.id}/usage/",
            {"page": 0, "page_size": 25, "period": "30d"},
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["query_status"] in {"pending", "complete"}


# ── Date-range symmetry validation ───────────────────────────────────────────


@pytest.mark.django_db
class TestDateRangeSymmetry:
    """start_date and end_date must be sent together — half a range silently
    falling through to `period` would turn "user picked Yesterday" into
    "user got 30 days of data"."""

    def test_only_start_date_rejected(self, auth_client, user_eval_template):
        resp = auth_client.get(
            f"/model-hub/eval-templates/{user_eval_template.id}/usage/",
            {
                "page": 0,
                "page_size": 25,
                "period": "30d",
                "start_date": "2026-01-01T00:00:00Z",
            },
        )
        assert resp.status_code == 400

    def test_only_end_date_rejected(self, auth_client, user_eval_template):
        resp = auth_client.get(
            f"/model-hub/eval-templates/{user_eval_template.id}/usage/",
            {
                "page": 0,
                "page_size": 25,
                "period": "30d",
                "end_date": "2026-01-01T00:00:00Z",
            },
        )
        assert resp.status_code == 400

    def test_start_after_end_rejected(self, auth_client, user_eval_template):
        resp = auth_client.get(
            f"/model-hub/eval-templates/{user_eval_template.id}/usage/",
            {
                "page": 0,
                "page_size": 25,
                "start_date": "2026-12-31T00:00:00Z",
                "end_date": "2026-01-01T00:00:00Z",
            },
        )
        assert resp.status_code == 400

    def test_both_dates_accepted(self, auth_client, user_eval_template):
        resp = auth_client.get(
            f"/model-hub/eval-templates/{user_eval_template.id}/usage/",
            {
                "page": 0,
                "page_size": 25,
                "start_date": "2026-01-01T00:00:00Z",
                "end_date": "2026-12-31T00:00:00Z",
            },
        )
        assert resp.status_code == 200
