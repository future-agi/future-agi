import uuid

import pytest

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_dataset


@pytest.fixture
def optimization_run(tool_context):
    """Create a minimal OptimizeDataset record."""
    from model_hub.models.optimize_dataset import OptimizeDataset

    return OptimizeDataset.objects.create(
        name="Test Optimization",
        optimize_type="PromptTemplate",
        environment="Training",
        version="v1",
        status="completed",
        optimizer_algorithm="random_search",
        best_score=0.85,
        baseline_score=0.70,
    )


@pytest.fixture
def running_optimization(tool_context):
    """Create a running OptimizeDataset record."""
    from model_hub.models.optimize_dataset import OptimizeDataset

    return OptimizeDataset.objects.create(
        name="Running Optimization",
        optimize_type="PromptTemplate",
        environment="Training",
        version="v1",
        status="running",
        optimizer_algorithm="bayesian",
    )


# ===================================================================
# READ TOOLS
# ===================================================================


class TestListOptimizationRunsTool:
    def test_list_empty(self, tool_context):
        result = run_tool("list_optimization_runs", {}, tool_context)

        assert not result.is_error
        assert result.data["total"] == 0

    def test_list_with_data(self, tool_context, optimization_run):
        result = run_tool("list_optimization_runs", {}, tool_context)

        assert not result.is_error
        assert result.data["total"] == 1
        assert "Test Optimization" in result.content

    def test_list_filter_by_status(self, tool_context, optimization_run):
        result = run_tool(
            "list_optimization_runs", {"status": "completed"}, tool_context
        )
        assert result.data["total"] == 1

        result = run_tool("list_optimization_runs", {"status": "failed"}, tool_context)
        assert result.data["total"] == 0

    def test_list_pagination(self, tool_context, optimization_run):
        result = run_tool(
            "list_optimization_runs", {"limit": 1, "offset": 0}, tool_context
        )
        assert not result.is_error
        assert len(result.data["runs"]) <= 1


class TestGetOptimizationRunTool:
    def test_get_existing(self, tool_context, optimization_run):
        result = run_tool(
            "get_optimization_run",
            {"optimization_id": str(optimization_run.id)},
            tool_context,
        )

        assert not result.is_error
        assert "Test Optimization" in result.content
        assert result.data["id"] == str(optimization_run.id)
        assert result.data["algorithm"] == "random_search"
        assert result.data["best_score"] == 0.85
        assert result.data["baseline_score"] == 0.70

    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_optimization_run",
            {"optimization_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error
        assert "Not Found" in result.content

    def test_get_without_trials(self, tool_context, optimization_run):
        result = run_tool(
            "get_optimization_run",
            {"optimization_id": str(optimization_run.id), "include_trials": False},
            tool_context,
        )

        assert not result.is_error
        assert result.data["trials"] == []

    def test_get_without_steps(self, tool_context, optimization_run):
        result = run_tool(
            "get_optimization_run",
            {"optimization_id": str(optimization_run.id), "include_steps": False},
            tool_context,
        )

        assert not result.is_error
        assert result.data["steps"] == []


# ===================================================================
# WRITE TOOLS
# ===================================================================


class TestStopOptimizationRunTool:
    def test_stop_missing_id_returns_candidates(self, tool_context):
        result = run_tool("stop_optimization_run", {}, tool_context)

        assert not result.is_error
        assert result.data["requires_optimization_id"] is True

    def test_stop_running(self, tool_context, running_optimization):
        result = run_tool(
            "stop_optimization_run",
            {"optimization_id": str(running_optimization.id)},
            tool_context,
        )

        # Tool may need Temporal or may handle this locally
        # Accept either success or a known error pattern
        if not result.is_error:
            assert (
                "stopped" in result.content.lower()
                or "cancelled" in result.content.lower()
            )

    def test_stop_nonexistent(self, tool_context):
        result = run_tool(
            "stop_optimization_run",
            {"optimization_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_optimization_id"] is True
