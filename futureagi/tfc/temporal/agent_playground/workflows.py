"""
Temporal workflows for graph execution engine.

These workflows orchestrate the graph execution process:
1. Analyze graph topology
2. Inject input data
3. Execute nodes in parallel (respecting dependencies)
4. For module nodes: spawn child workflow
5. Collect output data

IMPORTANT: Do NOT use workflow.logger in workflows - it uses Python's stdlib
logging which acquires locks and causes deadlocks. Logging should be done
in activities instead.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# Import types from separate module (no Django imports, safe for sandbox)
with workflow.unsafe.imports_passed_through():
    from tfc.temporal.agent_playground.types import (
        AnalyzeGraphInput,
        CollectOutputsInput,
        ExecuteGraphInput,
        ExecuteGraphOutput,
        ExecuteNodeInput,
        FinalizeGraphExecutionInput,
        FinalizeModuleExecutionInput,
        GetReadyNodesInput,
        InjectInputsInput,
        MarkNodeSkippedInput,
        OutputSinkConfig,
        SetupModuleExecutionInput,
    )


# =============================================================================
# Retry Policies
# =============================================================================

# Retry policy for setup activities (fast, few retries)
SETUP_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)

# Retry policy for node execution (longer, handles LLM API errors)
NODE_EXECUTION_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=3,
    backoff_coefficient=2.0,
    non_retryable_error_types=["ValueError", "NodeRunnerNotFoundError"],
)


# =============================================================================
# Helper Functions
# =============================================================================


def get_result_field(result, field: str, default=None):
    """Get a field from an activity result, handling both dict and dataclass."""
    if isinstance(result, dict):
        return result.get(field, default)
    return getattr(result, field, default)


# =============================================================================
# Workflows
# =============================================================================


@workflow.defn
class GraphExecutionWorkflow:
    """
    Execute a graph as a DAG with parallel node execution.

    Flow:
    1. Analyze graph topology (nodes, edges, execution order)
    2. Inject input data into unconnected input ports
    3. Execute nodes in parallel where dependencies allow
       - Atomic nodes: execute via activity
       - Module nodes: spawn child workflow (recursive)
    4. Skip nodes whose upstream dependencies failed
    5. Collect outputs from unconnected output ports
    """

    def __init__(self):
        self._graph_execution_id: str = ""
        self._failed: bool = False

    @workflow.run
    async def run(self, input: ExecuteGraphInput) -> ExecuteGraphOutput:
        self._graph_execution_id = input.graph_execution_id

        # Phase 1: Analyze graph topology
        analyze_result = await workflow.execute_activity(
            "analyze_graph_activity",
            AnalyzeGraphInput(graph_version_id=input.graph_version_id),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=SETUP_RETRY_POLICY,
        )

        if get_result_field(analyze_result, "status") == "FAILED":
            return ExecuteGraphOutput(
                graph_execution_id=self._graph_execution_id,
                status="FAILED",
                output_payload={},
                error=get_result_field(
                    analyze_result, "error", "Graph analysis failed"
                ),
            )

        module_node_ids = set(get_result_field(analyze_result, "module_node_ids", []))
        total_node_count = len(get_result_field(analyze_result, "node_ids", []))
        topology_data = get_result_field(analyze_result, "topology_data", {})

        # Phase 2: Inject input data
        inject_result = await workflow.execute_activity(
            "inject_inputs_activity",
            InjectInputsInput(
                graph_execution_id=self._graph_execution_id,
                graph_version_id=input.graph_version_id,
                input_payload=input.input_payload,
                topology_data=topology_data,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=SETUP_RETRY_POLICY,
        )

        if get_result_field(inject_result, "status") == "FAILED":
            return ExecuteGraphOutput(
                graph_execution_id=self._graph_execution_id,
                status="FAILED",
                output_payload={},
                error=get_result_field(
                    inject_result, "error", "Input injection failed"
                ),
            )

        # Convert OutputSinkConfig dataclasses to plain dicts for Temporal
        # serialization — activity inputs must be JSON-serializable, and
        # dataclasses are not automatically serialized by the Temporal SDK.
        base_sink_configs: list[dict] = []
        if input.primary_output_sink:
            base_sink_configs.append(
                {
                    "name": input.primary_output_sink.name,
                    "config": input.primary_output_sink.config,
                }
            )
        for sink in input.output_sinks:
            base_sink_configs.append({"name": sink.name, "config": sink.config})

        # Per-node overrides: OutputSinkConfig -> list[dict], keyed by node ID
        node_overrides_raw: dict[str, list[dict]] = {
            node_id: [{"name": s.name, "config": s.config} for s in sinks]
            for node_id, sinks in input.node_sink_overrides.items()
        }

        # Phase 3: Execute nodes - eagerly start downstream nodes as soon
        # as their dependencies complete (instead of waiting for the entire
        # batch).  Uses asyncio.wait(FIRST_COMPLETED) so that when *any*
        # node finishes we immediately re-check readiness and launch newly
        # unblocked nodes.
        #
        # Safety: track actual work (nodes launched + skipped) to prevent
        # infinite loops.  Pure "waiting for running nodes" iterations don't
        # count — only real state transitions do.  Each node is launched or
        # skipped exactly once (get_ready_nodes never returns a node that is
        # already RUNNING/completed, and Temporal retries happen inside the
        # activity — transparent to this loop), so total_node_count is the
        # exact ceiling.
        max_node_actions = total_node_count
        node_actions = 0  # incremented per launch or skip
        running: dict[asyncio.Task, str] = {}  # task -> node_id

        while True:
            if len(running) < input.max_concurrent_nodes:
                # Get ready, skip, and pending nodes
                ready_result = await workflow.execute_activity(
                    "get_ready_nodes_activity",
                    GetReadyNodesInput(
                        graph_execution_id=self._graph_execution_id,
                        graph_version_id=input.graph_version_id,
                        topology_data=topology_data,
                    ),
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=SETUP_RETRY_POLICY,
                )

                ready_node_ids = get_result_field(ready_result, "ready_node_ids", [])
                skip_nodes = get_result_field(ready_result, "skip_nodes", [])
                pending_node_ids = get_result_field(
                    ready_result, "pending_node_ids", []
                )

                # Mark skipped nodes
                for node_id, reason in skip_nodes:
                    await workflow.execute_activity(
                        "mark_node_skipped_activity",
                        MarkNodeSkippedInput(
                            graph_execution_id=self._graph_execution_id,
                            node_id=node_id,
                            reason=reason,
                            output_sink_configs=base_sink_configs,
                            node_sink_overrides=node_overrides_raw,
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=SETUP_RETRY_POLICY,
                    )
                    self._failed = (
                        True  # At least one node was skipped due to upstream failure
                    )
                    node_actions += 1

                # No more work to do - exit when there are no ready nodes,
                # no skips happened, AND nothing is still running.
                if not ready_node_ids and not skip_nodes and not running:
                    # Deadlock safety: if pending nodes remain but nothing is
                    # ready or skippable, mark them as SKIPPED to prevent hangs.
                    if pending_node_ids:
                        for node_id in pending_node_ids:
                            await workflow.execute_activity(
                                "mark_node_skipped_activity",
                                MarkNodeSkippedInput(
                                    graph_execution_id=self._graph_execution_id,
                                    node_id=node_id,
                                    reason="Deadlock detected: node could not become ready",
                                    output_sink_configs=base_sink_configs,
                                    node_sink_overrides=node_overrides_raw,
                                    error=True,
                                ),
                                start_to_close_timeout=timedelta(seconds=30),
                                retry_policy=SETUP_RETRY_POLICY,
                            )
                        self._failed = True
                    break

                # Launch ready nodes up to available capacity
                available = input.max_concurrent_nodes - len(running)
                atomic_nodes = [n for n in ready_node_ids if n not in module_node_ids]
                module_nodes = [n for n in ready_node_ids if n in module_node_ids]

                for node_id in atomic_nodes[:available]:
                    node_execution = workflow.execute_activity(
                        "execute_node_activity",
                        ExecuteNodeInput(
                            graph_execution_id=self._graph_execution_id,
                            graph_version_id=input.graph_version_id,
                            node_id=node_id,
                            output_sink_configs=base_sink_configs,
                            node_sink_overrides=node_overrides_raw,
                            topology_data=topology_data,
                        ),
                        start_to_close_timeout=timedelta(hours=1),
                        heartbeat_timeout=timedelta(minutes=5),
                        retry_policy=NODE_EXECUTION_RETRY_POLICY,
                    )
                    running[asyncio.ensure_future(node_execution)] = node_id
                    node_actions += 1
                    available -= 1

                for node_id in module_nodes[:available]:
                    module_execution = self._execute_module_node(
                        node_id=node_id,
                        graph_version_id=input.graph_version_id,
                        max_concurrent_nodes=input.max_concurrent_nodes,
                        task_queue=input.task_queue,
                        primary_output_sink=input.primary_output_sink,
                        output_sinks=input.output_sinks,
                        topology_data=topology_data,
                        base_sink_configs=base_sink_configs,
                        node_overrides_raw=node_overrides_raw,
                    )
                    running[asyncio.ensure_future(module_execution)] = node_id
                    node_actions += 1
                    available -= 1

                # Safety: if we've processed more actions than expected,
                # a node is being re-processed — break to prevent infinite loop.
                if node_actions > max_node_actions:
                    self._failed = True
                    break

            # Nothing running - skip cascade may need another iteration
            if not running:
                continue

            # Wait for ANY task to complete, then immediately loop back to
            # check readiness and launch newly unblocked downstream nodes.
            done, _ = await asyncio.wait(
                running.keys(), return_when=asyncio.FIRST_COMPLETED
            )

            for task in done:
                node_id = running.pop(task)
                try:
                    result = task.result()
                    if get_result_field(result, "status") == "FAILED":
                        self._failed = True
                except Exception:
                    self._failed = True

        # Phase 4: Collect outputs
        collect_result = await workflow.execute_activity(
            "collect_outputs_activity",
            CollectOutputsInput(
                graph_execution_id=self._graph_execution_id,
                graph_version_id=input.graph_version_id,
                topology_data=topology_data,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=SETUP_RETRY_POLICY,
        )

        output_payload = get_result_field(collect_result, "output_payload", {})
        # Preserve command arguments and completion results for histories written
        # before collection/finalization failures became part of the contract.
        validate_results = workflow.patched("graph-result-failures-v1")
        collection_failed = (
            validate_results and get_result_field(collect_result, "status") != "SUCCESS"
        )
        self._failed = self._failed or collection_failed
        collection_error = (
            get_result_field(collect_result, "error")
            or "Graph output collection failed"
        )

        final_status = "FAILED" if self._failed else "SUCCESS"
        final_error = (
            collection_error
            if collection_failed
            else "One or more nodes failed"
            if self._failed
            else None
        )

        # Phase 5: Update GraphExecution DB record with final status
        finalized = await workflow.execute_activity(
            "finalize_graph_execution_activity",
            FinalizeGraphExecutionInput(
                graph_execution_id=self._graph_execution_id,
                status=final_status,
                output_payload=output_payload,
                error=final_error,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=SETUP_RETRY_POLICY,
        )
        if validate_results and get_result_field(finalized, "status") != "SUCCESS":
            final_status = "FAILED"
            final_error = (
                get_result_field(finalized, "error") or "Graph finalization failed"
            )

        return ExecuteGraphOutput(
            graph_execution_id=self._graph_execution_id,
            status=final_status,
            output_payload=output_payload,
            error=final_error,
        )

    async def _execute_module_node(
        self,
        node_id: str,
        graph_version_id: str,
        max_concurrent_nodes: int,
        task_queue: str,
        primary_output_sink: OutputSinkConfig | None = None,
        output_sinks: list[OutputSinkConfig] | None = None,
        topology_data: dict | None = None,
        base_sink_configs: list[dict] | None = None,
        node_overrides_raw: dict[str, list[dict]] | None = None,
    ):
        """
        Execute a module node by spawning a child workflow.

        Steps:
        1. Setup: Create child GraphExecution, inject inputs
        2. Execute: Spawn child GraphExecutionWorkflow
        3. Finalize: Route child outputs to module node
        """
        # Step 1: Setup child execution
        setup_result = await workflow.execute_activity(
            "setup_module_execution_activity",
            SetupModuleExecutionInput(
                parent_graph_execution_id=self._graph_execution_id,
                parent_graph_version_id=graph_version_id,
                module_node_id=node_id,
                topology_data=topology_data,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=SETUP_RETRY_POLICY,
        )

        if get_result_field(setup_result, "status") == "FAILED":
            return setup_result

        child_graph_execution_id = get_result_field(
            setup_result, "child_graph_execution_id"
        )
        child_graph_version_id = get_result_field(
            setup_result, "child_graph_version_id"
        )
        child_input_payload = get_result_field(setup_result, "input_payload", {})

        # Step 2: Execute child workflow
        child_workflow_id = f"graph-execution-{child_graph_execution_id}"

        child_result = await workflow.execute_child_workflow(
            "GraphExecutionWorkflow",
            ExecuteGraphInput(
                graph_execution_id=child_graph_execution_id,
                graph_version_id=child_graph_version_id,
                input_payload=child_input_payload,
                max_concurrent_nodes=max_concurrent_nodes,
                task_queue=task_queue,
                parent_node_execution_id=node_id,
                primary_output_sink=primary_output_sink,
                output_sinks=output_sinks or [],
            ),
            id=child_workflow_id,
            task_queue=task_queue,
        )

        child_status = get_result_field(child_result, "status", "FAILED")

        # Step 3: Finalize - route outputs to module node
        finalize_result = await workflow.execute_activity(
            "finalize_module_execution_activity",
            FinalizeModuleExecutionInput(
                parent_graph_execution_id=self._graph_execution_id,
                parent_graph_version_id=graph_version_id,
                child_graph_execution_id=child_graph_execution_id,
                child_graph_version_id=child_graph_version_id,
                module_node_id=node_id,
                child_status=child_status,
                topology_data=topology_data,
                output_sink_configs=base_sink_configs or [],
                node_sink_overrides=node_overrides_raw or {},
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=SETUP_RETRY_POLICY,
        )

        return finalize_result


# All workflows for registration
ALL_WORKFLOWS = [GraphExecutionWorkflow]


def get_workflows():
    """Get all workflow classes for registration."""
    return ALL_WORKFLOWS


__all__ = [
    "GraphExecutionWorkflow",
    "ExecuteGraphInput",
    "ExecuteGraphOutput",
    "get_workflows",
    "ALL_WORKFLOWS",
]
