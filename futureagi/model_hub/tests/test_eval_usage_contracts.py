"""Contract tests for the eval usage stats endpoint (TH-5173 / PR #747).

Asserts the response shape matches EvalUsageStatsResponseSerializer so
runtime response validation never fires unexpectedly.
"""
import json
import uuid

import pytest
from ee.usage.models.usage import APICallLog, APICallStatusChoices
from model_hub.models.choices import OwnerChoices, SourceChoices
from model_hub.models.evals_metric import EvalTemplate, Feedback
from accounts.models.workspace import Workspace


def _make_template(organization, workspace=None, name=None):
    return EvalTemplate.no_workspace_objects.create(
        name=name or f"usage-contract-eval-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
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
        config=config or {
            "output": {"output": 1.0, "reason": "looks good"},
            "mappings": {"response": "hello"},
        },
    )


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
        for field in ("total_runs", "runs_period", "success_count", "error_count", "pass_rate"):
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


# ── Populated response contract ───────────────────────────────────────────────

@pytest.mark.django_db
class TestPopulatedContractResponse:
    """Contract validation against a real populated response.

    Exercises the shapes that carry actual risk: numeric scores, choice-format
    outputs {label, score}, feedback rows, and dynamic input_var_X columns.
    An empty-template test cannot catch type mismatches on real rows.
    """

    @pytest.fixture
    def template_with_logs(self, organization, workspace, user):
        template = _make_template(organization, workspace)


        # Plain numeric score
        _make_log(organization, workspace, template, config={
            "output": {"output": 0.85, "reason": "close enough"},
            "mappings": {"response": "hello"},
            "input_var_response": "hello",
        })

        # Choice-format output {label, score}
        _make_log(organization, workspace, template, config={
            "output": {"output": {"label": "Passed", "score": 1.0}, "reason": "correct"},
            "mappings": {"response": "world"},
            "input_var_response": "world",
        })

        # Log with feedback
        log_with_feedback = _make_log(organization, workspace, template, config={
            "output": {"output": 0.0, "reason": "wrong"},
            "mappings": {"response": "bad"},
        })
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

        resp = auth_client.get(
            f"/model-hub/eval-templates/{template_with_logs.id}/usage/",
            {"page": 0, "page_size": 25, "period": "30d"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"]["logs"]["total"] == 3

        s = EvalUsageStatsResponseSerializer(data=body)
        assert s.is_valid(), f"Serializer rejected populated response: {s.errors}"

    def test_chart_aggregates_choice_score(self, auth_client, template_with_logs):
        """Chart must include avg_score for choice-format {label, score} outputs.

        Previously choice outputs were silently skipped, leaving chart data
        empty even when logs exist.
        """
        resp = auth_client.get(
            f"/model-hub/eval-templates/{template_with_logs.id}/usage/",
            {"page": 0, "page_size": 25, "period": "30d"},
        )
        chart = resp.json()["result"]["chart"]
        scores = [p["avg_score"] for p in chart if p["avg_score"] is not None]
        assert len(scores) > 0, "Chart has no avg_score — choice outputs not aggregated"

    def test_table_rows_include_choice_and_numeric(
        self, auth_client, template_with_logs
    ):
        resp = auth_client.get(
            f"/model-hub/eval-templates/{template_with_logs.id}/usage/",
            {"page": 0, "page_size": 25, "period": "30d"},
        )
        table = resp.json()["result"]["table"]
        assert len(table) == 3
        # scores are wrapped as {"cell_value": <score>} in raw table rows
        raw_scores = [row.get("score") for row in table]
        scores = [
            s["cell_value"] if isinstance(s, dict) and "cell_value" in s else s
            for s in raw_scores
        ]
        assert 1.0 in scores
        assert 0.85 in scores


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
        """Sanity: the workspace filter must not over-restrict."""
        template = _make_template(organization, workspace=None)
        _make_log(organization, workspace, template)

        resp = auth_client.get(
            f"/model-hub/eval-templates/{template.id}/usage/",
            {"page": 0, "page_size": 25, "period": "30d"},
        )
        assert resp.json()["result"]["logs"]["total"] == 1


# ── Service layer — drift surfaces at the serializer boundary ────────────────

class TestServiceShapeMatchesSerializer:
    """The Usage views push response construction into
    `model_hub.services.eval_usage_service` and run the result through
    `Serializer(instance=result).data`. A missing required field would
    raise a KeyError at the serializer boundary — these tests pin the
    shape contract so drift can't silently re-emerge.
    """

    def test_empty_eval_usage_stats_renders(self):
        from model_hub.services.eval_usage_service import empty_eval_usage_stats
        from model_hub.serializers.contracts import (
            EvalUsageStatsResponseResultSerializer,
        )

        rendered = EvalUsageStatsResponseResultSerializer(
            instance=empty_eval_usage_stats("00000000-0000-0000-0000-000000000001")
        ).data
        assert rendered["template_id"] == "00000000-0000-0000-0000-000000000001"
        assert rendered["is_composite"] is False
        assert rendered["stats"]["total_runs"] == 0
        assert rendered["chart"] == []
        assert rendered["table"] == []
        assert rendered["logs"] == {"total": 0, "page": 0, "page_size": 25}

    def test_empty_eval_feedback_list_renders(self):
        from model_hub.services.eval_usage_service import empty_eval_feedback_list
        from model_hub.serializers.contracts import (
            EvalFeedbackListResponseResultSerializer,
        )

        rendered = EvalFeedbackListResponseResultSerializer(
            instance=empty_eval_feedback_list("00000000-0000-0000-0000-000000000001")
        ).data
        assert rendered["items"] == []
        assert rendered["total"] == 0

    def test_empty_api_call_log_details_renders(self):
        from model_hub.services.eval_usage_service import empty_api_call_log_details
        from model_hub.serializers.contracts import (
            EvalApiLogTableResponseResultSerializer,
        )

        rendered = EvalApiLogTableResponseResultSerializer(
            instance=empty_api_call_log_details()
        ).data
        assert rendered["table"] == []
        assert rendered["column_config"] == []


# ── EvalApiLogTable + column_config contract ─────────────────────────────────

@pytest.mark.django_db
class TestEvalApiLogTableContract:
    """The EvalApiLogTable response must conform to its serializer including
    the typed column_config shape (id/name/is_visible/status/source_type)."""

    def test_empty_response_validates(self, auth_client, user_eval_template):
        from model_hub.serializers.contracts import EvalApiLogTableResponseSerializer

        resp = auth_client.get(
            "/model-hub/get-eval-logs-details",
            {
                "eval_template_id": str(user_eval_template.id),
                "page_size": 25,
                "current_page_index": 0,
                "source": "feedback",
                "search": "",
                "filters": "[]",
                "sort": "[]",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        s = EvalApiLogTableResponseSerializer(data=body)
        assert s.is_valid(), f"Empty-shape response failed contract: {s.errors}"

    def test_column_config_items_have_typed_shape(
        self, auth_client, user_eval_template
    ):
        """Each column_config entry must declare the stable id/name/is_visible
        /status/source_type fields — Nikhil specifically asked for these to be
        typed (not bare JSONField) so consumers get a real contract."""
        resp = auth_client.get(
            "/model-hub/get-eval-logs-details",
            {
                "eval_template_id": str(user_eval_template.id),
                "page_size": 25,
                "current_page_index": 0,
                "source": "feedback",
                "search": "",
                "filters": "[]",
                "sort": "[]",
            },
        )
        result = resp.json()["result"]
        assert isinstance(result["column_config"], list)
        for col in result["column_config"]:
            assert "id" in col and isinstance(col["id"], str)
            assert "name" in col and isinstance(col["name"], str)
            assert "is_visible" in col and isinstance(col["is_visible"], bool)
            assert "status" in col and isinstance(col["status"], str)
            assert "source_type" in col and isinstance(col["source_type"], str)


# ── Date-range symmetry validation ───────────────────────────────────────────

@pytest.mark.django_db
class TestDateRangeSymmetry:
    """start_date and end_date must be sent together — half a range silently
    falling through to `period` is exactly the kind of bug Nikhil flags."""

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
        assert "together" in resp.json().get("detail", str(resp.json())).lower() or \
               resp.status_code == 400

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
