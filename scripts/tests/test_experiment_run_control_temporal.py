"""Real Temporal orchestration; row, graph and eval providers are STANDINs."""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "futureagi"))
from datetime import timedelta
from tfc.temporal.agent_playground.types import ExecuteGraphInput
from tfc.temporal.experiments.types import ProcessAgentWorkflowInput, ProcessPromptV2WorkflowInput
from tfc.temporal.experiments.workflows import ProcessAgentWorkflow, ProcessPromptV2Workflow


@workflow.defn(name="GraphExecutionWorkflow")
class GraphStandin:
    @workflow.run
    async def run(self, data: ExecuteGraphInput) -> dict:
        column_id = data.node_sink_overrides["node"][0].config["column_id"]
        await workflow.execute_activity("process_row_activity",
            {"column_id": column_id, "row_id": data.input_payload["row_id"]},
            start_to_close_timeout=timedelta(seconds=30))
        return {"status": "SUCCESS"}


class StandinActivities:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.eval_entered = asyncio.Event()
        self.eval_release = asyncio.Event()
        self.writes = []
        self.evaluated = []

    @activity.defn(name="bind_experiment_run_activity")
    async def bind(self, data: dict) -> None:
        pass

    @activity.defn(name="persist_experiment_run_state_activity")
    async def persist(self, data: dict) -> None:
        pass

    @activity.defn(name="setup_prompt_v2_activity")
    async def setup(self, data: dict) -> dict:
        return {"status": "READY", "column_id": data["experiment_prompt_config_id"],
                "row_ids": ["completed", "in-flight", "pending"],
                "messages": [], "model_name": "loopback-standin"}

    @activity.defn(name="setup_agent_activity")
    async def setup_agent(self, data: dict) -> dict:
        return {"status": "READY", "graph_version_id": "graph",
                "node_column_mapping": {"node": data["experiment_agent_config_id"]},
                "row_ids": ["completed", "in-flight", "pending"]}

    @activity.defn(name="prepare_agent_row_activity")
    async def prepare(self, data: dict) -> dict:
        return {"status": "READY", "graph_execution_id": data["row_id"],
                "input_payload": {"row_id": data["row_id"]}}

    @activity.defn(name="process_row_activity")
    async def row(self, data: dict) -> dict:
        if data["row_id"] == "in-flight" and data["column_id"] == "selected":
            self.entered.set()
            await self.release.wait()
        self.writes.append((data["column_id"], data["row_id"]))
        return {"status": "COMPLETED"}

    @activity.defn(name="process_batch_template_eval_activity")
    async def evaluate(self, data: dict) -> dict:
        if data["row_ids"] == ["completed"] and data["column_id"] == "selected":
            self.eval_entered.set()
            await self.eval_release.wait()
        self.evaluated.append((data["column_id"], data["row_ids"][0]))
        return {"status": "COMPLETED"}

    @activity.defn(name="check_column_status_activity")
    async def check(self, data: dict) -> dict:
        return {"status": "COMPLETED"}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["prompt", "agent"])
async def test_selected_stop_drains_then_resume_keeps_completed_and_sibling(kind):
    provider = StandinActivities()
    async with await WorkflowEnvironment.start_local() as env:
        queue = f"run-control-{uuid4()}"
        def worker():
            return Worker(env.client, task_queue=queue,
                workflow_runner=UnsandboxedWorkflowRunner(),
                workflows=[ProcessPromptV2Workflow, ProcessAgentWorkflow, GraphStandin],
                activities=[provider.bind, provider.persist, provider.setup, provider.setup_agent,
                    provider.prepare, provider.row, provider.check, provider.evaluate])

        async with worker():
            async def start(config):
                args = dict(experiment_id="experiment", dataset_id="dataset",
                            max_concurrent_rows=1, eval_template_ids=["eval"], task_queue=queue)
                if kind == "prompt":
                    fn, data = ProcessPromptV2Workflow.run, ProcessPromptV2WorkflowInput(
                        **args, experiment_prompt_config_id=config)
                else:
                    fn, data = ProcessAgentWorkflow.run, ProcessAgentWorkflowInput(
                        **args, experiment_agent_config_id=config)
                return await env.client.start_workflow(fn, data,
                    id=f"test-{config}-{uuid4()}", task_queue=queue)

            selected = await start("selected")
            sibling = await start("sibling")
            await asyncio.wait_for(provider.entered.wait(), 20)
            await asyncio.wait_for(provider.eval_entered.wait(), 20)
            await selected.signal("set_run_stopped", True)
            state = await selected.query("run_control")
            assert state["status"] == "STOPPING"
            assert state["config_id"] == "selected"
            await selected.signal("set_run_stopped", True)
            assert (await sibling.result()).completed_rows == 3
            provider.release.set()
            assert (await selected.query("run_control"))["status"] == "STOPPING"
            provider.eval_release.set()
            for _ in range(100):
                if (await selected.query("run_control"))["status"] == "STOPPED":
                    break
                await asyncio.sleep(0.05)
            assert (await selected.query("run_control"))["status"] == "STOPPED"
            assert provider.writes.count(("selected", "completed")) == 1
            assert ("selected", "pending") not in provider.writes

        # Replay on a new worker must retain the stop and completed work.
        async with worker():
            assert (await selected.query("run_control"))["status"] == "STOPPED"
            await selected.signal("set_run_stopped", False)
            await selected.signal("set_run_stopped", False)
            assert (await selected.result()).completed_rows == 3
            assert len(provider.writes) == len(set(provider.writes)) == 6
            assert len(provider.evaluated) == len(set(provider.evaluated)) == 6
            assert ("selected", "pending") in provider.writes
            assert (await selected.query("run_control"))["status"] == "COMPLETED"
