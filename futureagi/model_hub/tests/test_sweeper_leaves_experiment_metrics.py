"""The evaluation sweeper leaves experiment evaluations to their run.

An experiment's evaluations are scored by the experiment's own run. The
sweeper (the execute_evaluation tick) still takes dataset evaluations
(NOT_STARTED) and optimization evaluations (OPTIMIZATION_EVALUATION).
Applying an evaluation group to an experiment starts the experiment's run for
the group's evaluations, as Add evaluation with Run does. An experiment from
before snapshots has no run of its own, and the sweeper still runs its
evaluations.

The experiment is created through the real create view and its run is
started the way the view starts it. The sweeper is the real
execute_evaluation, and what it dispatches runs through the real
process_evaluation_single_task, as its worker activity would. Rows and the
run's evaluation step are processed by the real worker activity bodies.
Each scored row is recorded with who scored it. Replaced: the evaluator engine and
the API call log boundary (both recorded), the row's provider call, the
usage preflight, the usage event emitter (recorded), the workflow start and
analytics.
"""

import uuid
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Column, Row
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.services.credit_ledger_port import PayerRoute
from model_hub.tasks import user_evaluation
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    PROVIDER,
    START,
    create,
    finish,
    native_eval_transport,
    native_fixture_evaluator,
    native_preflight,
    row,
    setup,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants
from model_hub.tests.test_pinned_version_native import usage_events
from model_hub.views import eval_runner
from tfc.middleware.workspace_context import workspace_context

pytestmark = pytest.mark.django_db(transaction=True)

PAYER = "model_hub.services.credit_ledger_port.resolve_payer_route"
# The group tests apply turing_small where Turing models are available (the
# cloud case). They are about runs and scoring, not the model rule, which
# test_eval_group_member_model.py covers.
TURING_DENIED = "tfc.ee_gates._turing_denied_off_cloud"


@contextmanager
def sweeper():
    """The real sweeper tick. What it dispatches is kept, to run as its
    worker activity would. A tick can run inside a request here, so the
    worker's connection housekeeping is skipped (it would close the
    request's open transaction)."""
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


#: Who is scoring right now, for the record each scored row carries.
_SCORER = ["test setup"]


@contextmanager
def scored_by(name):
    _SCORER.append(name)
    try:
        yield
    finally:
        _SCORER.pop()


def run_dispatched(sweep):
    """Each dispatched evaluation runs as its worker activity would."""
    with (
        scored_by("sweeper"),
        workspace_context(None),
        patch.object(user_evaluation, "evaluation_tracker"),
        patch.object(user_evaluation, "track_mixpanel_event"),
        patch.object(user_evaluation, "get_mixpanel_properties", return_value={}),
    ):
        for evaluation in list(sweep.dispatched):
            user_evaluation.process_evaluation_single_task._original_func(evaluation)


def _scored_column(runner):
    """The experiment output column a runner scores, or None (a dataset or
    base column)."""
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

    def for_metric(metric, column=None):
        return [
            entry
            for entry in scored
            if entry[2] == str(metric.id)
            and (column is None or entry[3] == str(column.id))
        ]

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
        events = stack.enter_context(usage_events())
        stack.enter_context(
            native_preflight(MagicMock(return_value=SimpleNamespace(allowed=True)))
        )
        stack.enter_context(
            patch(PROVIDER, autospec=True, return_value=("actual output", {}))
        )
        yield SimpleNamespace(
            rows=lambda metric: sorted(entry[4] for entry in for_metric(metric)),
            rows_on=lambda metric, column: sorted(
                entry[4] for entry in for_metric(metric, column)
            ),
            api_log_entries=lambda metric: sum(
                1
                for call in api_log.call_args_list
                if call.kwargs["config"].get("reference_id") == str(metric.id)
            ),
            events_for=lambda metric, column=None: sum(
                1
                for _who, metric_id, column_id in emitted
                if metric_id == str(metric.id)
                and (column is None or column_id == str(column.id))
            ),
            events=events,
            describe=describe,
        )


def completed_experiment(actor):
    """An experiment made by the create view whose run has finished."""
    response, experiment, _body, _start = create(actor)
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    # The run's rows use the customer's own provider key, so they reach the
    # (replaced) provider and the outputs exist for the evaluation step.
    with scoring(), patch(PAYER, return_value=PayerRoute.byok("openai")):
        for row_id in prepared["row_ids"]:
            row(experiment, context, prepared, row_id)
        finish(context, prepared)
    # Every row is done, so the run's own status check leaves the experiment
    # Completed; that step is not part of what these tests drive.
    from model_hub.models.experiments import ExperimentsTable

    ExperimentsTable.objects.filter(id=experiment.id).update(
        status=StatusType.COMPLETED.value
    )
    experiment.refresh_from_db()
    return experiment


def fixture_template(actor, prefix):
    template = EvalTemplate.no_workspace_objects.create(
        name=f"{prefix}-{uuid.uuid4().hex}",
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
    pin = EvalTemplateVersion.no_workspace_objects.create(
        eval_template=template,
        version_number=1,
        is_default=True,
        organization=actor["org"],
        workspace=actor["workspace"],
        config_snapshot=dict(template.config),
    )
    return template, pin


def add_evaluation(actor, experiment, *, run, start=None):
    """Add evaluation on the experiment page, with or without Run."""
    template, pin = fixture_template(actor, "sweeper-added")
    body = {
        "name": "sweeper-" + uuid.uuid4().hex[:12],
        "template_id": str(template.id),
        "pinned_version_id": str(pin.id),
        "config": {"mapping": {"output": "output"}},
        "run": run,
    }
    with patch(START, **(start or {"return_value": "admitted-workflow"})) as started:
        response = actor["client"].post(
            f"/model-hub/experiments/{experiment.id}/add-eval/", body, format="json"
        )
    assert response.status_code == 200, response.content
    metric = UserEvalMetric.no_workspace_objects.get(
        pk=response.data["result"]["eval_id"]
    )
    return metric, started


def dataset_metric(actor, status):
    """An evaluation of the actor's dataset, in ``status``."""
    template, _pin = fixture_template(actor, "sweeper-dataset")
    dataset = actor["exp"]["dataset"]
    column = Column.no_workspace_objects.get(dataset=dataset)
    return UserEvalMetric.no_workspace_objects.create(
        name="sweeper-" + uuid.uuid4().hex[:12],
        organization=actor["org"],
        workspace=actor["workspace"],
        dataset=dataset,
        template=template,
        config={"mapping": {"output": str(column.id)}, "config": {}},
        status=status,
        user=actor["user"],
        model="turing_flash",
    )


def test_an_experiment_metric_is_scored_once_when_the_sweeper_ticks_during_its_run(
    tenants,
):
    """An evaluation added with Run to an experiment the create view made:
    one tick lands while the page starts the run, another after each batch
    of the run's evaluation step, which runs through the real activity
    bodies the workflow calls."""
    actor = tenants["a"]
    experiment = completed_experiment(actor)
    with sweeper() as sweep:

        def start_with_a_tick(*args, **kwargs):
            sweep.tick()
            return "admitted-workflow"

        metric, started = add_evaluation(
            actor, experiment, run=True, start={"side_effect": start_with_a_tick}
        )
        started.assert_called_once()
        assert started.call_args.kwargs["rerun_eval_template_ids"] == [str(metric.id)]
        with scoring() as scores:
            resolved = run_evaluation_step(
                experiment, [metric], between=lambda: background_tick(sweep)
            )
            run_dispatched(sweep)

    report = scores.describe()
    expected = sorted(
        str(row_id)
        for row_id in resolved["row_ids"]
        for _column in resolved["edt_column_ids"]
    )
    assert expected, report
    assert scores.rows(metric) == expected, report
    assert scores.api_log_entries(metric) == len(expected), report
    assert scores.events_for(metric) == len(expected), report
    assert str(metric.id) not in dispatched_ids(sweep), report


@pytest.mark.parametrize(
    "status,kind",
    [
        (StatusType.NOT_STARTED.value, "single"),
        (StatusType.OPTIMIZATION_EVALUATION.value, "optimization"),
    ],
    ids=["NOT_STARTED", "OPTIMIZATION_EVALUATION"],
)
def test_the_sweeper_still_takes_dataset_and_optimization_metrics(
    tenants, status, kind
):
    metric = dataset_metric(tenants["a"], status)

    with sweeper() as sweep:
        background_tick(sweep)

    assert sweep.dispatched == [{"type": kind, "eval_id": str(metric.id)}]
    metric.refresh_from_db()
    assert metric.status == StatusType.RUNNING.value


@pytest.mark.parametrize("case", ["added-without-run", "run-did-not-start"])
def test_an_experiment_metric_whose_run_never_started_is_left_pending(tenants, case):
    actor = tenants["a"]
    experiment = completed_experiment(actor)
    if case == "added-without-run":
        metric, _started = add_evaluation(actor, experiment, run=False)
    else:
        metric, started = add_evaluation(
            actor,
            experiment,
            run=True,
            start={"side_effect": RuntimeError("workflow service unavailable")},
        )
        started.assert_called_once()
    metric.refresh_from_db()
    assert metric.status == StatusType.EXPERIMENT_EVALUATION.value

    with sweeper() as sweep:
        background_tick(sweep)
        with scoring() as scores:
            run_dispatched(sweep)

    report = scores.describe()
    assert str(metric.id) not in dispatched_ids(sweep), report
    assert scores.rows(metric) == [], report
    assert scores.api_log_entries(metric) == 0, report
    assert scores.events_for(metric) == 0, report
    metric.refresh_from_db()
    assert metric.status == StatusType.EXPERIMENT_EVALUATION.value


def run_evaluations(actor, experiment, metrics):
    """The page's Run for evaluations already on the experiment, through
    the real run-evaluations view; returns the patched run start."""
    ids = [str(metric.id) for metric in metrics]
    with patch(START, return_value="admitted-workflow") as started:
        answer = actor["client"].post(
            f"/model-hub/experiments/{experiment.id}/run-evaluations/",
            {"eval_template_ids": ids},
            format="json",
        )
    assert answer.status_code == 200, answer.content
    started.assert_called_once()
    assert sorted(started.call_args.kwargs["rerun_eval_template_ids"]) == sorted(ids)
    return started


def test_an_evaluation_added_without_run_is_scored_once_when_it_is_run(tenants):
    """Left pending by the background task, an evaluation added without Run
    is scored once, by the run the person starts."""
    actor = tenants["a"]
    experiment = completed_experiment(actor)
    metric, _started = add_evaluation(actor, experiment, run=False)

    with sweeper() as sweep:
        background_tick(sweep)
        run_evaluations(actor, experiment, [metric])
        with scoring() as scores:
            resolved = run_evaluation_step(
                experiment, [metric], between=lambda: background_tick(sweep)
            )

    report = scores.describe()
    expected = sorted(
        str(row_id)
        for row_id in resolved["row_ids"]
        for _column in resolved["edt_column_ids"]
    )
    assert expected, report
    assert scores.rows(metric) == expected, report
    assert scores.api_log_entries(metric) == len(expected), report
    assert scores.events_for(metric) == len(expected), report
    assert str(metric.id) not in dispatched_ids(sweep), report


def test_the_new_page_runs_an_evaluation_that_has_no_columns_yet(tenants):
    """The new experiment page runs evaluations through rerun-cells. An
    evaluation added without Run has no columns; on an experiment whose
    variants the native path does not take (agent variants, prompts without
    a finished native run) rerun-cells sets the columns up and starts it."""
    from model_hub.views.experiments import ExperimentRerunCellsV2View

    actor = tenants["a"]
    experiment = completed_experiment(actor)
    metric, _started = add_evaluation(actor, experiment, run=False)
    assert not Column.objects.filter(
        source_id__endswith=f"-sourceid-{metric.id}", deleted=False
    ).exists()

    with (
        patch.object(
            ExperimentRerunCellsV2View, "_rerun_native_subset", return_value=None
        ),
        patch(
            "tfc.temporal.experiments.start_rerun_cells_v2_workflow",
            return_value="rerun-workflow",
        ) as started,
    ):
        answer = actor["client"].post(
            f"/model-hub/experiments/v2/{experiment.id}/rerun-cells/",
            {"user_eval_metric_ids": [str(metric.id)]},
            format="json",
        )

    assert answer.status_code == 200, answer.content
    started.assert_called_once()
    assert started.call_args.kwargs["eval_template_ids"] == [str(metric.id)]
    assert Column.objects.filter(
        source_id__endswith=f"-sourceid-{metric.id}", deleted=False
    ).exists()
    metric.refresh_from_db()
    assert metric.status == StatusType.EXPERIMENT_EVALUATION.value


def test_run_evaluations_waits_for_a_run_that_is_going(tenants):
    """Starting a run would stop the one that is going, so Run evaluations
    refuses, and starts nothing, until that run finishes."""
    from model_hub.models.experiments import ExperimentsTable

    actor = tenants["a"]
    experiment = completed_experiment(actor)
    metric, _started = add_evaluation(actor, experiment, run=False)
    ExperimentsTable.objects.filter(id=experiment.id).update(
        status=StatusType.RUNNING.value
    )

    with patch(START, return_value="admitted-workflow") as started:
        answer = actor["client"].post(
            f"/model-hub/experiments/{experiment.id}/run-evaluations/",
            {"eval_template_ids": [str(metric.id)]},
            format="json",
        )

    assert answer.status_code == 400, answer.content
    assert "Run these evaluations when it finishes." in answer.content.decode()
    started.assert_not_called()
    metric.refresh_from_db()
    assert metric.status == StatusType.EXPERIMENT_EVALUATION.value


def test_a_dataset_evaluation_the_sweeper_runs_is_scored_once(tenants):
    """Started the way the dataset page starts it; only the sweeper runs it."""
    actor = tenants["a"]
    metric = dataset_metric(actor, StatusType.COMPLETED.value)
    dataset = metric.dataset
    response = actor["client"].post(
        f"/model-hub/develops/{dataset.id}/start_evals_process/",
        {"user_eval_ids": [str(metric.id)]},
        format="json",
    )
    assert response.status_code == 200, response.content
    metric.refresh_from_db()
    assert metric.status == StatusType.NOT_STARTED.value

    with sweeper() as sweep:
        background_tick(sweep)
        assert dispatched_ids(sweep) == [str(metric.id)]
        with scoring() as scores:
            run_dispatched(sweep)
        # Taken once: the next tick finds nothing of it to run.
        sweep.dispatched.clear()
        background_tick(sweep)

    rows = sorted(
        str(row_id)
        for row_id in Row.no_workspace_objects.filter(
            dataset=dataset, deleted=False
        ).values_list("id", flat=True)
    )
    assert scores.rows(metric) == rows
    assert scores.api_log_entries(metric) == len(rows)
    assert len(scores.events) == len(rows)
    assert sweep.dispatched == []
    metric.refresh_from_db()
    assert metric.status == StatusType.COMPLETED.value


def apply_evaluation_group(
    actor, experiment, *, size=2, start=None, with_response=False
):
    """Apply an evaluation group on the experiment page, through the real
    apply-eval-group view."""
    group = EvalGroup.objects.create(
        name="sweeper-group-" + uuid.uuid4().hex[:12],
        organization=actor["org"],
        workspace=actor["workspace"],
        created_by=actor["user"],
    )
    group.eval_templates.add(
        *(fixture_template(actor, "sweeper-group")[0] for _ in range(size))
    )
    body = {
        "eval_group_id": str(group.id),
        "page_id": "EXPERIMENT",
        "filters": {
            "experiment_id": str(experiment.id),
            "model": "turing_small",
            "error_localizer": False,
        },
        "mapping": {"output": "output"},
    }
    with patch(START, **(start or {"return_value": "admitted-workflow"})) as started:
        response = actor["client"].post(
            "/model-hub/eval-groups/apply-eval-group/", body, format="json"
        )
    assert response.status_code == 200, response.content
    metrics = list(
        UserEvalMetric.no_workspace_objects.filter(
            eval_group=group, source_id=str(experiment.id), deleted=False
        )
    )
    assert len(metrics) == size
    if with_response:
        return metrics, started, response
    return metrics, started


def run_evaluation_step(experiment, metrics, between=None):
    """The run's evaluation step for evaluations added to an experiment whose
    outputs exist: the workflow resolves the output columns and rows, then
    scores each evaluation on each output column in batches (the real
    activity bodies)."""
    from tfc.temporal.experiments.activities import (
        _process_batch_template_eval_sync,
        _resolve_edt_columns_sync,
    )

    with scored_by("experiment run"), workspace_context(None):
        resolved = _resolve_edt_columns_sync(str(experiment.id))
        assert resolved["edt_column_ids"] and resolved["row_ids"], resolved
        for column_id in resolved["edt_column_ids"]:
            for metric in metrics:
                result = _process_batch_template_eval_sync(
                    resolved["row_ids"],
                    column_id,
                    str(experiment.snapshot_dataset_id),
                    str(experiment.id),
                    str(metric.id),
                    rows_per_batch=10,
                )
                assert result["status"] == "COMPLETED", result
                if between is not None:
                    between()
    return resolved


def test_applying_an_evaluation_group_starts_its_run_and_scores_each_once(tenants):
    actor = tenants["a"]
    experiment = completed_experiment(actor)

    with patch(TURING_DENIED, return_value=False):
        metrics, started, response = apply_evaluation_group(
            actor, experiment, with_response=True
        )

    started.assert_called_once()
    assert response.json()["result"] == {"run": "started"}
    assert started.call_args.kwargs["experiment_id"] == str(experiment.id)
    assert sorted(started.call_args.kwargs["rerun_eval_template_ids"]) == sorted(
        str(metric.id) for metric in metrics
    )
    assert all(
        metric.dataset_id == experiment.snapshot_dataset_id for metric in metrics
    )
    with sweeper() as sweep:
        with scoring() as scores:
            resolved = run_evaluation_step(
                experiment, metrics, between=lambda: background_tick(sweep)
            )

    report = scores.describe()
    rows = sorted(str(row_id) for row_id in resolved["row_ids"])
    assert rows, report
    per_metric = len(rows) * len(resolved["edt_column_ids"])
    for metric in metrics:
        assert scores.rows(metric) == sorted(rows * len(resolved["edt_column_ids"])), (
            report
        )
        assert scores.api_log_entries(metric) == per_metric, report
        assert scores.events_for(metric) == per_metric, report
    assert len(scores.events) == per_metric * len(metrics), report
    assert sweep.dispatched == [], report


def test_applying_a_group_while_a_run_is_going_leaves_that_run_alone(tenants):
    """A run of the experiment is going: applying a group starts nothing and
    stops nothing. The run still scores its own rows, the group's
    evaluations wait with no columns, and Run evaluations later sets them up
    and scores each once."""
    actor = tenants["a"]
    experiment = completed_experiment(actor)
    live, live_started = add_evaluation(actor, experiment, run=True)
    live_started.assert_called_once()
    experiment.refresh_from_db()
    assert experiment.status == StatusType.RUNNING.value

    with patch(TURING_DENIED, return_value=False):
        metrics, started, response = apply_evaluation_group(
            actor, experiment, with_response=True
        )

    started.assert_not_called()
    assert response.json()["result"] == {"run": "run_going"}
    experiment.refresh_from_db()
    assert experiment.status == StatusType.RUNNING.value
    for metric in metrics:
        metric.refresh_from_db()
        assert metric.status == StatusType.EXPERIMENT_EVALUATION.value
        assert not Column.objects.filter(
            source_id__endswith=f"-sourceid-{metric.id}", deleted=False
        ).exists()

    # The run that was going goes on and scores its own rows, once each.
    with sweeper() as sweep:
        with scoring() as scores:
            resolved = run_evaluation_step(
                experiment, [live], between=lambda: background_tick(sweep)
            )
    report = scores.describe()
    rows = sorted(str(row_id) for row_id in resolved["row_ids"])
    assert rows, report
    assert scores.rows(live) == sorted(rows * len(resolved["edt_column_ids"])), report
    for metric in metrics:
        assert scores.rows(metric) == [], report
        assert str(metric.id) not in dispatched_ids(sweep), report

    # Once that run has finished, Run evaluations sets the group's
    # evaluations up and its run scores each row once.
    from model_hub.models.experiments import ExperimentsTable

    ExperimentsTable.objects.filter(id=experiment.id).update(
        status=StatusType.COMPLETED.value
    )
    run_evaluations(actor, experiment, metrics)
    with sweeper() as sweep:
        with scoring() as scores:
            resolved = run_evaluation_step(
                experiment, metrics, between=lambda: background_tick(sweep)
            )
    report = scores.describe()
    per_metric = sorted(
        str(row_id)
        for row_id in resolved["row_ids"]
        for _column in resolved["edt_column_ids"]
    )
    assert per_metric, report
    for metric in metrics:
        assert scores.rows(metric) == per_metric, report
        assert scores.api_log_entries(metric) == len(per_metric), report
        assert scores.events_for(metric) == len(per_metric), report
    assert sweep.dispatched == [], report


def test_an_evaluation_group_whose_run_did_not_start_is_left_pending(tenants):
    actor = tenants["a"]
    experiment = completed_experiment(actor)

    with patch(TURING_DENIED, return_value=False):
        metrics, started, response = apply_evaluation_group(
            actor,
            experiment,
            start={"side_effect": RuntimeError("workflow service unavailable")},
            with_response=True,
        )

    started.assert_called_once()
    assert response.json()["result"] == {"run": "not_started"}
    with sweeper() as sweep:
        background_tick(sweep)
        with scoring() as scores:
            run_dispatched(sweep)

    assert sweep.dispatched == []
    for metric in metrics:
        assert scores.rows(metric) == []
        assert scores.api_log_entries(metric) == 0
        metric.refresh_from_db()
        assert metric.status == StatusType.EXPERIMENT_EVALUATION.value
    # No run started, so the experiment does not read as running.
    experiment.refresh_from_db()
    assert experiment.status != StatusType.RUNNING.value


def legacy_experiment(actor):
    """An experiment from before experiments had a snapshot: its output
    column and rows are on its dataset, and it has no run of its own."""
    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable

    dataset = actor["exp"]["dataset"]
    experiment = ExperimentsTable.objects.create(
        name="legacy-" + uuid.uuid4().hex[:8],
        dataset=dataset,
        user=actor["user"],
        status=StatusType.COMPLETED.value,
    )
    variant = ExperimentDatasetTable.objects.create(
        name="A", experiment=experiment, status=StatusType.COMPLETED.value
    )
    output = Column.objects.create(
        dataset=dataset,
        name="output-A-" + uuid.uuid4().hex[:8],
        data_type="text",
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id),
    )
    variant.columns.add(output)
    for dataset_row in Row.objects.filter(dataset=dataset, deleted=False):
        Cell.objects.create(
            dataset=dataset, column=output, row=dataset_row, value="actual output"
        )
    assert experiment.snapshot_dataset_id is None
    return experiment, output


def test_a_legacy_experiment_group_evaluation_is_scored_once_by_the_sweeper(
    tenants,
):
    """An experiment without a snapshot has no run of its own: the sweeper
    takes its evaluation, once, and scores each row of its output once."""
    actor = tenants["a"]
    experiment, output = legacy_experiment(actor)
    with patch(TURING_DENIED, return_value=False):
        (metric,), started = apply_evaluation_group(actor, experiment, size=1)
    started.assert_not_called()
    assert metric.dataset_id == experiment.dataset_id
    assert metric.status == StatusType.EXPERIMENT_EVALUATION.value

    with sweeper() as sweep:
        background_tick(sweep)
        assert sweep.dispatched == [{"type": "experiment", "eval_id": str(metric.id)}]
        with scoring() as scores:
            run_dispatched(sweep)
        # Taken once: the next tick finds nothing of it to run.
        sweep.dispatched.clear()
        background_tick(sweep)

    rows = sorted(
        str(row_id)
        for row_id in Row.no_workspace_objects.filter(
            dataset=experiment.dataset, deleted=False
        ).values_list("id", flat=True)
    )
    report = scores.describe()
    assert rows, report
    assert scores.rows_on(metric, output) == rows, report
    assert scores.events_for(metric, output) == len(rows), report
    assert sweep.dispatched == [], report
    metric.refresh_from_db()
    assert metric.status == StatusType.COMPLETED.value, report


def test_an_evaluation_of_an_experiment_with_a_snapshot_is_not_taken(tenants):
    """Only an experiment without a snapshot is left to the sweeper."""
    actor = tenants["a"]
    experiment = completed_experiment(actor)
    metric, _started = add_evaluation(actor, experiment, run=False)

    with sweeper() as sweep:
        background_tick(sweep)

    assert str(metric.id) not in dispatched_ids(sweep)
