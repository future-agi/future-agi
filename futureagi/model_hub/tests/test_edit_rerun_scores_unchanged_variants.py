"""Edit and rerun scores an edited evaluation again on every variant.

Saving an evaluation resets its cells on every variant. When the same save
also re-runs a prompt or agent, only the re-run variants score the evaluation
in their own runs; the other variants get the eval-only batches an
evaluation-only save uses (process_batch_template_eval_activity per output
column, rows_per_batch=10), once per row.

The experiment is made by the real create view with two prompt variants and
one agent variant, and saved through the real update view. Its runs are driven
as the workflow drives them: the native prompt rows and the eval-only batches
through the workflow's own activity bodies, planned by the workflow's own
helpers. The background evaluation task (the sweeper) ticks during the run.
A Stop goes through the real Stop view. Replaced: the workflow start and the
workflow service's cancel, the row's provider call, the evaluator engine and
the API call log boundary (both recorded), the usage event emitter (recorded),
the usage preflight and the provider-key route.
"""

import uuid
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.develop_dataset import Cell, Column, Row
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
from model_hub.tests.test_experiment_stop_default_path import CONFIRMED, stop
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
def scoring():
    """Each row an evaluation scores (each requests one API call log entry),
    the API call log entries requested and the usage events emitted, with
    who scored it and on which experiment output column."""
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
        _engine, api_log = stack.enter_context(native_eval_transport(score=1))
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


def experiment_with_three_variants(actor):
    """Two prompt variants and one agent variant, one evaluation, made by the
    create view; the prompts' first run finished. The agent's first run is
    stopped and its outputs are written as the agent wrote them (no graph
    runner runs here); then the evaluation is run on the agent's output, as
    Run on the evaluation runs it there, so every row has a real score."""
    dataset = actor["exp"]["dataset"]
    column = Column.no_workspace_objects.get(dataset=dataset)
    for order in (1, 2):
        extra = Row.no_workspace_objects.create(dataset=dataset, order=order)
        Cell.no_workspace_objects.create(
            dataset=dataset, column=column, row=extra, value=f"input-{order}"
        )
    body = create_body(actor)
    template, version = fixtures.prompt(actor, "second prompt")
    version.prompt_config_snapshot["messages"] = [
        {"role": "user", "content": "{{" + str(column.id) + "}}"}
    ]
    version.save(update_fields=["prompt_config_snapshot"])
    graph, graph_version, _node = graph_fixture.make_graph(actor)
    first = {**body["prompt_config"][0], "name": "First variant"}
    second = {**fixtures.entry(template, version), "name": "Second variant"}
    agent = {"agent_id": str(graph.id), "agent_version": str(graph_version.id)}
    body["prompt_config"] = [first, second, agent]
    response, experiment, body, _start = create(actor, body)
    assert response.status_code == 200, response.content

    contexts = state_of(experiment)["workflow_args"]["native_runs"]
    with scoring():
        for context in contexts.values():
            if context.get("execution_mode") != "graph":
                run_native(experiment, context)
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
    agent_output = Column.no_workspace_objects.get(pk=graph_context["output_column_id"])
    snapshot_rows = list(
        Row.no_workspace_objects.filter(
            dataset=experiment.snapshot_dataset, deleted=False
        ).order_by("order")
    )
    for snapshot_row in snapshot_rows:
        Cell.no_workspace_objects.update_or_create(
            dataset=experiment.snapshot_dataset,
            column=agent_output,
            row=snapshot_row,
            defaults={"value": "agent output", "status": "pass", "deleted": False},
        )
    rows = [str(snapshot_row.id) for snapshot_row in snapshot_rows]
    metric = experiment.user_eval_template_ids.get()
    # The create view made the evaluation's column on the agent's output with
    # no cells, and nothing above scores it: without scores there the save
    # would have nothing to reset. Run on the evaluation (rerun-cells with the
    # evaluation) scores the agent's output with this batch activity, every
    # row, ten rows a batch (RerunCellsV2Workflow's eval-only step).
    with scoring(), workspace_context(None):
        scored = activities._process_batch_template_eval_sync(
            rows,
            str(agent_output.id),
            str(experiment.snapshot_dataset_id),
            str(experiment.id),
            str(metric.id),
            rows_per_batch=10,
        )
    assert scored["status"] == "COMPLETED", scored
    agent_scores = Cell.no_workspace_objects.filter(
        column=evaluation_column(
            SimpleNamespace(experiment=experiment, metric=metric), agent_output.id
        ),
        deleted=False,
    )
    assert sorted(str(cell.row_id) for cell in agent_scores) == sorted(rows)
    assert all(
        cell.value not in ("", None) and cell.status != CellStatus.RUNNING.value
        for cell in agent_scores
    )
    experiment.refresh_from_db()
    configs = {
        config.prompt_template_id: config
        for config in ExperimentPromptConfig.no_workspace_objects.filter(
            experiment_dataset__experiment=experiment, deleted=False
        )
    }
    return SimpleNamespace(
        actor=actor,
        experiment=experiment,
        metric=metric,
        metric_entry=body["user_eval_metrics"][0],
        first=first,
        second=second,
        agent=agent,
        first_config=configs[uuid.UUID(first["prompt_id"])],
        second_config=configs[uuid.UUID(second["prompt_id"])],
        agent_config=ExperimentAgentConfig.no_workspace_objects.get(
            experiment_dataset__experiment=experiment
        ),
        agent_output=agent_output,
        rows=rows,
        url=f"/model-hub/experiments/v2/{experiment.id}/",
    )


def save(world, *, change_prompt):
    """Edit and rerun: the evaluation is edited, and with change_prompt the
    first prompt variant's model changes in the same save. The second prompt
    and the agent are sent unchanged."""
    first = {**world.first, "id": str(world.first_config.id)}
    if change_prompt:
        first["model"] = "gpt-4o"
    body = {
        "prompt_config": [
            first,
            {**world.second, "id": str(world.second_config.id)},
            {**world.agent, "id": str(world.agent_config.id)},
        ],
        "user_eval_metrics": [
            {
                **world.metric_entry,
                "id": str(world.metric.id),
                "config": {
                    "mapping": {"output": "output"},
                    "config": {"rubric": "stricter"},
                },
            }
        ],
    }
    with patch(START, return_value="updated-workflow") as started:
        response = world.actor["client"].put(world.url, body, format="json")
    assert response.status_code == 200, response.content
    started.assert_called_once()
    return started.call_args.kwargs


def evaluation_column(world, output_column_id):
    """The edited evaluation's result column on one variant's output."""
    output = Column.no_workspace_objects.get(pk=output_column_id)
    return Column.no_workspace_objects.get(
        dataset=world.experiment.snapshot_dataset,
        source_id=f"{output.source_id}-{output.id}-sourceid-{world.metric.id}",
        deleted=False,
    )


def drive(world, started, batches, sweep, scores):
    """The run as the workflow drives it: each native child row by row with a
    tick between rows, then each eval-only batch with a tick before each
    batch."""

    def between():
        background_tick(sweep)

    with scored_by("experiment run"):
        for context in started["native_runs"].values():
            run_native(world.experiment, context, between=between)
        with ticking_between_batches(sweep), workspace_context(None):
            for column_id, metric_id in batches:
                result = activities._process_batch_template_eval_sync(
                    world.rows,
                    column_id,
                    str(world.experiment.snapshot_dataset_id),
                    str(world.experiment.id),
                    metric_id,
                    rows_per_batch=10,
                )
                report = (result, scores.describe())
                assert result["status"] == "COMPLETED", report


def assert_scored_once_everywhere(world, outputs, scores, sweep):
    report = scores.describe()
    for output in outputs:
        assert scores.rows_on(world.metric, output) == sorted(world.rows), report
        assert scores.events_on(world.metric, output) == len(world.rows), report
        cells = Cell.no_workspace_objects.filter(
            column=evaluation_column(world, output), deleted=False
        )
        assert sorted(str(cell.row_id) for cell in cells) == sorted(world.rows), report
        assert all(
            cell.value not in ("", None) and cell.status != "running" for cell in cells
        ), report
    assert scores.api_log_entries(world.metric) == len(world.rows) * len(outputs), (
        report
    )
    assert str(world.metric.id) not in [
        evaluation["eval_id"] for evaluation in sweep.dispatched
    ], report


def test_a_mixed_save_scores_the_edited_evaluation_once_on_every_variant(tenants):
    from tfc.temporal.experiments.workflows import (
        eval_only_batches,
        rerun_config_ids_of_mixed_save,
    )

    world = experiment_with_three_variants(tenants["a"])
    started = save(world, change_prompt=True)

    rerun_prompts = started["rerun_prompt_config_ids"]
    rerun_agents = started["rerun_agent_config_ids"]
    rerun_evals = started["rerun_eval_template_ids"]
    assert rerun_prompts == [str(world.first_config.id)]
    assert rerun_agents == []
    assert rerun_evals == [str(world.metric.id)]
    native = started["native_runs"]
    # The changed prompt re-runs; the unchanged saved-version prompt gets an
    # eval-only native run; the unchanged agent gets no run of its own.
    assert native[str(world.first_config.id)]["execution_mode"] == "prompt"
    assert native[str(world.second_config.id)]["execution_mode"] == "eval_only"
    assert str(world.agent_config.id) not in native
    # The save reset the edited evaluation's scores on the agent's output too:
    # every row's earlier score is cleared and waits to be scored again.
    agent_cells = Cell.no_workspace_objects.filter(
        column=evaluation_column(world, world.agent_output.id), deleted=False
    )
    assert sorted(str(cell.row_id) for cell in agent_cells) == sorted(world.rows)
    assert {(cell.value, cell.status) for cell in agent_cells} == {
        ("", CellStatus.RUNNING.value)
    }

    # Planning as the workflow plans it (Step 2.5 to Step 3.5).
    excluded = rerun_config_ids_of_mixed_save(
        is_selective=True,
        configs_being_rerun=True,
        rerun_eval_template_ids=rerun_evals,
        prompt_config_ids=rerun_prompts,
        agent_config_ids=rerun_agents,
    )
    with workspace_context(None):
        resolved = activities._resolve_edt_columns_sync(
            str(world.experiment.id), exclude_config_ids=excluded
        )
    native_outputs = {context["output_column_id"] for context in native.values()}
    batches = eval_only_batches(
        is_selective=True,
        rerun_eval_template_ids=rerun_evals,
        edt_column_ids=[
            column
            for column in resolved["edt_column_ids"]
            if column not in native_outputs
        ],
    )
    assert batches == [(str(world.agent_output.id), str(world.metric.id))]

    with sweeper() as sweep, scoring() as scores:
        drive(world, started, batches, sweep, scores)

    outputs = [
        native[str(world.first_config.id)]["output_column_id"],
        native[str(world.second_config.id)]["output_column_id"],
        str(world.agent_output.id),
    ]
    assert_scored_once_everywhere(world, outputs, scores, sweep)


def test_an_evaluation_only_save_scores_every_variant_once(tenants):
    world = experiment_with_three_variants(tenants["a"])
    started = save(world, change_prompt=False)

    assert started["rerun_prompt_config_ids"] == []
    assert started["rerun_agent_config_ids"] == []
    native = started["native_runs"]
    assert {context["execution_mode"] for context in native.values()} == {"eval_only"}
    # Step 2.5 and Step 3.5 as they are for an evaluation-only save.
    with workspace_context(None):
        resolved = activities._resolve_edt_columns_sync(str(world.experiment.id))
    native_outputs = {context["output_column_id"] for context in native.values()}
    batches = [
        (column, metric_id)
        for column in resolved["edt_column_ids"]
        if column not in native_outputs
        for metric_id in started["rerun_eval_template_ids"]
    ]
    assert batches == [(str(world.agent_output.id), str(world.metric.id))]

    with sweeper() as sweep, scoring() as scores:
        drive(world, started, batches, sweep, scores)

    outputs = [context["output_column_id"] for context in native.values()]
    assert_scored_once_everywhere(
        world, [*outputs, str(world.agent_output.id)], scores, sweep
    )


def test_a_stop_between_batches_starts_no_further_batch(tenants):
    """One row per batch here, so a Stop after the first batch leaves the
    later batches unsent; the workflow's batches hold ten rows each.

    The Stop is the page's: the Stop view (run control off) writes Cancelled
    over the Queued experiment and cancels its workflows, then the cancelled
    workflow runs its own cleanup. That cleanup alone is not a Stop: it never
    writes Cancelled over a Queued or Running experiment, which may be a run
    asked for after the Stop."""
    from tfc.temporal.experiments.activities import _stop_experiment_cleanup_sync

    world = experiment_with_three_variants(tenants["a"])
    save(world, change_prompt=True)
    world.experiment.refresh_from_db()
    assert world.experiment.status == StatusType.QUEUED.value
    real_run = eval_runner.EvaluationRunner.run_prompt
    sent = []

    def run_then_stop(self, row_ids=None):
        result = real_run(self, row_ids=row_ids)
        sent.append(list(row_ids or []))
        if len(sent) == 1:
            stop(world.actor, world.experiment, CONFIRMED)
            _stop_experiment_cleanup_sync(
                str(world.experiment.id),
                "Experiment stopped",
                manage_connections=False,
            )
        return result

    with (
        scoring() as scores,
        scored_by("experiment run"),
        patch.object(eval_runner.EvaluationRunner, "run_prompt", run_then_stop),
        workspace_context(None),
    ):
        result = activities._process_batch_template_eval_sync(
            world.rows,
            str(world.agent_output.id),
            str(world.experiment.snapshot_dataset_id),
            str(world.experiment.id),
            str(world.metric.id),
            rows_per_batch=1,
        )

    report = scores.describe()
    assert result["status"] == "CANCELLED", (result, report)
    assert sent == [world.rows[:1]], report
    stopped_rows = scores.rows_on(world.metric, world.agent_output.id)
    assert stopped_rows == world.rows[:1], report
    assert scores.api_log_entries(world.metric) == 1, report
    world.experiment.refresh_from_db()
    assert world.experiment.status == StatusType.CANCELLED.value


def test_planning_adds_no_batch_to_a_prompt_only_save_and_keeps_others():
    from tfc.temporal.experiments.workflows import (
        eval_only_batches,
        rerun_config_ids_of_mixed_save,
    )

    columns = ["output-a", "output-b"]
    # A prompt-only save: its child runs score every evaluation.
    prompt_only = rerun_config_ids_of_mixed_save(
        is_selective=True,
        configs_being_rerun=True,
        rerun_eval_template_ids=[],
        prompt_config_ids=["prompt-a"],
        agent_config_ids=[],
    )
    prompt_only_batches = eval_only_batches(
        is_selective=True, rerun_eval_template_ids=[], edt_column_ids=columns
    )
    assert prompt_only is None
    assert prompt_only_batches == []

    # An evaluation-only save is not a mixed save; Step 3.5 covers it.
    eval_only = rerun_config_ids_of_mixed_save(
        is_selective=True,
        configs_being_rerun=False,
        rerun_eval_template_ids=["eval-1"],
        prompt_config_ids=[],
        agent_config_ids=[],
    )
    eval_only_save_batches = eval_only_batches(
        is_selective=True, rerun_eval_template_ids=["eval-1"], edt_column_ids=columns
    )
    assert eval_only is None
    assert eval_only_save_batches == [("output-a", "eval-1"), ("output-b", "eval-1")]

    # A full run (nothing selective) plans no eval-only batch.
    full_run_batches = eval_only_batches(
        is_selective=False, rerun_eval_template_ids=["eval-1"], edt_column_ids=columns
    )
    assert full_run_batches == []

    # A mixed save leaves out every re-run prompt and agent, once each.
    mixed = rerun_config_ids_of_mixed_save(
        is_selective=True,
        configs_being_rerun=True,
        rerun_eval_template_ids=["eval-1"],
        prompt_config_ids=["prompt-b", "prompt-a"],
        agent_config_ids=["agent-a", "prompt-a"],
    )
    assert mixed == ["agent-a", "prompt-a", "prompt-b"]


@pytest.mark.parametrize("patched", [True, False], ids=["new-run", "run-in-flight"])
def test_the_mixed_save_step_resolves_with_its_own_activity(patched):
    """The workflow body itself, with its activities and children answered
    here: a new run resolves the unchanged variants with the new activity
    name and schedules one batch per unchanged output; a run already in
    flight (no patch marker) replays as before, with neither."""
    import asyncio

    from tfc.temporal.experiments import workflows
    from tfc.temporal.experiments.types import (
        ResolveEdtColumnsExcludingInput,
        RunExperimentInput,
    )

    calls, children = [], []

    async def execute(name, value, **kwargs):
        calls.append((name, value))
        if name in {"setup_experiment_activity", "mark_experiment_running_activity"}:
            return {
                "status": "READY",
                "prompt_configs": ["changed", "kept"],
                "agent_configs": ["agent"],
                "eval_template_ids": ["metric"],
                "dataset_id": "snapshot",
                "has_base_column": False,
            }
        if name == "analyze_dependencies_activity":
            return {"dependent_eval_ids": ["metric"], "independent_eval_ids": []}
        if name == workflows.RESOLVE_UNCHANGED_VARIANTS_ACTIVITY:
            return {
                "edt_column_ids": ["kept-output", "agent-output"],
                "row_ids": ["row"],
            }
        return {"status": "COMPLETED"}

    async def child(_run, value, **kwargs):
        children.append(value)

        async def complete():
            return {"status": "COMPLETED", "completed_rows": 1, "failed_rows": 0}

        return asyncio.create_task(complete())

    def is_patched(patch_id):
        return patched and patch_id == "edit-rerun-scores-unchanged-variants-v1"

    value = RunExperimentInput(
        experiment_id="experiment",
        rerun_prompt_config_ids=["changed"],
        rerun_eval_template_ids=["metric"],
    )
    with (
        patch.object(workflows.workflow, "execute_activity", side_effect=execute),
        patch.object(workflows.workflow, "start_child_workflow", side_effect=child),
        patch.object(workflows.workflow, "patched", side_effect=is_patched),
    ):
        result = asyncio.run(workflows.RunExperimentV2Workflow().run(value))

    assert result.status == "COMPLETED", calls
    resolves = [(name, arg) for name, arg in calls if name.startswith("resolve_edt")]
    batches = [
        (arg.column_id, arg.eval_template_id, arg.row_ids, arg.rows_per_batch)
        for name, arg in calls
        if name == "process_batch_template_eval_activity"
    ]
    if patched:
        assert resolves == [
            (
                "resolve_edt_columns_excluding_activity",
                ResolveEdtColumnsExcludingInput(
                    experiment_id="experiment", exclude_config_ids=["changed"]
                ),
            )
        ]
        assert batches == [
            ("kept-output", "metric", ["row"], 10),
            ("agent-output", "metric", ["row"], 10),
        ]
    else:
        assert resolves == []
        assert batches == []
    assert [c.experiment_prompt_config_id for c in children] == ["changed"]


def test_the_new_activity_is_registered_and_the_existing_one_is_unchanged():
    import asyncio
    from dataclasses import fields

    from tfc.temporal.experiments import get_activities, workflows
    from tfc.temporal.experiments.types import (
        ResolveEdtColumnsExcludingInput,
        ResolveEdtColumnsInput,
    )

    # The worker registers both names; the new one under its own name.
    registered = {
        getattr(function, "__temporal_activity_definition").name
        for function in get_activities()
    }
    assert {
        "resolve_edt_columns_activity",
        "resolve_edt_columns_excluding_activity",
    } <= registered
    name, call_input = workflows.unchanged_variants_columns_call("exp", ["cfg"])
    assert name == "resolve_edt_columns_excluding_activity"
    assert call_input == ResolveEdtColumnsExcludingInput(
        experiment_id="exp", exclude_config_ids=["cfg"]
    )

    # The existing activity keeps its input and passes only its EDT filter.
    assert [field.name for field in fields(ResolveEdtColumnsInput)] == [
        "experiment_id",
        "edt_ids",
    ]
    answer = {"edt_column_ids": ["c"], "row_ids": ["r"], "dataset_id": "d"}
    with patch.object(
        activities, "_resolve_edt_columns_sync", return_value=answer
    ) as resolve:
        asyncio.run(
            activities.resolve_edt_columns_activity(
                ResolveEdtColumnsInput(experiment_id="exp", edt_ids=["edt"])
            )
        )
        asyncio.run(
            activities.resolve_edt_columns_excluding_activity(
                ResolveEdtColumnsExcludingInput(
                    experiment_id="exp", exclude_config_ids=["cfg"]
                )
            )
        )
    assert [call.args for call in resolve.call_args_list] == [("exp",), ("exp",)]
    assert [call.kwargs for call in resolve.call_args_list] == [
        {"edt_ids": ["edt"]},
        {"exclude_config_ids": ["cfg"]},
    ]
