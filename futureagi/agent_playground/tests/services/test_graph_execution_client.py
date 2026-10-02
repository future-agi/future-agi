"""Tests for agent playground Temporal client dispatch state handling."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from agent_playground.models import GraphExecution
from agent_playground.models.choices import GraphExecutionStatus
from tfc.temporal.agent_playground.client import (
    GraphExecutionWorkflowStartError,
    start_graph_execution,
    start_graph_execution_async,
)


@pytest.mark.unit
def test_start_graph_execution_marks_failed_when_temporal_start_raises(
    active_graph_version,
):
    with patch(
        "tfc.temporal.agent_playground.client.start_workflow_sync",
        side_effect=TimeoutError("temporal unavailable"),
    ):
        with pytest.raises(GraphExecutionWorkflowStartError) as exc_info:
            start_graph_execution(
                graph_version_id=str(active_graph_version.id),
                input_payload={"topic": "hello"},
            )

    graph_execution = GraphExecution.no_workspace_objects.get(
        id=exc_info.value.graph_execution_id
    )
    assert graph_execution.status == GraphExecutionStatus.FAILED
    assert graph_execution.input_payload == {"topic": "hello"}
    assert graph_execution.started_at is not None
    assert graph_execution.completed_at is not None
    assert "Failed to start graph execution workflow" in graph_execution.error_message
    assert "temporal unavailable" in graph_execution.error_message


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
def test_start_graph_execution_async_marks_failed_when_temporal_start_raises(
    active_graph_version,
):
    with patch(
        "tfc.temporal.agent_playground.client.start_workflow_async",
        new=AsyncMock(side_effect=TimeoutError("temporal unavailable")),
    ):
        with pytest.raises(GraphExecutionWorkflowStartError) as exc_info:
            asyncio.run(
                start_graph_execution_async(
                    graph_version_id=str(active_graph_version.id),
                    input_payload={"topic": "hello"},
                )
            )

    graph_execution = GraphExecution.no_workspace_objects.get(
        id=exc_info.value.graph_execution_id
    )
    assert graph_execution.status == GraphExecutionStatus.FAILED
    assert graph_execution.input_payload == {"topic": "hello"}
    assert graph_execution.started_at is not None
    assert graph_execution.completed_at is not None
    assert "Failed to start graph execution workflow" in graph_execution.error_message
    assert "temporal unavailable" in graph_execution.error_message
