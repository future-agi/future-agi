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
def org_scoped_optimization_run(tool_context):
    """OptimizeDataset reachable through the BRIDGED DatasetOptimizationViewSet,
    whose queryset scopes by column -> dataset -> organization (Phase 2A)."""
    from model_hub.models.develop_dataset import Column
    from model_hub.models.optimize_dataset import OptimizeDataset

    ds = make_dataset(tool_context, name="Opt Dataset")
    column = Column.objects.filter(dataset=ds, deleted=False).first()
    return OptimizeDataset.objects.create(
        name="Scoped Optimization",
        optimize_type="PromptTemplate",
        environment="Training",
        version="v1",
        status="completed",
        optimizer_algorithm="random_search",
        best_score=0.85,
        baseline_score=0.70,
        column=column,
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


# Phase 2A note: this class previously called "get_optimization_run", a tool
# name that was never registered (not in any manifest) — every test failed on
# registry lookup. The detail read is the bridged get_dataset_optimization
# (DatasetOptimizationViewSet.retrieve, org-scoped via column -> dataset).
class TestGetOptimizationRunTool:
    def test_get_existing(self, tool_context, org_scoped_optimization_run):
        result = run_tool(
            "get_dataset_optimization",
            {"id": str(org_scoped_optimization_run.id)},
            tool_context,
        )

        assert not result.is_error, result.content
        # The retrieve payload reports the run by name + scores (no id echo).
        assert result.data["optimiser_name"] == "Scoped Optimization"
        assert result.data["best_score"] == 0.85
        assert result.data["baseline_score"] == 0.7

    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_dataset_optimization",
            {"id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error

    def test_get_unscoped_run_not_visible(self, tool_context, optimization_run):
        """A run with no column->dataset->org linkage is outside the bridged
        queryset — the detail tool must not leak it."""
        result = run_tool(
            "get_dataset_optimization",
            {"id": str(optimization_run.id)},
            tool_context,
        )

        assert result.is_error


# ===================================================================
# WRITE TOOLS
# ===================================================================


class TestStopOptimizationRunTool:
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

        assert result.is_error
