"""Real worker result failures, including replay of histories without the patch."""

import pytest
from asgiref.sync import sync_to_async
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner

from agent_playground.models.choices import GraphExecutionStatus
from tfc.temporal.agent_playground import activities, workflows
from tfc.temporal.agent_playground.tests.test_e2e_workflow import run_workflow
from tfc.temporal.agent_playground.workflows import GraphExecutionWorkflow

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("failure", ["collect", "finalize"])
@pytest.mark.parametrize("legacy_history", [False, True])
async def test_result_failure_and_old_or_new_history_replay(
    workflow_environment, graph_builder, monkeypatch, failure, legacy_history
):
    graph_builder.add_node("A", inputs=["user_input"], outputs=["response"])
    version = await sync_to_async(graph_builder.build)()

    def failed_collection(graph_execution_id, *args, **kwargs):
        return {
            "graph_execution_id": graph_execution_id,
            "output_payload": {},
            "status": "FAILED",
            "error": "forced collection failure",
        }

    def failed_finalization(graph_execution_id, *args, **kwargs):
        return {
            "graph_execution_id": graph_execution_id,
            "status": "FAILED",
            "error": "forced finalization failure",
        }

    monkeypatch.setattr(
        activities,
        "_collect_outputs_sync"
        if failure == "collect"
        else "_finalize_graph_execution_sync",
        failed_collection if failure == "collect" else failed_finalization,
    )
    with monkeypatch.context() as historical:
        if legacy_history:
            historical.setattr(workflows.workflow, "patched", lambda _: False)
        result, execution = await run_workflow(
            workflow_environment, version, {"user_input": "hello"}
        )
    await sync_to_async(execution.refresh_from_db)()
    assert result.status == ("SUCCESS" if legacy_history else "FAILED")
    if not legacy_history:
        assert (
            result.error
            == f"forced {'collection' if failure == 'collect' else 'finalization'} failure"
        )
    if failure == "collect":
        assert execution.status == (
            GraphExecutionStatus.SUCCESS
            if legacy_history
            else GraphExecutionStatus.FAILED
        )
    else:
        assert execution.status == GraphExecutionStatus.PENDING

    history = await workflow_environment.client.get_workflow_handle(
        f"graph-execution-{execution.id}"
    ).fetch_history()
    markers = [
        event.marker_recorded_event_attributes
        for event in history.events
        if event.HasField("marker_recorded_event_attributes")
    ]
    assert bool(markers) is not legacy_history
    await Replayer(
        workflows=[GraphExecutionWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
