"""Each run path builds its evaluator from the version its binding pins.

The real runner and engine run; only the evaluator class is replaced, at the
point the engine builds it, so nothing calls a model. Unpinned runs, and runs
whose pinned version was deleted, keep the live template.
"""

import contextlib
import uuid
from unittest.mock import patch

import pytest

from evaluations.engine import instance
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)

LIVE_RUBRIC = "Live rubric: is {{output}} polite?"
LIVE_SYSTEM = "Live system message"
PINNED_RUBRIC = "Pinned rubric: is {{output}} correct?"
PINNED_SYSTEM = "Pinned system message"
DEFAULT_RUBRIC = "Default rubric: is {{output}} short?"
DEFAULT_SYSTEM = "Default system message"


class Built:
    """Records what the engine built the evaluator with. Never runs a model."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def run(self, *args, **kwargs):
        raise AssertionError("the evaluator is never run in these tests")


class Stop(Exception):
    """Ends the run once the evaluator is built."""


@contextlib.contextmanager
def built_evaluators(*, stop=False, target="evaluations.engine.instance"):
    """Swap only the evaluator class inside the real create_eval_instance."""
    real = instance.create_eval_instance
    seen = []

    def build(eval_class, *args, **kwargs):
        built, criteria = real(Built, *args, **kwargs)
        seen.append(built.kwargs)
        if stop:
            raise Stop()
        return built, criteria

    with patch(f"{target}.create_eval_instance", side_effect=build):
        yield seen


def make_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name=f"pinned-run-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="llm",
        criteria=LIVE_RUBRIC,
        model="turing_small",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "Pass/Fail",
            "required_keys": ["output"],
            "rule_prompt": LIVE_RUBRIC,
            "system_prompt": LIVE_SYSTEM,
            "model": "turing_small",
        },
    )


def make_version(
    template, number, rubric, system, *, default=False, model="turing_small"
):
    return EvalTemplateVersion.no_workspace_objects.create(
        eval_template=template,
        version_number=number,
        is_default=default,
        prompt_messages=[
            {"role": "system", "content": system},
            {"role": "user", "name": "eval_prompt", "content": rubric},
        ],
        config_snapshot=dict(template.config),
        criteria=rubric,
        model=model,
        organization=template.organization,
        workspace=template.workspace,
    )


@pytest.fixture
def world(organization, workspace, user):
    template = make_template(organization, workspace)
    pinned = make_version(template, 1, PINNED_RUBRIC, PINNED_SYSTEM)
    default = make_version(template, 2, DEFAULT_RUBRIC, DEFAULT_SYSTEM, default=True)
    dataset = Dataset.no_workspace_objects.create(
        name=f"pinned-run-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    return {
        "org": organization,
        "workspace": workspace,
        "user": user,
        "template": template,
        "pinned": pinned,
        "default": default,
        "dataset": dataset,
    }


def metric_for(world, pinned_version=None):
    return UserEvalMetric.no_workspace_objects.create(
        name=f"pinned-run-{uuid.uuid4().hex[:8]}",
        organization=world["org"],
        workspace=world["workspace"],
        dataset=world["dataset"],
        template=world["template"],
        pinned_version=pinned_version,
        model="turing_small",
        config={"mapping": {"output": "output"}},
        user=world["user"],
    )


def classic_build(metric, source):
    """The classic dataset and experiment runner up to the engine call."""
    from model_hub.views.eval_runner import EvaluationRunner

    runner = EvaluationRunner(
        user_eval_metric_id=metric.id,
        is_only_eval=True,
        source=source,
        source_id=str(metric.template_id),
        source_configs={"dataset_id": str(metric.dataset_id)},
    )
    runner.eval_template = metric.template
    with built_evaluators() as seen:
        runner._create_eval_instance(config={}, model=metric.model)
    (built,) = seen
    return built


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["dataset_evaluation", "experiment"])
def test_classic_run_builds_the_pinned_version(world, source):
    metric = metric_for(world, world["pinned"])

    built = classic_build(metric, source)

    assert built["rule_prompt"] == PINNED_RUBRIC
    assert built["system_prompt"] == PINNED_SYSTEM


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["dataset_evaluation", "experiment"])
def test_unpinned_classic_run_keeps_the_live_template_not_the_default(world, source):
    metric = metric_for(world)

    built = classic_build(metric, source)

    assert built["rule_prompt"] == LIVE_RUBRIC
    assert built["system_prompt"] == LIVE_SYSTEM


@pytest.mark.django_db
def test_a_deleted_pin_runs_the_live_template_not_the_default(world):
    metric = metric_for(world, world["pinned"])
    EvalTemplateVersion.all_objects.filter(pk=world["pinned"].pk).update(deleted=True)

    built = classic_build(metric, "experiment")

    assert built["rule_prompt"] == LIVE_RUBRIC
    assert built["system_prompt"] == LIVE_SYSTEM


@pytest.mark.django_db
def test_sdk_evaluate_keeps_the_live_template(world):
    from evaluations.engine import EvalRequest, run_eval

    with built_evaluators(stop=True, target="evaluations.engine.runner") as seen:
        with pytest.raises(Stop):
            run_eval(
                EvalRequest(
                    eval_template=world["template"],
                    inputs={"output": "an answer"},
                    organization_id=str(world["org"].id),
                    workspace_id=str(world["workspace"].id),
                )
            )

    (built,) = seen
    assert built["rule_prompt"] == LIVE_RUBRIC
    assert built["system_prompt"] == LIVE_SYSTEM


@pytest.mark.django_db
def test_saved_eval_test_keeps_the_live_template(world):
    from model_hub.views.utils.evals import run_eval_func

    with built_evaluators(stop=True) as seen:
        with contextlib.suppress(Exception):
            run_eval_func(
                {"mapping": {"output": "an answer"}, "config": {}},
                {"output": "an answer"},
                world["template"],
                world["org"],
                model=None,
                source="eval_playground_test",
                workspace=world["workspace"],
            )

    (built,) = seen
    assert built["rule_prompt"] == LIVE_RUBRIC
    assert built["system_prompt"] == LIVE_SYSTEM


def composite_link(world, *, link_config=None):
    parent = EvalTemplate.no_workspace_objects.create(
        name=f"pinned-composite-{uuid.uuid4().hex[:8]}",
        organization=world["org"],
        workspace=world["workspace"],
        owner="user",
        eval_type="llm",
        template_type="composite",
        config={"eval_type_id": "CustomPromptEvaluator"},
    )
    return CompositeEvalChild.no_workspace_objects.create(
        parent=parent,
        child=world["template"],
        order=0,
        weight=1.0,
        pinned_version=world["pinned"],
        config=link_config,
    )


def run_child(world, link):
    from model_hub.utils.composite_execution import _execute_child

    with built_evaluators(stop=True) as seen:
        _execute_child(
            child_template=world["template"],
            link=link,
            weight=1.0,
            mapping={"output": "an answer"},
            config={},
            model=None,
            org=world["org"],
            workspace=world["workspace"],
            input_data_types={},
            row_context=None,
            span_context=None,
            trace_context=None,
            session_context=None,
            call_context=None,
            error_localizer=False,
            source="composite_eval",
        )
    (built,) = seen
    return built


@pytest.mark.django_db
def test_a_pinned_composite_child_runs_its_version(world):
    built = run_child(world, composite_link(world))

    assert built["rule_prompt"] == PINNED_RUBRIC
    assert built["system_prompt"] == PINNED_SYSTEM


@pytest.mark.django_db
def test_a_pinned_jury_seat_runs_its_version_on_the_seat_model(world):
    link = composite_link(world, link_config={"run_config": {"model": "turing_flash"}})

    built = run_child(world, link)

    assert built["rule_prompt"] == PINNED_RUBRIC
    assert built["model"] == "turing_flash"


@pytest.mark.django_db
def test_a_composite_child_whose_pin_was_deleted_runs_the_live_template(world):
    # The pinned version names another model; once deleted, nothing of it runs.
    EvalTemplateVersion.all_objects.filter(pk=world["pinned"].pk).update(
        model="turing_flash", deleted=True
    )
    link = composite_link(world)
    link.refresh_from_db()

    built = run_child(world, link)

    assert built["rule_prompt"] == LIVE_RUBRIC
    assert built["system_prompt"] == LIVE_SYSTEM
    assert built["model"] == "turing_small"


@pytest.mark.django_db
@pytest.mark.parametrize("lookup", ["no-key", "routing-as-shipped"])
def test_classic_runner_refuses_a_pinned_model_without_a_key_before_charging(
    world, lookup
):
    """ "routing-as-shipped" is the real lookup with no stored key and the
    managed routing port as the line ships it (none registered)."""
    from model_hub.views.eval_runner import EvaluationRunner

    version = make_version(
        world["template"], 3, PINNED_RUBRIC, PINNED_SYSTEM, model="gpt-4o"
    )
    metric = metric_for(world, version)
    runner = EvaluationRunner(
        user_eval_metric_id=metric.id,
        is_only_eval=True,
        source="experiment",
        source_id=str(metric.template_id),
        source_configs={"dataset_id": str(metric.dataset_id)},
    )
    runner.eval_template = metric.template
    preflight = getattr(runner, "_refuse_pinned_model_without_key", None)
    assert preflight is not None, "no refusal before the up-front debit"

    def no_key(model, *args, **kwargs):
        raise ValueError("No API key found for organization")

    key_lookup = (
        patch.object(instance, "_get_api_key", side_effect=no_key)
        if lookup == "no-key"
        else patch.object(instance, "_get_api_key", wraps=instance._get_api_key)
    )
    with key_lookup as lookups:
        with pytest.raises(ValueError) as refused:
            preflight()
    asked = [call.args[0] for call in lookups.call_args_list]

    assert str(refused.value) == (
        "This evaluation's saved version uses a different model than its key "
        "allows. Pick a key for that model or unpin the version."
    )
    assert asked == ["gpt-4o"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "pinned,expected",
    [(True, "protect_flash"), (False, "turing_small")],
    ids=["pinned-protect-on-a-turing-card", "unpinned-keeps-the-card"],
)
def test_classic_usage_limit_is_checked_for_the_model_that_runs(
    world, pinned, expected
):
    """run_prompt's up-front usage limit goes by the model that runs; an
    unpinned metric keeps the card's tier although its default names another."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from model_hub.tests.test_native_prompt_create_bridge import native_preflight
    from model_hub.views.eval_runner import EvaluationRunner
    from sdk.utils.helpers import _get_api_call_type

    EvalTemplateVersion.all_objects.filter(pk=world["default"].pk).update(
        model="protect"
    )
    version = make_version(
        world["template"], 3, PINNED_RUBRIC, PINNED_SYSTEM, model="protect_flash"
    )
    metric = metric_for(world, version if pinned else None)
    runner = EvaluationRunner(
        user_eval_metric_id=metric.id,
        is_only_eval=True,
        source="dataset_evaluation",
        source_id=str(metric.template_id),
        source_configs={"dataset_id": str(metric.dataset_id)},
    )
    limit = MagicMock(return_value=SimpleNamespace(allowed=True))

    with (
        native_preflight(limit),
        patch.object(EvaluationRunner, "update_cell") as rows,
    ):
        runner.run_prompt()

    rows.assert_called_once()
    assert [call.args[1] for call in limit.call_args_list] == [
        _get_api_call_type(expected)
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "card,pinned_model,refused",
    [("turing_flash", "turing_large", False), ("turing_large", "turing_flash", True)],
    ids=["flash-card-pinned-large-not-refused", "large-card-pinned-flash-refused"],
)
def test_the_dataset_task_usage_limit_goes_by_the_pinned_model(
    world, card, pinned_model, refused
):
    """turing_flash's limit is used up. The task's up-front limit check (before
    any runner) checks the model the pin runs, not the card's."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from model_hub.tasks import user_evaluation
    from model_hub.tests.test_native_prompt_create_bridge import native_preflight
    from sdk.utils.helpers import _get_api_call_type

    version = make_version(
        world["template"], 3, PINNED_RUBRIC, PINNED_SYSTEM, model=pinned_model
    )
    metric = metric_for(world, version)
    UserEvalMetric.no_workspace_objects.filter(pk=metric.pk).update(model=card)
    metric.refresh_from_db()
    used_up = _get_api_call_type("turing_flash")
    limit = MagicMock(
        side_effect=lambda org_id, call_type: SimpleNamespace(
            allowed=call_type != used_up,
            reason="Usage limit exceeded",
            error_code="USAGE_LIMIT_EXCEEDED",
            dimension="",
            current_usage=1,
            limit=1,
            upgrade_cta=None,
        )
    )

    with (
        native_preflight(limit),
        patch.object(user_evaluation, "evaluation_tracker"),
        patch.object(user_evaluation, "track_mixpanel_event"),
        patch.object(user_evaluation, "get_mixpanel_properties", return_value={}),
        patch.object(user_evaluation, "EvaluationRunner", side_effect=Stop),
    ):
        # Stop: the check passed and the runner was about to be built.
        with pytest.raises(ValueError if refused else Stop):
            user_evaluation.process_single_evaluation(metric)

    assert [call.args[1] for call in limit.call_args_list] == [
        _get_api_call_type(pinned_model)
    ]


@pytest.mark.django_db
def test_the_task_limit_helper_keeps_unpinned_and_deleted_pins_on_the_card(world):
    from evaluations.engine import instance as engine

    helper = getattr(engine, "charged_model_for_metric", None)
    assert helper is not None, "no shared model helper for the task limit checks"
    unpinned = metric_for(world)
    gone = make_version(
        world["template"], 4, PINNED_RUBRIC, PINNED_SYSTEM, model="turing_large"
    )
    deleted = metric_for(world, gone)
    EvalTemplateVersion.all_objects.filter(pk=gone.pk).update(deleted=True)
    deleted.refresh_from_db()
    live = make_version(
        world["template"], 5, PINNED_RUBRIC, PINNED_SYSTEM, model="turing_large"
    )
    pinned = metric_for(world, live)

    assert helper(unpinned) == "turing_small"
    assert helper(deleted) == "turing_small"
    assert helper(pinned) == "turing_large"
