import uuid
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from simulate.constants.agent_prompt_optimiser import (
    AGENT_PROMPT_OPTIMISER_RUN_STEPS,
    TRIAL_TABLE_BASE_COLUMNS,
)
from simulate.utils.agent_prompt_optimiser import (
    _build_lookup_maps,
    _build_trial_row,
    _calculate_percentage_change,
    _create_component_evaluations,
    _create_prompt_trial,
    _create_trial_item_result,
    _get_trial_name,
    _process_eval_data,
    _process_individual_results,
    build_trial_table_data,
    create_agent_prompt_optimiser_run_steps,
    fetch_agent_level_issues_from_run,
    get_agent_prompt_optimiser_run_graph_data,
    get_agent_prompt_optimiser_run_steps,
    update_agent_optimiser_run_step,
)
from simulate.views.agent_prompt_optimiser import (
    OPTIMISER_PARAM_META,
    build_optimiser_parameters,
)


@pytest.mark.unit
class TestCalculatePercentageChange:
    """Tests for _calculate_percentage_change (absolute percentage point change)."""

    def test_positive_change(self):
        """Should calculate positive pp change: 0.75 → 0.9 = +15pp."""
        assert _calculate_percentage_change(0.9, 0.75) == 15.0

    def test_negative_change(self):
        """Should calculate negative pp change: 0.75 → 0.6 = -15pp."""
        assert _calculate_percentage_change(0.6, 0.75) == -15.0

    def test_zero_change(self):
        """Should return 0 when value equals baseline."""
        assert _calculate_percentage_change(0.75, 0.75) == 0.0

    def test_zero_baseline(self):
        """Should calculate change from zero baseline: 0 → 0.5 = +50pp."""
        assert _calculate_percentage_change(0.5, 0) == 50.0

    def test_none_value_returns_none(self):
        """Should return None when value is None."""
        assert _calculate_percentage_change(None, 0.75) is None

    def test_none_baseline_returns_none(self):
        """Should return None when baseline is None."""
        assert _calculate_percentage_change(0.5, None) is None

    def test_both_none_returns_none(self):
        """Should return None when both value and baseline are None."""
        assert _calculate_percentage_change(None, None) is None

    def test_rounds_to_two_decimals(self):
        """Should round result to 2 decimal places: 0.75 → 0.8333 = +8.33pp."""
        assert _calculate_percentage_change(0.8333, 0.75) == 8.33

    def test_large_positive_change(self):
        """Should handle large positive changes: 0.5 → 1.5 = +100pp."""
        assert _calculate_percentage_change(1.5, 0.5) == 100.0

    def test_large_negative_change(self):
        """Should handle large negative changes: 0.5 → 0.25 = -25pp."""
        assert _calculate_percentage_change(0.25, 0.5) == -25.0


@pytest.mark.unit
class TestProcessEvalData:
    """Tests for _process_eval_data function."""

    def test_separates_baseline_from_trials(self):
        """Should correctly separate baseline and trial eval scores."""
        baseline_trial_id = uuid.uuid4()
        trial_id = uuid.uuid4()
        eval_config_id = uuid.uuid4()

        eval_data = [
            {
                "trial_item_result__prompt_trial__id": baseline_trial_id,
                "trial_item_result__prompt_trial__is_baseline": True,
                "eval_config__id": eval_config_id,
                "eval_config__name": "Accuracy",
                "avg_score": 0.75,
            },
            {
                "trial_item_result__prompt_trial__id": trial_id,
                "trial_item_result__prompt_trial__is_baseline": False,
                "eval_config__id": eval_config_id,
                "eval_config__name": "Accuracy",
                "avg_score": 0.85,
            },
        ]

        baseline_scores, trial_scores, eval_ids = _process_eval_data(eval_data)

        assert eval_config_id in baseline_scores
        assert baseline_scores[eval_config_id]["score"] == 0.75
        assert baseline_scores[eval_config_id]["name"] == "Accuracy"
        assert trial_id in trial_scores
        assert eval_config_id in eval_ids

    def test_processes_baseline_evals_only(self):
        """Should handle baseline-only eval data."""
        eval_data = [
            {
                "trial_item_result__prompt_trial__id": uuid.uuid4(),
                "trial_item_result__prompt_trial__is_baseline": True,
                "eval_config__id": uuid.uuid4(),
                "eval_config__name": "Eval 1",
                "avg_score": 0.75,
            }
        ]

        baseline_scores, trial_scores, eval_ids = _process_eval_data(eval_data)

        assert len(baseline_scores) == 1
        assert len(trial_scores) == 0
        assert len(eval_ids) == 0

    def test_processes_non_baseline_evals_only(self):
        """Should handle non-baseline eval data."""
        trial_id = uuid.uuid4()
        eval_config_id = uuid.uuid4()

        eval_data = [
            {
                "trial_item_result__prompt_trial__id": trial_id,
                "trial_item_result__prompt_trial__is_baseline": False,
                "eval_config__id": eval_config_id,
                "eval_config__name": "Eval 1",
                "avg_score": 0.85,
            }
        ]

        baseline_scores, trial_scores, eval_ids = _process_eval_data(eval_data)

        assert len(baseline_scores) == 0
        assert trial_id in trial_scores
        assert eval_config_id in trial_scores[trial_id]
        assert eval_config_id in eval_ids

    def test_handles_multiple_evals_per_trial(self):
        """Should handle multiple eval configs for the same trial."""
        trial_id = uuid.uuid4()
        eval_config_1 = uuid.uuid4()
        eval_config_2 = uuid.uuid4()

        eval_data = [
            {
                "trial_item_result__prompt_trial__id": trial_id,
                "trial_item_result__prompt_trial__is_baseline": False,
                "eval_config__id": eval_config_1,
                "eval_config__name": "Accuracy",
                "avg_score": 0.85,
            },
            {
                "trial_item_result__prompt_trial__id": trial_id,
                "trial_item_result__prompt_trial__is_baseline": False,
                "eval_config__id": eval_config_2,
                "eval_config__name": "Relevance",
                "avg_score": 0.90,
            },
        ]

        baseline_scores, trial_scores, eval_ids = _process_eval_data(eval_data)

        assert trial_id in trial_scores
        assert eval_config_1 in trial_scores[trial_id]
        assert eval_config_2 in trial_scores[trial_id]
        assert len(eval_ids) == 2

    def test_handles_empty_eval_data(self):
        """Should handle empty eval data list."""
        baseline_scores, trial_scores, eval_ids = _process_eval_data([])

        assert baseline_scores == {}
        assert trial_scores == {}
        assert eval_ids == set()


@pytest.mark.unit
class TestFetchAgentLevelIssuesFromRun:
    """Tests for fetch_agent_level_issues_from_run function using mocks."""

    def test_extracts_issues_from_result(self):
        """Should extract issues from optimiser run result."""
        mock_run = MagicMock()
        mock_run.result = {
            "agent_level": {
                "actionable_recommendations": [
                    {"issue": "Response too slow", "priority": "high"},
                    {"issue": "Needs better greeting", "priority": "medium"},
                ]
            }
        }

        issues = fetch_agent_level_issues_from_run(mock_run)

        assert len(issues) == 2
        assert issues[0]["issue"] == "Response too slow"
        assert issues[1]["issue"] == "Needs better greeting"

    def test_returns_empty_list_when_no_result(self):
        """Should return empty list when result is None."""
        mock_run = MagicMock()
        mock_run.result = None

        issues = fetch_agent_level_issues_from_run(mock_run)

        assert issues == []

    def test_returns_empty_list_when_no_agent_level(self):
        """Should return empty list when agent_level key is missing."""
        mock_run = MagicMock()
        mock_run.result = {"other_key": "value"}

        issues = fetch_agent_level_issues_from_run(mock_run)

        assert issues == []

    def test_returns_empty_list_when_no_recommendations(self):
        """Should return empty list when actionable_recommendations is missing."""
        mock_run = MagicMock()
        mock_run.result = {"agent_level": {}}

        issues = fetch_agent_level_issues_from_run(mock_run)

        assert issues == []

    def test_handles_exception_gracefully(self):
        """Should return empty list on exception."""
        mock_run = MagicMock()
        type(mock_run).result = PropertyMock(side_effect=Exception("DB Error"))

        issues = fetch_agent_level_issues_from_run(mock_run)

        assert issues == []


@pytest.mark.unit
class TestGetTrialName:
    """Tests for _get_trial_name function ."""

    def test_returns_baseline_for_baseline_trial(self):
        """Should return 'Baseline' for baseline trials."""
        assert _get_trial_name(0) == "Baseline"

    def test_returns_trial_number_for_non_baseline(self):
        """Should return 'Baseline' for baseline even if trial_number is not 0."""
        assert _get_trial_name(5) == "Trial 5"


@pytest.mark.unit
class TestBuildLookupMaps:
    """Tests for _build_lookup_maps function."""

    @patch("simulate.utils.agent_prompt_optimiser.SimulateEvalConfig")
    @patch("simulate.utils.agent_prompt_optimiser.CallExecution")
    def test_builds_both_maps(self, mock_call_execution, mock_eval_config):
        """Should build both call executions and eval configs maps."""
        mock_ce1 = MagicMock()
        mock_ce1.id = uuid.uuid4()
        mock_ce2 = MagicMock()
        mock_ce2.id = uuid.uuid4()

        mock_ec1 = MagicMock()
        mock_ec1.id = uuid.uuid4()

        mock_call_execution.objects.filter.return_value = [mock_ce1, mock_ce2]
        mock_eval_config.objects.filter.return_value = [mock_ec1]

        mock_run = MagicMock()
        mock_run.test_execution.run_test = MagicMock()

        call_map, eval_map = _build_lookup_maps(mock_run)

        assert len(call_map) == 2
        assert str(mock_ce1.id) in call_map
        assert str(mock_ce2.id) in call_map
        assert len(eval_map) == 1
        assert str(mock_ec1.id) in eval_map

    @patch("simulate.utils.agent_prompt_optimiser.SimulateEvalConfig")
    @patch("simulate.utils.agent_prompt_optimiser.CallExecution")
    def test_returns_empty_maps_when_no_data(
        self, mock_call_execution, mock_eval_config
    ):
        """Should return empty maps when no data exists."""
        mock_call_execution.objects.filter.return_value = []
        mock_eval_config.objects.filter.return_value = []

        mock_run = MagicMock()

        call_map, eval_map = _build_lookup_maps(mock_run)

        assert call_map == {}
        assert eval_map == {}


@pytest.mark.unit
class TestCreatePromptTrial:
    """Tests for _create_prompt_trial function."""

    @patch("simulate.utils.agent_prompt_optimiser.PromptTrial")
    def test_creates_trial_with_correct_data(self, mock_prompt_trial):
        """Should create trial with correct parameters."""
        mock_run = MagicMock()
        trial_data = {
            "prompt": "Test prompt",
            "average_score": 0.85,
        }

        _create_prompt_trial(mock_run, trial_data, trial_number=1, is_baseline=False)

        mock_prompt_trial.objects.create.assert_called_once_with(
            agent_prompt_optimiser_run=mock_run,
            trial_number=1,
            is_baseline=False,
            prompt="Test prompt",
            average_score=0.85,
            metadata={},
        )

    @patch("simulate.utils.agent_prompt_optimiser.PromptTrial")
    def test_trial_data_is_baseline_overrides_parameter(self, mock_prompt_trial):
        """Should use is_baseline from trial_data if present."""
        mock_run = MagicMock()
        trial_data = {
            "prompt": "Test",
            "average_score": 0.5,
            "is_baseline": True,
        }

        _create_prompt_trial(mock_run, trial_data, trial_number=0, is_baseline=False)

        call_args = mock_prompt_trial.objects.create.call_args
        assert call_args[1]["is_baseline"] is True

    @patch("simulate.utils.agent_prompt_optimiser.PromptTrial")
    def test_uses_defaults_for_missing_fields(self, mock_prompt_trial):
        """Should use default values when fields are missing."""
        mock_run = MagicMock()

        _create_prompt_trial(mock_run, {}, trial_number=0, is_baseline=True)

        call_args = mock_prompt_trial.objects.create.call_args
        assert call_args[1]["prompt"] == ""
        assert call_args[1]["average_score"] == 0


@pytest.mark.unit
class TestCreateTrialItemResult:
    """Tests for _create_trial_item_result function."""

    @patch("simulate.utils.agent_prompt_optimiser.TrialItemResult")
    def test_creates_result_with_metadata(self, mock_trial_item):
        """Should create result with input/output from metadata."""
        mock_trial = MagicMock()
        mock_call_exec = MagicMock()
        result_data = {
            "score": 0.9,
            "reason": "Good",
            "metadata": {
                "input": "User input",
                "output": "Agent output",
            },
        }

        _create_trial_item_result(mock_trial, mock_call_exec, result_data)

        mock_trial_item.objects.create.assert_called_once()
        call_args = mock_trial_item.objects.create.call_args[1]
        assert call_args["score"] == 0.9
        assert call_args["reason"] == "Good"
        assert call_args["input_text"] == "User input"
        assert call_args["output_text"] == "Agent output"

    @patch("simulate.utils.agent_prompt_optimiser.TrialItemResult")
    def test_handles_missing_metadata(self, mock_trial_item):
        """Should handle missing metadata."""
        mock_trial = MagicMock()
        mock_call_exec = MagicMock()
        result_data = {"score": 0.5}

        _create_trial_item_result(mock_trial, mock_call_exec, result_data)

        call_args = mock_trial_item.objects.create.call_args[1]
        assert call_args["input_text"] == ""
        assert call_args["output_text"] == ""

    @patch("simulate.utils.agent_prompt_optimiser.TrialItemResult")
    def test_handles_none_metadata(self, mock_trial_item):
        """Should handle None metadata."""
        mock_trial = MagicMock()
        mock_call_exec = MagicMock()
        result_data = {"score": 0.5, "metadata": None}

        _create_trial_item_result(mock_trial, mock_call_exec, result_data)

        call_args = mock_trial_item.objects.create.call_args[1]
        assert call_args["input_text"] == ""
        assert call_args["output_text"] == ""


@pytest.mark.unit
class TestCreateComponentEvaluations:
    """Tests for _create_component_evaluations function."""

    @patch("simulate.utils.agent_prompt_optimiser.ComponentEvaluation")
    def test_creates_evaluations_for_each_config(self, mock_comp_eval):
        """Should create evaluation for each eval config in metadata."""
        mock_trial_item = MagicMock()
        eval_config_id = str(uuid.uuid4())
        mock_eval_config = MagicMock()
        eval_configs_map = {eval_config_id: mock_eval_config}

        metadata = {
            "component_evals": {
                eval_config_id: {"score": 0.9, "reason": "Good"},
            }
        }

        _create_component_evaluations(mock_trial_item, metadata, eval_configs_map)

        mock_comp_eval.objects.create.assert_called_once()
        call_args = mock_comp_eval.objects.create.call_args[1]
        assert call_args["score"] == 0.9
        assert call_args["reason"] == "Good"

    @patch("simulate.utils.agent_prompt_optimiser.ComponentEvaluation")
    def test_skips_unknown_eval_configs(self, mock_comp_eval):
        """Should skip eval configs not in the map."""
        mock_trial_item = MagicMock()
        unknown_id = str(uuid.uuid4())
        metadata = {
            "component_evals": {
                unknown_id: {"score": 0.9},
            }
        }

        _create_component_evaluations(mock_trial_item, metadata, {})

        mock_comp_eval.objects.create.assert_not_called()

    @patch("simulate.utils.agent_prompt_optimiser.ComponentEvaluation")
    def test_handles_none_metadata(self, mock_comp_eval):
        """Should handle None metadata."""
        mock_trial_item = MagicMock()

        _create_component_evaluations(mock_trial_item, None, {})

        mock_comp_eval.objects.create.assert_not_called()

    @patch("simulate.utils.agent_prompt_optimiser.ComponentEvaluation")
    def test_handles_non_dict_component_evals(self, mock_comp_eval):
        """Should skip when component_evals is not a dict."""
        mock_trial_item = MagicMock()
        metadata = {"component_evals": "not a dict"}

        _create_component_evaluations(mock_trial_item, metadata, {})

        mock_comp_eval.objects.create.assert_not_called()

    @patch("simulate.utils.agent_prompt_optimiser.ComponentEvaluation")
    def test_handles_non_dict_eval_data(self, mock_comp_eval):
        """Should use defaults when eval_data is not a dict."""
        mock_trial_item = MagicMock()
        eval_config_id = str(uuid.uuid4())
        mock_eval_config = MagicMock()
        eval_configs_map = {eval_config_id: mock_eval_config}

        metadata = {
            "component_evals": {
                eval_config_id: "not a dict",
            }
        }

        _create_component_evaluations(mock_trial_item, metadata, eval_configs_map)

        call_args = mock_comp_eval.objects.create.call_args[1]
        assert call_args["score"] == 0
        assert call_args["reason"] == ""


@pytest.mark.unit
class TestProcessIndividualResults:
    """Tests for _process_individual_results function."""

    @patch("simulate.utils.agent_prompt_optimiser._create_component_evaluations")
    @patch("simulate.utils.agent_prompt_optimiser._create_trial_item_result")
    def test_processes_each_result(self, mock_create_item, mock_create_evals):
        """Should process each individual result."""
        mock_trial = MagicMock()
        call_exec_id = str(uuid.uuid4())
        mock_call_exec = MagicMock()
        call_executions_map = {call_exec_id: mock_call_exec}

        individual_results = {
            call_exec_id: {"score": 0.9, "metadata": {}},
        }

        _process_individual_results(
            mock_trial, individual_results, call_executions_map, {}
        )

        mock_create_item.assert_called_once()
        mock_create_evals.assert_called_once()

    @patch("simulate.utils.agent_prompt_optimiser._create_trial_item_result")
    def test_skips_unknown_call_executions(self, mock_create_item):
        """Should skip results for unknown call executions."""
        mock_trial = MagicMock()
        unknown_id = str(uuid.uuid4())

        individual_results = {
            unknown_id: {"score": 0.9},
        }

        _process_individual_results(mock_trial, individual_results, {}, {})

        mock_create_item.assert_not_called()

    @patch("simulate.utils.agent_prompt_optimiser._create_trial_item_result")
    def test_handles_non_dict_results(self, mock_create_item):
        """Should handle non-dict individual_results."""
        mock_trial = MagicMock()

        _process_individual_results(mock_trial, "not a dict", {}, {})

        mock_create_item.assert_not_called()


@pytest.mark.unit
class TestCreateAgentPromptOptimiserRunSteps:
    """Tests for create_agent_prompt_optimiser_run_steps function."""

    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStep")
    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRun")
    def test_creates_all_steps(self, mock_run_model, mock_step_model):
        """Should create all defined steps."""
        mock_run = MagicMock()
        mock_run_model.objects.get.return_value = mock_run

        run_id = str(uuid.uuid4())
        create_agent_prompt_optimiser_run_steps(run_id)

        mock_step_model.objects.bulk_create.assert_called_once()
        created_steps = mock_step_model.objects.bulk_create.call_args[0][0]
        assert len(created_steps) == len(AGENT_PROMPT_OPTIMISER_RUN_STEPS)

    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStep")
    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRun")
    def test_steps_have_sequential_numbers(self, mock_run_model, mock_step_model):
        """Should assign sequential step numbers."""
        mock_run = MagicMock()
        mock_run_model.objects.get.return_value = mock_run

        # Capture actual step_number values passed to constructor
        captured_step_numbers = []

        def capture_step(**kwargs):
            captured_step_numbers.append(kwargs.get("step_number"))
            return MagicMock(**kwargs)

        mock_step_model.side_effect = capture_step

        run_id = str(uuid.uuid4())
        create_agent_prompt_optimiser_run_steps(run_id)

        assert captured_step_numbers == list(
            range(1, len(AGENT_PROMPT_OPTIMISER_RUN_STEPS) + 1)
        )


@pytest.mark.unit
class TestGetAgentPromptOptimiserRunSteps:
    """Tests for get_agent_prompt_optimiser_run_steps function."""

    @patch(
        "simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStepSerializer"
    )
    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStep")
    def test_returns_serialized_steps(self, mock_step_model, mock_serializer):
        """Should return serialized step data."""
        mock_step_model.objects.filter.return_value.order_by.return_value = []
        mock_serializer.return_value.data = [{"id": "1", "name": "Step 1"}]

        run_id = str(uuid.uuid4())
        result = get_agent_prompt_optimiser_run_steps(run_id)

        assert result == [{"id": "1", "name": "Step 1"}]

    @patch(
        "simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStepSerializer"
    )
    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStep")
    def test_orders_by_step_number(self, mock_step_model, mock_serializer):
        """Should order steps by step_number."""
        run_id = str(uuid.uuid4())
        get_agent_prompt_optimiser_run_steps(run_id)

        mock_step_model.objects.filter.return_value.order_by.assert_called_with(
            "step_number"
        )

    @patch(
        "simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStepSerializer"
    )
    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStep")
    def test_returns_multiple_steps_in_order(self, mock_step_model, mock_serializer):
        """Should return multiple steps preserving order from order_by."""
        mock_step1 = MagicMock(step_number=1)
        mock_step2 = MagicMock(step_number=2)
        mock_step3 = MagicMock(step_number=3)

        # Simulate ordered queryset result
        ordered_steps = [mock_step1, mock_step2, mock_step3]
        mock_step_model.objects.filter.return_value.order_by.return_value = (
            ordered_steps
        )

        mock_serializer.return_value.data = [
            {"id": "1", "step_number": 1, "name": "Step 1"},
            {"id": "2", "step_number": 2, "name": "Step 2"},
            {"id": "3", "step_number": 3, "name": "Step 3"},
        ]

        run_id = str(uuid.uuid4())
        result = get_agent_prompt_optimiser_run_steps(run_id)

        # Verify serializer receives the ordered steps
        mock_serializer.assert_called_once_with(ordered_steps, many=True)

        # Verify result maintains order
        assert len(result) == 3
        assert result[0]["step_number"] == 1
        assert result[1]["step_number"] == 2
        assert result[2]["step_number"] == 3

    @patch(
        "simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStepSerializer"
    )
    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStep")
    def test_returns_empty_list_when_no_steps(self, mock_step_model, mock_serializer):
        """Should return empty list when no steps exist."""
        mock_step_model.objects.filter.return_value.order_by.return_value = []
        mock_serializer.return_value.data = []

        run_id = str(uuid.uuid4())
        result = get_agent_prompt_optimiser_run_steps(run_id)

        assert result == []

    @patch(
        "simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStepSerializer"
    )
    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStep")
    def test_filters_by_run_id(self, mock_step_model, mock_serializer):
        """Should filter steps by the provided run_id."""
        run_id = str(uuid.uuid4())
        get_agent_prompt_optimiser_run_steps(run_id)

        mock_step_model.objects.filter.assert_called_once_with(
            agent_prompt_optimiser_run_id=run_id
        )


@pytest.mark.unit
class TestUpdateAgentOptimiserRunStep:
    """Tests for update_agent_optimiser_run_step function."""

    def test_handles_none_steps(self):
        """Should handle None steps list."""
        update_agent_optimiser_run_step(None, step_number=1, status="running")
        # Should not raise

    def test_handles_empty_steps(self):
        """Should handle empty steps list."""
        update_agent_optimiser_run_step([], step_number=1, status="running")
        # Should not raise

    def test_handles_missing_step_number(self):
        """Should handle when step_number doesn't exist in steps."""
        steps = [{"id": "1", "step_number": 1}]
        update_agent_optimiser_run_step(steps, step_number=99, status="running")
        # Should not raise

    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStep")
    def test_updates_status(self, mock_step_model):
        """Should update step status."""
        step_id = str(uuid.uuid4())
        mock_step = MagicMock()
        mock_step_model.objects.get.return_value = mock_step

        steps = [{"id": step_id, "step_number": 1}]
        update_agent_optimiser_run_step(steps, step_number=1, status="running")

        assert mock_step.status == "running"
        mock_step.save.assert_called_once()

    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStep")
    def test_updates_name(self, mock_step_model):
        """Should update step name."""
        step_id = str(uuid.uuid4())
        mock_step = MagicMock()
        mock_step_model.objects.get.return_value = mock_step

        steps = [{"id": step_id, "step_number": 1}]
        update_agent_optimiser_run_step(steps, step_number=1, name="New Name")

        assert mock_step.name == "New Name"
        mock_step.save.assert_called_once()

    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStep")
    def test_appends_error_to_description(self, mock_step_model):
        """Should append error to existing description."""
        step_id = str(uuid.uuid4())
        mock_step = MagicMock()
        mock_step.description = "Original description"
        mock_step_model.objects.get.return_value = mock_step

        steps = [{"id": step_id, "step_number": 1}]
        update_agent_optimiser_run_step(steps, step_number=1, error="Something failed")

        assert "Original description" in mock_step.description
        assert "Error: Something failed" in mock_step.description

    @patch("simulate.utils.agent_prompt_optimiser.AgentPromptOptimiserRunStep")
    def test_handles_step_not_found(self, mock_step_model):
        """Should handle DoesNotExist exception."""
        from simulate.models import AgentPromptOptimiserRunStep

        mock_step_model.objects.get.side_effect = (
            AgentPromptOptimiserRunStep.DoesNotExist
        )
        mock_step_model.DoesNotExist = AgentPromptOptimiserRunStep.DoesNotExist

        steps = [{"id": str(uuid.uuid4()), "step_number": 1}]
        update_agent_optimiser_run_step(steps, step_number=1, status="running")
        # Should not raise


@pytest.mark.unit
class TestBuildTrialRow:
    """Tests for _build_trial_row function."""

    def test_builds_basic_row(self):
        """Should build row with basic trial data."""
        trial_id = uuid.uuid4()
        mock_trial = MagicMock()
        mock_trial.id = trial_id
        mock_trial.trial_number = 1
        mock_trial.average_score = 0.85
        mock_trial.prompt = "Test prompt"

        row = _build_trial_row(mock_trial, 0.75, {}, {}, trial_id)

        assert row["id"] == str(trial_id)
        assert row["trial"] == "Trial 1"
        assert row["score"] == 0.85
        assert row["prompt"] == "Test prompt"
        assert row["is_best"] is True

    def test_calculates_percentage_change(self):
        """Should calculate score percentage point change."""
        mock_trial = MagicMock()
        mock_trial.id = uuid.uuid4()
        mock_trial.trial_number = 1
        mock_trial.average_score = 0.9
        mock_trial.prompt = ""

        row = _build_trial_row(mock_trial, 0.75, {}, {}, None)

        assert row["score_percentage_change"] == 15.0

    def test_includes_eval_scores(self):
        """Should include eval scores in row."""
        trial_id = uuid.uuid4()
        eval_config_id = uuid.uuid4()

        mock_trial = MagicMock()
        mock_trial.id = trial_id
        mock_trial.trial_number = 1
        mock_trial.average_score = 0.85
        mock_trial.prompt = ""

        baseline_eval_scores = {eval_config_id: {"name": "Accuracy", "score": 0.7}}
        trial_eval_scores = {
            trial_id: {eval_config_id: {"name": "Accuracy", "score": 0.84}}
        }

        row = _build_trial_row(
            mock_trial, 0.75, baseline_eval_scores, trial_eval_scores, None
        )

        assert str(eval_config_id) in row
        assert row[str(eval_config_id)]["score"] == 0.84
        assert row[str(eval_config_id)]["percentage_change"] == 14.0

    def test_handles_none_average_score(self):
        """Should handle None average_score."""
        mock_trial = MagicMock()
        mock_trial.id = uuid.uuid4()
        mock_trial.trial_number = 1
        mock_trial.average_score = None
        mock_trial.prompt = ""

        row = _build_trial_row(mock_trial, 0.75, {}, {}, None)

        assert row["score"] is None
        assert row["score_percentage_change"] is None


@pytest.mark.unit
class TestBuildTrialTableData:
    """Tests for build_trial_table_data function."""

    @patch("simulate.utils.agent_prompt_optimiser._process_eval_data")
    @patch("simulate.utils.agent_prompt_optimiser._fetch_all_eval_data")
    def test_excludes_baseline_from_table_data(
        self, mock_fetch_evals, mock_process_evals
    ):
        """Should exclude baseline trial from table data."""
        mock_fetch_evals.return_value = []
        mock_process_evals.return_value = ({}, {}, set())

        baseline = MagicMock()
        baseline.is_baseline = True
        baseline.trial_number = 0

        trial1 = MagicMock()
        trial1.id = uuid.uuid4()
        trial1.is_baseline = False
        trial1.trial_number = 1
        trial1.average_score = 0.85
        trial1.prompt = ""

        mock_run = MagicMock()
        mock_run.trials.all.return_value = [baseline, trial1]

        table_data, _ = build_trial_table_data(mock_run)

        assert len(table_data) == 1
        assert table_data[0]["trial"] == "Trial 1"

    @patch("simulate.utils.agent_prompt_optimiser._process_eval_data")
    @patch("simulate.utils.agent_prompt_optimiser._fetch_all_eval_data")
    def test_returns_base_columns(self, mock_fetch_evals, mock_process_evals):
        """Should include base columns in config."""
        mock_fetch_evals.return_value = []
        mock_process_evals.return_value = ({}, {}, set())

        mock_run = MagicMock()
        mock_run.trials.all.return_value = []

        _, column_config = build_trial_table_data(mock_run)

        base_ids = [col["id"] for col in TRIAL_TABLE_BASE_COLUMNS]
        config_ids = [col["id"] for col in column_config]

        for base_id in base_ids:
            assert base_id in config_ids

    @patch("simulate.utils.agent_prompt_optimiser._process_eval_data")
    @patch("simulate.utils.agent_prompt_optimiser._fetch_all_eval_data")
    def test_identifies_best_trial(self, mock_fetch_evals, mock_process_evals):
        """Should mark the best scoring trial."""
        mock_fetch_evals.return_value = []
        mock_process_evals.return_value = ({}, {}, set())

        baseline = MagicMock()
        baseline.is_baseline = True
        baseline.average_score = 0.7

        trial1 = MagicMock()
        trial1.id = uuid.uuid4()
        trial1.is_baseline = False
        trial1.trial_number = 1
        trial1.average_score = 0.80
        trial1.prompt = ""

        trial2 = MagicMock()
        trial2.id = uuid.uuid4()
        trial2.is_baseline = False
        trial2.trial_number = 2
        trial2.average_score = 0.90
        trial2.prompt = ""

        mock_run = MagicMock()
        mock_run.trials.all.return_value = [baseline, trial1, trial2]

        table_data, _ = build_trial_table_data(mock_run)

        best_count = sum(1 for row in table_data if row["is_best"])
        assert best_count == 1

        best_row = next(row for row in table_data if row["is_best"])
        assert best_row["id"] == str(trial2.id)


@pytest.mark.unit
class TestGetAgentPromptOptimiserRunGraphData:
    """Tests for get_agent_prompt_optimiser_run_graph_data function."""

    @patch("simulate.utils.agent_prompt_optimiser.ComponentEvaluation")
    def test_returns_graph_data_for_trials(self, mock_comp_eval):
        """Should return graph data keyed by eval_config_id."""
        trial1_id = uuid.uuid4()
        trial2_id = uuid.uuid4()
        eval_config_id = uuid.uuid4()

        mock_comp_eval.objects.filter.return_value.values.return_value.annotate.return_value = [
            {
                "trial_item_result__prompt_trial__id": trial1_id,
                "trial_item_result__prompt_trial__trial_number": 0,
                "eval_config__id": eval_config_id,
                "eval_config__name": "Accuracy",
                "avg_score": 0.75,
            },
            {
                "trial_item_result__prompt_trial__id": trial2_id,
                "trial_item_result__prompt_trial__trial_number": 1,
                "eval_config__id": eval_config_id,
                "eval_config__name": "Accuracy",
                "avg_score": 0.85,
            },
        ]

        trial1 = MagicMock()
        trial1.id = trial1_id
        trial1.trial_number = 0

        trial2 = MagicMock()
        trial2.id = trial2_id
        trial2.trial_number = 1

        mock_run = MagicMock()
        mock_run.trials.all.return_value.order_by.return_value = [trial1, trial2]

        result = get_agent_prompt_optimiser_run_graph_data(mock_run)

        assert len(result) == 1  # One eval_config
        assert str(eval_config_id) in result
        assert result[str(eval_config_id)]["name"] == "Accuracy"
        assert len(result[str(eval_config_id)]["evaluations"]) == 2
        assert result[str(eval_config_id)]["evaluations"][0]["trial_name"] == "Baseline"
        assert result[str(eval_config_id)]["evaluations"][1]["trial_name"] == "Trial 1"

    @patch("simulate.utils.agent_prompt_optimiser.ComponentEvaluation")
    def test_sorts_by_trial_number(self, mock_comp_eval):
        """Should sort evaluations within each eval_config by trial number."""
        trial1_id = uuid.uuid4()
        trial2_id = uuid.uuid4()
        eval_config_id = uuid.uuid4()

        # Return in wrong order
        mock_comp_eval.objects.filter.return_value.values.return_value.annotate.return_value = [
            {
                "trial_item_result__prompt_trial__id": trial2_id,
                "trial_item_result__prompt_trial__trial_number": 2,
                "eval_config__id": eval_config_id,
                "eval_config__name": "Accuracy",
                "avg_score": 0.90,
            },
            {
                "trial_item_result__prompt_trial__id": trial1_id,
                "trial_item_result__prompt_trial__trial_number": 1,
                "eval_config__id": eval_config_id,
                "eval_config__name": "Accuracy",
                "avg_score": 0.85,
            },
        ]

        trial1 = MagicMock()
        trial1.id = trial1_id
        trial1.trial_number = 1

        trial2 = MagicMock()
        trial2.id = trial2_id
        trial2.trial_number = 2

        mock_run = MagicMock()
        mock_run.trials.all.return_value.order_by.return_value = [trial1, trial2]

        result = get_agent_prompt_optimiser_run_graph_data(mock_run)

        evaluations = result[str(eval_config_id)]["evaluations"]
        trial_numbers = [e["trial_number"] for e in evaluations]
        assert trial_numbers == sorted(trial_numbers)

    @patch("simulate.utils.agent_prompt_optimiser.ComponentEvaluation")
    def test_returns_empty_for_no_trials(self, mock_comp_eval):
        """Should return empty dict when no evaluations exist."""
        mock_comp_eval.objects.filter.return_value.values.return_value.annotate.return_value = []

        mock_run = MagicMock()
        mock_run.trials.all.return_value.order_by.return_value = []

        result = get_agent_prompt_optimiser_run_graph_data(mock_run)

        assert result == {}

    @patch("simulate.utils.agent_prompt_optimiser.ComponentEvaluation")
    def test_includes_evaluations(self, mock_comp_eval):
        """Should include evaluations in graph data with correct structure."""
        trial_id = uuid.uuid4()
        eval_config_id = uuid.uuid4()

        mock_comp_eval.objects.filter.return_value.values.return_value.annotate.return_value = [
            {
                "trial_item_result__prompt_trial__id": trial_id,
                "trial_item_result__prompt_trial__trial_number": 1,
                "eval_config__id": eval_config_id,
                "eval_config__name": "Accuracy",
                "avg_score": 0.85,
            }
        ]

        trial = MagicMock()
        trial.id = trial_id
        trial.trial_number = 1

        mock_run = MagicMock()
        mock_run.trials.all.return_value.order_by.return_value = [trial]

        result = get_agent_prompt_optimiser_run_graph_data(mock_run)

        assert str(eval_config_id) in result
        assert result[str(eval_config_id)]["name"] == "Accuracy"
        assert len(result[str(eval_config_id)]["evaluations"]) == 1
        eval_item = result[str(eval_config_id)]["evaluations"][0]
        assert eval_item["trial_id"] == str(trial_id)
        assert eval_item["trial_number"] == 1
        assert eval_item["trial_name"] == "Trial 1"
        assert eval_item["score"] == 0.85


@pytest.mark.unit
class TestBuildOptimiserParameters:
    """Tests for build_optimiser_parameters function."""

    def test_protegi_returns_all_params(self):
        """Should return all protegi parameters with labels."""
        config = {
            "beam_size": 2,
            "num_gradients": 4,
            "errors_per_gradient": 4,
            "prompts_per_gradient": 4,
            "num_rounds": 8,
        }
        result = build_optimiser_parameters("protegi", config)
        assert len(result) == 5
        keys = [p["key"] for p in result]
        assert "beam_size" in keys
        assert "num_gradients" in keys
        assert "errors_per_gradient" in keys
        assert "prompts_per_gradient" in keys
        assert "num_rounds" in keys

    def test_params_have_labels_and_descriptions(self):
        """Should include human-readable labels and descriptions."""
        config = {"num_gradients": 4, "beam_size": 2}
        result = build_optimiser_parameters("protegi", config)
        for param in result:
            assert "label" in param
            assert "description" in param
            assert "key" in param
            assert "value" in param
            assert param["label"] != ""
            assert param["description"] != ""

    def test_param_values_match_config(self):
        """Should return correct values from configuration."""
        config = {"beam_size": 3, "num_rounds": 10}
        result = build_optimiser_parameters("protegi", config)
        param_map = {p["key"]: p["value"] for p in result}
        assert param_map["beam_size"] == 3
        assert param_map["num_rounds"] == 10

    def test_random_search_params(self):
        """Should return random_search parameters."""
        config = {"num_variations": 5}
        result = build_optimiser_parameters("random_search", config)
        assert len(result) == 1
        assert result[0]["key"] == "num_variations"
        assert result[0]["value"] == 5
        assert result[0]["label"] == "No. of variations"

    def test_bayesian_params(self):
        """Should return bayesian parameters."""
        config = {"min_examples": 10, "max_examples": 50, "n_trials": 20}
        result = build_optimiser_parameters("bayesian", config)
        assert len(result) == 3
        keys = [p["key"] for p in result]
        assert "min_examples" in keys
        assert "max_examples" in keys
        assert "n_trials" in keys

    def test_empty_configuration_returns_empty(self):
        """Should return empty list for None/empty configuration."""
        assert build_optimiser_parameters("protegi", None) == []
        assert build_optimiser_parameters("protegi", {}) == []

    def test_none_optimiser_type_returns_empty(self):
        """Should return empty list for None optimiser type."""
        assert build_optimiser_parameters(None, {"beam_size": 2}) == []

    def test_unknown_optimiser_type_returns_empty(self):
        """Should return empty list for unknown optimiser type."""
        assert build_optimiser_parameters("unknown_type", {"beam_size": 2}) == []

    def test_skips_none_values_in_config(self):
        """Should skip config keys with None values."""
        config = {"beam_size": 2, "num_gradients": None, "num_rounds": 8}
        result = build_optimiser_parameters("protegi", config)
        keys = [p["key"] for p in result]
        assert "num_gradients" not in keys
        assert "beam_size" in keys
        assert "num_rounds" in keys

    def test_case_insensitive_optimiser_type(self):
        """Should handle uppercase optimiser type."""
        config = {"beam_size": 2, "num_rounds": 5}
        result = build_optimiser_parameters("PROTEGI", config)
        assert len(result) >= 2

    def test_all_optimiser_types_have_meta(self):
        """Every key in OPTIMISER_REQUIRED_CONFIG_KEYS should have metadata."""
        from simulate.serializers.agent_prompt_optimiser import (
            OPTIMISER_REQUIRED_CONFIG_KEYS,
        )

        for opt_type, keys in OPTIMISER_REQUIRED_CONFIG_KEYS.items():
            for key in keys:
                assert key in OPTIMISER_PARAM_META, (
                    f"Missing metadata for {opt_type}.{key}"
                )
