"""Service layer for dataset operations — shared by views and ai_tools."""

import uuid
from dataclasses import dataclass
from typing import Optional

import structlog
from django.db.models import Q

from model_hub.models.choices import SourceChoices
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.run_prompt import PromptVersion, RunPrompter
from model_hub.services.derived_variable_service import (
    cleanup_derived_variables_for_column,
)

logger = structlog.get_logger(__name__)


@dataclass
class ServiceError:
    message: str
    code: str = "ERROR"


def _check_resource_limit(organization, workspace, api_call_type, config=None):
    """Check resource limits. Returns True if allowed, False if limit reached."""
    try:
        try:
            from ee.usage.models import APICallStatusChoices, APICallTypeChoices
        except ImportError:
            from futureagi._ee_stubs import APICallStatusChoices, APICallTypeChoices
        try:
            from ee.usage.utils import log_and_deduct_cost_for_resource_request
        except ImportError:
            from futureagi._ee_stubs import log_and_deduct_cost_for_resource_request

        call_log = log_and_deduct_cost_for_resource_request(
            organization,
            api_call_type=api_call_type,
            config=config or {},
            workspace=workspace,
        )
        if (
            call_log is None
            or call_log.status == APICallStatusChoices.RESOURCE_LIMIT.value
        ):
            return False
        call_log.status = APICallStatusChoices.SUCCESS.value
        call_log.save()
        return True
    except Exception as e:
        logger.warning(f"Resource limit check failed, allowing: {e}")
        return True


def create_dataset(*, name, columns, organization, workspace, user):
    """Create a dataset with columns.

    Args:
        name: Dataset name
        columns: List of dicts with 'name' and 'data_type' keys
        organization: Organization instance
        workspace: Workspace instance
        user: User instance

    Returns:
        dict with dataset_id, name, columns or ServiceError
    """
    from model_hub.models.choices import DatasetSourceChoices, SourceChoices
    from model_hub.models.develop_dataset import Column, Dataset

    # Resource limit check
    if not _check_resource_limit(organization, workspace, "dataset_add"):
        return ServiceError("Dataset creation limit reached", "RESOURCE_LIMIT")

    # Check for duplicate name (shared validator)
    from model_hub.validators.dataset_validators import validate_dataset_name_unique

    try:
        validate_dataset_name_unique(name, organization)
    except Exception:
        return ServiceError(
            f"A dataset named '{name}' already exists in this organization.",
            "DUPLICATE_NAME",
        )

    # Create dataset
    dataset = Dataset.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
        user=user,
    )

    # Create columns
    created_columns = []
    column_order = []
    column_config = {}

    for col_def in columns:
        col_id = uuid.uuid4()
        col = Column.objects.create(
            id=col_id,
            name=col_def["name"],
            data_type=col_def.get("data_type", "text"),
            source=SourceChoices.OTHERS.value,
            dataset=dataset,
        )
        created_columns.append(
            {
                "id": str(col.id),
                "name": col.name,
                "data_type": col.data_type,
            }
        )
        column_order.append(str(col_id))
        column_config[str(col_id)] = {"is_visible": True, "is_frozen": None}

    dataset.column_order = column_order
    dataset.column_config = column_config
    dataset.save()

    return {
        "dataset_id": str(dataset.id),
        "name": dataset.name,
        "columns": created_columns,
    }


def add_dataset_rows(*, dataset_id, rows, organization, workspace):
    """Add rows to a dataset.

    Args:
        dataset_id: UUID string
        rows: List of dicts mapping column names to values
        organization: Organization instance
        workspace: Workspace instance

    Returns:
        dict with dataset_id, dataset_name, rows_added, cells_created, total_rows or ServiceError
    """
    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    lookup = {"id": dataset_id, "deleted": False}
    if organization:
        lookup["organization"] = organization

    try:
        dataset = Dataset.objects.get(**lookup)
    except Dataset.DoesNotExist:
        return ServiceError(f"Dataset {dataset_id} not found.", "NOT_FOUND")

    # Resource limit check
    existing_count = Row.objects.filter(dataset=dataset, deleted=False).count()
    if not _check_resource_limit(
        organization,
        workspace,
        "row_add",
        config={"total_rows": existing_count + len(rows)},
    ):
        return ServiceError("Row limit reached.", "RESOURCE_LIMIT")

    # Get valid columns (exclude experiment columns)
    columns = Column.objects.filter(dataset=dataset, deleted=False).exclude(
        source__in=[
            SourceChoices.EXPERIMENT.value,
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        ]
    )
    col_map = {c.name: c for c in columns}

    last_row = Row.all_objects.filter(dataset=dataset).order_by("-created_at").first()
    max_order = last_row.order if last_row else -1

    from model_hub.services.dataset_validators import validate_and_convert_cell_value

    cells_created = 0
    for idx, row_data in enumerate(rows):
        new_row = Row.objects.create(
            id=uuid.uuid4(),
            dataset=dataset,
            order=max_order + 1 + idx,
        )

        cells_to_create = []
        for col in columns:
            raw_value = row_data.get(col.name, "")
            # Validate and convert cell value against column data type
            if raw_value and str(raw_value).strip():
                converted, error = validate_and_convert_cell_value(
                    str(raw_value), col.data_type
                )
                if error:
                    return ServiceError(
                        f"Row {idx + 1}, column '{col.name}': {error}",
                        "VALIDATION_ERROR",
                    )
                value = converted if converted is not None else ""
            else:
                value = raw_value
            cells_to_create.append(
                Cell(
                    id=uuid.uuid4(),
                    dataset=dataset,
                    column=col,
                    row=new_row,
                    value=value,
                )
            )

        Cell.objects.bulk_create(cells_to_create)
        cells_created += len(cells_to_create)

    total_rows = Row.objects.filter(dataset=dataset, deleted=False).count()

    # Auto-infer column types when first rows are added to a dataset
    # with default "text" columns (e.g., created via MCP create_dataset tool)
    if existing_count == 0 and rows:
        _infer_column_types_from_data(columns, rows)

    return {
        "dataset_id": str(dataset.id),
        "dataset_name": dataset.name,
        "rows_added": len(rows),
        "cells_created": cells_created,
        "total_rows": total_rows,
    }


def _infer_column_types_from_data(columns, rows):
    """Infer and update column types from row data for columns still at default 'text' type."""
    import pandas as pd

    from model_hub.models.choices import DataTypeChoices, determine_data_type

    text_columns = [c for c in columns if c.data_type == DataTypeChoices.TEXT.value]
    if not text_columns:
        return

    for col in text_columns:
        values = [row_data.get(col.name, "") for row_data in rows]
        # Filter to non-empty values
        values = [v for v in values if v and str(v).strip()]
        if not values:
            continue

        series = pd.Series(values)
        inferred_type = determine_data_type(series)
        if inferred_type != DataTypeChoices.TEXT.value:
            col.data_type = inferred_type
            col.save(update_fields=["data_type"])
            logger.info(
                "auto_inferred_column_type",
                column_name=col.name,
                inferred_type=inferred_type,
            )


def delete_column(*, dataset_id, column_id, organization=None):
    """Delete a column from a dataset (soft delete).

    Args:
        dataset_id: UUID string
        column_id: UUID string
        organization: Organization instance (required for org-scoped lookup)

    Returns:
        dict with dataset_id, column_id, column_name or ServiceError
    """
    from django.utils import timezone

    from model_hub.models.develop_dataset import Cell, Column, Dataset

    lookup = {"id": dataset_id, "deleted": False}
    if organization:
        lookup["organization"] = organization

    try:
        dataset = Dataset.objects.get(**lookup)
    except Dataset.DoesNotExist:
        return ServiceError(f"Dataset {dataset_id} not found.", "NOT_FOUND")

    try:
        column = Column.objects.get(id=column_id, dataset=dataset, deleted=False)
    except Column.DoesNotExist:
        return ServiceError(f"Column {column_id} not found.", "NOT_FOUND")

    now = timezone.now()
    col_name = column.name
    col_id_str = str(column.id)

    # Delete associated source model based on source type
    if column.source_id:
        if column.source == SourceChoices.RUN_PROMPT.value:
            RunPrompter.objects.filter(id=column.source_id).update(deleted=True)
            # Clean up derived variables from associated prompt versions
            try:
                run_prompter = RunPrompter.objects.filter(id=column.source_id).first()
                if run_prompter and run_prompter.prompt_id:
                    # Get all versions for this prompt and clean up derived variables
                    prompt_versions = PromptVersion.objects.filter(
                        original_template_id=run_prompter.prompt_id,
                        deleted=False,
                    )
                    for version in prompt_versions:
                        if cleanup_derived_variables_for_column(version, column.name):
                            version.save(update_fields=["metadata"])
            except Exception as cleanup_error:
                logger.warning(
                    f"Failed to cleanup derived variables for column {column.name}: {cleanup_error}"
                )
        if column.source == SourceChoices.EVALUATION.value:
            UserEvalMetric.objects.filter(id=column.source_id).update(deleted=True)
        if column.source == SourceChoices.ANNOTATION_LABEL.value:
            source_parts = column.source_id.split("-sourceid-")

            annotation_id = source_parts[0]
            label_id = source_parts[1]

            annotation = Annotations.objects.get(id=annotation_id)
            label = AnnotationsLabels.objects.get(id=label_id)

            columns_to_delete = Column.objects.filter(
                dataset=dataset,
                source_id=f"{annotation.id}-sourceid-{label.id}",
                deleted=False,
            )

            Cell.objects.filter(column__in=columns_to_delete).update(
                deleted=True, deleted_at=timezone.now()
            )

            for col in columns_to_delete:
                if str(col.id) in dataset.column_order:
                    dataset.column_order.remove(str(col.id))
                if str(col.id) in dataset.column_config:
                    del dataset.column_config[str(col.id)]
                annotation.columns.remove(col)

            annotation.labels.remove(label)
            columns_to_delete.update(deleted=True)

            annotation.save()
            dataset.save()

    # Soft delete cells and column
    Cell.objects.filter(dataset=dataset, column=column).update(
        deleted=True, deleted_at=now
    )
    # Delete cells where source_id starts with column.id
    Cell.objects.filter(column__source_id__startswith=f"{column.id}").update(
        deleted=True
    )

    # Get columns to delete (including those with source_id starting with column.id)
    columns_to_delete = Column.objects.filter(
        Q(id=column.id) | Q(source_id__startswith=f"{column.id}")
    ).values_list("id", flat=True)

    # Delete the columns (this will cascade delete related cells)
    Column.objects.filter(
        Q(id=column.id) | Q(source_id__startswith=f"{column.id}")
    ).update(deleted=True)

    column.deleted = True
    column.deleted_at = now
    column.save(update_fields=["deleted", "deleted_at"])

    # Clean up column_order and column_config
    columns_to_delete_strs = set(str(c) for c in columns_to_delete)
    if dataset.column_order:
        dataset.column_order = [
            c for c in dataset.column_order if c not in columns_to_delete_strs
        ]
    if dataset.column_config:
        dataset.column_config = {
            k: v
            for k, v in dataset.column_config.items()
            if k not in columns_to_delete_strs
        }
    dataset.save(update_fields=["column_order", "column_config"])

    # Mark dependent eval metrics as column_deleted
    try:
        metrics = UserEvalMetric.get_metrics_using_column(
            str(dataset.organization_id), col_id_str
        )
        for metric in metrics:
            metric.column_deleted = True
            metric.save(update_fields=["column_deleted"])
    except Exception:
        logger.warning(
            "failed_to_mark_dependent_metrics",
            column_id=col_id_str,
            dataset_id=str(dataset.id),
        )

    return {
        "dataset_id": str(dataset.id),
        "column_id": col_id_str,
        "column_name": col_name,
    }


def update_dataset(*, dataset_id, name, organization=None):
    """Update dataset name.

    Args:
        dataset_id: UUID string
        name: New dataset name
        organization: Organization instance (required for org-scoped lookup)

    Returns:
        dict with dataset_id, name or ServiceError
    """
    from model_hub.models.develop_dataset import Dataset

    lookup = {"id": dataset_id, "deleted": False}
    if organization:
        lookup["organization"] = organization

    try:
        dataset = Dataset.objects.get(**lookup)
    except Dataset.DoesNotExist:
        return ServiceError(f"Dataset {dataset_id} not found.", "NOT_FOUND")

    # Check for duplicate name (shared validator, excluding self)
    from model_hub.validators.dataset_validators import validate_dataset_name_unique

    try:
        validate_dataset_name_unique(name, dataset.organization, exclude_id=dataset.id)
    except Exception:
        return ServiceError(
            f"A dataset named '{name}' already exists.", "DUPLICATE_NAME"
        )

    dataset.name = name
    dataset.save(update_fields=["name"])

    return {"dataset_id": str(dataset.id), "name": dataset.name}


def add_columns(*, dataset_id, columns_data, organization):
    """Add columns to a dataset.

    Args:
        dataset_id: UUID string
        columns_data: List of dicts with 'name', 'data_type', and optional 'source'
        organization: Organization instance

    Returns:
        dict with dataset_id, columns_added, columns or ServiceError
    """
    from django.db import transaction

    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    try:
        dataset = Dataset.objects.get(
            id=dataset_id, organization=organization, deleted=False
        )
    except Dataset.DoesNotExist:
        return ServiceError(f"Dataset {dataset_id} not found.", "NOT_FOUND")

    column_names = [col["name"] for col in columns_data]

    if len(column_names) != len(set(column_names)):
        return ServiceError(
            "Duplicate column names found in request.", "DUPLICATE_NAME"
        )

    existing_columns = Column.objects.filter(
        name__in=column_names,
        dataset=dataset,
        deleted=False,
        dataset__organization=organization,
    ).values_list("name", flat=True)

    if existing_columns:
        return ServiceError(
            f"Column name(s) already exist: {', '.join(existing_columns)}",
            "DUPLICATE_NAME",
        )

    with transaction.atomic():
        new_columns = []
        for col_def in columns_data:
            source = col_def.get("source")
            new_columns.append(
                Column(
                    id=uuid.uuid4(),
                    name=col_def["name"],
                    data_type=col_def.get("data_type", "text"),
                    source=(
                        source
                        if source and getattr(SourceChoices, source, None)
                        else SourceChoices.OTHERS.value
                    ),
                    dataset=dataset,
                )
            )

        created_columns = Column.objects.bulk_create(new_columns)

        column_order = dataset.column_order or []
        column_config = dataset.column_config or {}

        for column in created_columns:
            column_order.append(str(column.id))
            column_config[str(column.id)] = {
                "is_visible": True,
                "is_frozen": None,
            }

        dataset.column_order = column_order
        dataset.column_config = column_config
        dataset.save(update_fields=["column_order", "column_config"])

        rows = list(Row.objects.filter(dataset=dataset, deleted=False))

        cells_to_create = [
            Cell(
                id=uuid.uuid4(),
                dataset=dataset,
                column=column,
                row=row,
                value=None,
            )
            for row in rows
            for column in created_columns
        ]
        if cells_to_create:
            Cell.objects.bulk_create(cells_to_create)

    added = [
        {
            "id": str(col.id),
            "name": col.name,
            "data_type": col.data_type,
        }
        for col in created_columns
    ]

    return {
        "dataset_id": str(dataset.id),
        "columns_added": len(added),
        "columns": added,
    }


def clone_dataset(*, source_dataset_id, new_name, organization, workspace, user):
    """Clone a dataset with all columns and rows.

    Returns:
        dict with dataset_id, name, source_dataset_id, columns_cloned, rows_cloned or ServiceError
    """
    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    try:
        source = Dataset.objects.get(
            id=source_dataset_id, deleted=False, organization=organization
        )
    except Dataset.DoesNotExist:
        return ServiceError(f"Dataset {source_dataset_id} not found.", "NOT_FOUND")

    # Resource limit checks
    if not _check_resource_limit(organization, workspace, "dataset_add"):
        return ServiceError("Dataset creation limit reached.", "RESOURCE_LIMIT")

    final_name = new_name or f"Copy of {source.name}"

    from model_hub.validators.dataset_validators import validate_dataset_name_unique

    try:
        validate_dataset_name_unique(final_name, organization)
    except Exception:
        return ServiceError(
            f"A dataset named '{final_name}' already exists.", "DUPLICATE_NAME"
        )

    row_count = Row.objects.filter(dataset=source, deleted=False).count()
    if not _check_resource_limit(
        organization,
        workspace,
        "row_add",
        config={"total_rows": row_count},
    ):
        return ServiceError("Row limit reached.", "RESOURCE_LIMIT")

    # Create new dataset
    new_dataset = Dataset.objects.create(
        id=uuid.uuid4(),
        name=final_name,
        organization=organization,
        workspace=workspace,
        model_type=source.model_type,
        dataset_config=source.dataset_config,
        user=user,
    )

    # Clone columns (only OTHERS source)
    col_mapping = {}
    source_cols = Column.objects.filter(
        dataset=source, deleted=False, source=SourceChoices.OTHERS.value
    )
    for old_col in source_cols:
        new_col_id = uuid.uuid4()
        col_mapping[str(old_col.id)] = str(new_col_id)
        Column.objects.create(
            id=new_col_id,
            dataset=new_dataset,
            name=old_col.name,
            data_type=old_col.data_type,
            source=SourceChoices.OTHERS.value,
        )

    # Update column_order and column_config
    if source.column_order:
        new_dataset.column_order = [
            col_mapping[cid] for cid in source.column_order if cid in col_mapping
        ]
    if source.column_config:
        new_dataset.column_config = {
            col_mapping[old_id]: cfg
            for old_id, cfg in source.column_config.items()
            if old_id in col_mapping
        }
    new_dataset.save()

    # Clone rows and cells
    rows_cloned = 0
    for old_row in Row.objects.filter(dataset=source, deleted=False):
        new_row = Row.objects.create(
            id=uuid.uuid4(),
            dataset=new_dataset,
            order=old_row.order,
        )
        rows_cloned += 1

        cells_to_create = []
        for old_cell in Cell.objects.filter(
            row=old_row, deleted=False, column__id__in=list(col_mapping.keys())
        ):
            new_col_id = col_mapping.get(str(old_cell.column_id))
            if new_col_id:
                cells_to_create.append(
                    Cell(
                        id=uuid.uuid4(),
                        dataset=new_dataset,
                        column_id=new_col_id,
                        row=new_row,
                        value=old_cell.value,
                    )
                )
        if cells_to_create:
            Cell.objects.bulk_create(cells_to_create)

    return {
        "dataset_id": str(new_dataset.id),
        "name": new_dataset.name,
        "source_dataset_id": str(source.id),
        "columns_cloned": len(col_mapping),
        "rows_cloned": rows_cloned,
    }


def delete_rows(*, dataset_id, row_ids, organization=None):
    """Soft-delete rows from a dataset.

    Args:
        dataset_id: UUID string
        row_ids: List of row UUIDs to delete
        organization: Organization instance (required for org-scoped lookup)

    Returns:
        dict with dataset_id, deleted or ServiceError
    """
    from model_hub.models.develop_dataset import Cell, Dataset, Row
    from model_hub.services.dataset_validators import cleanup_annotation_metadata

    lookup = {"id": dataset_id, "deleted": False}
    if organization:
        lookup["organization"] = organization

    try:
        dataset = Dataset.objects.get(**lookup)
    except Dataset.DoesNotExist:
        return ServiceError(f"Dataset {dataset_id} not found.", "NOT_FOUND")

    # Validate rows exist
    existing_rows = Row.objects.filter(id__in=row_ids, dataset=dataset, deleted=False)
    if existing_rows.count() == 0:
        return ServiceError("No matching rows found in this dataset.", "NOT_FOUND")

    deleted = existing_rows.update(deleted=True)
    Cell.objects.filter(row_id__in=row_ids, dataset=dataset).update(deleted=True)

    # Clean up annotation metadata
    cleanup_annotation_metadata(dataset)

    return {"dataset_id": str(dataset.id), "deleted": deleted}


def delete_datasets(*, dataset_ids, organization):
    """Soft-delete one or more datasets.

    Returns:
        dict with deleted count and names or ServiceError
    """
    from model_hub.models.develop_dataset import Dataset

    datasets = Dataset.objects.filter(
        id__in=dataset_ids,
        deleted=False,
        organization=organization,
    )

    found_ids = set(str(d) for d in datasets.values_list("id", flat=True))
    missing_ids = [did for did in dataset_ids if did not in found_ids]
    if missing_ids:
        return ServiceError(
            f"Datasets not found: {', '.join(missing_ids)}", "NOT_FOUND"
        )

    names = list(datasets.values_list("name", flat=True))
    deleted = datasets.update(deleted=True)

    return {"deleted": deleted, "names": names}


def duplicate_rows(*, dataset_id, row_ids, num_copies, organization, workspace):
    """Duplicate specific rows in a dataset.

    Returns:
        dict with dataset_id, source_rows, copies_per_row, total_new_rows or ServiceError
    """
    import json

    from model_hub.models.develop_dataset import Cell, Dataset, Row
    from model_hub.services.dataset_validators import MAX_DUPLICATE_COPIES

    try:
        dataset = Dataset.objects.get(
            id=dataset_id, deleted=False, organization=organization
        )
    except Dataset.DoesNotExist:
        return ServiceError(f"Dataset {dataset_id} not found.", "NOT_FOUND")

    if num_copies < 1:
        return ServiceError("Number of copies must be at least 1.", "VALIDATION_ERROR")
    if num_copies > MAX_DUPLICATE_COPIES:
        return ServiceError(
            f"Number of copies cannot exceed {MAX_DUPLICATE_COPIES}.",
            "VALIDATION_ERROR",
        )

    source_rows = Row.objects.filter(id__in=row_ids, dataset=dataset, deleted=False)
    if source_rows.count() == 0:
        return ServiceError("No matching rows found.", "NOT_FOUND")

    # Resource limit check
    if not _check_resource_limit(
        organization,
        workspace,
        "row_add",
        config={"total_rows": source_rows.count() * num_copies},
    ):
        return ServiceError("Row limit reached.", "RESOURCE_LIMIT")

    last_row = Row.all_objects.filter(dataset=dataset).order_by("-created_at").first()
    max_order = last_row.order if last_row else -1

    source_cells = Cell.objects.filter(
        row__in=source_rows, deleted=False
    ).select_related("column")

    cells_by_row = {}
    for cell in source_cells:
        cells_by_row.setdefault(cell.row_id, []).append(cell)

    new_rows = []
    new_cells = []
    current_order = max_order + 1

    for source_row in source_rows:
        for _ in range(num_copies):
            new_row = Row(id=uuid.uuid4(), dataset=dataset, order=current_order)
            new_rows.append(new_row)
            current_order += 1

            if source_row.id in cells_by_row:
                for source_cell in cells_by_row[source_row.id]:
                    new_cells.append(
                        Cell(
                            id=uuid.uuid4(),
                            dataset=dataset,
                            column=source_cell.column,
                            row=new_row,
                            value=source_cell.value,
                            value_infos=(
                                source_cell.value_infos
                                if source_cell.value_infos
                                else json.dumps({})
                            ),
                            status=source_cell.status,
                        )
                    )

    Row.objects.bulk_create(new_rows)
    Cell.objects.bulk_create(new_cells)

    return {
        "dataset_id": str(dataset.id),
        "dataset_name": dataset.name,
        "source_rows": source_rows.count(),
        "copies_per_row": num_copies,
        "total_new_rows": len(new_rows),
    }


def duplicate_dataset(*, dataset_id, name, row_ids, organization, workspace, user):
    """Duplicate a dataset with optional row selection.

    Returns:
        dict with dataset_id, name, rows_copied, columns_copied or ServiceError
    """
    import json

    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    try:
        source_dataset = Dataset.objects.get(
            id=dataset_id, deleted=False, organization=organization
        )
    except Dataset.DoesNotExist:
        return ServiceError(f"Dataset {dataset_id} not found.", "NOT_FOUND")

    if not name or not name.strip():
        return ServiceError("Dataset name is required.", "VALIDATION_ERROR")

    # Dataset creation limit
    if not _check_resource_limit(organization, workspace, "dataset_add"):
        return ServiceError("Dataset creation limit reached.", "RESOURCE_LIMIT")

    # Determine source rows
    if row_ids:
        source_rows = Row.objects.filter(
            id__in=row_ids, dataset=source_dataset, deleted=False
        )
    else:
        source_rows = Row.objects.filter(dataset=source_dataset, deleted=False)

    # Row limit check
    if not _check_resource_limit(
        organization,
        workspace,
        "row_add",
        config={"total_rows": source_rows.count()},
    ):
        return ServiceError("Row limit reached.", "RESOURCE_LIMIT")

    # Create new dataset
    new_dataset = Dataset.objects.create(
        id=uuid.uuid4(),
        name=name,
        organization=source_dataset.organization,
        workspace=workspace,
        model_type=source_dataset.model_type,
        column_order=(
            source_dataset.column_order.copy() if source_dataset.column_order else []
        ),
        column_config=(
            source_dataset.column_config.copy() if source_dataset.column_config else {}
        ),
        user=user,
    )

    # Copy columns (exclude experiment columns)
    source_columns = Column.objects.filter(
        dataset=source_dataset, deleted=False
    ).exclude(
        source__in=[
            SourceChoices.EXPERIMENT.value,
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        ]
    )

    col_mapping = {}
    for old_col in source_columns:
        new_col_id = uuid.uuid4()
        col_mapping[str(old_col.id)] = str(new_col_id)
        Column.objects.create(
            id=new_col_id,
            dataset=new_dataset,
            name=old_col.name,
            data_type=old_col.data_type,
            source=SourceChoices.OTHERS.value,
        )

    # Update column_order to use new IDs
    if new_dataset.column_order:
        new_dataset.column_order = [
            col_mapping.get(cid, cid)
            for cid in new_dataset.column_order
            if col_mapping.get(cid)
        ]
    if new_dataset.column_config:
        new_dataset.column_config = {
            col_mapping[old_id]: cfg
            for old_id, cfg in new_dataset.column_config.items()
            if old_id in col_mapping
        }
    new_dataset.save()

    # Copy rows and cells in batches
    batch_size = 1000
    rows_copied = 0
    for i in range(0, source_rows.count(), batch_size):
        batch_rows = source_rows[i : i + batch_size]
        new_rows = []
        new_cells = []
        row_id_mapping = {}

        for old_row in batch_rows:
            new_row_id = uuid.uuid4()
            row_id_mapping[old_row.id] = new_row_id
            new_rows.append(
                Row(id=new_row_id, dataset=new_dataset, order=old_row.order)
            )

        Row.objects.bulk_create(new_rows)
        rows_copied += len(new_rows)

        old_row_ids = list(row_id_mapping.keys())
        batch_cells = Cell.objects.filter(
            row_id__in=old_row_ids, deleted=False
        ).select_related("column")

        for cell in batch_cells:
            new_col_id = col_mapping.get(str(cell.column_id))
            if new_col_id and cell.row_id in row_id_mapping:
                new_cells.append(
                    Cell(
                        id=uuid.uuid4(),
                        dataset=new_dataset,
                        column_id=new_col_id,
                        row_id=row_id_mapping[cell.row_id],
                        value=cell.value,
                        value_infos=(
                            cell.value_infos if cell.value_infos else json.dumps({})
                        ),
                        status=cell.status,
                    )
                )

        if new_cells:
            Cell.objects.bulk_create(new_cells)

    return {
        "dataset_id": str(new_dataset.id),
        "dataset_name": new_dataset.name,
        "source_dataset_id": str(source_dataset.id),
        "rows_copied": rows_copied,
        "columns_copied": len(col_mapping),
    }


def merge_datasets(
    *, source_dataset_id, target_dataset_id, row_ids, organization, workspace
):
    """Merge rows from source dataset into target dataset.

    Returns:
        dict with target_dataset_id, rows_merged, columns_created or ServiceError
    """
    import json

    from model_hub.models.choices import DataTypeChoices, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    try:
        source_dataset = Dataset.objects.get(
            id=source_dataset_id, deleted=False, organization=organization
        )
    except Dataset.DoesNotExist:
        return ServiceError(
            f"Source dataset {source_dataset_id} not found.", "NOT_FOUND"
        )

    try:
        target_dataset = Dataset.objects.get(
            id=target_dataset_id, deleted=False, organization=organization
        )
    except Dataset.DoesNotExist:
        return ServiceError(
            f"Target dataset {target_dataset_id} not found.", "NOT_FOUND"
        )

    if str(source_dataset_id) == str(target_dataset_id):
        return ServiceError(
            "Source and target datasets cannot be the same.", "VALIDATION_ERROR"
        )

    # Determine source rows
    if row_ids:
        source_rows = Row.objects.filter(
            id__in=row_ids, dataset=source_dataset, deleted=False
        )
    else:
        source_rows = Row.objects.filter(dataset=source_dataset, deleted=False)

    if source_rows.count() == 0:
        return ServiceError("No rows to merge.", "VALIDATION_ERROR")

    # Row limit check
    existing_count = Row.objects.filter(dataset=target_dataset, deleted=False).count()
    if not _check_resource_limit(
        organization,
        workspace,
        "row_add",
        config={"total_rows": existing_count + source_rows.count()},
    ):
        return ServiceError("Row limit reached.", "RESOURCE_LIMIT")

    # Get source and target columns
    source_columns = Column.objects.filter(
        dataset=source_dataset, deleted=False
    ).exclude(
        source__in=[
            SourceChoices.EXPERIMENT.value,
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        ]
    )
    target_columns = Column.objects.filter(
        dataset=target_dataset, deleted=False
    ).exclude(
        source__in=[
            SourceChoices.EXPERIMENT.value,
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        ]
    )

    # Build column mapping (source col → target col)
    column_mapping = {}
    columns_created = 0

    for source_col in source_columns:
        matching = next(
            (
                c
                for c in target_columns
                if c.name == source_col.name and c.data_type == source_col.data_type
            ),
            None,
        )
        if matching:
            column_mapping[str(source_col.id)] = str(matching.id)
        else:
            new_col_id = uuid.uuid4()
            Column.objects.create(
                id=new_col_id,
                name=source_col.name,
                data_type=DataTypeChoices.TEXT.value,
                source=SourceChoices.OTHERS.value,
                dataset=target_dataset,
            )
            column_mapping[str(source_col.id)] = str(new_col_id)
            columns_created += 1

            # Update target dataset column order/config
            col_order = target_dataset.column_order or []
            col_config = target_dataset.column_config or {}
            col_order.append(str(new_col_id))
            col_config[str(new_col_id)] = {"is_visible": True, "is_frozen": None}
            target_dataset.column_order = col_order
            target_dataset.column_config = col_config
            target_dataset.save(update_fields=["column_order", "column_config"])

    # Get max order in target
    last_row = (
        Row.all_objects.filter(dataset=target_dataset).order_by("-created_at").first()
    )
    current_order = (last_row.order if last_row else -1) + 1

    # Merge rows in batches
    batch_size = 1000
    rows_merged = 0
    for i in range(0, source_rows.count(), batch_size):
        batch_rows = source_rows[i : i + batch_size]
        new_rows = []
        row_id_mapping = {}

        for old_row in batch_rows:
            new_row_id = uuid.uuid4()
            row_id_mapping[old_row.id] = new_row_id
            new_rows.append(
                Row(id=new_row_id, dataset=target_dataset, order=current_order)
            )
            current_order += 1

        Row.objects.bulk_create(new_rows)
        rows_merged += len(new_rows)

        old_row_ids = list(row_id_mapping.keys())
        batch_cells = Cell.objects.filter(row_id__in=old_row_ids, deleted=False)

        new_cells = []
        for cell in batch_cells:
            new_col_id = column_mapping.get(str(cell.column_id))
            if new_col_id and cell.row_id in row_id_mapping:
                new_cells.append(
                    Cell(
                        id=uuid.uuid4(),
                        dataset=target_dataset,
                        column_id=new_col_id,
                        row_id=row_id_mapping[cell.row_id],
                        value=cell.value,
                        value_infos=(
                            cell.value_infos if cell.value_infos else json.dumps({})
                        ),
                        status=cell.status,
                    )
                )

        if new_cells:
            Cell.objects.bulk_create(new_cells)

    return {
        "target_dataset_id": str(target_dataset.id),
        "target_dataset_name": target_dataset.name,
        "source_dataset_id": str(source_dataset.id),
        "rows_merged": rows_merged,
        "columns_created": columns_created,
    }


def add_rows_from_existing(
    *, target_dataset_id, source_dataset_id, column_mapping, organization, workspace
):
    """Add rows from one dataset to another using column name mapping.

    Args:
        column_mapping: dict mapping source column names to target column names

    Returns:
        dict with target_dataset_id, rows_added or ServiceError
    """
    import json

    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    try:
        target_dataset = Dataset.objects.get(
            id=target_dataset_id, deleted=False, organization=organization
        )
    except Dataset.DoesNotExist:
        return ServiceError(
            f"Target dataset {target_dataset_id} not found.", "NOT_FOUND"
        )

    try:
        source_dataset = Dataset.objects.get(
            id=source_dataset_id, deleted=False, organization=organization
        )
    except Dataset.DoesNotExist:
        return ServiceError(
            f"Source dataset {source_dataset_id} not found.", "NOT_FOUND"
        )

    if str(target_dataset_id) == str(source_dataset_id):
        return ServiceError(
            "Source and target datasets cannot be the same.", "VALIDATION_ERROR"
        )

    if not column_mapping or len(column_mapping) == 0:
        return ServiceError(
            "At least one column mapping is required.", "VALIDATION_ERROR"
        )

    # Validate no duplicate target columns
    target_names = list(column_mapping.values())
    if len(target_names) != len(set(target_names)):
        return ServiceError("Duplicate target columns in mapping.", "VALIDATION_ERROR")

    # Resolve column names to IDs
    source_columns = Column.objects.filter(dataset=source_dataset, deleted=False)
    target_columns = Column.objects.filter(dataset=target_dataset, deleted=False)

    source_col_map = {c.name: c for c in source_columns}
    target_col_map = {c.name: c for c in target_columns}

    # Build ID-based mapping
    col_id_mapping = {}  # source col ID → target col ID
    for src_name, tgt_name in column_mapping.items():
        src_col = source_col_map.get(src_name)
        tgt_col = target_col_map.get(tgt_name)
        if src_col and tgt_col:
            col_id_mapping[str(src_col.id)] = str(tgt_col.id)

    if not col_id_mapping:
        return ServiceError(
            "No valid column mappings found. Check column names.", "VALIDATION_ERROR"
        )

    # Row limit check
    source_rows = Row.objects.filter(dataset=source_dataset, deleted=False)
    existing_count = Row.objects.filter(dataset=target_dataset, deleted=False).count()
    if not _check_resource_limit(
        organization,
        workspace,
        "row_add",
        config={"total_rows": existing_count + source_rows.count()},
    ):
        return ServiceError("Row limit reached.", "RESOURCE_LIMIT")

    # Get max order in target
    last_row = (
        Row.all_objects.filter(dataset=target_dataset).order_by("-created_at").first()
    )
    current_order = (last_row.order if last_row else -1) + 1

    # Copy rows in batches
    batch_size = 1000
    rows_added = 0
    for i in range(0, source_rows.count(), batch_size):
        batch_rows = source_rows[i : i + batch_size]
        new_rows = []
        row_id_mapping_batch = {}

        for old_row in batch_rows:
            new_row_id = uuid.uuid4()
            row_id_mapping_batch[old_row.id] = new_row_id
            new_rows.append(
                Row(id=new_row_id, dataset=target_dataset, order=current_order)
            )
            current_order += 1

        Row.objects.bulk_create(new_rows)
        rows_added += len(new_rows)

        old_row_ids = list(row_id_mapping_batch.keys())
        batch_cells = Cell.objects.filter(row_id__in=old_row_ids, deleted=False)

        new_cells = []
        for cell in batch_cells:
            new_col_id = col_id_mapping.get(str(cell.column_id))
            if new_col_id and cell.row_id in row_id_mapping_batch:
                new_cells.append(
                    Cell(
                        id=uuid.uuid4(),
                        dataset=target_dataset,
                        column_id=new_col_id,
                        row_id=row_id_mapping_batch[cell.row_id],
                        value=cell.value,
                        value_infos=(
                            cell.value_infos if cell.value_infos else json.dumps({})
                        ),
                        status=cell.status,
                    )
                )

        if new_cells:
            Cell.objects.bulk_create(new_cells)

    return {
        "target_dataset_id": str(target_dataset.id),
        "target_dataset_name": target_dataset.name,
        "source_dataset_id": str(source_dataset.id),
        "rows_added": rows_added,
        "columns_mapped": len(col_id_mapping),
    }


def create_dataset_from_file(
    *, file_content, file_name, name, organization, workspace, user, model_type=None
):
    """Create a dataset from file content (CSV/JSON/JSONL).

    Validates the file, creates the dataset, uploads to Minio, and starts
    background processing — same flow as CreateDatasetFromLocalFileView.

    Args:
        file_content: Raw file bytes
        file_name: Original filename (used for format detection)
        name: Dataset name
        organization: Organization instance
        workspace: Workspace instance
        user: User instance
        model_type: Optional model type

    Returns:
        dict with dataset_id, name, estimated_rows, estimated_columns or ServiceError
    """
    import io
    import os

    from django.core.files.uploadedfile import InMemoryUploadedFile

    from model_hub.constants import (
        ALLOWED_FILE_EXTENSIONS,
        MAX_FILE_SIZE_BYTES,
        MAX_FILE_SIZE_MB,
    )
    from model_hub.models.choices import DatasetSourceChoices
    from model_hub.models.develop_dataset import Dataset
    from model_hub.validators.dataset_validators import validate_dataset_name_unique

    # Validate file extension
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in ALLOWED_FILE_EXTENSIONS:
        return ServiceError(
            f"Unsupported file format '{ext}'. Allowed: {', '.join(sorted(ALLOWED_FILE_EXTENSIONS))}",
            "VALIDATION_ERROR",
        )

    # Validate file size
    if len(file_content) > MAX_FILE_SIZE_BYTES:
        return ServiceError(
            f"File size exceeds the maximum allowed limit of {MAX_FILE_SIZE_MB} MB.",
            "VALIDATION_ERROR",
        )

    # Resource limit check
    if not _check_resource_limit(organization, workspace, "dataset_add"):
        return ServiceError("Dataset creation limit reached.", "RESOURCE_LIMIT")

    # Check duplicate name
    try:
        validate_dataset_name_unique(name, organization)
    except Exception:
        return ServiceError(
            f"A dataset named '{name}' already exists in this organization.",
            "DUPLICATE_NAME",
        )

    # Create an in-memory file for processing
    content_type = {
        ".csv": "text/csv",
        ".json": "application/json",
        ".jsonl": "text/plain",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(ext, "application/octet-stream")

    file_obj = InMemoryUploadedFile(
        file=io.BytesIO(file_content),
        field_name="file",
        name=file_name,
        content_type=content_type,
        size=len(file_content),
        charset=None,
    )

    # Quick file validation
    from model_hub.utils.file_reader import FileProcessor

    data, error = FileProcessor.process_file(file_obj=file_obj)
    if error:
        return ServiceError(error, "VALIDATION_ERROR")

    rows_in_dataset = data.shape[0]

    # Row limit check
    if not _check_resource_limit(
        organization, workspace, "row_add", config={"total_rows": rows_in_dataset}
    ):
        return ServiceError("Row limit reached.", "RESOURCE_LIMIT")

    # Upload to Minio
    file_obj.seek(0)
    from model_hub.views.datasets.create.file_upload import upload_file_to_minio

    file_key = f"datasets/{organization.id}/{uuid.uuid4()}/{file_name}"
    file_url = upload_file_to_minio(file_obj, file_key, org_id=str(organization.id))

    # Create dataset
    import pandas as pd

    dataset = Dataset.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=model_type,
        source=DatasetSourceChoices.BUILD.value,
        dataset_config={
            "dataset_source_local": True,
            "file_processing_status": "queued",
            "file_processing_queued_at": pd.Timestamp.now().isoformat(),
            "original_filename": file_name,
            "file_url": file_url,
            "estimated_rows": rows_in_dataset,
            "estimated_columns": data.shape[1],
        },
        user=user,
    )

    # Start background processing
    from model_hub.views.datasets.create.file_upload import process_dataset_from_file

    process_dataset_from_file.delay(str(dataset.id), file_url, file_name)

    return {
        "dataset_id": str(dataset.id),
        "name": dataset.name,
        "estimated_rows": int(rows_in_dataset),
        "estimated_columns": int(data.shape[1]),
        "processing_status": "queued",
    }


def create_dataset_from_huggingface(
    *,
    hf_dataset_name,
    hf_config,
    hf_split,
    name,
    organization,
    workspace,
    user,
    model_type=None,
    num_rows=None,
):
    """Create a dataset from a HuggingFace dataset.

    Validates the HF dataset, creates columns from its schema, creates empty
    rows, and starts background processing — same flow as
    CreateDatasetFromHuggingFaceView.

    Args:
        hf_dataset_name: HuggingFace dataset path (e.g. 'squad', 'glue')
        hf_config: Dataset configuration/subset name
        hf_split: Dataset split (e.g. 'train', 'test')
        name: Dataset name (defaults to hf_dataset_name with / replaced)
        organization: Organization instance
        workspace: Workspace instance
        user: User instance
        model_type: Optional model type
        num_rows: Optional max rows to import

    Returns:
        dict with dataset_id, name, rows, columns or ServiceError
    """
    from django.db import transaction

    from model_hub.constants import MAX_DATASET_NAME_LENGTH
    from model_hub.models.choices import SourceChoices, StatusType
    from model_hub.models.develop_dataset import Column, Dataset, Row
    from model_hub.serializers.develop_dataset import DatasetSerializer
    from model_hub.utils.utils import (
        get_data_type_huggingface,
        load_hf_dataset_with_retries,
    )
    from model_hub.validators.dataset_validators import validate_dataset_name_unique

    # Resource limit check
    if not _check_resource_limit(organization, workspace, "dataset_add"):
        return ServiceError("Dataset creation limit reached.", "RESOURCE_LIMIT")

    # Validate num_rows
    if num_rows is not None and num_rows < 0:
        return ServiceError("Number of rows must be non-negative.", "VALIDATION_ERROR")

    # Determine dataset name
    final_name = name or hf_dataset_name.replace("/", "_")
    if len(final_name) > MAX_DATASET_NAME_LENGTH:
        final_name = final_name[:MAX_DATASET_NAME_LENGTH]

    # Check duplicate name
    try:
        validate_dataset_name_unique(final_name, organization)
    except Exception:
        return ServiceError(
            f"A dataset named '{final_name}' already exists in this organization.",
            "DUPLICATE_NAME",
        )

    # Early row limit check when num_rows is known upfront
    if num_rows and not _check_resource_limit(
        organization, workspace, "row_add", config={"total_rows": num_rows}
    ):
        return ServiceError("Row limit reached.", "RESOURCE_LIMIT")

    # Get dataset info from HuggingFace
    import requests

    from tfc.settings.settings import HUGGINGFACE_API_TOKEN

    try:
        headers = {"Authorization": f"Bearer {HUGGINGFACE_API_TOKEN}"}
        api_url = (
            f"https://datasets-server.huggingface.co/size?dataset={hf_dataset_name}"
        )
        response = requests.get(api_url, headers=headers, timeout=30)
        response.raise_for_status()
        splits = response.json().get("size", {}).get("splits", [])
        split_info = next(
            (s for s in splits if s.get("split") == hf_split),
            None,
        )
        if not split_info:
            return ServiceError(
                f"Split '{hf_split}' not found in dataset '{hf_dataset_name}'.",
                "NOT_FOUND",
            )
        rows_in_dataset = num_rows if num_rows else int(split_info.get("num_rows", 0))
    except requests.exceptions.HTTPError as e:
        if e.response and e.response.status_code == 501:
            return ServiceError(
                "Dataset contains arbitrary code and cannot be loaded.",
                "VALIDATION_ERROR",
            )
        return ServiceError(
            f"Failed to fetch dataset info from HuggingFace: {e}",
            "VALIDATION_ERROR",
        )
    except Exception as e:
        return ServiceError(
            f"Failed to fetch dataset info from HuggingFace: {e}",
            "VALIDATION_ERROR",
        )

    # Load first row to detect schema
    try:
        first_row = load_hf_dataset_with_retries(
            hf_dataset_name, hf_config, hf_split, str(organization.id), streaming=False
        )
        if not first_row:
            return ServiceError(
                "Failed to preview dataset from HuggingFace.", "VALIDATION_ERROR"
            )
    except Exception as e:
        return ServiceError(
            f"Failed to preview dataset from HuggingFace: {e}", "VALIDATION_ERROR"
        )

    # Create columns from schema
    columns_to_create = []
    column_order = []
    column_config_updates = {}

    for column_info in first_row["features"]:
        column_name = column_info["name"].strip()
        data_type = get_data_type_huggingface(column_info)
        col = Column(
            id=uuid.uuid4(),
            name=column_name,
            data_type=data_type,
            status=StatusType.RUNNING.value,
            source=SourceChoices.OTHERS.value,
        )
        columns_to_create.append(col)
        column_config_updates[str(col.id)] = {"is_visible": True, "is_frozen": None}

    # Create dataset + columns + rows in a transaction
    dataset_id = uuid.uuid4()
    dataset_serializer = DatasetSerializer(
        data={
            "id": dataset_id,
            "name": final_name,
            "organization": organization.id,
            "model_type": model_type,
            "user": user.id,
        }
    )
    if not dataset_serializer.is_valid():
        return ServiceError(
            f"Dataset validation failed: {dataset_serializer.errors}",
            "VALIDATION_ERROR",
        )

    with transaction.atomic():
        dataset = dataset_serializer.save()
        for col in columns_to_create:
            col.dataset = dataset
        Column.objects.bulk_create(columns_to_create)

        for col in columns_to_create:
            column_order.append(str(col.id))

        dataset.column_order = column_order
        dataset.column_config = column_config_updates
        dataset.save()

    # Create rows
    rows_map = {}
    for index in range(rows_in_dataset):
        rows_map[index] = str(Row.objects.create(dataset=dataset, order=index).id)

    # Start background processing via Temporal
    import tfc.temporal.background_tasks.activities  # noqa: F401
    from tfc.temporal.drop_in import start_activity

    start_activity(
        "process_huggingface_dataset_activity",
        args=(
            str(dataset.id),
            hf_dataset_name,
            hf_config,
            hf_split,
            str(organization.id),
            rows_in_dataset,
            column_order,
            rows_map,
        ),
        queue="tasks_l",
    )

    return {
        "dataset_id": str(dataset.id),
        "name": dataset.name,
        "rows": rows_in_dataset,
        "columns": len(columns_to_create),
        "column_names": [c.name for c in columns_to_create],
        "processing_status": "queued",
    }
