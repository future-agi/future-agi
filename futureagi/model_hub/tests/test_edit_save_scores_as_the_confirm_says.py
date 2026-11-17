"""Edit and rerun: what each save does to scores, as its confirm says it.

One test per sentence of the Edit confirm and its footer total. The experiment
is made by the real create view with two prompt variants (each with a saved
prompt version) and one agent variant, and saved through the real update view
(PUT /model-hub/experiments/v2/<id>/). The run is RunExperimentV2Workflow.run
itself for the input the save starts: its activities are the real activity
functions (run in an activity environment), its native child runs are run row
by row through the real worker bodies, and the background evaluation task (the
sweeper) ticks between rows and before every batch.

Answered here instead of a Temporal server: the workflow start (its arguments
are kept), workflow.patched (True, as for a new run), workflow.memo (the
save's admission id), the native admission handshake (READY), the row's
provider call, the evaluator engine and the API call log boundary (both
recorded), the usage event emitter (recorded), the usage preflight and the
provider-key route.
"""

import asyncio
import dataclasses
import json
import uuid
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.experiments import (
    ExperimentAgentConfig,
    ExperimentPromptConfig,
)
from model_hub.services.credit_ledger_port import PayerRoute
from model_hub.services.experiment_submit import state_of
from model_hub.services.native_run_recording import mark_native_run
from model_hub.tasks import user_evaluation
from model_hub.tests import test_experiment_append_variant as fixtures
from model_hub.tests import test_experiment_native_graph as graph_fixture
from model_hub.tests.test_native_prompt_create_bridge import (
    PROVIDER,
    START,
    create,
    create_body,
    finish,
    native_eval_transport,
    native_preflight,
    row,
)
from model_hub.tests.test_native_prompt_create_bridge import (
    native_fixture_evaluator as native_fixture_evaluator,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants
from model_hub.tests.test_pinned_version_native import usage_events
from model_hub.views import eval_runner
from tfc.middleware.workspace_context import workspace_context
from tfc.temporal.experiments import activities

pytestmark = pytest.mark.django_db(transaction=True)

RERUN_START = "tfc.temporal.experiments.start_rerun_cells_v2_workflow"


@pytest.fixture(autouse=True)
def payer_stub():
    with patch(
        "model_hub.services.credit_ledger_port.resolve_payer_route",
        return_value=PayerRoute.byok("openai"),
    ):
        yield


# --- what was scored, and by whom ----------------------------------------

_SCORER = ["test setup"]


@contextmanager
def scored_by(name):
    _SCORER.append(name)
    try:
        yield
    finally:
        _SCORER.pop()


def _scored_column(runner):
    """The experiment output column a runner scores, or None."""
    column = getattr(runner, "column", None)
    if getattr(runner, "experiment_dataset", None) is None or column is None:
        return None
    return str(column.id)


@contextmanager
def scoring(score=1):
    """Each row an evaluation scores (each requests one API call log entry),
    the API call log entries requested and the usage events emitted, with
    who scored it and on which experiment output column. The evaluator
    answers ``score`` for every row."""
    real_handle = eval_runner.EvaluationRunner._handle_api_call
    real_emit = eval_runner.EvaluationRunner._emit_cell_usage
    scored, emitted = [], []

    def handle(self, row, *args, **kwargs):
        log_row = real_handle(self, row, *args, **kwargs)
        scored.append(
            (
                _SCORER[-1],
                type(self).__name__,
                str(self.user_eval_metric_id),
                _scored_column(self),
                str(getattr(row, "id", row)),
            )
        )
        return log_row

    def emit(self, *args, **kwargs):
        emitted.append(
            (_SCORER[-1], str(self.user_eval_metric_id), _scored_column(self))
        )
        return real_emit(self, *args, **kwargs)

    def rows_on(metric, column):
        return sorted(
            entry[4]
            for entry in scored
            if entry[2] == str(metric.id) and entry[3] == str(column)
        )

    def describe():
        lines = [
            f"{who} ({runner}) scored evaluation {metric_id} column {column_id} "
            f"row {row_id}"
            for who, runner, metric_id, column_id, row_id in scored
        ]
        lines += [
            f"{who} emitted a usage event for evaluation {metric_id} column {column_id}"
            for who, metric_id, column_id in emitted
        ]
        return "\n".join(lines) or "nothing was scored"

    with ExitStack() as stack:
        _engine, api_log = stack.enter_context(native_eval_transport(score=score))
        stack.enter_context(
            patch.object(eval_runner.EvaluationRunner, "_handle_api_call", handle)
        )
        stack.enter_context(
            patch.object(eval_runner.EvaluationRunner, "_emit_cell_usage", emit)
        )
        stack.enter_context(usage_events())
        stack.enter_context(
            native_preflight(MagicMock(return_value=SimpleNamespace(allowed=True)))
        )
        stack.enter_context(
            patch(PROVIDER, autospec=True, return_value=("actual output", {}))
        )
        yield SimpleNamespace(
            rows_on=rows_on,
            scored_metric=lambda metric: [
                entry for entry in scored if entry[2] == str(metric.id)
            ],
            api_log_entries=lambda metric: sum(
                1
                for call in api_log.call_args_list
                if call.kwargs["config"].get("reference_id") == str(metric.id)
            ),
            events_on=lambda metric, column: sum(
                1
                for _who, metric_id, column_id in emitted
                if metric_id == str(metric.id) and column_id == str(column)
            ),
            events_of=lambda metric: [
                entry for entry in emitted if entry[1] == str(metric.id)
            ],
            describe=describe,
        )


# --- the background evaluation task ---------------------------------------


@contextmanager
def sweeper():
    """The real sweeper tick; what it dispatches is kept, not run."""
    dispatched = []

    def dispatch(args=None, kwargs=None, **options):
        dispatched.append(dict(args[0]))

    with (
        patch.object(user_evaluation, "close_old_connections"),
        patch.object(
            user_evaluation.process_evaluation_single_task,
            "apply_async",
            side_effect=dispatch,
        ),
    ):
        yield SimpleNamespace(
            tick=user_evaluation.execute_evaluation._original_func,
            dispatched=dispatched,
        )


def background_tick(sweep):
    """A tick as the scheduled worker runs it: no request context."""
    with workspace_context(None):
        sweep.tick()


def dispatched_ids(sweep):
    return [evaluation["eval_id"] for evaluation in sweep.dispatched]


@contextmanager
def ticking_between_batches(sweep):
    """A tick before each batch of an eval-only batch activity: the batch
    loop asks whether the experiment was stopped before every batch."""
    from model_hub.services import experiment_utils

    real = experiment_utils.is_experiment_cancelled

    def tick_then_check(experiment_id):
        background_tick(sweep)
        return real(experiment_id)

    with patch.object(experiment_utils, "is_experiment_cancelled", tick_then_check):
        yield


# --- the experiment ---------------------------------------------------------


def run_native(experiment, context, *, between=None):
    """One native child run (a prompt run or an eval-only run), row by row."""
    prepared = activities._setup_prompt_v2_sync(
        str(experiment.id),
        str(experiment.snapshot_dataset_id),
        context["config_id"],
        native_context=context,
    )
    for row_id in prepared["row_ids"]:
        row(experiment, context, prepared, row_id)
        if between is not None:
            between()
    finish(context, prepared)
    return prepared


def versioned_prompt_entry(actor, column, name, variant):
    """A prompt run entry on its own template and saved version, whose message
    reads the dataset's input column."""
    template, version = fixtures.prompt(actor, name)
    version.prompt_config_snapshot["messages"] = [
        {"role": "user", "content": "{{" + str(column.id) + "}}"}
    ]
    version.save(update_fields=["prompt_config_snapshot"])
    return {**fixtures.entry(template, version), "name": variant}


def experiment_with_three_variants(actor, *, with_agent=True):
    """Two prompt variants and one agent variant, one evaluation, made by the
    create view; the prompts' first run finished. The agent's first run is
    stopped and its outputs are written as the agent wrote them. With
    with_agent=False the experiment has the two prompt variants only."""
    dataset = actor["exp"]["dataset"]
    column = Column.no_workspace_objects.get(dataset=dataset)
    for order in (1, 2):
        extra = Row.no_workspace_objects.create(dataset=dataset, order=order)
        Cell.no_workspace_objects.create(
            dataset=dataset, column=column, row=extra, value=f"input-{order}"
        )
    body = create_body(actor)
    first = {**body["prompt_config"][0], "name": "First variant"}
    second = versioned_prompt_entry(actor, column, "second prompt", "Second variant")
    agent = None
    if with_agent:
        graph, graph_version, _node = graph_fixture.make_graph(actor)
        agent = {"agent_id": str(graph.id), "agent_version": str(graph_version.id)}
    body["prompt_config"] = [first, second, *([agent] if agent else [])]
    response, experiment, body, _start = create(actor, body)
    assert response.status_code == 200, response.content

    contexts = state_of(experiment)["workflow_args"]["native_runs"]
    with scoring():
        for context in contexts.values():
            if context.get("execution_mode") != "graph":
                run_native(experiment, context)
    snapshot_rows = list(
        Row.no_workspace_objects.filter(
            dataset=experiment.snapshot_dataset, deleted=False
        ).order_by("order")
    )
    agent_output = agent_config = None
    if agent:
        graph_context = next(
            context
            for context in contexts.values()
            if context.get("execution_mode") == "graph"
        )
        mark_native_run(
            dataset=experiment.dataset,
            run_id=graph_context["run_id"],
            status="cancelled",
        )
        agent_output = Column.no_workspace_objects.get(
            pk=graph_context["output_column_id"]
        )
        for snapshot_row in snapshot_rows:
            Cell.no_workspace_objects.update_or_create(
                dataset=experiment.snapshot_dataset,
                column=agent_output,
                row=snapshot_row,
                defaults={"value": "agent output", "status": "pass", "deleted": False},
            )
        agent_config = ExperimentAgentConfig.no_workspace_objects.get(
            experiment_dataset__experiment=experiment
        )
    experiment.refresh_from_db()
    configs = {
        config.prompt_template_id: config
        for config in ExperimentPromptConfig.no_workspace_objects.filter(
            experiment_dataset__experiment=experiment, deleted=False
        )
    }
    first_config = configs[uuid.UUID(first["prompt_id"])]
    second_config = configs[uuid.UUID(second["prompt_id"])]
    metric = experiment.user_eval_template_ids.get()
    return SimpleNamespace(
        actor=actor,
        experiment=experiment,
        metric=metric,
        metric_entry=body["user_eval_metrics"][0],
        first=first,
        second=second,
        agent=agent,
        first_config=first_config,
        second_config=second_config,
        first_output=contexts[str(first_config.id)]["output_column_id"],
        second_output=contexts[str(second_config.id)]["output_column_id"],
        agent_config=agent_config,
        agent_output=str(agent_output.id) if agent_output else None,
        rows=[str(snapshot_row.id) for snapshot_row in snapshot_rows],
        url=f"/model-hub/experiments/v2/{experiment.id}/",
        input_column=column,
    )


def kept_entry(world):
    """The experiment's evaluation exactly as saved: nothing in it changes."""
    entry = {**world.metric_entry, "id": str(world.metric.id)}
    if world.metric.model:
        entry["model"] = world.metric.model
    return entry


def edited_entry(world):
    """The evaluation with its settings changed."""
    return {
        **kept_entry(world),
        "config": {"mapping": {"output": "output"}, "config": {"rubric": "stricter"}},
    }


def new_evaluation_entry(actor):
    """A new evaluation (no id), on its own template and version."""
    name = "added-check-" + uuid.uuid4().hex
    template = EvalTemplate.no_workspace_objects.create(
        name=name,
        owner="user",
        eval_type="llm",
        organization=actor["org"],
        workspace=actor["workspace"],
        criteria="Evaluate {{output}}",
        config={
            "eval_type_id": "NativeFixture",
            "output": "score",
            "required_keys": ["output"],
        },
    )
    version = EvalTemplateVersion.no_workspace_objects.create(
        eval_template=template,
        version_number=1,
        is_default=True,
        organization=actor["org"],
        workspace=actor["workspace"],
        config_snapshot=dict(template.config),
    )
    return {
        "name": name,
        "template_id": str(template.id),
        "pinned_version_id": str(version.id),
        "config": {"mapping": {"output": "output"}},
    }


def save(world, *, metrics, change_prompt=False, new_runs=()):
    """Edit and rerun through the real update view. With change_prompt the
    first prompt variant's model changes in the same save; the second prompt
    and the agent are sent unchanged. ``new_runs`` are entries added in the
    same save (no id)."""
    first = {**world.first, "id": str(world.first_config.id)}
    if change_prompt:
        first["model"] = "gpt-4o"
    kept = [{**world.second, "id": str(world.second_config.id)}]
    if world.agent:
        kept.append({**world.agent, "id": str(world.agent_config.id)})
    body = {
        "prompt_config": [first, *kept, *new_runs],
        "user_eval_metrics": metrics,
    }
    with patch(START, return_value="updated-workflow") as started:
        response = world.actor["client"].put(world.url, body, format="json")
    assert response.status_code == 200, response.content
    started.assert_called_once()
    return started.call_args.kwargs


def evaluation_cells(world, metric, output_column_id):
    """The cells of ``metric`` on one variant's output."""
    output = Column.no_workspace_objects.get(pk=output_column_id)
    column = Column.no_workspace_objects.get(
        dataset=world.experiment.snapshot_dataset,
        source_id=f"{output.source_id}-{output.id}-sourceid-{metric.id}",
        deleted=False,
    )
    return list(Cell.no_workspace_objects.filter(column=column, deleted=False))


def output_values(columns):
    """Each output column's values, by row."""
    return {
        str(column): {
            str(cell.row_id): cell.value
            for cell in Cell.no_workspace_objects.filter(
                column_id=column, deleted=False
            )
        }
        for column in columns
    }


def _input_of(input_type, started):
    names = {field.name for field in dataclasses.fields(input_type)}
    return input_type(**{key: value for key, value in started.items() if key in names})


def run_workflow(world, workflow_class, input_type, started, sweep):
    """``workflow_class``.run itself for the input the request started.

    Every activity is the real activity function, run in an activity
    environment; each native child is run row by row through the worker
    bodies, with a sweeper tick after each row.
    """
    from temporalio.testing import ActivityEnvironment

    from tfc.temporal.experiments import workflows

    names, children, returned = [], [], []

    def between():
        background_tick(sweep)

    async def execute(name, value, **kwargs):
        names.append(name)
        if name == "setup_experiment_activity" and getattr(
            value, "admission_only", False
        ):
            return {"status": "READY"}
        result = await ActivityEnvironment().run(getattr(activities, name), value)
        returned.append((name, result))
        return result

    async def child(_run, value, **kwargs):
        children.append(value)
        completed = 0
        if value.native_context:
            prepared = await asyncio.to_thread(
                run_native, world.experiment, value.native_context, between=between
            )
            completed = len(prepared["row_ids"])

        async def done():
            return {
                "status": "COMPLETED",
                "completed_rows": completed,
                "failed_rows": 0,
            }

        return asyncio.create_task(done())

    memo = {"experiment_submission_id": started.get("admission_id")}
    with (
        patch.object(workflows.workflow, "execute_activity", side_effect=execute),
        patch.object(workflows.workflow, "start_child_workflow", side_effect=child),
        patch.object(workflows.workflow, "patched", return_value=True),
        patch.object(workflows.workflow, "memo", return_value=memo),
        scored_by("experiment run"),
        ticking_between_batches(sweep),
        workspace_context(None),
    ):
        result = asyncio.run(
            getattr(workflows, workflow_class)().run(_input_of(input_type, started))
        )
    return SimpleNamespace(
        result=result, names=names, children=children, returned=returned
    )


def run_experiment_workflow(world, started, sweep):
    from tfc.temporal.experiments.types import RunExperimentInput

    return run_workflow(
        world, "RunExperimentV2Workflow", RunExperimentInput, started, sweep
    )


def run_the_evaluation_again(world):
    """Run on the evaluation, as the Evaluations drawer's Run sends it through
    start_evals_process: rerun-cells with the evaluation, then the
    RerunCellsV2Workflow it starts, with a sweeper tick after it."""
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput

    with patch(RERUN_START, return_value="rerun-workflow") as rerun_started:
        response = world.actor["client"].post(
            f"/model-hub/experiments/v2/{world.experiment.id}/rerun-cells/",
            {"user_eval_metric_ids": [str(world.metric.id)]},
            format="json",
        )
    assert response.status_code == 200, response.content
    rerun_started.assert_called_once()
    rerun = rerun_started.call_args.kwargs
    assert rerun["eval_only"] is True
    assert rerun["eval_template_ids"] == [str(world.metric.id)]
    with sweeper() as sweep, scoring() as scores:
        run = run_workflow(
            world, "RerunCellsV2Workflow", RerunCellsV2WorkflowInput, rerun, sweep
        )
        background_tick(sweep)
    return SimpleNamespace(run=run, scores=scores, sweep=sweep)


def scored_on_every_run(world):
    """Real scores of the evaluation on every variant's output before a save.
    The prompts' come from their first run. The agent's are made the way Run
    on the evaluation scores an agent's output (RerunCellsV2Workflow's
    eval-only step: one batch activity over every row, ten rows a batch).
    Run on the evaluation itself is refused at this point: the create's own
    execution still counts as going (the agent's first run is only stopped
    here, not run), and rerun-cells refuses a subset while one is."""
    with (
        sweeper() as sweep,
        scoring() as scores,
        scored_by("experiment run"),
        ticking_between_batches(sweep),
        workspace_context(None),
    ):
        result = activities._process_batch_template_eval_sync(
            world.rows,
            world.agent_output,
            str(world.experiment.snapshot_dataset_id),
            str(world.experiment.id),
            str(world.metric.id),
            rows_per_batch=10,
        )
    report = (result, scores.describe())
    assert result["status"] == "COMPLETED", report
    assert scores.rows_on(world.metric, world.agent_output) == sorted(world.rows), (
        report
    )
    assert scores.events_on(world.metric, world.agent_output) == len(world.rows), report
    assert str(world.metric.id) not in dispatched_ids(sweep), report
    for output in (world.first_output, world.second_output):
        cells = evaluation_cells(world, world.metric, output)
        seen = (report, [(cell.value, cell.status) for cell in cells])
        assert sorted(str(cell.row_id) for cell in cells) == sorted(world.rows), seen
        assert all(
            cell.value not in ("", None) and cell.status != "running" for cell in cells
        ), seen
    agent_cells = evaluation_cells(world, world.metric, world.agent_output)
    seen = (report, [(cell.value, cell.status) for cell in agent_cells])
    assert sorted(str(cell.row_id) for cell in agent_cells) == sorted(world.rows)
    assert all(
        cell.value not in ("", None) and cell.status == "pass" for cell in agent_cells
    ), seen
    return {str(cell.row_id): cell.value for cell in agent_cells}


def still_running(world):
    """What the finish check would wait for: each of the experiment's columns
    still running or holding running cells, with its source and counts (no
    values)."""
    from model_hub.models.choices import CellStatus, StatusType

    columns = Column.no_workspace_objects.filter(
        experiments_dataset_column__experiment=world.experiment, deleted=False
    ).distinct()
    waiting = []
    for column in columns:
        running = Cell.no_workspace_objects.filter(
            column=column, deleted=False, status=CellStatus.RUNNING.value
        ).count()
        if running or column.status == StatusType.RUNNING.value:
            waiting.append((column.source, column.source_id, column.status, running))
    return waiting


def assert_scored_once_on(world, metric, outputs, scores):
    report = scores.describe()
    for output in outputs:
        assert scores.rows_on(metric, output) == sorted(world.rows), report
        assert scores.events_on(metric, output) == len(world.rows), report
        cells = evaluation_cells(world, metric, output)
        assert sorted(str(cell.row_id) for cell in cells) == sorted(world.rows), report
        assert all(
            cell.value not in ("", None) and cell.status != "running" for cell in cells
        ), report
    assert scores.api_log_entries(metric) == len(world.rows) * len(outputs), report


def run_items(run_id):
    """A run's recorded results: each item's id, status, output and evaluator
    outcomes, by example."""
    from model_hub.models.experiments import ExperimentRunItem

    return {
        str(item.example_id): (str(item.id), item.status, item.output, item.score_refs)
        for item in ExperimentRunItem.no_workspace_objects.filter(run_id=run_id)
    }


def outcomes_of(items, metric):
    """Each recorded item's outcomes for ``metric``, by example."""
    return {
        example: [ref for ref in refs if ref.get("metric_id") == str(metric.id)]
        for example, (_id, _status, _output, refs) in items.items()
    }


def cell_states(world, metric, output_column_id):
    """(value, status) of each of ``metric``'s cells on one output, by row."""
    return {
        str(cell.row_id): (cell.value, cell.status)
        for cell in evaluation_cells(world, metric, output_column_id)
    }


def grid_scores(world, run_id, metric):
    """The results grid's answer for one run, through the read the experiment
    page's results make (GET datasets/<id>/results/): each example's score of
    ``metric`` for that run, as the grid gives it."""
    from django.urls import reverse

    response = world.actor["client"].get(
        reverse("dataset-results", kwargs={"dataset_id": world.experiment.dataset_id}),
        {"runs": run_id, "shape": "grid"},
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] is True, body
    return {
        row["example_id"]: (
            row["results"][run_id]["scores"][str(metric.id)]["status"],
            row["results"][run_id]["scores"][str(metric.id)]["value"],
        )
        for row in body["result"]["rows"]
    }


# --- the cases --------------------------------------------------------------


def test_an_evaluation_only_save_keeps_outputs_and_scores_every_run_once(tenants):
    """Kept runs keep their outputs. The edited evaluation is scored again on
    every run."""
    world = experiment_with_three_variants(tenants["a"])
    outputs = [world.first_output, world.second_output, world.agent_output]
    before = output_values(outputs)

    started = save(world, metrics=[edited_entry(world)])

    assert started["rerun_prompt_config_ids"] == []
    assert started["rerun_agent_config_ids"] == []
    assert started["rerun_eval_template_ids"] == [str(world.metric.id)]
    # Both saved-version prompts get an eval-only run on their own outputs.
    native = started["native_runs"]
    assert {context["execution_mode"] for context in native.values()} == {"eval_only"}
    assert {context["output_column_id"] for context in native.values()} == {
        world.first_output,
        world.second_output,
    }
    with sweeper() as sweep, scoring() as scores:
        run = run_experiment_workflow(world, started, sweep)
        background_tick(sweep)

    report = (run.names, scores.describe())
    # Every variant's outputs are unchanged.
    assert output_values(outputs) == before, report
    # The edited evaluation is scored once per row on every variant's output:
    # the prompts by their eval-only runs, the agent by an eval-only batch.
    assert_scored_once_on(world, world.metric, outputs, scores)
    assert str(world.metric.id) not in dispatched_ids(sweep), report
    assert "process_batch_template_eval_activity" in run.names, report
    # This experiment has no reference column, so the run has no base
    # evaluation to start.
    assert world.experiment.column_id is None
    assert "run_base_evaluation_activity" not in run.names, report


def test_an_evaluation_only_save_keeps_the_reference_column_scores(tenants):
    """With a reference column, the same save asks for the edited evaluation
    on it too, finds its earlier scores there and keeps them: the reference
    column is not a run and is not scored again. The save marks each kept
    score as from before the edit, the one change to it, and the run writes
    nothing to the reference column."""
    from model_hub.models.choices import SourceChoices
    from model_hub.views.experiments import SCORED_BEFORE_EDIT
    from tfc.utils.distributed_state import evaluation_tracker

    world = experiment_with_three_variants(tenants["a"])
    snapshot = world.experiment.snapshot_dataset
    world.experiment.column = Column.no_workspace_objects.create(
        name="reference",
        dataset=snapshot,
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    world.experiment.save(update_fields=["column"])
    # The evaluation's scores on the reference column from the first run.
    reference_scores = Column.no_workspace_objects.create(
        name=world.metric.name,
        dataset=snapshot,
        data_type="text",
        source=SourceChoices.EVALUATION.value,
        source_id=str(world.metric.id),
    )
    for row_id in world.rows:
        Cell.no_workspace_objects.create(
            dataset=snapshot,
            column=reference_scores,
            row_id=row_id,
            value="earlier score",
            status="pass",
        )
    reference_cells = Cell.no_workspace_objects.filter(
        column=reference_scores
    ).order_by("pk")
    earlier = list(reference_cells.values())
    assert [cell["value_infos"] for cell in earlier] == [[] for _ in world.rows]
    outputs = [world.first_output, world.second_output, world.agent_output]

    started = save(world, metrics=[edited_entry(world)])

    # The save keeps every earlier score and marks it as from before the edit;
    # nothing else in the reference column's cells changes.
    marked = [
        {**cell, "value_infos": json.dumps({SCORED_BEFORE_EDIT: True})}
        for cell in earlier
    ]
    saved = json.dumps(list(reference_cells.values()), sort_keys=True, default=str)
    assert saved == json.dumps(marked, sort_keys=True, default=str)
    reference_scores.refresh_from_db()
    assert (reference_scores.metadata or {}).get(SCORED_BEFORE_EDIT) is True

    with (
        sweeper() as sweep,
        scoring() as scores,
        patch.object(evaluation_tracker, "should_cancel", return_value=False),
    ):
        run = run_experiment_workflow(world, started, sweep)
        background_tick(sweep)

    report = (run.names, scores.describe())
    # The run asks for the edited evaluation on the reference column, and
    # that step finds it scored and leaves it.
    reference_steps = [
        result.status
        for name, result in run.returned
        if name == "run_base_evaluation_activity"
    ]
    assert reference_steps == ["SKIPPED"], report
    not_on_a_run = [
        entry for entry in scores.scored_metric(world.metric) if entry[3] is None
    ]
    assert not_on_a_run == [], report
    # Usage events for the evaluation: one per row on each run, none elsewhere.
    events = scores.events_of(world.metric)
    assert [entry for entry in events if entry[2] is None] == [], report
    assert len(events) == len(world.rows) * len(outputs), report
    # The run writes nothing to the reference column: its cells and its mark
    # are as the save left them.
    after = json.dumps(list(reference_cells.values()), sort_keys=True, default=str)
    assert after == saved, report
    reference_scores.refresh_from_db()
    assert (reference_scores.metadata or {}).get(SCORED_BEFORE_EDIT) is True, report
    assert str(world.metric.id) not in dispatched_ids(sweep), report
    # Every run is still scored once.
    assert_scored_once_on(world, world.metric, outputs, scores)


def test_a_mixed_save_scores_the_edited_evaluation_again_on_every_run(tenants):
    """Kept runs keep their outputs. The edited evaluation is scored again on
    every run: the re-run prompt in its own run, the unchanged prompt with a
    saved version in its eval-only run, and the unchanged agent in an eval-only
    batch. Nothing is left waiting, so the run finishes without a Stop."""
    from model_hub.models.choices import StatusType

    world = experiment_with_three_variants(tenants["a"])
    # The evaluation has real scores on every run before the save.
    scored_on_every_run(world)
    kept_outputs = [world.second_output, world.agent_output]
    before = output_values(kept_outputs)

    started = save(world, metrics=[edited_entry(world)], change_prompt=True)

    native = started["native_runs"]
    assert started["rerun_prompt_config_ids"] == [str(world.first_config.id)]
    assert started["rerun_agent_config_ids"] == []
    assert started["rerun_eval_template_ids"] == [str(world.metric.id)]
    assert native[str(world.first_config.id)]["execution_mode"] == "prompt"
    assert native[str(world.second_config.id)]["execution_mode"] == "eval_only"
    assert str(world.agent_config.id) not in native
    rerun_output = native[str(world.first_config.id)]["output_column_id"]
    with sweeper() as sweep, scoring() as scores:
        run = run_experiment_workflow(world, started, sweep)
        background_tick(sweep)

    report = (run.names, scores.describe())
    # Kept runs keep their outputs.
    assert output_values(kept_outputs) == before, report
    # Every run scores the edited evaluation once per row, the agent included.
    assert_scored_once_on(
        world,
        world.metric,
        [rerun_output, world.second_output, world.agent_output],
        scores,
    )
    agent_cells = evaluation_cells(world, world.metric, world.agent_output)
    assert all(cell.status == "pass" for cell in agent_cells), report
    assert "resolve_edt_columns_excluding_activity" in run.names, report
    assert "process_batch_template_eval_activity" in run.names, report
    assert str(world.metric.id) not in dispatched_ids(sweep), report
    # The run finishes, with no Stop.
    finished = (report, still_running(world))
    assert run.result.status == StatusType.COMPLETED.value, finished
    world.experiment.refresh_from_db()
    assert world.experiment.status == StatusType.COMPLETED.value, finished
    assert "stop_experiment_cleanup_activity" not in run.names, report


def test_running_the_evaluation_again_after_a_mixed_save_scores_every_run_again(
    tenants,
):
    """Run on the evaluation after a mixed save, with no Stop first: the save's
    run has finished, so the request is accepted and the evaluation is scored
    once more on every run (the save had already scored it on every run)."""
    world = experiment_with_three_variants(tenants["a"])
    scored_on_every_run(world)
    started = save(world, metrics=[edited_entry(world)], change_prompt=True)
    rerun_output = started["native_runs"][str(world.first_config.id)][
        "output_column_id"
    ]
    with sweeper() as sweep, scoring():
        run_experiment_workflow(world, started, sweep)
    agent_cells = evaluation_cells(world, world.metric, world.agent_output)
    assert all(cell.status == "pass" for cell in agent_cells)

    again = run_the_evaluation_again(world)

    report = (again.run.names, again.scores.describe())
    assert_scored_once_on(
        world,
        world.metric,
        [rerun_output, world.second_output, world.agent_output],
        again.scores,
    )
    agent_cells = evaluation_cells(world, world.metric, world.agent_output)
    assert all(cell.status == "pass" for cell in agent_cells), report
    assert str(world.metric.id) not in dispatched_ids(again.sweep), report


def test_a_save_that_only_adds_an_evaluation_scores_it_on_every_run(tenants):
    """Kept runs keep their outputs. The added evaluation is scored on every
    run."""
    from model_hub.models.evals_metric import UserEvalMetric

    world = experiment_with_three_variants(tenants["a"])
    outputs = [world.first_output, world.second_output, world.agent_output]
    before = output_values(outputs)
    added_entry = new_evaluation_entry(world.actor)

    started = save(world, metrics=[kept_entry(world), added_entry])

    added = UserEvalMetric.no_workspace_objects.get(
        name=added_entry["name"], source_id=str(world.experiment.id), deleted=False
    )
    assert started["rerun_prompt_config_ids"] == []
    assert started["rerun_agent_config_ids"] == []
    # Only the added evaluation is scored; the kept one is not in the run.
    assert started["rerun_eval_template_ids"] == [str(added.id)]
    with sweeper() as sweep, scoring() as scores:
        run = run_experiment_workflow(world, started, sweep)
        background_tick(sweep)

    report = (run.names, scores.describe())
    assert output_values(outputs) == before, report
    assert_scored_once_on(world, added, outputs, scores)
    assert scores.scored_metric(world.metric) == [], report
    assert str(added.id) not in dispatched_ids(sweep), report


def test_a_save_that_edits_and_adds_evaluations_scores_both_on_every_run(tenants):
    """Kept runs keep their outputs. The edited and added evaluations are
    scored on every run: the prompts with a saved version in their eval-only
    runs, the agent in eval-only batches."""
    from model_hub.models.evals_metric import UserEvalMetric

    world = experiment_with_three_variants(tenants["a"])
    outputs = [world.first_output, world.second_output, world.agent_output]
    before = output_values(outputs)
    added_entry = new_evaluation_entry(world.actor)

    started = save(world, metrics=[edited_entry(world), added_entry])

    added = UserEvalMetric.no_workspace_objects.get(
        name=added_entry["name"], source_id=str(world.experiment.id), deleted=False
    )
    assert started["rerun_prompt_config_ids"] == []
    assert started["rerun_agent_config_ids"] == []
    assert sorted(started["rerun_eval_template_ids"]) == sorted(
        [str(world.metric.id), str(added.id)]
    )
    native = started["native_runs"]
    assert {context["execution_mode"] for context in native.values()} == {"eval_only"}
    assert {context["output_column_id"] for context in native.values()} == {
        world.first_output,
        world.second_output,
    }
    with sweeper() as sweep, scoring() as scores:
        run = run_experiment_workflow(world, started, sweep)
        background_tick(sweep)

    report = (run.names, scores.describe())
    # Every run keeps its outputs.
    assert output_values(outputs) == before, report
    # Each evaluation is scored once per row on every run's output.
    assert_scored_once_on(world, world.metric, outputs, scores)
    assert_scored_once_on(world, added, outputs, scores)
    assert "process_batch_template_eval_activity" in run.names, report
    for metric in (world.metric, added):
        assert str(metric.id) not in dispatched_ids(sweep), report


def test_an_evaluation_added_with_a_rerun_prompt_is_scored_on_kept_runs_too(tenants):
    """An evaluation added in the same save as a re-run prompt: the re-run
    prompt scores it in its own run, and the kept runs are scored too, the
    prompt with a saved version in its eval-only run and the agent in an
    eval-only batch. The footer's amount for the re-run leaves that out and
    says so."""
    from model_hub.models.evals_metric import UserEvalMetric

    world = experiment_with_three_variants(tenants["a"])
    kept_outputs = [world.second_output, world.agent_output]
    before = output_values(kept_outputs)
    added_entry = new_evaluation_entry(world.actor)

    started = save(world, metrics=[kept_entry(world), added_entry], change_prompt=True)

    added = UserEvalMetric.no_workspace_objects.get(
        name=added_entry["name"], source_id=str(world.experiment.id), deleted=False
    )
    native = started["native_runs"]
    assert started["rerun_prompt_config_ids"] == [str(world.first_config.id)]
    assert started["rerun_agent_config_ids"] == []
    assert started["rerun_eval_template_ids"] == [str(added.id)]
    assert native[str(world.first_config.id)]["execution_mode"] == "prompt"
    assert native[str(world.second_config.id)]["execution_mode"] == "eval_only"
    assert str(world.agent_config.id) not in native
    rerun_output = native[str(world.first_config.id)]["output_column_id"]
    with sweeper() as sweep, scoring() as scores:
        run = run_experiment_workflow(world, started, sweep)
        background_tick(sweep)

    report = (run.names, scores.describe())
    # Kept runs keep their outputs.
    assert output_values(kept_outputs) == before, report
    # The added evaluation is scored once per row on every run, kept ones too.
    assert_scored_once_on(
        world,
        added,
        [rerun_output, world.second_output, world.agent_output],
        scores,
    )
    assert "resolve_edt_columns_excluding_activity" in run.names, report
    assert "process_batch_template_eval_activity" in run.names, report
    assert str(added.id) not in dispatched_ids(sweep), report


def test_a_rerun_keeps_the_earlier_runs_record_and_writes_the_same_cells(tenants):
    """A save that edits the evaluation and changes a prompt starts a new run
    of that prompt. The earlier run keeps its own recorded results, its
    scores of the edited evaluation included, and the results grid still
    shows them for that run. The new run writes the same output column and
    the same evaluation cells, which the save cleared, so after it those
    cells hold the new run's scores."""
    from model_hub.models.native_experiment_runs import NativeRunBinding

    world = experiment_with_three_variants(tenants["a"])
    bindings = NativeRunBinding.objects.filter(
        variant=world.first_config.experiment_dataset
    )
    earlier_runs = [str(run_id) for run_id in bindings.values_list("run_id", flat=True)]
    assert len(earlier_runs) == 1, earlier_runs
    [earlier] = earlier_runs
    earlier_items = run_items(earlier)
    earlier_scores = outcomes_of(earlier_items, world.metric)
    statuses = [ref["status"] for refs in earlier_scores.values() for ref in refs]
    assert len(earlier_items) == len(world.rows), earlier_items
    assert statuses == ["assessed"] * len(world.rows), earlier_items
    earlier_cells = cell_states(world, world.metric, world.first_output)
    # The results grid shows the earlier run's score on every example.
    earlier_grid = grid_scores(world, earlier, world.metric)
    assert sorted(earlier_grid) == sorted(earlier_items), earlier_grid
    assert set(earlier_grid.values()) == {("assessed", 1)}, earlier_grid

    started = save(world, metrics=[edited_entry(world)], change_prompt=True)

    context = started["native_runs"][str(world.first_config.id)]
    # The new run is a run of its own, on the same output column.
    assert context["run_id"] != earlier
    assert context["output_column_id"] == world.first_output
    # The save cleared the evaluation's cells on that column.
    cleared = cell_states(world, world.metric, world.first_output)
    assert set(cleared.values()) == {("", "running")}, cleared
    # The new run's evaluator answers differently from the earlier one's.
    with sweeper() as sweep, scoring(score=0) as scores:
        run = run_experiment_workflow(world, started, sweep)
        background_tick(sweep)

    report = (run.names, scores.describe())
    later_items = run_items(context["run_id"])
    later_scores = outcomes_of(later_items, world.metric)
    later_cells = cell_states(world, world.metric, world.first_output)
    seen = (report, earlier_items, later_items, earlier_cells, later_cells)
    earlier_values = [ref["value"] for refs in earlier_scores.values() for ref in refs]
    later_values = [ref["value"] for refs in later_scores.values() for ref in refs]
    # The earlier run's record is unchanged, its scores of the edited
    # evaluation included.
    assert run_items(earlier) == earlier_items, seen
    assert earlier_values == [1] * len(world.rows), seen
    # The new run recorded its own scores of the edited evaluation.
    assert len(later_items) == len(world.rows), seen
    assert later_values == [0] * len(world.rows), seen
    # The evaluation cells of that run's column hold the new run's scores.
    assert sorted(later_cells) == sorted(earlier_cells) == sorted(world.rows), seen
    assert later_cells != earlier_cells, seen
    assert all(
        value not in ("", None) and status != "running"
        for value, status in later_cells.values()
    ), seen
    # The results grid, read after the new run, still shows the earlier run's
    # own score of the edited evaluation for that run, not the new run's,
    # while it shows the new run's score for the new run.
    after_grid = grid_scores(world, earlier, world.metric)
    later_grid = grid_scores(world, context["run_id"], world.metric)
    grids = (seen, earlier_grid, after_grid, later_grid)
    assert after_grid == earlier_grid, grids
    assert sorted(later_grid) == sorted(earlier_grid), grids
    assert set(later_grid.values()) == {("assessed", 0)}, grids


def test_a_mixed_save_starts_a_new_run_of_the_changed_prompt_beside_the_earlier_one(
    tenants,
):
    """An evaluation edited while a prompt is changed and an agent is kept:
    the save starts a new run of the changed prompt, beside its earlier run,
    which stays as it was. The kept agent gets no new run. The kept prompt
    with a saved version gets its own eval-only run."""
    from model_hub.models.experiments import ExperimentRun
    from model_hub.models.native_experiment_runs import NativeRunBinding

    world = experiment_with_three_variants(tenants["a"])

    def runs_of(config):
        bindings = NativeRunBinding.objects.filter(variant=config.experiment_dataset)
        run_ids = bindings.values_list("run_id", flat=True)
        return sorted(str(run_id) for run_id in run_ids)

    def experiment_runs():
        runs = ExperimentRun.all_objects.filter(experiment=world.experiment)
        return {str(run_id) for run_id in runs.values_list("id", flat=True)}

    variants = {
        "changed prompt": world.first_config,
        "kept prompt": world.second_config,
        "kept agent": world.agent_config,
    }
    before = {name: runs_of(config) for name, config in variants.items()}
    assert [len(ids) for ids in before.values()] == [1, 1, 1], before
    all_before = experiment_runs()

    started = save(world, metrics=[edited_entry(world)], change_prompt=True)

    native = started["native_runs"]
    rerun = native[str(world.first_config.id)]
    kept = native[str(world.second_config.id)]
    after = {name: runs_of(config) for name, config in variants.items()}
    seen = (before, after, sorted(native))
    assert rerun["execution_mode"] == "prompt", seen
    assert kept["execution_mode"] == "eval_only", seen
    # The changed prompt: its earlier run and one new run.
    assert after["changed prompt"] == sorted(
        [before["changed prompt"][0], rerun["run_id"]]
    ), seen
    # The kept agent: no new run.
    assert after["kept agent"] == before["kept agent"], seen
    assert str(world.agent_config.id) not in native, seen
    # The kept prompt: its earlier run and its eval-only run.
    assert after["kept prompt"] == sorted([before["kept prompt"][0], kept["run_id"]]), (
        seen
    )
    # Two runs in all are new for the experiment, and none went away.
    assert experiment_runs() - all_before == {rerun["run_id"], kept["run_id"]}
    assert all_before <= experiment_runs()
    earlier = ExperimentRun.all_objects.get(pk=before["changed prompt"][0])
    new = ExperimentRun.all_objects.get(pk=rerun["run_id"])
    assert earlier.deleted is False
    # The new run takes the next number, so it is listed as a run of its own.
    assert new.ordinal > earlier.ordinal
    assert new.prompt_version_id == earlier.prompt_version_id


def test_a_prompt_rerun_with_only_prompts_kept_scores_the_kept_prompt(tenants):
    """An evaluation edited in the same save as a re-run prompt, in an
    experiment with no agent: the re-run prompt scores it in its own run and
    the kept prompt is scored again in its eval-only run."""
    world = experiment_with_three_variants(tenants["a"], with_agent=False)
    before = output_values([world.second_output])

    started = save(world, metrics=[edited_entry(world)], change_prompt=True)

    native = started["native_runs"]
    assert started["rerun_prompt_config_ids"] == [str(world.first_config.id)]
    assert started["rerun_agent_config_ids"] == []
    assert started["rerun_eval_template_ids"] == [str(world.metric.id)]
    assert native[str(world.first_config.id)]["execution_mode"] == "prompt"
    assert native[str(world.second_config.id)]["execution_mode"] == "eval_only"
    rerun_output = native[str(world.first_config.id)]["output_column_id"]
    with sweeper() as sweep, scoring() as scores:
        run = run_experiment_workflow(world, started, sweep)
        background_tick(sweep)

    report = (run.names, scores.describe())
    # The kept prompt keeps its outputs.
    assert output_values([world.second_output]) == before, report
    # The edited evaluation is scored once per row on both runs.
    assert_scored_once_on(
        world,
        world.metric,
        [rerun_output, world.second_output],
        scores,
    )
    assert str(world.metric.id) not in dispatched_ids(sweep), report


def test_an_evaluation_edited_with_a_new_run_scores_every_kept_run(tenants):
    """An evaluation edited in the same save as a new prompt run, with no run
    re-run: the new run scores it in its own run, and every kept run is
    scored again, the prompts in their eval-only runs and the agent in an
    eval-only batch."""
    world = experiment_with_three_variants(tenants["a"])
    kept_outputs = [world.first_output, world.second_output, world.agent_output]
    before = output_values(kept_outputs)
    new_run = versioned_prompt_entry(
        world.actor, world.input_column, "third prompt", "Third variant"
    )

    started = save(world, metrics=[edited_entry(world)], new_runs=[new_run])

    native = started["native_runs"]
    kept_ids = {str(world.first_config.id), str(world.second_config.id)}
    assert len(started["rerun_prompt_config_ids"]) == 1, started
    [new_id] = started["rerun_prompt_config_ids"]
    assert new_id not in kept_ids
    assert started["rerun_agent_config_ids"] == []
    assert started["rerun_eval_template_ids"] == [str(world.metric.id)]
    assert native[new_id]["execution_mode"] == "prompt"
    modes = {native[config_id]["execution_mode"] for config_id in kept_ids}
    assert modes == {"eval_only"}
    assert str(world.agent_config.id) not in native
    new_output = native[new_id]["output_column_id"]
    with sweeper() as sweep, scoring() as scores:
        run = run_experiment_workflow(world, started, sweep)
        background_tick(sweep)

    report = (run.names, scores.describe())
    # Every kept run keeps its outputs.
    assert output_values(kept_outputs) == before, report
    # The edited evaluation is scored once per row on every run, kept ones too.
    assert_scored_once_on(world, world.metric, [new_output, *kept_outputs], scores)
    assert "process_batch_template_eval_activity" in run.names, report
    assert str(world.metric.id) not in dispatched_ids(sweep), report


def test_an_http_experiment_edit_only_saves(tenants):
    """The Edit of an experiment with an HTTP source saves its settings and
    runs nothing: no workflow, nothing scored, every cell as it was."""
    from copy import deepcopy

    from model_hub.models.choices import CellStatus, SourceChoices, StatusType
    from model_hub.models.evals_metric import UserEvalMetric
    from model_hub.services import http_experiment_source as http
    from model_hub.tests.test_http_runner_integration import stage_http_execution
    from model_hub.views.experiment_runner import ExperimentRunner

    actor, experiment, source, plan = stage_http_execution(tenants)
    answer = {"output": "answer", "error": None, "status_code": 200, "latency_ms": 1.0}
    with patch.object(http, "execute_http_row", return_value=answer):
        for row_id in plan["row_ids"]:
            http.run_http_snapshot_row(
                str(experiment.pk), str(source.pk), row_id, plan["admission_id"]
            )
    http.finish_http_source(str(experiment.pk), str(source.pk), plan["admission_id"])
    experiment.status = StatusType.COMPLETED.value
    experiment.save(update_fields=["status"])
    template = EvalTemplate.no_workspace_objects.create(
        name="Saved evaluation " + uuid.uuid4().hex[:8],
        organization=actor["org"],
        workspace=actor["workspace"],
        criteria="Check {{output}}",
        model="fixture-model",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="Saved score",
        dataset=experiment.snapshot_dataset,
        organization=actor["org"],
        workspace=actor["workspace"],
        template=template,
        source_id=str(experiment.pk),
        status=StatusType.COMPLETED.value,
        model="fixture-model",
        config={"mapping": {"output": "output"}, "reason_column": True},
    )
    experiment.user_eval_template_ids.add(metric)
    runner = ExperimentRunner(experiment.pk)
    runner.load_experiment()
    runner.precreate_eval_columns_for_edts([str(source.pk)])
    eval_columns = Column.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        deleted=False,
    )
    assert eval_columns.exists()
    for column in eval_columns:
        for row_id in plan["row_ids"]:
            Cell.no_workspace_objects.create(
                dataset=experiment.snapshot_dataset,
                column=column,
                row_id=row_id,
                value="previous score",
                status=CellStatus.PASS.value,
                value_infos=json.dumps({"reason": "saved"}),
            )
    cells = Cell.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset
    ).order_by("pk")
    before = json.dumps(list(cells.values()), sort_keys=True, default=str)
    entry = {
        "id": str(metric.pk),
        "template_id": str(template.pk),
        "name": metric.name,
        "config": deepcopy(metric.config),
        "model": "edited-evaluator-model",
    }

    with (
        sweeper() as sweep,
        scoring() as scores,
        patch.object(http, "execute_http_row") as send,
        patch(START) as start,
    ):
        response = actor["client"].put(
            f"/model-hub/experiments/v2/{experiment.pk}/",
            {"user_eval_metrics": [entry]},
            format="json",
        )
        background_tick(sweep)

    assert response.status_code == 200, response.content
    start.assert_not_called()
    send.assert_not_called()
    assert scores.scored_metric(metric) == [], scores.describe()
    assert str(metric.pk) not in dispatched_ids(sweep)
    assert json.dumps(list(cells.values()), sort_keys=True, default=str) == before
    metric.refresh_from_db()
    assert metric.model == "edited-evaluator-model"
