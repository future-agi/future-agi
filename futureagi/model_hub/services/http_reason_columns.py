"""Read-only resolution of the one recognized historical HTTP reason twin."""

from model_hub.models.develop_dataset import Cell, Column
from tfc.routers import primary_required


def _empty(value):
    # JSON false/0 and serialized containers are payload, not empty metadata.
    return value is None or value == "" or value == [] or value == {}


@primary_required
def resolve_http_reason_twins(config, *, canonical_name, historical_name):
    """Return the canonical member of a safe pair, or leave singletons alone.

    Call inside the caller's existing transaction/write fence, before its
    get_or_create fallback. Do not filter by label/type: extra identity matches
    must refuse, not disappear. This function never changes columns or cells.
    """
    columns = list(
        Column.objects.filter(
            dataset=config["dataset"],
            source=config["source"],
            source_id=config["source_id"],
            deleted=False,
        )
    )
    if len(columns) <= 1:
        return None
    by_name = {column.name: column for column in columns}
    if (
        len(columns) == 2
        and canonical_name != historical_name
        and set(by_name) == {canonical_name, historical_name}
        and all(column.data_type == "text" for column in columns)
    ):
        twin = by_name[historical_name]
        cells = Cell.objects.filter(column=twin, deleted=False).values_list(
            "value", "feedback_info", "value_infos", "column_metadata"
        )
        if all(
            value in (None, "") and all(_empty(payload) for payload in metadata)
            for value, *metadata in cells.iterator()
        ):
            return by_name[canonical_name]
    conflicts = ", ".join(f"{column.name!r} ({column.pk})" for column in columns)
    raise ValueError(
        f"HTTP reason columns conflict: {conflicts}. Expected canonical "
        f"{canonical_name!r} and exactly one empty historical {historical_name!r}, "
        "both text. No columns or cells were changed. Export these columns and "
        "their cells, then ask your workspace administrator to reconcile this "
        "identity while preserving all saved data before you retry."
    )
