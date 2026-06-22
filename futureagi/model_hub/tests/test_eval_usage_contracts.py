"""Contract tests for the eval usage stats endpoint (TH-5173 / PR #747).

Asserts the response shape matches EvalUsageStatsResponseSerializer so
runtime response validation never fires unexpectedly.
"""
import pytest
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate


@pytest.fixture
def user_eval_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="usage-contract-eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "AgentEvaluator"},
        eval_tags=["llm"],
        criteria="Check {{response}}",
        model="turing_large",
        visible_ui=True,
    )


@pytest.mark.e2e
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
