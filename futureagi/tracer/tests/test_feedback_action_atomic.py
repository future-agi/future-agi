"""Edit Feedback on trace feedback saves all of an edit or none of it.

W11-EVALS-FB-SAVE-BEFORE-LOOKUP: a re-calculation that cannot start must not
store the edit and then report a failure. W11-EVALS-FB-NOTE-FIELD: the
person's edited note is where the Feedback tab reads it.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.utils import timezone
from model_hub.models.ai_model import AIModel
from model_hub.models.evals_metric import EvalTemplate, Feedback
from tfc.utils.tests.two_tenants import make_tenant
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.views import observation_span as span_view

URL = "/tracer/observation-span/submit_feedback_action_type/"
pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]

NO_RESULT = (
    "This span has no eval result to re-calculate, so nothing was saved. "
    "Choose Re-tune to save your changes without re-running the eval."
)
NOT_SAVED = "Your feedback wasn't saved. Try again."
SAVED_NOT_RERUN = (
    "Your feedback was saved, but the eval couldn't be re-run. "
    "Reload the page to see what was saved."
)


@pytest.fixture
def owner():
    tenant = make_tenant("feedback-atomic")
    yield tenant
    tenant.client.stop_workspace_injection()


@pytest.fixture
def evaluator(monkeypatch):
    observe = Mock(return_value=True)
    monkeypatch.setattr(span_view, "evaluate_observation_span", Mock())
    monkeypatch.setattr(span_view, "evaluate_observation_span_observe", observe)
    monkeypatch.setattr(span_view, "get_mixpanel_properties", Mock(return_value={}))
    monkeypatch.setattr(span_view, "track_mixpanel_event", Mock())
    return observe


def _observe_feedback(tenant):
    project = Project.no_workspace_objects.create(
        name=f"Feedback atomic {uuid.uuid4().hex[:8]}",
        organization=tenant.organization,
        workspace=tenant.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    trace = Trace.objects.create(project=project)
    span = ObservationSpan.objects.create(
        id=f"atomic-{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="Feedback span",
        observation_type="llm",
        start_time=timezone.now(),
    )
    template = EvalTemplate.no_workspace_objects.create(
        name=f"feedback-atomic-{uuid.uuid4().hex[:8]}",
        organization=tenant.organization,
        workspace=tenant.workspace,
        config={"type": "pass_fail", "criteria": "Is the output correct?"},
    )
    config = CustomEvalConfig.objects.create(
        name="Feedback eval", project=project, eval_template=template, config={}
    )
    # What Observe stores: the eval's own explanation and the person's note.
    feedback = Feedback.objects.create(
        source="observe",
        source_id=span.id,
        value="true",
        explanation="The eval's own explanation",
        feedback_improvement="The note the person wrote",
        user=tenant.user,
        eval_template=template,
        custom_eval_config_id=config.id,
        organization=tenant.organization,
        workspace=tenant.workspace,
    )
    log = EvalLogger.objects.create(
        observation_span=span,
        trace=trace,
        custom_eval_config=config,
        eval_task_id=str(uuid.uuid4()),
        output_bool=False,
    )
    return SimpleNamespace(span=span, config=config, feedback=feedback, log=log)


def _edit(records, action_type="recalculate"):
    return {
        "observation_span_id": records.span.id,
        "custom_eval_config_id": str(records.config.id),
        "feedback_id": str(records.feedback.id),
        "action_type": action_type,
        "value": "failed",
        "explanation": "Corrected note",
    }


def _assert_feedback_unchanged(records):
    records.feedback.refresh_from_db()
    assert records.feedback.value == "true"
    assert records.feedback.explanation == "The eval's own explanation"
    assert records.feedback.feedback_improvement == "The note the person wrote"
    assert records.feedback.action_type is None


def test_recalculate_without_eval_result_saves_nothing_and_says_choose_retune(
    owner, evaluator
):
    records = _observe_feedback(owner)
    records.log.deleted = True
    records.log.deleted_at = timezone.now()
    records.log.save(update_fields=["deleted", "deleted_at"])

    response = owner.client.post(URL, _edit(records), format="json")

    assert response.status_code == 400, response.content
    assert response.json()["message"] == NO_RESULT
    _assert_feedback_unchanged(records)
    evaluator.assert_not_called()


def test_failed_result_retirement_rolls_back_the_edit(owner, evaluator, monkeypatch):
    records = _observe_feedback(owner)
    monkeypatch.setattr(
        span_view,
        "soft_delete_eval_results",
        Mock(side_effect=RuntimeError("retire failed")),
    )

    response = owner.client.post(URL, _edit(records), format="json")

    assert response.status_code == 400, response.content
    assert response.json()["message"] == NOT_SAVED
    _assert_feedback_unchanged(records)
    records.log.refresh_from_db()
    assert records.log.deleted is False
    evaluator.assert_not_called()


def test_failed_rerun_after_save_says_the_edit_was_saved(owner, evaluator):
    records = _observe_feedback(owner)
    evaluator.side_effect = RuntimeError("rerun failed")

    response = owner.client.post(URL, _edit(records), format="json")

    assert response.status_code == 400, response.content
    assert response.json()["message"] == SAVED_NOT_RERUN
    records.feedback.refresh_from_db()
    assert records.feedback.value == "failed"
    assert records.feedback.action_type == "recalculate"


@pytest.mark.parametrize("action_type", ["retune", "recalculate"])
def test_edited_note_replaces_the_note_the_feedback_tab_shows(
    owner, evaluator, action_type
):
    records = _observe_feedback(owner)

    response = owner.client.post(URL, _edit(records, action_type), format="json")

    assert response.status_code == 200, response.content
    records.feedback.refresh_from_db()
    assert records.feedback.feedback_improvement == "Corrected note"
    assert records.feedback.explanation == "Corrected note"
    assert records.feedback.value == "failed"


def test_action_only_request_keeps_the_note_observe_stored(owner, evaluator):
    records = _observe_feedback(owner)
    body = {
        key: value
        for key, value in _edit(records, "retune").items()
        if key not in ("value", "explanation")
    }

    response = owner.client.post(URL, body, format="json")

    assert response.status_code == 200, response.content
    records.feedback.refresh_from_db()
    assert records.feedback.value == "true"
    assert records.feedback.explanation == "The eval's own explanation"
    assert records.feedback.feedback_improvement == "The note the person wrote"
    assert records.feedback.action_type == "retune"
