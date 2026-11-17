"""A failed experiment row says what happened in words chosen from a code.

Real admission, setup, row execution and recording; the workflow start and the
evaluator engine are stand-ins. These tests use the product's own route
resolver and never install one: the native world's prompt model, "model-a", is
in no catalog and is no custom model, and the tenants' organizations have no
provider key, so each row meets its real refusal before any provider call.
"""

import json
import pickle
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db import transaction

from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.experiment_submit import admit_submission, stage_submission
from model_hub.services.persisted_run_comparison import _bindings
from model_hub.services.results_grid import result_cell, trial_record
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    PROVIDER,
    START,
    attach_executable_native_eval,
    create,
    finish,
    native_fixture_evaluator,
    native_result_reason,
    row,
    setup,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants  # noqa: F401
from model_hub.tests.test_native_run_binding import native_world as native_world

# Row execution manages worker connections, as the native bridge tests do.
pytestmark = pytest.mark.django_db(transaction=True)

MODEL = "model-a"
UNCATALOGUED = "{model} isn't a model this workspace can run. Choose another model for this prompt."
RUN_FAILED = "Run failed on this row"
PROVIDER_ERROR = "The model provider returned an error."
KEY_MISSING = (
    "API key not configured for {provider}. Please add your API key in settings."
)
PROMPT_NOT_SCORED = "Not scored: this row's prompt didn't run."
AGENT_NOT_SCORED = "Not scored: this row's agent run failed."
AGENT_FAILED = "The agent run failed on this row"
AGENT_CANCELLED = "The agent run was cancelled"
EVALUATION_FAILED = "The evaluation failed on this row"
CATALOG_TEXT = (
    "Model 'model-a' is not available in the current model catalog. "
    "It may be deprecated or retired. Please select a supported model from the latest available models list."
)
PROVIDER_TEXT = "the provider's own words about the request"
PROMPT_RESPONSE = "tfc.temporal.experiments.provider_policy.prompt_response"


def reason_of(cell):
    value = cell.value_infos
    return json.loads(value) if isinstance(value, str) else value


def admitted(w, display_name=None):
    """Admit the native world's prompt with one attached evaluator, then set it up."""
    if display_name is not None:
        ExperimentPromptConfig.no_workspace_objects.filter(pk=w["config"].pk).update(
            model_display_name=display_name
        )
    w["experiment"].refresh_from_db()
    metric, pin = attach_executable_native_eval(w)
    with transaction.atomic():
        stage_submission(
            w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace
        )
    with patch(START, return_value="row-failure-admitted"):
        admit_submission(str(w["experiment"].id))
    context, prepared = setup(w["experiment"])
    return metric, pin, context, prepared


def run_every_row(experiment, context, prepared):
    with patch(PROVIDER) as provider:
        results = [
            row(experiment, context, prepared, row_id) for row_id in prepared["row_ids"]
        ]
        finish(context, prepared)
    provider.assert_not_called()
    return results


def bound_run(context):
    return NativeRunBinding.objects.get(run_id=context["run_id"]).run


def output_cells(prepared):
    cells = Cell.no_workspace_objects.filter(
        column_id=prepared["column_id"], row_id__in=prepared["row_ids"], deleted=False
    )
    assert cells.count() == len(prepared["row_ids"])
    return cells


def assert_not_scored(w, metric, row_ids):
    """Each row's evaluator result and reason cells carry the prompt row's line."""
    result, reason = native_result_reason(w, metric)
    for column in (result, reason):
        cells = Cell.no_workspace_objects.filter(
            column=column, row_id__in=row_ids, deleted=False
        )
        assert cells.count() == len(row_ids)
        for cell in cells:
            assert (cell.value, cell.status) == (
                PROMPT_NOT_SCORED,
                CellStatus.ERROR.value,
            )
            assert reason_of(cell) == {"reason": PROMPT_NOT_SCORED}


def grid_error(run, item):
    """The results grid cell's error for this one-try row."""
    return result_cell([trial_record(item, item.trial_number, run, _bindings(run))])[
        "error"
    ]


def first_error(run):
    """The run's First error, as the results runs catalog projects it."""
    from model_hub.services.results_catalog import project_run

    return project_run(run)["first_error"]


def test_the_catalog_miss_is_a_value_error_that_carries_the_model_id():
    from agentic_eval.core_evals.run_prompt.litellm_models import (
        LiteLLMModelManager,
        ModelNotInCatalog,
    )

    with pytest.raises(ModelNotInCatalog) as caught:
        LiteLLMModelManager(MODEL).get_provider(MODEL)
    assert isinstance(caught.value, ValueError)
    assert caught.value.model_name == MODEL
    # Callers that show the exception's own text read the words they read today.
    assert str(caught.value) == CATALOG_TEXT
    copy = pickle.loads(pickle.dumps(caught.value))
    assert (type(copy), copy.model_name, str(copy)) == (
        ModelNotInCatalog,
        MODEL,
        CATALOG_TEXT,
    )


@pytest.mark.parametrize(
    "display_name,shown",
    [(None, MODEL), ("", MODEL), ("Retired Model A", "Retired Model A")],
    ids=["no_display_name", "blank_display_name", "display_name"],
)
def test_a_native_row_whose_model_the_catalog_lacks_names_it_and_what_to_do(
    native_world, display_name, shown
):
    w = native_world
    metric, pin, context, prepared = admitted(w, display_name)
    results = run_every_row(w["experiment"], context, prepared)
    assert [r["status"] for r in results] == ["FAILED"] * len(prepared["row_ids"])
    sentence = UNCATALOGUED.format(model=shown)
    run = bound_run(context)
    items = list(run.items.order_by("id"))
    assert len(items) == len(prepared["row_ids"])
    for item in items:
        assert item.status == "failed" and item.output is None
        # The row drawer reads this message.
        assert item.error == {
            "type": "ModelNotInCatalog",
            "code": "model_not_in_catalog",
            "message": sentence,
        }
        assert item.score_refs == [
            {
                "metric_id": str(metric.id),
                "evaluator_version_id": str(pin.id),
                "status": "not_assessed",
                "reason": "execution_failed",
                "explanation": PROMPT_NOT_SCORED,
            }
        ]
        assert grid_error(run, item) == sentence
    # The output cell holds the sentence, never the exception's own text.
    for cell in output_cells(prepared):
        assert (cell.value, cell.status) == (sentence, CellStatus.ERROR.value)
        assert reason_of(cell) == {"reason": sentence}
    assert_not_scored(w, metric, prepared["row_ids"])
    assert first_error(run) == sentence


@pytest.mark.parametrize(
    "error",
    [ValueError(CATALOG_TEXT), RuntimeError("provider unavailable")],
    ids=["value_error_with_the_catalog_words", "runtime_error"],
)
def test_any_other_failure_on_a_native_row_reads_run_failed_on_this_row(
    native_world, error
):
    # A plain ValueError with the catalog's own words is not the catalog miss:
    # the row's code comes from the exception's type, never from its text.
    w = native_world
    metric, _, context, prepared = admitted(w)
    with patch(PROMPT_RESPONSE, side_effect=error) as prompt_response:
        run_every_row(w["experiment"], context, prepared)
    assert prompt_response.call_count == len(prepared["row_ids"])
    run = bound_run(context)
    for item in run.items.all():
        assert item.error == {
            "type": type(error).__name__,
            "code": "row_failed",
            "message": RUN_FAILED,
        }
        assert item.score_refs[0]["explanation"] == PROMPT_NOT_SCORED
        assert grid_error(run, item) == RUN_FAILED
    # The output cell says what the row says, never the exception's text.
    for cell in output_cells(prepared):
        assert (cell.value, cell.status) == (RUN_FAILED, CellStatus.ERROR.value)
        assert reason_of(cell) == {"reason": RUN_FAILED}
    assert_not_scored(w, metric, prepared["row_ids"])
    assert first_error(run) == RUN_FAILED


def test_a_row_whose_run_settings_changed_after_admission_reads_run_failed_on_this_row(
    native_world,
):
    w = native_world
    metric, _, context, prepared = admitted(w)
    # Settings bound at admission no longer match the experiment's.
    ExperimentsTable.no_workspace_objects.filter(pk=w["experiment"].pk).update(
        run_policy={"limits": {}}
    )
    run_every_row(w["experiment"], context, prepared)
    run = bound_run(context)
    for item in run.items.all():
        assert item.error == {
            "type": "RunWriteError",
            "code": "row_failed",
            "message": RUN_FAILED,
        }
        assert grid_error(run, item) == RUN_FAILED
    for cell in output_cells(prepared):
        assert (cell.value, reason_of(cell)) == (RUN_FAILED, {"reason": RUN_FAILED})
    assert_not_scored(w, metric, prepared["row_ids"])
    assert first_error(run) == RUN_FAILED


def test_a_provider_failure_reads_the_provider_returned_an_error(native_world):
    w = native_world
    metric, _, context, prepared = admitted(w)
    with patch(PROMPT_RESPONSE, return_value=(PROVIDER_TEXT, {"failure": True})):
        run_every_row(w["experiment"], context, prepared)
    run = bound_run(context)
    for item in run.items.all():
        assert item.error == {
            "type": "ProviderFailure",
            "code": "provider_failed",
            "message": PROVIDER_ERROR,
        }
        assert grid_error(run, item) == PROVIDER_ERROR
        # The provider's own reply stays where it is kept today, and only there.
        assert item.output == PROVIDER_TEXT
    for cell in output_cells(prepared):
        assert (cell.value, cell.status) == (PROVIDER_TEXT, CellStatus.ERROR.value)
    assert_not_scored(w, metric, prepared["row_ids"])
    assert first_error(run) == PROVIDER_ERROR


def test_a_native_row_with_no_key_for_its_provider_says_which_key_to_add(tenants):
    actor = tenants["a"]
    response, experiment, _, _ = create(actor)
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    run_every_row(experiment, context, prepared)
    sentence = KEY_MISSING.format(
        provider="openai"
    )  # the catalog's provider for gpt-4o-mini
    run = bound_run(context)
    for item in run.items.all():
        assert item.error == {
            "type": "ModelRouteUnavailable",
            "code": "provider_key_missing",
            "message": sentence,
        }
        assert grid_error(run, item) == sentence
    # The run's status reads its First error from the results runs catalog.
    listed = actor["client"].get(f"/model-hub/datasets/{experiment.dataset_id}/runs/")
    assert listed.status_code == 200, listed.content
    runs = {r["run_id"]: r for r in listed.json()["result"]["results"]}
    assert runs[context["run_id"]]["first_error"] == sentence


def grid_try(number, status, error=None):
    return {
        "trial_number": number,
        "status": status,
        "output": None,
        "error": error,
        "scores": {},
        "cost_usd": None,
        "wall_ms": None,
        "cost": None,
        "latency_ms": None,
        "trace_id": None,
        "result_id": f"result-{number}",
        "carried": False,
        "availability": None,
    }


def test_a_grid_cell_shows_only_a_sentence_a_code_chose():
    def error_of(*trials):
        return result_cell(list(trials))["error"]

    named = UNCATALOGUED.format(model="Retired Model A")
    assert (
        error_of(
            grid_try(1, "error", {"code": "model_not_in_catalog", "message": named})
        )
        == named
    )
    # A fixed code's sentence is the code's, whatever text was stored beside it.
    assert (
        error_of(grid_try(1, "error", {"code": "row_failed", "message": "stored text"}))
        == RUN_FAILED
    )
    assert (
        error_of(
            grid_try(1, "error", {"code": "provider_failed", "message": "stored text"})
        )
        == PROVIDER_ERROR
    )
    # No code, an unknown code or a stored text: the cell keeps its own fallback.
    # A failed agent row's code gives the agent sentence; an older agent row
    # stored only the graph's text, so its cell keeps the fallback.
    assert (
        error_of(
            grid_try(1, "error", {"code": "agent_failed", "message": "stored text"})
        )
        == AGENT_FAILED
    )
    assert (
        error_of(
            grid_try(
                1, "error", {"type": "GraphExecutionFailure", "message": "graph text"}
            )
        )
        is None
    )
    assert (
        error_of(grid_try(1, "error", {"code": "something_else", "message": "x"}))
        is None
    )
    assert error_of(grid_try(1, "error", "as stored")) is None
    # The first failed try that has a code.
    assert (
        error_of(
            grid_try(1, "completed"),
            grid_try(2, "error", {"message": "no code"}),
            grid_try(3, "infra", {"code": "provider_failed"}),
        )
        == PROVIDER_ERROR
    )
    # A named sentence stays bounded.
    long = "x" * 2100
    assert (
        error_of(
            grid_try(1, "error", {"code": "model_not_in_catalog", "message": long})
        )
        == long[:2000]
    )
    assert error_of(grid_try(1, "completed")) is None
    assert error_of(grid_try(1, "queued")) is None
    assert error_of() is None


def test_a_runs_first_error_is_its_first_coded_failure_in_example_and_trial_order():
    from model_hub.services.results_grid import first_failure_message

    def item(status, error=None):
        return SimpleNamespace(status=status, error=error)

    evidence = {
        ("b", 1): item("failed", {"code": "provider_failed"}),
        ("a", 2): item(
            "failed", {"type": "GraphExecutionFailure", "message": "graph text"}
        ),
        ("a", 1): item("completed"),
        ("c", 1): item("failed", {"code": "row_failed"}),
    }
    assert first_failure_message(evidence) == PROVIDER_ERROR
    assert (
        first_failure_message({("a", 1): item("failed", {"message": "no code"})})
        is None
    )
    assert first_failure_message({}) is None


def test_every_fixed_code_records_its_own_sentence():
    from model_hub.services.row_failures import coded_error, coded_message

    for code, sentence in [
        ("row_failed", RUN_FAILED),
        ("provider_failed", PROVIDER_ERROR),
        ("agent_failed", AGENT_FAILED),
        ("agent_cancelled", AGENT_CANCELLED),
        ("evaluation_failed", EVALUATION_FAILED),
    ]:
        error = coded_error("Failure", code)
        assert error == {"type": "Failure", "code": code, "message": sentence}
        assert coded_message(error) == sentence
    for sentence in (AGENT_FAILED, AGENT_CANCELLED, EVALUATION_FAILED):
        assert "Native" not in sentence


def test_the_run_contract_carries_first_error():
    from model_hub.serializers.results_runs import CanonicalRunSerializer

    field = CanonicalRunSerializer().fields["first_error"]
    assert field.allow_null and not field.required
    assert field.to_representation(RUN_FAILED) == RUN_FAILED


def test_a_failed_agent_rows_evaluators_say_its_agent_run_failed():
    from model_hub.views import eval_runner, experiment_runner

    recorder = object.__new__(experiment_runner._NativeGraphRecorder)
    recorder.plan = {
        "terminal_column_ids": ["column-1"],
        "node_column_mapping": {"node-1": "column-1"},
    }
    recorder.run = SimpleNamespace(
        evaluator_versions=[
            {"metric_id": "metric-1", "evaluator_version_id": "version-1"}
        ],
        experiment=SimpleNamespace(run_policy={}),
    )
    recorder.state = {"evaluator_results": {}, "node_results": {}}
    recorder.locked = nullcontext
    recorder.save = lambda: None
    recorder.dataset = recorder.binding = None
    recorder.item = SimpleNamespace(input={"values": {}})
    recorder.stopped = False
    recorder.row_id = "row-1"
    evaluator = MagicMock()
    with (
        patch.object(eval_runner, "NativeEvaluationRunner", return_value=evaluator),
        patch(
            "model_hub.services.experiment_utils.is_user_eval_stopped",
            return_value=False,
        ),
    ):
        recorder.evaluate(graph_failed=True)
    evaluator.record_upstream_failure.assert_called_once_with(
        "row-1", message=AGENT_NOT_SCORED
    )
    assert (
        recorder.state["evaluator_results"]["column-1:metric-1"]["reason"]
        == "execution_failed"
    )


def classic_world(organization, workspace, user, *, display_name, linked):
    dataset = Dataset.objects.create(
        name="Classic catalog miss",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    experiment = ExperimentsTable.objects.create(
        name="Classic catalog miss",
        dataset=dataset,
        snapshot_dataset=dataset,
        user=user,
    )
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.RUNNING.value
    )
    target = Row.objects.create(dataset=dataset, order=0)
    variant = ExperimentDatasetTable.objects.create(name="A", experiment=experiment)
    ExperimentPromptConfig.objects.create(
        experiment_dataset=variant,
        name="A",
        model=MODEL,
        model_display_name=display_name,
    )
    column = Column.objects.create(
        name="A",
        dataset=dataset,
        data_type="text",
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id) if linked else None,
    )
    variant.columns.add(column)
    Cell.objects.create(
        dataset=dataset,
        row=target,
        column=column,
        value="",
        status=CellStatus.RUNNING.value,
    )
    return experiment, target, column


@pytest.mark.parametrize(
    "display_name,linked,shown",
    [
        (None, True, MODEL),
        ("Retired Model A", True, "Retired Model A"),
        ("Retired Model A", False, MODEL),
    ],
    ids=["no_display_name", "display_name", "column_without_its_variant"],
)
def test_a_classic_row_whose_model_the_catalog_lacks_names_it_in_its_cell(
    organization, workspace, user, display_name, linked, shown
):
    from model_hub.views.experiment_runner import _process_row_impl

    experiment, target, column = classic_world(
        organization, workspace, user, display_name=display_name, linked=linked
    )
    with patch(PROVIDER) as provider:
        _process_row_impl(
            row_id=target.id,
            column_id=column.id,
            dataset_id=experiment.dataset_id,
            experiment_id=experiment.id,
            messages=[{"role": "user", "content": "Say hi"}],
            model=MODEL,
            model_config={},
            skip_celery_evals=True,
        )
    provider.assert_not_called()
    sentence = UNCATALOGUED.format(model=shown)
    cell = Cell.no_workspace_objects.get(column=column, row=target)
    assert (cell.value, cell.status) == (sentence, CellStatus.ERROR.value)
    assert reason_of(cell) == {"reason": sentence}
