"""
Service for column operations in datasets.

This module handles:
- Creating and updating columns with proper data types
- Ensuring column uniqueness by source_id
- Managing column lifecycle in datasets
"""

from typing import Any, Optional, Tuple

import structlog
from django.db import transaction

from model_hub.models.choices import SourceChoices, StatusType
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.utils.column_utils import get_column_data_type

logger = structlog.get_logger(__name__)


def get_or_create_column(
    dataset: Dataset,
    source: str,
    source_id: Any,
    name: str,
    output_format: str = "string",
    response_format: Any = None,
    status: Optional[str] = None,
) -> Tuple[Column, bool]:
    """
    Get or create a column for a dataset, ensuring uniqueness by source_id.

    This function handles the common pattern of creating columns for run prompts
    and experiments, with proper data type determination and update logic.
    Uses row-level locking to prevent race conditions during concurrent updates.

    Args:
        dataset: The dataset to create the column in
        source: The source type (e.g., SourceChoices.RUN_PROMPT.value)
        source_id: The ID of the source (e.g., run_prompter.id)
        name: The column name
        output_format: The output format setting (string, object, etc.)
        response_format: The response format setting (dict or string)
        status: Optional status to set on the column

    Returns:
        Tuple of (column, created) where created is True if a new column was created
    """
    data_type = get_column_data_type(output_format, response_format)

    defaults = {
        "name": name,
        "data_type": data_type,
    }
    if status is not None:
        defaults["status"] = status

    with transaction.atomic():
        column, created = Column.objects.get_or_create(
            source=source,
            dataset=dataset,
            source_id=source_id,
            defaults=defaults,
        )

        if not created:
            # Lock the row for update to prevent concurrent modifications
            column = Column.objects.select_for_update().get(id=column.id)

            # Update fields if they changed
            needs_save = False
            update_fields = []

            if column.data_type != data_type:
                column.data_type = data_type
                update_fields.append("data_type")
                needs_save = True

            if status is not None and column.status != status:
                column.status = status
                update_fields.append("status")
                needs_save = True

            if needs_save:
                column.save(update_fields=update_fields)

    return column, created


def create_run_prompt_column(
    dataset: Dataset,
    source_id: Any,
    name: str,
    output_format: str = "string",
    response_format: Any = None,
) -> Tuple[Column, bool]:
    """
    Create or get a column for a run prompt.

    Args:
        dataset: The dataset to create the column in
        source_id: The run prompter ID
        name: The column name
        output_format: The output format setting
        response_format: The response format setting

    Returns:
        Tuple of (column, created)
    """
    return get_or_create_column(
        dataset=dataset,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=source_id,
        name=name,
        output_format=output_format,
        response_format=response_format,
    )


def create_experiment_column(
    dataset: Dataset,
    source_id: Any,
    name: str,
    output_format: str = "string",
    response_format: Any = None,
    status: str = StatusType.RUNNING.value,
) -> Tuple[Column, bool]:
    """
    Create or get a column for an experiment.

    Args:
        dataset: The dataset to create the column in
        source_id: The experiment dataset table ID
        name: The column name
        output_format: The output format setting
        response_format: The response format setting
        status: The initial status for the column

    Returns:
        Tuple of (column, created)
    """
    return get_or_create_column(
        dataset=dataset,
        source=SourceChoices.EXPERIMENT.value,
        source_id=source_id,
        name=name,
        output_format=output_format,
        response_format=response_format,
        status=status,
    )


def get_correct_data_type(
    column: Column,
    run_prompter: Any,
) -> str:
    """
    Get the correct data_type for a column based on its RunPrompter's response_format.

    This handles the case where existing columns were created before the
    UUID response_format handling was added. If the RunPrompter has a
    response_format that should produce JSON output, return "json".

    NOTE: This function does NOT save to the database. Use fix_column_data_type()
    to actually persist the fix. This separation allows safe use in threaded contexts.

    Args:
        column: The column to check
        run_prompter: The associated RunPrompter instance

    Returns:
        The correct data_type for the column
    """
    if not run_prompter:
        return column.data_type

    # Get the correct data_type based on current response_format
    return get_column_data_type(
        run_prompter.output_format or "string",
        run_prompter.response_format,
    )


def fix_column_data_type(
    column: Column,
    run_prompter: Any,
) -> bool:
    """
    Fix a column's data_type if it's incorrect and save to database.

    This should be called OUTSIDE of threaded contexts to avoid connection issues.

    Args:
        column: The column to fix
        run_prompter: The associated RunPrompter instance

    Returns:
        True if the column was fixed, False if no change was needed
    """
    if not run_prompter:
        return False

    correct_data_type = get_column_data_type(
        run_prompter.output_format or "string",
        run_prompter.response_format,
    )

    if column.data_type != correct_data_type:
        logger.info(
            f"Fixing column data_type: column={column.id}, "
            f"old={column.data_type}, new={correct_data_type}, "
            f"response_format={run_prompter.response_format}"
        )
        column.data_type = correct_data_type
        column.save(update_fields=["data_type"])
        return True

    return False


def update_column_for_rerun(
    column: Column,
    output_format: str = "string",
    response_format: Any = None,
    status: Optional[str] = StatusType.RUNNING.value,
    name: Optional[str] = None,
    extract_derived_vars: bool = True,
) -> None:
    """
    Update a column's data type, status, and optionally name for a rerun.

    Optionally extracts derived variables if output format changed to JSON.

    Args:
        column: The column to update
        output_format: The output format setting
        response_format: The response format setting
        status: The new status for the column (None to skip update)
        name: The new name for the column (None to skip update)
        extract_derived_vars: Whether to extract derived variables if format is JSON
    """
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.run_prompt import RunPrompter
    from model_hub.services.derived_variable_service import (
        extract_derived_variables_from_output,
    )
    from model_hub.utils.column_utils import is_json_response_format

    data_type = get_column_data_type(output_format, response_format)

    update_fields = []

    if name is not None and column.name != name:
        column.name = name
        update_fields.append("name")

    if column.data_type != data_type:
        column.data_type = data_type
        update_fields.append("data_type")

    if status is not None and column.status != status:
        column.status = status
        update_fields.append("status")

    if update_fields:
        column.save(update_fields=update_fields)

    # Extract derived variables if format changed to JSON and extraction is enabled
    if extract_derived_vars and is_json_response_format(response_format):
        try:
            # Get first cell with value to extract schema
            first_cell = (
                Cell.objects.filter(
                    dataset=column.dataset,
                    column=column,
                    deleted=False,
                )
                .exclude(value__isnull=True)
                .exclude(value="")
                .first()
            )

            if first_cell and first_cell.value:
                extracted = extract_derived_variables_from_output(
                    first_cell.value, column.name
                )

                if extracted.get("is_json") and extracted.get("full_variables"):
                    # Store in run_prompter config
                    run_prompter = RunPrompter.objects.filter(
                        id=column.source_id,
                        deleted=False,
                    ).first()

                    if run_prompter:
                        run_config = run_prompter.run_prompt_config or {}
                        run_config["derived_variables"] = extracted
                        run_prompter.run_prompt_config = run_config
                        run_prompter.save(update_fields=["run_prompt_config"])

                        logger.info(
                            f"Extracted derived variables for column {column.name} on rerun",
                            column_id=str(column.id),
                            num_paths=len(extracted.get("paths", [])),
                        )
        except Exception as e:
            logger.warning(
                f"Failed to extract derived variables on rerun for column {column.name}: {e}",
                column_id=str(column.id),
            )


def prune_dataset_columns(dataset, col_id_strs):
    """Drop a set of column ids from a Dataset's ``column_order`` AND
    ``column_config`` in a single row update.

    ``column_config`` is keyed by column id — leaving stale soft-deleted ids
    in it makes a client round-trip of the full config fail validation
    against the (now smaller) live column set. ``column_order`` and
    ``column_config`` must therefore be pruned together; any delete path
    that touches one without the other re-introduces the staleness bug.

    ``col_id_strs`` is an iterable of column ids as strings (the FE / JSON
    side uses string ids, so we normalise here).
    """
    from model_hub.models.develop_dataset import Dataset

    col_id_strs = {str(c) for c in col_id_strs}
    if not col_id_strs:
        return

    updates = {}
    if dataset.column_order:
        updates["column_order"] = [
            c for c in dataset.column_order if c not in col_id_strs
        ]
    if dataset.column_config:
        updates["column_config"] = {
            k: v for k, v in dataset.column_config.items() if k not in col_id_strs
        }
    if updates:
        Dataset.objects.filter(id=dataset.id).update(**updates)


def delete_eval_column_and_dependents(column, organization_id):
    """Soft-delete an eval column, its reason columns, their cells, and
    update any metrics that referenced the column.

    This is the single authoritative delete path shared by DeleteColumnView
    and DeleteAndRunUserEvalView so both fix the Cell full-scan (TH-5508) and
    stay in sync if the logic ever changes.

    Self-atomic: opens its own ``transaction.atomic()`` internally so callers
    can't accidentally run it non-atomically. Callers that need to bundle
    additional writes (e.g. ``eval_metric.save()`` in ``DeleteEvalsView``)
    can wrap the call in their own outer ``transaction.atomic()`` — Django's
    nested atomic uses savepoints, so the outer transaction becomes the boss
    and the service's inner atomic becomes a savepoint.
    """
    from django.db.models import Q
    from django.utils import timezone

    from model_hub.models.develop_dataset import Cell, Column, Dataset
    from model_hub.models.evals_metric import UserEvalMetric

    with transaction.atomic():
        now = timezone.now()

        # 1. Collect ALL related column IDs in a single indexed query.
        col_ids = list(
            Column.objects.filter(
                Q(id=column.id) | Q(source_id__startswith=f"{column.id}-sourceid-"),
                deleted=False,
            ).values_list("id", flat=True)
        )
        col_id_strs = {str(c) for c in col_ids}

        # 2. Soft-delete cells by indexed column_id IN — no JOIN on source_id.
        if col_ids:
            Cell.objects.filter(
                column_id__in=col_ids, deleted=False
            ).update(deleted=True, deleted_at=now)

        # 3. Prune column_order AND column_config in one Dataset row update.
        prune_dataset_columns(column.dataset, col_id_strs)

        # 4. Flag other metrics that referenced this column BEFORE deleting it
        #    (column must still be visible for get_metrics_using_column to find it).
        metrics = UserEvalMetric.get_metrics_using_column(organization_id, column.id)
        other_metric_ids = [m.id for m in metrics]
        if other_metric_ids:
            UserEvalMetric.objects.filter(id__in=other_metric_ids).update(
                column_deleted=True
            )

        # 5. Soft-delete the columns.
        if col_ids:
            Column.objects.filter(id__in=col_ids).update(
                deleted=True, deleted_at=now
            )
