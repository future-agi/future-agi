import ast

import structlog

from model_hub.models.develop_dataset import Row
from simulate.utils.scenario_rows import org_cells, org_columns, org_rows

logger = structlog.get_logger(__name__)


def _coerce_persona_value(value):
    if isinstance(value, str):
        if value.strip().startswith("{"):
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}
    return value if isinstance(value, dict) else {}


def _call_organization_id(call_execution):
    """The organization of the call's own run test; None when it has none."""
    test_execution = getattr(call_execution, "test_execution", None)
    run_test = getattr(test_execution, "run_test", None)
    return getattr(run_test, "organization_id", None)


def get_eval_playground_scenario_columns(call_execution, context=None) -> dict:
    call_metadata = getattr(call_execution, "call_metadata", None) or {}
    if not isinstance(call_metadata, dict):
        call_metadata = {}
    # The call's own row_id column wins over call_metadata, as in the detail
    # views: call_metadata is caller-written and a pre-fix result PATCH could
    # overwrite its row_id (T6 #7d).
    row_id = getattr(call_execution, "row_id", None) or call_metadata.get("row_id")
    if not row_id:
        return {}

    row_id_str = str(row_id)
    ctx = context or {}
    rows_map = ctx.get("rows_map")
    columns_by_dataset = ctx.get("columns_by_dataset")
    cells_by_row = ctx.get("cells_by_row")

    if rows_map is not None and row_id_str in rows_map:
        row = rows_map[row_id_str]
        ds_id = str(row.dataset.id) if row.dataset else None
        dataset_columns = columns_by_dataset.get(ds_id, []) if ds_id else []
        row_cells = cells_by_row.get(row_id_str, {})
    else:
        # Only live rows, columns and cells of the call's own organization:
        # another organization's row, or a soft-deleted one, reads exactly like
        # a row id that names nothing (T6 #7d).
        organization_id = _call_organization_id(call_execution)
        try:
            row = org_rows(organization_id).get(id=row_id)
        except Row.DoesNotExist:
            return {}
        dataset_columns = org_columns(organization_id).filter(
            id__in=row.dataset.column_order
        )
        row_cells = None

    scenario_data = {}
    for dataset_column in dataset_columns:
        try:
            if row_cells is not None:
                cell = row_cells.get(str(dataset_column.id))
            else:
                cell = (
                    org_cells(organization_id)
                    .filter(column=dataset_column, row_id=row.id)
                    .first()
                )
            cell_value = cell.value or "" if cell else ""
        except Exception:
            cell_value = ""
            logger.exception(
                "eval_playground_failed_to_fetch_scenario_cell",
                row_id=str(row.id),
                column_id=str(dataset_column.id),
            )

        if dataset_column.name == "persona":
            cell_value = _coerce_persona_value(cell_value)

        scenario_data[str(dataset_column.id)] = {
            "value": cell_value,
            "visible": True,
            "dataset_column_id": str(dataset_column.id),
            "dataset_id": str(row.dataset.id),
            "column_name": dataset_column.name,
            "data_type": dataset_column.data_type,
        }

    return scenario_data


def build_eval_playground_scenario_context(call_execution, context=None) -> dict | None:
    scenario = getattr(call_execution, "scenario", None)
    if not scenario:
        return None

    columns = {}
    call_metadata = getattr(call_execution, "call_metadata", None) or {}
    row_data = (
        call_metadata.get("row_data", {}) if isinstance(call_metadata, dict) else {}
    )
    if isinstance(row_data, dict):
        for key, value in row_data.items():
            columns[key] = _coerce_persona_value(value) if key == "persona" else value

    try:
        scenario_columns = get_eval_playground_scenario_columns(
            call_execution, context=context
        )
        for column in scenario_columns.values():
            if not isinstance(column, dict):
                continue
            column_name = column.get("column_name")
            if not column_name:
                continue
            columns[column_name] = column.get("value")
    except Exception:
        logger.exception("eval_playground_failed_to_hydrate_scenario_columns")

    return {
        "name": getattr(scenario, "name", None),
        "description": getattr(scenario, "description", None),
        "scenario_type": getattr(scenario, "scenario_type", None),
        "source": getattr(scenario, "source", None),
        "columns": columns,
    }
