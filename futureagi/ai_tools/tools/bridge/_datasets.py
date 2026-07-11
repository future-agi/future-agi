"""Bridge registration for DatasetView + dataset row access.

tracer/views/dataset.py is short enough to take the decorator inline, but
keeping it here for consistency with the legacy-file bridge pattern.
Standard CRUD names auto-generated: list_datasets, get_dataset,
create_dataset, update_dataset, delete_dataset.

F2 (corpus): ``get_dataset`` (CRUD retrieve) returns the dataset's metadata
but NOT its row count or rows — the model had no way to answer "how many
rows" and invented a nonexistent ``get_dataset_rows``. The dashboard dataset
table is served by ``GetDatasetTableView`` (GET
/model-hub/develops/<dataset_id>/get-dataset-table/), which returns
``metadata.total_rows`` plus a page of ``table`` rows. Bridge it as
``get_dataset_rows`` so the true, workspace-scoped row count and a page of
rows are reachable.
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.develop_dataset import GetDatasetTableView
from tracer.views.dataset import DatasetView

expose_to_mcp(category="datasets")(DatasetView)


def _dataset_rows_scope_label(params, data):
    """Surface the TRUE total row count up-front (F2).

    ``get-dataset-table`` paginates: ``table`` holds only one page, but
    ``metadata.total_rows`` is the full DB COUNT of non-deleted rows in the
    dataset. Without this label the model sees a short ``table`` list and may
    report the PAGE size as the row count. State the real total and the page
    window explicitly.
    """
    if not isinstance(data, dict):
        return None
    meta = data.get("metadata") or {}
    total = meta.get("total_rows")
    if total is None:
        return None
    table = data.get("table")
    shown = len(table) if isinstance(table, list) else None
    page = (params or {}).get("current_page_index", 0) or 0
    page_size = (params or {}).get("page_size", 10) or 10
    name = meta.get("dataset_name")
    name_phrase = f" '{name}'" if name else ""
    shown_phrase = (
        f" Showing {shown} row(s) on this page (page {page}, page_size "
        f"{page_size})."
        if shown is not None
        else ""
    )
    return (
        f"Scope: dataset{name_phrase} has {total} row(s) total "
        f"(non-deleted, workspace-scoped).{shown_phrase} "
        f"Report total_rows as the dataset's row count, not the page size."
    )


# get_dataset_rows -> GetDatasetTableView.get (bare APIView, GET).
# dataset_id is a URL path kwarg (pk_field + pk_kwarg). All filters/sort/
# search are optional; the common ask is just a count + a first page.
expose_to_mcp(
    category="datasets",
    tools={
        "get": {
            "name": "get_dataset_rows",
            "method": "GET",
            "detail": True,
            "pk_field": "dataset_id",
            "pk_kwarg": "dataset_id",
            "id_source": "list_datasets",
            "result_scope": _dataset_rows_scope_label,
            "description": (
                "Get a dataset's rows AND its true total row count (the same "
                "data the dataset table view shows). Returns "
                "`metadata.total_rows` (the full non-deleted row count) plus a "
                "page of rows in `table`, and the dataset's column config. Use "
                "this to answer 'how many rows does dataset X have' or to "
                "inspect actual row values. Page through with `page_size` / "
                "`current_page_index`."
            ),
            "query_params": {
                "page_size": {
                    "type": int,
                    "required": False,
                    "default": 10,
                    "description": (
                        "Rows per page (1-500, default 10). The total row "
                        "count is returned regardless of page size in "
                        "`metadata.total_rows`."
                    ),
                },
                "current_page_index": {
                    "type": int,
                    "required": False,
                    "default": 0,
                    "description": "0-indexed page number (default 0).",
                },
                "column_config_only": {
                    "type": bool,
                    "required": False,
                    "default": False,
                    "description": (
                        "If true, return only the column configuration (and "
                        "per-column eval averages) without the row values — "
                        "cheaper when you only need the schema."
                    ),
                },
                "filters": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional JSON-encoded filter list (the same shape the "
                        "dataset table UI sends); omit for no filtering. When "
                        "set, `total_rows` reflects the filtered count."
                    ),
                },
                "search": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional JSON-encoded search object "
                        "({\"key\": <text>, \"type\": [\"text\"]}); omit for "
                        "no search."
                    ),
                },
            },
        },
    },
)(GetDatasetTableView)
