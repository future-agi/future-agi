"""
Data classes for agent_playground graph execution workflows and activities.

This file is separate from activities.py to avoid Django imports
when Temporal validates workflows in its sandbox.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

# Default used when a graph has no user-set step concurrency.
# Keep this as the single source of truth; callers and models import it.
DEFAULT_MAX_CONCURRENT_NODES = 10
MAX_CONCURRENT_NODES_ERROR = (
    "Step concurrency must be an integer greater than zero."
)


def validate_max_concurrent_nodes(value: Any) -> int:
    """Refuse values the engine cannot honour before a run starts."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(MAX_CONCURRENT_NODES_ERROR)
    return value


# =============================================================================
# Output Sink Config
# =============================================================================


@dataclass
class OutputSinkConfig:
    """Configuration for an output sink."""

    name: str  # Registered sink name (e.g., "cell")
    config: dict[str, Any] = field(default_factory=dict)  # Per-sink config


# =============================================================================
# Activity Input/Output Data Classes
# =============================================================================


@dataclass
class AnalyzeGraphInput:
    """Input for analyzing a graph version."""

    graph_version_id: str


@dataclass
class AnalyzeGraphOutput:
    """Output from analyzing a graph version."""

    graph_version_id: str
    node_ids: list[str]
    start_node_ids: list[str]
    end_node_ids: list[str]
    topological_order: list[str]
    module_node_ids: list[str]  # Nodes that execute child graphs
    status: str  # "SUCCESS" or "FAILED"
    topology_data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class InjectInputsInput:
    """Input for injecting graph inputs."""

    graph_execution_id: str
    graph_version_id: str
    input_payload: dict[str, Any]
    topology_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class InjectInputsOutput:
    """Output from injecting graph inputs."""

    graph_execution_id: str
    all_valid: bool
    injected_count: int
    status: str  # "SUCCESS" or "FAILED"
    error: Optional[str] = None


@dataclass
class ExecuteNodeInput:
    """Input for executing a single node."""

    graph_execution_id: str
    graph_version_id: str
    node_id: str
    output_sink_configs: list[dict[str, Any]] = field(default_factory=list)
    node_sink_overrides: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    topology_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecuteNodeOutput:
    """Output from executing a node."""

    node_id: str
    status: str  # "SUCCESS", "FAILED", "SKIPPED"
    outputs: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class CollectOutputsInput:
    """Input for collecting graph outputs."""

    graph_execution_id: str
    graph_version_id: str
    topology_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectOutputsOutput:
    """Output from collecting graph outputs."""

    graph_execution_id: str
    output_payload: dict[str, Any]
    status: str  # "SUCCESS" or "FAILED"
    error: Optional[str] = None


@dataclass
class GetReadyNodesInput:
    """Input for getting ready/skip nodes."""

    graph_execution_id: str
    graph_version_id: str
    topology_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class GetReadyNodesOutput:
    """Output from getting ready/skip nodes."""

    ready_node_ids: list[str]
    skip_nodes: list[tuple[str, str]]  # List of (node_id, reason)
    pending_node_ids: list[
        str
    ]  # Nodes still waiting (not ready, not skip, not terminal)
    status: str


@dataclass
class MarkNodeSkippedInput:
    """Input for marking a node as skipped."""

    graph_execution_id: str
    node_id: str
    reason: str
    output_sink_configs: list[dict[str, Any]] = field(default_factory=list)
    node_sink_overrides: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    error: bool = False


@dataclass
class MarkNodeSkippedOutput:
    """Output from marking a node as skipped."""

    node_id: str
    status: str


# =============================================================================
# Workflow Input/Output Data Classes
# =============================================================================


@dataclass
class ExecuteGraphInput:
    """Input for executing a graph."""

    graph_execution_id: str
    graph_version_id: str
    input_payload: dict[str, Any]
    max_concurrent_nodes: int = DEFAULT_MAX_CONCURRENT_NODES
    task_queue: str = "tasks_l"
    parent_node_execution_id: Optional[str] = None  # Set when executing as module
    # Output sink configuration
    primary_output_sink: Optional[OutputSinkConfig] = None  # Applied to every node
    output_sinks: list[OutputSinkConfig] = field(
        default_factory=list
    )  # Additional sinks for every node
    node_sink_overrides: dict[str, list[OutputSinkConfig]] = field(
        default_factory=dict
    )  # Per-node overrides keyed by node ID


@dataclass
class ExecuteGraphOutput:
    """Output from executing a graph."""

    graph_execution_id: str
    status: str  # "SUCCESS", "FAILED", "CANCELLED"
    output_payload: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


# =============================================================================
# Module Execution Types
# =============================================================================


@dataclass
class SetupModuleExecutionInput:
    """Input for setting up a module node execution."""

    parent_graph_execution_id: str
    parent_graph_version_id: str
    module_node_id: str
    topology_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SetupModuleExecutionOutput:
    """Output from setting up a module node execution."""

    child_graph_execution_id: str
    child_graph_version_id: str
    module_node_id: str
    status: str  # "SUCCESS" or "FAILED"
    input_payload: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class FinalizeModuleExecutionInput:
    """Input for finalizing a module node execution."""

    parent_graph_execution_id: str
    parent_graph_version_id: str
    child_graph_execution_id: str
    child_graph_version_id: str
    module_node_id: str
    child_status: str  # Status from child workflow
    topology_data: dict[str, Any] = field(default_factory=dict)
    output_sink_configs: list[dict[str, Any]] = field(default_factory=list)
    node_sink_overrides: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class FinalizeModuleExecutionOutput:
    """Output from finalizing a module node execution."""

    module_node_id: str
    status: str  # "SUCCESS", "FAILED", "SKIPPED"
    error: Optional[str] = None


# =============================================================================
# Standalone Node Execution Types
# =============================================================================


@dataclass
class FinalizeGraphExecutionInput:
    """Input for finalizing a graph execution (updating DB status)."""

    graph_execution_id: str
    status: str  # "SUCCESS" or "FAILED"
    output_payload: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class FinalizeGraphExecutionOutput:
    """Output from finalizing a graph execution."""

    graph_execution_id: str
    status: str  # "SUCCESS" or "FAILED"
    error: Optional[str] = None


@dataclass
class ExecuteNodeStandaloneInput:
    """Input for executing a single node outside a graph workflow.

    Used by other Temporal workflows that want to run a node runner
    and store outputs via sinks — without any GraphExecution or ExecutionData.
    """

    template_name: str
    config: dict[str, Any]
    inputs: dict[str, Any]
    organization_id: str
    output_sink_configs: list[dict[str, Any]] = field(default_factory=list)
    node_id: str = ""
    node_name: str = ""
    workspace_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecuteNodeStandaloneOutput:
    """Output from standalone node execution."""

    status: str  # "SUCCESS" or "FAILED"
    outputs: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    sink_results: list[dict[str, Any]] = field(default_factory=list)


__all__ = [
    "DEFAULT_MAX_CONCURRENT_NODES",
    "MAX_CONCURRENT_NODES_ERROR",
    "validate_max_concurrent_nodes",
    # Output sink config
    "OutputSinkConfig",
    # Activity types
    "AnalyzeGraphInput",
    "AnalyzeGraphOutput",
    "InjectInputsInput",
    "InjectInputsOutput",
    "ExecuteNodeInput",
    "ExecuteNodeOutput",
    "CollectOutputsInput",
    "CollectOutputsOutput",
    "GetReadyNodesInput",
    "GetReadyNodesOutput",
    "MarkNodeSkippedInput",
    "MarkNodeSkippedOutput",
    # Module execution types
    "SetupModuleExecutionInput",
    "SetupModuleExecutionOutput",
    "FinalizeModuleExecutionInput",
    "FinalizeModuleExecutionOutput",
    # Graph execution finalization types
    "FinalizeGraphExecutionInput",
    "FinalizeGraphExecutionOutput",
    # Standalone execution types
    "ExecuteNodeStandaloneInput",
    "ExecuteNodeStandaloneOutput",
    # Workflow types
    "ExecuteGraphInput",
    "ExecuteGraphOutput",
]
