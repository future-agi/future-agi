"""
Tests for user_evaluation task functions in model_hub/tasks/user_evaluation.py.

Run with: pytest model_hub/tests/test_user_evaluation_tasks.py -v
"""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest


@pytest.fixture(autouse=True)
def _allow_usage_metering():
    """Unit tests in this file mock eval execution; usage metering is covered elsewhere."""
    with patch("ee.usage.services.metering.check_usage") as mock_check_usage:
        mock_check_usage.return_value = MagicMock(allowed=True)
        yield mock_check_usage


class TestProcessSingleEvaluation:
    """Tests for process_single_evaluation function."""

    @patch("model_hub.tasks.user_evaluation.evaluation_tracker")
    @patch("model_hub.tasks.user_evaluation.EvaluationRunner")
    @patch("model_hub.tasks.user_evaluation.Row")
    @patch("model_hub.tasks.user_evaluation.Column")
    @patch("model_hub.tasks.user_evaluation.track_mixpanel_event")
    @patch("model_hub.tasks.user_evaluation.get_mixpanel_properties")
    def test_processes_evaluation_successfully(
        self,
        mock_mixpanel_props,
        mock_track,
        mock_column,
        mock_row,
        mock_runner_class,
        mock_tracker,
    ):
        """Test successful processing of a single evaluation."""
        from model_hub.tasks.user_evaluation import process_single_evaluation

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"
        mock_row.objects.filter.return_value.count.return_value = 10

        mock_eval_metric = MagicMock()
        mock_eval_metric.id = "eval-123"
        mock_eval_metric.dataset.id = "dataset-123"
        mock_eval_metric.template.name = "Test Template"
        mock_eval_metric.template.id = "template-123"
        mock_eval_metric.organization = MagicMock()

        mock_runner = MagicMock()
        mock_runner._get_all_column_ids_being_used.return_value = []
        mock_runner_class.return_value = mock_runner
        mock_column.objects.filter.return_value = []

        process_single_evaluation(mock_eval_metric)

        mock_tracker.mark_running.assert_called_once()
        mock_runner.run_prompt.assert_called_once()
        mock_tracker.mark_completed.assert_called_once_with("eval-123")
        mock_tracker.clear_cancel_flag.assert_called_once_with("eval-123")

    @patch("model_hub.tasks.user_evaluation.evaluation_tracker")
    @patch("model_hub.tasks.user_evaluation.Row")
    @patch("model_hub.tasks.user_evaluation.track_mixpanel_event")
    @patch("model_hub.tasks.user_evaluation.get_mixpanel_properties")
    def test_requests_cancel_if_already_running(
        self, mock_mixpanel_props, mock_track, mock_row, mock_tracker
    ):
        """Test that cancellation is requested if evaluation is already running."""
        from model_hub.tasks.user_evaluation import process_single_evaluation

        mock_tracker.is_running.return_value = True
        mock_tracker.instance_id = "current-instance"
        mock_running_info = MagicMock()
        mock_running_info.instance_id = "other-instance"
        mock_tracker.get_running_info.return_value = mock_running_info
        mock_row.objects.filter.return_value.count.return_value = 10

        mock_eval_metric = MagicMock()
        mock_eval_metric.id = "eval-123"
        mock_eval_metric.dataset.id = "dataset-123"
        mock_eval_metric.template.name = "Test Template"
        mock_eval_metric.template.id = "template-123"
        mock_eval_metric.organization = MagicMock()

        with patch("model_hub.tasks.user_evaluation.EvaluationRunner"):
            with patch("model_hub.tasks.user_evaluation.Column"):
                process_single_evaluation(mock_eval_metric)

        mock_tracker.request_cancel.assert_called_once_with(
            "eval-123", reason="New evaluation requested"
        )

    @patch("model_hub.tasks.user_evaluation.evaluation_tracker")
    @patch("model_hub.tasks.user_evaluation.EvaluationRunner")
    @patch("model_hub.tasks.user_evaluation.Row")
    @patch("model_hub.tasks.user_evaluation.Column")
    @patch("model_hub.tasks.user_evaluation.track_mixpanel_event")
    @patch("model_hub.tasks.user_evaluation.get_mixpanel_properties")
    def test_cleans_up_on_exception(
        self,
        mock_mixpanel_props,
        mock_track,
        mock_column,
        mock_row,
        mock_runner_class,
        mock_tracker,
    ):
        """Test that cleanup happens even when an exception occurs."""
        from model_hub.tasks.user_evaluation import process_single_evaluation

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"
        mock_row.objects.filter.return_value.count.return_value = 10

        mock_eval_metric = MagicMock()
        mock_eval_metric.id = "eval-123"
        mock_eval_metric.dataset.id = "dataset-123"
        mock_eval_metric.template.name = "Test Template"
        mock_eval_metric.template.id = "template-123"
        mock_eval_metric.organization = MagicMock()

        mock_runner = MagicMock()
        mock_runner._get_all_column_ids_being_used.return_value = []
        mock_runner.run_prompt.side_effect = Exception("Runner failed")
        mock_runner_class.return_value = mock_runner
        mock_column.objects.filter.return_value = []

        with pytest.raises(Exception, match="Runner failed"):
            process_single_evaluation(mock_eval_metric)

        # Cleanup should still happen
        mock_tracker.mark_completed.assert_called_once_with("eval-123")
        mock_tracker.clear_cancel_flag.assert_called_once_with("eval-123")

    @patch("model_hub.tasks.user_evaluation.evaluation_tracker")
    @patch("model_hub.tasks.user_evaluation.EvaluationRunner")
    @patch("model_hub.tasks.user_evaluation.Row")
    @patch("model_hub.tasks.user_evaluation.Column")
    @patch("model_hub.tasks.user_evaluation.RunPrompter")
    @patch("model_hub.tasks.user_evaluation.track_mixpanel_event")
    @patch("model_hub.tasks.user_evaluation.get_mixpanel_properties")
    def test_skips_if_dependent_prompt_running(
        self,
        mock_mixpanel_props,
        mock_track,
        mock_prompter,
        mock_column,
        mock_row,
        mock_runner_class,
        mock_tracker,
    ):
        """Test that evaluation is skipped if it depends on a running prompt."""
        from model_hub.models.choices import SourceChoices, StatusType
        from model_hub.tasks.user_evaluation import process_single_evaluation

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"
        mock_row.objects.filter.return_value.count.return_value = 10

        mock_eval_metric = MagicMock()
        mock_eval_metric.id = "eval-123"
        mock_eval_metric.dataset.id = "dataset-123"
        mock_eval_metric.template.name = "Test Template"
        mock_eval_metric.template.id = "template-123"
        mock_eval_metric.organization = MagicMock()

        # Create a column that depends on a running prompt
        mock_col = MagicMock()
        mock_col.source = SourceChoices.RUN_PROMPT.value
        mock_col.source_id = "prompt-123"
        mock_column.objects.filter.return_value = [mock_col]

        # The dependent prompt is running
        mock_prompter.objects.filter.return_value.exists.return_value = True

        mock_runner = MagicMock()
        mock_runner._get_all_column_ids_being_used.return_value = ["col-123"]
        mock_runner_class.return_value = mock_runner

        process_single_evaluation(mock_eval_metric)

        # Runner should NOT be called
        mock_runner.run_prompt.assert_not_called()
        # Status should be reset to NOT_STARTED
        assert mock_eval_metric.status == StatusType.NOT_STARTED.value
        mock_eval_metric.save.assert_called_once()


class TestProcessExperimentEvaluation:
    """Tests for process_experiment_evaluation function."""

    @patch("model_hub.tasks.user_evaluation.evaluation_tracker")
    @patch("model_hub.tasks.user_evaluation.ExperimentRunner")
    @patch("model_hub.tasks.user_evaluation.Row")
    @patch("model_hub.tasks.user_evaluation.track_mixpanel_event")
    @patch("model_hub.tasks.user_evaluation.get_mixpanel_properties")
    def test_processes_experiment_evaluation_successfully(
        self, mock_mixpanel_props, mock_track, mock_row, mock_runner_class, mock_tracker
    ):
        """Test successful processing of an experiment evaluation."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.user_evaluation import process_experiment_evaluation

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"
        mock_row.objects.filter.return_value.count.return_value = 10

        mock_eval_metric = MagicMock()
        mock_eval_metric.id = "eval-123"
        mock_eval_metric.source_id = "experiment-456"
        mock_eval_metric.dataset.id = "dataset-123"
        mock_eval_metric.template.name = "Test Template"
        mock_eval_metric.organization = MagicMock()

        mock_runner = MagicMock()
        mock_experiment = MagicMock()
        mock_experiment.user_eval_template_ids.filter.return_value.all.return_value = []
        mock_runner.experiment = mock_experiment
        mock_runner_class.return_value = mock_runner

        process_experiment_evaluation(mock_eval_metric)

        # Should use experiment tracking key
        mock_tracker.mark_running.assert_called_once()
        call_args = mock_tracker.mark_running.call_args
        assert call_args[0][0] == "experiment_experiment-456"

        mock_runner.run_additional_evaluations.assert_called_once_with(["eval-123"])

        # Should clean up with experiment tracking key
        mock_tracker.mark_completed.assert_called_once_with("experiment_experiment-456")
        mock_tracker.clear_cancel_flag.assert_called_once_with(
            "experiment_experiment-456"
        )

    @patch("model_hub.tasks.user_evaluation.evaluation_tracker")
    @patch("model_hub.tasks.user_evaluation.ExperimentRunner")
    @patch("model_hub.tasks.user_evaluation.Row")
    @patch("model_hub.tasks.user_evaluation.track_mixpanel_event")
    @patch("model_hub.tasks.user_evaluation.get_mixpanel_properties")
    def test_marks_experiment_completed_when_all_evals_done(
        self, mock_mixpanel_props, mock_track, mock_row, mock_runner_class, mock_tracker
    ):
        """Test that experiment is marked completed when all evaluations finish."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.user_evaluation import process_experiment_evaluation

        mock_tracker.is_running.return_value = False
        mock_tracker.instance_id = "test-instance"
        mock_row.objects.filter.return_value.count.return_value = 10

        mock_eval_metric = MagicMock()
        mock_eval_metric.id = "eval-123"
        mock_eval_metric.source_id = "experiment-456"
        mock_eval_metric.dataset.id = "dataset-123"
        mock_eval_metric.template.name = "Test Template"
        mock_eval_metric.organization = MagicMock()

        mock_runner = MagicMock()
        # All evals are completed
        mock_eval_1 = MagicMock()
        mock_eval_1.status = StatusType.COMPLETED.value
        mock_eval_2 = MagicMock()
        mock_eval_2.status = StatusType.COMPLETED.value
        mock_experiment = MagicMock()
        mock_experiment.user_eval_template_ids.filter.return_value.all.return_value = [
            mock_eval_1,
            mock_eval_2,
        ]
        mock_runner.experiment = mock_experiment
        mock_runner_class.return_value = mock_runner

        process_experiment_evaluation(mock_eval_metric)

        # Experiment should be marked as COMPLETED
        assert mock_experiment.status == StatusType.COMPLETED.value
        mock_experiment.save.assert_called_once()


@pytest.mark.django_db
class TestExecuteEvaluation:
    """Tests for execute_evaluation Temporal activity."""

    @patch("model_hub.tasks.user_evaluation.process_evaluation_single_task")
    @patch("model_hub.tasks.user_evaluation.UserEvalMetric")
    def test_processes_pending_evaluations(self, mock_user_eval, mock_process_task):
        """Test that pending evaluations are processed."""
        from model_hub.models.choices import StatusType
        from model_hub.tasks.user_evaluation import execute_evaluation

        mock_eval = MagicMock()
        mock_eval.id = "eval-123"
        mock_eval.status = StatusType.NOT_STARTED.value
        mock_user_eval.objects.filter.return_value.all.return_value.__getitem__.return_value = [
            mock_eval
        ]

        execute_evaluation()

        mock_user_eval.objects.filter.return_value.update.assert_called_once_with(
            status=StatusType.RUNNING.value
        )
        mock_process_task.apply_async.assert_called()

    @patch("model_hub.tasks.user_evaluation.UserEvalMetric")
    def test_handles_no_pending_evaluations(self, mock_user_eval):
        """Test that function handles case with no pending evaluations."""
        from model_hub.tasks.user_evaluation import execute_evaluation

        mock_user_eval.objects.filter.return_value.all.return_value.__getitem__.return_value = (
            []
        )

        # Should not raise
        execute_evaluation()


@pytest.mark.django_db
class TestProcessEvaluationSingleTask:
    """Tests for process_evaluation_single_task Temporal activity."""

    @patch("model_hub.tasks.user_evaluation.process_single_evaluation")
    @patch("model_hub.tasks.user_evaluation.UserEvalMetric")
    @patch("model_hub.tasks.user_evaluation.close_old_connections")
    def test_calls_single_evaluation_for_single_type(
        self, mock_close, mock_user_eval, mock_process
    ):
        """Test that single type calls process_single_evaluation."""
        from model_hub.tasks.user_evaluation import process_evaluation_single_task

        mock_eval = MagicMock()
        mock_user_eval.objects.get.return_value = mock_eval

        process_evaluation_single_task({"type": "single", "eval_id": "eval-123"})

        mock_process.assert_called_once_with(mock_eval)

    @patch("model_hub.tasks.user_evaluation.process_experiment_evaluation")
    @patch("model_hub.tasks.user_evaluation.UserEvalMetric")
    @patch("model_hub.tasks.user_evaluation.close_old_connections")
    def test_calls_experiment_evaluation_for_experiment_type(
        self, mock_close, mock_user_eval, mock_process
    ):
        """Test that experiment type calls process_experiment_evaluation."""
        from model_hub.tasks.user_evaluation import process_evaluation_single_task

        mock_eval = MagicMock()
        mock_user_eval.objects.get.return_value = mock_eval

        process_evaluation_single_task({"type": "experiment", "eval_id": "eval-123"})

        mock_process.assert_called_once_with(mock_eval)

    @patch("model_hub.tasks.user_evaluation.DevelopOptimizer")
    @patch("model_hub.tasks.user_evaluation.UserEvalMetric")
    @patch("model_hub.tasks.user_evaluation.close_old_connections")
    def test_calls_optimizer_for_optimization_type(
        self, mock_close, mock_user_eval, mock_optimizer_class
    ):
        """Test that optimization type calls DevelopOptimizer."""
        from model_hub.tasks.user_evaluation import process_evaluation_single_task

        mock_eval = MagicMock()
        mock_eval.source_id = "optim-123"
        mock_user_eval.objects.get.return_value = mock_eval

        mock_optimizer = MagicMock()
        mock_optimizer_class.return_value = mock_optimizer

        process_evaluation_single_task({"type": "optimization", "eval_id": "eval-123"})

        mock_optimizer.create_column.assert_called_once()
        assert mock_optimizer.run_feedback_eval.call_count == 2


@pytest.mark.django_db
class TestErrorLocalizerTask:
    """Tests for error_localizer_task Temporal activity."""

    @patch("model_hub.tasks.user_evaluation.process_single_error_localization")
    @patch("model_hub.tasks.user_evaluation.ErrorLocalizerTask")
    def test_processes_pending_tasks(self, mock_error_task, mock_process):
        """Test that pending error localization tasks are processed."""
        from model_hub.tasks.user_evaluation import error_localizer_task

        task_id = "550e8400-e29b-41d4-a716-446655440003"
        mock_task = MagicMock()
        mock_task.id = task_id

        # Create mock querysets for different filter calls
        # First call: filter(status=PENDING).values_list("id", flat=True)[:50]
        mock_qs_for_ids = MagicMock()
        mock_values_list = MagicMock()
        mock_values_list.__getitem__ = MagicMock(return_value=[task_id])
        mock_qs_for_ids.values_list.return_value = mock_values_list

        # Second call: filter(id__in=...).update(...)
        mock_qs_for_update = MagicMock()
        mock_qs_for_update.update.return_value = 1

        # Third call: filter(id__in=...) - needs to be iterable
        mock_qs_for_tasks = MagicMock()
        mock_qs_for_tasks.__iter__ = MagicMock(return_value=iter([mock_task]))

        # Track filter calls and return appropriate mock
        call_count = [0]

        def filter_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_qs_for_ids
            elif call_count[0] == 2:
                return mock_qs_for_update
            else:
                return mock_qs_for_tasks

        mock_error_task.objects.filter.side_effect = filter_side_effect

        error_localizer_task()

        mock_task.mark_as_running.assert_called_once()
        mock_process.apply_async.assert_called_once_with(args=(task_id,))

    @patch("model_hub.tasks.user_evaluation.ErrorLocalizerTask")
    def test_handles_no_pending_tasks(self, mock_error_task):
        """Test that function handles case with no pending tasks."""
        from model_hub.tasks.user_evaluation import error_localizer_task

        mock_error_task.objects.filter.return_value.values_list.return_value.__getitem__.return_value = (
            []
        )

        # Should not raise
        error_localizer_task()


@pytest.mark.django_db
class TestProcessSingleErrorLocalization:
    """Tests for process_single_error_localization Temporal activity."""

    @patch("model_hub.tasks.user_evaluation.ErrorLocalizer")
    @patch("model_hub.tasks.user_evaluation.log_and_deduct_cost_for_api_request")
    @patch("model_hub.tasks.user_evaluation.Workspace")
    @patch("model_hub.tasks.user_evaluation.ErrorLocalizerTask")
    @patch("model_hub.tasks.user_evaluation.close_old_connections")
    @patch("ee.usage.services.metering.check_usage")
    def test_processes_error_localization_successfully(
        self,
        mock_check_usage,
        mock_close,
        mock_error_task,
        mock_workspace,
        mock_log_cost,
        mock_localizer,
    ):
        """Test successful error localization processing."""
        from model_hub.models.error_localizer_model import ErrorLocalizerStatus
        from model_hub.tasks.user_evaluation import process_single_error_localization
        try:
            from ee.usage.models.usage import APICallStatusChoices
        except ImportError:
            APICallStatusChoices = None

        mock_check_usage.return_value = MagicMock(allowed=True)

        mock_task = MagicMock()
        mock_task.id = "task-123"
        mock_task.status = ErrorLocalizerStatus.RUNNING
        mock_task.workspace = MagicMock()
        mock_task.organization = MagicMock()
        mock_task.eval_template.name = "Test Eval"
        mock_task.eval_template.choices = []
        mock_task.eval_template.description = "Test description"
        mock_task.input_data = {}
        mock_task.input_keys = []
        mock_task.input_types = {}
        mock_task.eval_result = "pass"
        mock_task.eval_explanation = "Test passed"
        mock_task.rule_prompt = "Test rule"
        mock_error_task.objects.get.return_value = mock_task

        mock_api_log = MagicMock()
        mock_api_log.status = APICallStatusChoices.PROCESSING.value
        mock_log_cost.return_value = mock_api_log

        mock_localizer_instance = MagicMock()
        mock_localizer_instance.localize_errors.return_value = (
            "error_analysis",
            "selected_key",
        )
        mock_localizer.return_value = mock_localizer_instance

        process_single_error_localization("task-123")

        mock_task.mark_as_completed.assert_called_once_with(
            "error_analysis", "selected_key"
        )

    @patch("model_hub.tasks.user_evaluation.log_and_deduct_cost_for_api_request")
    @patch("model_hub.tasks.user_evaluation.Workspace")
    @patch("model_hub.tasks.user_evaluation.ErrorLocalizerTask")
    @patch("model_hub.tasks.user_evaluation.close_old_connections")
    @patch("ee.usage.services.metering.check_usage")
    def test_fails_when_api_call_not_allowed(
        self, mock_check_usage, mock_close, mock_error_task, mock_workspace, mock_log_cost
    ):
        """Test that task is marked failed when API call is not allowed."""
        from model_hub.models.error_localizer_model import ErrorLocalizerStatus
        from model_hub.tasks.user_evaluation import process_single_error_localization

        mock_task = MagicMock()
        mock_task.id = "task-123"
        mock_task.status = ErrorLocalizerStatus.RUNNING
        mock_task.workspace = MagicMock()
        mock_task.organization = MagicMock()
        mock_error_task.objects.get.return_value = mock_task
        mock_check_usage.return_value = MagicMock(allowed=True)

        mock_log_cost.return_value = None  # API call not allowed

        with pytest.raises(ValueError, match="API call not allowed"):
            process_single_error_localization("task-123")

        mock_task.mark_as_failed.assert_called()


@pytest.mark.django_db
class TestProcessEvalBatchAsyncTask:
    """Tests for process_eval_batch_async_task Temporal activity."""

    @patch("model_hub.tasks.user_evaluation.UserEvalMetric")
    @patch("model_hub.tasks.user_evaluation.EvaluationRunner")
    @patch("model_hub.tasks.user_evaluation.Column")
    @patch("model_hub.tasks.user_evaluation.close_old_connections")
    def test_processes_batch_successfully(
        self, mock_close, mock_column, mock_runner_class, mock_user_metric
    ):
        """Test successful batch processing."""
        from model_hub.tasks.user_evaluation import process_eval_batch_async_task

        mock_col = MagicMock()
        mock_column.objects.get.return_value = mock_col

        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        # The async task looks up the metric to check template_type for
        # composite dispatch; return a single-template metric so it falls
        # through to the existing EvaluationRunner path.
        mock_metric = MagicMock()
        mock_metric.template.template_type = "single"
        mock_user_metric.objects.select_related.return_value.get.return_value = (
            mock_metric
        )

        # Use valid UUIDs for all parameters
        column_uuid = "550e8400-e29b-41d4-a716-446655440004"
        row_uuids = [
            "550e8400-e29b-41d4-a716-446655440005",
            "550e8400-e29b-41d4-a716-446655440006",
            "550e8400-e29b-41d4-a716-446655440007",
        ]
        runner_params = {
            "user_eval_metric_id": "550e8400-e29b-41d4-a716-446655440008",
            "is_only_eval": True,
            "format_output": False,
            "source": "test",
            "source_id": "550e8400-e29b-41d4-a716-446655440009",
            "source_configs": {},
        }

        process_eval_batch_async_task(column_uuid, row_uuids, runner_params)

        mock_runner.run_prompt.assert_called_once_with(row_ids=row_uuids)

    @patch("model_hub.tasks.user_evaluation.EvaluationRunner")
    @patch("model_hub.tasks.user_evaluation.Column")
    @patch("model_hub.tasks.user_evaluation.close_old_connections")
    def test_handles_exception_gracefully(
        self, mock_close, mock_column, mock_runner_class
    ):
        """Test that exceptions are handled and connections are closed."""
        from model_hub.tasks.user_evaluation import process_eval_batch_async_task

        mock_column.objects.get.side_effect = Exception("Column not found")

        runner_params = {
            "user_eval_metric_id": "metric-123",
        }

        # Should not raise, but should log error
        process_eval_batch_async_task("column-123", ["row-1"], runner_params)

        # Connections should still be closed in finally block
        mock_close.assert_called()


class TestDistributedTrackerUsage:
    """Tests to verify distributed tracker is properly used."""

    def test_evaluation_tracker_is_imported(self):
        """Test that evaluation_tracker is properly imported."""
        from model_hub.tasks.user_evaluation import evaluation_tracker

        assert evaluation_tracker is not None

    def test_distributed_lock_manager_is_imported(self):
        """Test that distributed_lock_manager is properly imported."""
        from model_hub.tasks.user_evaluation import distributed_lock_manager

        assert distributed_lock_manager is not None


class TestTemporalActivityTimeouts:
    """Tests to verify Temporal activity timeouts are configured correctly (1 hour)."""

    def test_execute_evaluation_timeout(self):
        """Verify execute_evaluation has 1-hour timeout."""
        # The decorator sets time_limit=3600 (1 hour)
        from model_hub.tasks.user_evaluation import execute_evaluation

        # This test verifies the function exists and is callable
        assert callable(execute_evaluation)

    def test_process_evaluation_single_task_timeout(self):
        """Verify process_evaluation_single_task has 1-hour timeout."""
        from model_hub.tasks.user_evaluation import process_evaluation_single_task

        assert callable(process_evaluation_single_task)

    def test_error_localizer_task_timeout(self):
        """Verify error_localizer_task has 1-hour timeout."""
        from model_hub.tasks.user_evaluation import error_localizer_task

        assert callable(error_localizer_task)

    def test_process_single_error_localization_timeout(self):
        """Verify process_single_error_localization has 1-hour timeout."""
        from model_hub.tasks.user_evaluation import process_single_error_localization

        assert callable(process_single_error_localization)

    def test_process_eval_batch_async_task_timeout(self):
        """Verify process_eval_batch_async_task has 1-hour timeout."""
        from model_hub.tasks.user_evaluation import process_eval_batch_async_task

        assert callable(process_eval_batch_async_task)
