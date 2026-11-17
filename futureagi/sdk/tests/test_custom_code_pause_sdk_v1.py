"""/sdk/api/v1/eval/ answers a refused custom code evaluation with 503, the refusal
sentence, code CUSTOM_CODE_EVALS_PAUSED and retryable false.

Any other failure keeps the generic 500.
"""

import json
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework import status

from agentic_eval.core_evals.fi_utils import sandbox
from model_hub.models.evals_metric import EvalTemplate

SENTENCE = "Custom code evaluations are temporarily unavailable on Future AGI Cloud."
CUSTOMER_CODE = "def evaluate(input, **kwargs):\n    return 0.5\n"
CALLER_CODE_EVAL_ID = 990034


@pytest.fixture
def executor(monkeypatch):
    sent = []

    def urlopen(request, timeout=None):
        sent.append(json.loads(request.data))
        raise AssertionError("nothing may reach the code executor")

    monkeypatch.setattr(sandbox.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sandbox, "CODE_EXECUTOR_URL", "http://code-executor.test:8060")
    return sent


@pytest.fixture
def caller_code_template(db):
    """A system template whose code the caller supplies, like the built-in custom_code_evaluation."""
    template = EvalTemplate.no_workspace_objects.create(
        name="pause_sdk_v1_caller_code",
        eval_id=CALLER_CODE_EVAL_ID,
        owner="system",
        organization=None,
        workspace=None,
        eval_type="code",
        config={
            "required_keys": [],
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "config": {"code": {"type": "code", "default": None}},
        },
    )
    # save() stamps the request's organization and workspace on a row saved while a
    # workspace context is set; a built-in has neither, so clear them without save().
    EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(
        organization=None, workspace=None
    )
    template.refresh_from_db()
    return template


def body(response):
    return response.content.decode()


def assert_paused(response):
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, response.data
    assert response.data["code"] == "CUSTOM_CODE_EVALS_PAUSED"
    assert response.data["error_code"] == "CUSTOM_CODE_EVALS_PAUSED"
    assert response.data["retryable"] is False


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_caller_code_on_v1_eval_answers_the_sentence(
    executor, caller_code_template, auth_client
):
    with (
        patch(
            "sdk.utils.evaluations._log_and_deduct_cost_for_standalone_eval",
            return_value=None,
        ),
        patch("sdk.views.evaluations.track_mixpanel_event"),
    ):
        response = auth_client.post(
            "/sdk/api/v1/eval/",
            {
                "inputs": [{"input": "hello"}],
                "config": {str(CALLER_CODE_EVAL_ID): {"code": CUSTOMER_CODE}},
            },
            format="json",
        )
    assert_paused(response)
    assert SENTENCE in body(response)
    assert "try again" not in body(response).lower()
    assert executor == []


@pytest.mark.django_db
def test_a_refusal_from_the_runner_becomes_a_503_with_its_sentence(auth_client):
    from agentic_eval.core_evals.fi_utils.exceptions import CustomCodeEvalPausedError
    from sdk.utils.evaluations import StandaloneEvaluationError

    refusal = StandaloneEvaluationError(CustomCodeEvalPausedError(SENTENCE))
    with (
        patch("sdk.views.evaluations._run_protect", side_effect=refusal),
        patch("sdk.views.evaluations.track_mixpanel_event"),
    ):
        response = auth_client.post(
            "/sdk/api/v1/eval/",
            {"inputs": [{"input": "hello"}], "config": {"7": {}}},
            format="json",
        )
    assert_paused(response)
    assert SENTENCE in body(response)


@pytest.mark.django_db
def test_any_other_failure_keeps_the_generic_500(auth_client):
    from sdk.utils.evaluations import StandaloneEvaluationError

    with (
        patch(
            "sdk.views.evaluations._run_protect",
            side_effect=StandaloneEvaluationError(ValueError("boom")),
        ),
        patch("sdk.views.evaluations.track_mixpanel_event"),
    ):
        response = auth_client.post(
            "/sdk/api/v1/eval/",
            {"inputs": [{"input": "hello"}], "config": {"7": {}}},
            format="json",
        )
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "boom" not in body(response)
