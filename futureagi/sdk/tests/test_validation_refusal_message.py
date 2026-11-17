"""A refused SDK request says why in its message.

The SDK routes answer a request that fails validation with 400 and the
platform's error body. Its message (and detail, error and result, which carry
the same text) is the first field error, after the field it is about; every
field error stays, as before, under details and errors. A field named
non_field_errors is not a field: its sentence reads alone, or after the field
that holds it. A refusal with no field error still says "Validation failed".
"""

from uuid import uuid4

import pytest

from model_hub.models.evals_metric import EvalTemplate
from sdk.utils.api_errors import sdk_validation_error_response
from tracer.models.project import Project

VALIDATION_FAILED = "Validation failed"


def refusal(message, field_errors, attr=None):
    """The whole 400 body of a refused SDK request."""
    body = {
        "status": False,
        "type": "validation_error",
        "code": "invalid",
        "detail": message,
        "message": message,
        "error": message,
        "result": message,
        "details": field_errors,
        "errors": field_errors,
    }
    if attr is not None:
        body["attr"] = attr
    return body


@pytest.fixture
def output_template(user):
    return EvalTemplate.objects.create(
        name=f"refusal-message-{uuid4().hex}",
        organization=None,
        workspace=None,
        owner="system",
        config={
            "eval_type_id": "TestEval",
            "required_keys": ["output"],
            "output": "score",
        },
    )


@pytest.mark.django_db
def test_configure_with_an_unknown_template_says_so_in_the_message(auth_client):
    name = f"no-such-template-{uuid4().hex}"
    response = auth_client.post(
        "/sdk/api/v1/configure-evaluations/",
        {
            "eval_config": {"eval_templates": name, "inputs": {"input": "x"}},
            "platform": "langfuse",
        },
        format="json",
    )

    sentence = f"Evaluation template with name :{name} not found."
    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal(
        f"eval_config: {sentence}",
        {"eval_config.non_field_errors": [sentence]},
        attr="eval_config.non_field_errors",
    )


@pytest.mark.django_db
def test_pipeline_with_an_unknown_template_says_so_in_the_message(
    auth_client, organization, workspace
):
    project = Project.objects.create(
        name=f"refusal-message-{uuid4().hex}",
        organization=organization,
        workspace=workspace,
        trace_type="observe",
        model_type="GenerativeLLM",
    )
    name = f"no-such-template-{uuid4().hex}"
    response = auth_client.post(
        "/sdk/api/v1/evaluate-pipeline/",
        {
            "project_name": project.name,
            "version": "v1",
            "eval_data": [{"eval_template": name, "inputs": {"input": ["x"]}}],
        },
        format="json",
    )

    sentence = f"Evaluation template with name '{name}' not found."
    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal(
        f"eval_data.0.eval_template: {sentence}",
        {"eval_data.0.eval_template": [sentence]},
        attr="eval_data.0.eval_template",
    )


@pytest.mark.django_db
def test_new_eval_with_an_unexpected_input_says_so_in_the_message(
    auth_client, output_template
):
    response = auth_client.post(
        "/sdk/api/v1/new-eval/",
        {
            "eval_name": output_template.name,
            "inputs": {"output": "a reply", "answer": "an extra input"},
        },
        format="json",
    )

    sentence = "Unexpected keys provided: answer. Accepted keys are: output"
    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal(
        f"inputs: {sentence}", {"inputs": [sentence]}, attr="inputs"
    )


@pytest.mark.django_db
def test_new_eval_with_an_unknown_template_says_so_in_the_message(auth_client):
    name = f"no-such-template-{uuid4().hex}"
    response = auth_client.post(
        "/sdk/api/v1/new-eval/",
        {"eval_name": name, "inputs": {"output": "a reply"}},
        format="json",
    )

    sentence = f"Evaluation template with name :{name} not found."
    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal(sentence, {"non_field_errors": [sentence]})


@pytest.mark.parametrize(
    "errors, message, field_errors",
    [
        (
            {"non_field_errors": ["Not found."]},
            "Not found.",
            {"non_field_errors": ["Not found."]},
        ),
        (
            {"eval_config": {"non_field_errors": ["Not found."]}},
            "eval_config: Not found.",
            {"eval_config.non_field_errors": ["Not found."]},
        ),
        (
            {"eval_data": [{"eval_template": ["Not found."]}, {}]},
            "eval_data.0.eval_template: Not found.",
            {"eval_data.0.eval_template": ["Not found."]},
        ),
        (
            {"eval_data": [{}, {"non_field_errors": ["Not found."]}]},
            "eval_data.1: Not found.",
            {"eval_data.1.non_field_errors": ["Not found."]},
        ),
        (
            {"eval_name": ["This field is required."], "answer": ["Unknown field."]},
            "eval_name: This field is required.",
            {"eval_name": ["This field is required."], "answer": ["Unknown field."]},
        ),
        (
            {"inputs": ["First sentence.", "Second sentence."]},
            "inputs: First sentence.",
            {"inputs": ["First sentence.", "Second sentence."]},
        ),
        (
            {"inputs": [], "config": [" ", "Bad config."]},
            "config: Bad config.",
            {"inputs": [], "config": [" ", "Bad config."]},
        ),
    ],
    ids=[
        "non-field-alone",
        "non-field-of-a-field",
        "nested-field",
        "non-field-of-an-item",
        "first-of-several-fields",
        "first-of-several-sentences",
        "first-that-has-a-sentence",
    ],
)
def test_the_message_is_the_first_field_error(errors, message, field_errors):
    body = sdk_validation_error_response(errors).data
    assert [body[key] for key in ("detail", "message", "error", "result")] == [
        message
    ] * 4
    assert body["details"] == body["errors"] == field_errors
    assert (body["status"], body["type"], body["code"]) == (
        False,
        "validation_error",
        "invalid",
    )


@pytest.mark.parametrize(
    "errors",
    [{}, {"inputs": []}, {"inputs": [""]}, {"inputs": ["  "], "config": []}],
    ids=["no-errors", "empty-field", "blank-sentence", "only-blank-or-empty"],
)
def test_a_refusal_with_no_field_error_keeps_its_message(errors):
    response = sdk_validation_error_response(errors)
    body = response.data
    assert response.status_code == 400
    assert [body[key] for key in ("detail", "message", "error", "result")] == [
        VALIDATION_FAILED
    ] * 4
    assert body["errors"] == errors
