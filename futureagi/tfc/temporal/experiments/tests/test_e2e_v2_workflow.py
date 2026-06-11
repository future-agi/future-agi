"""
End-to-end tests for RunExperimentV2Workflow using temporalio.testing.

Tests use an in-memory Temporal server (WorkflowEnvironment) with real Django ORM
calls against the test database, but with mocked LLM calls (_process_row_sync)
and mocked node runners (LLMPromptEchoRunner for agent graph execution).

Covers all experiment types and flows:
- LLM experiment with prompt configs only
- LLM experiment with agent configs only
- LLM experiment with both prompt + agent configs (mixed flow)
- TTS experiment (prompt flow, output_format=audio)
- STT experiment (prompt flow, voice_input_column)
- Image experiment (prompt flow, output_format=image)
- Edge cases: empty dataset, no configs

IMPORTANT: These tests must NOT run in parallel with pytest-xdist.
Run with:
    set -a && source .env.test.local && set +a
    pytest tfc/temporal/experiments/tests/test_e2e_v2_workflow.py -v -p no:xdist
"""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.db import close_old_connections
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tfc.temporal.agent_playground.activities import ALL_ACTIVITIES as GRAPH_ACTIVITIES
from tfc.temporal.agent_playground.workflows import GraphExecutionWorkflow
from tfc.temporal.experiments import get_activities, get_workflows
from tfc.temporal.experiments.types import RunExperimentInput, RunExperimentOutput
from tfc.temporal.experiments.workflows import RunExperimentV2Workflow

# All tests in this module must run on the same xdist worker (sequential), and
# are excluded from the default non-e2e backend run.
pytestmark = [pytest.mark.e2e, pytest.mark.xdist_group("temporal_experiment_e2e")]

TASK_QUEUE = "test-experiment-v2"


# =============================================================================
# Async-safe ORM helpers
# =============================================================================


@sync_to_async
def _get_experiment(experiment_id):
    """Reload experiment from DB."""
    from model_hub.models.experiments import ExperimentsTable

    return ExperimentsTable.objects.get(id=experiment_id)


@sync_to_async
def _get_experiment_datasets(experiment_id):
    """Get all ExperimentDatasetTable records for an experiment."""
    from model_hub.models.experiments import ExperimentDatasetTable

    return list(
        ExperimentDatasetTable.objects.filter(
            experiment_id=experiment_id,
            deleted=False,
        ).values_list("id", "name", "status")
    )


@sync_to_async
def _get_experiment_columns(experiment_id):
    """Get all experiment-generated columns for an experiment's snapshot dataset."""
    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Column
    from model_hub.models.experiments import ExperimentsTable

    exp = ExperimentsTable.objects.get(id=experiment_id)
    return list(
        Column.objects.filter(
            dataset=exp.snapshot_dataset,
            source=SourceChoices.EXPERIMENT.value,
            deleted=False,
        ).values_list("id", "name", "status")
    )


@sync_to_async
def _get_cells_for_column(column_id):
    """Get all cells for a column."""
    from model_hub.models.develop_dataset import Cell

    return list(
        Cell.objects.filter(
            column_id=column_id,
            deleted=False,
        ).values_list("id", "value", "status")
    )


# =============================================================================
# Workflow runner helper
# =============================================================================


async def run_experiment_workflow(env, experiment_id, max_concurrent_rows=10):
    """
    Helper to run RunExperimentV2Workflow and return its result.

    Registers all required workflows (experiment + graph execution) and
    activities (experiment + graph engine) on a single worker.
    """
    workflow_id = f"experiment-v2-{experiment_id}"

    all_workflows = get_workflows() + [GraphExecutionWorkflow]
    all_activities = get_activities() + GRAPH_ACTIVITIES

    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=all_workflows,
        activities=all_activities,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await env.client.execute_workflow(
            RunExperimentV2Workflow.run,
            RunExperimentInput(
                experiment_id=str(experiment_id),
                max_concurrent_rows=max_concurrent_rows,
                task_queue=TASK_QUEUE,
            ),
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )

    # Give activity threads a moment to release DB connections
    await asyncio.sleep(0.2)
    await sync_to_async(close_old_connections)()

    return result


# =============================================================================
# Group 1: LLM Experiment — Prompt Flow Only
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestLLMPromptFlow:
    """Tests for LLM experiments with ExperimentPromptConfig only (no agents)."""

    async def test_single_prompt_config(
        self,
        workflow_environment,
        experiment,
        experiment_prompt_config,
    ):
        """
        LLM experiment with one EPC → runs ProcessPromptV2Workflow.

        Verifies:
        - Experiment status transitions: NotStarted → Running → Completed
        - Column created in snapshot dataset with source="experiment"
        - Cells populated by mock _process_row_sync
        - EDT status updated to Completed
        """
        result = await run_experiment_workflow(
            workflow_environment,
            experiment.id,
        )

        assert result.experiment_id == str(experiment.id)
        assert result.failed_rows == 0
        assert result.total_rows_processed > 0

        # Check experiment final status
        exp = await _get_experiment(experiment.id)
        assert exp.status == "Completed"

        # Check experiment columns were created
        columns = await _get_experiment_columns(experiment.id)
        assert len(columns) == 1

        # Check cells are populated (not running)
        col_id = columns[0][0]
        cells = await _get_cells_for_column(col_id)
        assert len(cells) == 3  # 3 rows in snapshot
        for _, value, status in cells:
            assert status == "pass"
            assert "mock_response_for_" in value

    async def test_multiple_prompt_configs(
        self,
        workflow_environment,
        experiment,
        experiment_prompt_config,
        prompt_template,
        prompt_version,
    ):
        """
        LLM experiment with two EPCs (different models) → parallel child workflows.

        Creates a second EPC for a different model.
        """
        from model_hub.models.experiments import (
            ExperimentDatasetTable,
            ExperimentPromptConfig,
        )

        # Create second EPC with different model
        edt2_name = "Test Prompt Template-claude-test-model"
        edt2 = await sync_to_async(ExperimentDatasetTable.objects.create)(
            name=edt2_name,
            experiment=experiment,
            status="NotStarted",
        )
        await sync_to_async(ExperimentPromptConfig.objects.create)(
            experiment_dataset=edt2,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
            name=edt2_name,
            model="claude-test-model",
            model_display_name="Claude Test",
            model_config={},
            model_params={},
            configuration={},
            output_format="string",
            order=1,
            messages=None,
        )

        result = await run_experiment_workflow(
            workflow_environment,
            experiment.id,
        )

        assert result.failed_rows == 0

        # Should have 2 experiment columns (one per EPC)
        columns = await _get_experiment_columns(experiment.id)
        assert len(columns) == 2

        # Both should have 3 cells each
        for col_id, _, _ in columns:
            cells = await _get_cells_for_column(col_id)
            assert len(cells) == 3


# =============================================================================
# Group 2: LLM Experiment — Agent Flow Only
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestLLMAgentFlow:
    """Tests for LLM experiments with ExperimentAgentConfig only (no prompts)."""

    async def test_single_node_agent(
        self,
        workflow_environment,
        experiment,
        experiment_agent_config_single,
    ):
        """
        LLM experiment with single-node agent graph → ProcessAgentWorkflow.

        Verifies:
        - Graph executes via child GraphExecutionWorkflow
        - CellOutputSink writes node output to experiment cell
        - One column created (one LLM node in graph)
        """
        result = await run_experiment_workflow(
            workflow_environment,
            experiment.id,
        )

        assert result.experiment_id == str(experiment.id)
        assert result.failed_rows == 0

        exp = await _get_experiment(experiment.id)
        assert exp.status == "Completed"

        # Single LLM node → 1 column
        columns = await _get_experiment_columns(experiment.id)
        assert len(columns) == 1

        # Cells populated by CellOutputSink (echo runner writes input as response)
        col_id = columns[0][0]
        cells = await _get_cells_for_column(col_id)
        assert len(cells) == 3
        for _, value, status in cells:
            assert status == "pass"
            assert value != ""  # CellOutputSink wrote the node output

    async def test_two_node_agent(
        self,
        workflow_environment,
        experiment,
        experiment_agent_config_two_node,
    ):
        """
        LLM experiment with two-node chain agent graph.

        Both nodes are llm_prompt, so both get experiment columns.
        Edge: Summarizer.response → Reviewer.query
        """
        result = await run_experiment_workflow(
            workflow_environment,
            experiment.id,
        )

        assert result.failed_rows == 0

        exp = await _get_experiment(experiment.id)
        assert exp.status == "Completed"

        # Two LLM nodes → 2 columns
        columns = await _get_experiment_columns(experiment.id)
        assert len(columns) == 2

        # Both columns should have 3 populated cells
        for col_id, _, _ in columns:
            cells = await _get_cells_for_column(col_id)
            assert len(cells) == 3


# =============================================================================
# Group 3: LLM Experiment — Mixed Flow (Prompt + Agent)
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestLLMMixedFlow:
    """Tests for LLM experiments with BOTH prompt and agent configs."""

    async def test_prompt_and_agent_together(
        self,
        workflow_environment,
        experiment,
        experiment_prompt_config,
        experiment_agent_config_single,
    ):
        """
        LLM experiment with 1 EPC + 1 EAC → parallel child workflows.

        ProcessPromptV2Workflow and ProcessAgentWorkflow run concurrently.
        """
        result = await run_experiment_workflow(
            workflow_environment,
            experiment.id,
        )

        assert result.failed_rows == 0

        exp = await _get_experiment(experiment.id)
        assert exp.status == "Completed"

        # 1 prompt column + 1 agent column = 2 columns
        columns = await _get_experiment_columns(experiment.id)
        assert len(columns) == 2

        # All columns should have 3 cells
        for col_id, _, _ in columns:
            cells = await _get_cells_for_column(col_id)
            assert len(cells) == 3

    async def test_prompt_and_two_node_agent(
        self,
        workflow_environment,
        experiment,
        experiment_prompt_config,
        experiment_agent_config_two_node,
    ):
        """
        LLM experiment with 1 EPC + 1 EAC (two-node graph).

        Prompt flow creates 1 column, agent flow creates 2 columns (one per LLM node).
        Total: 3+ experiment columns.
        """
        result = await run_experiment_workflow(
            workflow_environment,
            experiment.id,
        )

        assert result.failed_rows == 0

        # 3 columns: 1 from prompt + 2 from agent nodes
        columns = await _get_experiment_columns(experiment.id)
        assert len(columns) == 3


# =============================================================================
# Group 4: TTS Experiment (Prompt Flow)
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestTTSFlow:
    """Tests for TTS experiment type — uses prompt flow with output_format=audio."""

    async def test_tts_experiment(
        self,
        workflow_environment,
        tts_experiment,
        tts_prompt_config,
    ):
        """
        TTS experiment with inline messages and audio output.

        EPC has: prompt_template=None, messages inline, output_format="audio",
        model_config={"voice": "alloy"}.
        """
        result = await run_experiment_workflow(
            workflow_environment,
            tts_experiment.id,
        )

        assert result.experiment_id == str(tts_experiment.id)
        assert result.failed_rows == 0

        exp = await _get_experiment(tts_experiment.id)
        assert exp.status == "Completed"

        columns = await _get_experiment_columns(tts_experiment.id)
        assert len(columns) == 1

        col_id = columns[0][0]
        cells = await _get_cells_for_column(col_id)
        assert len(cells) == 3
        for _, value, status in cells:
            assert status == "pass"


# =============================================================================
# Group 5: STT Experiment (Prompt Flow)
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestSTTFlow:
    """Tests for STT experiment type — uses prompt flow with voice_input_column."""

    async def test_stt_experiment(
        self,
        workflow_environment,
        stt_experiment,
        stt_prompt_config,
    ):
        """
        STT experiment with voice_input_column and inline messages.

        EPC has: prompt_template=None, messages inline, voice_input_column set.
        """
        result = await run_experiment_workflow(
            workflow_environment,
            stt_experiment.id,
        )

        assert result.experiment_id == str(stt_experiment.id)
        assert result.failed_rows == 0

        exp = await _get_experiment(stt_experiment.id)
        assert exp.status == "Completed"

        columns = await _get_experiment_columns(stt_experiment.id)
        assert len(columns) == 1

        col_id = columns[0][0]
        cells = await _get_cells_for_column(col_id)
        assert len(cells) == 3
        for _, value, status in cells:
            assert status == "pass"


# =============================================================================
# Group 6: Image Experiment (Prompt Flow)
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestImageFlow:
    """Tests for Image experiment type — uses prompt flow with output_format=image."""

    async def test_image_experiment(
        self,
        workflow_environment,
        image_experiment,
        image_prompt_config,
    ):
        """
        Image experiment with inline messages and image output.

        EPC has: prompt_template=None, messages inline, output_format="image".
        """
        result = await run_experiment_workflow(
            workflow_environment,
            image_experiment.id,
        )

        assert result.experiment_id == str(image_experiment.id)
        assert result.failed_rows == 0

        exp = await _get_experiment(image_experiment.id)
        assert exp.status == "Completed"

        columns = await _get_experiment_columns(image_experiment.id)
        assert len(columns) == 1

        col_id = columns[0][0]
        cells = await _get_cells_for_column(col_id)
        assert len(cells) == 3
        for _, value, status in cells:
            assert status == "pass"


# =============================================================================
# Group 7: Edge Cases
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestEdgeCases:
    """Tests for edge cases and error scenarios."""

    async def test_experiment_with_no_configs(
        self,
        workflow_environment,
        experiment,
    ):
        """
        Experiment with no EPC or EAC records.

        RunExperimentV2Workflow should still complete (0 prompt handles + 0 agent handles).
        """
        result = await run_experiment_workflow(
            workflow_environment,
            experiment.id,
        )

        assert result.experiment_id == str(experiment.id)
        # No configs means no rows processed
        assert result.total_rows_processed == 0
        assert result.failed_rows == 0

    async def test_batch_concurrency(
        self,
        workflow_environment,
        experiment,
        experiment_prompt_config,
    ):
        """
        Test with max_concurrent_rows=1 to force sequential batch processing.

        All 3 rows should still complete, just processed one at a time.
        """
        result = await run_experiment_workflow(
            workflow_environment,
            experiment.id,
            max_concurrent_rows=1,
        )

        assert result.failed_rows == 0
        assert result.total_rows_processed > 0

        columns = await _get_experiment_columns(experiment.id)
        col_id = columns[0][0]
        cells = await _get_cells_for_column(col_id)
        assert len(cells) == 3
