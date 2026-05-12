import uuid
from unittest.mock import patch

import pytest

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import (
    make_dataset,
    make_dataset_with_rows,
    make_eval_template,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def experiment_dataset(tool_context, mock_resource_limit):
    """Dataset with an input column suitable for experiments."""
    ds, cols, rows = make_dataset_with_rows(
        tool_context,
        name="Experiment Dataset",
        columns=[("input", "text"), ("expected", "text")],
        row_data=[
            {"input": "What is AI?", "expected": "AI is artificial intelligence."},
            {"input": "What is ML?", "expected": "ML is machine learning."},
        ],
    )
    return ds, cols, rows


@pytest.fixture
def mock_temporal_experiment():
    """Mock the Temporal experiment workflow start."""
    with patch(
        "tfc.temporal.experiments.start_experiment_workflow",
        return_value="mock-workflow-id",
    ):
        yield


@pytest.fixture
def valid_prompt_config():
    """Minimal valid prompt config for experiments (2 variants)."""
    return [
        {
            "name": "Variant A",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "{{input}}"},
            ],
            "model": ["gpt-4o"],
            "configuration": {"temperature": 0.7, "max_tokens": 500},
        },
        {
            "name": "Variant B",
            "messages": [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "{{input}}"},
            ],
            "model": ["gpt-4o"],
            "configuration": {"temperature": 0.3, "max_tokens": 300},
        },
    ]


# ===================================================================
# READ TOOLS
# ===================================================================


class TestListExperimentsTool:
    def test_list_empty(self, tool_context):
        result = run_tool("list_experiments", {}, tool_context)

        assert not result.is_error
        assert "Experiments (0)" in result.content
        assert result.data["total"] == 0


class TestGetExperimentStatsTool:
    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_experiment_stats",
            {"experiment_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_experiment_id"] is True


class TestGetExperimentResultsTool:
    def test_get_nonexistent_returns_candidates(self, tool_context):
        result = run_tool(
            "get_experiment_results",
            {"experiment_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_experiment_id"] is True


class TestGetExperimentDataTool:
    def test_get_nonexistent_returns_candidates(self, tool_context):
        result = run_tool(
            "get_experiment_data",
            {"experiment_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_experiment_id"] is True


class TestRunExperimentEvalsTool:
    def test_missing_experiment_returns_candidates(self, tool_context):
        result = run_tool("run_experiment_evals", {}, tool_context)

        assert not result.is_error
        assert result.data["requires_experiment_id"] is True

    def test_missing_eval_ids_returns_configured_eval_candidates(
        self, tool_context, experiment_dataset
    ):
        from model_hub.models.evals_metric import UserEvalMetric
        from model_hub.models.experiments import ExperimentsTable

        ds, cols, _rows = experiment_dataset
        template = make_eval_template(tool_context, name="Experiment Quality")
        metric = UserEvalMetric.objects.create(
            name="Quality Metric",
            template=template,
            dataset=ds,
            organization=tool_context.organization,
            workspace=tool_context.workspace,
            user=tool_context.user,
        )
        experiment = ExperimentsTable.objects.create(
            name="Candidate Experiment",
            dataset=ds,
            column=cols[0],
            prompt_config=[],
            user=tool_context.user,
        )
        experiment.user_eval_template_ids.add(metric)

        result = run_tool(
            "run_experiment_evals",
            {"experiment_id": str(experiment.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_eval_template_ids"] is True
        assert result.data["evals"][0]["name"] == "Quality Metric"

    def test_eval_id_accepts_single_string(self, tool_context, experiment_dataset):
        from model_hub.models.evals_metric import UserEvalMetric
        from model_hub.models.experiments import ExperimentsTable

        ds, cols, _rows = experiment_dataset
        template = make_eval_template(tool_context, name="Runnable Quality")
        metric = UserEvalMetric.objects.create(
            name="Runnable Metric",
            template=template,
            dataset=ds,
            organization=tool_context.organization,
            workspace=tool_context.workspace,
            user=tool_context.user,
        )
        experiment = ExperimentsTable.objects.create(
            name="Runnable Experiment",
            dataset=ds,
            column=cols[0],
            prompt_config=[],
            user=tool_context.user,
        )
        experiment.user_eval_template_ids.add(metric)

        with patch("model_hub.views.experiment_runner.ExperimentRunner") as runner:
            runner.return_value.load_experiment.return_value = None
            runner.return_value.empty_or_create_evals_column.return_value = None
            result = run_tool(
                "run_experiment_evals",
                {
                    "experiment_id": str(experiment.id),
                    "eval_template_ids": str(metric.id),
                },
                tool_context,
            )

        assert not result.is_error
        assert result.data["eval_template_ids"] == [str(metric.id)]


class TestCompareExperimentsTool:
    def test_missing_experiment_returns_candidates(self, tool_context):
        result = run_tool(
            "compare_experiments",
            {"experiment_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_experiment_id"] is True


class TestRerunExperimentTool:
    def test_missing_experiment_returns_candidates(self, tool_context):
        result = run_tool("rerun_experiment", {}, tool_context)

        assert not result.is_error
        assert result.data["requires_experiment_id"] is True


# ===================================================================
# WRITE TOOLS
# ===================================================================


class TestCreateExperimentTool:
    def test_create_basic(
        self,
        tool_context,
        experiment_dataset,
        valid_prompt_config,
        mock_temporal_experiment,
        mock_resource_limit,
    ):
        ds, cols, rows = experiment_dataset
        input_col = next(c for c in cols if c.name == "input")

        result = run_tool(
            "create_experiment",
            {
                "name": "Test Experiment",
                "dataset_id": str(ds.id),
                "column_id": str(input_col.id),
                "prompt_config": valid_prompt_config,
            },
            tool_context,
        )

        assert not result.is_error
        assert "Experiment Created" in result.content
        assert result.data["name"] == "Test Experiment"
        assert result.data["variant_count"] == 2
        assert result.data["workflow_started"] is True

    def test_create_nonexistent_dataset(
        self, tool_context, valid_prompt_config, mock_temporal_experiment
    ):
        result = run_tool(
            "create_experiment",
            {
                "name": "Bad Exp",
                "dataset_id": str(uuid.uuid4()),
                "column_id": str(uuid.uuid4()),
                "prompt_config": valid_prompt_config,
            },
            tool_context,
        )

        assert result.is_error

    def test_create_nonexistent_column(
        self,
        tool_context,
        experiment_dataset,
        valid_prompt_config,
        mock_temporal_experiment,
        mock_resource_limit,
    ):
        ds, cols, rows = experiment_dataset

        result = run_tool(
            "create_experiment",
            {
                "name": "Bad Col Exp",
                "dataset_id": str(ds.id),
                "column_id": str(uuid.uuid4()),
                "prompt_config": valid_prompt_config,
            },
            tool_context,
        )

        assert result.is_error

    def test_create_single_variant_rejected(
        self,
        tool_context,
        experiment_dataset,
        mock_temporal_experiment,
        mock_resource_limit,
    ):
        """Need at least 2 variants for comparison."""
        ds, cols, rows = experiment_dataset
        input_col = next(c for c in cols if c.name == "input")

        result = run_tool(
            "create_experiment",
            {
                "name": "Single Var",
                "dataset_id": str(ds.id),
                "column_id": str(input_col.id),
                "prompt_config": [
                    {
                        "name": "Only One",
                        "messages": [{"role": "user", "content": "{{input}}"}],
                        "model": ["gpt-4o"],
                        "configuration": {"temperature": 0.7},
                    }
                ],
            },
            tool_context,
        )

        # Pydantic min_length=2 validation
        assert result.is_error

    def test_create_duplicate_name(
        self,
        tool_context,
        experiment_dataset,
        valid_prompt_config,
        mock_temporal_experiment,
        mock_resource_limit,
    ):
        ds, cols, rows = experiment_dataset
        input_col = next(c for c in cols if c.name == "input")
        params = {
            "name": "Dup Exp",
            "dataset_id": str(ds.id),
            "column_id": str(input_col.id),
            "prompt_config": valid_prompt_config,
        }

        run_tool("create_experiment", params, tool_context)
        result = run_tool("create_experiment", params, tool_context)

        assert result.is_error
        assert "already exists" in result.content

    def test_create_without_temporal(
        self,
        tool_context,
        experiment_dataset,
        valid_prompt_config,
        mock_resource_limit,
    ):
        """Experiment should still be created if Temporal is unavailable."""
        ds, cols, rows = experiment_dataset
        input_col = next(c for c in cols if c.name == "input")

        with patch(
            "tfc.temporal.experiments.start_experiment_workflow",
            side_effect=Exception("Temporal unavailable"),
        ):
            result = run_tool(
                "create_experiment",
                {
                    "name": "No Temporal Exp",
                    "dataset_id": str(ds.id),
                    "column_id": str(input_col.id),
                    "prompt_config": valid_prompt_config,
                },
                tool_context,
            )

        assert not result.is_error
        assert result.data["workflow_started"] is False


class TestDeleteExperimentTool:
    def test_delete_nonexistent(self, tool_context):
        result = run_tool(
            "delete_experiment",
            {"experiment_ids": [str(uuid.uuid4())]},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_experiment_id"] is True

    def test_delete_existing(
        self,
        tool_context,
        experiment_dataset,
        valid_prompt_config,
        mock_temporal_experiment,
        mock_resource_limit,
    ):
        ds, cols, rows = experiment_dataset
        input_col = next(c for c in cols if c.name == "input")

        create_result = run_tool(
            "create_experiment",
            {
                "name": "To Delete",
                "dataset_id": str(ds.id),
                "column_id": str(input_col.id),
                "prompt_config": valid_prompt_config,
            },
            tool_context,
        )
        exp_id = create_result.data["id"]

        result = run_tool(
            "delete_experiment",
            {"experiment_ids": [exp_id]},
            tool_context,
        )

        assert not result.is_error
        assert result.data["deleted"] == 1
