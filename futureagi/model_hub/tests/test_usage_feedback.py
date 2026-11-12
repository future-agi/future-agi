"""
Tests for Phase 10: Usage & Feedback APIs.
"""

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, Feedback


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


# Not e2e-marked: pytest.ini deselects e2e by default, and this regression
# must run with the default suite.
@pytest.mark.integration
@pytest.mark.django_db
class TestEvalFeedbackListAuthor:
    def test_feedback_items_carry_author_id(
        self, auth_client, eval_template, user, organization, workspace
    ):
        """W10-EVALS-U01: trace feedback can only be edited by its author.

        The Feedback tab compares each row's author with the viewer before it
        offers Edit Feedback, so every item must carry the author's id.
        """
        feedback = Feedback.objects.create(
            source="observe",
            source_id="span-w10-evals-u01",
            value="passed",
            explanation="Original note",
            user=user,
            eval_template=eval_template,
            organization=organization,
            workspace=workspace,
        )

        response = auth_client.get(
            f"/model-hub/eval-templates/{eval_template.id}/feedback-list/"
        )

        assert response.status_code == 200
        items = response.data["result"]["items"]
        assert [item["id"] for item in items] == [str(feedback.id)]
        assert items[0]["user_id"] == str(user.id)
