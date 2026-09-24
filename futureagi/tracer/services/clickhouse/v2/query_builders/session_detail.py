"""Whole-session detail reads over complete CH25 physical winners.

This is not the windowed session-list contract: detail metrics include every
live member span, including children, across the expanded canonical ID group.
Raw membership only discovers immutable keys. A replaced session ID, a cleared
ID, or a tombstone must still participate in FINAL before membership is tested.
"""

_LIVE_SESSION_SPANS = """
    SELECT * FROM spans FINAL
    PREWHERE project_id = %(project_id)s
      AND (observation_type, service_name, toStartOfHour(start_time), trace_id, id) IN (
          SELECT DISTINCT observation_type, service_name,
                          toStartOfHour(start_time), trace_id, id
          FROM spans
          PREWHERE project_id = %(project_id)s
            AND trace_session_id IN %(session_group_ids)s
      )
    WHERE trace_session_id IN %(session_group_ids)s AND is_deleted = 0
"""

# Do not let a profile or optimizer move mutable session membership ahead of
# replacement, or prune the newest version with a mutable-column skip index.
_FINAL_SETTINGS = """
    SETTINGS optimize_move_to_prewhere = 0,
             optimize_move_to_prewhere_if_final = 0,
             use_skip_indexes_if_final = 0,
             enable_optimize_predicate_expression_to_final_subquery = 0,
             query_plan_merge_expressions = 0
"""


def _params(project_id, session_group_ids):
    if not project_id or not isinstance(session_group_ids, (tuple, list)):
        raise ValueError("Session detail requires explicit project and group scope")
    if not session_group_ids or any(not value for value in session_group_ids):
        raise ValueError("Session detail requires a nonempty session group")
    return {
        "project_id": str(project_id),
        "session_group_ids": tuple(dict.fromkeys(map(str, session_group_ids))),
    }


def build_session_detail_aggregate_query(project_id, session_group_ids):
    return (
        f"""
        SELECT
            min(start_time) AS session_start,
            max(end_time) AS session_end,
            round(sum(cost), 6) AS total_cost,
            sum(total_tokens) AS total_tokens,
            count(DISTINCT trace_id) AS total_traces,
            toString(argMaxIf(
                end_user_id,
                tuple(start_time, trace_id, id, observation_type, service_name),
                isNotNull(end_user_id)
                    AND end_user_id != toUUID('00000000-0000-0000-0000-000000000000')
            )) AS end_user_id
        FROM ({_LIVE_SESSION_SPANS}) AS live_session_spans
        {_FINAL_SETTINGS}
    """,
        _params(project_id, session_group_ids),
    )


def build_session_detail_traces_query(project_id, session_group_ids, *, limit, offset):
    if type(limit) is not int or limit <= 0 or type(offset) is not int or offset < 0:
        raise ValueError(
            "Session detail pagination must be positive limit/nonnegative offset"
        )
    return (
        f"""
        SELECT
            trace_id, trace_messages.1 AS input, trace_messages.2 AS output,
            root_latency_ms, total_cost, trace_min_start_time, total_tokens,
            input_tokens, output_tokens
        FROM (
        SELECT
            toString(trace_id) AS trace_id,
            argMin(
                tuple(input, output),
                tuple(if(parent_span_id IS NULL OR parent_span_id = '', 0, 1),
                      start_time, id, observation_type, service_name)
            ) AS trace_messages,
            min(CASE WHEN parent_span_id IS NULL OR parent_span_id = ''
                     THEN latency_ms ELSE NULL END) AS root_latency_ms,
            round(sum(cost), 6) AS total_cost,
            min(start_time) AS trace_min_start_time,
            sum(total_tokens) AS total_tokens,
            sum(prompt_tokens) AS input_tokens,
            sum(completion_tokens) AS output_tokens
        FROM ({_LIVE_SESSION_SPANS}) AS live_session_spans
        GROUP BY trace_id
        ) AS session_traces
        ORDER BY trace_min_start_time ASC, trace_id ASC
        LIMIT %(limit)s OFFSET %(offset)s
        {_FINAL_SETTINGS}
    """,
        {
            **_params(project_id, session_group_ids),
            "limit": limit,
            "offset": offset,
        },
    )
