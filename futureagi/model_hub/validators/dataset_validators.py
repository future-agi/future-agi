"""Shared dataset validation helpers used by views, services, and AI tools."""

from rest_framework.exceptions import ValidationError

from model_hub.constants import (
    MAX_BATCH_DELETE_SIZE,
    MAX_EMPTY_DATASET_ROWS,
    MAX_FILE_SIZE_BYTES,
    MAX_FILE_SIZE_MB,
    MAX_MANUAL_COLUMNS,
    MAX_MANUAL_ROWS,
)
from tfc.utils.error_codes import get_error_message


def validate_dataset_name_unique(name, organization, exclude_id=None):
    """Check that no other non-deleted dataset in *organization* uses *name*.

    Raises ``ValidationError`` with ``DATASET_EXIST_IN_ORG`` when a duplicate
    is found.  Pass *exclude_id* when updating an existing dataset so it does
    not conflict with itself.
    """
    from model_hub.models.develop_dataset import Dataset

    qs = Dataset.objects.filter(name=name, organization=organization, deleted=False)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    if qs.exists():
        raise ValidationError(get_error_message("DATASET_EXIST_IN_ORG"))


def available_dataset_copy_name(source_name, organization):
    """Name an automatic copy without colliding with this org's live datasets."""
    from model_hub.models.develop_dataset import Dataset

    limit = Dataset._meta.get_field("name").max_length
    base = f"Copy of {source_name}"
    # Name availability is org-wide, even when a request has workspace context.
    # This lookup never returns dataset contents or crosses the caller's org.
    datasets = Dataset.no_workspace_objects.filter(organization=organization)
    candidate = base[:limit]
    copy_number = 2
    while datasets.filter(name=candidate).exists():
        suffix = f" ({copy_number})"
        candidate = f"{base[: limit - len(suffix)]}{suffix}"
        copy_number += 1
    return candidate


def validate_row_column_bounds(rows=None, columns=None):
    """Enforce upper bounds on manual row / column creation.

    Raises ``ValidationError`` when *rows* exceeds ``MAX_MANUAL_ROWS`` or
    *columns* exceeds ``MAX_MANUAL_COLUMNS``.
    """
    if rows is not None and rows > MAX_MANUAL_ROWS:
        msg = get_error_message("ROW_COUNT_EXCEEDS_LIMIT").format(MAX_MANUAL_ROWS)
        raise ValidationError(msg)
    if columns is not None and columns > MAX_MANUAL_COLUMNS:
        msg = get_error_message("COLUMN_COUNT_EXCEEDS_LIMIT").format(MAX_MANUAL_COLUMNS)
        raise ValidationError(msg)


def validate_empty_dataset_row_bound(rows):
    """Enforce upper bound on empty-dataset row creation.

    Raises ``ValidationError`` when *rows* exceeds ``MAX_EMPTY_DATASET_ROWS``.
    """
    if rows is not None and rows > MAX_EMPTY_DATASET_ROWS:
        msg = get_error_message("ROW_COUNT_EXCEEDS_LIMIT").format(
            MAX_EMPTY_DATASET_ROWS
        )
        raise ValidationError(msg)


def validate_file_size(file):
    """Validate uploaded file does not exceed the maximum allowed size.

    Raises ``ValidationError`` with ``FILE_SIZE_EXCEEDS_LIMIT`` when the file
    is too large.
    """
    if hasattr(file, "size") and file.size > MAX_FILE_SIZE_BYTES:
        msg = get_error_message("FILE_SIZE_EXCEEDS_LIMIT").format(MAX_FILE_SIZE_MB)
        raise ValidationError(msg)


def validate_batch_delete_size(ids):
    """Validate that the number of IDs in a batch-delete does not exceed the limit.

    Raises ``ValidationError`` with ``BATCH_SIZE_EXCEEDS_LIMIT``.
    """
    if len(ids) > MAX_BATCH_DELETE_SIZE:
        msg = get_error_message("BATCH_SIZE_EXCEEDS_LIMIT").format(
            MAX_BATCH_DELETE_SIZE
        )
        raise ValidationError(msg)
