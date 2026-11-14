"""
Service for managing derived variables from JSON/structured prompt outputs.

This module handles:
- Extracting dot-notation paths from JSON outputs
- Storing and managing derived variable metadata
- Cleaning up variables when columns are deleted
- Updating variables when prompts are rerun
"""

import json
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import structlog

from model_hub.utils.column_utils import is_json_response_format
from model_hub.utils.json_path_resolver import (
    extract_json_keys,
    parse_json_safely,
    resolve_json_path,
    resolve_json_path_raw,
)

logger = structlog.get_logger(__name__)

# Configuration constants for derived variable extraction
# These limits prevent excessive memory usage and processing time on deeply nested
# or very large JSON structures while providing reasonable coverage for typical use cases.
MAX_JSON_DEPTH = 5  # Maximum nesting depth to traverse when extracting paths
MAX_JSON_PATHS = 100  # Maximum number of paths to extract from a single JSON object
MAX_SAMPLE_LENGTH = 200  # Maximum length for sample values in schema


def infer_type_from_value(value: Any) -> str:
    """
    Infer the type of a value for schema documentation.

    Args:
        value: Any JSON value

    Returns:
        Type string: "string", "number", "boolean", "array", "object", "null"
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def extract_derived_variables_from_output(
    output: Any,
    column_name: str,
    max_depth: int = MAX_JSON_DEPTH,
    max_paths: int = MAX_JSON_PATHS,
) -> Dict[str, Any]:
    """
    Extract derived variables from a JSON output.

    Args:
        output: The prompt output (string or parsed JSON)
        column_name: Name of the column that contains this output
        max_depth: Maximum depth for path extraction
        max_paths: Maximum number of paths to extract

    Returns:
        Dict with structure:
        {
            "paths": ["path1", "path2", ...],
            "schema": {
                "path1": {"type": "string", "sample": "value"},
                ...
            },
            "full_variables": ["column.path1", "column.path2", ...],
            "raw_sample": {...}  # First few levels of JSON for preview
        }
    """
    # Try to parse the output as JSON
    parsed_data, is_valid_json = parse_json_safely(output)

    if not is_valid_json:
        return {
            "paths": [],
            "schema": {},
            "full_variables": [],
            "raw_sample": None,
            "is_json": False,
        }

    # Extract paths with early termination at max_paths limit
    # This avoids allocating memory for paths that will be discarded
    paths = extract_json_keys(parsed_data, max_depth=max_depth, max_paths=max_paths)

    # Build schema with types and samples
    # Use resolve_json_path_raw to preserve native types and avoid double-serialization
    schema = {}
    for path in paths:
        value = resolve_json_path_raw(parsed_data, path)
        schema[path] = {
            "type": infer_type_from_value(value),
            "sample": _truncate_sample(value),
        }

    # Generate full variable names
    full_variables = [f"{column_name}.{path}" for path in paths]

    # Create a truncated sample for preview
    raw_sample = _truncate_json_for_preview(parsed_data)

    return {
        "paths": paths,
        "schema": schema,
        "full_variables": full_variables,
        "raw_sample": raw_sample,
        "is_json": True,
    }


def _truncate_sample(value: Any, max_length: int = MAX_SAMPLE_LENGTH) -> Any:
    """Truncate a sample value for storage."""
    if isinstance(value, str) and len(value) > max_length:
        return value[:max_length] + "..."
    if isinstance(value, (list, dict)):
        try:
            serialized = json.dumps(value)
            if len(serialized) > max_length:
                return f"<{infer_type_from_value(value)}: {len(value) if isinstance(value, (list, dict)) else 1} items>"
        except (TypeError, ValueError):
            return "<complex value>"
    return value


def _truncate_json_for_preview(
    data: Any, max_depth: int = 2, current_depth: int = 0
) -> Any:
    """
    Truncate JSON for preview, limiting depth.
    """
    if current_depth >= max_depth:
        if isinstance(data, dict):
            return f"{{...{len(data)} keys}}"
        if isinstance(data, list):
            return f"[...{len(data)} items]"
        return data

    if isinstance(data, dict):
        return {
            key: _truncate_json_for_preview(value, max_depth, current_depth + 1)
            for key, value in list(data.items())[:10]  # Limit to 10 keys
        }
    if isinstance(data, list):
        return [
            _truncate_json_for_preview(item, max_depth, current_depth + 1)
            for item in data[:5]  # Limit to 5 items
        ]
    return data


def merge_derived_variables(
    existing: Dict[str, Any],
    new_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge new derived variables with existing ones.

    This handles the case where a prompt rerun produces different JSON structure.
    New paths are added, existing paths are updated, removed paths are kept but marked.

    Args:
        existing: Existing derived variables dict
        new_data: New derived variables from latest run

    Returns:
        Merged derived variables dict
    """
    if not existing:
        return new_data

    if not new_data.get("is_json"):
        return new_data

    merged_paths = list(new_data.get("paths", []))
    merged_schema = dict(new_data.get("schema", {}))

    # Track paths that existed before but are now missing
    existing_paths = set(existing.get("paths", []))
    new_paths = set(new_data.get("paths", []))
    removed_paths = existing_paths - new_paths

    # Mark removed paths as stale
    for path in removed_paths:
        if path in existing.get("schema", {}):
            merged_schema[path] = {
                **existing["schema"][path],
                "stale": True,
                "message": "Path not found in latest output",
            }
            if path not in merged_paths:
                merged_paths.append(path)

    return {
        "paths": merged_paths,
        "schema": merged_schema,
        "full_variables": (
            [f"{new_data.get('column_name', '')}.{p}" for p in merged_paths]
            if new_data.get("column_name")
            else new_data.get("full_variables", [])
        ),
        "raw_sample": new_data.get("raw_sample"),
        "is_json": True,
    }


def update_prompt_version_derived_variables(
    prompt_version,
    output_index: int,
    column_name: str,
    response_format_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update derived variables for a specific output in a prompt version.

    This should be called after a prompt run completes.

    Args:
        prompt_version: PromptVersion model instance
        output_index: Index of the output in prompt_version.output
        column_name: Name of the column containing this output
        response_format_type: The response format type (e.g., "json_object", "object")

    Returns:
        Updated derived variables for this column
    """
    if not prompt_version.output:
        return {}

    if output_index >= len(prompt_version.output):
        logger.warning(
            "Output index out of range",
            output_index=output_index,
            output_length=len(prompt_version.output),
        )
        return {}

    output = prompt_version.output[output_index]

    # Check if this should be treated as JSON output
    # Uses is_json_response_format which handles dict, string, and UUID response formats
    is_json_output = is_json_response_format(response_format_type)

    # Also try to detect JSON even if not explicitly set
    if not is_json_output:
        _, is_json_output = parse_json_safely(output)

    if not is_json_output:
        return {"is_json": False, "paths": [], "schema": {}, "full_variables": []}

    # Extract variables
    derived_vars = extract_derived_variables_from_output(output, column_name)

    # Get existing metadata
    metadata = prompt_version.metadata or {}
    derived_variables_meta = metadata.get("derived_variables", {})

    # Merge with existing
    existing_for_column = derived_variables_meta.get(column_name, {})
    merged = merge_derived_variables(existing_for_column, derived_vars)

    # Update metadata
    derived_variables_meta[column_name] = merged
    metadata["derived_variables"] = derived_variables_meta
    prompt_version.metadata = metadata

    return merged


def cleanup_derived_variables_for_column(
    prompt_version,
    column_name: str,
) -> bool:
    """
    Remove derived variables for a deleted column.

    Args:
        prompt_version: PromptVersion model instance
        column_name: Name of the deleted column

    Returns:
        True if cleanup was performed, False otherwise
    """
    if not prompt_version.metadata:
        return False

    derived_variables = prompt_version.metadata.get("derived_variables", {})

    if column_name not in derived_variables:
        return False

    del derived_variables[column_name]
    prompt_version.metadata["derived_variables"] = derived_variables

    return True


def rename_derived_variables_for_column(
    prompt_version,
    old_column_name: str,
    new_column_name: str,
) -> bool:
    """
    Update derived variables when a column is renamed.

    This updates both the key in derived_variables dict and the
    full_variables paths that contain the column name.

    Args:
        prompt_version: PromptVersion model instance
        old_column_name: Original name of the column
        new_column_name: New name of the column

    Returns:
        True if rename was performed, False otherwise
    """
    if not prompt_version.metadata:
        return False

    derived_variables = prompt_version.metadata.get("derived_variables", {})

    if old_column_name not in derived_variables:
        return False

    # Get the existing data for this column
    column_data = derived_variables[old_column_name]

    # Update full_variables with new column name prefix
    if column_data.get("full_variables"):
        old_prefix = f"{old_column_name}."
        new_prefix = f"{new_column_name}."
        column_data["full_variables"] = [
            fv.replace(old_prefix, new_prefix, 1) if fv.startswith(old_prefix) else fv
            for fv in column_data["full_variables"]
        ]

    # Remove old key and add with new key
    del derived_variables[old_column_name]
    derived_variables[new_column_name] = column_data

    prompt_version.metadata["derived_variables"] = derived_variables

    return True


def rename_derived_variables_in_run_prompter(
    run_prompter,
    old_column_name: str,
    new_column_name: str,
) -> bool:
    """
    Update derived variables stored in RunPrompter.run_prompt_config when a column is renamed.

    Args:
        run_prompter: RunPrompter model instance
        old_column_name: Original name of the column
        new_column_name: New name of the column

    Returns:
        True if rename was performed, False otherwise
    """
    if not run_prompter.run_prompt_config:
        return False

    derived_vars = run_prompter.run_prompt_config.get("derived_variables", {})

    if not derived_vars or not derived_vars.get("full_variables"):
        return False

    # Update full_variables with new column name prefix
    old_prefix = f"{old_column_name}."
    new_prefix = f"{new_column_name}."
    derived_vars["full_variables"] = [
        fv.replace(old_prefix, new_prefix, 1) if fv.startswith(old_prefix) else fv
        for fv in derived_vars["full_variables"]
    ]

    run_prompter.run_prompt_config["derived_variables"] = derived_vars

    return True


def get_all_derived_variables(prompt_version) -> Dict[str, List[str]]:
    """
    Get all derived variables for a prompt version.

    Returns a dict mapping column names to their full variable paths.

    Args:
        prompt_version: PromptVersion model instance

    Returns:
        Dict like:
        {
            "OutputColumn": ["OutputColumn.user.name", "OutputColumn.user.email"],
            ...
        }
    """
    if not prompt_version.metadata:
        return {}

    derived_variables = prompt_version.metadata.get("derived_variables", {})

    result = {}
    for column_name, data in derived_variables.items():
        if data.get("is_json"):
            result[column_name] = data.get("full_variables", [])

    return result


def get_derived_variable_schema(prompt_version, column_name: str) -> Dict[str, Any]:
    """
    Get the schema for derived variables of a specific column.

    Args:
        prompt_version: PromptVersion model instance
        column_name: Column name to get schema for

    Returns:
        Schema dict with paths, types, and samples
    """
    if not prompt_version.metadata:
        return {}

    derived_variables = prompt_version.metadata.get("derived_variables", {})
    return derived_variables.get(column_name, {})


def get_dataset_derived_variables(dataset_id: str, organization) -> Dict[str, Any]:
    """
    Get all derived variables from all run prompt columns in a dataset.

    This aggregates derived variables from all run prompt columns
    that produce JSON outputs.

    Uses bulk queries to avoid N+1 query patterns:
    - 1 query for dataset
    - 1 query for columns
    - 1 query for all RunPrompters

    Args:
        dataset_id: UUID of the dataset
        organization: Organization object for access control

    Returns:
        Dict with structure:
        {
            "derived_variables": {
                "ColumnName": {
                    "paths": [...],
                    "full_variables": [...],
                    "schema": {...}
                },
                ...
            }
        }
    """
    from model_hub.models.develop_dataset import Cell, Column, Dataset, SourceChoices
    from model_hub.models.run_prompt import RunPrompter

    try:
        # Get the dataset
        dataset = Dataset.objects.filter(
            id=dataset_id,
            organization=organization,
            deleted=False,
        ).first()

        if not dataset:
            return {"derived_variables": {}}

        # Get all run prompt columns in the dataset with prefetched RunPrompters
        run_prompt_columns = (
            Column.objects.filter(
                dataset=dataset,
                source=SourceChoices.RUN_PROMPT.value,
                deleted=False,
            )
            .exclude(source_id__isnull=True)
            .select_related("dataset")
        )

        # Build a mapping of source_id to column for efficient lookup
        column_by_source_id = {col.source_id: col for col in run_prompt_columns}

        if not column_by_source_id:
            return {"derived_variables": {}}

        # Fetch all RunPrompters in one query (eliminates N+1)
        run_prompters = RunPrompter.objects.filter(
            id__in=column_by_source_id.keys(),
            deleted=False,
        ).only("id", "run_prompt_config")

        all_derived_variables = {}

        # Build mapping of run_prompter_id to derived variables
        for run_prompter in run_prompters:
            column = column_by_source_id.get(str(run_prompter.id))
            if not column:
                continue

            try:
                # Check if derived variables are stored in run_prompt_config
                run_config = run_prompter.run_prompt_config or {}
                derived_vars = run_config.get("derived_variables", {})

                if derived_vars and derived_vars.get("is_json"):
                    all_derived_variables[column.name] = {
                        "paths": derived_vars.get("paths", []),
                        "full_variables": derived_vars.get("full_variables", []),
                        "schema": derived_vars.get("schema", {}),
                    }
                    continue

                # If not stored, try to extract from first cell with value
                # Get first cell with a value to extract schema
                first_cell = (
                    Cell.objects.filter(
                        dataset=dataset,
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
                        all_derived_variables[column.name] = {
                            "paths": extracted.get("paths", []),
                            "full_variables": extracted.get("full_variables", []),
                            "schema": extracted.get("schema", {}),
                        }
                        # Store for future use
                        run_config["derived_variables"] = extracted
                        run_prompter.run_prompt_config = run_config
                        run_prompter.save(update_fields=["run_prompt_config"])

            except Exception as col_error:
                logger.exception(
                    "Error getting derived variables for column",
                    column_name=column.name,
                    column_id=str(column.id),
                    error=str(col_error),
                )
                continue

        return {"derived_variables": all_derived_variables}

    except Exception as e:
        logger.exception(
            "Error getting dataset derived variables",
            dataset_id=dataset_id,
            error=str(e),
        )
        return {"derived_variables": {}}


def resolve_derived_variable(
    prompt_version,
    full_variable_path: str,
    output_index: int = 0,
) -> Tuple[Optional[str], bool]:
    """
    Resolve a derived variable to its actual value.

    Args:
        prompt_version: PromptVersion model instance
        full_variable_path: Full path like "OutputColumn.user.name"
        output_index: Which output to resolve from (for batch runs)

    Returns:
        Tuple of (resolved_value, was_found)
    """
    if not prompt_version.output or output_index >= len(prompt_version.output):
        return None, False

    # Split the path into column name and JSON path
    parts = full_variable_path.split(".", 1)
    if len(parts) < 2:
        # No nested path, return the whole output
        return prompt_version.output[output_index], True

    column_name, json_path = parts

    # Get the output and resolve the path
    output = prompt_version.output[output_index]
    resolved = resolve_json_path(output, json_path)

    return resolved, True if resolved else False
