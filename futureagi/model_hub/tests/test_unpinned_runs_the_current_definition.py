"""An evaluation with no pinned version runs its current definition.

The template's default version holds older text and another model than the
template's current definition. An unpinned evaluation, and one whose pin was
deleted, runs the current definition as one unit: its text and its model.
The default version it resolves only stamps the run. A pinned evaluation
runs its version's text and model. Never a mix of the two.

Classic runs go through the real EvaluationRunner and engine; only the
evaluator class is replaced where the engine builds it. Experiment runs go
through admission, the worker row and the native runner with the real judge;
only its provider call is replaced, so the test reads what it sends. A
composite child and a jury seat run through the real child execution.
"""

import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from evaluations.engine import instance
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.tests.test_native_prompt_create_bridge import (
    native_fixture_evaluator as native_fixture_evaluator,
)
from model_hub.tests.test_native_run_binding import native_world as native_world
from model_hub.tests.test_pinned_version_prompt import (
    create_in_editor,
    definition,
    update,
)
from model_hub.tests.test_pinned_version_prompt_native import (
    assert_rule_turn,
    attach,
    run_one_row,
    version_of,
)
from model_hub.tests.test_pinned_version_runs import (
    LIVE_RUBRIC,
    LIVE_SYSTEM,
    built_evaluators,
    composite_link,
    run_child,
)
from model_hub.tests.test_pinned_version_runs import world as world

pytestmark = pytest.mark.django_db(transaction=True)

CURRENT = "Current rubric: is {{output}} polite?"
CURRENT_SYSTEM = "Current system message"
CURRENT_MODEL = "turing_large"
FIRST = "First version rubric: is {{output}} correct?"
FIRST_SYSTEM = "First version system message"
FIRST_MODEL = "turing_small"
SECOND = "Second version rubric: is {{output}} short?"
SECOND_SYSTEM = "Second version system message"
SECOND_MODEL = "turing_flash"

# The experiment judge reads {{input}} and {{output}} (the native harness's
# inputs).
CURRENT_JUDGE = "Current definition: is {{output}} polite about {{input}}?"
FIRST_JUDGE = "First version: does {{output}} answer {{input}}?"


def definition_config(rubric, system, model):
    return {
        "eval_type_id": "CustomPromptEvaluator",
        "output": "Pass/Fail",
        "required_keys": ["output"],
        "rule_prompt": rubric,
        "system_prompt": system,
        "model": model,
    }


def save_version(template, number, rubric, system, model, *, default=False):
    return EvalTemplateVersion.no_workspace_objects.create(
        eval_template=template,
        version_number=number,
        is_default=default,
        prompt_messages=[
            {"role": "system", "content": system},
            {"role": "user", "name": "eval_prompt", "content": rubric},
        ],
        config_snapshot=definition_config(rubric, system, model),
        criteria=rubric,
        model=model,
        organization=template.organization,
        workspace=template.workspace,
    )


@pytest.fixture
def edited(organization, workspace, user):
    """V1 is the default; the current definition was edited after it, with
    another text and model, and no version was saved."""
    template = EvalTemplate.no_workspace_objects.create(
        name=f"current-definition-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="llm",
        criteria=FIRST,
        model=FIRST_MODEL,
        config=definition_config(FIRST, FIRST_SYSTEM, FIRST_MODEL),
    )
    first = save_version(template, 1, FIRST, FIRST_SYSTEM, FIRST_MODEL, default=True)
    template.config = definition_config(CURRENT, CURRENT_SYSTEM, CURRENT_MODEL)
    template.criteria = CURRENT
    template.model = CURRENT_MODEL
    template.save(update_fields=["config", "criteria", "model"])
    dataset = Dataset.no_workspace_objects.create(
        name=f"current-definition-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    return SimpleNamespace(
        organization=organization,
        workspace=workspace,
        user=user,
        template=template,
        first=first,
        dataset=dataset,
    )


def metric_for(edited, pinned_version=None, model=CURRENT_MODEL):
    return UserEvalMetric.no_workspace_objects.create(
        name=f"current-definition-{uuid.uuid4().hex[:8]}",
        organization=edited.organization,
        workspace=edited.workspace,
        dataset=edited.dataset,
        template=edited.template,
        pinned_version=pinned_version,
        model=model,
        config={"mapping": {"output": "output"}},
        user=edited.user,
    )


def classic_run(metric, source):
    """The classic dataset and experiment runner up to the engine call."""
    from model_hub.views.eval_runner import EvaluationRunner

    runner = EvaluationRunner(
        user_eval_metric_id=metric.id,
        is_only_eval=True,
        source=source,
        source_id=str(metric.template_id),
        source_configs={"dataset_id": str(metric.dataset_id)},
    )
    runner.eval_template = EvalTemplate.no_workspace_objects.get(pk=metric.template_id)
    with built_evaluators() as seen:
        runner._create_eval_instance(config={}, model=metric.model)
    (built,) = seen
    return runner, built


# --- classic runs -----------------------------------------------------------


@pytest.mark.parametrize("source", ["dataset_evaluation", "experiment"])
def test_an_unpinned_run_runs_the_current_definition(edited, source):
    runner, built = classic_run(metric_for(edited), source)

    assert built["rule_prompt"] == CURRENT
    assert built["system_prompt"] == CURRENT_SYSTEM
    assert built["model"] == CURRENT_MODEL
    # The runner's own definition (evaluator type, keys, the error
    # localizer's config) is the current one too.
    effective = runner._get_effective_eval_config()
    assert effective["rule_prompt"] == CURRENT
    assert effective["system_prompt"] == CURRENT_SYSTEM
    assert effective["model"] == CURRENT_MODEL
    # The default version stamps the run and supplies nothing that runs.
    assert runner._resolved_version == edited.first
    assert runner.version_number is None
    assert runner._saved_choice_version() is None


@pytest.mark.parametrize("source", ["dataset_evaluation", "experiment"])
def test_a_pinned_run_runs_its_version(edited, source):
    runner, built = classic_run(metric_for(edited, edited.first), source)

    assert built["rule_prompt"] == FIRST
    assert built["system_prompt"] == FIRST_SYSTEM
    assert built["model"] == FIRST_MODEL
    effective = runner._get_effective_eval_config()
    assert effective["rule_prompt"] == FIRST
    assert effective["system_prompt"] == FIRST_SYSTEM
    assert effective["model"] == FIRST_MODEL
    assert runner._resolved_version == edited.first
    assert runner.version_number == 1


def test_a_deleted_pin_runs_the_current_definition(edited):
    second = save_version(edited.template, 2, SECOND, SECOND_SYSTEM, SECOND_MODEL)
    metric = metric_for(edited, second)
    EvalTemplateVersion.all_objects.filter(pk=second.pk).update(deleted=True)

    runner, built = classic_run(metric, "dataset_evaluation")

    assert built["rule_prompt"] == CURRENT
    assert built["system_prompt"] == CURRENT_SYSTEM
    assert built["model"] == CURRENT_MODEL
    effective = runner._get_effective_eval_config()
    assert effective["rule_prompt"] == CURRENT
    assert effective["model"] == CURRENT_MODEL
    # The deleted pin falls back to the default for the stamp only.
    assert runner._resolved_version == edited.first
    assert runner.version_number is None


# --- experiment runs ---------------------------------------------------------


@contextmanager
def judge_models():
    """The model each built judge would call."""
    real = instance.create_eval_instance
    models = []

    def build(*args, **kwargs):
        judge, criteria = real(*args, **kwargs)
        models.append(getattr(judge, "_model", None))
        return judge, criteria

    with patch.object(instance, "create_eval_instance", side_effect=build):
        yield models


def edited_in_editor(client):
    """V1 saved by the create page; then the current definition is edited in
    the editor, its text and its model, and no version is saved: V1 stays the
    default."""
    template = create_in_editor(client, FIRST_JUDGE, model=FIRST_MODEL)
    body = definition("llm", CURRENT_JUDGE, CURRENT_MODEL, None, None)
    update(client, template.id, {**body, "description": None, "tags": []})
    template.refresh_from_db()
    first = version_of(template, 1)
    assert first.is_default
    assert FIRST_JUDGE in json.dumps(first.config_snapshot)
    assert CURRENT_JUDGE not in json.dumps(first.config_snapshot)
    assert CURRENT_JUDGE in json.dumps(template.config)
    assert not EvalTemplateVersion.no_workspace_objects.filter(
        eval_template=template, version_number=2
    ).exists()
    return template


def test_an_unpinned_experiment_run_sends_the_current_definition(
    native_world, auth_client
):
    w = native_world
    template = edited_in_editor(auth_client)
    attach(w, template, None, {}, model=CURRENT_MODEL)

    with judge_models() as models:
        messages, system_message = run_one_row(w)

    assert messages[:-1] == [{"role": "system", "content": system_message}]
    assert_rule_turn(messages, CURRENT_JUDGE)
    assert "First version" not in json.dumps(messages)
    assert models == [CURRENT_MODEL]


def test_a_pinned_experiment_run_sends_its_version(native_world, auth_client):
    w = native_world
    template = edited_in_editor(auth_client)
    attach(w, template, version_of(template, 1), {}, model=CURRENT_MODEL)

    with judge_models() as models:
        messages, system_message = run_one_row(w)

    assert messages[:-1] == [{"role": "system", "content": system_message}]
    assert_rule_turn(messages, FIRST_JUDGE)
    assert "Current definition" not in json.dumps(messages)
    assert models == [FIRST_MODEL]


# --- a composite child and a jury seat ----------------------------------------


def with_older_default(world):
    """The default version names another text and model than the template."""
    default = world["default"]
    default.config_snapshot = {
        **world["template"].config,
        "rule_prompt": "Default rubric: is {{output}} short?",
        "system_prompt": "Default system message",
        "model": "turing_flash",
    }
    default.model = "turing_flash"
    default.save(update_fields=["config_snapshot", "model"])


def unpinned_link(world, *, link_config=None):
    link = composite_link(world, link_config=link_config)
    link.pinned_version = None
    link.save(update_fields=["pinned_version"])
    return link


def test_an_unpinned_composite_child_runs_the_current_definition(world):
    with_older_default(world)

    built = run_child(world, unpinned_link(world))

    assert built["rule_prompt"] == LIVE_RUBRIC
    assert built["system_prompt"] == LIVE_SYSTEM
    assert built["model"] == "turing_small"


def test_an_unpinned_jury_seat_runs_the_current_definition_on_its_model(world):
    with_older_default(world)
    seat = unpinned_link(world, link_config={"run_config": {"model": "turing_large"}})

    built = run_child(world, seat)

    assert built["rule_prompt"] == LIVE_RUBRIC
    assert built["system_prompt"] == LIVE_SYSTEM
    assert built["model"] == "turing_large"
