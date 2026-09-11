"""
Temporal workflows and activities for agent_playground graph execution.

This module provides:
- GraphExecutionWorkflow: Executes a graph as a DAG with parallel node execution
- Activities for node execution, data routing, and module handling
- Client utilities for starting and managing graph executions

Usage:
    from tfc.temporal.agent_playground import (
        start_graph_execution,
        get_graph_execution_status,
        cancel_graph_execution,
    )

    # Start a graph execution
    execution_id = start_graph_execution(
        graph_version_id="...",
        input_payload={"context": "Hello", "question": "What is AI?"},
    )

    # Check status
    status = get_graph_execution_status(execution_id)

    # Cancel if needed
    cancel_graph_execution(execution_id)
"""

from tfc.temporal.agent_playground.client import (
    GraphExecutionWorkflowStartError,
    cancel_graph_execution,
    cancel_graph_execution_async,
    get_graph_execution_status,
    get_graph_execution_status_async,
    start_graph_execution,
    start_graph_execution_async,
)
from tfc.temporal.agent_playground.types import (
    ExecuteGraphInput,
    ExecuteGraphOutput,
)


def get_workflows():
    """Get all workflow classes for registration (no Django imports)."""
    from tfc.temporal.agent_playground.workflows import ALL_WORKFLOWS

    return ALL_WORKFLOWS


def get_activities():
    """Get all activity functions for registration (has Django imports)."""
    from tfc.temporal.agent_playground.activities import ALL_ACTIVITIES

    return ALL_ACTIVITIES


__all__ = [
    # Client API
    "GraphExecutionWorkflowStartError",
    "start_graph_execution",
    "start_graph_execution_async",
    "get_graph_execution_status",
    "get_graph_execution_status_async",
    "cancel_graph_execution",
    "cancel_graph_execution_async",
    # Types
    "ExecuteGraphInput",
    "ExecuteGraphOutput",
    # Registration helpers
    "get_workflows",
    "get_activities",
]
