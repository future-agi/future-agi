"""The Stop's claims on their real paths.

1. A native prompt row whose provider call is in flight at the Stop: the
   Stop goes through the real Stop view while the call is in flight, and the
   row is kept and counted, with the spend the provider reported for that one
   call (its usage and price) recorded on it once; the next row is never sent.
   a. Through the prompt workflow's own activities (setup_prompt_v2_activity,
      then process_row_activity) under Temporal's ActivityEnvironment: this
      runs everywhere.
   b. Through the prompt workflow (ProcessPromptV2Workflow) under Temporal's
      time-skipping test server, wherever one exists (TEMPORAL_TEST_SERVER,
      as simulate/tests uses it). Skipped where none can start: the test
      machines cannot download one.
2. An evaluation batch run through its activity (the experiment's per-row
   evaluation step, process_batch_template_eval_activity) with the Stop
   landing while rows wait in the runner's pool of five: the rows already
   sent are kept, charged once each, their usage recorded once each, and no
   charge is refunded; the row still queued is never charged and says the
   experiment stopped.

Only the provider call, the payer lookup (the tenant's own key), the
workflow service the Stop asks, the evaluator engine, and the charge and
refund calls (counted, as the evaluator bridge tests count them) are
stand-ins. The evaluator's own result handling, charge, status and usage
paths run for real.
"""

import asyncio
import os
import threading
import time
from unittest.mock import patch
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio.client import WorkflowFailureError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from model_hub.models.choices import (
    CellStatus,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentRun, ExperimentsTable
from model_hub.services.credit_ledger_port import PayerRoute
from model_hub.services.experiment_submit import state_of
from model_hub.services.native_run_projection import project_run
from model_hub.tests.test_experiment_run_control import call, seed
from model_hub.tests.test_experiment_stop_default_path import running, stop
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    PROVIDER,
    create,
    native_eval_transport,
    native_fixture_evaluator,
    native_preflight,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants
from model_hub.views.experiments import ExperimentStopV2View

pytestmark = pytest.mark.django_db(transaction=True)

CANCEL = "tfc.temporal.experiments.cancel_all_experiment_workflows"
PAYER = "model_hub.services.credit_ledger_port.resolve_payer_route"
STOPPED = "Experiment stopped"
# What the provider reports for the call in flight: the row's spend record.
USAGE = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
REPORTED = {
    "metadata": {
        "usage": USAGE,
        "cost": {"total_cost": 0.125, "pricing_source": "available_models"},
    }
}


@pytest.fixture(autouse=True)
def run_control_off(settings):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = False


def test_server_options():
    """CI's verified test server binary; the SDK default elsewhere."""
    path = os.environ.get("TEMPORAL_TEST_SERVER")
    return {"test_server_existing_path": path} if path else {}


# 1. A prompt row in flight, through the prompt workflow ----------------------------


def prompt_world(tenants):
    """A native prompt experiment of two rows, admitted and running."""
    from model_hub.models.experiments import ExperimentPromptConfig

    actor = tenants["a"]
    source = actor["exp"]["dataset"]
    source_column = Column.no_workspace_objects.get(dataset=source, deleted=False)
    extra = Row.no_workspace_objects.create(dataset=source, order=1)
    Cell.no_workspace_objects.create(
        dataset=source, column=source_column, row=extra, value="row 1"
    )
    response, experiment, _, _ = create(actor)
    assert response.status_code == 200, response.content
    running(experiment)
    config = ExperimentPromptConfig.no_workspace_objects.get(
        experiment_dataset__experiment=experiment
    )
    context = state_of(experiment)["workflow_args"]["native_runs"][str(config.id)]
    return actor, experiment, config, context


def assert_kept_counted_and_charged_once(experiment, context, answers, provider):
    # The Stop answered while the row was in flight; the next row was never
    # sent: one provider call in all.
    assert answers and answers[0]["rows_in_flight"] == 1
    assert provider.call_count == 1
    run = ExperimentRun._base_manager.get(pk=context["run_id"])
    assert run.status == "cancelled" and run.completed_at is not None
    items = list(run.items.filter(deleted=False))
    assert [(item.status, item.output) for item in items] == [
        ("completed", "late output")
    ]
    # Its spend is recorded once: the provider's usage and price for the one
    # call that was sent.
    assert items[0].token_usage == USAGE
    assert items[0].cost == 0.125
    assert (
        Cell.no_workspace_objects.filter(
            dataset=experiment.snapshot_dataset,
            value="late output",
            status=CellStatus.PASS.value,
        ).count()
        == 1
    )
    assert project_run(run, dataset=experiment.dataset)["counts"]["completed"] == 1


def test_a_prompt_row_in_flight_at_the_stop_through_the_row_activity(tenants):
    from tfc.temporal.experiments import activities
    from tfc.temporal.experiments.types import ProcessRowInput, SetupPromptV2Input

    actor, experiment, config, context = prompt_world(tenants)
    dataset_id = str(experiment.snapshot_dataset_id)
    answers = []

    def provider_call(*args, **kwargs):
        # Inside the row activity: Stop is pressed while this row's call is
        # in flight.
        answers.append(stop(actor, experiment))
        return ("late output", REPORTED)

    env = ActivityEnvironment()
    with (
        patch(PAYER, return_value=PayerRoute.byok("openai")),
        patch(PROVIDER, autospec=True, side_effect=provider_call) as provider,
    ):
        setup = asyncio.run(
            env.run(
                activities.setup_prompt_v2_activity,
                SetupPromptV2Input(
                    experiment_id=str(experiment.id),
                    dataset_id=dataset_id,
                    experiment_prompt_config_id=str(config.id),
                    native_context=context,
                ),
            )
        )
        assert setup.status == "READY", setup

        def row_input(row_id):
            return ProcessRowInput(
                row_id=row_id,
                column_id=setup.column_id,
                dataset_id=dataset_id,
                experiment_id=str(experiment.id),
                messages=setup.messages,
                model=setup.model_name,
                model_config=setup.model_config,
                output_format=setup.output_format,
                run_prompt_config=setup.run_prompt_config,
                native_context=context,
            )

        first, second = setup.row_ids
        result = asyncio.run(env.run(activities.process_row_activity, row_input(first)))
        stored_cell = (
            Cell.no_workspace_objects.filter(
                column_id=setup.column_id, row_id=first, deleted=False
            )
            .values_list("value", flat=True)
            .first()
        )
        assert result.status == "COMPLETED", stored_cell
        # The next row: its activity is refused before any call (the Stop
        # ended the run), so nothing is sent.
        with pytest.raises(Exception, match="cancelled"):
            asyncio.run(env.run(activities.process_row_activity, row_input(second)))

    assert_kept_counted_and_charged_once(experiment, context, answers, provider)


@pytest.mark.skipif(
    not os.environ.get("TEMPORAL_TEST_SERVER"),
    reason="no Temporal test server here: it cannot be downloaded on the test machines",
)
def test_a_prompt_row_in_flight_at_the_stop_through_the_prompt_workflow(tenants):
    from tfc.temporal.experiments import activities
    from tfc.temporal.experiments.run_control_activities import (
        bind_experiment_run_activity,
        persist_experiment_run_state_activity,
    )
    from tfc.temporal.experiments.types import ProcessPromptV2WorkflowInput
    from tfc.temporal.experiments.workflows import ProcessPromptV2Workflow

    actor, experiment, config, context = prompt_world(tenants)
    answers = []

    def provider_call(*args, **kwargs):
        # Inside the workflow's row activity: Stop is pressed while this
        # row's call is in flight.
        answers.append(stop(actor, experiment))
        return ("late output", REPORTED)

    def first_row_settled():
        run = ExperimentRun._base_manager.get(pk=context["run_id"])
        return run.items.filter(deleted=False, status="completed").exists()

    async def drive():
        async with await WorkflowEnvironment.start_time_skipping(
            **test_server_options()
        ) as env:
            queue = f"stop-in-flight-{uuid4()}"
            async with Worker(
                env.client,
                task_queue=queue,
                workflow_runner=UnsandboxedWorkflowRunner(),
                workflows=[ProcessPromptV2Workflow],
                activities=[
                    bind_experiment_run_activity,
                    persist_experiment_run_state_activity,
                    activities.setup_prompt_v2_activity,
                    activities.process_row_activity,
                    activities.check_column_status_activity,
                    activities.check_experiment_dataset_status_activity,
                ],
            ):
                handle = await env.client.start_workflow(
                    ProcessPromptV2Workflow.run,
                    ProcessPromptV2WorkflowInput(
                        experiment_id=str(experiment.id),
                        dataset_id=str(experiment.snapshot_dataset_id),
                        experiment_prompt_config_id=str(config.id),
                        max_concurrent_rows=1,
                        native_context=context,
                    ),
                    id=f"process-prompt-{uuid4()}",
                    task_queue=queue,
                )
                for _ in range(600):
                    if await sync_to_async(first_row_settled)():
                        break
                    await asyncio.sleep(0.05)
                # The Stop cancels the workflow (its cancel is a stand-in
                # here, so cancel it as the service would).
                await handle.cancel()
                with pytest.raises(WorkflowFailureError):
                    await handle.result()

    with (
        patch(PAYER, return_value=PayerRoute.byok("openai")),
        patch(PROVIDER, autospec=True, side_effect=provider_call) as provider,
    ):
        asyncio.run(drive())

    assert_kept_counted_and_charged_once(experiment, context, answers, provider)


# 2. Queued evaluation rows, through the evaluation activity --------------------------


@pytest.fixture
def evaluation_world(organization, workspace, user, monkeypatch):
    experiment, rows, variants = seed(organization, workspace, user)
    dataset = experiment.snapshot_dataset
    variant, _, output = variants[0]
    output.source = SourceChoices.EXPERIMENT.value
    output.source_id = str(variant.id)
    output.save(update_fields=["source", "source_id"])
    # Six rows with an output: the pool runs five, the sixth waits.
    rows = list(rows) + [
        Row.objects.create(dataset=dataset, order=order) for order in range(3, 6)
    ]
    Cell.objects.filter(column=output).update(
        value="answer", status=CellStatus.PASS.value
    )
    for row in rows[3:]:
        Cell.objects.create(
            dataset=dataset,
            row=row,
            column=output,
            value="answer",
            status=CellStatus.PASS.value,
        )
    ExperimentsTable.objects.filter(pk=experiment.pk).update(
        status=StatusType.RUNNING.value
    )
    template = EvalTemplate.no_workspace_objects.create(
        name="tone-check",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        # A registered evaluator type (the bridge tests' fixture type, active
        # through native_fixture_evaluator): the runner resolves its class
        # before any row (load_user_eval_metric -> get_eval_class), and its
        # engine is the evaluator stand-in.
        config={"eval_type_id": "NativeFixture", "output": "score"},
    )
    metric = UserEvalMetric.objects.create(
        name="tone",
        organization=user.organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        config={"mapping": {}},
        user=user,
    )
    from tfc.utils.distributed_state import evaluation_tracker

    monkeypatch.setattr(evaluation_tracker, "should_cancel", lambda *_: False)
    return experiment, rows, output, metric, user, workspace


def test_a_queued_evaluation_row_after_the_stop_is_never_charged_on_its_activity(
    evaluation_world, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from model_hub.views.eval_runner import EvaluationRunner
    from tfc.temporal.experiments import activities
    from tfc.temporal.experiments.types import ProcessBatchEvalInput

    experiment, rows, output, metric, user, workspace = evaluation_world
    monkeypatch.setattr(
        CANCEL,
        lambda _id: {
            "main_cancelled": True,
            "rerun_cancelled": 0,
            "main_confirmed": True,
            "reruns_confirmed": True,
        },
    )
    stopped, first = threading.Event(), threading.Lock()

    def evaluator_run(*args, **kwargs):
        # The evaluator call (its engine is the stand-in). The runner charges a
        # row when its call is prepared, just before this.
        if first.acquire(blocking=False):
            # Five rows are in flight; the sixth waits in the pool. Stop.
            deadline = time.monotonic() + 20
            while billing.call_count < 5 and time.monotonic() < deadline:
                time.sleep(0.01)
            response = call(
                ExperimentStopV2View, "post", experiment.id, user, workspace
            )
            assert response.status_code == 200, response.data
            stopped.set()
        else:
            assert stopped.wait(30)
        return engine.run.return_value

    # run_prompt logs and swallows what it raises: keep it, so a failure here
    # names it.
    raised = []

    def recording(method):
        def wrapper(self, *args, **kwargs):
            try:
                return method(self, *args, **kwargs)
            except Exception as error:
                raised.append(f"{method.__name__}: {error!r}")
                raise

        return wrapper

    from model_hub.views import eval_runner

    refund = MagicMock()
    original_usage = EvaluationRunner._emit_cell_usage
    with (
        native_eval_transport(score=1) as (engine, billing),
        native_preflight(MagicMock(return_value=SimpleNamespace(allowed=True))),
        patch.object(eval_runner, "refund_cost_for_api_call", refund),
        patch.object(
            EvaluationRunner,
            "_emit_cell_usage",
            autospec=True,
            side_effect=original_usage,
        ) as usage,
        patch.object(
            EvaluationRunner,
            "load_user_eval_metric",
            recording(EvaluationRunner.load_user_eval_metric),
        ),
        patch.object(
            EvaluationRunner, "update_cell", recording(EvaluationRunner.update_cell)
        ),
    ):
        engine.run.side_effect = evaluator_run
        result = asyncio.run(
            ActivityEnvironment().run(
                activities.process_batch_template_eval_activity,
                ProcessBatchEvalInput(
                    row_ids=[str(row.id) for row in rows],
                    column_id=str(output.id),
                    dataset_id=str(experiment.snapshot_dataset_id),
                    experiment_id=str(experiment.id),
                    eval_template_id=str(metric.id),
                ),
            )
        )

    assert result is not None
    result_column = Column.objects.get(
        dataset=experiment.snapshot_dataset,
        deleted=False,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id__endswith=f"-sourceid-{metric.id}",
    )
    cells = {
        str(cell.row_id): (cell.value, cell.status)
        for cell in Cell.objects.filter(column=result_column, deleted=False)
    }
    seen = (raised, cells)
    # Each row sent before the Stop was charged once and evaluated once, its
    # usage was recorded once, and no charge was refunded. The queued row
    # was never charged.
    assert billing.call_count == 5, seen
    assert engine.run.call_count == 5, seen
    assert usage.call_count == 5, seen
    assert refund.call_count == 0, seen
    # Kept: the results of the five rows already sent, and their usage.
    kept = {
        row_id
        for row_id, (value, status) in cells.items()
        if status == CellStatus.PASS.value and value != STOPPED
    }
    assert len(kept) == 5, seen
    assert {str(c.args[3].id) for c in usage.call_args_list} == kept, seen
    # Never sent: the queued row says the experiment stopped.
    queued = set(cells) - kept
    assert len(cells) == len(rows) and len(queued) == 1, seen
    assert cells[queued.pop()] == (STOPPED, CellStatus.ERROR.value), seen
