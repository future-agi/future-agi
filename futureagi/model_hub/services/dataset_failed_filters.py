"""Shared, scoped predicates for the dataset's stored evaluation results."""

from django.db.models import Count, Q
from rest_framework.exceptions import ValidationError

from model_hub.models.choices import CellStatus, DataTypeChoices, SourceChoices

FAILED_FILTERS = frozenset(("system:row_failed", "system:row_failed_in"))
EVAL_SOURCES = frozenset(
    (
        SourceChoices.EVALUATION.value,
        SourceChoices.EXPERIMENT_EVALUATION.value,
        SourceChoices.OPTIMISATION_EVALUATION.value,
    )
)


def failure_columns(columns_map):
    """Only scored boolean columns have a stored pass/fail verdict.

    Numeric scores have no threshold in the classic column contract. Do not
    invent one or interpret execution errors/ordinary input booleans as fails.
    """
    return {
        key: column
        for key, column in columns_map.items()
        if column.source in EVAL_SOURCES
        and column.data_type == DataTypeChoices.BOOLEAN.value
    }


def validate_read_columns(filters, sort, columns_map):
    from model_hub.services.dataset_splits import validate_split_filter

    available = set(columns_map)
    eval_ids = set(failure_columns(columns_map))
    for item in filters:
        key = str(item.get("column_id", ""))
        config = item.get("filter_config") or {}
        if key == "system:splits":
            validate_split_filter(config)
        elif key in FAILED_FILTERS:
            value = config.get("filter_value")
            if key == "system:row_failed":
                valid = (
                    config.get("filter_type") == "boolean"
                    and config.get("filter_op") == "equals"
                    and value is True
                )
            else:
                valid = (
                    config.get("filter_type") == "categorical"
                    and config.get("filter_op") == "in"
                    and isinstance(value, list)
                    and bool(value)
                    and all(isinstance(v, str) and v in eval_ids for v in value)
                )
            if not valid:
                raise ValidationError(
                    {"filters": "Invalid or inaccessible failed-evaluation filter."}
                )
        elif key not in available:
            raise ValidationError(
                {"column_id": "Unknown or inaccessible dataset column."}
            )
    if any(str(item.get("column_id", "")) not in available for item in sort):
        raise ValidationError(
            {"column_id": "Unknown or inaccessible dataset sort column."}
        )


def failed_cells(cells, columns_map, selected=None):
    ids = failure_columns(columns_map)
    if selected is not None:
        ids = {key: column for key, column in ids.items() if key in selected}
    # all callers supply cells bound to the authorized dataset and visible rows.
    return cells.filter(
        column_id__in=ids,
        deleted=False,
        row__deleted=False,
        column__deleted=False,
        status=CellStatus.PASS.value,
    ).filter(Q(value__iexact="false") | Q(value__iexact="Failed"))


def apply_failed_filter(cells, rows, item, columns_map):
    selected = (
        item["filter_config"]["filter_value"]
        if item["column_id"] == "system:row_failed_in"
        else None
    )
    matching = failed_cells(cells, columns_map, selected)
    return rows.filter(id__in=matching.values("row_id"))


def failure_counts(cells, columns_map):
    matching = failed_cells(cells, columns_map)
    counts = {
        str(row["column_id"]): row["count"]
        for row in matching.order_by()
        .values("column_id")
        .annotate(count=Count("row_id", distinct=True))
    }
    return {
        "failed_rows": matching.order_by().values("row_id").distinct().count(),
        "failed_by_eval": [
            {"column_id": key, "name": column.name, "count": counts.get(key, 0)}
            for key, column in sorted(
                failure_columns(columns_map).items(),
                key=lambda item: (item[1].name, item[0]),
            )
        ],
    }
