"""Creating an optimisation run takes stored environment names and plain versions.

The model Optimize drawer sends the environment as its number, 1 to 4; that
is taken too and stored as its text, as before. Other text is refused with
the existing validation error envelope and the stock field errors, and no run
is stored. An ordinary request is still created with its values unchanged.
"""

import uuid

import pytest
from rest_framework import status

from model_hub.models.ai_model import AIModel
from model_hub.models.metric import Metric
from model_hub.models.optimize_dataset import OptimizeDataset


@pytest.fixture
def valid_metrics(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.check_valid_metrics",
        lambda *args, **kwargs: (True, ""),
    )


def _create_model(organization, workspace):
    return AIModel.all_objects.create(
        user_model_id=f"optimise-model-{uuid.uuid4()}",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )


def _create_metric(model):
    return Metric.objects.create(
        name=f"Metric {uuid.uuid4()}",
        text_prompt="Score the output.",
        criteria_breakdown=["Score the output."],
        model=model,
        metric_type=Metric.MetricTypes.WHOLE_USER_OUTPUT,
        evaluation_type=Metric.EvalMetricTypes.EVAL_PROMPT_TEMPLATE,
    )


def _create_payload(model, metric, **overrides):
    return {
        "name": f"Right answer run {uuid.uuid4()}",
        "start_date": "2026-01-01T00:00:00",
        "end_date": "2026-01-02T00:00:00",
        "model": str(model.id),
        "optimize_type": OptimizeDataset.OptimizeType.RIGHT_ANSWER,
        "environment": OptimizeDataset.EnvTypes.TRAINING,
        "version": "v-1.2",
        "metrics": [str(metric.id)],
        **overrides,
    }


def _assert_refused_without_a_run(response, payload, field, message):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"] == {field: [message]}
    assert not OptimizeDataset.no_workspace_objects.filter(
        name=payload["name"]
    ).exists()


def _assert_ordinary_request_is_created(auth_client, model, metric):
    payload = _create_payload(model, metric)
    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/", payload, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    created = OptimizeDataset.no_workspace_objects.get(id=response.json()["data"]["id"])
    assert created.environment == OptimizeDataset.EnvTypes.TRAINING
    assert created.version == "v-1.2"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "environment",
    ["staging-eu", "production", "", 5],
    ids=["unknown-name", "lowercase-name", "blank", "number-outside-the-four"],
)
def test_optimisation_create_refuses_an_environment_outside_the_stored_names(
    auth_client, organization, workspace, valid_metrics, environment
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    payload = _create_payload(model, metric, environment=environment)

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/", payload, format="json"
    )

    _assert_refused_without_a_run(
        response, payload, "environment", f'"{environment}" is not a valid choice.'
    )
    _assert_ordinary_request_is_created(auth_client, model, metric)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("environment", "stored"),
    [(3, "3"), ("3", "3"), (OptimizeDataset.EnvTypes.PRODUCTION, "Production")],
    ids=["number", "number-text", "name"],
)
def test_optimisation_create_takes_the_environment_the_drawer_sends(
    auth_client, organization, workspace, valid_metrics, environment, stored
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    payload = _create_payload(model, metric, environment=environment)

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/", payload, format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    created = OptimizeDataset.no_workspace_objects.get(id=response.json()["data"]["id"])
    assert created.optimize_type == OptimizeDataset.OptimizeType.RIGHT_ANSWER
    assert created.environment == stored
    assert created.version == "v-1.2"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("version", "message"),
    [
        ("v2 (Dana's)", "This value does not match the required pattern."),
        ("v 1", "This value does not match the required pattern."),
        ("v" * 65, "This value does not match the required pattern."),
        ("", "This field may not be blank."),
    ],
    ids=["punctuation", "space", "too-long", "blank"],
)
def test_optimisation_create_refuses_a_version_outside_the_plain_pattern(
    auth_client, organization, workspace, valid_metrics, version, message
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    payload = _create_payload(model, metric, version=version)

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/", payload, format="json"
    )

    _assert_refused_without_a_run(response, payload, "version", message)
    _assert_ordinary_request_is_created(auth_client, model, metric)
