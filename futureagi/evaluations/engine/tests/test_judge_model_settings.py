"""An LLM-as-a-judge evaluator is built only with the settings found for its model.

The dataset runner builds the evaluator class a binding's pinned version
names, so a template that now runs as an agent evaluator can still build an
LLM-as-a-judge evaluator from a version saved before the switch. That
evaluator is built only with the key (or managed route) found for its own
model, as one built from the template's current type is, and only when a
model is set: otherwise the evaluation is refused before an evaluator exists
and no model is called. The real engine prepares each evaluator; only the
model transports are replaced (see test_version_model_needs_its_own_key).
Every key here is synthetic.
"""

import uuid

import pytest

from evaluations.engine.registry import get_eval_class
from evaluations.engine.tests.test_version_model_needs_its_own_key import (
    OTHER_MODEL,
    STORED_KEY,
    asked_models,
    assert_no_gateway_or_model_library_call,
    make_template,
    make_version,
    transports,
)
from model_hub.models.api_key import ApiKey
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.services import model_route
from model_hub.views.eval_runner import EvaluationRunner

NOT_CONFIGURED = "API key not configured for openai"
NO_MODEL = "no model set"


def agent_template_with_a_judge_version(organization, workspace, model):
    """A template that now runs as an agent evaluator. Its version was saved
    while it was LLM-as-a-judge on ``model``; switching the template's type
    keeps that version."""
    template = make_template(organization, workspace, model=model)
    version = make_version(template, model=model)
    template.eval_type = "agent"
    template.config = {**template.config, "eval_type_id": "AgentEvaluator"}
    template.save()
    return template, version


def pinned_dataset_runner(template, version, organization, workspace, user, model):
    """The runner of a dataset evaluation on ``model`` that pins ``version``."""
    dataset = Dataset.no_workspace_objects.create(
        name=f"judge-settings-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    column = Column.no_workspace_objects.create(
        name="output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    metric = UserEvalMetric.objects.create(
        name=f"judge-settings-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        user=user,
        model=model,
        pinned_version=version,
        config={"mapping": {"output": str(column.id)}},
    )
    runner = EvaluationRunner(user_eval_metric_id=metric.id)
    runner.load_user_eval_metric()
    # A live pin runs its version (EvaluationRunner._initialize_eval_metric).
    runner.version_number = version.version_number
    return runner, metric


@pytest.mark.django_db
def test_a_judge_version_of_an_agent_template_is_refused_without_stored_model_settings(
    organization, workspace, user
):
    """The binding's version names the LLM-as-a-judge evaluator although the
    template now runs as an agent evaluator, and no key is stored for its
    model: the evaluation is refused before an evaluator is built, and no
    model is called."""
    template, version = agent_template_with_a_judge_version(
        organization, workspace, OTHER_MODEL
    )
    runner, metric = pinned_dataset_runner(
        template, version, organization, workspace, user, OTHER_MODEL
    )
    # The version decides the evaluator; the template's type does not.
    assert template.config["eval_type_id"] == "AgentEvaluator"
    assert runner.eval_class is get_eval_class("CustomPromptEvaluator")

    with model_route.managed_routing_port(None), transports() as seams:
        with pytest.raises(ValueError, match=NOT_CONFIGURED):
            # The call each dataset cell makes first (EvaluationRunner._run_evaluation).
            runner._create_eval_instance(config={}, model=metric.model)

    assert asked_models(seams) == [OTHER_MODEL]
    # Building an evaluator fetches the gateway client; none was built.
    assert seams.get_gateway.call_count == 0
    assert seams.turing.call_count == 0
    assert_no_gateway_or_model_library_call(seams)


@pytest.mark.django_db
def test_a_judge_version_of_an_agent_template_runs_with_its_models_key(
    organization, workspace, user
):
    """The same binding with a key stored for the judge model: the evaluator
    is built with that key and that model's provider."""
    template, version = agent_template_with_a_judge_version(
        organization, workspace, OTHER_MODEL
    )
    ApiKey.objects.create(
        organization=organization,
        workspace=workspace,
        provider="openai",
        key=STORED_KEY,
    )
    runner, metric = pinned_dataset_runner(
        template, version, organization, workspace, user, OTHER_MODEL
    )

    with transports() as seams:
        evaluator = runner._create_eval_instance(config={}, model=metric.model)

    assert asked_models(seams) == [OTHER_MODEL]
    assert evaluator._model == OTHER_MODEL
    assert evaluator.api_key
    assert evaluator.provider == "openai"


@pytest.mark.django_db
def test_a_judge_version_with_no_model_is_refused(organization, workspace, user):
    """The binding, the template and its version set no model, and the
    version names the LLM-as-a-judge evaluator: the evaluation is refused
    before an evaluator is built, and no model is called."""
    template, version = agent_template_with_a_judge_version(organization, workspace, "")
    runner, metric = pinned_dataset_runner(
        template, version, organization, workspace, user, ""
    )
    assert runner.eval_class is get_eval_class("CustomPromptEvaluator")

    with model_route.managed_routing_port(None), transports() as seams:
        with pytest.raises(ValueError, match=NO_MODEL):
            runner._create_eval_instance(config={}, model=metric.model)

    assert seams.get_gateway.call_count == 0
    assert seams.turing.call_count == 0
    assert_no_gateway_or_model_library_call(seams)
