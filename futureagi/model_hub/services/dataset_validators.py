"""Shared validation logic for dataset operations — used by views and ai_tools."""

import json
from datetime import datetime

import structlog

from model_hub.constants import MAX_FILE_SIZE_BYTES
from model_hub.utils.json_path_resolver import parse_json_safely

logger = structlog.get_logger(__name__)

# --- Constants ---

NON_EDITABLE_SOURCE_TYPES = [
    "run_prompt",
    "evaluation",
    "optimization",
    "annotation_label",
    "retrieval",
    "extracted_entities",
    "extracted_json",
    "python_code",
    "classification",
    "api_call",
    "conditional",
    "evaluation_reason",
]

MAX_CELL_VALUE_LENGTH = 100_000
MAX_EMPTY_ROWS_PER_REQUEST = 100
MAX_PAGE_SIZE = 500
MAX_DUPLICATE_COPIES = 100


# --- Validation functions ---


def validate_and_convert_cell_value(value, data_type):
    """Validate and convert a cell value based on column data type.

    Returns (converted_value, error_message). error_message is None on success.
    """
    from model_hub.models.choices import (
        BooleanChoices,
        DataTypeChoices,
        DateTimeFormatChoices,
    )

    # Handle empty values for all types
    if not value or (isinstance(value, str) and value.strip() == ""):
        return None, None

    # Max length check
    if isinstance(value, str) and len(value) > MAX_CELL_VALUE_LENGTH:
        return (
            None,
            f"Value exceeds maximum length of {MAX_CELL_VALUE_LENGTH} characters",
        )

    if data_type == DataTypeChoices.TEXT.value:
        return str(value), None

    if data_type == DataTypeChoices.BOOLEAN.value:
        if value in BooleanChoices.TRUE_OPTIONS.value:
            return "true", None
        if value in BooleanChoices.FALSE_OPTIONS.value:
            return "false", None
        allowed = BooleanChoices.TRUE_OPTIONS.value + BooleanChoices.FALSE_OPTIONS.value
        return None, f"Invalid boolean value. Allowed: {allowed}"

    if data_type == DataTypeChoices.INTEGER.value:
        try:
            return str(int(float(value))), None
        except (ValueError, TypeError):
            return None, "Invalid integer value"

    if data_type == DataTypeChoices.FLOAT.value:
        try:
            return str(float(value)), None
        except (ValueError, TypeError):
            return None, "Invalid float value"

    if data_type == DataTypeChoices.DATETIME.value:
        for date_format in DateTimeFormatChoices.OPTIONS.value:
            try:
                dt = datetime.strptime(value, date_format)
                return dt.strftime("%Y-%m-%d %H:%M:%S"), None
            except ValueError:
                continue
        return (
            None,
            f"Invalid datetime format. Accepted: {DateTimeFormatChoices.OPTIONS.value}",
        )

    if data_type == DataTypeChoices.ARRAY.value:
        parsed, is_valid = parse_json_safely(value)
        if is_valid and isinstance(parsed, list):
            return json.dumps(parsed), None
        return None, "Invalid array value — not valid JSON array"

    if data_type == DataTypeChoices.JSON.value:
        parsed, is_valid = parse_json_safely(value)
        if is_valid:
            return json.dumps(parsed), None
        return None, "Invalid JSON value — not valid JSON"

    if data_type in (
        DataTypeChoices.IMAGE.value,
        DataTypeChoices.IMAGES.value,
        DataTypeChoices.AUDIO.value,
        DataTypeChoices.DOCUMENT.value,
    ):
        return (
            None,
            f"Cannot update {data_type} cells via this tool — use the dashboard UI",
        )

    # OTHERS, PERSONA, or unknown types — store as-is
    return str(value), None


def validate_column_is_editable(column):
    """Check if a column's source type allows direct cell editing.

    Returns (is_editable: bool, error_message: str | None).
    """
    if column.source in NON_EDITABLE_SOURCE_TYPES:
        return False, (
            f"Column '{column.name}' (source: {column.source}) is not directly "
            "editable. It is managed by its source operation."
        )
    return True, None


def validate_num_rows(raw_value, max_allowed=MAX_EMPTY_ROWS_PER_REQUEST):
    """Parse and validate a num_rows value.

    Returns (num_rows: int, error_message: str | None).
    """
    try:
        num_rows = int(raw_value)
    except (ValueError, TypeError):
        return None, "Number of rows must be a valid integer"

    if num_rows <= 0:
        return None, "Number of rows must be at least 1"

    if num_rows > max_allowed:
        return None, f"Number of rows cannot exceed {max_allowed}"

    return num_rows, None


def validate_row_ids_or_select_all(row_ids, selected_all_rows):
    """Validate that either row_ids or selected_all_rows is provided.

    Returns (is_valid: bool, error_message: str | None).
    """
    if not row_ids and not selected_all_rows:
        return False, "Either row_ids or selected_all_rows must be provided"
    return True, None


def cleanup_annotation_metadata(dataset):
    """Clean up annotation label metadata after row deletion.

    Extracted from DeleteRowView for reuse in service layer.
    """
    from model_hub.models.develop_annotations import Annotations

    try:
        annotations = Annotations.objects.filter(dataset=dataset, deleted=False)
        for annot in annotations:
            for label in annot.labels.all():
                if label.type == "text":
                    existing_metadata = label.metadata or {}
                    existing_metadata[str(dataset.id)] = {}
                    label.metadata = existing_metadata
                    label.save(update_fields=["metadata"])
    except Exception as e:
        logger.warning(
            "annotation_metadata_cleanup_failed",
            dataset_id=str(dataset.id),
            error=str(e),
        )
