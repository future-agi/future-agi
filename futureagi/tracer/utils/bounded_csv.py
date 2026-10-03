import csv
import io
import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from django.http import HttpResponse

_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
# Keep synchronous trace CSV work to one already-qualified list hydration
# chunk. Span and session exports have smaller cursor-bounded caps because
# their filtered selectors may need several classify reads before hydration.
# These are bounded-page contracts, not all-row exports; callers disclose the
# remaining population with the terminal truncation row below.
BOUNDED_EXPORT_PAGE_SIZE = 100
BOUNDED_SPAN_EXPORT_PAGE_SIZE = 20
BOUNDED_SESSION_EXPORT_PAGE_SIZE = 20


def _format_csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=isinstance(value, dict),
        )
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_TRIGGERS):
        return "'" + value
    return value


def bounded_page_csv_response(
    *,
    rows: Iterable[Mapping[str, Any]] | None,
    filename: str,
    metadata: Mapping[str, Any] | None = None,
    fieldnames: Iterable[str] | None = None,
) -> HttpResponse:
    """Serialize one finite list page and disclose any incomplete export."""

    page_rows = list(rows or ())
    derived_fieldnames = (key for row in page_rows for key in row)
    fieldnames = list(dict.fromkeys([*(fieldnames or ()), *derived_fieldnames]))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([_format_csv_cell(field) for field in fieldnames])
    for row in page_rows:
        writer.writerow([_format_csv_cell(row.get(field)) for field in fieldnames])

    read_metadata = metadata or {}
    truncated = bool(
        read_metadata.get("has_more")
        or read_metadata.get("total_rows_is_lower_bound")
        or read_metadata.get("query_complete") is False
    )
    inexact_candidates = bool(
        read_metadata.get("query_exact") is False
        or read_metadata.get("ordering_exact") is False
    )
    if truncated:
        marker = (
            f"# export truncated after {len(page_rows)} rows; "
            "refine filters to export a complete bounded page"
        )
        if inexact_candidates:
            marker += "; candidate membership or ordering is inexact"
        writer.writerow([marker])
    elif inexact_candidates:
        writer.writerow(
            [
                "# export candidate membership or ordering is inexact; "
                "results are not an exact ordered population"
            ]
        )

    response = HttpResponse(buffer.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
