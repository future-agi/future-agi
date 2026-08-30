"""Behavioral test for the recalculate action on observation spans.

Regression guard for #2333: ``submit_feedback_action_type`` (action
``recalculate``) calls ``evaluate_observation_span`` positionally with
``(span_id, config_id, task_id, feedback_id)`` where ``task_id`` is the
``eval_task_id`` of the previously persisted eval logger.

Before the fix, ``eval_task_id`` was appended to the signature AFTER
``feedback_id``, so ``task_id`` silently bound to ``feedback_id`` and
``feedback_id`` to ``eval_task_id`` — the eval was persisted with the
wrong linkage. This test drives the REAL path (view -> evaluate_observation_span
-> _execute_evaluation -> EvalLogger persistence; only the eval engine and the
billing layer are stubbed) and asserts the persisted eval carries the correct
``eval_task_id`` and that the feedback id reaches the billing source config.
"""

import json
import uuid

import pytest

from model_hub.models.choices import FeedbackSourceChoices
from model_hub.models.evals_metric import Feedback
from tracer.models.observation_span import EvalLogger


@pytest.fixture
def cost_log_recorder(monkeypatch, stub_cost_log):
    """Capture the source/config the real eval path sends to the billing
    layer while still stubbing billing itself (delegates to ``stub_cost_log``).
    """
    import tracer.utils.eval as eval_module

    recorded = {}

    def _record(
        *, organization, api_call_type, source, source_id, config, workspace, **kwargs
    ):
        recorded["source"] = source
        recorded["source_id"] = source_id
        recorded["config"] = config if isinstance(config, dict) else json.loads(config)
        return stub_cost_log(
            organization=organization,
            api_call_type=api_call_type,
            source=source,
            source_id=source_id,
            config=config,
            workspace=workspace,
            **kwargs,
        )

    monkeypatch.setattr(eval_module, "log_and_deduct_cost_for_api_request", _record)
    return recorded


def test_recalculate_persists_eval_with_correct_task_and_feedback_ids(
    auth_client,
    user,
    organization,
    workspace,
    observation_span,
    project_version,
    custom_eval_config,
    stub_run_eval,
    cost_log_recorder,
):
    """Recalculate runs the real ``evaluate_observation_span`` path with the
    positional ``(span_id, config_id, task_id, feedback_id)`` call and
    persists the eval with the correct ``eval_task_id`` and feedback linkage.
    """
    span = observation_span
    # EXPERIMENT branch: recalculate calls ``evaluate_observation_span``
    # (not the observe sibling) when the span belongs to a project version.
    span.project_version = project_version
    span.save(update_fields=["project_version"])

    task_id = f"task_{uuid.uuid4().hex[:12]}"

    # The span already has an eval result; recalculate derives the
    # eval_task_id from it, soft-deletes it, then re-runs the eval.
    prior_logger = EvalLogger.objects.create(
        trace=span.trace,
        observation_span=span,
        custom_eval_config=custom_eval_config,
        eval_task_id=task_id,
        output_str="prior result",
    )

    feedback = Feedback.objects.create(
        source=FeedbackSourceChoices.EXPERIMENT.value,
        source_id=span.id,
        value="Looks good",
        explanation=None,
        user=user,
        organization=organization,
        workspace=workspace,
    )

    response = auth_client.post(
        "/tracer/observation-span/submit_feedback_action_type/",
        {
            "observation_span_id": str(span.id),
            "action_type": "recalculate",
            "custom_eval_config_id": str(custom_eval_config.id),
            "feedback_id": str(feedback.id),
        },
        format="json",
    )

    assert response.status_code == 200, response.content

    # The recalculate branch soft-deleted the prior logger.
    prior_logger.refresh_from_db()
    assert prior_logger.deleted is True

    # The real path persisted a fresh eval logger whose eval_task_id is the
    # task id (position 3), NOT the feedback id (which would indicate the
    # silent parameter swap this test guards against).
    new_logger = EvalLogger.objects.get(
        observation_span=span, custom_eval_config=custom_eval_config
    )
    assert new_logger.id != prior_logger.id
    assert new_logger.eval_task_id == task_id

    # feedback_id travelled through the real call and landed in the billing
    # source config as the feedback-linked eval source.
    assert cost_log_recorder["config"]["feedback_id"] == str(feedback.id)
    assert cost_log_recorder["source"] == "feedback"
