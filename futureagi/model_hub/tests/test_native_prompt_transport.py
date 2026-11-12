"""Native workflow context survives each transport boundary; no network."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from model_hub.models.choices import StatusType
from model_hub.tests.test_native_prompt_create_bridge import (
    create, tenants, native_fixture_evaluator,  # noqa: F401 -- imported autouse fixture
)
from model_hub.services.experiment_submit import state_of
from temporalio.client import WorkflowExecutionStatus
from tfc.temporal.experiments import client
from tfc.temporal.experiments.types import RunExperimentInput
from tfc.temporal.experiments.workflows import RunExperimentV2Workflow

# Real worker helpers manage connections and async calls cross DB threads.
pytestmark = pytest.mark.django_db(transaction=True)


def test_admitted_client_carries_exact_native_run_mapping(tenants):
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    state = state_of(experiment)
    remote = MagicMock()
    description = MagicMock(status=WorkflowExecutionStatus.RUNNING)
    description.memo = AsyncMock(return_value={"experiment_submission_id": state["admission_id"]})
    remote.get_workflow_handle.return_value.describe = AsyncMock(return_value=description)
    with patch("tfc.temporal.experiments.admission.admit_v2_workflow", new_callable=AsyncMock, return_value="confirmed") as admit, patch.object(
        client, "get_client", new_callable=AsyncMock, return_value=remote,
    ):
        result = asyncio.run(client._start_admitted_v2_workflow_async(
            experiment_id=str(experiment.id), max_concurrent_rows=10, task_queue="tasks_l",
            rerun_prompt_config_ids=[], rerun_agent_config_ids=[], rerun_eval_template_ids=[],
            column_changed=False, admission_id=state["admission_id"], native_runs=state["workflow_args"]["native_runs"],
        ))
    assert result == "confirmed"
    assert admit.call_args.args[0].native_runs == state["workflow_args"]["native_runs"]
    assert admit.call_args.args[1] == state["admission_id"]


def test_native_context_requires_committed_admission_identity():
    with pytest.raises(ValueError, match="admission"):
        asyncio.run(client._start_admitted_v2_workflow_async(
            experiment_id="not-dispatched", max_concurrent_rows=10, task_queue="tasks_l",
            rerun_prompt_config_ids=[], rerun_agent_config_ids=[], rerun_eval_template_ids=[],
            column_changed=False, admission_id=None, native_runs={"config": {"run_id": "run"}},
        ))


def test_parent_dispatches_native_context_and_cleanup_retains_identity(tenants):
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    contexts = state_of(experiment)["workflow_args"]["native_runs"]
    child_inputs = []
    cleanups = []

    async def execute(name, value, **kwargs):
        if name == "setup_experiment_activity":
            return {"status": "READY", "prompt_configs": list(contexts), "agent_configs": [],
                    "eval_template_ids": [], "dataset_id": str(experiment.snapshot_dataset_id), "has_base_column": False}
        if name == "stop_experiment_cleanup_activity":
            cleanups.append(value.native_runs)
        return {"status": "COMPLETED"}

    async def start_child(_function, value, **kwargs):
        child_inputs.append(value)
        async def complete():
            return {"status": "COMPLETED", "completed_rows": 1, "failed_rows": 0}
        return asyncio.create_task(complete())

    input = RunExperimentInput(experiment_id=str(experiment.id), native_runs=contexts)
    with patch("tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=execute), patch(
        "tfc.temporal.experiments.workflows.workflow.start_child_workflow", side_effect=start_child,
    ):
        result = asyncio.run(RunExperimentV2Workflow().run(input))
    assert result.status == "COMPLETED"
    assert child_inputs[0].native_context == contexts[child_inputs[0].experiment_prompt_config_id]

    async def cancel(name, value, **kwargs):
        if name == "setup_experiment_activity":
            raise asyncio.CancelledError()
        return await execute(name, value, **kwargs)
    with patch("tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=cancel):
        result = asyncio.run(RunExperimentV2Workflow().run(input))
    assert result.status == "CANCELLED" and cleanups == [contexts]


@pytest.mark.parametrize("status", [
    WorkflowExecutionStatus.FAILED, WorkflowExecutionStatus.CANCELED,
    WorkflowExecutionStatus.TERMINATED, WorkflowExecutionStatus.TIMED_OUT,
    WorkflowExecutionStatus.COMPLETED,
])
def test_same_memo_terminal_worker_is_not_recovered_admission(tenants, status):
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.tests.test_native_prompt_create_bridge import START
    response, experiment, body, _ = create(
        tenants["a"], transport={"side_effect": RuntimeError("lost ack")},
    )
    assert response.status_code == 503
    state = state_of(experiment)
    description = MagicMock(status=status)
    description.memo = AsyncMock(return_value={"experiment_submission_id": state["admission_id"]})
    remote = MagicMock()
    remote.get_workflow_handle.return_value.describe = AsyncMock(return_value=description)
    remote.start_workflow = AsyncMock()

    def start(**kwargs):
        return asyncio.run(client._start_admitted_v2_workflow_async(**{
            "max_concurrent_rows": 10, "task_queue": "tasks_l",
            "rerun_prompt_config_ids": [], "rerun_agent_config_ids": [],
            "rerun_eval_template_ids": [], "column_changed": False, **kwargs,
        }))

    # Exercise the existing memo recovery code, not a replacement for admission.
    with patch("tfc.temporal.experiments.admission.get_client", new_callable=AsyncMock, return_value=remote), patch.object(
        client, "get_client", new_callable=AsyncMock, return_value=remote,
    ), patch(START, side_effect=start):
        response = tenants["a"]["client"].post("/model-hub/experiments/v2/", body, format="json")
    assert response.status_code == 503
    assert state_of(experiment)["state"] == "pending"
    binding = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    assert binding.run.status == "draft" and binding.run.completed_at is None
    assert binding.run.items.count() == 0
    remote.start_workflow.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_lost_ack_parent_waits_past_setup_budget_then_same_child_executes_once(tenants):
    from asgiref.sync import sync_to_async
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.services.experiment_submit import native_admission_ready
    from model_hub.tests.test_native_prompt_create_bridge import START, PROVIDER
    from tfc.temporal.experiments import activities
    from tfc.temporal.experiments.workflows import ProcessPromptV2Workflow

    response, experiment, body, lost = create(
        tenants["a"], transport={"side_effect": RuntimeError("remote start accepted; ack lost")},
    )
    assert response.status_code == 503
    state = state_of(experiment)
    contexts = state["workflow_args"]["native_runs"]
    binding = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    original_run_id = binding.run_id
    polls, children, cleanups, analyses = [], [], [], []

    async def call(function, *args, **kwargs):
        return await sync_to_async(function, thread_sensitive=True)(*args, **kwargs)

    async def execute(name, value, **kwargs):
        if name == "setup_experiment_activity":
            if value.admission_only:
                ready = await call(native_admission_ready, value.experiment_id, value.native_runs)
                polls.append(ready)
                return {"status": "READY" if ready else "ADMISSION_PENDING"}
            return await call(activities._setup_experiment_sync, value.experiment_id)
        if name == "analyze_dependencies_activity":
            # The create fixture has an output-mapped evaluator. This is normal
            # scheduling after acknowledgement, never failure/stop cleanup.
            assert polls == [False] * 5 + [True] and not children and not cleanups
            analysis = await call(activities._analyze_dependencies_sync,
                                  value.experiment_id, value.eval_template_ids)
            assert value.eval_template_ids
            assert analysis["independent_eval_ids"] == []
            assert set(analysis["dependent_eval_ids"]) == set(value.eval_template_ids)
            analyses.append(analysis)
            return analysis
        if name == "setup_prompt_v2_activity":
            return await call(activities._setup_prompt_v2_sync, value.experiment_id,
                              value.dataset_id, value.experiment_prompt_config_id,
                              native_context=value.native_context)
        if name == "process_row_activity":
            return await call(activities._process_row_sync, value.row_id, value.column_id,
                              value.dataset_id, value.experiment_id, value.messages,
                              value.model, value.model_config, value.output_format,
                              value.run_prompt_config, native_context=value.native_context)
        if name == "check_column_status_activity":
            return await call(activities._check_column_status_sync, value.entity_id)
        if name == "check_experiment_dataset_status_activity":
            return await call(activities._check_experiment_dataset_status_sync,
                              value.entity_id, native_context=value.native_context)
        if name == "check_experiment_status_activity":
            return await call(activities._check_experiment_status_sync, value.entity_id)
        if name == "cleanup_running_cells_activity" and not value.native_runs:
            return await call(activities._cleanup_running_cells_sync, value.experiment_id)
        cleanups.append(name)
        raise AssertionError("Unexpected failure/stop activity: " + name)

    async def child(_function, value, **kwargs):
        children.append(value)
        return asyncio.create_task(ProcessPromptV2Workflow().run(value))

    async def timer(_duration):
        # Five successful pending polls exceed the old three-attempt failure path.
        assert not children and not cleanups
        current = await call(lambda: NativeRunBinding.objects.get(run_id=original_run_id).run)
        assert current.status == "draft" and current.completed_at is None
        provider.assert_not_called()
        if len(polls) == 5:
            with patch(START, return_value="same-live-worker") as retry:
                reply = await call(tenants["a"]["client"].post,
                                   "/model-hub/experiments/v2/", body, format="json")
            assert reply.status_code == 200, reply.content
            assert retry.call_args == lost.call_args

    input = RunExperimentInput(experiment_id=str(experiment.id), native_runs=contexts)
    with patch(PROVIDER, autospec=True, return_value=("recovered output", {})) as provider, patch(
        "tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=execute,
    ), patch("tfc.temporal.experiments.workflows.workflow.start_child_workflow", side_effect=child), patch(
        "tfc.temporal.experiments.workflows.workflow.sleep", side_effect=timer,
    ), patch("tfc.temporal.experiments.run_control.workflow.patched", return_value=False):
        result = asyncio.run(RunExperimentV2Workflow().run(input))
    assert result.status == StatusType.COMPLETED.value
    assert polls == [False] * 5 + [True] and len(children) == 1 and not cleanups
    assert children[0].native_context == next(iter(contexts.values()))
    assert len(analyses) == 1
    assert children[0].eval_template_ids == analyses[0]["dependent_eval_ids"]
    binding.run.refresh_from_db()
    assert binding.run.id == original_run_id and binding.run.status == "completed"
    assert binding.run.completed_at is not None and binding.run.items.count() == 1
    assert binding.run.items.get().output == "recovered output"
    provider.assert_called_once()
    with patch(START) as replay:
        reply = tenants["a"]["client"].post("/model-hub/experiments/v2/", body, format="json")
    assert reply.status_code == 200
    replay.assert_not_called()
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 1


@pytest.mark.parametrize("lost_ack", [False, True])
def test_append_binds_only_new_variant_and_preserves_original_evidence(tenants, lost_ack):
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.services.results_catalog import project_run as read_run
    from model_hub.tests import test_experiment_append_variant as fixtures
    from model_hub.tests.test_native_prompt_create_bridge import START, PROVIDER, setup, row, finish
    from tfc.temporal.experiments import activities

    actor = tenants["a"]
    response, experiment, _, _ = create(actor)
    assert response.status_code == 200, response.content
    original_context, prepared = setup(experiment)
    with patch(PROVIDER, autospec=True, return_value=("original", {})):
        for row_id in prepared["row_ids"]:
            row(experiment, original_context, prepared, row_id)
    finish(original_context, prepared)
    activities._check_experiment_status_sync(str(experiment.id))
    before = fixtures.snapshot(experiment)
    original = NativeRunBinding.objects.get(run_id=original_context["run_id"])
    old_items = list(original.run.items.values())
    template, version = fixtures.prompt(actor, "appended version")
    body = {"append_only": True, "prompt_config": [fixtures.entry(template, version)]}
    url = f"/model-hub/experiments/v2/{experiment.id}/"
    transport = {"side_effect": RuntimeError("ack lost")} if lost_ack else {"return_value": "appended"}
    with patch(START, **transport) as first:
        response = actor["client"].put(url, body, format="json")
    assert response.status_code == (503 if lost_ack else 200), response.content
    contexts = state_of(experiment)["workflow_args"]["native_runs"]
    assert len(contexts) == 1 and original_context["config_id"] not in contexts
    context = next(iter(contexts.values()))
    assert first.call_args.kwargs["rerun_prompt_config_ids"] == [context["config_id"]]
    assert first.call_args.kwargs["native_runs"] == contexts
    child = NativeRunBinding.objects.get(run_id=context["run_id"])
    assert child.execution_id != original.execution_id and child.run_id != original.run_id
    assert child.snapshot_id == original.snapshot_id
    if lost_ack:
        assert child.run.status == "draft"
        with patch(START, return_value="appended") as retry:
            response = actor["client"].put(url, body, format="json")
        assert response.status_code == 200, response.content
        assert retry.call_args == first.call_args
        assert state_of(experiment)["workflow_args"]["native_runs"] == contexts
    with patch(START) as replay:
        response = actor["client"].put(url, body, format="json")
    assert response.status_code == 200
    replay.assert_not_called()
    after = fixtures.snapshot(experiment)
    for kind in before:
        persisted = {item["id"]: item for item in after[kind]}
        assert all(persisted[item["id"]] == item for item in before[kind]), kind
    prepared = activities._setup_prompt_v2_sync(
        str(experiment.id), str(experiment.snapshot_dataset_id), context["config_id"],
        native_context=context,
    )
    with patch(PROVIDER, autospec=True, return_value=("appended", {})) as provider:
        for row_id in prepared["row_ids"]:
            row(experiment, context, prepared, row_id)
    finish(context, prepared)
    provider.assert_called_once()
    assert list(original.run.items.values()) == old_items
    child.run.refresh_from_db()
    assert child.run.status == "completed" and child.run.items.get().output == "appended"
    assert child.run.items.get().example_id == original.run.items.get().example_id
    assert child.run.dataset_version_id == original.run.dataset_version_id
    assert read_run(child.run)["run_id"] == str(child.run_id)
    assert read_run(original.run)["run_id"] == str(original.run_id)
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 2


def test_live_workflow_with_changed_memo_cannot_confirm_native_admission():
    remote = MagicMock()
    description = MagicMock(status=WorkflowExecutionStatus.RUNNING)
    description.memo = AsyncMock(return_value={"experiment_submission_id": "another-submission"})
    remote.get_workflow_handle.return_value.describe = AsyncMock(return_value=description)
    with patch("tfc.temporal.experiments.admission.admit_v2_workflow", new_callable=AsyncMock, return_value="workflow"), patch.object(
        client, "get_client", new_callable=AsyncMock, return_value=remote,
    ):
        with pytest.raises(ValueError, match="live matching"):
            asyncio.run(client._start_admitted_v2_workflow_async(
                experiment_id="experiment", max_concurrent_rows=10, task_queue="tasks_l",
                rerun_prompt_config_ids=[], rerun_agent_config_ids=[], rerun_eval_template_ids=[],
                column_changed=False, admission_id="submission", native_runs={"config": {"run_id": "run"}},
            ))


def test_pending_parent_bounds_history_without_changing_execution_identity():
    class Continued(BaseException):
        pass

    value = RunExperimentInput(experiment_id="experiment", native_runs={"config": {"run_id": "run", "execution_id": "submission"}})
    polls, continued = [], []

    async def execute(name, received, **kwargs):
        assert name == "setup_experiment_activity" and received.admission_only
        assert received.native_runs == value.native_runs
        polls.append(received)
        return {"status": "ADMISSION_PENDING"}

    def continue_as_new(received, **kwargs):
        assert kwargs == {"memo": {"experiment_submission_id": "submission"}}
        continued.append(received)
        raise Continued()

    with patch("tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=execute), patch(
        "tfc.temporal.experiments.workflows.workflow.sleep", new_callable=AsyncMock,
    ), patch("tfc.temporal.experiments.workflows.workflow.continue_as_new", side_effect=continue_as_new), patch(
        "tfc.temporal.experiments.workflows.workflow.start_child_workflow", new_callable=AsyncMock,
    ) as child:
        with pytest.raises(Continued):
            asyncio.run(RunExperimentV2Workflow().run(value))
    assert len(polls) == 120 and continued == [value]
    child.assert_not_called()


@pytest.mark.parametrize("mode", ["cells", "columns", "failed_only"])
@pytest.mark.django_db(transaction=True)
def test_subset_parent_schedules_and_records_exact_plan_through_real_activities(tenants, mode):
    from asgiref.sync import sync_to_async
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.tests.test_native_prompt_create_bridge import subset_world, subset_post, PROVIDER
    from model_hub.services.experiment_submit import native_admission_ready
    from tfc.temporal.experiments import activities
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow, ProcessPromptV2Workflow
    experiment, configs, columns, rows = subset_world(tenants["a"])
    body = {"cells": [{"column_id": str(columns[i].id), "row_id": rows[i]} for i in range(2)]}
    if mode != "cells":
        body = {"source_ids": [str(config.experiment_dataset_id) for config in configs]}
    if mode == "failed_only":
        body["failed_only"] = True
        for i, column in enumerate(columns):
            Cell.no_workspace_objects.filter(column=column, row_id=rows[i]).update(status=CellStatus.ERROR.value)
    response, _ = subset_post(tenants["a"], experiment, body)
    assert response.status_code == 200, response.content
    state = state_of(experiment)
    contexts = state["workflow_args"]["native_runs"]
    children, scheduled, acknowledgements = [], [], []

    async def call(function, *args, **kwargs):
        return await sync_to_async(function, thread_sensitive=True)(*args, **kwargs)

    async def execute(name, value, **kwargs):
        if name == "setup_experiment_activity":
            assert value.admission_only
            ready = await call(native_admission_ready, value.experiment_id, value.native_runs)
            acknowledgements.append(ready)
            return {"status": "READY" if ready else "ADMISSION_PENDING"}
        if name == "mark_experiment_running_activity":
            assert acknowledgements == [True]
            return await call(activities._mark_experiment_running_sync, value.experiment_id)
        if name == "setup_prompt_v2_activity":
            return await call(activities._setup_prompt_v2_sync, value.experiment_id,
                              value.dataset_id, value.experiment_prompt_config_id,
                              rerun_row_ids=value.rerun_row_ids, failed_only=value.failed_only,
                              native_context=value.native_context)
        if name == "process_row_activity":
            scheduled.append((value.native_context["config_id"], value.row_id))
            return await call(activities._process_row_sync, value.row_id, value.column_id,
                              value.dataset_id, value.experiment_id, value.messages, value.model,
                              value.model_config, value.output_format, value.run_prompt_config,
                              native_context=value.native_context)
        if name == "check_column_status_activity":
            return await call(activities._check_column_status_sync, value.entity_id)
        if name == "check_experiment_dataset_status_activity":
            return await call(activities._check_experiment_dataset_status_sync,
                              value.entity_id, native_context=value.native_context)
        if name == "check_experiment_status_activity":
            return await call(activities._check_experiment_status_sync, value.entity_id)
        raise AssertionError("Unexpected activity: " + name)

    async def child(_function, value, **kwargs):
        children.append(value)
        assert state["admission_id"] in kwargs["id"]
        return asyncio.create_task(ProcessPromptV2Workflow().run(value))

    value = RerunCellsV2WorkflowInput(experiment_id=str(experiment.id), **state["workflow_args"])
    with patch(PROVIDER, autospec=True, return_value=("executed-subset", {})) as provider, patch(
        "tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=execute,
    ), patch("tfc.temporal.experiments.workflows.workflow.start_child_workflow", side_effect=child), patch(
        "tfc.temporal.experiments.run_control.workflow.patched", return_value=False,
    ):
        result = asyncio.run(RerunCellsV2Workflow().run(value))
    plan = state["execution_plan"]
    expected = {(key, row_id) for key in contexts for row_id in plan["targets"][key]["snapshot_row_ids"]}
    assert set(scheduled) == expected and len(scheduled) == len(expected) == provider.call_count
    assert result.status == StatusType.COMPLETED.value and len(children) == len(contexts)
    for child_input in children:
        context = contexts[child_input.experiment_prompt_config_id]
        assert child_input.native_context == context
        # Rows were resolved at admission; the child setup activity reads its
        # persisted plan, so no row list or failed-only scan crosses the workflow.
        assert child_input.rerun_row_ids == [] and child_input.failed_only is False
        run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
        assert run.status == "completed"
        assert run.items.count() == len(plan["targets"][child_input.experiment_prompt_config_id]["snapshot_row_ids"])


@pytest.mark.parametrize("terminal", [False, True])
def test_subset_client_recovers_only_matching_live_admission(terminal):
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    remote = MagicMock()
    description = MagicMock(status=WorkflowExecutionStatus.COMPLETED if terminal else WorkflowExecutionStatus.RUNNING)
    description.memo = AsyncMock(return_value={"experiment_submission_id": "plan", "execution_plan_hash": "hash"})
    remote.get_workflow_handle.return_value.describe = AsyncMock(return_value=description)
    remote.start_workflow = AsyncMock()
    value = RerunCellsV2WorkflowInput(experiment_id="experiment", dataset_id="snapshot",
        prompt_config_ids=["config"], native_runs={"config": {"plan_id": "plan", "execution_id": "plan", "plan_hash": "hash"}})
    with patch.object(client, "get_client", new_callable=AsyncMock, return_value=remote):
        if terminal:
            with pytest.raises(ValueError, match="live matching"):
                asyncio.run(client._admit_subset_workflow_async(value, "plan", "tasks_l"))
        else:
            result = asyncio.run(client._admit_subset_workflow_async(value, "plan", "tasks_l"))
            assert result == "rerun-experiment-cells-experiment-plan"
    remote.start_workflow.assert_not_called()


def test_subset_parent_waits_for_ack_and_preserves_plan_across_continue_as_new():
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow
    class Continued(BaseException):
        pass
    context = {"plan_id": "plan", "execution_id": "plan", "plan_hash": "hash"}
    value = RerunCellsV2WorkflowInput(experiment_id="experiment", dataset_id="snapshot",
        prompt_config_ids=["config"], native_runs={"config": context})
    polls = []
    async def execute(name, received, **kwargs):
        assert name == "setup_experiment_activity" and received.admission_only
        polls.append(received)
        return {"status": "ADMISSION_PENDING"}
    def continued(received, **kwargs):
        assert received is value
        assert kwargs == {"memo": {"experiment_submission_id": "plan", "execution_plan_hash": "hash"}}
        raise Continued()
    with patch("tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=execute), patch(
        "tfc.temporal.experiments.workflows.workflow.sleep", new_callable=AsyncMock,
    ), patch("tfc.temporal.experiments.workflows.workflow.continue_as_new", side_effect=continued), patch(
        "tfc.temporal.experiments.workflows.workflow.start_child_workflow", new_callable=AsyncMock,
    ) as child:
        with pytest.raises(Continued):
            asyncio.run(RerunCellsV2Workflow().run(value))
    assert len(polls) == 120
    child.assert_not_called()


def _subset_value(plan_hash="hash"):
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    return RerunCellsV2WorkflowInput(
        experiment_id="experiment", dataset_id="snapshot", prompt_config_ids=["config"],
        native_runs={"config": {"plan_id": "plan", "execution_id": "plan", "plan_hash": plan_hash}},
    )


def test_subset_client_starts_once_with_plan_identity_when_absent():
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
    from temporalio.service import RPCError, RPCStatusCode
    remote = MagicMock()
    live = MagicMock(status=WorkflowExecutionStatus.RUNNING)
    live.memo = AsyncMock(return_value={"experiment_submission_id": "plan", "execution_plan_hash": "hash"})
    remote.get_workflow_handle.return_value.describe = AsyncMock(
        side_effect=[RPCError("not found", RPCStatusCode.NOT_FOUND, b""), live])
    remote.start_workflow = AsyncMock()
    with patch.object(client, "get_client", new_callable=AsyncMock, return_value=remote):
        result = asyncio.run(client._admit_subset_workflow_async(_subset_value(), "plan", "tasks_l"))
    assert result == "rerun-experiment-cells-experiment-plan"
    remote.start_workflow.assert_awaited_once()
    kwargs = remote.start_workflow.await_args.kwargs
    assert kwargs["id"] == result and kwargs["task_queue"] == "tasks_l"
    assert kwargs["memo"] == {"experiment_submission_id": "plan", "execution_plan_hash": "hash"}
    assert kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
    assert kwargs["id_conflict_policy"] == WorkflowIDConflictPolicy.FAIL


@pytest.mark.parametrize("memo", [
    {"experiment_submission_id": "other", "execution_plan_hash": "hash"},
    {"experiment_submission_id": "plan", "execution_plan_hash": "other"},
    {},
])
def test_subset_client_refuses_live_workflow_with_foreign_plan_identity(memo):
    remote = MagicMock()
    description = MagicMock(status=WorkflowExecutionStatus.RUNNING)
    description.memo = AsyncMock(return_value=memo)
    remote.get_workflow_handle.return_value.describe = AsyncMock(return_value=description)
    remote.start_workflow = AsyncMock()
    with patch.object(client, "get_client", new_callable=AsyncMock, return_value=remote):
        with pytest.raises(ValueError, match="live matching"):
            asyncio.run(client._admit_subset_workflow_async(_subset_value(), "plan", "tasks_l"))
    remote.start_workflow.assert_not_called()


def test_subset_client_requires_one_committed_plan_identity():
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    value = RerunCellsV2WorkflowInput(experiment_id="experiment", dataset_id="snapshot",
        prompt_config_ids=["a", "b"], native_runs={
            "a": {"plan_id": "plan", "plan_hash": "one"}, "b": {"plan_id": "plan", "plan_hash": "two"}})
    with pytest.raises(ValueError, match="one committed plan"):
        asyncio.run(client._admit_subset_workflow_async(value, "plan", "tasks_l"))
    with pytest.raises(ValueError, match="requires a committed admission"):
        client.start_rerun_cells_v2_workflow("experiment", "snapshot", native_runs=value.native_runs)


def test_subset_parent_never_reports_success_when_a_native_child_fails():
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow
    value = _subset_value()
    names = []

    async def execute(name, received, **kwargs):
        names.append(name)
        if name == "cleanup_running_cells_activity":
            assert received.native_runs == value.native_runs
        return {"status": "READY"}

    async def child(_function, received, **kwargs):
        assert received.native_context == value.native_runs["config"]
        assert received.rerun_row_ids == [] and received.failed_only is False

        async def failing():
            raise RuntimeError("child failed")

        return asyncio.ensure_future(failing())

    with patch("tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=execute), patch(
        "tfc.temporal.experiments.workflows.workflow.start_child_workflow", side_effect=child,
    ):
        with pytest.raises(RuntimeError, match="Native prompt evidence did not finish"):
            asyncio.run(RerunCellsV2Workflow().run(value))
    assert "cleanup_running_cells_activity" in names and "mark_experiment_failed_activity" not in names
    assert "check_experiment_status_activity" not in names


@pytest.mark.parametrize("parent_kind", ["whole", "subset"])
def test_cancelled_pending_parent_exits_without_any_cleanup_or_child(parent_kind):
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow
    value = _subset_value()
    parent = RerunCellsV2Workflow()
    if parent_kind == "whole":
        value = RunExperimentInput(experiment_id=value.experiment_id, native_runs=value.native_runs)
        parent = RunExperimentV2Workflow()
    calls = []
    async def execute(name, received, **kwargs):
        calls.append(name)
        assert name == "setup_experiment_activity" and received.admission_only
        return {"status": "ADMISSION_CANCELLED"}
    with patch("tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=execute), patch(
        "tfc.temporal.experiments.workflows.workflow.start_child_workflow", new_callable=AsyncMock,
    ) as child:
        result = asyncio.run(parent.run(value))
    assert result.status == "CANCELLED" and result.total_rows_processed == 0
    assert calls == ["setup_experiment_activity"]
    child.assert_not_called()


def test_cancelled_admission_activity_returns_terminal_handshake_not_failed_setup():
    from model_hub.services.experiment_submit import SubmissionCancelled
    from tfc.temporal.experiments import activities
    from tfc.temporal.experiments.types import SetupExperimentInput
    value = _subset_value()
    with patch("model_hub.services.experiment_submit.native_admission_ready",
               side_effect=SubmissionCancelled("stopped")):
        result = asyncio.run(activities.setup_experiment_activity(SetupExperimentInput(
            experiment_id=value.experiment_id, native_runs=value.native_runs, admission_only=True)))
    assert result.status == "ADMISSION_CANCELLED"


@pytest.mark.parametrize("parent_kind", ["whole", "subset"])
def test_eval_only_plan_reaches_native_child_and_never_classic_per_edt_eval(parent_kind):
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow
    context = {"plan_id": "plan", "execution_id": "plan", "plan_hash": "hash", "run_id": "run",
               "config_id": "config", "execution_mode": "eval_only", "output_column_id": "output"}
    contexts = {"config": context}
    calls, children = [], []
    async def execute(name, value, **kwargs):
        calls.append((name, value))
        if name in {"setup_experiment_activity", "mark_experiment_running_activity"}:
            return {"status": "READY", "prompt_configs": ["config"], "agent_configs": [],
                    "eval_template_ids": ["metric"], "dataset_id": "snapshot", "has_base_column": False}
        if name == "analyze_dependencies_activity":
            return {"dependent_eval_ids": ["metric"], "independent_eval_ids": []}
        if name == "resolve_edt_columns_activity":
            return {"edt_column_ids": ["output"], "row_ids": ["row"]}
        assert name != "process_batch_template_eval_activity", "native eval executed twice"
        return {"status": "COMPLETED"}
    async def child(_function, value, **kwargs):
        children.append(value)
        async def complete():
            return {"status": "COMPLETED", "completed_rows": 1, "failed_rows": 0}
        return asyncio.create_task(complete())
    if parent_kind == "whole":
        value = RunExperimentInput(experiment_id="experiment", native_runs=contexts,
            rerun_eval_template_ids=["metric"], rerun_prompt_config_ids=[])
        parent = RunExperimentV2Workflow()
    else:
        value = RerunCellsV2WorkflowInput(experiment_id="experiment", dataset_id="snapshot",
            native_runs=contexts, prompt_config_ids=[], eval_template_ids=["metric"], eval_only=True)
        parent = RerunCellsV2Workflow()
    with patch("tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=execute), patch(
        "tfc.temporal.experiments.workflows.workflow.start_child_workflow", side_effect=child,
    ):
        result = asyncio.run(parent.run(value))
    assert result.status == "COMPLETED"
    assert len(children) == 1 and children[0].native_context == context
    assert children[0].experiment_prompt_config_id == "config"


def test_native_evaluator_child_does_not_start_unreceipted_eval_activities():
    from tfc.temporal.experiments.types import ProcessPromptV2WorkflowInput
    from tfc.temporal.experiments.workflows import ProcessPromptV2Workflow
    context = {"run_id": "run", "execution_mode": "prompt"}
    calls = []
    async def execute(name, value, **kwargs):
        calls.append((name, value))
        if name == "setup_prompt_v2_activity":
            return {"status": "READY", "row_ids": ["row"], "column_id": "output",
                    "experiment_dataset_id": "variant", "messages": [], "model_name": "model"}
        if name == "process_row_activity":
            assert value.native_context == context
        return {"status": "COMPLETED"}
    value = ProcessPromptV2WorkflowInput(experiment_id="experiment", dataset_id="snapshot",
        experiment_prompt_config_id="config", native_context=context, eval_template_ids=["metric"])
    with patch("tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=execute), patch(
        "tfc.temporal.experiments.workflows.workflow.start_activity",
    ) as background, patch("tfc.temporal.experiments.run_control.workflow.patched", return_value=False):
        result = asyncio.run(ProcessPromptV2Workflow().run(value))
    assert result.status == "COMPLETED"
    background.assert_not_called()
    assert [name for name, _ in calls].count("process_row_activity") == 1
    assert calls[-1][0] == "check_experiment_dataset_status_activity"
    assert calls[-1][1].native_context == context


# r7 COR-001: real HTTP selector/admission to the real parent, fake transport only.
@pytest.mark.parametrize("mixed", [False, True], ids=["base-only", "disjoint-base-native"])
@pytest.mark.parametrize("tag_column", [False, True], ids=["evaluation", "evaluation-tags"])
def test_bulk_failed_eval_request_preserves_base_rows_through_parent(tenants, mixed, tag_column):
    from dataclasses import fields
    from model_hub.models.choices import CellStatus, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Row
    from model_hub.models.native_experiment_runs import NativeExperimentSnapshot, NativeRunBinding
    from model_hub.tests.test_native_prompt_create_bridge import (
        seed_native_admission, complete_current, attach_executable_native_eval, subset_post,
    )
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow
    actor = tenants["a"]
    experiment, _ = seed_native_admission(actor)
    complete_current(experiment)
    parent = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    snapshot = NativeExperimentSnapshot.objects.get(experiment=experiment)
    source = Column.no_workspace_objects.get(pk=next(iter(snapshot.column_map)))
    # A real base output target exists in addition to the native prompt output.
    experiment.column = source
    experiment.save(update_fields=["column"])
    metric, _ = attach_executable_native_eval({"dataset": experiment.dataset,
        "snapshot": experiment.snapshot_dataset, "experiment": experiment,
        "created_by": actor["user"], "source_column": source})
    rows = [str(value) for value in Row.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset, deleted=False).order_by("order", "id").values_list("id", flat=True)]
    assert len(rows) == 2
    native_column = Column.no_workspace_objects.create(dataset=experiment.snapshot_dataset,
        name="native-eval", data_type="float", source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"{parent.variant_id}-{parent.output_column_id}-sourceid-{metric.id}")
    parent.variant.columns.add(native_column)
    base_column = Column.no_workspace_objects.create(dataset=experiment.snapshot_dataset,
        name="base-eval", data_type="float", source_id=str(metric.id),
        source=SourceChoices.EVALUATION_TAGS.value if tag_column else SourceChoices.EVALUATION.value)
    for index, row_id in enumerate(rows):
        Cell.no_workspace_objects.create(dataset=experiment.snapshot_dataset, row_id=row_id,
            column=native_column, value="native", status=CellStatus.ERROR.value if mixed and index == 0 else CellStatus.PASS.value)
        Cell.no_workspace_objects.create(dataset=experiment.snapshot_dataset, row_id=row_id,
            column=base_column, value="base", status=CellStatus.ERROR.value if index == 1 else CellStatus.PASS.value)
    previous = list(parent.run.items.order_by("id").values())
    reply, starter = subset_post(actor, experiment, {"user_eval_metric_ids": [str(metric.id)], "failed_only": True},
                                 "bulk-base-failures")
    assert reply.status_code == 200, reply.content
    args = starter.call_args.kwargs
    # Preserve the classic union-of-failed-rows contract for base activities;
    # canonical native selection stays the strict per-target subset below.
    expected = set(rows if mixed else [rows[1]])
    assert set(args["row_ids"]) == expected
    contexts = args.get("native_runs", {})
    if mixed:
        target = state_of(experiment)["execution_plan"]["targets"][str(parent.native_config_id)]
        assert target["snapshot_row_ids"] == [rows[0]]
        assert set(target["evaluation_sources"]) == {rows[0]}
        assert len(contexts) == 1
    else:
        assert not contexts
        assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 1
    assert previous == list(parent.run.items.order_by("id").values())
    value = RerunCellsV2WorkflowInput(**{key: val for key, val in args.items()
        if key in {field.name for field in fields(RerunCellsV2WorkflowInput)}})
    base_calls, children = [], []
    async def execute(name, value, **kwargs):
        if name in {"setup_experiment_activity", "mark_experiment_running_activity"}:
            return {"status": "READY"}
        if name == "analyze_dependencies_activity":
            return {"dependent_eval_ids": [str(metric.id)], "independent_eval_ids": []}
        if name == "resolve_edt_columns_activity":
            return {"edt_column_ids": [str(parent.output_column_id)], "row_ids": rows}
        if name == "run_base_evaluation_activity":
            base_calls.append(value)
        if mixed:
            assert name != "process_batch_template_eval_activity", "native evaluator executed twice"
        return {"status": "COMPLETED"}
    async def child(_function, value, **kwargs):
        children.append(value)
        async def complete():
            return {"status": "COMPLETED", "completed_rows": 1, "failed_rows": 0}
        return asyncio.create_task(complete())
    with patch("tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=execute), patch(
        "tfc.temporal.experiments.workflows.workflow.start_child_workflow", side_effect=child,
    ):
        result = asyncio.run(RerunCellsV2Workflow().run(value))
    assert result.status == "COMPLETED"
    assert len(base_calls) == 1 and set(base_calls[0].row_ids) == expected
    assert base_calls[0].eval_template_id == str(metric.id)
    assert len(children) == int(mixed)
    if mixed:
        assert children[0].native_context == contexts[str(parent.native_config_id)]


# r8 graph tests-first checkpoint. Production graph integration remains pending.
def test_r8_agent_transport_dataclasses_preserve_native_context():
    from dataclasses import fields
    from tfc.temporal.experiments.types import (
        ProcessAgentWorkflowInput, SetupAgentInput, PrepareAgentRowInput,
    )
    for cls in (ProcessAgentWorkflowInput, SetupAgentInput, PrepareAgentRowInput):
        names = {field.name for field in fields(cls)}
        assert "native_context" in names, f"{cls.__name__} loses the admitted graph identity"


@pytest.mark.parametrize("parent_kind", ["whole", "subset"])
def test_r8_parent_forwards_exact_graph_context(parent_kind):
    """Transport-only control: no database, authorization or recording mock."""
    import uuid
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow
    experiment_id, dataset_id, config_id, run_id, plan_id = [str(uuid.uuid4()) for _ in range(5)]
    context = {"run_id": run_id, "execution_id": plan_id, "plan_id": plan_id,
               "config_id": config_id, "plan_hash": "f" * 64, "execution_mode": "graph", "row_count": 1}
    children = []

    async def execute(name, value, **kwargs):
        if name in {"setup_experiment_activity", "mark_experiment_running_activity"}:
            return {"status": "READY", "dataset_id": dataset_id, "prompt_configs": [],
                    "agent_configs": [config_id], "eval_template_ids": [], "has_base_column": False}
        return {"status": "COMPLETED"}

    async def start_child(function, value, **kwargs):
        children.append(value)
        async def complete():
            return {"status": "COMPLETED", "completed_rows": 1, "failed_rows": 0}
        return asyncio.create_task(complete())

    if parent_kind == "whole":
        input = RunExperimentInput(experiment_id=experiment_id, native_runs={config_id: context})
        parent = RunExperimentV2Workflow()
    else:
        input = RerunCellsV2WorkflowInput(experiment_id=experiment_id, dataset_id=dataset_id,
            agent_config_ids=[config_id], native_runs={config_id: context})
        parent = RerunCellsV2Workflow()
    with patch("tfc.temporal.experiments.workflows.workflow.execute_activity", side_effect=execute), patch(
        "tfc.temporal.experiments.workflows.workflow.start_child_workflow", side_effect=start_child,
    ):
        result = asyncio.run(parent.run(input))
    assert result.status == "COMPLETED"
    assert len(children) == 1
    assert getattr(children[0], "native_context", None) == context


def r8_drive_graph(world, *, context=None, refusal=None):
    """Run the real agent/graph workflows and actual registered activities.

    Only Temporal scheduling/heartbeats, evaluator engine/billing transport and
    the trace exporter are in-process. Graph topology, deterministic node runner,
    dataset scoping, admissions, native binding, writes and finalizers are real.
    This is regression SOURCE, never an executed workflow/Temporal receipt.
    """
    from contextlib import ExitStack, asynccontextmanager
    from dataclasses import fields
    from types import SimpleNamespace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from tfc.temporal.experiments import activities as experiment_activities
    from tfc.temporal.agent_playground import activities as graph_activities
    from tfc.temporal.agent_playground.workflows import GraphExecutionWorkflow
    from tfc.temporal.experiments.types import ProcessAgentWorkflowInput
    from tfc.temporal.experiments.workflows import ProcessAgentWorkflow
    from model_hub.tests.test_native_prompt_create_bridge import (
        r8_graph_context, native_eval_transport, native_preflight,
    )
    if context is None:
        context, _, _ = r8_graph_context(world)
    assert "native_context" in {field.name for field in fields(ProcessAgentWorkflowInput)}
    experiment = world["experiment"]
    input = ProcessAgentWorkflowInput(experiment_id=str(experiment.id),
        dataset_id=str(experiment.snapshot_dataset_id), experiment_agent_config_id=str(world["config"].id),
        native_context=context)
    children, calls = [], []
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("r8.graph.regression")

    @asynccontextmanager
    async def heartbeat():
        yield

    async def execute(name, value, **kwargs):
        calls.append(name)
        if refusal == "prepare_failed" and name == "prepare_agent_row_activity":
            # A failed activity response is a transport fixture, not a fake
            # canonical claim/result or a bypass of target authorization.
            return {"row_id": value.row_id, "status": "FAILED", "graph_execution_id": "",
                    "input_payload": {}, "error": "Graph preparation refused"}
        handler = getattr(experiment_activities, name, None) or getattr(graph_activities, name, None)
        assert handler is not None, f"unregistered graph bridge activity: {name}"
        return await handler(value)

    async def execute_child(name, value, **kwargs):
        assert name == "GraphExecutionWorkflow"
        children.append(value)
        if refusal == "child_transport_failed":
            raise RuntimeError("graph child transport failed")
        return await GraphExecutionWorkflow().run(value)

    def start_activity(name, value, **kwargs):
        return asyncio.create_task(execute(name, value, **kwargs))

    checker = MagicMock(return_value=SimpleNamespace(allowed=refusal != "evaluator_quota"))
    try:
        with ExitStack() as stack:
            engine, billing = stack.enter_context(native_eval_transport(score=0))
            stack.enter_context(native_preflight(checker))
            stack.enter_context(patch.object(experiment_activities, "Heartbeater", heartbeat))
            stack.enter_context(patch.object(graph_activities, "Heartbeater", heartbeat))
            stack.enter_context(patch("temporalio.workflow.execute_activity", side_effect=execute))
            stack.enter_context(patch("temporalio.workflow.start_activity", side_effect=start_activity))
            stack.enter_context(patch("temporalio.workflow.execute_child_workflow", side_effect=execute_child))
            stack.enter_context(patch("tfc.temporal.experiments.run_control.workflow.patched", return_value=False))
            stack.enter_context(patch("opentelemetry.trace.get_tracer", return_value=tracer))
            result = asyncio.run(ProcessAgentWorkflow().run(input))
        spans = exporter.get_finished_spans()
        return result, children, calls, spans, engine, billing
    finally:
        provider.shutdown()


def r8_finish_graph_parent(world):
    """Run the real parent status phase for a settled graph-only fixture.

    r8_drive_graph intentionally runs only the child, including when imported
    by other suites. A sealed child/variant does not make its parent terminal:
    the parent waits for all children and base evaluations before this phase.
    """
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from tfc.temporal.experiments import activities

    experiment = world["experiment"]
    experiment.refresh_from_db()
    assert experiment.column_id is None, "base evaluations must finish before the parent"
    state = state_of(experiment)
    assert state["state"] == "accepted"
    contexts = state["workflow_args"]["native_runs"]
    assert set(contexts) == {str(world["config"].id)}, "finish every admitted child first"
    run = NativeRunBinding.objects.get(run_id=contexts[str(world["config"].id)]["run_id"]).run
    assert run.status == "completed" and run.completed_at is not None
    result = activities._check_experiment_status_sync(str(experiment.id))
    assert result["is_complete"] and result["status"] == StatusType.COMPLETED.value, result
    experiment.refresh_from_db()
    assert experiment.status == StatusType.COMPLETED.value


@pytest.mark.parametrize("string_ids", [False, True], ids=["uuid", "string"])
def test_r8_bulk_cell_reset_reuses_row_identity_and_preserves_other_cells(tenants, string_ids):
    """Native plans carry string row IDs; ORM cells carry UUID row IDs."""
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Cell, Column, Row
    from model_hub.views.eval_runner import bulk_update_or_create_cells

    dataset = tenants["a"]["exp"]["dataset"]
    column = Column.no_workspace_objects.get(dataset=dataset, deleted=False)
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    untouched = Cell.no_workspace_objects.filter(dataset=dataset).exclude(row=row).order_by("id")
    untouched_before = list(untouched.values())
    assert untouched_before
    row_ids = [str(row.id) if string_ids else row.id]
    values = {"value": "", "value_infos": "{}", "status": CellStatus.RUNNING.value}
    args = (row_ids, str(column.id), str(dataset.id), values)
    selected = Cell.no_workspace_objects.filter(dataset=dataset, column=column, row=row)

    assert bulk_update_or_create_cells(*args) == (0, 1)
    cell = selected.get()
    selected.update(value="previous execution", status=CellStatus.PASS.value)
    # A new execution resets the existing cell, not a second physical cell.
    assert bulk_update_or_create_cells(*args) == (1, 0)
    assert selected.get().pk == cell.pk
    cell.refresh_from_db()
    assert cell.value == "" and cell.status == CellStatus.RUNNING.value
    assert list(untouched.values()) == untouched_before

    # The same identity normalization must preserve the skip-completed contract.
    selected.update(value="settled execution", status=CellStatus.PASS.value)
    settled = list(selected.values())
    assert bulk_update_or_create_cells(*args, skip_completed=True) == (0, 0)
    assert list(selected.values()) == settled
    assert list(untouched.values()) == untouched_before


@pytest.mark.django_db(transaction=True)
def test_r8_graph_executes_pinned_inputs_records_nodes_and_actual_root(tenants):
    from model_hub.models.develop_dataset import Cell, Column
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.tests.test_native_prompt_create_bridge import r8_create_graph, r8_graph_context
    world = r8_create_graph(tenants["a"])
    context, binding, target = r8_graph_context(world)
    # Mutation after admission must not affect graph input interpretation.
    ids = list(binding.snapshot.column_map.values())
    Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset, column_id__in=ids).update(value="MUTATED")
    Column.no_workspace_objects.filter(id__in=ids).update(name="renamed-live-input")
    result, children, calls, spans, engine, _ = r8_drive_graph(world)
    assert result.status == "COMPLETED"
    assert len(children) == len(target["snapshot_row_ids"])
    assert all(child.input_payload == {"query": "pinned graph input"} for child in children)
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert run.status == "completed" and run.completed_at is not None
    assert run.items.count() == len(target["example_ids"])
    assert engine.run.call_count == run.items.count()
    for item in run.items.all():
        assert item.output == {"response": "native:PINNED GRAPH INPUT"}
        assert item.status == "completed" and item.trial_number == 1
        assert {ref["evaluator_version_id"] for ref in item.score_refs} == {str(world["pin"].id)}
        assert all(ref["status"] == "assessed" and ref["value"] == 0 for ref in item.score_refs)
        # Proposed private receipt contract; implementation is still pending.
        receipt = item.metadata["native_graph"]
        assert receipt["plan_id"] == context["plan_id"]
        assert receipt["run_id"] == str(run.id)
        assert receipt["example_id"] == str(item.example_id) and receipt["trial_number"] == 1
        node = receipt["node_results"][str(world["node"].id)]
        assert node["status"] == "SUCCESS"
        assert node["outputs"] == {"response": "native:PINNED GRAPH INPUT"}
        assert receipt["graph_execution_id"] in {child.graph_execution_id for child in children}
        roots = [span for span in spans if span.parent is None
                 and span.attributes.get("fi.experiment.run_id") == str(run.id)
                 and span.attributes.get("fi.experiment.example_id") == str(item.example_id)]
        assert len(roots) == 1, "actual execution root must carry canonical identity"
        root = roots[0]
        assert root.attributes["fi.experiment.trial_number"] == 1
        assert root.attributes["fi.experiment.execution_id"] == context["execution_id"]
        assert item.trace_id == format(root.context.trace_id, "032x")
        assert item.root_span_id == format(root.context.span_id, "016x")
    assert "process_batch_template_eval_activity" not in calls, "native evaluator outcomes must seal with the output"


@pytest.mark.django_db(transaction=True)
def test_r8_graph_terminal_replay_never_reexecutes_child_or_changes_receipt(tenants):
    from agent_playground.models import GraphExecution
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.tests.test_native_prompt_create_bridge import r8_create_graph, r8_graph_context
    world = r8_create_graph(tenants["a"])
    context, _, _ = r8_graph_context(world)
    first, children, _, _, _, _ = r8_drive_graph(world)
    assert first.status == "COMPLETED" and children
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    items = list(run.items.order_by("id").values())
    cells = list(Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset).order_by("id").values())
    graph_count = GraphExecution.no_workspace_objects.count()
    replay, children, _, _, engine, billing = r8_drive_graph(world)
    assert replay.status == "COMPLETED" and children == []
    engine.run.assert_not_called(); billing.assert_not_called()
    assert GraphExecution.no_workspace_objects.count() == graph_count
    assert list(run.items.order_by("id").values()) == items
    assert list(Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset).order_by("id").values()) == cells


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("refusal", ["prepare_failed", "child_transport_failed", "evaluator_quota"])
def test_r8_graph_failures_and_refusals_settle_owned_cells_before_seal(tenants, refusal):
    from model_hub.models.choices import CellStatus, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.tests.test_native_prompt_create_bridge import r8_create_graph, r8_graph_context
    world = r8_create_graph(tenants["a"])
    context, _, target = r8_graph_context(world)
    _, children, _, _, engine, billing = r8_drive_graph(world, refusal=refusal)
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert run.completed_at is not None
    assert run.items.count() == len(target["example_ids"])
    assert run.status == ("completed" if refusal == "evaluator_quota" else "failed")
    columns = Column.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset,
        deleted=False, source__in=[SourceChoices.EXPERIMENT.value, SourceChoices.EXPERIMENT_EVALUATION.value,
                                  SourceChoices.EVALUATION_REASON.value])
    cells = Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset,
        column__in=columns, row_id__in=target["snapshot_row_ids"], deleted=False)
    assert cells.exists()
    assert not cells.filter(status=CellStatus.RUNNING.value).exists()
    # An evaluator failure/refusal must settle the reason as well as result.
    reasons = cells.filter(column__source=SourceChoices.EVALUATION_REASON.value)
    assert reasons.exists() and set(reasons.values_list("status", flat=True)) == {CellStatus.ERROR.value}
    engine.run.assert_not_called(); billing.assert_not_called()
    if refusal == "prepare_failed":
        assert children == []
    for item in run.items.all():
        if refusal == "evaluator_quota":
            assert item.status == "completed" and all(ref["status"] == "error" for ref in item.score_refs)
        else:
            assert item.status == "failed" and item.error


@pytest.mark.django_db(transaction=True)
def test_r8_graph_whole_rerun_terminal_idempotency_key_replays_exact_plan(tenants):
    from django.urls import reverse
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.tests.test_native_prompt_create_bridge import r8_create_graph, r8_graph_context, START
    world = r8_create_graph(tenants["a"])
    first_context, _, _ = r8_graph_context(world)
    first, _, _, _, _, _ = r8_drive_graph(world)
    assert first.status == "COMPLETED"
    cells = Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset).order_by("id")
    cell_ids = list(cells.values_list("id", flat=True))
    body = {"experiment_ids": [str(world["experiment"].id)]}
    # Child completion must not bypass the still-active parent's admission guard.
    before = state_of(world["experiment"])
    with patch(START) as blocked:
        active = world["actor"]["client"].post(reverse("experiments-v2-rerun"), body,
            format="json", HTTP_IDEMPOTENCY_KEY="r8-graph-rerun")
    assert active.status_code == 400, active.content
    assert active.json()["detail"] == "Retry the original active batch before starting another execution."
    blocked.assert_not_called()
    assert state_of(world["experiment"]) == before
    r8_finish_graph_parent(world)
    with patch(START, return_value="r8-rerun-admitted") as start:
        reply = world["actor"]["client"].post(reverse("experiments-v2-rerun"), body,
            format="json", HTTP_IDEMPOTENCY_KEY="r8-graph-rerun")
    assert reply.status_code == 200, reply.content
    context, binding, _ = r8_graph_context(world)
    assert context["run_id"] != first_context["run_id"]
    assert state_of(world["experiment"])["execution_plan"]["execution_key"] == "r8-graph-rerun"
    assert start.call_count == 1
    result, children, calls, _, engine, _ = r8_drive_graph(world)
    assert result.status == "COMPLETED" and len(children) == first.total_rows
    assert engine.run.call_count == first.total_rows
    assert "create_error_agent_cells_activity" not in calls
    r8_finish_graph_parent(world)
    assert list(cells.values_list("id", flat=True)) == cell_ids
    items = list(binding.run.items.order_by("id").values())
    state = state_of(world["experiment"])
    count = NativeRunBinding.objects.filter(snapshot__experiment=world["experiment"]).count()
    with patch(START) as repeated:
        reply = world["actor"]["client"].post(reverse("experiments-v2-rerun"), body,
            format="json", HTTP_IDEMPOTENCY_KEY="r8-graph-rerun")
    assert reply.status_code == 200, reply.content
    repeated.assert_not_called()
    assert state_of(world["experiment"]) == state
    assert NativeRunBinding.objects.filter(snapshot__experiment=world["experiment"]).count() == count
    assert list(binding.run.items.order_by("id").values()) == items


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("failed_only", [False, True])
def test_r8_graph_subset_pins_one_row_and_terminal_key_preserves_other_cells(tenants, failed_only):
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Cell, Column, Row
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.tests.test_native_prompt_create_bridge import (
        r8_create_graph, r8_graph_context, subset_post,
    )
    actor = tenants["a"]
    source = actor["exp"]["dataset"]
    source_column = Column.no_workspace_objects.get(dataset=source, deleted=False)
    extra = Row.no_workspace_objects.create(dataset=source, order=1)
    Cell.no_workspace_objects.create(dataset=source, column=source_column, row=extra, value="second")
    world = r8_create_graph(actor)
    first_context, binding, target = r8_graph_context(world)
    assert len(target["snapshot_row_ids"]) == 2
    first, _, _, _, _, _ = r8_drive_graph(world)
    assert first.status == "COMPLETED"
    old_items = list(binding.run.items.order_by("id").values())
    cells = Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset).order_by("id")
    cell_ids = list(cells.values_list("id", flat=True))
    row_id, other_row = target["snapshot_row_ids"]
    column_id = str(binding.output_column_id)
    if failed_only:
        Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset,
            column_id=column_id, row_id=row_id).update(status=CellStatus.ERROR.value)
    untouched = Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset,
                                                row_id=other_row).order_by("id")
    untouched_before = list(untouched.values())
    body = {"cells": [{"column_id": column_id, "row_id": row_id}], "failed_only": failed_only}
    before = state_of(world["experiment"])
    active, blocked = subset_post(actor, world["experiment"], body, key="r8-graph-subset")
    assert active.status_code == 409, active.content
    assert active.json()["code"] == "execution_key_conflict"
    assert active.json()["detail"] == "Retry the active execution before starting another subset."
    blocked.assert_not_called()
    assert state_of(world["experiment"]) == before
    r8_finish_graph_parent(world)
    response, start = subset_post(actor, world["experiment"], body, key="r8-graph-subset")
    assert response.status_code == 200, response.content
    state = state_of(world["experiment"])
    contexts = state.get("workflow_args", {}).get("native_runs", {})
    assert str(world["config"].id) in contexts
    context = contexts[str(world["config"].id)]
    chosen = state["execution_plan"]["targets"][str(world["config"].id)]
    assert chosen["snapshot_row_ids"] == [row_id]
    assert chosen["example_ids"] == [target["example_ids"][target["snapshot_row_ids"].index(row_id)]]
    assert context["run_id"] != first_context["run_id"]
    assert state["execution_plan"]["execution_key"] == "r8-graph-subset"
    assert start.call_args.kwargs["native_runs"] == contexts
    result, children, calls, _, engine, _ = r8_drive_graph(world, context=context)
    assert result.status == "COMPLETED" and len(children) == 1
    assert engine.run.call_count == 1
    assert "create_error_agent_cells_activity" not in calls
    r8_finish_graph_parent(world)
    assert list(cells.values_list("id", flat=True)) == cell_ids
    assert list(untouched.values()) == untouched_before
    assert list(binding.run.items.order_by("id").values()) == old_items
    subset_run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert subset_run.completed_at is not None and subset_run.items.count() == 1
    receipts = list(subset_run.items.values())
    replay, repeated = subset_post(actor, world["experiment"], body, key="r8-graph-subset")
    assert replay.status_code == 200, replay.content
    repeated.assert_not_called()
    assert list(subset_run.items.values()) == receipts
    conflict, dispatched = subset_post(actor, world["experiment"],
        {**body, "cells": [{"column_id": column_id, "row_id": other_row}]}, key="r8-graph-subset")
    assert conflict.status_code == 409, conflict.content
    dispatched.assert_not_called()
    assert list(untouched.values()) == untouched_before


# r11: real LLM rendering, per-terminal evaluator inputs and durable retry evidence.
def r11_llm_graph(actor, *, module=False):
    from agent_playground.models import Graph, GraphVersion, Node, NodeTemplate, Port
    from agent_playground.models.prompt_template_node import PromptTemplateNode
    from model_hub.tests.test_experiment_append_variant import prompt
    graph = Graph.no_workspace_objects.create(name="r11-prompts", organization=actor["org"],
        workspace=actor["workspace"], created_by=actor["user"])
    version = GraphVersion.no_workspace_objects.create(
        graph=graph, version_number=1, status="active", tags=["native-fixture"],
    )
    template, _ = NodeTemplate.no_workspace_objects.get_or_create(name="llm_prompt", defaults={
        "display_name": "LLM Prompt", "description": "Prompt regression", "categories": ["llm"],
        "input_definition": [], "output_definition": [{"key": "response", "data_schema": {}, "schema_source": "prompt_version"}],
        "input_mode": "dynamic", "output_mode": "strict", "config_schema": {},
    })
    nodes = []
    for label in (["child"] if module else ["left", "right"]):
        node = Node.no_workspace_objects.create(graph_version=version, node_template=template,
            type="atomic", name=label, config={})
        Port.no_workspace_objects.create(node=node, key="custom", display_name="query", direction="input",
            data_schema={"type": "string"})
        Port.no_workspace_objects.create(node=node, key="response", display_name=label + "-response", direction="output",
            data_schema={"type": "string"})
        prompt_template, prompt_version = prompt(actor, label)
        prompt_version.prompt_config_snapshot = {
            "messages": [{"role": "system", "content": label + " instructions"},
                         {"role": "user", "content": [{"type": "text", "text": label + " {{query}}"}]}],
            "configuration": {"model": "gpt-4o-mini", "output_format": "string", "max_tokens": 64},
        }
        prompt_version.save(update_fields=["prompt_config_snapshot"])
        PromptTemplateNode.no_workspace_objects.create(node=node, prompt_template=prompt_template,
                                                       prompt_version=prompt_version)
        nodes.append(node)
    version.full_clean()
    if not module:
        return graph, version, nodes[0]
    parent_graph = Graph.no_workspace_objects.create(name="r11-module", organization=actor["org"],
        workspace=actor["workspace"], created_by=actor["user"])
    parent = GraphVersion.no_workspace_objects.create(
        graph=parent_graph, version_number=1, status="active", tags=["native-fixture"],
    )
    node = Node.no_workspace_objects.create(graph_version=parent, type="subgraph", name="Module",
                                           ref_graph_version=version, config={})
    for port in nodes[0].ports.filter(deleted=False):
        Port.no_workspace_objects.create(node=node, key="custom", display_name=port.routing_key,
            direction=port.direction, data_schema=port.data_schema, ref_port=port)
    parent.full_clean()
    return parent_graph, parent, node


def r11_provider_capture(executed):
    from copy import deepcopy
    from model_hub.views.eval_runner import _format_messages_to_prompt_chain
    def respond(provider, *args, **kwargs):
        messages = deepcopy(provider.messages)
        output = "answer for " + _format_messages_to_prompt_chain(messages)
        executed.append((messages, output))
        return output, {}
    return respond


def test_r11_graph_prompt_chain_and_output_match_each_executed_terminal(tenants):
    from unittest.mock import patch
    from model_hub.models.develop_dataset import Cell
    from model_hub.tests.test_native_prompt_create_bridge import r8_create_graph, r8_graph_context
    from model_hub.views.eval_runner import _format_messages_to_prompt_chain
    world = r8_create_graph(tenants["a"], graph_factory=r11_llm_graph, prompt_chain=True)
    context, binding, target = r8_graph_context(world)
    plan = binding.run.metadata["native_identity"]["graph_plan"]
    assert len(plan["terminal_column_ids"]) == 2
    Cell.no_workspace_objects.filter(column_id__in=binding.snapshot.column_map.values()).update(value="MUTABLE DECOY")
    executed = []
    with patch("agent_playground.services.engine.runners.llm_prompt.RunPrompt.litellm_response",
               autospec=True, side_effect=r11_provider_capture(executed)) as provider:
        result, _, _, _, engine, billing = r8_drive_graph(world)
        assert result.status == "COMPLETED"
        assert provider.call_count == engine.run.call_count == billing.call_count == 2 * len(target["example_ids"])
    expected = sorted((_format_messages_to_prompt_chain(messages), output) for messages, output in executed)
    actual = sorted((call.kwargs["input"], call.kwargs["output"]) for call in engine.run.call_args_list)
    assert actual == expected
    assert all("pinned graph input" in prompt and "MUTABLE DECOY" not in prompt for prompt, _ in actual)
    for item in binding.run.items.all():
        for node_id in plan["node_column_mapping"]:
            node = item.metadata["native_graph"]["node_results"][node_id]
            assert (node["prompt_messages"], node["outputs"]["response"]) in executed
        outcomes = item.score_refs[0]["terminal_results"]
        assert {ref["output_column_id"] for ref in outcomes} == set(plan["terminal_column_ids"])
        assert all(ref["status"] == "assessed" and ref["value"] == 0 for ref in outcomes)
    before = list(binding.run.items.order_by("id").values())
    with patch("agent_playground.services.engine.runners.llm_prompt.RunPrompt.litellm_response") as provider:
        replay = r8_drive_graph(world)
        provider.assert_not_called(); replay[4].run.assert_not_called(); replay[5].assert_not_called()
    assert before == list(binding.run.items.order_by("id").values())


def test_r11_completed_nodes_replay_persisted_prompts_after_evaluator_boundary_failure(tenants):
    from copy import deepcopy
    from django.db import DatabaseError
    from types import SimpleNamespace
    from tfc.temporal.experiments import activities
    from model_hub.tests.test_native_prompt_create_bridge import (
        r8_create_graph, r8_graph_context, native_eval_transport, native_preflight,
    )
    from model_hub.views.experiment_runner import _NativeGraphRecorder, _run_native_graph_row
    from model_hub.views.eval_runner import _format_messages_to_prompt_chain
    world = r8_create_graph(tenants["a"], graph_factory=r11_llm_graph, prompt_chain=True)
    context, binding, target = r8_graph_context(world)
    experiment = world["experiment"]
    activities._setup_agent_sync(str(experiment.id), str(experiment.snapshot_dataset_id), str(world["config"].id),
                                 native_context=context)
    row_id = target["snapshot_row_ids"][0]
    prepared = activities._prepare_agent_row_sync(row_id, str(experiment.snapshot_dataset_id), str(experiment.id),
        str(world["version"].id), str(world["config"].id), native_context=context)
    args = (str(experiment.id), str(experiment.snapshot_dataset_id), row_id, context)
    kwargs = {"graph_execution_id": prepared["graph_execution_id"], "graph_version_id": str(world["version"].id),
              "input_payload": prepared["input_payload"]}
    executed = []
    with patch("agent_playground.services.engine.runners.llm_prompt.RunPrompt.litellm_response", autospec=True,
               side_effect=r11_provider_capture(executed)), patch.object(_NativeGraphRecorder, "evaluate",
               side_effect=DatabaseError("evaluation boundary unavailable")):
        with pytest.raises(DatabaseError, match="evaluation boundary"):
            _run_native_graph_row(*args, **kwargs)
    item = binding.run.items.get()
    assert item.status not in {"completed", "failed", "skipped"}
    nodes = deepcopy(item.metadata["native_graph"]["node_results"])
    assert all(node["prompt_messages"] for node in nodes.values())
    with native_eval_transport() as (engine, _), native_preflight(MagicMock(return_value=SimpleNamespace(allowed=True))), patch(
        "agent_playground.services.engine.runners.llm_prompt.RunPrompt.litellm_response",
    ) as provider, patch("agent_playground.services.engine.runners.llm_prompt.LLMPromptRunner._render_messages") as render:
        assert _run_native_graph_row(*args, **kwargs)["status"] == "SUCCESS"
        provider.assert_not_called(); render.assert_not_called()
    item.refresh_from_db()
    assert item.metadata["native_graph"]["node_results"] == nodes
    assert sorted((c.kwargs["input"], c.kwargs["output"]) for c in engine.run.call_args_list) == sorted(
        (_format_messages_to_prompt_chain(messages), output) for messages, output in executed)


@pytest.mark.parametrize("terminal", ["module", "deterministic"])
@pytest.mark.parametrize("prompt_chain", [True, False])
def test_r11_terminal_without_direct_prompt_is_unavailable_only_when_mapping_needs_it(tenants, terminal, prompt_chain):
    from model_hub.tests.test_native_prompt_create_bridge import r8_create_graph, r8_graph_context
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.choices import CellStatus, SourceChoices
    factory = (lambda actor: r11_llm_graph(actor, module=True)) if terminal == "module" else None
    world = r8_create_graph(tenants["a"], graph_factory=factory, prompt_chain=prompt_chain)
    _, binding, target = r8_graph_context(world)
    executed = []
    with patch("agent_playground.services.engine.runners.llm_prompt.RunPrompt.litellm_response", autospec=True,
               side_effect=r11_provider_capture(executed)):
        result, _, _, _, engine, billing = r8_drive_graph(world)
    assert result.status == "COMPLETED"
    if prompt_chain:
        engine.run.assert_not_called(); billing.assert_not_called()
    else:
        assert engine.run.call_count == billing.call_count == len(target["example_ids"])
    for item in binding.run.items.all():
        node = item.metadata["native_graph"]["node_results"][str(world["node"].id)]
        assert "prompt_messages" not in node  # not a fabricated empty prompt
        if terminal == "module":
            children = item.metadata["native_graph"]["child_results"]
            assert any(n.get("prompt_messages") for child in children.values() for n in child["node_results"].values())
        ref = item.score_refs[0]
        if prompt_chain:
            assert ref["status"] == "not_assessed" and ref["reason"] == "prompt_chain_unavailable"
            assert "value" not in ref
        else:
            assert ref["status"] == "assessed" and ref["value"] == 0
    if prompt_chain:
        cells = Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset,
            column__source__in=[SourceChoices.EXPERIMENT_EVALUATION.value, SourceChoices.EVALUATION_REASON.value], deleted=False)
        assert cells.exists() and set(cells.values_list("status", flat=True)) == {CellStatus.ERROR.value}
        assert not cells.filter(status=CellStatus.RUNNING.value).exists()


@pytest.mark.parametrize("bad_identity", ["duplicate_current", "unknown_node", "foreign_node"])
def test_r11_version_scoping_keeps_strict_current_node_identity_validation(tenants, bad_identity):
    import uuid
    from model_hub.models.develop_dataset import Column
    from model_hub.models.choices import SourceChoices
    from model_hub.services.experiment_submit import native_graph_plan
    from model_hub.services.experiment_run_writes import RunWriteError
    from model_hub.tests.test_native_prompt_create_bridge import r8_create_graph, r8_graph_context
    from model_hub.tests.test_experiment_native_graph import make_graph
    world = r8_create_graph(tenants["a"])
    _, binding, _ = r8_graph_context(world)
    node_id = world["node"].id if bad_identity == "duplicate_current" else uuid.uuid4()
    if bad_identity == "foreign_node":
        node_id = make_graph(tenants["b"])[2].id
    column = Column.no_workspace_objects.create(dataset=world["experiment"].snapshot_dataset,
        name="invalid-extra", source=SourceChoices.EXPERIMENT.value, source_id=str(binding.variant_id),
        data_type="text", metadata={"node_id": str(node_id)})
    binding.variant.columns.add(column)
    with pytest.raises(RunWriteError, match="node identities"):
        native_graph_plan(world["config"], binding.snapshot, workspace=tenants["a"]["workspace"])


def test_r11_prompt_checkpoint_failure_precedes_provider_and_canonical_seal(tenants):
    from django.db import DatabaseError
    from tfc.temporal.experiments import activities
    from model_hub.tests.test_native_prompt_create_bridge import r8_create_graph, r8_graph_context
    from model_hub.views.experiment_runner import _NativeGraphRecorder, _run_native_graph_row
    world = r8_create_graph(tenants["a"], graph_factory=r11_llm_graph, prompt_chain=True)
    context, binding, target = r8_graph_context(world)
    experiment = world["experiment"]
    activities._setup_agent_sync(str(experiment.id), str(experiment.snapshot_dataset_id), str(world["config"].id),
                                 native_context=context)
    row_id = target["snapshot_row_ids"][0]
    prepared = activities._prepare_agent_row_sync(row_id, str(experiment.snapshot_dataset_id), str(experiment.id),
        str(world["version"].id), str(world["config"].id), native_context=context)
    with patch.object(_NativeGraphRecorder, "save_prompt_messages", side_effect=DatabaseError("checkpoint failed")), patch(
        "agent_playground.services.engine.runners.llm_prompt.RunPrompt.litellm_response",
    ) as provider:
        with pytest.raises(DatabaseError, match="checkpoint failed"):
            _run_native_graph_row(str(experiment.id), str(experiment.snapshot_dataset_id), row_id, context,
                graph_execution_id=prepared["graph_execution_id"], graph_version_id=str(world["version"].id),
                input_payload=prepared["input_payload"])
        provider.assert_not_called()
    assert not binding.run.items.filter(status__in=["completed", "failed", "skipped"]).exists()
