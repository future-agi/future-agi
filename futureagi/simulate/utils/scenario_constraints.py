"""Shared custom-column constraint construction for scenario-generation payloads."""

from typing import Any

_COLUMN_TYPE_TO_CONSTRAINT: dict[str, str] = {
    "json": "json",
    "persona": "json",
    "number": "number",
    "integer": "number",
    "float": "number",
    "boolean": "boolean",
    "string": "text",
    "datetime": "datetime",
    "array": "array",
}


def apply_custom_column_constraints(
    constraints: list[dict[str, Any]],
    schema: dict[str, dict[str, Any]],
    custom_columns: list[dict[str, Any]] | None,
    agent_name: str,
    *,
    branch_context_footer: str = "",
) -> None:
    """Append `custom_columns` entries to `constraints` and `schema`, in place."""
    if not custom_columns:
        return
    for column in custom_columns:
        column_name = column.get("name")
        if not column_name:
            continue
        column_type = column.get("data_type", "text")
        column_description = column.get("description", "")
        constraint_type = _COLUMN_TYPE_TO_CONSTRAINT.get(column_type, "text")
        default_property: dict[str, Any] = (
            {"min_length": 10, "max_length": 500, "required_elements": []}
            if constraint_type == "text"
            else {}
        )
        user_property = column.get("property") or {}
        constraints.append(
            {
                "field": column_name,
                "type": constraint_type,
                "content": (
                    f"{column_description}. Generate realistic and contextually relevant "
                    f"data for {agent_name} scenarios that can be tailored using the "
                    f"conversation branch information below.{branch_context_footer}"
                ),
                "property": {**default_property, **user_property},
            }
        )
        schema[column_name] = {"type": constraint_type}
