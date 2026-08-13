"""
Node Readiness and Skip Logic for Graph Execution.

This module determines when nodes are ready to execute and when they should
be skipped due to upstream failures.

Readiness Rules:
    A node is ready to execute when ALL of its input ports are satisfied.
    A port is satisfied if:
    - It's connected and the upstream node completed with SUCCESS and has valid data
    - It's connected and upstream failed/skipped, but port has a default value
    - It's unconnected and has valid injected data or a default value

Skip Rules:
    A node should be skipped when:
    - Any upstream node is FAILED or SKIPPED
    - AND the affected input port has no default value
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from agent_playground.models import ExecutionData, NodeExecution
from agent_playground.models.choices import NodeExecutionStatus, PortDirection

if TYPE_CHECKING:
    from agent_playground.services.engine.analyzer import GraphTopology


@dataclass
class PortSatisfactionInfo:
    """Information about whether a port's data requirements are met."""

    port_id: UUID
    port_key: str
    is_satisfied: bool
    has_data: bool
    is_valid: bool  # Whether the data passes JSON schema validation
    has_default: bool
    upstream_status: NodeExecutionStatus | None  # None if unconnected
    reason: str


@dataclass
class NodeReadinessResult:
    """Result of checking if a node is ready to execute."""

    is_ready: bool
    should_skip: bool
    skip_reason: str | None
    unsatisfied_ports: list[PortSatisfactionInfo]


def _get_port_data(
    port_id: UUID,
    graph_execution_id: UUID,
) -> tuple[bool, bool]:
    """
    Check if valid data exists for a port.

    Args:
        port_id: The port to check
        graph_execution_id: Current execution context

    Returns:
        Tuple of (has_data, is_valid)
    """
    try:
        execution_data = ExecutionData.no_workspace_objects.get(
            node_execution__graph_execution_id=graph_execution_id,
            port_id=port_id,
        )
        return True, execution_data.is_valid
    except ExecutionData.DoesNotExist:
        return False, False


def check_node_readiness(
    node_id: UUID,
    topology: "GraphTopology",
    graph_execution_id: UUID,
    node_executions: dict[UUID, "NodeExecution"] | None = None,
) -> NodeReadinessResult:
    """
    Check if a node is ready to execute.

    A node is ready when all its input ports are satisfied:
    - Connected ports: upstream must be SUCCESS with valid data, or port has default
    - Unconnected ports: must have valid injected data or a default value

    A node should be skipped if upstream failed/skipped and no default available.

    Args:
        node_id: The node to check
        topology: Pre-analyzed graph topology
        graph_execution_id: Current execution context
        node_executions: Optional pre-fetched map of node_id -> NodeExecution.
            When provided, avoids per-upstream DB queries (N+1 optimization).

    Returns:
        NodeReadinessResult with is_ready, should_skip, and port details
    """
    input_ports = topology.node_input_ports.get(node_id, [])

    if not input_ports:
        # Node has no inputs - always ready
        return NodeReadinessResult(
            is_ready=True,
            should_skip=False,
            skip_reason=None,
            unsatisfied_ports=[],
        )

    unsatisfied_ports: list[PortSatisfactionInfo] = []
    should_skip = False
    skip_reason = None

    for port in input_ports:
        edge = topology.edge_by_target_port.get(port.id)
        has_default = port.default_value is not None

        if edge is None:
            # Unconnected port - check for injected data
            has_data, is_valid = _get_port_data(port.id, graph_execution_id)

            if has_data and is_valid:
                continue  # Satisfied with valid injected data
            elif has_data and not is_valid:
                # Data exists but fails schema validation
                unsatisfied_ports.append(
                    PortSatisfactionInfo(
                        port_id=port.id,
                        port_key=port.routing_key,
                        is_satisfied=False,
                        has_data=True,
                        is_valid=False,
                        has_default=has_default,
                        upstream_status=None,
                        reason="Injected data fails schema validation",
                    )
                )
            elif has_default:
                continue  # Satisfied with default
            else:
                unsatisfied_ports.append(
                    PortSatisfactionInfo(
                        port_id=port.id,
                        port_key=port.routing_key,
                        is_satisfied=False,
                        has_data=False,
                        is_valid=False,
                        has_default=has_default,
                        upstream_status=None,
                        reason="Unconnected port with no injected data and no default",
                    )
                )
            continue

        # Connected port - check upstream node status
        source_port = topology.ports.get(edge.source_port_id)
        if not source_port:
            unsatisfied_ports.append(
                PortSatisfactionInfo(
                    port_id=port.id,
                    port_key=port.routing_key,
                    is_satisfied=False,
                    has_data=False,
                    is_valid=False,
                    has_default=has_default,
                    upstream_status=None,
                    reason="Source port not found in topology",
                )
            )
            continue

        source_node_id = source_port.node_id

        # Get upstream node execution status
        if node_executions is not None:
            upstream_execution = node_executions.get(source_node_id)
        else:
            try:
                upstream_execution = NodeExecution.no_workspace_objects.get(
                    graph_execution_id=graph_execution_id,
                    node_id=source_node_id,
                )
            except NodeExecution.DoesNotExist:
                upstream_execution = None

        if upstream_execution is None:
            # Upstream hasn't started yet
            unsatisfied_ports.append(
                PortSatisfactionInfo(
                    port_id=port.id,
                    port_key=port.routing_key,
                    is_satisfied=False,
                    has_data=False,
                    is_valid=False,
                    has_default=has_default,
                    upstream_status=None,
                    reason="Upstream node execution not found (not started)",
                )
            )
            continue

        upstream_status = upstream_execution.status

        if upstream_status == NodeExecutionStatus.SUCCESS:
            # Check if upstream produced valid data for this port
            has_data, is_valid = _get_port_data(edge.source_port_id, graph_execution_id)

            if has_data and is_valid:
                continue  # Satisfied with valid upstream data
            elif has_data and not is_valid:
                # Upstream produced invalid data
                if has_default:
                    continue  # Fall back to default
                else:
                    unsatisfied_ports.append(
                        PortSatisfactionInfo(
                            port_id=port.id,
                            port_key=port.routing_key,
                            is_satisfied=False,
                            has_data=True,
                            is_valid=False,
                            has_default=has_default,
                            upstream_status=upstream_status,
                            reason="Upstream data fails schema validation, no default",
                        )
                    )
            elif has_default:
                continue  # Satisfied with default (no data produced)
            else:
                # Upstream succeeded but wrote no output and port has no fallback.
                # This state is unresolvable — mark for skip, not perpetual pending.
                should_skip = True
                skip_reason = (
                    f"Upstream node {source_node_id} succeeded but produced no data, "
                    f"port '{port.routing_key}' has no default value"
                )
                unsatisfied_ports.append(
                    PortSatisfactionInfo(
                        port_id=port.id,
                        port_key=port.routing_key,
                        is_satisfied=False,
                        has_data=False,
                        is_valid=False,
                        has_default=has_default,
                        upstream_status=upstream_status,
                        reason="Upstream succeeded but produced no data, no default",
                    )
                )

        elif upstream_status in (
            NodeExecutionStatus.FAILED,
            NodeExecutionStatus.SKIPPED,
        ):
            # Upstream failed or was skipped
            if has_default:
                continue  # Satisfied with default
            else:
                # Cannot recover - this node should be skipped
                should_skip = True
                skip_reason = (
                    f"Upstream node {source_node_id} is {upstream_status}, "
                    f"port '{port.routing_key}' has no default value"
                )
                unsatisfied_ports.append(
                    PortSatisfactionInfo(
                        port_id=port.id,
                        port_key=port.routing_key,
                        is_satisfied=False,
                        has_data=False,
                        is_valid=False,
                        has_default=has_default,
                        upstream_status=upstream_status,
                        reason=f"Upstream {upstream_status}, no default available",
                    )
                )

        elif upstream_status in (
            NodeExecutionStatus.PENDING,
            NodeExecutionStatus.RUNNING,
        ):
            # Still waiting for upstream
            unsatisfied_ports.append(
                PortSatisfactionInfo(
                    port_id=port.id,
                    port_key=port.routing_key,
                    is_satisfied=False,
                    has_data=False,
                    is_valid=False,
                    has_default=has_default,
                    upstream_status=upstream_status,
                    reason=f"Upstream still {upstream_status}",
                )
            )

    is_ready = len(unsatisfied_ports) == 0 and not should_skip

    return NodeReadinessResult(
        is_ready=is_ready,
        should_skip=should_skip,
        skip_reason=skip_reason,
        unsatisfied_ports=unsatisfied_ports,
    )


def get_ready_and_skip_nodes(
    topology: "GraphTopology",
    graph_execution_id: UUID,
) -> tuple[list[UUID], list[tuple[UUID, str]], list[UUID]]:
    """
    Get all nodes that are ready to execute, nodes that should be skipped,
    and nodes that are still pending (waiting for upstream).

    Iterates through all nodes and checks their readiness status.
    A node is considered if it's PENDING (not yet started or completed).

    Args:
        topology: Pre-analyzed graph topology
        graph_execution_id: Current execution context

    Returns:
        Tuple of:
        - List of node IDs ready to execute
        - List of (node_id, skip_reason) for nodes to skip
        - List of node IDs still pending (waiting, neither ready nor skip)
    """
    ready_nodes: list[UUID] = []
    skip_nodes: list[tuple[UUID, str]] = []
    pending_nodes: list[UUID] = []

    # Get all node executions for this graph execution
    node_executions = {
        ne.node_id: ne
        for ne in NodeExecution.no_workspace_objects.filter(
            graph_execution_id=graph_execution_id
        )
    }

    for node_id in topology.nodes:
        node_exec = node_executions.get(node_id)

        # Skip nodes that have already completed or are running
        if node_exec and node_exec.status in (
            NodeExecutionStatus.RUNNING,
            NodeExecutionStatus.SUCCESS,
            NodeExecutionStatus.FAILED,
            NodeExecutionStatus.SKIPPED,
        ):
            continue

        # Check readiness (pass pre-fetched node_executions to avoid N+1 queries)
        result = check_node_readiness(
            node_id, topology, graph_execution_id, node_executions
        )

        if result.should_skip:
            skip_nodes.append(
                (node_id, result.skip_reason or "Upstream dependency failed")
            )
        elif result.is_ready:
            ready_nodes.append(node_id)
        else:
            pending_nodes.append(node_id)

    return ready_nodes, skip_nodes, pending_nodes


def mark_node_skipped(
    node_id: UUID,
    graph_execution_id: UUID,
    reason: str,
) -> NodeExecution:
    """
    Mark a node as skipped in the database.

    Creates or updates the NodeExecution record with SKIPPED status.

    Args:
        node_id: Node to mark as skipped
        graph_execution_id: Current execution context
        reason: Reason for skipping

    Returns:
        The updated NodeExecution record
    """
    node_execution, created = NodeExecution.no_workspace_objects.update_or_create(
        graph_execution_id=graph_execution_id,
        node_id=node_id,
        defaults={
            "status": NodeExecutionStatus.SKIPPED,
            "error_message": reason,
        },
    )
    return node_execution
