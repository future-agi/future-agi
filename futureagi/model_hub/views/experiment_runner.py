import json
import uuid

import structlog
from django.core.exceptions import ObjectDoesNotExist
from django.db import close_old_connections
from django.db.models import Count, Q

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
from model_hub.models.choices import (
    CellStatus,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentsTable,
    PendingRowTask,
)
from model_hub.models.openai_tools import Tools
from model_hub.models.run_prompt import UserResponseSchema
from model_hub.models.tts_voices import TTSVoice
from model_hub.services.column_service import create_experiment_column
from model_hub.services.experiment_utils import is_experiment_cancelled
from model_hub.utils.utils import remove_empty_text_from_messages
from model_hub.views.eval_runner import EvaluationRunner, bulk_update_or_create_cells
from model_hub.views.run_prompt import populate_placeholders
from tfc.temporal import temporal_activity
from tfc.utils.error_codes import get_error_message


def _get_or_create_eval_column(
    dataset,
    column_config: dict,
    experiment_dataset=None,
):
    """Create or look up a result column for a composite eval metric.

    Mirrors `EvaluationRunner._create_or_update_column` but without
    requiring a runner instance — the composite path pre-creates columns
    at experiment setup, before any runner has been constructed. Adds
    the column to the experiment's M2M when running under an experiment
    and appends to `dataset.column_order` for normal dataset runs.
    """
    from django.db import transaction

    from model_hub.models.develop_dataset import Column, Dataset

    with transaction.atomic():
        dataset_obj = Dataset.no_workspace_objects.select_for_update().get(
            id=dataset.id
        )
        try:
            column, created = Column.objects.get_or_create(**column_config)
        except Exception:
            column = Column.objects.get(
                dataset=column_config["dataset"],
                source=column_config["source"],
                source_id=column_config["source_id"],
                deleted=False,
            )
            created = False

        if created:
            if experiment_dataset:
                column.status = StatusType.RUNNING.value
                column.save(update_fields=["status"])
                experiment_dataset.columns.add(column)
            else:
                order = dataset_obj.column_order or []
                order.append(str(column.id))
                dataset_obj.column_order = order
                dataset_obj.save(update_fields=["column_order"])

    return column


def check_and_update_column_status(column_id: uuid.UUID):
    """Check if all cells in a column are either PASS or ERROR, and update column status if complete.
    If column is complete and it's an experiment column, trigger evaluations.

    Uses optimistic updates instead of row locks to avoid connection timeouts.
    """
    try:
        # Read without locks - allows concurrent reads
        column = (
            Column.objects.select_related("dataset")
            .filter(id=column_id, deleted=False)
            .first()
        )
        if not column:
            return False

        current_status = column.status

        # Already completed - nothing to do
        if current_status == StatusType.COMPLETED.value:
            return True

        # OPTIMIZATION: Use count() instead of exists() for single query
        total_cells = Cell.objects.filter(column=column, deleted=False).count()

        if total_cells == 0:
            # FIX: Don't mark column as COMPLETED if dataset has rows but column has no cells.
            # This means the column hasn't started processing yet (e.g., new eval column).
            # Only mark as COMPLETED if the dataset truly has no rows.
            if column.dataset:
                expected_rows = Row.objects.filter(
                    dataset=column.dataset, deleted=False
                ).count()
                if expected_rows > 0:
                    # Dataset has rows but column has no cells - NOT complete yet
                    logger.debug(
                        f"Column {column_id} has 0 cells but dataset has {expected_rows} rows - not complete yet"
                    )
                    return False

            # No cells AND no expected rows - column is complete
            Column.objects.filter(
                id=column_id,
                deleted=False,
                status=current_status,  # Optimistic lock
            ).update(status=StatusType.COMPLETED.value)
            # Always return True - column is complete (either we updated it or another worker did)
            return True

        # Check if there are any pending row tasks (REGISTERED or PROCESSING) for this column
        pending_tasks_count = PendingRowTask.objects.filter(
            column=column,
            status__in=[
                PendingRowTask.TaskStatus.REGISTERED,
                PendingRowTask.TaskStatus.PROCESSING,
            ],
        ).count()

        # OPTIMIZATION: Single query to check if any cells are RUNNING
        running_count = Cell.objects.filter(
            column=column, deleted=False, status=CellStatus.RUNNING.value
        ).count()

        # Column is still running if there are running cells or pending tasks
        if running_count > 0 or pending_tasks_count > 0:
            return False

        # All cells are either PASS or ERROR - column is complete
        # Use optimistic update - only update if status is still what we read
        updated = Column.objects.filter(
            id=column_id,
            deleted=False,
            status=current_status,  # Optimistic lock
        ).update(status=StatusType.COMPLETED.value)

        if updated > 0:
            logger.info(
                f"Column {column_id} marked as completed - all cells are PASS or ERROR"
            )

            # Note: Evaluations are now triggered row-wise in _process_row_impl
            # after each row completes, so we don't need to trigger them here
            # when the column completes. This allows incremental evaluation processing.

            # Update experiment_dataset status when any experiment-related column completes
            if column.source == SourceChoices.EXPERIMENT.value:
                # This is a result column - source_id is the experiment_dataset.id directly
                if column.source_id:
                    try:
                        experiment_dataset_id = uuid.UUID(str(column.source_id))
                        check_and_update_experiment_dataset_status(
                            experiment_dataset_id
                        )
                    except (ValueError, TypeError) as e:
                        logger.exception(
                            f"Error parsing experiment_dataset_id from result column {column_id} source_id {column.source_id}: {e}"
                        )
                    except Exception as e:
                        logger.exception(
                            f"Error checking experiment_dataset status for result column {column_id}: {e}"
                        )
            elif column.source in [
                SourceChoices.EXPERIMENT_EVALUATION.value,
                SourceChoices.EVALUATION_REASON.value,
            ]:
                # This is an evaluation column - source_id has pattern "{experiment_dataset.id}-sourceid-{eval_metric_id}"
                if column.source_id and "-sourceid-" in str(column.source_id):
                    try:
                        # Extract experiment_dataset_id from source_id
                        experiment_dataset_id_str = str(column.source_id).split(
                            "-sourceid-"
                        )[0]
                        experiment_dataset_id = uuid.UUID(experiment_dataset_id_str)
                        # Check if experiment_dataset is complete
                        check_and_update_experiment_dataset_status(
                            experiment_dataset_id
                        )
                    except (ValueError, IndexError) as e:
                        logger.exception(
                            f"Error extracting experiment_dataset_id from eval column {column_id} source_id {column.source_id}: {e}"
                        )
                    except Exception as e:
                        logger.exception(
                            f"Error checking experiment_dataset status for eval column {column_id}: {e}"
                        )

        return True
    except Exception as e:
        logger.exception(f"Error checking column status for {column_id}: {e}")
        return False


def _trigger_row_wise_evaluations(
    row_id,
    column_id,
    dataset_id,
    experiment_id,
):
    """
    Trigger evaluations for a single row as soon as it's processed.
    This allows evaluations to run incrementally instead of waiting for all rows.
    """
    try:
        # Get the column to check if it's an experiment column
        try:
            column = Column.objects.get(id=column_id, deleted=False)
        except Column.DoesNotExist:
            return

        # Only trigger for experiment columns
        if column.source != SourceChoices.EXPERIMENT.value or not column.source_id:
            return

        # Get experiment_dataset and experiment
        try:
            experiment_dataset = ExperimentDatasetTable.objects.select_related(
                "experiment"
            ).get(id=column.source_id)
            experiment = experiment_dataset.experiment

            if not experiment:
                return
        except ExperimentDatasetTable.DoesNotExist:
            return

        # Get eval templates for this experiment
        eval_templates = list(experiment.user_eval_template_ids.all())

        if not eval_templates:
            return

        dataset = column.dataset

        # Process each eval template
        for eval_template in eval_templates:
            try:
                # Prepare config for runner_params (same as eval_runner.py pattern)
                config = {
                    "dataset_id": str(experiment_dataset.id),
                    "input": str(column.id),
                    "experiment_id": str(experiment.id),
                    "source": "experiment",
                }

                # Prepare runner_params for process_eval_batch_async_task
                # Follow exact same pattern as eval_runner.py (lines 1671-1689)
                runner_params = {
                    "user_eval_metric_id": str(eval_template.id),
                    "experiment_dataset_id": (
                        str(experiment_dataset.id) if experiment_dataset else None
                    ),
                    "optimize_id": None,  # Not used for experiments
                    "is_only_eval": False,
                    "format_output": False,
                    "cancel_event": None,
                    "futureagi_eval": False,
                    "protect": False,
                    "protect_flash": False,
                    "source": "experiment",
                    "source_id": str(eval_template.template.id),
                    "source_configs": config or {},
                }

                # Pass result column ID (input column) - async task will create evaluation column
                # Follow exact same pattern as eval_runner.py (lines 1691-1705)
                result_column_id = str(column.id) if column else None

                # Trigger evaluation for this single row
                # Import here to avoid circular import
                from model_hub.tasks.user_evaluation import (
                    process_eval_batch_async_task,
                )

                process_eval_batch_async_task.apply_async(
                    args=(
                        result_column_id,
                        [str(row_id)],  # Single row in a list
                        runner_params,
                    ),
                    queue="tasks_l",
                )

                logger.debug(
                    f"Triggered row-wise evaluation for row {row_id}, eval_template {eval_template.id}, result_column {result_column_id}"
                )

            except Exception as eval_error:
                logger.exception(
                    f"Error triggering row-wise evaluation for row {row_id}, eval_template {eval_template.id}: {eval_error}"
                )

    except Exception as e:
        logger.exception(
            f"Error in _trigger_row_wise_evaluations for row {row_id}: {e}"
        )


def check_and_update_experiment_dataset_status(experiment_dataset_id: uuid.UUID):
    """Check if all columns in an experiment_dataset are complete, and update status if complete.

    Uses optimistic updates instead of row locks to avoid connection timeouts.
    """
    try:
        # Read without locks - allows concurrent reads
        experiment_dataset = ExperimentDatasetTable.objects.filter(
            id=experiment_dataset_id
        ).first()
        if not experiment_dataset:
            return False

        current_status = experiment_dataset.status

        # Get result columns (source_id = experiment_dataset.id)
        result_column_ids = list(
            Column.objects.filter(
                source_id=str(experiment_dataset_id), deleted=False
            ).values_list("id", flat=True)
        )

        # CRITICAL FIX: Also get evaluation columns for this experiment_dataset
        # Eval columns have source_id like "{experiment_dataset.id}-sourceid-{eval_metric_id}"
        eval_column_ids = list(
            Column.objects.filter(
                source_id__startswith=f"{experiment_dataset_id}-sourceid-",
                deleted=False,
            ).values_list("id", flat=True)
        )

        # Combine all column IDs (result columns + evaluation columns)
        all_column_ids = list(set(result_column_ids + eval_column_ids))

        if not all_column_ids:
            # No columns means dataset is complete - use optimistic update
            if current_status != StatusType.COMPLETED.value:
                ExperimentDatasetTable.objects.filter(
                    id=experiment_dataset_id,
                    status=current_status,  # Optimistic lock
                ).update(status=StatusType.COMPLETED.value)

            # CRITICAL FIX: Always check experiment status when dataset is complete
            experiment = experiment_dataset.experiment
            if experiment:
                logger.info(
                    f"No columns for experiment_dataset {experiment_dataset_id}, "
                    f"checking experiment {experiment.id} status"
                )
                check_and_update_experiment_status(experiment.id)
            return True

        # CRITICAL FIX: Before checking column statuses, update any columns that have
        # status=RUNNING but no running cells. This handles reason columns and other
        # columns whose status wasn't properly updated after their cells completed.
        running_columns = list(
            Column.objects.filter(
                id__in=all_column_ids, status=StatusType.RUNNING.value, deleted=False
            ).values_list("id", flat=True)
        )

        if running_columns:
            # For each running column, check if it actually has running cells
            # If no running cells exist, mark the column as completed
            for col_id in running_columns:
                running_cells = Cell.objects.filter(
                    column_id=col_id, deleted=False, status=CellStatus.RUNNING.value
                ).exists()
                if not running_cells:
                    # No running cells - column should be completed
                    Column.objects.filter(
                        id=col_id, status=StatusType.RUNNING.value, deleted=False
                    ).update(status=StatusType.COMPLETED.value)
                    logger.info(
                        f"Updated column {col_id} to COMPLETED (no running cells)"
                    )

        # Re-check: Single bulk query to check if any columns are still RUNNING
        running_count = Column.objects.filter(
            id__in=all_column_ids, status=StatusType.RUNNING.value
        ).count()

        if running_count > 0:
            return False

        # All columns are complete - experiment_dataset is complete
        # Use optimistic update - only update if status is still what we read
        if current_status != StatusType.COMPLETED.value:
            updated = ExperimentDatasetTable.objects.filter(
                id=experiment_dataset_id,
                status=current_status,  # Optimistic lock
            ).update(status=StatusType.COMPLETED.value)

            if updated > 0:
                logger.info(
                    f"ExperimentDataset {experiment_dataset_id} marked as completed"
                )

        # NOTE: We intentionally do NOT cascade to check_and_update_experiment_status here.
        # In Temporal workflows, base evaluations run in parallel with prompt processing.
        # If we check experiment status here, the experiment might be marked as COMPLETED
        # before base evaluations finish (because their columns/cells might not exist yet).
        # The main RunExperimentWorkflow calls check_experiment_status_activity explicitly
        # AFTER all base evaluations complete to ensure proper sequencing.

        return True
    except Exception as e:
        logger.exception(
            f"Error checking experiment_dataset status for {experiment_dataset_id}: {e}"
        )
        return False


def check_and_update_experiment_status(experiment_id: uuid.UUID):
    """Check if all cells in all columns related to the experiment are PASS or ERROR, and update experiment status if complete.

    Uses optimistic updates instead of row locks to avoid connection timeouts.
    """
    try:
        logger.info(f"Checking experiment status for {experiment_id}")

        # Read without locks - allows concurrent reads
        experiment = ExperimentsTable.objects.filter(
            id=experiment_id, deleted=False
        ).first()
        if not experiment:
            return False

        current_status = experiment.status

        # Already completed - nothing to do
        if current_status == StatusType.COMPLETED.value:
            return True

        # Get all experiment_datasets for this experiment
        experiment_datasets = list(experiment.experiment_datasets.filter(deleted=False))

        logger.info(f"Experiment datasets: {experiment_datasets}")

        if not experiment_datasets:
            # No datasets means experiment might be complete, but check columns directly
            # OPTIMIZATION: Get column IDs in one query
            column_ids = list(
                Column.objects.filter(
                    experiments_dataset_column__experiment=experiment,
                    deleted=False,
                ).values_list("id", flat=True)
            )

            if not column_ids:
                logger.info(f"No columns for experiment {experiment_id}")
                # No columns - use optimistic update
                ExperimentsTable.objects.filter(
                    id=experiment_id,
                    deleted=False,
                    status=current_status,  # Optimistic lock
                ).update(status=StatusType.COMPLETED.value)
                return True

            # OPTIMIZATION: Single bulk query to check all cells at once
            running_count = Cell.objects.filter(
                column_id__in=column_ids,
                deleted=False,
                status=CellStatus.RUNNING.value,
            ).count()

            logger.info(f"running_count: {running_count} {experiment_id}")

            if running_count > 0:
                return False

            # All columns are COMPLETED and all cells are PASS or ERROR
            # Use optimistic update
            updated = ExperimentsTable.objects.filter(
                id=experiment_id,
                deleted=False,
                status=current_status,  # Optimistic lock
            ).update(status=StatusType.COMPLETED.value)

            if updated > 0:
                logger.info(
                    f"Experiment {experiment_id} marked as completed - all columns are COMPLETED and all cells are PASS or ERROR"
                )
            return True

        # REMOVED: Don't check experiment_dataset status - check columns directly instead
        # The experiment_dataset status might be out of sync with actual column status
        # We rely on column/cell status checks below as the source of truth

        # Get columns linked to THIS experiment via FK relationship.
        # Uses experiment -> experiment_datasets -> columns to correctly filter
        # to only columns that belong to this specific experiment run.
        all_column_ids = list(
            Column.objects.filter(
                experiments_dataset_column__experiment=experiment,
                deleted=False,
            ).values_list("id", flat=True)
        )

        if not all_column_ids:
            # No columns linked to experiment - mark as completed
            ExperimentsTable.objects.filter(
                id=experiment_id,
                deleted=False,
                status=current_status,  # Optimistic lock
            ).update(status=StatusType.COMPLETED.value)
            return True

        # CRITICAL FIX: Before checking column statuses, update any columns that have
        # status=RUNNING but no running cells. This handles reason columns and other
        # columns whose status wasn't properly updated after their cells completed.
        if all_column_ids:
            running_columns = list(
                Column.objects.filter(
                    id__in=all_column_ids,
                    status=StatusType.RUNNING.value,
                    deleted=False,
                ).values_list("id", flat=True)
            )

            if running_columns:
                for col_id in running_columns:
                    running_cells = Cell.objects.filter(
                        column_id=col_id, deleted=False, status=CellStatus.RUNNING.value
                    ).exists()
                    if not running_cells:
                        Column.objects.filter(
                            id=col_id, status=StatusType.RUNNING.value, deleted=False
                        ).update(status=StatusType.COMPLETED.value)
                        logger.info(
                            f"Updated column {col_id} to COMPLETED (no running cells) for experiment {experiment_id}"
                        )

            # Re-check if any columns are still RUNNING after fixing
            running_column_count = Column.objects.filter(
                id__in=all_column_ids,
                deleted=False,
                status=StatusType.RUNNING.value,
            ).count()
            logger.info(f"running_column_count: {running_column_count} {experiment_id}")

            if running_column_count > 0:
                return False

        # OPTIMIZATION: Single bulk query to check all cells at once
        if all_column_ids:
            running_count = Cell.objects.filter(
                column_id__in=all_column_ids,
                deleted=False,
                status=CellStatus.RUNNING.value,
            ).count()
            logger.info(f"running_count: {running_count} {experiment_id}")
            if running_count > 0:
                return False

        # OPTIMIZATION: Also check eval columns (base columns) with bulk query
        # Convert UUIDs to strings for CharField comparison
        eval_template_ids = [
            str(id)
            for id in experiment.user_eval_template_ids.values_list("id", flat=True)
        ]
        if eval_template_ids:
            eval_column_ids = list(
                Column.objects.filter(
                    source_id__in=eval_template_ids, deleted=False
                ).values_list("id", flat=True)
            )
        else:
            eval_column_ids = []

        # CRITICAL FIX: Check if any base eval columns are still RUNNING
        # First, fix any columns that have status=RUNNING but no running cells
        if eval_column_ids:
            running_eval_columns = list(
                Column.objects.filter(
                    id__in=eval_column_ids,
                    status=StatusType.RUNNING.value,
                    deleted=False,
                ).values_list("id", flat=True)
            )

            if running_eval_columns:
                for col_id in running_eval_columns:
                    running_cells = Cell.objects.filter(
                        column_id=col_id, deleted=False, status=CellStatus.RUNNING.value
                    ).exists()
                    if not running_cells:
                        Column.objects.filter(
                            id=col_id, status=StatusType.RUNNING.value, deleted=False
                        ).update(status=StatusType.COMPLETED.value)
                        logger.info(
                            f"Updated base eval column {col_id} to COMPLETED (no running cells) for experiment {experiment_id}"
                        )

            # Re-check if any base eval columns are still RUNNING
            running_column_count = Column.objects.filter(
                id__in=eval_column_ids,
                deleted=False,
                status=StatusType.RUNNING.value,
            ).count()
            logger.info(
                f"running_column_count base eval: {running_column_count} {experiment_id}"
            )

            if running_column_count > 0:
                return False

            running_count = Cell.objects.filter(
                column_id__in=eval_column_ids,
                deleted=False,
                status=CellStatus.RUNNING.value,
            ).count()
            logger.info(
                f"running_count base eval cells: {running_count} {experiment_id}"
            )

            if running_count > 0:
                return False

        # All columns are COMPLETED and all cells are PASS or ERROR - experiment is complete
        # Use optimistic update - only update if status is still what we read
        updated = ExperimentsTable.objects.filter(
            id=experiment_id,
            deleted=False,
            status=current_status,  # Optimistic lock
        ).update(status=StatusType.COMPLETED.value)

        if updated > 0:
            logger.info(
                f"Experiment {experiment_id} marked as completed - all columns are COMPLETED and all cells are PASS or ERROR"
            )
        return True
    except Exception as e:
        logger.exception(f"Error checking experiment status for {experiment_id}: {e}")
        return False


def normalize_voice_config(run_prompt_config: dict, organization_id: uuid.UUID) -> dict:
    """
    Normalizes voice configuration in run_prompt_config:
    - If voice is an array, takes the first element
    - Tries to look up the voice in the database (don't check if it's UUID, just try)
    - If found (custom voice): sets voice (name) and voice_id (UUID)
    - If not found (system voice): keeps voice as-is and sets voice_id to the same value

    Args:
        run_prompt_config: The run_prompt_config dictionary
        organization_id: The organization ID for filtering custom voices

    Returns:
        Updated run_prompt_config with normalized voice and voice_id
    """
    if not run_prompt_config or not isinstance(run_prompt_config, dict):
        return run_prompt_config

    # Handle voice (may be an array or UUID string)
    voice_val = run_prompt_config.get("voice")
    if voice_val is not None:
        # If it's an array, take the first element
        if isinstance(voice_val, list):
            if len(voice_val) > 0:
                voice_val = voice_val[0]
            else:
                voice_val = None

        if voice_val:
            voice_str = str(voice_val).strip()
            if voice_str:
                # Try to look up the voice in the database (don't check if it's UUID, just try)
                tts_voice = None
                try:
                    # Try as UUID first
                    voice_uuid = uuid.UUID(voice_str)
                    tts_voice = TTSVoice.objects.filter(
                        id=voice_uuid, organization_id=organization_id, deleted=False
                    ).first()
                except (ValueError, TypeError):
                    # Not a valid UUID, try as name (though unlikely to match)
                    tts_voice = TTSVoice.objects.filter(
                        name=voice_str, organization_id=organization_id, deleted=False
                    ).first()

                if tts_voice:
                    # Custom voice found - set both voice (name) and voice_id (provider voice_id)
                    run_prompt_config["voice"] = tts_voice.name
                    run_prompt_config["voice_id"] = str(tts_voice.voice_id)
                else:
                    # System voice - not in DB, keep voice as-is but don't set voice_id
                    # Let the handler map the voice name via VOICE_ID_MAP
                    run_prompt_config["voice"] = voice_str
                    # Don't set voice_id for system voices - handler will map voice name to ID

    return run_prompt_config


class ExperimentRunner:
    def __init__(self, experiment_id: uuid.UUID, cancel_event=None):
        self.experiment_id = experiment_id
        self.experiment = None
        self.dataset = None
        self.cancel_event = cancel_event

    def load_experiment(self):
        """Load experiment and related dataset (uses snapshot_dataset for V2)"""
        try:
            self.experiment = ExperimentsTable.objects.select_related(
                "dataset", "snapshot_dataset"
            ).get(id=self.experiment_id, deleted=False)
            # V2: use snapshot_dataset where experiment data lives; fall back for V1
            self.dataset = self.experiment.snapshot_dataset or self.experiment.dataset
        except ObjectDoesNotExist as e:
            raise ValueError("Invalid experiment ID or dataset does not exist.") from e

    def run_additional_evaluations(self, eval_template_ids: list[str]):
        """Run additional evaluations on existing experiment results"""
        try:
            # Get all experiment datasets for this experiment
            if self.experiment is None:
                return
            experiment_datasets = self.experiment.experiment_datasets.filter(
                deleted=False
            )

            for experiment_dataset in experiment_datasets:
                # Get the corresponding result columns
                if not self.cancel_event:
                    columns = list(
                        Column.objects.filter(source_id=experiment_dataset.id)
                    )
                    # For agents: only evaluate terminal columns
                    terminal_cols = [
                        c
                        for c in columns
                        if c.metadata and c.metadata.get("is_terminal")
                    ]
                    if not terminal_cols:
                        terminal_cols = columns[:1]

                    for column in terminal_cols:
                        # Run each new evaluation
                        for eval_template_id in eval_template_ids:
                            self.run_evaluations(
                                column, eval_template_id, experiment_dataset
                            )
                            user_eval_metric = UserEvalMetric.objects.get(
                                id=eval_template_id
                            )
                            self.run_base_col_eval(user_eval_metric)

            return True
        except Exception as e:
            logger.exception(f"Error running additional evaluations: {e}")
            return False

    def empty_or_create_evals_column(self, eval_template_ids=None):
        user_evals_qs = self.experiment.user_eval_template_ids.select_related(
            "template"
        )
        if eval_template_ids:
            user_evals_qs = user_evals_qs.filter(id__in=eval_template_ids)

        user_evals = list(user_evals_qs)
        experiment_datasets = list(
            self.experiment.experiment_datasets.filter(deleted=False)
        )

        all_columns = list(
            Column.objects.filter(
                experiments_dataset_column__experiment=self.experiment
            )
        )
        # Build multimap: source_id -> [columns] to handle agents with
        # multiple output columns sharing the same source_id (EDT id)
        from collections import defaultdict

        cols_by_source = defaultdict(list)
        for col in all_columns:
            cols_by_source[str(col.source_id)].append(col)

        base_columns = list(
            Column.objects.filter(
                source_id__in=[ue.id for ue in user_evals], deleted=False
            )
        )
        eval_cols = {str(col.source_id): col for col in base_columns}

        cols_ids_to_update = []

        for user_eval_metric in user_evals:
            # Mirror dataset behavior (develop_dataset.py:7102-7128): every
            # eval gets a reason column unconditionally. Experiments used to
            # gate on `config.reason_column`, which forced API callers to
            # opt in for parity that's automatic on the dataset side.
            config_reason_column = True

            for experiment_dataset in experiment_datasets:
                exp_id = str(experiment_dataset.id)
                columns = cols_by_source.get(exp_id, [])

                # For agents: only create eval columns for terminal output
                # columns. For prompts: use the single output column.
                terminal_cols = [
                    c for c in columns if c.metadata and c.metadata.get("is_terminal")
                ]
                if not terminal_cols:
                    terminal_cols = columns[:1]

                is_composite = user_eval_metric.template.template_type == "composite"

                for column in terminal_cols:
                    config = {
                        "dataset_id": exp_id,
                        "input": str(column.id),
                        "evaluator_id": str(user_eval_metric.template.evaluator_id),
                        "experiment_id": str(self.experiment.id),
                        "source": "experiment",
                    }

                    if is_composite:
                        from model_hub.tasks.composite_runner import (
                            CompositeEvaluationRunner,
                        )

                        column_config = CompositeEvaluationRunner.build_column_config(
                            user_eval_metric=user_eval_metric,
                            dataset=self.dataset,
                            experiment_dataset=experiment_dataset,
                            column=column,
                        )
                        eval_column = _get_or_create_eval_column(
                            self.dataset,
                            column_config,
                            experiment_dataset=experiment_dataset,
                        )
                    else:
                        runner = EvaluationRunner(
                            user_eval_metric_id=user_eval_metric.id,
                            experiment_dataset=experiment_dataset,
                            column=column,
                            source="experiment",
                            source_id=user_eval_metric.template.id,
                            source_configs=config,
                        )
                        runner.load_user_eval_metric()
                        column_config = runner._get_column_config(self.dataset)
                        eval_column = runner._create_or_update_column(
                            self.dataset, column_config
                        )
                        runner.load_user_eval_metric()

                        if config_reason_column:
                            reason_column = runner._create_reason_column(
                                self.dataset,
                                f"{user_eval_metric.name}-reason",
                                parent_column=eval_column,
                            )
                            if reason_column is not None:
                                cols_ids_to_update.append(reason_column.id)

                    cols_ids_to_update.append(eval_column.id)

            # BASE COLUMN — only create eval columns if experiment has a base column
            if self.experiment.column and not eval_cols.get(str(user_eval_metric.id)):
                if user_eval_metric.template.template_type == "composite":
                    from model_hub.tasks.composite_runner import (
                        CompositeEvaluationRunner,
                    )

                    column_config = CompositeEvaluationRunner.build_column_config(
                        user_eval_metric=user_eval_metric,
                        dataset=self.dataset,
                    )
                    _get_or_create_eval_column(self.dataset, column_config)
                else:
                    runner = EvaluationRunner(
                        user_eval_metric_id=user_eval_metric.id, is_only_eval=True
                    )
                    runner.load_user_eval_metric()
                    column_config = runner._get_column_config(self.dataset)
                    column = runner._create_or_update_column(
                        self.dataset, column_config, new_column=True
                    )
                    runner.load_user_eval_metric()
                    if config_reason_column:
                        reason_column = runner._create_reason_column(
                            self.dataset, f"{user_eval_metric.name}-reason"
                        )

        # Batch update cell statuses
        Cell.objects.filter(column_id__in=cols_ids_to_update).update(
            value_infos=json.dumps({}), value="", status=CellStatus.RUNNING.value
        )

    def precreate_eval_columns_for_edts(self, edt_ids):
        """Pre-create per-EDT eval columns for specific EDTs only.

        Unlike empty_or_create_evals_column which iterates ALL EDTs and
        resets all cells, this method targets only the given EDTs and
        does NOT reset any cells.  Used by the PUT view to create eval
        column placeholders for newly added prompt/agent configs so the
        FE can render them on the first poll.
        """
        from collections import defaultdict

        user_evals = list(
            self.experiment.user_eval_template_ids.select_related("template").all()
        )
        if not user_evals:
            return

        experiment_datasets = list(
            self.experiment.experiment_datasets.filter(id__in=edt_ids, deleted=False)
        )
        if not experiment_datasets:
            return

        # Get output columns for these EDTs (via M2M)
        all_columns = list(
            Column.objects.filter(
                experiments_dataset_column__in=experiment_datasets,
                deleted=False,
            )
        )
        cols_by_source = defaultdict(list)
        for col in all_columns:
            cols_by_source[str(col.source_id)].append(col)

        for user_eval_metric in user_evals:
            # Always-on for experiments (parity with empty_or_create_evals_column).
            config_reason_column = True

            is_composite = user_eval_metric.template.template_type == "composite"

            for edt in experiment_datasets:
                columns = cols_by_source.get(str(edt.id), [])
                # For agents: only target terminal output columns
                terminal_cols = [
                    c for c in columns if c.metadata and c.metadata.get("is_terminal")
                ]
                if not terminal_cols:
                    terminal_cols = columns[:1]

                for column in terminal_cols:
                    if is_composite:
                        from model_hub.tasks.composite_runner import (
                            CompositeEvaluationRunner,
                        )

                        column_config = CompositeEvaluationRunner.build_column_config(
                            user_eval_metric=user_eval_metric,
                            dataset=self.dataset,
                            experiment_dataset=edt,
                            column=column,
                        )
                        _get_or_create_eval_column(
                            self.dataset, column_config, experiment_dataset=edt
                        )
                        continue

                    config = {
                        "dataset_id": str(edt.id),
                        "input": str(column.id),
                        "evaluator_id": str(user_eval_metric.template.evaluator_id),
                        "experiment_id": str(self.experiment.id),
                        "source": "experiment",
                    }
                    runner = EvaluationRunner(
                        user_eval_metric_id=user_eval_metric.id,
                        experiment_dataset=edt,
                        column=column,
                        source="experiment",
                        source_id=user_eval_metric.template.id,
                        source_configs=config,
                    )
                    runner.load_user_eval_metric()
                    column_config = runner._get_column_config(self.dataset)
                    pre_eval_column = runner._create_or_update_column(
                        self.dataset, column_config
                    )

                    if config_reason_column:
                        runner._create_reason_column(
                            self.dataset,
                            f"{user_eval_metric.name}-reason",
                            parent_column=pre_eval_column,
                        )

    @staticmethod
    def create_agent_output_columns(eac, snapshot_dataset_id):
        """Create output columns for an agent's LLM/subgraph nodes.

        Idempotent — uses get_or_create so safe to call from both the
        PUT view (for pre-creation) and setup_agent_activity (workflow).

        Also runs topology analysis to mark terminal columns with
        ``metadata.is_terminal = True``.

        Args:
            eac: ExperimentAgentConfig instance (with graph_version,
                 graph_version.graph, experiment_dataset loaded).
            snapshot_dataset_id: UUID or str of the snapshot dataset.

        Returns:
            (node_column_mapping, terminal_column_ids):
                node_column_mapping: dict[str, str] — node_id → column_id
                terminal_column_ids: list[str] — column IDs for end nodes
        """
        from django.db.models import Q

        from agent_playground.models.choices import NodeType
        from agent_playground.models.node import Node
        from model_hub.models.choices import SourceChoices, StatusType
        from model_hub.models.develop_dataset import Column

        experiment_dataset = eac.experiment_dataset

        agent_nodes = (
            Node.objects.filter(
                graph_version=eac.graph_version,
                deleted=False,
            )
            .filter(Q(node_template__name="llm_prompt") | Q(type=NodeType.SUBGRAPH))
            .select_related("node_template")
        )

        node_column_mapping = {}
        terminal_column_ids = []

        for node in agent_nodes:
            col_name = (
                f"{eac.graph_version.graph.name}"
                f"-v{eac.graph_version.version_number}"
                f"-{node.name}"
            )
            column, column_created = Column.objects.get_or_create(
                name=col_name,
                data_type="text",
                source=SourceChoices.EXPERIMENT.value,
                dataset_id=str(snapshot_dataset_id),
                source_id=str(experiment_dataset.id),
                defaults={
                    "status": StatusType.NOT_STARTED.value,
                    "metadata": {"node_id": str(node.id)},
                },
            )

            if column_created:
                experiment_dataset.columns.add(column)
            else:
                if not column.metadata:
                    column.metadata = {}
                if "node_id" not in column.metadata:
                    column.metadata["node_id"] = str(node.id)
                    column.save(update_fields=["metadata"])

            node_column_mapping[str(node.id)] = str(column.id)
            if column.metadata and column.metadata.get("is_terminal"):
                terminal_column_ids.append(str(column.id))

        # If no terminal columns marked, run topology analysis
        if not terminal_column_ids and node_column_mapping:
            try:
                from agent_playground.services.engine.analyzer import GraphAnalyzer

                topology = GraphAnalyzer.analyze(eac.graph_version_id)
                agent_node_id_set = set(node_column_mapping.keys())
                terminal_node_ids = {
                    str(nid) for nid in topology.end_node_ids
                } & agent_node_id_set

                if not terminal_node_ids:
                    for nid in reversed(topology.topological_order):
                        if str(nid) in agent_node_id_set:
                            terminal_node_ids = {str(nid)}
                            break

                for nid in terminal_node_ids:
                    col_id = node_column_mapping[nid]
                    terminal_column_ids.append(col_id)
                    tcol = Column.objects.get(id=col_id)
                    if not tcol.metadata:
                        tcol.metadata = {}
                    tcol.metadata["is_terminal"] = True
                    tcol.save(update_fields=["metadata"])
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "Failed topology analysis for terminal columns, "
                    "falling back to last column"
                )
                terminal_column_ids = [list(node_column_mapping.values())[-1]]

        return node_column_mapping, terminal_column_ids

    def process_prompt(self, prompt_config: dict, model_spec):
        """Process a single prompt configuration for a specific model

        Args:
            prompt_config: The prompt configuration dict
            model_spec: Either a string (model name) or dict with 'name', 'config', 'display_name'
        """
        if self.experiment is None:
            return

        # Handle both string and dict model specifications
        if isinstance(model_spec, dict):
            model = model_spec.get("name")
            if not model:
                raise ValueError(
                    f"ModelSpec missing required 'name' field or name is empty: {model_spec}"
                )
            run_prompt_config_for_model = model_spec.get("config", {})
            display_suffix = model_spec.get("display_name") or model
        else:
            # Backward compatible: model_spec is a string
            model = model_spec
            display_suffix = model
            # Get the specific config for the current model from the run_prompt_config dictionary
            run_prompt_config_for_model = prompt_config.get(
                "run_prompt_config", {}
            ).get(model)

        # Merge model-specific parameters from modelParams if available
        if run_prompt_config_for_model is None:
            run_prompt_config_for_model = {}
        model_params_from_payload = prompt_config.get("model_params", {}).get(model, {})
        run_prompt_config_for_model.update(model_params_from_payload)

        # Normalize voice_id (handle array, lookup name from DB)
        if self.experiment and self.experiment.dataset:
            run_prompt_config_for_model = normalize_voice_config(
                run_prompt_config_for_model, self.experiment.dataset.organization.id
            )

        # If audio output and a per-model voice is provided, include the voice in the column suffix
        try:
            voice = None
            if isinstance(run_prompt_config_for_model, dict):
                voice = run_prompt_config_for_model.get("voice")
            if prompt_config.get("output_format") == "audio" and voice is not None:
                voice_str = str(voice).strip()
                if voice_str:
                    # Avoid duplicating voice if already present in display_suffix (case-insensitive)
                    if voice_str.lower() not in str(display_suffix).lower():
                        display_suffix = f"{display_suffix}-{voice_str}"
        except Exception:
            # Non-fatal: naming enhancement should not block processing
            pass

        column_name = f"{self.experiment.name}-{prompt_config['name']}-{display_suffix}"
        close_old_connections()

        # Check if entry exists within THIS experiment's relationship (important for reruns)
        experiment_dataset = self.experiment.experiment_datasets.filter(
            name=column_name, deleted=False
        ).first()

        dataset_created = False

        if not experiment_dataset:
            # Create EDT with FK to experiment
            try:
                experiment_dataset, dataset_created = (
                    ExperimentDatasetTable.objects.get_or_create(
                        name=column_name,
                        experiment=self.experiment,
                        defaults={
                            "status": StatusType.RUNNING.value,
                        },
                    )
                )
            except ExperimentDatasetTable.MultipleObjectsReturned:
                experiment_dataset = ExperimentDatasetTable.objects.filter(
                    name=column_name, experiment=self.experiment
                ).first()
                dataset_created = False

        # Update status to RUNNING for re-runs
        if experiment_dataset and not dataset_created:
            experiment_dataset.status = StatusType.RUNNING.value
            experiment_dataset.save(update_fields=["status"])

        # Create column for experiment results
        column, column_created = create_experiment_column(
            dataset=self.dataset,
            source_id=experiment_dataset.id,
            name=column_name,
            output_format=prompt_config.get("output_format", "string"),
            response_format=prompt_config.get("response_format"),
            status=StatusType.RUNNING.value,
        )

        if column_created:
            experiment_dataset.columns.add(column)

        # Add to experiment's experiments_datasets
        if dataset_created and self.experiment:
            self.experiment.experiments_datasets.add(experiment_dataset)

        # Process each row
        rows = (
            Row.objects.filter(dataset_id=self.dataset.id, deleted=False)
            .order_by("order")
            .all()
        )
        empty_values = {
            "value": "",
            "status": CellStatus.RUNNING.value,
            "value_infos": json.dumps({}),
        }
        bulk_update_or_create_cells(
            rows.values_list("id", flat=True), column.id, self.dataset.id, empty_values
        )

        # Spawn row processing tasks
        for row in rows:
            process_row_task.apply_async(
                args=(
                    str(row.id),
                    str(column.id),
                    str(self.dataset.id),
                    str(self.experiment.id),
                    prompt_config["messages"],
                    model,
                    prompt_config["configuration"],
                    prompt_config.get("output_format"),
                    run_prompt_config_for_model,
                ),
                queue="tasks_l",
            )

        # Note: Column status will be updated by process_row_task when all rows complete
        # Evaluations will be triggered automatically when column completes
        logger.info(
            f"process_prompt spawned {len(rows)} row tasks for column {column.id}"
        )

        # Note: ExperimentDatasetTable status will be updated when all columns complete

        close_old_connections()
        return column

    def process_row(
        self,
        row,
        column,
        messages,
        model,
        model_config,
        output_format=None,
        run_prompt_config=None,
    ):
        """Process individual row for a prompt"""
        status = CellStatus.PASS.value

        try:
            close_old_connections()
            unsupported_exception = False
            tools_config = []
            if model_config.get("tools"):
                tools = Tools.objects.filter(id__in=model_config.get("tools")).all()
                for tool in tools:
                    tools_config.append(tool.config)

            rf = model_config.get("response_format")
            if rf and not isinstance(rf, dict):
                try:
                    uuid.UUID(str(rf))
                    rf = UserResponseSchema.objects.get(id=rf)
                    rf = rf.schema
                except Exception:
                    pass

            try:
                messages = populate_placeholders(
                    messages, self.dataset.id, row.id, column.id, model_name=model,
                    template_format=model_config.get("template_format"),
                )
                if output_format != "audio":
                    messages = remove_empty_text_from_messages(messages)
                # logger.info(f"messages: {messages}")

            except ValueError as e:
                unsupported_exception = True
                raise e

            run_prompt = RunPrompt(
                model=model,
                organization_id=self.experiment.dataset.organization.id,
                messages=messages,
                temperature=model_config.get("temperature"),
                frequency_penalty=model_config.get("frequency_penalty"),
                presence_penalty=model_config.get("presence_penalty"),
                max_tokens=model_config.get("max_tokens"),
                top_p=model_config.get("top_p"),
                response_format=rf,
                tool_choice=model_config.get("tool_choice"),
                tools=tools_config,
                output_format=output_format,
                run_prompt_config=run_prompt_config,
                workspace_id=(
                    self.experiment.dataset.workspace.id
                    if self.experiment.dataset.workspace
                    else None
                ),
            )

            response, value_info = run_prompt.litellm_response()
            value_info["reason"] = value_info.get("data", {}).get("response")

        except Exception as e:
            logger.exception(f"Error in processing the row: {str(e)}")
            # if unsupported_exception:
            response = str(e)
            value_info = {"reason": str(e)}
            # else:
            #     response = get_error_message("FAILED_TO_PROCESS_ROW")
            #     value_info = {"reason": response}
            status = CellStatus.ERROR.value

        Cell.objects.update_or_create(
            dataset=self.dataset,
            column=column,
            row=row,
            defaults={
                "value_infos": json.dumps(value_info),
                "value": str(response),
                "status": status,
            },
        )

        close_old_connections()

    def run_evaluations(self, column, eval_template_id, experiment_dataset):
        """Run evaluation on a result column"""
        user_eval_metric = UserEvalMetric.objects.select_related("template").get(
            id=eval_template_id
        )
        logger.info(f"UEM ID: {user_eval_metric.id}")
        config = {
            "dataset_id": str(experiment_dataset.id),
            "input": str(column.id),
            "experiment_id": str(self.experiment.id),
            "source": "experiment",
        }

        if user_eval_metric.template.template_type == "composite":
            # CompositeEvaluationRunner.run_prompt creates the result
            # column itself (via `_get_or_create_column`), so no explicit
            # pre-creation is needed here. Column creation is idempotent.
            from model_hub.tasks.composite_runner import CompositeEvaluationRunner

            composite_runner = CompositeEvaluationRunner(
                user_eval_metric_id=user_eval_metric.id,
                experiment_dataset=experiment_dataset,
                column=column,
                source="experiment",
                source_id=str(user_eval_metric.template.id),
                source_configs=config,
            )
            composite_runner.run_prompt()
            return

        runner = EvaluationRunner(
            user_eval_metric_id=user_eval_metric.id,
            experiment_dataset=experiment_dataset,
            column=column,
            source="experiment",
            source_id=user_eval_metric.template.id,
            source_configs=config,
        )
        runner.load_user_eval_metric()
        column_config = runner._get_column_config(self.dataset)
        column = runner._create_or_update_column(self.dataset, column_config)
        if user_eval_metric.config.get("reason_column"):
            reason_column_name = f"{user_eval_metric.name}-reason"
            runner._create_reason_column(self.dataset, reason_column_name)

        runner.run_prompt()

    def run_base_col_eval(self, user_eval_metric):
        column_qs = Column.objects.filter(source_id=user_eval_metric.id, deleted=False)
        column_exists = column_qs.exists()
        has_cells = (
            column_qs.first().cell_set.filter(deleted=False).exists()
            if column_exists
            else False
        )

        if column_exists and has_cells:
            return

        if user_eval_metric.template.template_type == "composite":
            # CompositeEvaluationRunner.run_prompt handles column create;
            # no pre-creation needed here.
            from model_hub.tasks.composite_runner import CompositeEvaluationRunner

            composite_runner = CompositeEvaluationRunner(
                user_eval_metric_id=user_eval_metric.id,
                source="experiment",
                source_id=str(user_eval_metric.template.id),
            )
            composite_runner.run_prompt()
            return

        runner = EvaluationRunner(
            user_eval_metric_id=user_eval_metric.id,
            is_only_eval=True,
            source="experiment",
        )
        runner.load_user_eval_metric()
        column_config = runner._get_column_config(self.dataset)
        runner._create_or_update_column(self.dataset, column_config, new_column=True)
        if user_eval_metric.config.get("reason_column"):
            reason_column_name = f"{user_eval_metric.name}-reason"
            runner._create_reason_column(self.dataset, reason_column_name)
        runner.run_prompt()

    def _cleanup_incomplete_columns(self):
        """Remove incomplete columns from experiment M2M before re-run.

        When an experiment is re-run after adding more rows to the dataset,
        old columns with fewer cells than the current row count remain linked
        via M2M, causing the UI to show loading bars. This method removes
        those incomplete columns from the M2M relationship.
        """
        try:
            row_count = Row.objects.filter(dataset=self.dataset, deleted=False).count()

            if row_count == 0:
                return

            # Find columns linked to this experiment that have fewer cells than rows
            incomplete_columns = (
                Column.objects.filter(
                    experiments_dataset_column__experiment=self.experiment,
                    deleted=False,
                )
                .annotate(cell_count=Count("cell", filter=Q(cell__deleted=False)))
                .filter(cell_count__lt=row_count)
            )

            if not incomplete_columns.exists():
                return

            incomplete_count = incomplete_columns.count()
            incomplete_ids = list(incomplete_columns.values_list("id", flat=True))

            # Remove incomplete columns from all ExperimentDatasetTable M2M relationships
            for experiment_dataset in self.experiment.experiment_datasets.filter(
                deleted=False
            ):
                experiment_dataset.columns.remove(*incomplete_columns)

            logger.info(
                f"Cleaned up {incomplete_count} incomplete columns from experiment "
                f"{self.experiment.id}: {incomplete_ids}"
            )
        except Exception as e:
            # Non-fatal: cleanup failure should not block experiment run
            logger.warning(
                f"Failed to cleanup incomplete columns for experiment "
                f"{self.experiment.id}: {e}"
            )

    def run(self):
        """Main method to run the experiment - spawns Celery tasks"""
        self.load_experiment()
        self._cleanup_incomplete_columns()
        self.experiment.status = StatusType.RUNNING.value
        self.experiment.save()
        self.empty_or_create_evals_column()

        try:
            # Spawn process_prompt tasks for each prompt_config + model combination
            for prompt_config in self.experiment.prompt_config:
                for model in prompt_config["model"]:
                    process_prompt_task.apply_async(
                        args=(
                            str(self.experiment.id),
                            prompt_config,
                            model,
                        ),
                        queue="tasks_l",
                    )

            # Spawn run_base_col_eval tasks for each eval template
            for user_eval_metric in self.experiment.user_eval_template_ids.all():
                run_base_col_eval_task.apply_async(
                    args=(
                        str(self.experiment.id),
                        str(user_eval_metric.id),
                        str(self.dataset.id),
                    ),
                    queue="tasks_l",
                )

            # Note: Experiment status will be updated by check_and_update_experiment_status
            # when all tasks complete
            logger.info(f"Experiment {self.experiment.id} tasks spawned")
        except Exception as e:
            logger.exception(f"{e} error experiment runner")
            # Ensure experiment status is set to failed if any error occurs
            self.experiment.status = StatusType.FAILED.value
            self.experiment.save()


# Standalone functions for Celery tasks
def _process_row_impl(
    row_id: uuid.UUID,
    column_id: uuid.UUID,
    dataset_id: uuid.UUID,
    experiment_id: uuid.UUID,
    messages: list,
    model: str,
    model_config: dict,
    output_format: str = None,
    run_prompt_config: dict = None,
    skip_celery_evals: bool = False,
):
    """Implementation of process_row that can be called as a standalone function"""
    try:
        close_old_connections()
        row = Row.objects.get(id=row_id, deleted=False)
        column = Column.objects.get(id=column_id, deleted=False)
        dataset = column.dataset
        experiment = ExperimentsTable.objects.get(id=experiment_id, deleted=False)

        status = CellStatus.PASS.value
        unsupported_exception = False
        tools_config = []

        if model_config.get("tools"):
            tools = Tools.objects.filter(id__in=model_config.get("tools")).all()
            for tool in tools:
                tools_config.append(tool.config)

        rf = model_config.get("response_format")
        if rf and not isinstance(rf, dict):
            try:
                uuid.UUID(str(rf))
                rf = UserResponseSchema.objects.get(id=rf)
                rf = rf.schema
            except Exception:
                pass

        try:
            messages = populate_placeholders(
                messages, dataset_id, row_id, column_id, model_name=model,
                template_format=model_config.get("template_format"),
            )
            if output_format != "audio":
                messages = remove_empty_text_from_messages(messages)

        except ValueError as e:
            unsupported_exception = True
            raise e

        run_prompt = RunPrompt(
            model=model,
            organization_id=experiment.dataset.organization.id,
            messages=messages,
            temperature=model_config.get("temperature"),
            frequency_penalty=model_config.get("frequency_penalty"),
            presence_penalty=model_config.get("presence_penalty"),
            max_tokens=model_config.get("max_tokens"),
            top_p=model_config.get("top_p"),
            response_format=rf,
            tool_choice=model_config.get("tool_choice"),
            tools=tools_config,
            output_format=output_format,
            run_prompt_config=run_prompt_config,
            workspace_id=(
                experiment.dataset.workspace.id
                if experiment.dataset.workspace
                else None
            ),
        )

        response, value_info = run_prompt.litellm_response()
        value_info["reason"] = value_info.get("data", {}).get("response")

    except Exception as e:
        logger.exception(f"Error in processing the row: {str(e)}")
        # if unsupported_exception:
        response = str(e)
        value_info = {"reason": str(e)}
        # else:
        #     response = get_error_message("FAILED_TO_PROCESS_ROW")
        #     value_info = {"reason": response}
        status = CellStatus.ERROR.value

        # Guard: don't overwrite cleanup state if experiment was cancelled
        # while this activity's thread was in-flight (LLM call).
        if is_experiment_cancelled(experiment_id):
            close_old_connections()
            return

        # Save the cell with ERROR status before potentially re-raising
        Cell.objects.update_or_create(
            dataset=dataset,
            column=column,
            row=row,
            defaults={
                "value_infos": json.dumps(value_info),
                "value": str(response),
                "status": status,
            },
        )

        close_old_connections()

        # Re-raise non-ValueError exceptions for Temporal to retry
        # ValueError is configured as non-retryable in Temporal retry policy
        if not isinstance(e, ValueError):
            raise

        return  # For ValueError, don't re-raise (non-retryable)

    # Guard: don't overwrite cleanup state if experiment was cancelled
    # while this activity's thread was in-flight (LLM call).
    if is_experiment_cancelled(experiment_id):
        close_old_connections()
        return

    Cell.objects.update_or_create(
        dataset=dataset,
        column=column,
        row=row,
        defaults={
            "value_infos": json.dumps(value_info),
            "value": str(response),
            "status": status,
        },
    )

    close_old_connections()

    # Trigger row-wise evaluations if this is an experiment column
    # Only trigger if row was successfully processed (status is PASS)
    # Skip if called from Temporal (Temporal handles evals separately)
    if not skip_celery_evals:
        _trigger_row_wise_evaluations(
            row_id=row_id,
            column_id=column_id,
            dataset_id=dataset_id,
            experiment_id=experiment_id,
        )

    # Note: Column status check is done in process_row_task AFTER PendingRowTask is updated
    # This prevents race condition where check runs before task status is updated


@temporal_activity(time_limit=3600, queue="tasks_l")
def process_row_task(
    row_id: str,
    column_id: str,
    dataset_id: str,
    experiment_id: str,
    messages: list,
    model: str,
    model_config: dict,
    output_format: str = None,
    run_prompt_config: dict = None,
):
    """Celery task to process a single row"""
    pending_task = None
    try:
        close_old_connections()

        # Find the corresponding PendingRowTask
        try:
            pending_task = PendingRowTask.objects.get(
                row_id=row_id,
                column_id=column_id,
                dataset_id=dataset_id,
                experiment_id=experiment_id,
                status=PendingRowTask.TaskStatus.PROCESSING,
            )
        except PendingRowTask.DoesNotExist:
            # If no pending task found, continue processing anyway (backward compatibility)
            logger.debug(
                f"No PendingRowTask found for row {row_id}, column {column_id} - continuing with processing"
            )

        _process_row_impl(
            uuid.UUID(row_id),
            uuid.UUID(column_id),
            uuid.UUID(dataset_id),
            uuid.UUID(experiment_id),
            messages,
            model,
            model_config,
            output_format,
            run_prompt_config,
        )

        # Mark PendingRowTask as COMPLETED on success
        # OPTIMIZATION: Use update instead of save for better performance
        if pending_task:
            PendingRowTask.objects.filter(id=pending_task.id).update(
                status=PendingRowTask.TaskStatus.COMPLETED
            )
            logger.debug(
                f"Marked PendingRowTask {pending_task.id} as COMPLETED for row {row_id}"
            )

        # Check if column is complete AFTER updating PendingRowTask status
        # This prevents race condition where check runs before task status is updated
        check_and_update_column_status(uuid.UUID(column_id))

    except Exception as e:
        logger.exception(f"Error in process_row_task for row {row_id}: {e}")
        # Mark cell as ERROR
        try:
            Cell.objects.filter(
                row_id=row_id, column_id=column_id, deleted=False
            ).update(status=CellStatus.ERROR.value)
        except Exception as update_error:
            logger.exception(f"Error updating cell status: {update_error}")

        # Mark PendingRowTask as FAILED on error
        # OPTIMIZATION: Use update instead of save for better performance
        if pending_task:
            try:
                PendingRowTask.objects.filter(id=pending_task.id).update(
                    status=PendingRowTask.TaskStatus.FAILED
                )
                logger.debug(
                    f"Marked PendingRowTask {pending_task.id} as FAILED for row {row_id}"
                )
            except Exception as update_error:
                logger.exception(
                    f"Error updating PendingRowTask status: {update_error}"
                )

        # Check if column is complete AFTER updating PendingRowTask status (even on error)
        # This prevents race condition where check runs before task status is updated
        check_and_update_column_status(uuid.UUID(column_id))


@temporal_activity(time_limit=3600 * 3, queue="tasks_l")
def process_prompt_task(
    experiment_id: str,
    prompt_config: dict,
    model_spec,
):
    """Celery task to process a single prompt configuration for a specific model"""
    try:
        close_old_connections()
        experiment = ExperimentsTable.objects.get(
            id=uuid.UUID(experiment_id), deleted=False
        )
        dataset = experiment.dataset

        # Handle both string and dict model specifications
        if isinstance(model_spec, dict):
            model = model_spec.get("name")
            if not model:
                raise ValueError(
                    f"ModelSpec missing required 'name' field or name is empty: {model_spec}"
                )
            run_prompt_config_for_model = model_spec.get("config", {})
            display_suffix = model_spec.get("display_name") or model
        else:
            # Backward compatible: model_spec is a string
            model = model_spec
            display_suffix = model
            # Get the specific config for the current model from the run_prompt_config dictionary
            run_prompt_config_for_model = prompt_config.get(
                "run_prompt_config", {}
            ).get(model)

        # Merge model-specific parameters from modelParams if available
        if run_prompt_config_for_model is None:
            run_prompt_config_for_model = {}
        model_params_from_payload = prompt_config.get("model_params", {}).get(model, {})
        run_prompt_config_for_model.update(model_params_from_payload)

        # Normalize voice_id (handle array, lookup name from DB)
        if experiment and experiment.dataset:
            run_prompt_config_for_model = normalize_voice_config(
                run_prompt_config_for_model, experiment.dataset.organization.id
            )

        # If audio output and a per-model voice is provided, include the voice in the column suffix
        try:
            voice = None
            if isinstance(run_prompt_config_for_model, dict):
                voice = run_prompt_config_for_model.get("voice")
            if prompt_config.get("output_format") == "audio" and voice is not None:
                voice_str = str(voice).strip()
                if voice_str:
                    # Avoid duplicating voice if already present in display_suffix (case-insensitive)
                    if voice_str.lower() not in str(display_suffix).lower():
                        display_suffix = f"{display_suffix}-{voice_str}"
        except Exception:
            # Non-fatal: naming enhancement should not block processing
            pass

        column_name = f"{experiment.name}-{prompt_config['name']}-{display_suffix}"
        close_old_connections()

        # Check if entry exists within THIS experiment's relationship (important for reruns)
        experiment_dataset = experiment.experiment_datasets.filter(
            name=column_name, deleted=False
        ).first()

        dataset_created = False

        if not experiment_dataset:
            # Create EDT with FK to experiment
            try:
                experiment_dataset, dataset_created = (
                    ExperimentDatasetTable.objects.get_or_create(
                        name=column_name,
                        experiment=experiment,
                        defaults={
                            "status": StatusType.RUNNING.value,
                        },
                    )
                )
            except ExperimentDatasetTable.MultipleObjectsReturned:
                experiment_dataset = ExperimentDatasetTable.objects.filter(
                    name=column_name, experiment=experiment
                ).first()
                dataset_created = False

        # Update status to RUNNING for re-runs
        if experiment_dataset and not dataset_created:
            experiment_dataset.status = StatusType.RUNNING.value
            experiment_dataset.save(update_fields=["status"])

        # Create column for experiment results
        column, column_created = create_experiment_column(
            dataset=dataset,
            source_id=experiment_dataset.id,
            name=column_name,
            output_format=prompt_config.get("output_format", "string"),
            response_format=prompt_config.get("response_format"),
            status=StatusType.RUNNING.value,
        )

        if column_created:
            experiment_dataset.columns.add(column)

        # Add to experiment's experiments_datasets
        if dataset_created and experiment:
            experiment.experiments_datasets.add(experiment_dataset)

        # Process each row - spawn Celery tasks
        rows = (
            Row.objects.filter(dataset_id=dataset.id, deleted=False)
            .order_by("order")
            .all()
        )
        empty_values = {
            "value": "",
            "status": CellStatus.RUNNING.value,
            "value_infos": json.dumps({}),
        }
        bulk_update_or_create_cells(
            rows.values_list("id", flat=True), column.id, dataset.id, empty_values
        )

        # Create PendingRowTask records for batching with concurrency limits
        # OPTIMIZATION: Use bulk_create instead of individual creates
        pending_tasks = []
        for row in rows:
            pending_tasks.append(
                PendingRowTask(
                    row=row,
                    column=column,
                    dataset=dataset,
                    experiment=experiment,
                    messages=prompt_config["messages"],
                    model=model,
                    model_config=prompt_config["configuration"],
                    output_format=prompt_config.get("output_format"),
                    run_prompt_config=run_prompt_config_for_model,
                    status=PendingRowTask.TaskStatus.REGISTERED,
                )
            )

        # Bulk create all pending tasks
        if pending_tasks:
            PendingRowTask.objects.bulk_create(pending_tasks, batch_size=500)

        # Note: Column status will be updated by process_row_task when all rows complete
        # Evaluations will be triggered after column is complete via a separate check
        # Row processing tasks will be spawned by the periodic process_pending_row_tasks task

        close_old_connections()

        # After creating all pending row tasks, log the count
        logger.info(
            f"process_prompt_task created {len(pending_tasks)} pending row tasks for column {column.id}"
        )

    except Exception as e:
        logger.exception(f"Error in process_prompt_task: {e}")
        # Mark experiment_dataset as failed if critical error
        try:
            if "experiment_dataset" in locals() and experiment_dataset:
                experiment_dataset.status = StatusType.FAILED.value
                experiment_dataset.save(update_fields=["status"])
        except Exception:
            pass


@temporal_activity(time_limit=3600 * 2, queue="tasks_l")
def run_evaluations_task(
    column_id: str,
    eval_template_id: str,
    experiment_dataset_id: str,
    experiment_id: str,
    dataset_id: str,
):
    """Celery task to run evaluation on a result column"""
    try:
        close_old_connections()
        column = Column.objects.get(id=uuid.UUID(column_id), deleted=False)
        logger.info(f"Column Name run_evaluations_task: {column.name}")
        experiment_dataset = ExperimentDatasetTable.objects.get(
            id=uuid.UUID(experiment_dataset_id)
        )
        experiment = ExperimentsTable.objects.get(
            id=uuid.UUID(experiment_id), deleted=False
        )
        dataset = column.dataset

        user_eval_metric = UserEvalMetric.objects.get(id=uuid.UUID(eval_template_id))
        logger.info(f"UEM ID: {user_eval_metric.id}")
        config = {
            "dataset_id": str(experiment_dataset.id),
            "input": str(column.id),
            "experiment_id": str(experiment.id),
            "source": "experiment",
        }
        runner = EvaluationRunner(
            user_eval_metric_id=user_eval_metric.id,
            experiment_dataset=experiment_dataset,
            column=column,
            source="experiment",
            source_id=user_eval_metric.template.id,
            source_configs=config,
        )
        runner.load_user_eval_metric()
        column_config = runner._get_column_config(dataset)
        eval_column = runner._create_or_update_column(dataset, column_config)
        if user_eval_metric.config.get("reason_column"):
            reason_column_name = f"{user_eval_metric.name}-reason"
            runner._create_reason_column(dataset, reason_column_name)
        logger.info(
            f"Running evaluation for {user_eval_metric.id} for column {column.name}"
        )
        runner.run_prompt()

        close_old_connections()

        # CRITICAL FIX: Check column status first to trigger status check chain
        # This ensures that when eval column completes, it properly triggers
        # experiment_dataset and experiment status checks
        check_and_update_column_status(eval_column.id)

        # Also check experiment_dataset status after evaluation completes
        check_and_update_experiment_dataset_status(uuid.UUID(experiment_dataset_id))

    except Exception as e:
        logger.exception(f"Error in run_evaluations_task: {e}")


@temporal_activity(time_limit=3600 * 2, queue="tasks_l")
def run_base_col_eval_task(
    experiment_id: str,
    user_eval_metric_id: str,
    dataset_id: str,
):
    """Celery task to run base column evaluation"""
    try:
        close_old_connections()
        experiment = ExperimentsTable.objects.select_related(
            "dataset", "snapshot_dataset"
        ).get(id=uuid.UUID(experiment_id), deleted=False)
        dataset = experiment.snapshot_dataset
        user_eval_metric = UserEvalMetric.objects.get(id=uuid.UUID(user_eval_metric_id))

        # NOTE: source_id is stored as string in Column model, so convert UUID to string
        column_qs = Column.objects.filter(
            source_id=str(user_eval_metric.id), deleted=False
        )
        column_exists = column_qs.exists()
        has_cells = (
            column_qs.first().cell_set.filter(deleted=False).exists()
            if column_exists
            else False
        )

        logger.info(f"Evaluation {has_cells} for experiment {column_exists}.")

        # Only run if column doesn't exist or it has no cells
        if not column_exists or not has_cells:
            runner = EvaluationRunner(
                user_eval_metric_id=user_eval_metric.id,
                is_only_eval=True,
                source="experiment",
            )
            runner.load_user_eval_metric()
            column_config = runner._get_column_config(dataset)
            runner._create_or_update_column(dataset, column_config, new_column=True)
            if user_eval_metric.config.get("reason_column"):
                reason_column_name = f"{user_eval_metric.name}-reason"
                runner._create_reason_column(dataset, reason_column_name)
            logger.info(f"Running base column evaluation for {user_eval_metric.id}")
            runner.run_prompt()

        close_old_connections()

        # Check experiment status after base col eval completes
        check_and_update_experiment_status(uuid.UUID(experiment_id))

    except Exception as e:
        logger.exception(f"Error in run_base_col_eval_task: {e}")
