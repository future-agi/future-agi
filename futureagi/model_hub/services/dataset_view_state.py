"""Validate grid state without executing saved predicates or trusting column IDs."""

from rest_framework.exceptions import ValidationError

from model_hub.models.develop_dataset import Column
from tracer.utils.filter_operators import filter_op_is_allowed

DENSITIES = ("compact", "comfortable", "tall", "full")
STATE_KEYS = {
    "q",
    "density",
    "filters",
    "sort",
    "layout",
    "failed",
    "fevals",
    "splits",
    "expr",
    "statuses",
    "tags",
}
SYSTEM_COLUMNS = {"system:order", "system:source", "system:updated"}


def _require(condition, message):
    if not condition:
        raise ValidationError({"state": message})


def validate_grid_state(state, dataset):
    _require(not (set(state) - STATE_KEYS), "Unknown grid state field.")
    _require(isinstance(state.get("q", ""), str), "Search must be text.")
    _require(state.get("density", "compact") in DENSITIES, "Unknown density.")
    # These controls have independent capabilities and cannot be activated by a view.
    _require(
        isinstance(state.get("failed", False), bool), "Invalid failed-only filter."
    )
    from model_hub.models.dataset_splits import DatasetSplit

    split_names = state.get("splits", "")
    _require(
        isinstance(split_names, str) and len(split_names) <= 3100,
        "Invalid saved split filter.",
    )
    selected = set(split_names.split(",")) - {"", "none"}
    available = set(
        DatasetSplit.objects.filter(dataset=dataset, name__in=selected).values_list(
            "name", flat=True
        )
    )
    _require(selected == available, "Saved split is not accessible.")
    for key, empty in (("expr", ""), ("statuses", []), ("tags", [])):
        _require(
            state.get(key, empty) == empty, f"{key} is not supported in saved views."
        )
    columns = {
        str(pk)
        for pk in Column.no_workspace_objects.filter(
            dataset=dataset, deleted=False
        ).values_list("id", flat=True)
    }
    known = columns | SYSTEM_COLUMNS
    fevals = state.get("fevals", [])
    _require(
        isinstance(fevals, list)
        and len(fevals) <= 100
        and all(isinstance(value, str) and value in columns for value in fevals),
        "Evaluation column is not accessible.",
    )
    filters = state.get("filters", [])
    _require(isinstance(filters, list) and len(filters) <= 100, "Invalid filters.")
    for item in filters:
        _require(isinstance(item, dict), "Invalid filter.")
        _require(
            isinstance(item.get("columnId"), str)
            and item["columnId"].split("::", 1)[0] in columns,
            "Filter column is not accessible.",
        )
        config = item.get("filterConfig")
        _require(isinstance(config, dict), "Invalid filter configuration.")
        _require(
            all(key in config for key in ("filterType", "filterOp", "filterValue")),
            "Incomplete filter.",
        )
        _require(
            isinstance(config["filterType"], str)
            and isinstance(config["filterOp"], str)
            and filter_op_is_allowed(config["filterType"], config["filterOp"]),
            "Invalid filter type or operator.",
        )
    sort = state.get("sort", [])
    _require(isinstance(sort, list) and len(sort) <= 100, "Invalid sort.")
    for item in sort:
        _require(isinstance(item, dict), "Invalid sort column.")
        _require(set(item) == {"colId", "sort"}, "Unknown sort field.")
        _require(
            isinstance(item.get("colId"), str) and item["colId"] in columns,
            "Sort column is not accessible.",
        )
        _require(item.get("sort") in ("asc", "desc"), "Unknown sort direction.")
    layout = state.get("layout", {})
    _require(isinstance(layout, dict), "Invalid layout.")
    _require(
        not (set(layout) - {"columns", "sections", "density", "extras"}),
        "Unknown layout field.",
    )
    _require(
        layout.get("density") is None or layout["density"] in DENSITIES,
        "Invalid layout density.",
    )
    for key in ("sections", "extras"):
        _require(layout.get(key, {}) == {}, "Unsupported grid layout option.")
    prefs = layout.get("columns", {})
    _require(isinstance(prefs, dict), "Invalid columns layout.")
    _require(
        not (set(prefs) - {"visible", "pinned", "order", "widths"}),
        "Unknown column preference.",
    )
    for field in ("visible", "pinned", "widths"):
        values = prefs.get(field, {})
        _require(isinstance(values, dict), "Invalid column preference map.")
        _require(set(values).issubset(known), "Layout column is not accessible.")
        for value in values.values():
            valid = (
                isinstance(value, bool)
                if field == "visible"
                else value in ("left", None)
                if field == "pinned"
                else type(value) in (int, float) and 0 < value <= 10000
            )
            _require(valid, "Invalid column preference value.")
    order = prefs.get("order", [])
    _require(
        isinstance(order, list) and all(isinstance(v, str) for v in order),
        "Invalid column order.",
    )
    _require(
        set(order).issubset(known) and len(order) == len(set(order)),
        "Invalid ordered column.",
    )
    return state
