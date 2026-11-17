"""The experiment's Stop on the default path (run control off).

(A) A row whose call was already sent when Stop is pressed is saved and
    counted, even when its result arrives after the stop ended the run; no
    new call is sent after the stop. Nothing is paid for and then dropped.
(B) Once every workflow of the experiment is known cancelled, the stop gives
    every unfinished run its terminal seal at once, with the cleanup's own
    fenced step, so a cleanup that later fails (it runs inside the cancelled
    workflow and swallows its errors) never leaves the experiment refusing
    edits and reruns.

With (A) and (B) the four whole-stop conditions hold with run control off:
1. a row in flight at the stop is saved, counted and charged once;
2. after the stop, whether the workflow's cleanup succeeds or fails, the
   experiment can be edited and rerun (ensure_native_idle passes);
3. the stop's step and the workflow's cleanup end a graph run once, in
   either order, with no error;
4. nothing is ended by the stop when it did not reach every workflow. (The
   earlier guard "a running run is left to its cleanup" no longer holds by
   design: a confirmed stop now ends it, and its row in flight still saves.)

Real create, setup, row execution, Stop view and cleanup; only the provider
call, the workflow service and the evaluator engine are stand-ins.
"""

from unittest.mock import patch

import pytest

from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.experiments import ExperimentRun, ExperimentsTable
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.credit_ledger_port import PayerRoute
from model_hub.services.experiment_run_writes import transition_run
from model_hub.services.experiment_submit import ensure_native_idle, state_of
from model_hub.services.native_run_projection import project_run
from model_hub.tests.test_experiment_run_control import seed
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    PROVIDER,
    create,
    native_fixture_evaluator,
    r8_create_graph,
    r8_graph_context,
    row,
    setup,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants
from tfc.temporal.experiments import activities

# Row execution manages worker connections, as the native bridge tests do.
pytestmark = pytest.mark.django_db(transaction=True)

CANCEL = "tfc.temporal.experiments.cancel_all_experiment_workflows"
CONFIRMED = {
    "main_cancelled": True,
    "rerun_cancelled": 0,
    "main_confirmed": True,
    "reruns_confirmed": True,
}
NOT_REACHED = {
    "main_cancelled": False,
    "rerun_cancelled": 0,
    "main_confirmed": False,
    "reruns_confirmed": True,
}


@pytest.fixture(autouse=True)
def run_control_off(settings):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = False


def running(experiment):
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.RUNNING.value
    )


def stop(actor, experiment, cancelled=CONFIRMED):
    with patch(CANCEL, return_value=dict(cancelled)):
        response = actor["client"].post(
            f"/model-hub/experiments/v2/{experiment.id}/stop/", {}, format="json"
        )
    assert response.status_code == 200, response.content
    return response.json()["result"]


def stored(run_id):
    return ExperimentRun._base_manager.get(pk=run_id)


def native_world(tenants, rows=1):
    actor = tenants["a"]
    # The shared tenant dataset has one row; add the rest before the create
    # copies it, as the bridge tests do.
    source = actor["exp"]["dataset"]
    source_column = Column.no_workspace_objects.get(dataset=source, deleted=False)
    for order in range(1, rows):
        extra = Row.no_workspace_objects.create(dataset=source, order=order)
        Cell.no_workspace_objects.create(
            dataset=source, column=source_column, row=extra, value=f"row {order}"
        )
    response, experiment, _, _ = create(actor)
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    assert len(prepared["row_ids"]) == rows
    running(experiment)
    return actor, experiment, context, prepared


# (A) and condition 1 -----------------------------------------------------------


def test_a_row_in_flight_at_the_stop_is_saved_counted_and_charged_once(tenants):
    actor, experiment, context, prepared = native_world(tenants, rows=2)
    first, second = prepared["row_ids"][0], prepared["row_ids"][1]
    answers = []

    def provider_call(*args, **kwargs):
        # Stop is pressed while this row's call is in flight.
        answers.append(stop(actor, experiment))
        return ("late output", {})

    # The tenant pays with its own key: the payer lookup is stubbed, as in
    # test_experiment_version_params, so the row reaches its provider call
    # (without a route it refuses before the call, ModelRouteUnavailable).
    with (
        patch(
            "model_hub.services.credit_ledger_port.resolve_payer_route",
            return_value=PayerRoute.byok("openai"),
        ),
        patch(PROVIDER, autospec=True, side_effect=provider_call) as provider,
    ):
        result = row(experiment, context, prepared, first)
        stored_cell = (
            Cell.no_workspace_objects.filter(
                column_id=prepared["column_id"], row_id=first, deleted=False
            )
            .values_list("value", flat=True)
            .first()
        )
        assert result["status"] == "COMPLETED", stored_cell
        # The stop answered while the row was in flight, and ended the run.
        assert answers[0]["rows_in_flight"] == 1
        # After the stop no new call is sent.
        with pytest.raises(Exception):  # noqa: B017
            row(experiment, context, prepared, second)
    assert provider.call_count == 1

    run = stored(context["run_id"])
    assert run.status == "cancelled" and run.completed_at is not None
    items = list(run.items.filter(deleted=False))
    assert [(item.status, item.output) for item in items] == [
        ("completed", "late output")
    ]
    # Its evaluators were not called after the stop; the row says why.
    assert {ref["reason"] for ref in items[0].score_refs} == {"execution_stopped"}
    # Saved where the page reads it, and counted.
    assert (
        Cell.no_workspace_objects.filter(
            column_id=prepared["column_id"],
            value="late output",
            status=CellStatus.PASS.value,
        ).count()
        == 1
    )
    assert project_run(run, dataset=experiment.dataset)["counts"]["completed"] == 1


def test_a_classic_row_in_flight_at_the_stop_keeps_its_result(
    organization, workspace, user
):
    from model_hub.views.experiment_runner import _process_row_impl

    experiment, rows, variants = seed(organization, workspace, user)
    running(experiment)
    _, _, column = variants[0]
    sent = []

    def prompt_response(run_prompt, experiment_row, row_id, column_id):
        sent.append(row_id)
        # The stop lands while this call is in flight; its cleanup marks the
        # running cells stopped.
        ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
            status=StatusType.CANCELLED.value
        )
        Cell.no_workspace_objects.filter(
            column=column, status=CellStatus.RUNNING.value
        ).update(status=CellStatus.ERROR.value, value="Experiment stopped by user")
        return "late output", {"data": {"response": "late output"}}

    args = {
        "column_id": column.id,
        "dataset_id": experiment.dataset_id,
        "experiment_id": experiment.id,
        "messages": [{"role": "user", "content": "Say hi"}],
        "model": "gpt-4o-mini",
        "model_config": {},
        "skip_celery_evals": True,
    }
    with patch(
        "tfc.temporal.experiments.provider_policy.prompt_response",
        side_effect=prompt_response,
    ):
        _process_row_impl(row_id=rows[0].id, **args)
        # After the stop no new call is sent; the next row keeps its mark.
        _process_row_impl(row_id=rows[1].id, **args)
    assert sent == [rows[0].id]
    kept = Cell.no_workspace_objects.get(column=column, row=rows[0])
    assert (kept.value, kept.status) == ("late output", CellStatus.PASS.value)
    untouched = Cell.no_workspace_objects.get(column=column, row=rows[1])
    assert untouched.status == CellStatus.ERROR.value


# (B) and condition 2 -----------------------------------------------------------


def test_a_confirmed_stop_ends_every_run_and_a_failing_cleanup_changes_nothing(tenants):
    actor, experiment, context, _ = native_world(tenants)
    with pytest.raises(ValueError):
        ensure_native_idle(experiment)

    stop(actor, experiment)
    run = stored(context["run_id"])
    assert run.status == "cancelled" and run.completed_at is not None
    assert ensure_native_idle(experiment) is None

    # The cancelled workflow's own cleanup then fails: nothing changes.
    contexts = state_of(experiment)["workflow_args"]["native_runs"]
    with patch.object(
        activities,
        "_stop_experiment_cleanup_sync",
        side_effect=RuntimeError("cleanup failed"),
    ):
        with pytest.raises(RuntimeError):
            activities._cleanup_native_execution_sync(
                str(experiment.id), contexts, stopped=True
            )
    assert stored(context["run_id"]).completed_at == run.completed_at
    assert ensure_native_idle(experiment) is None


def test_after_the_stop_and_a_cleanup_that_succeeds_it_stays_unlocked(tenants):
    actor, experiment, context, _ = native_world(tenants)
    stop(actor, experiment)
    sealed = stored(context["run_id"]).completed_at
    contexts = state_of(experiment)["workflow_args"]["native_runs"]
    result = activities._cleanup_native_execution_sync(
        str(experiment.id), contexts, stopped=True
    )
    assert result["status"] == "COMPLETED"
    assert stored(context["run_id"]).completed_at == sealed
    assert ensure_native_idle(experiment) is None


# Condition 4 -------------------------------------------------------------------


def test_a_stop_that_did_not_reach_every_workflow_ends_nothing_itself(tenants):
    actor, experiment, context, _ = native_world(tenants)
    stop(actor, experiment, NOT_REACHED)
    run = stored(context["run_id"])
    assert run.completed_at is None
    # Left to the workflow's cleanup, as before.
    with pytest.raises(ValueError):
        ensure_native_idle(experiment)


def test_a_stop_whose_rerun_search_failed_ends_nothing_itself(tenants):
    actor, experiment, context, _ = native_world(tenants)
    stop(actor, experiment, {**CONFIRMED, "reruns_confirmed": False})
    assert stored(context["run_id"]).completed_at is None


# Condition 3 -------------------------------------------------------------------


def graph_running(tenants):
    world = r8_create_graph(tenants["a"])
    _, binding, _ = r8_graph_context(world)
    run = binding.run
    transition_run(
        run_id=run.id,
        status="running",
        organization_id=run.dataset_version.organization_id,
        workspace_id=run.dataset_version.workspace_id,
    )
    running(world["experiment"])
    contexts = state_of(world["experiment"])["workflow_args"]["native_runs"]
    return world, run, contexts


def test_the_stop_then_the_cleanup_end_a_graph_run_once(tenants):
    world, run, contexts = graph_running(tenants)
    settle = activities._settle_cancelled_native_graph
    with patch.object(
        activities, "_settle_cancelled_native_graph", wraps=settle
    ) as settled:
        stop(world["actor"], world["experiment"])
        sealed = stored(run.id)
        assert sealed.status == "cancelled" and sealed.completed_at is not None
        assert settled.call_count == 1
        result = activities._cleanup_native_execution_sync(
            str(world["experiment"].id), contexts, stopped=True
        )
        assert settled.call_count == 1
    assert result["status"] == "COMPLETED"
    assert stored(run.id).completed_at == sealed.completed_at


def test_the_cleanup_then_the_stop_end_a_graph_run_once(tenants):
    world, run, contexts = graph_running(tenants)
    activities._cleanup_native_execution_sync(
        str(world["experiment"].id), contexts, stopped=True
    )
    sealed = stored(run.id)
    assert sealed.status == "cancelled" and sealed.completed_at is not None
    stop(world["actor"], world["experiment"])
    assert stored(run.id).completed_at == sealed.completed_at
    assert NativeRunBinding.objects.get(run_id=run.id).run.status == "cancelled"


# What "known cancelled" means ------------------------------------------------


class StandInWorkflowService:
    """The workflow service as the stop sees it: the main workflow is running,
    absent or unreachable; the rerun search works or fails."""

    def __init__(self, main, search_fails=False):
        self.main = main
        self.search_fails = search_fails

    def get_workflow_handle(self, workflow_id):
        from types import SimpleNamespace

        from temporalio.client import WorkflowExecutionStatus
        from temporalio.service import RPCError, RPCStatusCode

        async def describe():
            if self.main == "absent":
                raise RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
            if self.main == "unreachable":
                raise RuntimeError("connection refused")
            return SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)

        async def cancel():
            return None

        return SimpleNamespace(describe=describe, cancel=cancel)

    def list_workflows(self, query):
        service = self

        class Search:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if service.search_fails:
                    raise RuntimeError("visibility store unavailable")
                raise StopAsyncIteration

        return Search()


@pytest.mark.parametrize(
    "main,search_fails,expected",
    [
        (
            "running",
            False,
            {"main_cancelled": True, "main_confirmed": True, "reruns_confirmed": True},
        ),
        (
            "absent",
            False,
            {"main_cancelled": False, "main_confirmed": True, "reruns_confirmed": True},
        ),
        (
            "unreachable",
            False,
            {
                "main_cancelled": False,
                "main_confirmed": False,
                "reruns_confirmed": True,
            },
        ),
        (
            "running",
            True,
            {"main_cancelled": True, "main_confirmed": True, "reruns_confirmed": False},
        ),
    ],
)
def test_the_cancel_reports_what_it_knows(monkeypatch, main, search_fails, expected):
    import asyncio
    from unittest.mock import AsyncMock

    from tfc.temporal.experiments import client as experiments_client

    monkeypatch.setattr(
        experiments_client,
        "get_client",
        AsyncMock(return_value=StandInWorkflowService(main, search_fails)),
    )
    result = asyncio.run(experiments_client.cancel_all_experiment_workflows_async("x"))
    assert {key: result[key] for key in expected} == expected
    assert result["rerun_cancelled"] == 0
