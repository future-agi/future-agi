"""Independent CH25 canonical-session reference; SELECT-only, no application imports.

Contract (SessionListQueryBuilder's public windowed list, not session detail):
* Resolve FULL six-part physical winners before exact time/deletion/parent/value
  predicates. Both roots and child witnesses use the frozen [start, end) window.
* Resolve latest old->new remaps, folding all old aliases and the new ID onto
  the lexicographically smallest OLD UUID. Never truncate a consolidation group.
* Each scalar leaf has its own latest-live witness across the canonical session.
  is_null means no typed presence anywhere in that session, not a missing child.
* Session start and displayed metrics use ALL live window roots of a matching
  session, not only the spans/traces witnessing its filters.

No candidate IDs, application builders/compiler, rollups, dictionaries, PG,
HTTP, diagnostic connections, or population sampling are used here. The caller
owns the SELECT-only reader and diagnostic resource guards. Errors propagate;
an unfinished read is never converted into an empty reference.

The API requires explicit authorized project scope. Multi-project reads remain
unsupported until public cross-project collision/order semantics are qualified.
Native UUID vs UUID-string ordering is an explicit caller choice: current
default-cursor and filtered-classifier paths differ. This oracle does not hide
that difference. A canonical session has no single physical _version: the first
root witness below is versioned, but is NOT a whole-session version token.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from uuid import UUID

from observe_trace_id_reference import membership_having
from replay_observe_filters import ReplayError


CONTRACT = "canonical-session-windowed-latest-roots-v1"
NIL_UUID = "00000000-0000-0000-0000-000000000000"
SAFE_FINAL_SETTINGS = {
    "optimize_move_to_prewhere": 0,
    "optimize_move_to_prewhere_if_final": 0,
    "use_skip_indexes_if_final": 0,
    "enable_optimize_predicate_expression_to_final_subquery": 0,
    "query_plan_merge_expressions": 0,
}


def _uuid(value):
    try:
        parsed = str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ReplayError("SESSION_REFERENCE_INVALID_UUID") from None
    if parsed == NIL_UUID:
        raise ReplayError("SESSION_REFERENCE_INVALID_UUID")
    return parsed


def _utc(value):
    try:
        value = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ReplayError("SESSION_REFERENCE_EXPLICIT_TIMEZONE_REQUIRED") from None


def _us(value):
    delta = _utc(value) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _validated_request(project_ids, authorized_project_ids, start, end, filters,
                       page_size, order_mode):
    if (not isinstance(project_ids, (list, tuple)) or not project_ids
            or not isinstance(authorized_project_ids, (list, tuple))
            or not authorized_project_ids):
        raise ReplayError("SESSION_REFERENCE_EXPLICIT_PROJECT_SCOPE_REQUIRED")
    projects = tuple(_uuid(value) for value in project_ids)
    authorized = {_uuid(value) for value in authorized_project_ids}
    if len(set(projects)) != len(projects) or not set(projects) <= authorized:
        raise ReplayError("SESSION_REFERENCE_PROJECT_NOT_AUTHORIZED")
    if len(projects) != 1:
        raise ReplayError("SESSION_REFERENCE_WORKSPACE_NOT_QUALIFIED")
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ReplayError("SESSION_REFERENCE_INVALID_PAGE_SIZE")
    if order_mode not in ("uuid", "uuid_string"):
        raise ReplayError("SESSION_REFERENCE_EXPLICIT_ORDER_REQUIRED")
    start, end = _utc(start), _utc(end)
    if start >= end:
        raise ReplayError("SESSION_REFERENCE_INVALID_WINDOW")
    if not isinstance(filters, (list, tuple)):
        raise ReplayError("SESSION_REFERENCE_FLAT_AND_REQUIRED")
    scalar, date_count = [], 0
    for item in filters:
        if (not isinstance(item, dict)
                or set(item) - {"column_id", "filter_config"}
                or not isinstance(item.get("column_id"), str)
                or not item["column_id"]
                or not isinstance(item.get("filter_config"), dict)):
            raise ReplayError("SESSION_REFERENCE_FLAT_AND_REQUIRED")
        cfg = item["filter_config"]
        if set(cfg) - {"col_type", "filter_type", "filter_op", "filter_value",
                       "attribute_value_types", "attributeValueTypes"}:
            raise ReplayError("SESSION_REFERENCE_UNSUPPORTED_FILTER_CONFIG")
        if (cfg.get("col_type") == "SYSTEM_METRIC"
                and item["column_id"] in ("created_at", "start_time")):
            values = cfg.get("filter_value")
            if (cfg.get("filter_type") != "datetime"
                    or cfg.get("filter_op") != "between"
                    or not isinstance(values, (list, tuple)) or len(values) != 2
                    or (_utc(values[0]), _utc(values[1])) != (start, end)):
                raise ReplayError("SESSION_REFERENCE_WINDOW_FILTER_MISMATCH")
            date_count += 1
            if date_count > 1:
                raise ReplayError("SESSION_REFERENCE_MULTIPLE_DATE_FILTERS_UNSUPPORTED")
        elif cfg.get("col_type") == "SPAN_ATTRIBUTE":
            if cfg.get("filter_type") not in ("text", "string", "number", "boolean"):
                raise ReplayError("SESSION_REFERENCE_UNSUPPORTED_TYPE")
            scalar.append(item)
        else:
            # Native user IDs need independently verified dimension/remap
            # resolution; annotations/evals need a separate relational oracle.
            raise ReplayError("SESSION_REFERENCE_UNSUPPORTED_FILTER")
    if len(scalar) > 10:
        raise ReplayError("SESSION_REFERENCE_TOO_MANY_PREDICATES")
    having, values = membership_having(scalar) if scalar else ("1", {})
    return projects, start, end, having, values


def _positive_witness_filter(filters):
    return next((item for item in filters
        if item["filter_config"].get("col_type") == "SPAN_ATTRIBUTE"
        and item["filter_config"].get("filter_type") in {"number", "boolean"}
        and item["filter_config"].get("filter_op") in {
            "equals", "in", "between", "greater_than", "greater_than_or_equal",
            "less_than", "less_than_or_equal", "is_not_null",
        }), None)


def build_session_reference_query(*, project_ids, authorized_project_ids, start,
                                  end, filters=(), page_size=25, order_mode):
    """Choose 2 pages plus lookahead AFTER complete independent membership.

    No independent seed LIMIT, exact-start prefilter, live-only raw prefilter,
    or candidate/result IDs may restrict either latest physical population.
    Metric aggregates coexist in the statement but never choose its membership.
    """
    projects, start, end, having, values = _validated_request(
        project_ids, authorized_project_ids, start, end, filters, page_size, order_mode
    )
    order = "toUUID(session_id)" if order_mode == "uuid" else "session_id"
    params = {"reference_project_ids": projects, "ref_start_us": _us(start),
              "ref_end_us": _us(end), "ref_visible_limit": page_size * 2 + 1,
              **values}
    hour_scope = """project_id IN %(reference_project_ids)s
          AND toStartOfHour(start_time) >= toStartOfHour(fromUnixTimestamp64Micro(%(ref_start_us)s, 'UTC'))
          AND toStartOfHour(start_time) <= toStartOfHour(fromUnixTimestamp64Micro(%(ref_end_us)s - 1, 'UTC'))"""
    canonical = f"""if(ifNull(m.mapped_session_id, '') IN ('', '{NIL_UUID}'),
                      toString(s.trace_session_id), m.mapped_session_id)"""
    physical_scope = witness_ctes = canonical_scope = ""
    anchor = _positive_witness_filter(filters)
    if anchor is not None:
        predicate, bindings = membership_having([anchor], scalar_span=True)
        for name, value in bindings.items():
            predicate = predicate.replace(f"%({name})s", f"%(narrow_{name})s")
            params[f"narrow_{name}"] = value
        prefix = "project_id, observation_type, service_name, toStartOfHour(start_time), trace_id"
        # A live qualifying winner necessarily exists among these raw rows.
        # Stale/deleted/out-of-window versions only add groups. Expand every
        # alias and immutable prefix; replay complete winners before membership.
        witness_ctes = f""", raw_positive_witnesses AS (
        SELECT DISTINCT trace_session_id
        FROM spans
        PREWHERE {hour_scope}
        WHERE isNotNull(trace_session_id)
          AND trace_session_id != toUUID('{NIL_UUID}')
          AND ({predicate})
    ), (
        SELECT groupUniqArray({canonical})
        FROM raw_positive_witnesses AS s
        LEFT JOIN survivor_map AS m ON toString(s.trace_session_id) = m.any_id
    ) AS reference_witness_sessions, (
        SELECT arrayDistinct(arrayConcat(groupArray(any_id), reference_witness_sessions))
        FROM survivor_map
        WHERE mapped_session_id IN (SELECT arrayJoin(reference_witness_sessions))
    ) AS reference_session_aliases, (
        SELECT groupUniqArray(tuple({prefix}))
        FROM spans
        PREWHERE {hour_scope}
        WHERE trace_session_id IN (SELECT toUUIDOrNull(arrayJoin(reference_session_aliases)))
    ) AS reference_physical_prefixes"""
        physical_scope = f"\n          AND tuple({prefix}) IN (SELECT arrayJoin(reference_physical_prefixes))"
        canonical_scope = "\n        WHERE canonical_id IN (SELECT arrayJoin(reference_witness_sessions))"
    sql = f"""
    WITH physical_winners AS (
        SELECT project_id, observation_type, service_name, trace_id, id,
               start_time, end_time, parent_span_id, trace_session_id,
               is_deleted, _version, cost, total_tokens,
               attrs_string, attrs_number, attrs_bool
        FROM spans FINAL
        PREWHERE {hour_scope}{physical_scope}
    ), live_window_spans AS (
        SELECT * FROM physical_winners
        WHERE is_deleted = 0
          AND start_time >= fromUnixTimestamp64Micro(%(ref_start_us)s, 'UTC')
          AND start_time < fromUnixTimestamp64Micro(%(ref_end_us)s, 'UTC')
          AND isNotNull(trace_session_id)
          AND trace_session_id != toUUID('{NIL_UUID}')
    ), latest_remaps AS (
        SELECT old_id, new_id, version FROM trace_session_id_remap FINAL
    ), remap_groups AS (
        SELECT new_id, min(toString(old_id)) AS survivor_id,
               groupArray(toString(old_id)) AS old_ids
        FROM latest_remaps
        GROUP BY new_id
    ), aliases AS (
        SELECT arrayJoin(arrayDistinct(arrayConcat(old_ids, [toString(new_id)]))) AS any_id,
               survivor_id FROM remap_groups
    ), survivor_map AS (
        SELECT any_id, min(survivor_id) AS mapped_session_id
        FROM aliases GROUP BY any_id
    ){witness_ctes}, canonical_spans AS (
        SELECT s.*, {canonical} AS canonical_id,
               ifNull(parent_span_id, '') = '' AS is_root
        FROM live_window_spans AS s
        LEFT JOIN survivor_map AS m ON toString(s.trace_session_id) = m.any_id{canonical_scope}
    ), session_population AS (
        SELECT toString(project_id) AS project_id, canonical_id AS session_id,
               minIf(start_time, is_root) AS session_start,
               maxIf(end_time, is_root) AS session_end,
               countIf(is_root) AS root_count,
               uniqExactIf(trace_id, is_root) AS traces_count,
               sumIf(cost, is_root) AS total_cost,
               sumIf(total_tokens, is_root) AS total_tokens,
               argMinIf(
                   tuple(toString(project_id), observation_type, service_name,
                         toUnixTimestamp64Micro(toDateTime64(toStartOfHour(start_time), 6, 'UTC')),
                         trace_id, id, toUnixTimestamp64Micro(start_time), _version),
                   tuple(start_time, trace_id, id, observation_type, service_name),
                   is_root) AS first_root
        FROM canonical_spans
        GROUP BY project_id, canonical_id
        HAVING root_count > 0 AND ({having})
    )
    SELECT project_id, session_id, toUnixTimestamp64Micro(session_start) AS session_start_us,
           toUnixTimestamp64Micro(session_end) AS session_end_us,
           dateDiff('second', session_start, session_end, 'UTC') AS duration,
           root_count, traces_count, total_cost, total_tokens, first_root,
           count() OVER () AS total_sessions
    FROM session_population
    ORDER BY session_start DESC, {order} DESC
    LIMIT %(ref_visible_limit)s
    """
    return sql, params


def _order_key(row, mode):
    session_id = _uuid(row["session_id"])
    uuid_int = UUID(session_id).int
    # CH25 UUID compares low UInt64, then high UInt64; not UUID text ordering.
    suffix = ((uuid_int & ((1 << 64) - 1), uuid_int >> 64)
              if mode == "uuid" else (session_id,))
    return (row["session_start_us"], *suffix)


def reference_session_pages(reader, *, project_ids, authorized_project_ids, start,
                            end, filters=(), page_size=25, order_mode):
    """Return two independently chosen visible pages and separate root metrics.

    The first_root witness carries an actual physical version; session_version
    is explicitly absent. Metrics are raw aggregate values (not HTTP/default
    formatting), and are not a graph/full-population metric qualification.
    """
    sql, params = build_session_reference_query(
        project_ids=project_ids, authorized_project_ids=authorized_project_ids,
        start=start, end=end, filters=filters, page_size=page_size, order_mode=order_mode
    )
    result = reader.execute_ch_query(sql, params, settings=dict(SAFE_FINAL_SETTINGS))
    rows = result.data
    try:
        if not isinstance(rows, list):
            raise ValueError
        totals = {row["total_sessions"] for row in rows}
        total = next(iter(totals)) if totals else 0
        if (len(totals) > 1 or type(total) is not int or total < 0
                or len(rows) != min(total, page_size * 2 + 1)):
            raise ValueError
        seen, previous = set(), None
        for row in rows:
            key = (_uuid(row["project_id"]), _uuid(row["session_id"]))
            if key[0] not in params["reference_project_ids"] or key in seen:
                raise ValueError
            seen.add(key)
            if (type(row["session_start_us"]) is not int
                    or not params["ref_start_us"] <= row["session_start_us"] < params["ref_end_us"]):
                raise ValueError
            order = _order_key(row, order_mode)
            if previous is not None and order >= previous:
                raise ValueError
            previous = order
            first = row["first_root"]
            if (not isinstance(first, (list, tuple)) or len(first) != 8
                    or _uuid(first[0]) != key[0]
                    or not all(isinstance(first[i], str) for i in (1, 2, 4, 5))
                    or not first[4] or not first[5]
                    or type(first[3]) is not int
                    or first[3] != row["session_start_us"] // 3_600_000_000 * 3_600_000_000
                    or first[6] != row["session_start_us"]
                    or type(first[7]) is not int or not 0 <= first[7] < 2**64):
                raise ValueError
            if (type(row["root_count"]) is not int or row["root_count"] < 1
                    or type(row["traces_count"]) is not int
                    or not 1 <= row["traces_count"] <= row["root_count"]
                    or type(row["total_tokens"]) is not int
                    or type(row["total_cost"]) not in (float, int)
                    or not math.isfinite(row["total_cost"])
                    or (row["session_end_us"] is not None and type(row["session_end_us"]) is not int)
                    or (row["duration"] is not None and type(row["duration"]) is not int)):
                raise ValueError
    except (KeyError, TypeError, ValueError, ReplayError):
        raise ReplayError("SESSION_REFERENCE_RESULT_INVALID_OR_INCOMPLETE") from None
    visible = rows[:page_size * 2]
    identities = [
        {key: row[key] for key in ("project_id", "session_id", "session_start_us", "first_root")}
        for row in visible
    ]
    return {
        "contract": CONTRACT,
        "order_mode": order_mode,
        "pages": [identities[:page_size], identities[page_size:]],
        "metrics": [
            {key: row[key] for key in ("project_id", "session_id", "session_start_us",
                                      "session_end_us", "duration", "root_count",
                                      "traces_count", "total_cost", "total_tokens")}
            for row in visible
        ],
        "total_sessions": total,
        "has_more_after_two_pages": total > page_size * 2,
        "population_exhausted_after_two_pages": total <= page_size * 2,
        "session_version": None,
        "first_root_version_is_session_version": False,
        "same_transaction_snapshot": False,
        "http_e2e": False,
        "graph_metrics_verified": False,
    }
