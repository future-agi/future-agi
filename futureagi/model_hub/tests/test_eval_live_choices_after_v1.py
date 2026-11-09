"""Choices edited after V1 keep scoring evaluations while every switch is OFF.

The template editor (EvalTemplateUpdateView in model_hub/views/separate_evals.py)
updates choices, choice_scores and multi_choice in place and creates V1 only on
the first publish, so a template can carry a V1 whose saved choices differ from
its live ones. Unpinned dataset bindings and Observe evaluations resolve that
default version to stamp the run and apply its prompt, as at the 708J base
4952674379, and keep scoring with the live choices. With every switch OFF a
pinned binding does too: the dataset drawer keeps its Default and numbered
version choices as at int6 (D19-2), and a pin chosen there, or saved before this
release, selects the version's prompt but not its choices. Only the
DSX_EVAL_SAVED_CHOICES_ENABLED switch (the dataset.evalVersions ON state) runs a
pinned version's saved choices (historical execution).
"""

import uuid
from types import SimpleNamespace

import pytest

from evaluations.engine.runner import EvalRequest, run_eval
from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import Row
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.tests.test_dsx_l4_experiment_eval_pin import _world
from model_hub.views.eval_runner import EvaluationRunner

V1 = {
    "choices": ["good", "bad"],
    "choice_scores": {"good": 0.2, "bad": 0.0},
    "multi_choice": False,
}
LIVE = {
    "choices": ["good", "bad", "partial"],
    "choice_scores": {"good": 0.9, "bad": 0.0, "partial": 0.5},
    "multi_choice": True,
}


def _judge_class(built):
    class Judge:
        """Records its configuration and returns one verdict; calls no model."""

        def __init__(self, **config):
            built.append(config)

        def run(self, **params):
            return SimpleNamespace(
                eval_results=[{"data": "good", "failure": False, "reason": "ok"}]
            )

    return Judge


@pytest.fixture
def edited_after_v1(auth_client, organization, workspace):
    """A user template published as V1, then edited in place by the editor."""
    template = EvalTemplate.no_workspace_objects.create(
        name=f"live-choices-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="llm",
        model="turing_large",
        criteria="Grade {{output}}",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "choices",
            "required_keys": ["output"],
            "choices": V1["choices"],
            "choice_scores": V1["choice_scores"],
        },
        choices=V1["choices"],
        choice_scores=V1["choice_scores"],
        multi_choice=V1["multi_choice"],
    )
    url = f"/model-hub/eval-templates/{template.id}/update/"
    response = auth_client.put(url, {"publish": True}, format="json")
    assert response.status_code == 200, response.content
    v1 = EvalTemplateVersion.objects.get(eval_template=template)
    response = auth_client.put(
        url,
        {
            "choice_scores": LIVE["choice_scores"],
            "multi_choice": LIVE["multi_choice"],
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    template.refresh_from_db()
    # The edit changed the live definition in place and created no version.
    assert template.choices == LIVE["choices"]
    assert template.choice_scores == LIVE["choice_scores"]
    assert template.multi_choice is True
    assert list(EvalTemplateVersion.objects.filter(eval_template=template)) == [v1]
    assert v1.config_snapshot["choice_scores"] == V1["choice_scores"]
    return template, v1


def _dataset_runner(template, metric):
    """The state EvaluationRunner._initialize_eval_metric leaves for a binding."""
    runner = EvaluationRunner.__new__(EvaluationRunner)
    runner.eval_template = template
    runner.user_eval_metric = metric
    runner.user_eval_metric_id = metric.id
    runner._resolved_version = EvalTemplateVersion.objects.resolve_for_metric(metric)
    runner.version_number = runner._resolved_version.version_number
    runner.futureagi_eval = False
    runner.eval_class = None
    runner.criteria = None
    runner.organization_id = template.organization_id
    runner.workspace_id = template.workspace_id
    return runner


@pytest.mark.django_db
@pytest.mark.parametrize("binding", ["unpinned", "pinned-v1"])
@pytest.mark.parametrize("path", ["evaluator", "error-config", "cell-result"])
def test_dataset_binding_scores_with_live_choices_unless_it_pins_a_version(
    edited_after_v1, organization, workspace, user, settings, binding, path
):
    # The dataset.evalVersions ON state: the deployment enabled historical
    # execution. With every switch OFF see the D19-2 OFF-state test below.
    settings.DSX_EVAL_SAVED_CHOICES_ENABLED = True
    template, v1 = edited_after_v1
    world = _world(organization, workspace, user, "live-choices")
    metric = UserEvalMetric.no_workspace_objects.create(
        name=f"live-choices-{binding}",
        dataset=world["dataset"],
        template=template,
        organization=organization,
        workspace=workspace,
        pinned_version=v1 if binding == "pinned-v1" else None,
        config={"mapping": {"output": str(world["column"].id)}},
    )
    runner = _dataset_runner(template, metric)
    # Both bindings resolve V1: the pin, else the template's only (default) version.
    assert runner._resolved_version == v1
    expected = V1 if binding == "pinned-v1" else LIVE
    if path == "evaluator":
        built = []
        runner._create_eval_instance(
            config={}, eval_class=_judge_class(built), model="turing_large"
        )
        (config,) = built
        assert config["output_type"] == "choices"
        assert config["choices"] == expected["choices"]
        assert config["choice_scores"] == expected["choice_scores"]
        assert config["multi_choice"] is expected["multi_choice"]
    elif path == "error-config":
        config = runner._prepare_eval_config({}, model="turing_large")
        assert config["choices"] == expected["choices"]
        assert config["choice_scores"] == expected["choice_scores"]
    else:
        row = Row.no_workspace_objects.get(dataset=world["dataset"])
        value = runner.format_output({"output": "choices", "data": "good"}, row=row)
        assert value == {"score": expected["choice_scores"]["good"], "choice": "good"}


@pytest.mark.django_db
def test_observe_eval_scores_with_the_live_choices(
    edited_after_v1, organization, workspace, monkeypatch
):
    template, _v1 = edited_after_v1
    built = []
    monkeypatch.setattr(
        "evaluations.engine.runner.get_eval_class",
        lambda eval_type_id: _judge_class(built),
    )
    # Few-shot retrieval reads stored feedback through the embedding store.
    monkeypatch.setattr(
        "evaluations.engine.params._get_few_shot_examples", lambda **kwargs: []
    )
    # The request tracer/utils/eval.py builds for a span, trace or session
    # evaluation: no version_number.
    result = run_eval(
        EvalRequest(
            eval_template=template,
            inputs={"output": "an answer"},
            model="turing_large",
            kb_id=None,
            runtime_config={"mapping": {"output": "output"}},
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
        )
    )
    (config,) = built
    assert config["output_type"] == "choices"
    assert config["choices"] == LIVE["choices"]
    assert config["choice_scores"] == LIVE["choice_scores"]
    assert config["multi_choice"] is True
    assert result.value == {"score": 0.9, "choice": "good"}


def _assert_scores_with(runner, world, path, expected):
    """The evaluator config, error config or cell result scores with ``expected``."""
    if path == "evaluator":
        built = []
        runner._create_eval_instance(
            config={}, eval_class=_judge_class(built), model="turing_large"
        )
        (config,) = built
        assert config["output_type"] == "choices"
        assert config["choices"] == expected["choices"]
        assert config["choice_scores"] == expected["choice_scores"]
        assert config["multi_choice"] is expected["multi_choice"]
    elif path == "error-config":
        config = runner._prepare_eval_config({}, model="turing_large")
        assert config["choices"] == expected["choices"]
        assert config["choice_scores"] == expected["choice_scores"]
    else:
        row = Row.no_workspace_objects.get(dataset=world["dataset"])
        value = runner.format_output({"output": "choices", "data": "good"}, row=row)
        assert value == {"score": expected["choice_scores"]["good"], "choice": "good"}


def _pin_with_the_legacy_version_select(auth_client, world, template, metric, version):
    """Pin ``version`` with the request the int6 dataset drawer sends.

    D19-2 keeps the drawer's Default and numbered version choices as at int6
    with every switch OFF; Update posts the choice to edit_and_run_user_eval.
    """
    response = auth_client.post(
        f"/model-hub/develops/{world['dataset'].id}"
        f"/edit_and_run_user_eval/{metric.id}/",
        {
            "name": metric.name,
            "template_id": str(template.id),
            "model": "gpt-4o-mini",
            "run": False,
            "config": {"mapping": {"output": str(world["column"].id)}, "config": {}},
            "pinned_version_id": str(version.id),
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    metric.refresh_from_db()
    assert metric.pinned_version_id == version.id


@pytest.mark.unit
def test_the_saved_choices_switch_is_off_by_default():
    from django.conf import settings

    assert getattr(settings, "DSX_EVAL_SAVED_CHOICES_ENABLED", None) is False


@pytest.mark.django_db
@pytest.mark.parametrize("binding", ["unpinned", "existing-pin", "legacy-select-v1"])
@pytest.mark.parametrize("path", ["evaluator", "error-config", "cell-result"])
def test_every_switch_off_scores_every_dataset_binding_with_live_choices(
    edited_after_v1, auth_client, organization, workspace, user, binding, path
):
    """D19-2 OFF state: the legacy version controls pin as at int6, choices stay live."""
    template, v1 = edited_after_v1
    world = _world(organization, workspace, user, "switch-off")
    metric = UserEvalMetric.no_workspace_objects.create(
        name=f"switch-off-{binding}",
        dataset=world["dataset"],
        template=template,
        organization=organization,
        workspace=workspace,
        # A pin saved before this release (the drawer's version selector, the
        # automatic maybe_pin_new_version pin, the experiments L4 pin).
        pinned_version=v1 if binding == "existing-pin" else None,
        config={"mapping": {"output": str(world["column"].id)}},
    )
    if binding == "legacy-select-v1":
        _pin_with_the_legacy_version_select(auth_client, world, template, metric, v1)
    runner = _dataset_runner(template, metric)
    # Every binding resolves V1 (its pin, else the only and default version), so
    # V1's prompt runs; V1's saved choices do not.
    assert runner._resolved_version == v1
    _assert_scores_with(runner, world, path, LIVE)
