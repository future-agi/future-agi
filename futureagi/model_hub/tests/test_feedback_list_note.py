"""The eval Feedback tab gets the note the person wrote on trace feedback.

W11-EVALS-FB-NOTE-FIELD / W10-EVALS-U08: Observe stores the eval's own
explanation in ``explanation`` and the person's note in
``feedback_improvement``. The feedback list must carry both so the tab can show
the person's note instead of the eval's explanation.
"""

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, Feedback


@pytest.fixture
def eval_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="feedback-note-eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
        visible_ui=True,
    )


def _feedback_list(auth_client, eval_template):
    response = auth_client.get(
        f"/model-hub/eval-templates/{eval_template.id}/feedback-list/"
    )
    assert response.status_code == 200, response.content
    return {item["id"]: item for item in response.data["result"]["items"]}


@pytest.mark.integration
@pytest.mark.django_db
class TestEvalFeedbackListNote:
    def test_trace_feedback_carries_the_persons_note(
        self, auth_client, eval_template, user, organization, workspace
    ):
        feedback = Feedback.objects.create(
            source="observe",
            source_id="span-w11-note",
            value="failed",
            explanation="The eval's own explanation",
            feedback_improvement="The note the person wrote",
            user=user,
            eval_template=eval_template,
            organization=organization,
            workspace=workspace,
        )

        item = _feedback_list(auth_client, eval_template)[str(feedback.id)]

        assert item["feedback_improvement"] == "The note the person wrote"
        assert item["explanation"] == "The eval's own explanation"

    def test_feedback_without_a_separate_note_sends_blank(
        self, auth_client, eval_template, user, organization, workspace
    ):
        feedback = Feedback.objects.create(
            source="dataset",
            source_id="column-w11-note",
            value="passed",
            explanation="Dataset note",
            user=user,
            eval_template=eval_template,
            organization=organization,
            workspace=workspace,
        )

        item = _feedback_list(auth_client, eval_template)[str(feedback.id)]

        assert item["feedback_improvement"] == ""
        assert item["explanation"] == "Dataset note"
