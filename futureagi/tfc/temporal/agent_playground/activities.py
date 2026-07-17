"""
Temporal activities for graph execution engine.

These activities handle the individual units of work for executing graphs.
Each activity is idempotent and can be retried safely.

Note: Django ORM is synchronous, so we use otel_sync_to_async to wrap database operations.
This ensures OTel context (trace/span info) is propagated to the sync thread.
"""

from uuid import UUID

from django.db import close_old_connections
from django.utils import timezone
from temporalio import activity

from tfc.telemetry import otel_sync_to_async
from tfc.temporal.agent_playground.types import (
    AnalyzeGraphInput,
    AnalyzeGraphOutput,
    CollectOutputsInput,
    CollectOutputsOutput,
    ExecuteNodeInput,
    ExecuteNodeOutput,
    ExecuteNodeStandaloneInput,
    ExecuteNodeStandaloneOutput,
    FinalizeGraphExecutionInput,
    FinalizeGraphExecutionOutput,
    FinalizeModuleExecutionInput,
    FinalizeModuleExecutionOutput,
    GetReadyNodesInput,
    GetReadyNodesOutput,
    InjectInputsInput,
    InjectInputsOutput,
    MarkNodeSkippedInput,
    MarkNodeSkippedOutput,
    SetupModuleExecutionInput,
    SetupModuleExecutionOutput,
)
from tfc.temporal.common.heartbeat import Heartbeater

# =============================================================================
# Synchronous Helper Functions (wrapped with otel_sync_to_async in activities)
# =============================================================================


def _analyze_graph_sync(graph_version_id: str) -> dict:
    """Synchronous implementation of analyze_graph."""
    close_old_connections()

    try:
        from agent_playground.services.engine import GraphAnalyzer

        topology = GraphAnalyzer.analyze(UUID(graph_version_id))

        topology_data = topology.to_dict()

        # Identify module nodes
        module_node_ids = [
            str(nid) for nid in topology.nodes.keys() if topology.is_module_node(nid)
        ]

        return {
            "graph_version_id": graph_version_id,
            "node_ids": [str(nid) for nid in topology.nodes.keys()],
            "start_node_ids": topology_data["start_node_ids"],
            "end_node_ids": topology_data["end_node_ids"],
            "topological_order": topology_data["topological_order"],
            "module_node_ids": module_node_ids,
            "status": "SUCCESS",
            "topology_data": topology_data,
        }
    except Exception as e:
        activity.logger.exception(f"Failed to analyze graph {graph_version_id}: {e}")
        return {
            "graph_version_id": graph_version_id,
            "node_ids": [],
            "start_node_ids": [],
            "end_node_ids": [],
            "topological_order": [],
            "module_node_ids": [],
            "status": "FAILED",
            "topology_data": {},
            "error": str(e),
        }
    finally:
        close_old_connections()


def _inject_inputs_sync(
    graph_execution_id: str,
    graph_version_id: str,
    input_payload: dict,
    topology_data: dict | None = None,
) -> dict:
    """Synchronous implementation of inject_inputs."""
    close_old_connections()

    try:
        from agent_playground.models import GraphExecution
        from agent_playground.services.engine import GraphTopology, inject_graph_inputs

        # Mark execution as started
        GraphExecution.no_workspace_objects.filter(id=UUID(graph_execution_id)).update(
            started_at=timezone.now()
        )

        topology = GraphTopology.from_dict(topology_data)

        result = inject_graph_inputs(
            graph_execution_id=UUID(graph_execution_id),
            topology=topology,
            input_payload=input_payload,
        )

        return {
            "graph_execution_id": graph_execution_id,
            "all_valid": result.all_valid,
            "injected_count": len(result.injected_ports),
            "status": "SUCCESS",
        }
    except Exception as e:
        activity.logger.exception(f"Failed to inject inputs: {e}")
        return {
            "graph_execution_id": graph_execution_id,
            "all_valid": False,
            "injected_count": 0,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


def _resolve_sinks(
    output_sink_configs: list | None,
    node_sink_overrides: dict | None,
    node_id: str,
    config: dict,
) -> list:
    """Merge all sink sources into one flat list for a given node.

    Args:
        output_sink_configs: Workflow-level sinks (primary + output_sinks from
            ExecuteGraphInput). Applied to every node.
        node_sink_overrides: Per-node runtime overrides keyed by node ID.
            Passed from ExecuteGraphInput.node_sink_overrides. We pick out
            only the sinks for *this* node_id.
        node_id: The current node's ID — used to look up its overrides.
        config: The node's Node.config from the database. May contain a
            "_output_sinks" key with statically-configured sinks.

    Returns:
        Flat list of sink config dicts (each has "name" and "config" keys).
    """
    resolved = list(output_sink_configs or [])
    resolved += (node_sink_overrides or {}).get(node_id, [])
    resolved += config.get("_output_sinks", [])
    return resolved


def _call_sinks_safe(
    resolved_sinks: list,
    node_id: str,
    node_name: str,
    template_name: str,
    inputs: dict,
    outputs: dict,
    status: str,
    metadata: dict,
) -> None:
    """Call output sinks if any are configured. Failures are logged, never raised."""
    if not resolved_sinks:
        return
    from agent_playground.services.engine.output_sink import call_sinks

    call_sinks(
        sink_configs=resolved_sinks,
        context_kwargs={
            "node_id": node_id,
            "node_name": node_name,
            "template_name": template_name,
            "inputs": inputs,
            "outputs": outputs,
            "status": status,
            "metadata": metadata,
        },
    )


def _execute_node_sync(
    graph_execution_id: str,
    graph_version_id: str,
    node_id: str,
    output_sink_configs: list | None = None,
    node_sink_overrides: dict | None = None,
    topology_data: dict | None = None,
) -> dict:
    """Synchronous implementation of execute_node."""
    close_old_connections()

    try:
        from agent_playground.models import GraphVersion, NodeExecution
        from agent_playground.models.choices import NodeExecutionStatus
        from agent_playground.services.engine import (
            GraphTopology,
            collect_node_inputs,
            get_runner,
            route_node_outputs,
            store_node_inputs,
        )

        topology = GraphTopology.from_dict(topology_data)
        node = topology.get_node(UUID(node_id))

        # Build execution context with org/workspace for runners
        graph_version = GraphVersion.no_workspace_objects.select_related("graph").get(
            id=UUID(graph_version_id)
        )
        execution_context = {
            "organization_id": str(graph_version.graph.organization_id),
            "workspace_id": (
                str(graph_version.graph.workspace_id)
                if graph_version.graph.workspace_id
                else None
            ),
            "node_id": node_id,
        }

        # Get or create NodeExecution and mark as RUNNING
        node_execution, created = NodeExecution.no_workspace_objects.update_or_create(
            graph_execution_id=UUID(graph_execution_id),
            node_id=UUID(node_id),
            defaults={
                "status": NodeExecutionStatus.RUNNING,
                "started_at": timezone.now(),
            },
        )

        # Resolve template name and config early for sink calls
        template_name = node.node_template.name if node.node_template else ""
        config = node.config or {}
        resolved_sinks = _resolve_sinks(
            output_sink_configs,
            node_sink_overrides,
            node_id,
            config,
        )

        try:
            # Collect inputs
            inputs = collect_node_inputs(
                node_id=UUID(node_id),
                graph_execution_id=UUID(graph_execution_id),
                topology=topology,
            )

            # Store connected input data for observability & input-side validation
            store_node_inputs(
                node_id=UUID(node_id),
                node_execution=node_execution,
                topology=topology,
                inputs=inputs,
            )

            runner = get_runner(template_name)

            # Execute node
            outputs = runner.run(config, inputs, execution_context)

            # Route outputs (ExecutionData — graph-internal routing)
            routing_result = route_node_outputs(
                node_id=UUID(node_id),
                node_execution=node_execution,
                topology=topology,
                outputs=outputs,
            )

            # Call output sinks on SUCCESS
            _call_sinks_safe(
                resolved_sinks,
                node_id=node_id,
                node_name=node.name,
                template_name=template_name,
                inputs=inputs,
                outputs=outputs,
                status="SUCCESS",
                metadata={"graph_execution_id": graph_execution_id},
            )

            # Mark as SUCCESS
            node_execution.status = NodeExecutionStatus.SUCCESS
            node_execution.completed_at = timezone.now()
            node_execution.save(update_fields=["status", "completed_at"])

            activity.logger.info(
                f"Node {node_id} completed successfully, "
                f"outputs_valid={routing_result.all_valid}"
            )

            return {
                "node_id": node_id,
                "status": "SUCCESS",
                "outputs": outputs,
            }

        except Exception as e:
            failure_outputs = getattr(e, "outputs", {}) or {}
            node_execution.status = NodeExecutionStatus.FAILED
            node_execution.completed_at = timezone.now()
            node_execution.error_message = str(e)
            node_execution.save(
                update_fields=["status", "completed_at", "error_message"]
            )
            if failure_outputs:
                route_node_outputs(
                    node_id=UUID(node_id),
                    node_execution=node_execution,
                    topology=topology,
                    outputs=failure_outputs,
                )

            # Call output sinks on FAILED so they can record the failure
            _call_sinks_safe(
                resolved_sinks,
                node_id=node_id,
                node_name=node.name,
                template_name=template_name,
                inputs=locals().get("inputs", {}),
                outputs=failure_outputs,
                status="FAILED",
                metadata={"graph_execution_id": graph_execution_id, "error": str(e)},
            )

            activity.logger.exception(f"Node {node_id} failed: {e}")
            return {
                "node_id": node_id,
                "status": "FAILED",
                "outputs": failure_outputs,
                "error": str(e),
            }

    except Exception as e:
        activity.logger.exception(f"Failed to execute node {node_id}: {e}")
        return {
            "node_id": node_id,
            "status": "FAILED",
            "outputs": {},
            "error": str(e),
        }
    finally:
        close_old_connections()


def _get_ready_nodes_sync(
    graph_execution_id: str,
    graph_version_id: str,
    topology_data: dict | None = None,
) -> dict:
    """Synchronous implementation of get_ready_nodes."""
    close_old_connections()

    try:
        from agent_playground.services.engine import (
            GraphTopology,
            get_ready_and_skip_nodes,
        )

        topology = GraphTopology.from_dict(topology_data)

        ready_nodes, skip_nodes, pending_nodes = get_ready_and_skip_nodes(
            topology=topology,
            graph_execution_id=UUID(graph_execution_id),
        )

        return {
            "ready_node_ids": [str(nid) for nid in ready_nodes],
            "skip_nodes": [(str(nid), reason) for nid, reason in skip_nodes],
            "pending_node_ids": [str(nid) for nid in pending_nodes],
            "status": "SUCCESS",
        }
    except Exception as e:
        activity.logger.exception(f"Failed to get ready nodes: {e}")
        return {
            "ready_node_ids": [],
            "skip_nodes": [],
            "pending_node_ids": [],
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


def _mark_node_skipped_sync(
    graph_execution_id: str,
    node_id: str,
    reason: str,
    output_sink_configs: list | None = None,
    node_sink_overrides: dict | None = None,
) -> dict:
    """Synchronous implementation of mark_node_skipped."""
    close_old_connections()

    try:
        from agent_playground.services.engine import mark_node_skipped

        mark_node_skipped(
            node_id=UUID(node_id),
            graph_execution_id=UUID(graph_execution_id),
            reason=reason,
        )

        # Call sinks with SKIPPED status so cells get error state
        resolved_sinks = _resolve_sinks(
            output_sink_configs, node_sink_overrides, node_id, {}
        )
        _call_sinks_safe(
            resolved_sinks,
            node_id=node_id,
            node_name="",
            template_name="",
            inputs={},
            outputs={},
            status="SKIPPED",
            metadata={
                "graph_execution_id": graph_execution_id,
                "error": reason,
            },
        )

        return {
            "node_id": node_id,
            "status": "SUCCESS",
        }
    except Exception as e:
        activity.logger.exception(f"Failed to mark node {node_id} as skipped: {e}")
        return {
            "node_id": node_id,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


def _collect_outputs_sync(
    graph_execution_id: str,
    graph_version_id: str,
    topology_data: dict | None = None,
) -> dict:
    """Synchronous implementation of collect_outputs."""
    close_old_connections()

    try:
        from agent_playground.services.engine import (
            GraphTopology,
            collect_graph_outputs,
        )

        topology = GraphTopology.from_dict(topology_data)

        outputs = collect_graph_outputs(
            graph_execution_id=UUID(graph_execution_id),
            topology=topology,
        )

        return {
            "graph_execution_id": graph_execution_id,
            "output_payload": outputs,
            "status": "SUCCESS",
        }
    except Exception as e:
        activity.logger.exception(f"Failed to collect outputs: {e}")
        return {
            "graph_execution_id": graph_execution_id,
            "output_payload": {},
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


# =============================================================================
# Activity Definitions
# =============================================================================


@activity.defn
async def analyze_graph_activity(input: AnalyzeGraphInput) -> AnalyzeGraphOutput:
    """Analyze a graph version and build its topology."""
    result = await otel_sync_to_async(_analyze_graph_sync, thread_sensitive=False)(
        input.graph_version_id
    )
    return AnalyzeGraphOutput(**result)


@activity.defn
async def inject_inputs_activity(input: InjectInputsInput) -> InjectInputsOutput:
    """Inject input data into the graph's unconnected input ports."""
    result = await otel_sync_to_async(_inject_inputs_sync, thread_sensitive=False)(
        input.graph_execution_id,
        input.graph_version_id,
        input.input_payload,
        input.topology_data,
    )
    return InjectInputsOutput(**result)


@activity.defn
async def execute_node_activity(input: ExecuteNodeInput) -> ExecuteNodeOutput:
    """Execute a single node (atomic or module)."""
    async with Heartbeater():
        result = await otel_sync_to_async(_execute_node_sync, thread_sensitive=False)(
            input.graph_execution_id,
            input.graph_version_id,
            input.node_id,
            input.output_sink_configs,
            input.node_sink_overrides,
            input.topology_data,
        )
    return ExecuteNodeOutput(**result)


@activity.defn
async def get_ready_nodes_activity(input: GetReadyNodesInput) -> GetReadyNodesOutput:
    """Get nodes that are ready to execute and nodes that should be skipped."""
    result = await otel_sync_to_async(_get_ready_nodes_sync, thread_sensitive=False)(
        input.graph_execution_id,
        input.graph_version_id,
        input.topology_data,
    )
    return GetReadyNodesOutput(**result)


@activity.defn
async def mark_node_skipped_activity(
    input: MarkNodeSkippedInput,
) -> MarkNodeSkippedOutput:
    """Mark a node as skipped due to upstream failure."""
    if input.error:
        activity.logger.exception(
            "Graph execution error: node %s skipped "
            "(graph_execution_id=%s, reason=%s)",
            input.node_id,
            input.graph_execution_id,
            input.reason,
        )
    result = await otel_sync_to_async(_mark_node_skipped_sync, thread_sensitive=False)(
        input.graph_execution_id,
        input.node_id,
        input.reason,
        input.output_sink_configs,
        input.node_sink_overrides,
    )
    return MarkNodeSkippedOutput(**result)


@activity.defn
async def collect_outputs_activity(input: CollectOutputsInput) -> CollectOutputsOutput:
    """Collect final outputs from the graph's unconnected output ports."""
    result = await otel_sync_to_async(_collect_outputs_sync, thread_sensitive=False)(
        input.graph_execution_id,
        input.graph_version_id,
        input.topology_data,
    )
    return CollectOutputsOutput(**result)


# =============================================================================
# Graph Execution Finalization
# =============================================================================


def _finalize_graph_execution_sync(
    graph_execution_id: str,
    status: str,
    output_payload: dict,
    error: str | None,
) -> dict:
    """Update the GraphExecution DB record with final status and outputs."""
    close_old_connections()

    try:
        from agent_playground.models import GraphExecution
        from agent_playground.models.choices import GraphExecutionStatus

        graph_execution = GraphExecution.no_workspace_objects.get(
            id=UUID(graph_execution_id)
        )

        graph_execution.status = (
            GraphExecutionStatus.SUCCESS
            if status == "SUCCESS"
            else GraphExecutionStatus.FAILED
        )
        graph_execution.output_payload = output_payload
        graph_execution.completed_at = timezone.now()
        if error:
            graph_execution.error_message = error
        graph_execution.save(
            update_fields=["status", "output_payload", "completed_at", "error_message"]
        )

        return {
            "graph_execution_id": graph_execution_id,
            "status": "SUCCESS",
        }
    except Exception as e:
        activity.logger.exception(f"Failed to finalize graph execution: {e}")
        return {
            "graph_execution_id": graph_execution_id,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def finalize_graph_execution_activity(
    input: FinalizeGraphExecutionInput,
) -> FinalizeGraphExecutionOutput:
    """Update GraphExecution DB record with final status and outputs."""
    result = await otel_sync_to_async(
        _finalize_graph_execution_sync, thread_sensitive=False
    )(
        input.graph_execution_id,
        input.status,
        input.output_payload,
        input.error,
    )
    return FinalizeGraphExecutionOutput(**result)


# =============================================================================
# Module Execution Activities
# =============================================================================


def _setup_module_execution_sync(
    parent_graph_execution_id: str,
    parent_graph_version_id: str,
    module_node_id: str,
    topology_data: dict | None = None,
) -> dict:
    """
    Set up a module node execution by creating child GraphExecution and injecting inputs.
    """
    close_old_connections()

    try:
        from agent_playground.models import GraphExecution, NodeExecution
        from agent_playground.models.choices import (
            GraphExecutionStatus,
            NodeExecutionStatus,
        )
        from agent_playground.services.engine import (
            GraphTopology,
            collect_node_inputs,
            store_node_inputs,
        )

        # Get parent topology
        parent_topology = GraphTopology.from_dict(topology_data)

        # Get child graph version ID
        child_graph_version_id = parent_topology.get_module_child_graph_version_id(
            UUID(module_node_id)
        )

        # Get or create parent node execution and mark as RUNNING
        parent_node_execution, _ = NodeExecution.no_workspace_objects.update_or_create(
            graph_execution_id=UUID(parent_graph_execution_id),
            node_id=UUID(module_node_id),
            defaults={
                "status": NodeExecutionStatus.RUNNING,
                "started_at": timezone.now(),
            },
        )

        # Collect module node inputs from the parent graph.
        module_inputs = collect_node_inputs(
            node_id=UUID(module_node_id),
            graph_execution_id=UUID(parent_graph_execution_id),
            topology=parent_topology,
        )

        # Remap module input keys to child graph port routing_keys using ref_port.
        # Module ports use display_name as routing_key (key="custom"), but child
        # graph ports may use a different routing_key. ref_port links them directly.
        child_input_payload: dict = {}
        module_input_ports = parent_topology.node_input_ports.get(
            UUID(module_node_id), []
        )
        for port in module_input_ports:
            if port.routing_key in module_inputs and port.ref_port_id:
                ref_port = parent_topology.ports.get(port.ref_port_id)
                if ref_port is None:
                    # ref_port is in child graph, not parent topology — fetch it
                    from agent_playground.models import Port as PortModel

                    try:
                        ref_port = PortModel.no_workspace_objects.get(
                            id=port.ref_port_id
                        )
                    except PortModel.DoesNotExist:
                        continue
                child_input_payload[ref_port.routing_key] = module_inputs[
                    port.routing_key
                ]
            elif port.routing_key in module_inputs:
                # No ref_port — fall back to same key (backward compatibility)
                child_input_payload[port.routing_key] = module_inputs[port.routing_key]

        # Create child GraphExecution with remapped input_payload
        child_graph_execution = GraphExecution.no_workspace_objects.create(
            graph_version_id=child_graph_version_id,
            parent_node_execution=parent_node_execution,
            status=GraphExecutionStatus.PENDING,
            input_payload=child_input_payload,
        )

        # Store connected input data for observability & input-side validation
        store_node_inputs(
            node_id=UUID(module_node_id),
            node_execution=parent_node_execution,
            topology=parent_topology,
            inputs=module_inputs,
        )

        activity.logger.info(
            f"Setup module execution: module_node={module_node_id}, "
            f"child_graph_execution={child_graph_execution.id}, "
            f"input_keys={list(module_inputs.keys())}"
        )

        return {
            "child_graph_execution_id": str(child_graph_execution.id),
            "child_graph_version_id": str(child_graph_version_id),
            "module_node_id": module_node_id,
            "input_payload": module_inputs,
            "status": "SUCCESS",
        }

    except Exception as e:
        activity.logger.exception(f"Failed to setup module execution: {e}")
        return {
            "child_graph_execution_id": "",
            "child_graph_version_id": "",
            "module_node_id": module_node_id,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


def _finalize_module_execution_sync(
    parent_graph_execution_id: str,
    parent_graph_version_id: str,
    child_graph_execution_id: str,
    child_graph_version_id: str,
    module_node_id: str,
    child_status: str,
    topology_data: dict | None = None,
    output_sink_configs: list | None = None,
    node_sink_overrides: dict | None = None,
) -> dict:
    """
    Finalize a module node execution by routing child outputs to module node.
    """
    close_old_connections()

    try:
        from agent_playground.models import NodeExecution
        from agent_playground.models.choices import NodeExecutionStatus
        from agent_playground.services.engine import (
            GraphAnalyzer,
            GraphTopology,
            route_module_outputs,
        )

        # Get node execution
        node_execution = NodeExecution.no_workspace_objects.get(
            graph_execution_id=UUID(parent_graph_execution_id),
            node_id=UUID(module_node_id),
        )

        if child_status != "SUCCESS":
            # Child workflow failed - mark module node as failed
            node_execution.status = NodeExecutionStatus.FAILED
            node_execution.completed_at = timezone.now()
            node_execution.error_message = (
                f"Child graph execution failed: {child_status}"
            )
            node_execution.save(
                update_fields=["status", "completed_at", "error_message"]
            )

            # Call sinks so cell shows error state
            resolved_sinks = _resolve_sinks(
                output_sink_configs, node_sink_overrides, module_node_id, {}
            )
            _call_sinks_safe(
                resolved_sinks,
                node_id=module_node_id,
                node_name="",
                template_name="",
                inputs={},
                outputs={},
                status="FAILED",
                metadata={
                    "graph_execution_id": parent_graph_execution_id,
                    "error": f"Child graph execution failed: {child_status}",
                },
            )

            return {
                "module_node_id": module_node_id,
                "status": "FAILED",
                "error": f"Child graph execution failed: {child_status}",
            }

        # Route child outputs to module node
        parent_topology = GraphTopology.from_dict(topology_data)
        child_topology = GraphAnalyzer.analyze(UUID(child_graph_version_id))

        routing_result = route_module_outputs(
            parent_graph_execution_id=UUID(parent_graph_execution_id),
            child_graph_execution_id=UUID(child_graph_execution_id),
            module_node_id=UUID(module_node_id),
            parent_topology=parent_topology,
            child_topology=child_topology,
        )

        # Mark module node as SUCCESS
        node_execution.status = NodeExecutionStatus.SUCCESS
        node_execution.completed_at = timezone.now()
        node_execution.save(update_fields=["status", "completed_at"])

        # Collect routed outputs for the sink call
        from agent_playground.models import ExecutionData

        module_outputs = {}
        for ed in ExecutionData.no_workspace_objects.filter(
            node_execution=node_execution,
            port__direction="output",
        ).select_related("port"):
            if ed.is_valid:
                module_outputs[ed.port.routing_key] = ed.payload

        # Call sinks so cell shows the module output
        resolved_sinks = _resolve_sinks(
            output_sink_configs, node_sink_overrides, module_node_id, {}
        )
        _call_sinks_safe(
            resolved_sinks,
            node_id=module_node_id,
            node_name="",
            template_name="",
            inputs={},
            outputs=module_outputs,
            status="SUCCESS",
            metadata={"graph_execution_id": parent_graph_execution_id},
        )

        activity.logger.info(
            f"Finalized module execution: module_node={module_node_id}, "
            f"outputs_valid={routing_result.all_valid}"
        )

        return {
            "module_node_id": module_node_id,
            "status": "SUCCESS",
        }

    except Exception as e:
        activity.logger.exception(f"Failed to finalize module execution: {e}")

        # Try to mark node as failed
        try:
            from agent_playground.models import NodeExecution
            from agent_playground.models.choices import NodeExecutionStatus

            node_execution = NodeExecution.no_workspace_objects.get(
                graph_execution_id=UUID(parent_graph_execution_id),
                node_id=UUID(module_node_id),
            )
            node_execution.status = NodeExecutionStatus.FAILED
            node_execution.completed_at = timezone.now()
            node_execution.error_message = str(e)
            node_execution.save(
                update_fields=["status", "completed_at", "error_message"]
            )
        except Exception:
            pass

        # Best-effort sink call for error state
        try:
            resolved_sinks = _resolve_sinks(
                output_sink_configs, node_sink_overrides, module_node_id, {}
            )
            _call_sinks_safe(
                resolved_sinks,
                node_id=module_node_id,
                node_name="",
                template_name="",
                inputs={},
                outputs={},
                status="FAILED",
                metadata={
                    "graph_execution_id": parent_graph_execution_id,
                    "error": str(e),
                },
            )
        except Exception:
            pass

        return {
            "module_node_id": module_node_id,
            "status": "FAILED",
            "error": str(e),
        }
    finally:
        close_old_connections()


@activity.defn
async def setup_module_execution_activity(
    input: SetupModuleExecutionInput,
) -> SetupModuleExecutionOutput:
    """Set up a module node execution by creating child GraphExecution."""
    result = await otel_sync_to_async(
        _setup_module_execution_sync, thread_sensitive=False
    )(
        input.parent_graph_execution_id,
        input.parent_graph_version_id,
        input.module_node_id,
        input.topology_data,
    )
    return SetupModuleExecutionOutput(**result)


@activity.defn
async def finalize_module_execution_activity(
    input: FinalizeModuleExecutionInput,
) -> FinalizeModuleExecutionOutput:
    """Finalize a module node execution by routing child outputs."""
    result = await otel_sync_to_async(
        _finalize_module_execution_sync, thread_sensitive=False
    )(
        input.parent_graph_execution_id,
        input.parent_graph_version_id,
        input.child_graph_execution_id,
        input.child_graph_version_id,
        input.module_node_id,
        input.child_status,
        input.topology_data,
        input.output_sink_configs,
        input.node_sink_overrides,
    )
    return FinalizeModuleExecutionOutput(**result)


# =============================================================================
# Standalone Node Execution (no graph context)
# =============================================================================


def _execute_node_standalone_sync(
    template_name: str,
    config: dict,
    inputs: dict,
    organization_id: str,
    output_sink_configs: list | None = None,
    node_id: str = "",
    node_name: str = "",
    workspace_id: str = "",
    metadata: dict | None = None,
) -> dict:
    """Execute a node runner standalone — no GraphExecution or ExecutionData.

    Used by other Temporal workflows that want to run a node runner and store
    outputs via sinks without the full graph execution machinery.
    """
    close_old_connections()

    outputs = {}
    status = "SUCCESS"
    error = None
    sink_results = []

    try:
        from agent_playground.services.engine import get_runner

        execution_context = {
            "organization_id": organization_id,
            "workspace_id": workspace_id or None,
            "node_id": node_id,
        }
        runner = get_runner(template_name)
        outputs = runner.run(config, inputs, execution_context)

    except Exception as e:
        status = "FAILED"
        error = str(e)
        activity.logger.exception(f"Standalone node execution failed: {e}")

    # Always call sinks (even on failure so they can record it)
    if output_sink_configs:
        from agent_playground.services.engine.output_sink import call_sinks

        sink_results = call_sinks(
            sink_configs=output_sink_configs,
            context_kwargs={
                "node_id": node_id,
                "node_name": node_name,
                "template_name": template_name,
                "inputs": inputs,
                "outputs": outputs,
                "status": status,
                "metadata": metadata or {},
            },
        )

    close_old_connections()

    return {
        "status": status,
        "outputs": outputs,
        "error": error,
        "sink_results": sink_results,
    }


@activity.defn
async def execute_node_standalone_activity(
    input: ExecuteNodeStandaloneInput,
) -> ExecuteNodeStandaloneOutput:
    """Execute a node runner standalone — no graph context, outputs go to sinks."""
    async with Heartbeater():
        result = await otel_sync_to_async(
            _execute_node_standalone_sync, thread_sensitive=False
        )(
            input.template_name,
            input.config,
            input.inputs,
            input.organization_id,
            input.output_sink_configs,
            input.node_id,
            input.node_name,
            input.workspace_id,
            input.metadata,
        )
    return ExecuteNodeStandaloneOutput(**result)


# All activities for registration
ALL_ACTIVITIES = [
    analyze_graph_activity,
    inject_inputs_activity,
    execute_node_activity,
    get_ready_nodes_activity,
    mark_node_skipped_activity,
    collect_outputs_activity,
    finalize_graph_execution_activity,
    setup_module_execution_activity,
    finalize_module_execution_activity,
    execute_node_standalone_activity,
]


__all__ = [
    "analyze_graph_activity",
    "inject_inputs_activity",
    "execute_node_activity",
    "get_ready_nodes_activity",
    "mark_node_skipped_activity",
    "collect_outputs_activity",
    "finalize_graph_execution_activity",
    "setup_module_execution_activity",
    "finalize_module_execution_activity",
    "execute_node_standalone_activity",
    "ALL_ACTIVITIES",
]
