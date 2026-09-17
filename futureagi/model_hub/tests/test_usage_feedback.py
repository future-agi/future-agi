"""
Tests for Phase 10: Usage & Feedback APIs.
"""

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate


@pytest.fixture
def eval_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="usage-test-eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
        visible_ui=True,
    )


@pytest.mark.e2e
@pytest.mark.django_db
class TestEvalUsageStatsAPI:
    def test_get_usage_stats(self, auth_client, eval_template):
        response = auth_client.get(
            f"/model-hub/eval-templates/{eval_template.id}/usage/"
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert result["template_id"] == str(eval_template.id)
        stats = result["stats"]
        assert "total_runs" in stats
        assert "runs_period" in stats
        assert "success_count" in stats
        assert "error_count" in stats
        assert "pass_rate" in stats

    def test_usage_nonexistent(self, auth_client):
        response = auth_client.get(
            "/model-hub/eval-templates/00000000-0000-0000-0000-000000000000/usage/"
        )
        assert response.status_code == 404


@pytest.mark.e2e
@pytest.mark.django_db
class TestEvalFeedbackListAPI:
    def test_get_feedback_empty(self, auth_client, eval_template):
        response = auth_client.get(
            f"/model-hub/eval-templates/{eval_template.id}/feedback-list/"
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert result["template_id"] == str(eval_template.id)
        assert result["total"] == 0
        assert result["items"] == []

    def test_feedback_nonexistent(self, auth_client):
        response = auth_client.get(
            "/model-hub/eval-templates/00000000-0000-0000-0000-000000000000/feedback-list/"
        )
        assert response.status_code == 404
