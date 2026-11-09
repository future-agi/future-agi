"""Per-evaluation score overrides on Choices evaluations.

Release-lead ruling (a), 10-31: every server path that turns a Choices label
into a score applies ONE rule. The binding's override applies when present
(``run_config.choice_scores``; observe and simulate bindings store it at the
top-level ``choice_scores``); otherwise the template's map applies unchanged.
An override is validated when its binding is saved: keys must be choices of
the template, values numbers between 0 and 1, and an invalid override is a
400 with a clear message.

The path tests drive the real formatter of each path. The fallback tests use
no new API, so they pass with and without the rule.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401
from tfc.middleware.workspace_context import set_workspace_context

pytestmark = pytest.mark.usefixtures("no_external_lookups")

TEMPLATE_SCORES = {"Good": 1.0, "Neutral": 0.5, "Bad": 0.0}
# A per-evaluation edit: "Good" now scores 0.25; the other labels keep the
# template's scores.
OVERRIDE = {"Good": 0.25}
EFFECTIVE = {"Good": 0.25, "Neutral": 0.5, "Bad": 0.0}
GOOD = {"output": "choices", "data": "Good"}


def _template(eval_type_id="CustomPromptEvaluator", choice_scores=TEMPLATE_SCORES):
    scores = dict(choice_scores) if choice_scores is not None else None
    return SimpleNamespace(
        id="tpl-tone",
        name="tone",
        description="",
        config={
            "output": "choices",
            "eval_type_id": eval_type_id,
            "rule_prompt": "Judge {{output}}",
            "required_keys": ["output"],
        },
        choice_scores=scores,
        choices=list(scores or {}),
        multi_choice=False,
        pass_threshold=0.5,
        output_type_normalized="deterministic",
        organization=None,
        workspace=None,
    )


def _dataset_runner(template, binding_config):
    """The dataset/experiment runner as it formats a row for a binding."""
    from model_hub.views.eval_runner import EvaluationRunner

    runner = EvaluationRunner("binding-1", format_output=True)
    runner.eval_template = template
    runner.user_eval_metric = SimpleNamespace(config=binding_config)
    return runner


def _row_none_runner(template, runtime_config):
    """The run_eval_func runner (simulate writes, playground, dataset preview,
    workbench, external evals, composite children): the binding config is the
    runtime_config handed to _create_eval_instance, then format_output(row=None).
    """
    from model_hub.views.eval_runner import EvaluationRunner

    runner = EvaluationRunner(
        template.config["eval_type_id"], format_output=True, is_only_eval=True
    )
    runner.eval_template = template
    with patch(
        "evaluations.engine.instance.create_eval_instance",
        return_value=(MagicMock(), None),
    ):
        runner._create_eval_instance(
            config={}, eval_class=MagicMock(), runtime_config=runtime_config
        )
    return runner


def _run_engine(template, runtime_config, label="Good"):
    """The unified engine (observe span/trace/session evals, SDK evaluate)."""
    from evaluations.engine import runner as engine_runner

    raw = SimpleNamespace(
        eval_results=[
            {
                "data": label,
                "failure": False,
                "reason": "judged",
                "runtime": 1,
                "model": "judge",
                "metrics": [],
                "metadata": {},
            }
        ]
    )
    instance = MagicMock()
    instance.run.return_value = raw
    with (
        patch.object(engine_runner, "get_eval_class", return_value=MagicMock()),
        patch.object(
            engine_runner, "create_eval_instance", return_value=(instance, None)
        ),
        patch.object(engine_runner, "prepare_run_params", return_value={}),
        patch(
            "evaluations.engine.preprocessing.preprocess_inputs",
            side_effect=lambda _name, params: params,
        ),
    ):
        return engine_runner.run_eval(
            engine_runner.EvalRequest(
                eval_template=template,
                inputs={"output": "a reply"},
                runtime_config=runtime_config,
            )
        )


def _clustering():
    # Importing the clustering module initializes its embedding manager; only
    # its pure score predicate is exercised here, never model serving.
    with patch(
        "agentic_eval.core.embeddings.serving_client.ModelServingClient.health_check",
        return_value=True,
    ):
        from tracer.queries import eval_clustering
    return eval_clustering


# --------------------------------------------------------------------------
# DB fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Choice Override Org")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="choice-override@example.com",
        password="testpassword123",
        name="Choice Override",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="Choice Override Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def auth_client(user, workspace):
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    set_workspace_context(workspace=workspace, organization=user.organization)
    yield client
    client.stop_workspace_injection()


@pytest.fixture
def dataset(db, organization, workspace):
    ds = Dataset.objects.create(
        name="Choice Override Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    ds.column_order = []
    ds.save()
    return ds


@pytest.fixture
def output_column(db, dataset):
    col = Column.objects.create(
        name="Output Column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order.append(str(col.id))
    dataset.save()
    return col


@pytest.fixture
def choices_template(db, organization, workspace):
    return EvalTemplate.objects.create(
        name="tone-choices",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        criteria="Judge {{output}}",
        model="gpt-4o-mini",
        output_type_normalized="deterministic",
        choices=list(TEMPLATE_SCORES),
        choice_scores=dict(TEMPLATE_SCORES),
        pass_threshold=0.5,
        config={
            "output": "choices",
            "required_keys": ["output"],
            "eval_type_id": "AgentEvaluator",
        },
    )


@pytest.fixture
def experiment(db, dataset, output_column):
    return ExperimentsTable.objects.create(
        name="Choice Override Experiment",
        dataset=dataset,
        column=output_column,
        status=StatusType.COMPLETED.value,
    )


def _binding_config(choice_scores):
    return {
        "mapping": {"output": "Output Column"},
        "config": {},
        "run_config": {"choice_scores": choice_scores},
    }


# --------------------------------------------------------------------------
# Fallback: no override, the template's map applies unchanged (passes with
# and without the rule).
# --------------------------------------------------------------------------


def test_fallback_dataset_row_uses_template_map():
    runner = _dataset_runner(
        _template(), {"mapping": {}, "run_config": {"model": "judge"}}
    )
    assert runner.format_output(dict(GOOD), row=object()) == {
        "score": 1.0,
        "choice": "Good",
    }


def test_fallback_engine_uses_template_map():
    result = _run_engine(_template(), {"mapping": {"output": "output"}})
    assert result.value == {"score": 1.0, "choice": "Good"}


def test_fallback_row_none_uses_template_map():
    template = _template()
    runner = _row_none_runner(template, {"mapping": {}})
    assert runner.format_output(dict(GOOD), eval_template=template) == {
        "score": 1.0,
        "choice": "Good",
    }


# --------------------------------------------------------------------------
# The shared rule
# --------------------------------------------------------------------------


class TestResolveChoiceScores:
    def test_no_override_returns_the_template_map_unchanged(self):
        from evaluations.engine.instance import resolve_choice_scores

        template = _template()
        for runtime_config in (None, {}, {"run_config": {}}, {"mapping": {}}):
            assert resolve_choice_scores(template, runtime_config) is (
                template.choice_scores
            )

    def test_run_config_override_applies_over_the_template_map(self):
        from evaluations.engine.instance import resolve_choice_scores

        resolved = resolve_choice_scores(
            _template(), {"run_config": {"choice_scores": OVERRIDE}}
        )
        assert resolved == EFFECTIVE

    def test_top_level_override_applies_for_observe_and_simulate_bindings(self):
        from evaluations.engine.instance import resolve_choice_scores

        assert resolve_choice_scores(_template(), {"choice_scores": OVERRIDE}) == (
            EFFECTIVE
        )

    def test_run_config_wins_over_top_level(self):
        from evaluations.engine.instance import resolve_choice_scores

        resolved = resolve_choice_scores(
            _template(),
            {
                "choice_scores": {"Good": 0.75},
                "run_config": {"choice_scores": {"Good": 0.1}},
            },
        )
        assert resolved["Good"] == 0.1

    @pytest.mark.parametrize("absent", [None, {}, [], "Good"])
    def test_an_empty_or_malformed_value_is_not_an_override(self, absent):
        from evaluations.engine.instance import resolve_choice_scores

        template = _template()
        assert (
            resolve_choice_scores(template, {"run_config": {"choice_scores": absent}})
            is template.choice_scores
        )

    def test_override_without_a_template_map(self):
        from evaluations.engine.instance import resolve_choice_scores

        template = _template(choice_scores=None)
        assert resolve_choice_scores(template, None) is None
        assert resolve_choice_scores(template, {"choice_scores": OVERRIDE}) == (
            OVERRIDE
        )


# --------------------------------------------------------------------------
# One test per path
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_dataset_path_applies_saved_binding_override(
    auth_client, dataset, output_column, choices_template
):
    """Dataset eval_runner row branch, with the binding saved by add_user_eval."""
    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        {
            "name": "tone-dataset",
            "template_id": str(choices_template.id),
            "config": _binding_config(OVERRIDE),
            "run": False,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    metric = UserEvalMetric.objects.get(name="tone-dataset", dataset=dataset)
    assert metric.config["run_config"]["choice_scores"] == OVERRIDE

    runner = _dataset_runner(metric.template, metric.config)
    assert runner.format_output(dict(GOOD), row=object()) == {
        "score": 0.25,
        "choice": "Good",
    }


@pytest.mark.django_db
def test_experiment_path_applies_saved_binding_override(
    auth_client, experiment, choices_template
):
    """Experiment rows share the eval_runner row branch; the binding comes
    from the experiment add-eval route."""
    with patch("model_hub.views.experiments.ExperimentRunner"):
        response = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/add-eval/",
            {
                "name": "tone-experiment",
                "template_id": str(choices_template.id),
                "config": {
                    "mapping": {"output": "output_column"},
                    "run_config": {"choice_scores": OVERRIDE},
                },
            },
            format="json",
        )
    assert response.status_code == 200, response.content
    metric = UserEvalMetric.objects.get(
        name="tone-experiment", source_id=str(experiment.id)
    )
    assert metric.config["run_config"]["choice_scores"] == OVERRIDE

    runner = _dataset_runner(metric.template, metric.config)
    runner.experiment_dataset = object()
    assert runner.format_output(dict(GOOD), row=object()) == {
        "score": 0.25,
        "choice": "Good",
    }


def test_observe_trace_eval_applies_top_level_override():
    """Observe span/trace/session evals run the engine with
    runtime_config=custom_eval_config.config, which stores the edit at the
    top level."""
    result = _run_engine(
        _template(),
        {"mapping": {"output": "output"}, "choice_scores": OVERRIDE},
    )
    assert result.value == {"score": 0.25, "choice": "Good"}


def test_api_evaluate_applies_override_from_sdk_config():
    """SDK standalone evaluate lifts eval_config["params"] to the top level of
    the engine runtime_config (sdk/utils/evaluations.py _run_eval)."""
    eval_config = {"params": {"run_config": {"choice_scores": OVERRIDE}}}
    engine_runtime_config = {**eval_config, **eval_config["params"]}
    result = _run_engine(_template(), engine_runtime_config)
    assert result.value == {"score": 0.25, "choice": "Good"}


def test_simulate_write_path_applies_top_level_override():
    """Simulate stores eval_outputs through run_eval_func -> format_output
    (row=None); the playground, dataset preview, workbench, external evals and
    composite children share this branch."""
    template = _template()
    runner = _row_none_runner(
        template, {"mapping": {"output": "transcript"}, "choice_scores": OVERRIDE}
    )
    assert runner.format_output(dict(GOOD), eval_template=template) == {
        "score": 0.25,
        "choice": "Good",
    }


def test_simulate_read_model_uses_the_shared_rule():
    """A partial override keeps the template's scores for the other labels,
    as the write path does."""
    from simulate.services.run_results_v3_scoring import resolve_eval_scoring_spec

    template = _template()
    spec = resolve_eval_scoring_spec(
        SimpleNamespace(eval_template=template, config={"choice_scores": OVERRIDE})
    )
    assert spec.choice_scores == {"good": 0.25, "neutral": 0.5, "bad": 0.0}

    empty = resolve_eval_scoring_spec(
        SimpleNamespace(
            eval_template=template, config={"run_config": {"choice_scores": {}}}
        )
    )
    assert empty.choice_scores == {"good": 1.0, "neutral": 0.5, "bad": 0.0}


def test_evaluator_failure_flag_uses_the_same_map():
    """The evaluator computes its pass/fail from the map it is given; it must
    be the map the stored score uses."""
    from evaluations.engine.instance import create_eval_instance

    template = _template(eval_type_id="AgentEvaluator")
    with patch("evaluations.engine.instance.resolve_version", return_value=None):
        top_level = MagicMock()
        create_eval_instance(
            eval_class=top_level,
            eval_template=template,
            config={},
            model="turing_large",
            runtime_config={"choice_scores": OVERRIDE},
        )
        empty = MagicMock()
        create_eval_instance(
            eval_class=empty,
            eval_template=template,
            config={},
            model="turing_large",
            runtime_config={"run_config": {"choice_scores": {}}},
        )
    assert top_level.call_args.kwargs["choice_scores"] == EFFECTIVE
    assert empty.call_args.kwargs["choice_scores"] == TEMPLATE_SCORES


def test_observe_failure_clustering_maps_bare_labels_with_override():
    clustering = _clustering()
    entry = SimpleNamespace(
        output_float=None,
        output_str="Good",
        output_str_list=None,
        custom_eval_config=SimpleNamespace(
            eval_template=_template(), config={"choice_scores": OVERRIDE}
        ),
    )
    assert clustering._eval_result_score(entry) == 0.25


def test_feedback_widget_map_uses_binding_override():
    from model_hub.selectors.feedback import resolve_feedback_template_data

    template = _template()
    template.config = {"output": "score"}
    metric = SimpleNamespace(
        name="tone-binding", config={"run_config": {"choice_scores": OVERRIDE}}
    )
    data = resolve_feedback_template_data(metric, template)
    assert data["choice_scores"] == EFFECTIVE


# --------------------------------------------------------------------------
# Save-time validation
# --------------------------------------------------------------------------


class TestOverrideValidator:
    def test_valid_partial_and_absent_overrides_pass(self):
        from model_hub.utils.scoring import validate_choice_score_override

        template = _template()
        for config in (
            None,
            {},
            {"run_config": {"choice_scores": {}}},
            {"run_config": {"choice_scores": OVERRIDE}},
            {"choice_scores": {"Good": 0, "Bad": 1}},
        ):
            validate_choice_score_override(template, config)

    @pytest.mark.parametrize(
        "override,needle",
        [
            ({"Maybe": 0.5}, "'Maybe' is not a choice of this evaluation"),
            ({"Good": "high"}, "'Good' must be a number"),
            ({"Good": True}, "'Good' must be a number"),
            ({"Good": math.nan}, "'Good' must be between 0 and 1"),
            ({"Good": 1.5}, "'Good' must be between 0 and 1"),
            ({"Good": -0.1}, "'Good' must be between 0 and 1"),
            # A valid JSON integer too large for a float.
            ({"Good": 10**400}, "'Good' must be between 0 and 1"),
            ({"Good": -(10**400)}, "'Good' must be between 0 and 1"),
            (["Good"], "must be an object"),
        ],
    )
    def test_invalid_overrides_are_refused(self, override, needle):
        from model_hub.utils.scoring import validate_choice_score_override

        for config in (
            {"run_config": {"choice_scores": override}},
            {"choice_scores": override},
        ):
            with pytest.raises(ValueError) as refused:
                validate_choice_score_override(_template(), config)
            assert needle in str(refused.value)

    def test_unknown_key_message_names_the_template_choices(self):
        from model_hub.utils.scoring import validate_choice_score_override

        with pytest.raises(ValueError) as refused:
            validate_choice_score_override(
                _template(), {"run_config": {"choice_scores": {"Maybe": 0.5}}}
            )
        assert "Good, Neutral, Bad" in str(refused.value)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "override,needle",
    [
        ({"Maybe": 0.5}, "Maybe"),
        ({"Good": "high"}, "must be a number"),
        ({"Good": 1.5}, "must be between 0 and 1"),
        ({"Good": 10**400}, "must be between 0 and 1"),
    ],
)
def test_dataset_add_refuses_invalid_override(
    auth_client, dataset, output_column, choices_template, override, needle
):
    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        {
            "name": "tone-refused",
            "template_id": str(choices_template.id),
            "config": _binding_config(override),
            "run": False,
        },
        format="json",
    )
    assert response.status_code == 400, response.content
    assert needle in response.content.decode()
    assert not UserEvalMetric.objects.filter(name="tone-refused").exists()


@pytest.mark.django_db
def test_dataset_edit_refuses_invalid_override(
    auth_client, organization, workspace, user, dataset, output_column, choices_template
):
    metric = UserEvalMetric.objects.create(
        name="tone-edit",
        organization=organization,
        workspace=workspace,
        user=user,
        template=choices_template,
        dataset=dataset,
        status=StatusType.INACTIVE.value,
        config=_binding_config(OVERRIDE),
    )
    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/edit_and_run_user_eval/{metric.id}/",
        {"config": _binding_config({"Good": 2})},
        format="json",
    )
    assert response.status_code == 400, response.content
    assert "must be between 0 and 1" in response.content.decode()
    metric.refresh_from_db()
    assert metric.config["run_config"]["choice_scores"] == OVERRIDE


@pytest.mark.django_db
def test_experiment_add_refuses_invalid_override(
    auth_client, experiment, choices_template
):
    with patch("model_hub.views.experiments.ExperimentRunner"):
        response = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/add-eval/",
            {
                "name": "tone-experiment-refused",
                "template_id": str(choices_template.id),
                "config": {
                    "mapping": {"output": "output_column"},
                    "run_config": {"choice_scores": {"Good": True}},
                },
            },
            format="json",
        )
    assert response.status_code == 400, response.content
    assert "must be a number" in response.content.decode()
    assert not UserEvalMetric.objects.filter(name="tone-experiment-refused").exists()


def test_observe_binding_save_refuses_unknown_key_but_keeps_legacy_rows_editable():
    from rest_framework import serializers

    from tracer.serializers.custom_eval_config import CustomEvalConfigSerializer

    template = _template()
    # validate() compares the sent template's pk with the binding's template.
    template.pk = template.id
    with pytest.raises(serializers.ValidationError) as refused:
        CustomEvalConfigSerializer().validate(
            {"eval_template": template, "config": {"choice_scores": {"Maybe": 0.5}}}
        )
    assert "Maybe" in str(refused.value.detail)

    # A partial update that does not send a config never re-validates the
    # stored one.
    legacy = SimpleNamespace(
        eval_template=template, config={"choice_scores": {"Maybe": 0.5}}
    )
    validated = CustomEvalConfigSerializer(instance=legacy).validate({})
    assert validated["config"] == {"choice_scores": {"Maybe": 0.5}}


def test_simulate_binding_edit_refuses_invalid_override():
    from simulate.services.eval_config_edit import (
        EvalConfigEditRefused,
        update_eval_config,
    )

    eval_config = MagicMock()
    eval_config.eval_template = _template()
    eval_config.config = {}
    with pytest.raises(EvalConfigEditRefused) as refused:
        update_eval_config(
            eval_config,
            {"config": {"choice_scores": {"Good": 7}}},
            organization=None,
            workspace=None,
        )
    assert "must be between 0 and 1" in str(refused.value)
    eval_config.save.assert_not_called()


# --------------------------------------------------------------------------
# Every save path refuses an invalid override before it writes anything
# --------------------------------------------------------------------------

V2_CREATE_URL = "/model-hub/experiments/v2/"


def _v2_detail_url(experiment_id):
    return f"/model-hub/experiments/v2/{experiment_id}/"


@pytest.fixture
def no_v2_workflow():
    """The V2 create/edit views start a Temporal workflow; no worker runs here."""
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="wf-choice-override",
    ) as started:
        yield started


@pytest.fixture
def v2_world(db, organization, workspace, user):
    """A dataset with one input column and a row, plus a published prompt."""
    from model_hub.models.develop_dataset import Cell, Row
    from model_hub.models.run_prompt import PromptTemplate, PromptVersion

    dataset = Dataset.no_workspace_objects.create(
        name="Choice Override V2 Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    column = Column.no_workspace_objects.create(
        name="question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=dataset, column=column, row=row, value="What is 2+2?"
    )
    dataset.column_order = [str(column.id)]
    dataset.column_config = {str(column.id): {"is_visible": True, "is_frozen": None}}
    dataset.save()
    prompt = PromptTemplate.no_workspace_objects.create(
        name="Choice Override V2 Prompt",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    prompt_version = PromptVersion.no_workspace_objects.create(
        original_template=prompt,
        template_version="v1",
        prompt_config_snapshot={
            "messages": [{"role": "user", "content": "Answer {{question}}"}],
            "configuration": {},
        },
        variable_names={},
        is_default=True,
    )
    return {
        "dataset": dataset,
        "column": column,
        "prompt": prompt,
        "pv": prompt_version,
    }


def _v2_body(world, template, choice_scores, name):
    return {
        "name": name,
        "dataset_id": str(world["dataset"].id),
        "experiment_type": "llm",
        "prompt_config": [
            {
                "name": "answer",
                "prompt_id": str(world["prompt"].id),
                "prompt_version": str(world["pv"].id),
                "model": "gpt-4o-mini",
            }
        ],
        "user_eval_metrics": [
            {
                "template_id": str(template.id),
                "name": "tone",
                "config": {
                    "mapping": {"output": str(world["column"].id)},
                    "run_config": {"choice_scores": choice_scores},
                },
                "model": "turing_small",
            }
        ],
    }


def _v2_created(auth_client, world, template, name):
    response = auth_client.post(
        V2_CREATE_URL, _v2_body(world, template, OVERRIDE, name), format="json"
    )
    assert response.status_code == 200, response.content
    experiment_id = response.json()["result"].split("experiment_id=")[1].strip()
    experiment = ExperimentsTable.objects.get(id=experiment_id)
    return experiment, experiment.user_eval_template_ids.get()


def _v2_entry(metric, choice_scores):
    """The eval card as the editor sends it back, with a new score override."""
    config = dict(metric.config)
    config["run_config"] = {
        **(config.get("run_config") or {}),
        "choice_scores": choice_scores,
    }
    return {
        "id": str(metric.id),
        "template_id": str(metric.template_id),
        "name": metric.name,
        "config": config,
        "model": metric.model,
    }


@pytest.mark.django_db
def test_experiment_v2_create_refuses_invalid_override_before_any_write(
    auth_client, v2_world, choices_template, no_v2_workflow
):
    response = auth_client.post(
        V2_CREATE_URL,
        _v2_body(v2_world, choices_template, {"Good": 2}, "tone-v2-refused"),
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "must be between 0 and 1" in response.content.decode()
    assert not ExperimentsTable.objects.filter(name="tone-v2-refused").exists()
    no_v2_workflow.assert_not_called()


@pytest.mark.django_db
def test_experiment_v2_update_refuses_invalid_override_before_any_write(
    auth_client, v2_world, choices_template, no_v2_workflow
):
    experiment, metric = _v2_created(
        auth_client, v2_world, choices_template, "tone-v2-edit-refused"
    )
    snapshot_question = Column.no_workspace_objects.get(
        dataset_id=experiment.snapshot_dataset_id, name="question", deleted=False
    )
    column_before = experiment.column_id
    assert column_before != snapshot_question.id

    # The edit also changes the experiment's column; the invalid override
    # must refuse the whole edit before the column (or anything else) changes.
    response = auth_client.put(
        _v2_detail_url(experiment.id),
        {
            "column_id": str(snapshot_question.id),
            "user_eval_metrics": [_v2_entry(metric, {"Maybe": 0.5})],
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "'Maybe' is not a choice of this evaluation" in response.content.decode()
    experiment.refresh_from_db()
    assert experiment.column_id == column_before
    metric.refresh_from_db()
    assert metric.config["run_config"]["choice_scores"] == OVERRIDE


@pytest.mark.django_db
def test_experiment_v2_override_only_update_is_saved_and_reruns(
    auth_client, v2_world, choices_template, no_v2_workflow
):
    experiment, metric = _v2_created(
        auth_client, v2_world, choices_template, "tone-v2-edit"
    )
    no_v2_workflow.reset_mock()

    response = auth_client.put(
        _v2_detail_url(experiment.id),
        {"user_eval_metrics": [_v2_entry(metric, {"Good": 0.75})]},
        format="json",
    )

    assert response.status_code == 200, response.content
    metric.refresh_from_db()
    assert metric.config["run_config"]["choice_scores"] == {"Good": 0.75}
    assert no_v2_workflow.call_args.kwargs["rerun_eval_template_ids"] == [
        str(metric.id)
    ]

    # Sending the saved card back unchanged is not an edit: nothing re-runs.
    no_v2_workflow.reset_mock()
    unchanged = auth_client.put(
        _v2_detail_url(experiment.id),
        {"user_eval_metrics": [_v2_entry(metric, {"Good": 0.75})]},
        format="json",
    )
    assert unchanged.status_code == 200, unchanged.content
    no_v2_workflow.assert_not_called()


@pytest.mark.django_db
def test_eval_user_template_create_refuses_invalid_override(
    auth_client, dataset, output_column, choices_template
):
    response = auth_client.post(
        "/model-hub/eval-user-template/create/",
        {
            "name": "tone-user-template",
            "template_id": str(choices_template.id),
            "dataset_id": str(dataset.id),
            "config": _binding_config({"Good": "high"}),
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "must be a number" in response.content.decode()
    assert not UserEvalMetric.objects.filter(name="tone-user-template").exists()


@pytest.mark.django_db
def test_workbench_save_refuses_invalid_override(
    auth_client, organization, workspace, user, choices_template
):
    from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate

    prompt_template = PromptTemplate.no_workspace_objects.create(
        name="Choice Override Workbench Prompt",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )

    response = auth_client.post(
        f"/model-hub/prompt-templates/{prompt_template.id}/update-evaluation-configs/",
        {
            "id": str(choices_template.id),
            "name": "tone-workbench",
            "mapping": {"output": "model_output"},
            "config": {"run_config": {"choice_scores": {"Good": 2}}},
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "must be between 0 and 1" in response.content.decode()
    assert not PromptEvalConfig.objects.filter(prompt_template=prompt_template).exists()


@pytest.mark.django_db
def test_observe_template_switch_revalidates_the_retained_override(
    auth_client, organization, workspace, choices_template
):
    """A PATCH that switches the template and sends no config keeps the stored
    override; it must still be a valid override for the new template."""
    from model_hub.models.ai_model import AIModel
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.project import Project

    yes_no = EvalTemplate.objects.create(
        name="tone-yes-no",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        criteria="Judge {{output}}",
        model="gpt-4o-mini",
        output_type_normalized="deterministic",
        choices=["Yes", "No"],
        choice_scores={"Yes": 1.0, "No": 0.0},
        pass_threshold=0.5,
        config={
            "output": "choices",
            "required_keys": ["output"],
            "eval_type_id": "AgentEvaluator",
        },
    )
    project = Project.objects.create(
        name="Choice Override Observe Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    binding = CustomEvalConfig.objects.create(
        name="tone-observe",
        project=project,
        eval_template=choices_template,
        config={"choice_scores": OVERRIDE},
        mapping={"output": "output"},
        filters={},
    )

    response = auth_client.patch(
        f"/tracer/custom-eval-config/{binding.id}/",
        {"eval_template": str(yes_no.id)},
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "'Good' is not a choice of this evaluation" in response.content.decode()
    binding.refresh_from_db()
    assert binding.eval_template_id == choices_template.id


@pytest.mark.django_db
def test_simulate_mixed_batch_is_refused_whole(
    auth_client, organization, workspace, choices_template
):
    """One valid and one invalid override in one request: a 400, and neither
    eval is saved."""
    from simulate.models import RunTest, SimulateEvalConfig

    run_test = RunTest.objects.create(
        name="Choice Override Run Test",
        organization=organization,
        workspace=workspace,
    )

    response = auth_client.post(
        f"/simulate/run-tests/{run_test.id}/eval-configs/",
        {
            "evaluations_config": [
                {
                    "template_id": str(choices_template.id),
                    "name": "tone-valid",
                    "mapping": {"output": "transcript"},
                    "config": {"choice_scores": OVERRIDE},
                },
                {
                    "template_id": str(choices_template.id),
                    "name": "tone-invalid",
                    "mapping": {"output": "transcript"},
                    "config": {"choice_scores": {"Maybe": 0.5}},
                },
            ]
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "'Maybe' is not a choice of this evaluation" in response.content.decode()
    assert not SimulateEvalConfig.objects.filter(run_test=run_test).exists()


@pytest.mark.django_db
def test_sdk_configure_evaluations_refuses_invalid_override(
    auth_client, choices_template
):
    from tracer.models.external_eval_config import ExternalEvalConfig

    response = auth_client.post(
        "/sdk/api/v1/configure-evaluations/",
        {
            "eval_config": {
                "eval_templates": choices_template.name,
                "inputs": {"output": "a reply"},
                "config": {"run_config": {"choice_scores": {"Maybe": 0.5}}},
            },
            "platform": "langfuse",
            "custom_eval_name": "tone-external",
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "'Maybe' is not a choice of this evaluation" in response.content.decode()
    assert not ExternalEvalConfig.objects.filter(name="tone-external").exists()


@pytest.mark.django_db
def test_composite_create_refuses_invalid_child_override(auth_client, choices_template):
    response = auth_client.post(
        "/model-hub/eval-templates/create-composite/",
        {
            "name": "tone-composite",
            "child_template_ids": [str(choices_template.id)],
            "child_configs": {
                str(choices_template.id): {
                    "run_config": {"choice_scores": {"Maybe": 0.5}}
                }
            },
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "'Maybe' is not a choice of this evaluation" in response.content.decode()
    assert not EvalTemplate.no_workspace_objects.filter(name="tone-composite").exists()


@pytest.mark.django_db
def test_composite_update_refuses_invalid_child_override(
    auth_client, organization, workspace, choices_template
):
    from model_hub.models.evals_metric import CompositeEvalChild

    parent = EvalTemplate.objects.create(
        name="tone-composite-edit",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        template_type="composite",
        config={},
    )
    link = CompositeEvalChild.objects.create(
        parent=parent, child=choices_template, order=0, weight=1.0, config={}
    )

    response = auth_client.patch(
        f"/model-hub/eval-templates/{parent.id}/composite/",
        {"child_configs": {str(choices_template.id): {"choice_scores": {"Good": 2}}}},
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "must be between 0 and 1" in response.content.decode()
    link.refresh_from_db()
    assert link.config == {}


# --------------------------------------------------------------------------
# SDK evaluate: error localization and the params-nested override
# --------------------------------------------------------------------------


def _sdk_choices_result(score, failure):
    """SDK evaluate's result for the label "Good" (sdk/utils/evaluations.py
    _run_eval): the raw evaluator data keeps the label, and the resolved value
    carries the score the binding's effective map gave it."""
    return {
        "data": {"result": "Good"},
        "failure": failure,
        "reason": "judged",
        "runtime": 1,
        "model": "judge",
        "metrics": [],
        "metadata": {},
        "output": "choices",
        "value": {"score": score, "choice": "Good"},
    }


def _localize_standalone(user, template, eval_config, result):
    """Store an SDK evaluation with error localization on, then run the
    localization worker on the task it triggered (its gate included)."""
    from ee.evals.localizer.error_localizer import LocalizerResult
    from model_hub.tasks.user_evaluation import process_single_error_localization
    from sdk.utils.evaluations import _create_evaluation
    from tfc.constants.api_calls import APICallStatusChoices

    evaluation = _create_evaluation(
        eval_template=template,
        inputs={"output": "a reply"},
        user=user,
        eval_config=eval_config,
        result=result,
        error_localizer_enabled=True,
        trace_eval=False,
        custom_eval_name=None,
        span_id=None,
    )
    task = evaluation.error_localizer
    assert task is not None

    with (
        patch("model_hub.tasks.user_evaluation.close_old_connections"),
        patch(
            "model_hub.tasks.user_evaluation.log_and_deduct_cost_for_api_request",
            return_value=MagicMock(status=APICallStatusChoices.PROCESSING.value),
        ),
        patch("model_hub.tasks.user_evaluation.ErrorLocalizer") as localizer,
        patch(
            "ee.usage.services.metering.check_usage",
            return_value=MagicMock(allowed=True),
        ),
    ):
        localizer.return_value.localize_errors.return_value = LocalizerResult(
            analysis={"summary": "tone"}, selected_key="output"
        )
        process_single_error_localization._original_func(str(task.id))

    task.refresh_from_db()
    return task, localizer


@pytest.mark.django_db
def test_sdk_evaluate_error_localization_gates_on_the_override_score(
    user, workspace, choices_template
):
    """Template Good=1, binding Good=0.25, threshold 0.5: the evaluation fails,
    so localization runs instead of re-scoring "Good" as 1 and skipping it."""
    from model_hub.models.error_localizer_model import ErrorLocalizerStatus

    task, localizer = _localize_standalone(
        user,
        choices_template,
        {"params": {"run_config": {"choice_scores": OVERRIDE}}},
        _sdk_choices_result(0.25, failure=True),
    )

    assert task.status == ErrorLocalizerStatus.COMPLETED, task.error_message
    localizer.return_value.localize_errors.assert_called_once()


@pytest.mark.django_db
def test_fallback_sdk_evaluate_error_localization_uses_template_map(
    user, workspace, choices_template
):
    """No override: "Good" scores the template's 1.0 and passes, so the gate
    skips localization (passes with and without the rule)."""
    from model_hub.models.error_localizer_model import ErrorLocalizerStatus

    task, localizer = _localize_standalone(
        user, choices_template, {}, _sdk_choices_result(1.0, failure=False)
    )

    assert task.status == ErrorLocalizerStatus.SKIPPED
    assert "passed" in task.error_message
    localizer.return_value.localize_errors.assert_not_called()


# SDK callers wrap their config in "params", and SDK evaluate lifts its keys
# over the outer ones before the engine runs, so validation checks both.
SDK_PARAMS_OVERRIDES = [
    pytest.param(
        {"params": {"run_config": {"choice_scores": {"Good": 7}}}},
        "'Good' must be between 0 and 1",
        id="nested",
    ),
    pytest.param(
        {"params": {"choice_scores": {"Maybe": 0.5}}},
        "'Maybe' is not a choice of this evaluation",
        id="nested-top-level",
    ),
    pytest.param(
        {
            "run_config": {"choice_scores": OVERRIDE},
            "params": {"run_config": {"choice_scores": {"Good": 7}}},
        },
        "'Good' must be between 0 and 1",
        id="valid-outer-shadowed-by-invalid-params",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize("config,needle", SDK_PARAMS_OVERRIDES)
def test_sdk_configure_evaluations_refuses_invalid_params_override(
    auth_client, choices_template, config, needle
):
    from tracer.models.external_eval_config import ExternalEvalConfig

    response = auth_client.post(
        "/sdk/api/v1/configure-evaluations/",
        {
            "eval_config": {
                "eval_templates": choices_template.name,
                "inputs": {"output": "a reply"},
                "config": config,
            },
            "platform": "langfuse",
            "custom_eval_name": "tone-external-params",
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert needle in response.content.decode()
    assert not ExternalEvalConfig.objects.filter(name="tone-external-params").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("config,needle", SDK_PARAMS_OVERRIDES)
def test_sdk_evaluate_refuses_invalid_params_override_before_it_runs(
    auth_client, choices_template, config, needle
):
    with (
        patch("sdk.views.evaluations._run_standalone_eval") as run,
        patch("sdk.views.evaluations._handle_async_eval") as run_async,
    ):
        response = auth_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": choices_template.name,
                "inputs": {"output": "a reply"},
                "config": config,
            },
            format="json",
        )

    assert response.status_code == 400, response.content
    assert needle in response.content.decode()
    run.assert_not_called()
    run_async.assert_not_called()


@pytest.mark.django_db
def test_sdk_config_with_a_valid_params_override_is_accepted(
    user, workspace, choices_template
):
    from sdk.serializers.evaluations import ConfigureEvaluationsSerializer

    config = {"params": {"run_config": {"choice_scores": OVERRIDE}}}
    serializer = ConfigureEvaluationsSerializer(
        data={
            "eval_templates": choices_template.name,
            "inputs": {"output": "a reply"},
            "config": config,
        },
        context={"request": SimpleNamespace(user=user, workspace=workspace)},
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["config"] == config


@pytest.mark.parametrize(
    "eval_config,effective",
    [
        ({"params": {"run_config": {"choice_scores": OVERRIDE}}}, EFFECTIVE),
        (
            {
                "run_config": {"choice_scores": {"Good": 0.0}},
                "params": {"run_config": {"choice_scores": OVERRIDE}},
            },
            EFFECTIVE,
        ),
        ({"run_config": {"choice_scores": OVERRIDE}}, EFFECTIVE),
        ({}, TEMPLATE_SCORES),
    ],
)
def test_sdk_evaluate_runs_the_config_its_validation_checks(eval_config, effective):
    """SDK evaluate hands the engine engine_runtime_config(eval_config), the
    same config ConfigureEvaluationsSerializer validates."""
    from evaluations.engine.instance import resolve_choice_scores
    from sdk.utils.evaluations import engine_runtime_config

    assert resolve_choice_scores(_template(), engine_runtime_config(eval_config)) == (
        effective
    )


# --------------------------------------------------------------------------
# SDK external-platform evaluations and the CI/CD pipeline
# --------------------------------------------------------------------------


def _judge_says_good():
    """An evaluator instance whose judge answers the label "Good"."""
    instance = MagicMock(cost=None, token_usage=None)
    instance.run.return_value = SimpleNamespace(
        eval_results=[
            {
                "data": "Good",
                "failure": False,
                "reason": "judged",
                "runtime": 1,
                "model": "judge",
                "metrics": [],
                "metadata": {},
            }
        ]
    )
    return instance


def _run_external_platform_eval(stored):
    """Run a stored external-platform evaluation through its entry point
    (tracer/utils/external_eval.py run_external_eval_config), the judge
    answering "Good". Cost logging, the input mapping and the send to the
    platform are patched (no network)."""
    from tracer.utils.external_eval import run_external_eval_config

    with (
        patch("tracer.utils.external_eval._log_and_deduct_cost_for_external_eval"),
        patch("tracer.utils.external_eval._send_eval_result") as send,
        patch("evaluations.engine.registry.get_eval_class", return_value=MagicMock()),
        patch(
            "evaluations.engine.instance.create_eval_instance",
            return_value=(_judge_says_good(), None),
        ) as create,
        patch(
            "model_hub.views.eval_runner.EvaluationRunner.map_fields",
            return_value={"output": "a reply"},
        ),
    ):
        run_external_eval_config._original_func(str(stored.id))

    stored.refresh_from_db()
    return create, send


# The external-platform run applies the override SDK evaluate applies to the
# same config, which is the one SDK configure-evaluations validated.
EXTERNAL_PLATFORM_CONFIGS = [
    pytest.param(
        {"params": {"run_config": {"choice_scores": OVERRIDE}}},
        EFFECTIVE,
        id="params-run-config",
    ),
    pytest.param(
        {"params": {"choice_scores": OVERRIDE}}, EFFECTIVE, id="params-top-level"
    ),
    pytest.param(
        {
            "run_config": {"choice_scores": {"Good": 0.0}},
            "params": {"run_config": {"choice_scores": OVERRIDE}},
        },
        EFFECTIVE,
        id="params-over-outer",
    ),
    pytest.param(
        {"run_config": {"choice_scores": OVERRIDE}}, EFFECTIVE, id="outer-run-config"
    ),
    pytest.param({}, TEMPLATE_SCORES, id="no-override"),
]

# configure-evaluations stores the other top-level request fields as the
# platform credentials, and ExternalEvalConfig.save() refuses a Langfuse config
# without its three non-empty keys (get_required_credentials, clean()).
# Synthetic values: the send to the platform is patched.
LANGFUSE_TEST_CREDENTIALS = {
    "langfuse_secret_key": "secret",
    "langfuse_public_key": "public",
    "langfuse_host": "https://example.test",
}


@pytest.mark.django_db
@pytest.mark.parametrize("config,effective", EXTERNAL_PLATFORM_CONFIGS)
def test_external_platform_eval_applies_the_override_it_was_configured_with(
    auth_client, choices_template, config, effective
):
    from evaluations.engine.instance import resolve_choice_scores
    from tracer.models.external_eval_config import ExternalEvalConfig, StatusChoices

    response = auth_client.post(
        "/sdk/api/v1/configure-evaluations/",
        {
            "eval_config": {
                "eval_templates": choices_template.name,
                "inputs": {"output": "a reply"},
                "config": config,
            },
            "platform": "langfuse",
            "custom_eval_name": "tone-external-run",
            **LANGFUSE_TEST_CREDENTIALS,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    stored = ExternalEvalConfig.no_workspace_objects.get(name="tone-external-run")
    assert stored.credentials == LANGFUSE_TEST_CREDENTIALS

    create, send = _run_external_platform_eval(stored)

    expected = {"score": effective["Good"], "choice": "Good"}
    assert stored.status == StatusChoices.COMPLETED, stored.error_message
    assert stored.eval_results["value"] == expected
    assert send.call_args.args[1] == expected
    # The evaluator's pass/fail map is the same one, and the stored config
    # stays as it was sent.
    runtime_config = create.call_args.kwargs["runtime_config"]
    assert resolve_choice_scores(choices_template, runtime_config) == effective
    assert stored.config == config


@pytest.fixture
def pipeline_project(db, organization, workspace):
    from tracer.models.project import Project

    return Project.objects.create(
        name="tone-pipeline",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )


def _pipeline_body(template, config):
    return {
        "project_name": "tone-pipeline",
        "version": "v1",
        "eval_data": [
            {
                "eval_template": template.name,
                "inputs": {"output": ["a reply"]},
                "config": config,
            }
        ],
    }


PIPELINE_INVALID_OVERRIDES = [
    pytest.param(
        {"run_config": {"choice_scores": {"Good": "high"}}},
        "'Good' must be a number",
        id="run-config-not-a-number",
    ),
    pytest.param(
        {"choice_scores": {"Maybe": 0.5}},
        "'Maybe' is not a choice of this evaluation",
        id="top-level-unknown-choice",
    ),
    *SDK_PARAMS_OVERRIDES,
]


@pytest.mark.django_db
@pytest.mark.parametrize("config,needle", PIPELINE_INVALID_OVERRIDES)
def test_sdk_pipeline_refuses_invalid_override_before_anything_is_queued(
    auth_client, choices_template, pipeline_project, config, needle
):
    """POST /sdk/api/v1/evaluate-pipeline/ runs each item's config through
    SDK evaluate (tfc/temporal/evaluations/activities.py -> _run_eval), so it
    validates the override as SDK evaluate does."""
    from model_hub.models.evaluation import Evaluation
    from tracer.models.eval_ci_cd import EvaluationRun

    with patch("tfc.temporal.evaluations.start_evaluation_workflow") as start:
        response = auth_client.post(
            "/sdk/api/v1/evaluate-pipeline/",
            _pipeline_body(choices_template, config),
            format="json",
        )

    assert response.status_code == 400, response.content
    assert needle in response.content.decode()
    assert not EvaluationRun.no_workspace_objects.filter(
        project=pipeline_project
    ).exists()
    assert not Evaluation.no_workspace_objects.filter(
        eval_template=choices_template
    ).exists()
    start.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "config,effective",
    [
        pytest.param(
            {"params": {"run_config": {"choice_scores": OVERRIDE}}},
            EFFECTIVE,
            id="params-run-config",
        ),
        pytest.param(
            {"run_config": {"choice_scores": OVERRIDE}}, EFFECTIVE, id="run-config"
        ),
        pytest.param({}, TEMPLATE_SCORES, id="no-override"),
    ],
)
def test_sdk_pipeline_runs_the_valid_override_it_accepted(
    auth_client, choices_template, pipeline_project, config, effective
):
    """The queued evaluation runs through the real Temporal activity body; the
    judge, cost logging and the trace hook are patched (no network)."""
    import ast

    from evaluations.engine import runner as engine_runner
    from evaluations.engine.instance import resolve_choice_scores
    from model_hub.models.evaluation import Evaluation, StatusChoices
    from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

    with patch("tfc.temporal.evaluations.start_evaluation_workflow") as start:
        response = auth_client.post(
            "/sdk/api/v1/evaluate-pipeline/",
            _pipeline_body(choices_template, config),
            format="json",
        )

    assert response.status_code == 200, response.content
    evaluation = Evaluation.no_workspace_objects.get(eval_template=choices_template)
    assert evaluation.eval_config == config
    start.assert_called_once_with(evaluation_id=str(evaluation.id))

    with (
        patch("tfc.temporal.evaluations.activities.close_old_connections"),
        patch(
            "sdk.utils.evaluations._log_and_deduct_cost_for_standalone_eval",
            return_value=None,
        ),
        patch(
            "model_hub.services.ground_truth_service.GroundTruthService.inject_context"
        ),
        patch("ee.usage.services.emitter.emit"),
        patch("tracer.utils.inline_evals.trigger_inline_eval"),
        patch.object(engine_runner, "get_eval_class", return_value=MagicMock()),
        patch.object(
            engine_runner,
            "create_eval_instance",
            return_value=(_judge_says_good(), None),
        ) as create,
        patch.object(engine_runner, "prepare_run_params", return_value={}),
        patch(
            "evaluations.engine.preprocessing.preprocess_inputs",
            side_effect=lambda _name, params: params,
        ),
    ):
        outcome = _run_single_evaluation_sync(str(evaluation.id))

    assert outcome["status"] == StatusChoices.COMPLETED, outcome
    evaluation.refresh_from_db()
    # Evaluation.value is a text column: the formatted dict is stored as text.
    assert ast.literal_eval(evaluation.value) == {
        "score": effective["Good"],
        "choice": "Good",
    }
    runtime_config = create.call_args.kwargs["runtime_config"]
    assert resolve_choice_scores(choices_template, runtime_config) == effective


# --------------------------------------------------------------------------
# Request-time runs (no save): the eval playground (also config-full's Test
# run) and the dataset single-row preview apply the request's override, so
# they validate it before they run.
# --------------------------------------------------------------------------

REQUEST_TIME_INVALID_OVERRIDES = [
    pytest.param(
        {"run_config": {"choice_scores": {"Maybe": 0.5}}},
        "'Maybe' is not a choice of this evaluation",
        id="run-config-unknown-choice",
    ),
    pytest.param(
        {"choice_scores": {"Good": 2}},
        "'Good' must be between 0 and 1",
        id="top-level-out-of-range",
    ),
]


def _playground_post(auth_client, template, config):
    return auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": "gpt-4o-mini",
            "mapping": {"output": "a reply"},
            "config": config,
        },
        format="json",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("config,needle", REQUEST_TIME_INVALID_OVERRIDES)
def test_eval_playground_refuses_invalid_override_before_it_runs(
    auth_client, choices_template, config, needle
):
    with patch(
        "model_hub.views.separate_evals.run_eval_func",
        return_value={"output": {"output": "Good"}},
    ) as run:
        response = _playground_post(auth_client, choices_template, config)

    assert response.status_code == 400, response.content
    assert needle in response.content.decode()
    run.assert_not_called()


@pytest.mark.django_db
def test_eval_playground_runs_a_valid_override_as_sent(auth_client, choices_template):
    from evaluations.engine.instance import resolve_choice_scores

    with patch(
        "model_hub.views.separate_evals.run_eval_func",
        return_value={"output": {"output": {"score": 0.25, "choice": "Good"}}},
    ) as run:
        response = _playground_post(
            auth_client, choices_template, {"run_config": {"choice_scores": OVERRIDE}}
        )

    assert response.status_code == 200, response.content
    run.assert_called_once()
    assert resolve_choice_scores(choices_template, run.call_args.args[0]) == EFFECTIVE


def _preview_post(auth_client, dataset, template, config):
    return auth_client.post(
        f"/model-hub/develops/{dataset.id}/preview_run_eval/",
        {"template_id": str(template.id), "config": config},
        format="json",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("config,needle", REQUEST_TIME_INVALID_OVERRIDES)
def test_dataset_preview_refuses_invalid_override_before_it_runs(
    auth_client, dataset, output_column, choices_template, config, needle
):
    from model_hub.models.develop_dataset import Row

    Row.objects.create(dataset=dataset, order=0)
    config = {"mapping": {"output": str(output_column.id)}, **config}
    with patch(
        "model_hub.views.develop_dataset.process_eval_for_single_row",
        return_value={"output": "Good", "reason": ""},
    ) as run:
        response = _preview_post(auth_client, dataset, choices_template, config)

    assert response.status_code == 400, response.content
    assert needle in response.content.decode()
    run.assert_not_called()


@pytest.mark.django_db
def test_dataset_preview_runs_a_valid_override_as_sent(
    auth_client, dataset, output_column, choices_template
):
    from evaluations.engine.instance import resolve_choice_scores
    from model_hub.models.develop_dataset import Row

    Row.objects.create(dataset=dataset, order=0)
    config = {
        "mapping": {"output": str(output_column.id)},
        "run_config": {"choice_scores": OVERRIDE},
    }
    with patch(
        "model_hub.views.develop_dataset.process_eval_for_single_row",
        return_value={"output": {"score": 0.25, "choice": "Good"}, "reason": ""},
    ) as run:
        response = _preview_post(auth_client, dataset, choices_template, config)

    assert response.status_code == 200, response.content
    run.assert_called_once()
    # process_eval_for_single_row(..., model, runtime_config): the runner's
    # runtime_config, which format_output scores with.
    runtime_config = run.call_args.args[-1]
    assert resolve_choice_scores(choices_template, runtime_config) == EFFECTIVE
