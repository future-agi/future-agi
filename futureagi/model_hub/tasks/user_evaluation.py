import json
import uuid

import structlog
from django.db import close_old_connections

from accounts.models import workspace
from accounts.models.workspace import Workspace
from agentic_eval.core.utils.functions import detect_input_type

logger = structlog.get_logger(__name__)
try:
    from ee.evals.localizer.error_localizer import ErrorLocalizer
except ImportError:
    # Activity-aware stub: runs inside Temporal evaluation activities.
    from tfc.ee_stub import _ee_activity_stub
    ErrorLocalizer = _ee_activity_stub("ErrorLocalizer")
from analytics.utils import (
    MixpanelEvents,
    MixpanelSources,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.models.choices import CellStatus, ModelChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.error_localizer_model import (
    ErrorLocalizerSource,
    ErrorLocalizerStatus,
    ErrorLocalizerTask,
)
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.evaluation import Evaluation
from model_hub.models.run_prompt import RunPrompter
from model_hub.views.develop_optimiser import DevelopOptimizer
from model_hub.views.eval_runner import EvaluationRunner
from model_hub.views.experiment_runner import ExperimentRunner
from sdk.utils.helpers import _get_api_call_type
from tfc.temporal import temporal_activity
from tfc.utils.distributed_locks import distributed_lock_manager

# Distributed state management for tracking running evaluations across instances
from tfc.utils.distributed_state import evaluation_tracker
from tfc.utils.error_codes import get_error_for_api_status
from tracer.models.observation_span import EvalLogger
try:
    from ee.usage.models.usage import APICallLog, APICallStatusChoices, APICallTypeChoices
except ImportError:
    APICallLog = None
    from tfc.oss_stubs.usage import APICallStatusChoices
    from tfc.oss_stubs.usage import APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request, refund_cost_for_api_call
except ImportError:
    from tfc.oss_stubs.usage import log_and_deduct_cost_for_api_request
    from tfc.oss_stubs.usage import refund_cost_for_api_call


def _mark_cells_usage_limit_error(user_eval_metric, usage_check):
    """Flip RUNNING cells for this eval to ERROR when a usage pre-check fails.

    Without this, non-composite eval cells stay in RUNNING forever (the worker
    raised before writing any result), leaving the UI stuck on a loading
    skeleton. The structured `value_infos` lets the frontend render a
    credit-limit-specific message + upgrade CTA instead of a generic error.
    """
    try:
        from django.db.models import Q

        # Dataset evals: Column.source_id == uem.id, source = 'evaluation'.
        # Experiment evals: one column per (prompt_config, dataset snapshot,
        # uem) with source = 'experiment_evaluation' and
        # source_id like '{...}-sourceid-{uem.id}'. We need to catch both.
        eval_columns = Column.objects.filter(
            Q(source=SourceChoices.EVALUATION.value, source_id=str(user_eval_metric.id))
            | Q(
                source=SourceChoices.EXPERIMENT_EVALUATION.value,
                source_id__endswith=f"-sourceid-{user_eval_metric.id}",
            ),
            deleted=False,
        )
        eval_column_ids = list(eval_columns.values_list("id", flat=True))
        if not eval_column_ids:
            return

        # Reason columns only exist for dataset evals. Experiments render
        # reason inline inside the eval cell's value_infos, so there is no
        # separate experiment_evaluation_reason column source.
        reason_column_ids = list(
            Column.objects.filter(
                source=SourceChoices.EVALUATION_REASON.value,
                source_id__endswith=f"-sourceid-{user_eval_metric.id}",
                deleted=False,
            ).values_list("id", flat=True)
        )

        upgrade_cta = None
        cta = getattr(usage_check, "upgrade_cta", None)
        if cta is not None:
            upgrade_cta = cta.model_dump() if hasattr(cta, "model_dump") else dict(cta)

        value_infos = {
            "error_code": usage_check.error_code or "USAGE_LIMIT_EXCEEDED",
            "reason": usage_check.reason or "Usage limit exceeded",
            "dimension": usage_check.dimension,
            "current_usage": usage_check.current_usage,
            "limit": usage_check.limit,
            "upgrade_cta": upgrade_cta,
        }
        display = usage_check.reason or "Usage limit exceeded"

        updated = Cell.objects.filter(
            column_id__in=eval_column_ids + reason_column_ids,
            deleted=False,
            status=CellStatus.RUNNING.value,
        ).update(
            status=CellStatus.ERROR.value,
            value=display,
            value_infos=value_infos,
        )
        logger.info(
            "usage_limit_error_marked_on_cells",
            eval_id=str(user_eval_metric.id),
            cells_updated=updated,
            error_code=usage_check.error_code,
        )
    except Exception as exc:
        # Surfacing the limit is a UX enhancement — never let it mask the
        # original failure.
        logger.warning(
            "failed_to_mark_cells_usage_limit_error",
            eval_id=str(user_eval_metric.id),
            error=str(exc),
        )


def process_single_evaluation(user_eval_metric):
    """
    Process a single evaluation with distributed locking and state tracking.

    This function uses distributed locks and state management to:
    - Prevent duplicate processing across multiple instances
    - Propagate cancel signals across all instances
    - Track running evaluations in Redis for visibility
    """
    eval_id = user_eval_metric.id

    logger.info(
        "process_single_evaluation_starting",
        eval_id=str(eval_id),
        dataset_id=(
            str(user_eval_metric.dataset.id) if user_eval_metric.dataset else None
        ),
        instance_id=evaluation_tracker.instance_id,
    )

    # Check if evaluation is already running and request cancellation if needed
    # This works across all instances via Redis
    if evaluation_tracker.is_running(eval_id):
        running_info = evaluation_tracker.get_running_info(eval_id)
        logger.info(
            "process_single_evaluation_already_running",
            eval_id=str(eval_id),
            running_on=running_info.instance_id if running_info else "unknown",
            current_instance=evaluation_tracker.instance_id,
        )
        evaluation_tracker.request_cancel(eval_id, reason="New evaluation requested")

    rows = Row.objects.filter(dataset=user_eval_metric.dataset, deleted=False)
    properties = get_mixpanel_properties(
        eval=user_eval_metric.template,
        org=user_eval_metric.organization,
        source=MixpanelSources.DATASET.value,
        dataset=user_eval_metric.dataset,
        count=rows.count(),
    )
    track_mixpanel_event(MixpanelEvents.EVAL_RUN_STARTED.value, properties)

    # Block agent-type evals when ee is absent.
    if getattr(user_eval_metric.template, "eval_type", "") == "agent":
        from tfc.ee_gating import is_oss
        if is_oss():
            user_eval_metric.status = StatusType.FAILED.value
            user_eval_metric.save(update_fields=["status"])
            raise ValueError(
                "Agent evaluations are not available on your plan. "
                "Use LLM-as-a-Judge or Code evaluations instead."
            )

    try:
        from ee.usage.services.metering import check_usage
    except ImportError:
        check_usage = None

    api_call_type = _get_api_call_type(
        user_eval_metric.model or ModelChoices.TURING_LARGE.value
    )
    if check_usage is not None:
        usage_check = check_usage(str(user_eval_metric.organization.id), api_call_type)
        if not usage_check.allowed:
            user_eval_metric.status = StatusType.FAILED.value
            user_eval_metric.save(update_fields=["status"])
            _mark_cells_usage_limit_error(user_eval_metric, usage_check)
            raise ValueError(usage_check.reason or "Usage limit exceeded")

    runner = EvaluationRunner(
        user_eval_metric_id=user_eval_metric.id,
        is_only_eval=True,
        source="dataset_evaluation",
        source_id=user_eval_metric.template.id,
        source_configs={
            "dataset_id": str(user_eval_metric.dataset.id),
            "source": "dataset",
        },
    )
    cols_used = runner._get_all_column_ids_being_used()

    columns_used = Column.objects.filter(id__in=cols_used)

    # Mark evaluation as running in distributed state (visible to all instances)
    evaluation_tracker.mark_running(
        eval_id,
        runner_info={
            "dataset_id": str(user_eval_metric.dataset.id),
            "template": (
                user_eval_metric.template.name if user_eval_metric.template else None
            ),
            "type": "single_evaluation",
        },
    )

    try:
        skip_process = False
        for column in columns_used:
            if column.source == SourceChoices.RUN_PROMPT.value:
                if RunPrompter.objects.filter(
                    id=column.source_id,
                    status__in=[
                        StatusType.RUNNING.value,
                        StatusType.EDITING.value,
                        StatusType.PARTIAL_RUN.value,
                    ],
                ).exists():
                    logger.info(f"{user_eval_metric.id} depends on a running prompt")
                    skip_process = True
                    break

        if not skip_process:
            # Inject distributed cancel checker into runner if it supports it
            if hasattr(runner, "_check_cancel_callback"):
                runner._check_cancel_callback = lambda: (
                    evaluation_tracker.should_cancel(eval_id)
                )
            logger.info(
                "process_single_evaluation_running",
                eval_id=str(eval_id),
            )
            runner.run_prompt()
            logger.info(
                "process_single_evaluation_completed",
                eval_id=str(eval_id),
            )
        else:
            logger.info(
                "process_single_evaluation_skipped_dependent_prompt",
                eval_id=str(user_eval_metric.id),
            )
            user_eval_metric.status = StatusType.NOT_STARTED.value
            user_eval_metric.save()
    except Exception as e:
        logger.exception(
            "process_single_evaluation_error",
            eval_id=str(eval_id),
            error=str(e),
            error_type=type(e).__name__,
        )
        raise
    finally:
        # Always mark as completed and clear any cancel flags
        evaluation_tracker.mark_completed(eval_id)
        evaluation_tracker.clear_cancel_flag(eval_id)
        logger.debug(
            "process_single_evaluation_cleanup_done",
            eval_id=str(eval_id),
        )


def process_experiment_evaluation(user_eval_metric):
    """
    Process an experiment evaluation with distributed locking and state tracking.

    Uses experiment's source_id as the tracking key since multiple evaluations
    can be part of the same experiment.
    """
    experiment_id = user_eval_metric.source_id
    tracking_key = f"experiment_{experiment_id}"

    logger.info(
        "process_experiment_evaluation_starting",
        experiment_id=str(experiment_id),
        eval_metric_id=str(user_eval_metric.id),
        instance_id=evaluation_tracker.instance_id,
    )

    # Check if experiment is already running and request cancellation if needed
    if evaluation_tracker.is_running(tracking_key):
        running_info = evaluation_tracker.get_running_info(tracking_key)
        logger.info(
            "process_experiment_evaluation_already_running",
            experiment_id=str(experiment_id),
            running_on=running_info.instance_id if running_info else "unknown",
            current_instance=evaluation_tracker.instance_id,
        )
        evaluation_tracker.request_cancel(
            tracking_key, reason="New experiment evaluation requested"
        )

    rows = Row.objects.filter(dataset=user_eval_metric.dataset, deleted=False)
    properties = get_mixpanel_properties(
        eval=user_eval_metric.template,
        org=user_eval_metric.organization,
        source=MixpanelSources.EXPERIMENT.value,
        dataset=user_eval_metric.dataset,
        count=rows.count(),
        experiment_id=user_eval_metric.source_id,
    )
    track_mixpanel_event(MixpanelEvents.EVAL_RUN_STARTED.value, properties)

    try:
        from ee.usage.services.metering import check_usage
    except ImportError:
        check_usage = None

    api_call_type = _get_api_call_type(
        user_eval_metric.model or ModelChoices.TURING_LARGE.value
    )
    if check_usage is not None:
        usage_check = check_usage(str(user_eval_metric.organization.id), api_call_type)
        if not usage_check.allowed:
            user_eval_metric.status = StatusType.FAILED.value
            user_eval_metric.save(update_fields=["status"])
            _mark_cells_usage_limit_error(user_eval_metric, usage_check)
            raise ValueError(usage_check.reason or "Usage limit exceeded")

    runner = ExperimentRunner(experiment_id=user_eval_metric.source_id)
    runner.load_experiment()

    # Mark experiment as running in distributed state
    evaluation_tracker.mark_running(
        tracking_key,
        runner_info={
            "experiment_id": str(experiment_id),
            "eval_metric_id": str(user_eval_metric.id),
            "type": "experiment_evaluation",
        },
    )

    try:
        # Inject distributed cancel checker into runner if it supports it
        if hasattr(runner, "_check_cancel_callback"):
            runner._check_cancel_callback = lambda: evaluation_tracker.should_cancel(
                tracking_key
            )

        runner.run_additional_evaluations([user_eval_metric.id])

        # Check if all evaluations for this experiment are completed
        experiment = runner.experiment
        all_evals_completed = all(
            eval.status == StatusType.COMPLETED.value
            for eval in experiment.user_eval_template_ids.filter(deleted=False).all()
        )
        if all_evals_completed:
            experiment.status = StatusType.COMPLETED.value
            experiment.save(update_fields=["status"])
            logger.info(
                "process_experiment_evaluation_all_completed",
                experiment_id=str(experiment_id),
            )
        else:
            logger.info(
                "process_experiment_evaluation_partial_complete",
                experiment_id=str(experiment_id),
            )
    except Exception as e:
        logger.exception(
            "process_experiment_evaluation_error",
            experiment_id=str(experiment_id),
            eval_metric_id=str(user_eval_metric.id),
            error=str(e),
            error_type=type(e).__name__,
        )
        raise
    finally:
        # Always mark as completed and clear any cancel flags
        evaluation_tracker.mark_completed(tracking_key)
        evaluation_tracker.clear_cancel_flag(tracking_key)
        logger.debug(
            "process_experiment_evaluation_cleanup_done",
            experiment_id=str(experiment_id),
        )


@temporal_activity(time_limit=3600, queue="default")
def execute_evaluation():
    close_old_connections()
    try:
        # Get evaluations that need to be processed - single ORM call
        all_evaluations = list(
            UserEvalMetric.objects.filter(
                status__in=[
                    StatusType.NOT_STARTED.value,
                    StatusType.EXPERIMENT_EVALUATION.value,
                    StatusType.OPTIMIZATION_EVALUATION.value,
                ]
            ).all()[:30]
        )

        if not all_evaluations:
            logger.info("No evaluations to process")
            return

        # Update status for all evaluations
        all_eval_ids = [eval.id for eval in all_evaluations]
        UserEvalMetric.objects.filter(id__in=all_eval_ids).update(
            status=StatusType.RUNNING.value
        )

        # Prepare data for processing - check status directly
        evaluations_to_process = []
        for eval in all_evaluations:
            eval_id = str(eval.id)

            if eval.status == StatusType.NOT_STARTED.value:
                evaluations_to_process.append({"type": "single", "eval_id": eval_id})
            elif eval.status == StatusType.EXPERIMENT_EVALUATION.value:
                evaluations_to_process.append(
                    {"type": "experiment", "eval_id": eval_id}
                )
            elif eval.status == StatusType.OPTIMIZATION_EVALUATION.value:
                evaluations_to_process.append(
                    {"type": "optimization", "eval_id": eval_id}
                )

        for eval in evaluations_to_process:
            process_evaluation_single_task.apply_async(args=(eval,))

    except Exception as e:
        logger.error(f"Fatal error in execute_evaluation: {str(e)}")
        raise


@temporal_activity(time_limit=3600, queue="tasks_l")
def process_evaluation_single_task(evaluation):
    close_old_connections()

    eval_obj = UserEvalMetric.objects.get(id=evaluation["eval_id"])
    logger.info(f"Processing evaluation {eval_obj.id}")

    if evaluation["type"] == "single":
        # Composite evals need the composite runner, not the single-eval one
        if eval_obj.template and eval_obj.template.template_type == "composite":
            from model_hub.tasks.composite_runner import CompositeEvaluationRunner

            composite_runner = CompositeEvaluationRunner(
                user_eval_metric_id=str(eval_obj.id),
            )
            composite_runner.run_prompt()
        else:
            process_single_evaluation(eval_obj)
    elif evaluation["type"] == "experiment":
        process_experiment_evaluation(eval_obj)
    elif evaluation["type"] == "optimization":
        runner = DevelopOptimizer(
            optim_obj_id=eval_obj.source_id,
        )
        runner.create_column()
        runner.run_feedback_eval(runner.old_column, eval_obj)
        runner.run_feedback_eval(runner.new_column, eval_obj)

    close_old_connections()


@temporal_activity(time_limit=3600, queue="default")
def error_localizer_task():
    """
    Celery task to run error localization on evaluation results.
    Processes all pending tasks concurrently using multithreading.
    """
    logger.info("Starting error localization task")
    try:
        # Get the IDs of the pending tasks
        pending_task_ids = list(
            ErrorLocalizerTask.objects.filter(
                status=ErrorLocalizerStatus.PENDING
            ).values_list("id", flat=True)[:50]
        )

        if not pending_task_ids:
            logger.info("No pending error localization tasks found")
            return

        # Update the status of all pending tasks to RUNNING
        ErrorLocalizerTask.objects.filter(id__in=pending_task_ids).update(
            status=ErrorLocalizerStatus.RUNNING
        )

        # Fetch the pending tasks for processing
        pending_tasks = ErrorLocalizerTask.objects.filter(id__in=pending_task_ids)

        # Process tasks concurrently
        # with ThreadPoolExecutor(max_workers=10) as executor:
        #     futures = []
        #     for pending_task in pending_tasks:
        #         futures.append(executor.submit(process_single_error_localization, pending_task.id))

        #     # Wait for all futures to complete
        #     for future in as_completed(futures):
        #         try:
        #             future.result()
        #         except Exception as e:
        #             logger.error(f"Error in processing task: {str(e)}")

        for pending_task in pending_tasks:
            pending_task.mark_as_running()
            process_single_error_localization.apply_async(args=(pending_task.id,))

    except Exception as e:
        logger.error(f"Error in error_localizer_task: {str(e)}")


@temporal_activity(time_limit=3600, queue="tasks_xl")
def process_eval_batch_async_task(column_id, row_ids, runner_params):
    """
    Process a batch of rows for evaluation asynchronously.
    This task recreates the EvaluationRunner with all params and processes the batch.

    Args:
        column_id: UUID string of the evaluation column
        dataset_id: UUID string of the dataset
        row_ids: List of row UUID strings to process in this batch
        runner_params: Dictionary containing all EvaluationRunner initialization params
    """
    close_old_connections()
    try:
        logger.info(f"Processing batch of {len(row_ids)} rows for column {column_id}")

        # Get experiment_dataset if provided
        experiment_dataset = None
        if runner_params.get("experiment_dataset_id"):
            from model_hub.models.experiments import ExperimentDatasetTable

            experiment_dataset_uuid = uuid.UUID(runner_params["experiment_dataset_id"])
            experiment_dataset = ExperimentDatasetTable.objects.get(
                id=experiment_dataset_uuid
            )

        # Get optimize if provided
        optimize = None
        if runner_params.get("optimize_id"):
            from model_hub.models.develop_optimisation import OptimizationDataset

            optimize_uuid = uuid.UUID(runner_params["optimize_id"])
            optimize = OptimizationDataset.objects.get(id=optimize_uuid)

        # Get the column object from column_id
        if column_id and isinstance(column_id, str):
            column_id = uuid.UUID(column_id)
            column = Column.objects.get(id=column_id)
        else:
            column = None

        # Branch on template_type: composites need a separate runner that
        # fans out each row across child evals and writes linked
        # Evaluation rows. Single evals go through the existing
        # EvaluationRunner path untouched.
        metric_for_dispatch = UserEvalMetric.objects.select_related("template").get(
            id=runner_params["user_eval_metric_id"]
        )

        if metric_for_dispatch.template.template_type == "composite":
            from model_hub.tasks.composite_runner import CompositeEvaluationRunner

            composite_runner = CompositeEvaluationRunner(
                user_eval_metric_id=runner_params["user_eval_metric_id"],
                experiment_dataset=experiment_dataset,
                column=column if experiment_dataset else None,
                optimize=optimize,
                source=runner_params.get("source"),
                source_id=runner_params.get("source_id"),
                source_configs=runner_params.get("source_configs", {}),
            )
            composite_runner.run_prompt(row_ids=row_ids)
            logger.info(
                f"Completed composite batch of {len(row_ids)} rows for column {column_id}"
            )
            return

        # Create EvaluationRunner instance with all params
        runner_kwargs = {
            "user_eval_metric_id": runner_params["user_eval_metric_id"],
            "experiment_dataset": experiment_dataset,
            "optimize": optimize,
            "is_only_eval": runner_params.get("is_only_eval", False),
            "format_output": runner_params.get("format_output", False),
            "cancel_event": runner_params.get("cancel_event"),
            "futureagi_eval": runner_params.get("futureagi_eval", False),
            "protect": runner_params.get("protect", False),
            "protect_flash": runner_params.get("protect_flash", False),
            "source": runner_params.get("source"),
            "source_id": runner_params.get("source_id"),
            "source_configs": runner_params.get("source_configs", {}),
        }

        # Pass column parameter only for experiments (matching experiment_runner.py pattern)
        if experiment_dataset:
            runner_kwargs["column"] = column

        runner = EvaluationRunner(**runner_kwargs)

        # Call run_prompt with the batch row_ids to process this batch
        # This will handle initialization, processing, and status checking
        runner.run_prompt(row_ids=row_ids)

        logger.info(
            f"Completed processing batch of {len(row_ids)} rows for column {column_id}"
        )

    except Exception as e:
        logger.exception(
            f"Error in process_eval_batch_async_task for column {column_id}: {str(e)}"
        )
    finally:
        close_old_connections()


def _get_input_type(input):
    """Determine input types for a dictionary of inputs."""
    input_type = {}
    for key, value in input.items():
        input_type[key] = detect_input_type(value).get("type", "text")
    return input_type


def _validate_error_localizer_fields(rule_prompt, input_data, eval_result):
    """
    Validate required fields for error localization.
    Returns (status, error_message) tuple.
    If validation fails, returns FAILED status with appropriate error message.
    """
    missing_fields = []
    if not rule_prompt:
        missing_fields.append("rule_prompt")
    if not input_data:
        missing_fields.append("input_data")
    if not eval_result:
        missing_fields.append("eval_result")

    if missing_fields:
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        logger.error(f"ErrorLocalizerTask validation failed - {error_msg}")
        return ErrorLocalizerStatus.FAILED, error_msg

    return ErrorLocalizerStatus.PENDING, ""


def trigger_error_localization_for_column(
    eval_template,
    config,
    required_field,
    mapping,
    eval_result,
    response,
    cell,
    log_id=None,
):
    """
    Helper function to create ErrorLocalizerTask records for cells.
    """
    input_data_dict = {}
    required_keys = []

    if eval_template.name == "Deterministic Evals":
        input_variables = config.get("input", [])
        for idx, input_variable in enumerate(input_variables):
            input_data_dict[f"variable_{idx + 1}"] = input_variable

    else:
        if isinstance(required_field, list):
            for idx, field in enumerate(required_field):
                if field == "required_keys":
                    required_keys = mapping[idx]
                    break

        if required_keys and isinstance(required_keys, list):
            for idx, field in enumerate(required_field):
                if field in required_keys:
                    input_data_dict[field] = mapping[idx]

    input_type_dict = _get_input_type(input_data_dict)
    input_keys = list(input_data_dict.keys())
    rule_prompt = (
        config.get("rule_prompt") or eval_template.criteria or eval_template.description
    )

    cell = Cell.objects.select_related(
        "dataset", "dataset__organization", "dataset__workspace"
    ).get(id=cell.id)
    workspace = cell.dataset.workspace
    if not workspace:
        workspace = Workspace.objects.get(
            organization=cell.dataset.organization, is_default=True, is_active=True
        )

    task_exists = ErrorLocalizerTask.objects.filter(source_id=cell.id).exists()

    # Validate required fields before creating/updating task
    initial_status, error_message = _validate_error_localizer_fields(
        rule_prompt, input_data_dict, eval_result
    )

    if task_exists:
        task = ErrorLocalizerTask.objects.get(source_id=cell.id)
        metadata = task.metadata
        metadata.update({"log_id": log_id})
        task.eval_result = eval_result
        task.eval_explanation = response.get("reason", "")
        task.input_data = input_data_dict
        task.input_keys = input_keys
        task.input_types = input_type_dict
        task.status = initial_status
        task.rule_prompt = rule_prompt
        task.error_message = error_message
        task.metadata = metadata
        task.save()

    else:
        task = ErrorLocalizerTask(
            eval_template=eval_template,
            source=ErrorLocalizerSource.DATASET,
            source_id=cell.id,
            input_data=input_data_dict,
            input_keys=input_keys,
            input_types=input_type_dict,
            eval_result=eval_result,
            eval_explanation=response.get("reason", ""),
            rule_prompt=rule_prompt,
            organization=cell.dataset.organization,
            workspace=workspace,
            metadata={"log_id": log_id},
            status=initial_status,
            error_message=error_message,
        )
        task.save()

    logger.info(f"Created ErrorLocalizerTask for cell {cell.id}")


def trigger_error_localization_for_span(
    eval_template, eval_logger, value, mapping, eval_explanation, log_id=None
):
    try:
        """
        Helper function to create ErrorLocalizerTask records for spans.
        """
        input_data_dict = mapping
        input_keys = list(input_data_dict.keys())
        input_type_dict = _get_input_type(input_data_dict)

        task_exists = ErrorLocalizerTask.objects.filter(
            source_id=eval_logger.id
        ).exists()
        workspace = eval_logger.observation_span.project.workspace
        if not workspace:
            workspace = Workspace.objects.get(
                organization=eval_logger.observation_span.project.organization,
                is_default=True,
                is_active=True,
            )

        rule_prompt = (
            eval_template.config.get("rule_prompt")
            or eval_template.criteria
            or eval_template.description
        )

        # Validate required fields before creating/updating task
        initial_status, error_message = _validate_error_localizer_fields(
            rule_prompt, input_data_dict, value
        )

        if task_exists:
            task = ErrorLocalizerTask.objects.get(source_id=eval_logger.id)
            metadata = task.metadata
            metadata.update({"log_id": str(log_id)})
            task.eval_result = value
            task.eval_explanation = eval_explanation
            task.input_data = input_data_dict
            task.input_keys = input_keys
            task.input_types = input_type_dict
            task.status = initial_status
            task.rule_prompt = rule_prompt
            task.error_message = error_message
            task.metadata = metadata
            task.save()

        else:
            task = ErrorLocalizerTask(
                eval_template=eval_template,
                source=ErrorLocalizerSource.OBSERVE,
                source_id=eval_logger.id,
                input_data=input_data_dict,
                input_keys=input_keys,
                input_types=input_type_dict,
                eval_result=value,
                eval_explanation=eval_explanation,
                rule_prompt=rule_prompt,
                organization=eval_logger.observation_span.project.organization,
                metadata={"log_id": log_id},
                workspace=workspace,
                status=initial_status,
                error_message=error_message,
            )
            task.save()

        logger.info(f"Created ErrorLocalizerTask for Eval Logger {eval_logger.id}")
    except Exception as e:
        logger.error(f"Error in trigger_error_localization_for_span: {str(e)}")


def _get_input_keys(input_data):
    """
    Returns input keys for standalone evaluations.
    """
    if isinstance(input_data, dict):
        return list(input_data.keys())
    else:
        return []


def trigger_error_localization_for_standalone(evaluation: Evaluation):
    """
    Helper function to create ErrorLocalizerTask for standalone evaluations.
    """
    try:
        input_keys = _get_input_keys(evaluation.input_data)
        input_types = _get_input_type(evaluation.input_data)

        workspace = evaluation.workspace
        if not workspace:
            workspace = Workspace.objects.get(
                organization=evaluation.organization, is_default=True, is_active=True
            )

        rule_prompt = (
            evaluation.eval_template.config.get("rule_prompt")
            or evaluation.eval_template.criteria
            or evaluation.eval_template.description
        )

        # Validate required fields before creating task
        initial_status, error_message = _validate_error_localizer_fields(
            rule_prompt, evaluation.input_data, evaluation.data
        )

        error_localizer_task = ErrorLocalizerTask.objects.create(
            eval_template=evaluation.eval_template,
            source=ErrorLocalizerSource.STANDALONE,
            source_id=evaluation.id,
            input_data=evaluation.input_data,
            input_keys=input_keys,
            input_types=input_types,
            eval_result=evaluation.data,
            eval_explanation=evaluation.reason,
            rule_prompt=rule_prompt,
            organization=evaluation.organization,
            workspace=workspace,
            status=initial_status,
            error_message=error_message,
        )

        logger.info(
            f"Created ErrorLocalizerTask for Standalone Evaluation {evaluation.id}"
        )
        return error_localizer_task
    except Exception as e:
        logger.exception(
            f"Error in trigger_error_localization_for_standalone: {str(e)}"
        )


def trigger_error_localization_for_playground(
    eval_template, log, value, mapping, eval_explanation
):
    try:
        """
        Helper function to create ErrorLocalizerTask records for playground.
        """
        input_data_dict = mapping
        input_keys = list(input_data_dict.keys())
        input_type_dict = _get_input_type(input_data_dict)

        workspace = log.workspace
        if not workspace:
            workspace = Workspace.objects.get(
                organization=log.organization, is_default=True, is_active=True
            )

        rule_prompt = (
            eval_template.config.get("rule_prompt")
            or eval_template.criteria
            or eval_template.description
        )

        # Validate required fields before creating/updating task
        initial_status, error_message = _validate_error_localizer_fields(
            rule_prompt, input_data_dict, value
        )

        try:
            task = ErrorLocalizerTask.objects.get(source_id=log.log_id)
            task.eval_result = value
            task.eval_explanation = eval_explanation
            task.input_data = input_data_dict
            task.input_keys = input_keys
            task.input_types = input_type_dict
            task.status = initial_status
            task.rule_prompt = rule_prompt
            task.error_message = error_message
        except ErrorLocalizerTask.DoesNotExist:
            task = ErrorLocalizerTask(
                eval_template=eval_template,
                source=ErrorLocalizerSource.PLAYGROUND,
                source_id=log.log_id,
                input_data=input_data_dict,
                input_keys=input_keys,
                input_types=input_type_dict,
                eval_result=value,
                eval_explanation=eval_explanation,
                rule_prompt=rule_prompt,
                organization=log.organization,
                workspace=workspace,
                status=initial_status,
                error_message=error_message,
            )

        task.save()
        logger.info(f"Created ErrorLocalizerTask for Eval Logger {log.log_id}")
    except Exception as e:
        logger.error(f"Error in trigger_error_localization_for_span: {str(e)}")


def trigger_error_localization_for_simulate(
    eval_template,
    call_execution,
    eval_config,
    value,
    mapping,
    eval_explanation,
    log_id=None,
):
    try:
        """
        Helper function to create ErrorLocalizerTask records for simulate evaluations.
        """
        input_data_dict = mapping
        input_keys = list(input_data_dict.keys())
        input_type_dict = _get_input_type(input_data_dict)

        # # Create a unique source_id combining call_execution and eval_config

        workspace = call_execution.test_execution.run_test.workspace
        if not workspace:
            workspace = Workspace.objects.get(
                organization=call_execution.test_execution.run_test.organization,
                is_default=True,
                is_active=True,
            )

        # task_exists = ErrorLocalizerTask.objects.filter(source_id=source_id).exists()

        # if task_exists:
        #     task = ErrorLocalizerTask.objects.get(source_id=source_id)
        #     metadata = task.metadata
        #     metadata.update({"log_id": log_id, "call_execution_id": str(call_execution.id), "eval_config_id": str(eval_config.id)})
        #     task.eval_result = value
        #     task.eval_explanation = eval_explanation
        #     task.input_data = input_data_dict
        #     task.input_keys = input_keys
        #     task.input_types = input_type_dict
        #     task.status = ErrorLocalizerStatus.PENDING
        #     task.error_message = ""
        #     task.metadata = metadata
        #     task.save()
        # else:
        config = eval_config.config or {}
        rule_prompt = (
            config.get("rule_prompt")
            or eval_template.criteria
            or eval_template.description
        )

        # Validate required fields before creating task
        initial_status, error_message = _validate_error_localizer_fields(
            rule_prompt, input_data_dict, value
        )

        task = ErrorLocalizerTask(
            eval_template=eval_template,
            source=ErrorLocalizerSource.SIMULATE,
            source_id=call_execution.id,
            input_data=input_data_dict,
            input_keys=input_keys,
            input_types=input_type_dict,
            eval_result=value,
            eval_explanation=eval_explanation,
            rule_prompt=rule_prompt,
            organization=call_execution.test_execution.run_test.organization,
            metadata={
                "log_id": log_id,
                # "call_execution_id": str(call_execution.id),
                "eval_config_id": str(eval_config.id),
            },
            status=initial_status,
            error_message=error_message,
        )
        task.save()

        logger.info(
            f"Created ErrorLocalizerTask for Simulate Eval - Call: {call_execution.id}, Config: {eval_config.id}"
        )
    except Exception as e:
        logger.error(f"Error in trigger_error_localization_for_simulate: {str(e)}")


@temporal_activity(time_limit=3600, queue="tasks_s", rate_limit="100/s")
def process_single_error_localization(task_id):
    """
    Process a single error localization task.
    """
    try:
        close_old_connections()
        task = ErrorLocalizerTask.objects.get(id=task_id)

        # Make sure the task is marked as running
        if task.status != ErrorLocalizerStatus.RUNNING:
            task.mark_as_running()

        if not task.workspace:
            task.workspace = Workspace.objects.get(
                organization=task.organization, is_default=True, is_active=True
            )
        # Pre-check: enforce free tier limits
        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        if check_usage is not None:
            usage_check = check_usage(
                str(task.organization.id), APICallTypeChoices.ERROR_LOCALIZER.value
            )
            if not usage_check.allowed:
                if task:
                    task.mark_as_failed(usage_check.reason or "Usage limit exceeded")
                raise ValueError(usage_check.reason or "Usage limit exceeded")

        # Log and deduct cost for error localization
        api_call_log_row = log_and_deduct_cost_for_api_request(
            organization=task.organization,
            api_call_type=APICallTypeChoices.ERROR_LOCALIZER.value,
            workspace=task.workspace,
            source="error_localizer",
            source_id=str(task.id),
            config={
                "reference_id": str(task.source_id),
                "error_localizer_task_id": str(task.id),
            },
        )

        if not api_call_log_row:
            logger.error("API call not allowed : Error validating the api call.")
            task.mark_as_failed("API call not allowed : Error validating the api call.")
            raise ValueError("API call not allowed : Error validating the api call.")

        if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
            error_message = get_error_for_api_status(api_call_log_row.status)
            task.mark_as_failed(error_message)
            return

        try:
            localizer = ErrorLocalizer(
                eval_name=task.eval_template.name,
                choices=(
                    task.eval_template.choices if task.eval_template.choices else []
                ),
                rule_prompt=task.rule_prompt,
                input=task.input_data,
                input_keys=task.input_keys,
                input_type=task.input_types,
                evaluation_result=task.eval_result,
                evaluation_explanation=task.eval_explanation,
            )

            error_analysis, selected_input_key = localizer.localize_errors()
        except Exception as e:
            import traceback

            logger.error(
                f"Error in process_single_error_localization: {str(e)}\n{traceback.format_exc()}"
            )
            task.mark_as_failed(str(e))
            refund_cost_for_api_call(api_call_log_row)
            return

        # Check if we got valid results
        if not error_analysis:
            logger.warning(
                f"Error localization returned empty results for cell {task.source_id}"
            )
            task.mark_as_skipped("Error localization returned empty results")
            refund_cost_for_api_call(api_call_log_row)
            return

        # Update the task with the results
        task.mark_as_completed(error_analysis, selected_input_key)
        api_call_log_row.status = APICallStatusChoices.SUCCESS.value
        api_call_log_row.save(update_fields=["status"])

        # Dual-write: emit usage event for new billing system (cost-based)
        try:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.schemas.events import UsageEvent
            except ImportError:
                UsageEvent = None
            try:
                from ee.usage.services.config import BillingConfig
            except ImportError:
                BillingConfig = None
            try:
                from ee.usage.services.emitter import emit
            except ImportError:
                emit = None

            actual_cost = getattr(localizer, "cost", {}).get("total_cost", 0)
            if not actual_cost and hasattr(localizer, "llm"):
                actual_cost = getattr(localizer.llm, "cost", {}).get("total_cost", 0)
            credits = BillingConfig.get().calculate_ai_credits(actual_cost)

            emit(
                UsageEvent(
                    org_id=str(task.organization.id),
                    event_type=BillingEventType.ERROR_LOCALIZER,
                    amount=credits,
                    properties={
                        "source": "error_localizer",
                        "source_id": str(task.id),
                        "raw_cost_usd": str(actual_cost),
                    },
                )
            )
        except Exception:
            pass  # Metering failure must not break the action

        logger.info(f"Error Localization task {task.id} completed")

        if task.source == ErrorLocalizerSource.DATASET:
            try:
                cell = Cell.objects.get(id=task.source_id)

                value_infos = json.loads(cell.value_infos)
                value_infos.update(
                    {
                        "error_analysis": error_analysis,
                        "selected_input_key": selected_input_key,
                        "input_types": task.input_types,
                        "input_data": task.input_data,
                    }
                )

                cell.value_infos = json.dumps(value_infos)
                cell.save(update_fields=["value_infos"])

                metadata = task.metadata
                if metadata.get("log_id", None):
                    try:
                        log = APICallLog.objects.get(log_id=metadata.get("log_id"))
                        config = json.loads(log.config)
                        config["error_localizer"] = {
                            "error_analysis": error_analysis,
                            "selected_input_key": selected_input_key,
                            "input_types": task.input_types,
                            "input_data": task.input_data,
                        }
                        log.config = json.dumps(config)
                        log.save(update_fields=["config"])
                    except APICallLog.DoesNotExist:
                        logger.info("Log doesn't exist.")
            except Exception as e:
                logger.error(f"Error in updating cell metadata: {str(e)}")
                refund_cost_for_api_call(api_call_log_row)
                task.mark_as_failed(str(e))

        elif task.source == ErrorLocalizerSource.OBSERVE:
            try:
                eval_logger = EvalLogger.objects.get(id=task.source_id)
                output_metadata = eval_logger.output_metadata or {}
                output_metadata.update(
                    {
                        "error_analysis": error_analysis,
                        "selected_input_key": selected_input_key,
                        "input_types": task.input_types,
                        "input_data": task.input_data,
                    }
                )

                eval_logger.output_metadata = output_metadata
                eval_logger.save(update_fields=["output_metadata"])

                metadata = task.metadata
                if metadata.get("log_id", None):
                    try:
                        log = APICallLog.objects.get(log_id=metadata.get("log_id"))
                        config = json.loads(log.config)
                        config["error_localizer"] = {
                            "error_analysis": error_analysis,
                            "selected_input_key": selected_input_key,
                            "input_types": task.input_types,
                            "input_data": task.input_data,
                        }
                        log.config = json.dumps(config)
                        log.save(update_fields=["config"])
                    except APICallLog.DoesNotExist:
                        logger.info("Log doesn't exist.")

            except Exception as e:
                logger.error(f"Error in updating span metadata: {str(e)}")
                refund_cost_for_api_call(api_call_log_row)
                task.mark_as_failed(str(e))

        elif task.source == ErrorLocalizerSource.PLAYGROUND:
            try:
                eval_logger = APICallLog.objects.get(log_id=task.source_id)
                config = json.loads(eval_logger.config) or {}
                config["error_localizer"] = {
                    "error_analysis": error_analysis,
                    "selected_input_key": selected_input_key,
                    "input_types": task.input_types,
                    "input_data": task.input_data,
                }
                eval_logger.config = json.dumps(config)
                eval_logger.save(update_fields=["config"])
            except Exception as e:
                logger.exception(f"Error in updating log config: {str(e)}")
                refund_cost_for_api_call(api_call_log_row)
                task.mark_as_failed(str(e))
    finally:
        close_old_connections()
