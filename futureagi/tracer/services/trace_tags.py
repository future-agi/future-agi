"""Tag updates for collector traces whose authoritative row lives in CH25."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from uuid import UUID

from tracer.services.clickhouse.v2 import trace_writer
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService


class TraceTagsNotFound(LookupError):
    """No unambiguous live trace exists in the caller's project scope."""


def _read_trace_rows(analytics, *, trace_id, project_ids, projection):
    project_ids = tuple(dict.fromkeys(str(value) for value in project_ids))
    if not project_ids:
        return []
    if len(project_ids) > 4096:
        raise ValueError("Trace tag project scope is too large")
    return analytics.execute_ch_query(
        f"""
        SELECT {projection}
        FROM traces
        PREWHERE project_id IN %(tag_project_ids)s
          AND id = %(tag_trace_id)s
        GROUP BY project_id, id
        HAVING argMax(is_deleted, _version) = 0
        LIMIT 2
        """,
        {
            "tag_project_ids": project_ids,
            "tag_trace_id": str(trace_id),
        },
        timeout_ms=5000,
        settings={"max_threads": 1},
    ).data


def update_collector_trace_tags(*, trace_id, project_ids, tags):
    """Replace tags without fabricating PG metadata or rewriting span content.

    Use a tuple so nullable columns come from the *same* latest row, including
    NULL values. Keep created_at unchanged: it is the CH partition key. The
    existing list's argMax(tags, _version) sees this write without dictionary lag.
    """
    columns = trace_writer._TRACE_COLUMNS
    rows = _read_trace_rows(
        V2AnalyticsQueryService(),
        trace_id=str(UUID(str(trace_id))),
        project_ids=project_ids,
        projection=f"argMax(tuple({', '.join(columns)}), _version) AS trace_row",
    )
    # A producer-controlled trace id need not be unique across projects. Never
    # choose an arbitrary project; the caller can pin it via project_id instead.
    if len(rows) != 1:
        raise TraceTagsNotFound
    row = dict(zip(columns, rows[0]["trace_row"], strict=True))
    # CH consumers expect tag names, not PG's rich objects. The UI derives
    # colors from names when reading legacy/string tags.
    tag_names = [tag["name"] if isinstance(tag, dict) else tag for tag in tags]
    row["tags"] = json.dumps(tag_names, ensure_ascii=False)
    row["updated_at"] = datetime.now(UTC)
    row["_version"] = max(time.time_ns(), int(row["_version"]) + 1)
    # This is the authoritative edit, not best-effort PG mirroring. Do not
    # acknowledge success until the existing CH write client accepts the row.
    try:
        trace_writer._get_client().insert(
            "traces", [[row[column] for column in columns]], column_names=list(columns)
        )
    except Exception:
        trace_writer._reset_client()
        raise
    return {"id": str(row["id"]), "tags": tag_names}


def read_collector_trace_tags(analytics, *, trace_id, project_id):
    """Use the list's trace-level tag source; None means no live trace row.

    An empty list is an explicit clear and must not fall back to root-span tags.
    The caller has already proven this project's trace identity via scoped spans.
    """
    rows = _read_trace_rows(
        analytics,
        trace_id=trace_id,
        project_ids=[project_id],
        projection="argMax(tags, _version) AS tags",
    )
    if not rows:
        return None
    return json.loads(rows[0]["tags"] or "[]")
