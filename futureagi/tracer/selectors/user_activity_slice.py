"""Base Users admission on a qualified current-activity pin.

No metric predicates are moved after LIMIT. Unsupported shapes, missing flags,
coverage or freshness retain the native exact builder, including count routes.
BL01-Q must supply the shared pin; no HTTP request can assert its own coverage.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from tracer.services.clickhouse.query_builders.base import _unix_microseconds
from tracer.services.clickhouse.v2.id_remap_sql import survivor_map_subquery
from tracer.services.derived_metrics.bounded_reads import KeyRelation
from tracer.services.derived_metrics.user_list_current_clickhouse import activity_rows

if TYPE_CHECKING:
    from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
    from tracer.services.derived_metrics.base_list_current import CurrentFactsRead

ORDER = "ORDER BY last_active DESC NULLS LAST, end_user_id DESC"


def activity_read_available(
    builder: UserListQueryBuilder, read: CurrentFactsRead | None
) -> bool:
    """Only one authorized project, finite base pages, and exact default order."""
    if (
        read is None
        or not read.active
        or str(read.org_id) != builder.organization_id
        or builder.empty_scope
        or builder.end_user_id
        or builder.candidate_end_user_ids
        or builder.max_rows is not None
        or any(not builder._is_date_filter(item) for item in builder.filters)
        or builder._order_by() != ORDER
    ):
        return False
    projects: Sequence[str | None] = (
        builder.project_ids if builder.project_ids is not None else [builder.project_id]
    )
    if len(projects) != 1 or not projects[0]:
        return False
    if not read.permits(projects[0]) or not read.permits_user_activity(projects[0]):
        return False
    # Dependency on FLAG0's exact helper, not a second interpretation of rollout.
    from tracer.services.derived_metrics.feature_flag import metrics_enabled

    return metrics_enabled()


def build_activity_page(
    builder: UserListQueryBuilder,
    read: CurrentFactsRead,
    *,
    limit: int,
    offset: int = 0,
    cursor_mode: bool = False,
    cursor_before: tuple[datetime | None, str] | None = None,
    window: tuple[datetime, datetime] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Admit by activity/curated identity, then aggregate usage for those IDs only.

    The keyset is identical to the native exact reader, including NULL activity
    and native UUID ordering. A cursor caller requests its existing sentinel;
    this function does not invent a lower bound or change count proof semantics.
    Numbered routes count the full admitted population before LIMIT.
    """
    if not activity_read_available(builder, read):
        raise ValueError("user activity is not qualified for this request")
    if type(limit) is not int or limit <= 0 or type(offset) is not int or offset < 0:
        raise ValueError("user activity requires finite pagination")
    start, end = window or builder.parse_time_range(builder.filters)
    # parse_time_range always fills missing bounds with its finite defaults.
    assert start is not None and end is not None
    params = {
        "org_id": builder.organization_id,
        "activity_start_us": _unix_microseconds(start),
        "activity_end_us": _unix_microseconds(end),
        "activity_limit": limit,
        "activity_offset": offset,
    }
    if builder.project_ids is not None:
        params["project_ids"] = tuple(builder.project_ids)
    else:
        params["project_id"] = builder.project_id
    if builder.search:
        params["search"] = builder.search
    current, bound = activity_rows(read)
    params.update(bound)
    window_sql = """is_deleted = 0 AND canonical_user_id != ''
        AND unit_start >= fromUnixTimestamp64Micro(%(activity_start_us)s, 'UTC')
        AND unit_start < fromUnixTimestamp64Micro(%(activity_end_us)s, 'UTC')"""
    keyset = ""
    if cursor_mode and cursor_before is not None:
        before_time, before_id = cursor_before
        params["activity_before_id"] = str(before_id)
        id_before = "end_user_id < toUUID(%(activity_before_id)s)"
        if before_time is None:
            keyset = f"WHERE last_active IS NULL AND {id_before}"
        else:
            params["activity_before_us"] = _unix_microseconds(before_time)
            at = "fromUnixTimestamp64Micro(%(activity_before_us)s, 'UTC')"
            keyset = (
                f"WHERE last_active IS NULL OR last_active < {at} "
                f"OR (last_active = {at} AND {id_before})"
            )
    count = "0 AS total_count" if cursor_mode else "count() OVER() AS total_count"
    ctes = f"""eu_survivor_map AS ({survivor_map_subquery("end_user_id_remap")}),
        {builder._curated_user_ctes()},
        activity_by_user AS (
            SELECT toUUID(canonical_user_id) AS end_user_id,
                max(end_time) AS last_active
            FROM ({current}) WHERE {window_sql}
            GROUP BY canonical_user_id
        ),
        admitted_users AS (
            SELECT eu.user_id AS user_id, eu.first_seen AS activated_at,
                activity.last_active AS last_active, eu.project_id AS project_id,
                eu.user_id_type AS user_id_type, eu.user_id_hash AS user_id_hash,
                eu.end_user_id AS end_user_id, {count}
            FROM searched_end_users eu
            INNER JOIN activity_by_user activity ON activity.end_user_id = eu.end_user_id
            {keyset}
            {ORDER}
            LIMIT %(activity_limit)s OFFSET %(activity_offset)s
        )"""
    # Explicit positive membership is safe only because moves retract the old
    # canonical-user logical key. It bounds aggregation before usage is read.
    page_rows, page_params = activity_rows(
        read,
        key_relation=KeyRelation(
            ("canonical_user_id",),
            "SELECT toString(end_user_id) FROM admitted_users",
            {},
        ),
        param_prefix="user_activity_page",
    )
    params.update(page_params)
    query = f"""WITH {ctes}, page_usage AS (
        SELECT toUUID(canonical_user_id) AS end_user_id,
            sum(cost) AS total_cost, sum(toInt64(total_tokens)) AS total_tokens,
            sum(toInt64(prompt_tokens)) AS input_tokens,
            sum(toInt64(completion_tokens)) AS output_tokens,
            uniqExact(trace_id) AS num_traces
        FROM ({page_rows}) WHERE {window_sql}
        GROUP BY canonical_user_id
    )
    SELECT admitted.user_id AS user_id, usage.total_cost AS total_cost,
        usage.total_tokens AS total_tokens, usage.input_tokens AS input_tokens,
        usage.output_tokens AS output_tokens, usage.num_traces AS num_traces,
        toUInt64(0) AS num_sessions, admitted.activated_at AS activated_at,
        admitted.last_active AS last_active, admitted.project_id AS project_id,
        admitted.user_id_type AS user_id_type, admitted.user_id_hash AS user_id_hash,
        admitted.end_user_id AS end_user_id, admitted.total_count AS total_count
    FROM admitted_users admitted
    INNER JOIN page_usage usage ON usage.end_user_id = admitted.end_user_id
    {ORDER}"""
    # num_sessions is deliberately not embedded. The manager's finite native
    # remap-aware metric read remains the exact route for requested session counts.
    return query, params
