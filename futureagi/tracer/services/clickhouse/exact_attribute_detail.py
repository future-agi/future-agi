"""One-statement exact latest-state span attribute detail aggregation.

The spans table is direct-write ReplacingMergeTree data.  Mutable predicates
(deletion and key presence) must therefore run *after* argMax has selected the
latest version of every immutable physical span identity.  In particular, a
later key removal or tombstone must win over an older key-bearing row.

``EXACT_ATTRIBUTE_DETAIL_SQL_LEVEL`` selects the statement; every level is one
statement and one snapshot.  Level 0 is the original statement with its
settings and its 30 s deadline.  Level 1 seeds the key's identities as an IN
set and replays their versions filtered by immutable identity columns only.
An executor whose client does not show that query settings reach the server
(the server-enforced read-only lane, test doubles) always runs level 0.

This reader is intended for the existing exact-aggregation background worker;
HTTP requests serve/poll the last atomically published snapshot and never wait
for a full tenant scan.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any, NamedTuple

from django.conf import settings as django_settings

from tracer.services.clickhouse.attribute_reads import (
    V2AttributeQueryExecutor,
    validate_attribute_key,
)

EXACT_ATTRIBUTE_DETAIL_HORIZON_DAYS = 365
EXACT_ATTRIBUTE_DETAIL_TOP_VALUES = 100
EXACT_ATTRIBUTE_DETAIL_QUERY_TIMEOUT_MS = 30_000
_MIB = 1024 * 1024
EXACT_ATTRIBUTE_DETAIL_READ_SETTINGS: dict[str, Any] = {
    "max_threads": 1,
    "max_block_size": 512,
    "preferred_block_size_bytes": 4 * _MIB,
    "preferred_max_column_in_block_size_bytes": 4 * _MIB,
    "optimize_aggregation_in_order": 1,
    "max_bytes_before_external_group_by": 32 * _MIB,
    "max_bytes_before_external_sort": 32 * _MIB,
    "optimize_use_projections": 0,
    "allow_experimental_projection_optimization": 0,
    "max_bytes_to_read": 36 * 1024 * _MIB,
    "max_memory_usage": 36 * 1024 * _MIB,
    "read_overflow_mode": "throw",
    "max_result_rows": 1_001,
    "max_result_bytes": 64 * _MIB,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}

# Levels 1-2: the original settings with the in-order seed read off, the IN
# set's limits pinned so that no profile can truncate it, and a finite memory
# guard on the set (a read over it fails and publishes nothing).
EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_READ_SETTINGS: dict[str, Any] = {
    **EXACT_ATTRIBUTE_DETAIL_READ_SETTINGS,
    "optimize_aggregation_in_order": 0,
    "use_index_for_in_with_subqueries_max_values": 100_000,
    "set_overflow_mode": "throw",
    "max_rows_in_set": 0,
    "max_bytes_in_set": 512 * _MIB,
    "group_by_two_level_threshold_bytes": 128 * _MIB,
    "group_by_two_level_threshold": 32_768,
}

_TYPE_PRIORITY: dict[str, int] = {
    "string": 0,
    "number": 1,
    "boolean": 2,
    "array": 3,
    "map": 4,
    "json": 5,
}


EXACT_ATTRIBUTE_DETAIL_SQL = r"""
WITH
candidate_identities AS
(
    /*
     * The key predicate is intentionally applied only to the identity seed.
     * Every version of those identities is replayed below, so a later key
     * removal or tombstone still wins.  This avoids deserialising every wide
     * attribute Map in the project merely to discover that the requested key
     * was never present on that span.
     */
    SELECT
        project_id,
        trace_id,
        id,
        start_time
    FROM spans AS candidate_source
    PREWHERE candidate_source.project_id = toUUID(%(project_id)s)
      AND candidate_source.start_time >= %(window_start)s
      AND candidate_source.start_time < %(window_end)s
    WHERE
        (
            indexHint(has(mapKeys(candidate_source.attrs_string), %(attribute_key)s))
            AND mapContains(candidate_source.attrs_string, %(attribute_key)s)
        )
        OR (
            indexHint(has(mapKeys(candidate_source.attrs_number), %(attribute_key)s))
            AND mapContains(candidate_source.attrs_number, %(attribute_key)s)
        )
        OR (
            indexHint(has(mapKeys(candidate_source.attrs_bool), %(attribute_key)s))
            AND mapContains(candidate_source.attrs_bool, %(attribute_key)s)
        )
        OR (
            candidate_source.attributes_extra NOT IN ('', '{}', 'null')
            AND JSONHas(candidate_source.attributes_extra, %(attribute_key)s)
        )
    GROUP BY project_id, trace_id, id, start_time
),
latest_spans AS
(
    SELECT
        attribute_source.project_id AS project_id,
        attribute_source.trace_id AS trace_id,
        attribute_source.id AS id,
        attribute_source.start_time AS start_time,
        argMax(
            tuple(
                attribute_source.is_deleted,
                mapContains(attribute_source.attrs_string, %(attribute_key)s),
                attribute_source.attrs_string[%(attribute_key)s],
                mapContains(attribute_source.attrs_number, %(attribute_key)s),
                attribute_source.attrs_number[%(attribute_key)s],
                mapContains(attribute_source.attrs_bool, %(attribute_key)s),
                attribute_source.attrs_bool[%(attribute_key)s],
                JSONHas(attribute_source.attributes_extra, %(attribute_key)s),
                JSONExtractRaw(
                    attribute_source.attributes_extra,
                    %(attribute_key)s
                )
            ),
            attribute_source._version
        ) AS latest_state
    FROM spans AS attribute_source
    INNER JOIN candidate_identities AS candidate
      ON candidate.project_id = attribute_source.project_id
     AND candidate.trace_id = attribute_source.trace_id
     AND candidate.id = attribute_source.id
     AND candidate.start_time = attribute_source.start_time
    WHERE attribute_source.project_id = toUUID(%(project_id)s)
      AND attribute_source.start_time >= %(window_start)s
      AND attribute_source.start_time < %(window_end)s
    GROUP BY
        attribute_source.project_id,
        attribute_source.trace_id,
        attribute_source.id,
        attribute_source.start_time
),
exploded_values AS
(
    /*
     * Expand each current live span once.  The synthetic ``__span__`` event
     * is emitted exactly once for every physical span carrying the key, while
     * typed events retain each populated storage representation.  Keeping the
     * expansion in one ARRAY JOIN prevents ClickHouse from inlining and
     * replaying the expensive latest-state CTE for one UNION branch per type.
     */
    SELECT
        tupleElement(attribute_event, 1) AS attribute_type,
        tupleElement(attribute_event, 2) AS value_json,
        tupleElement(attribute_event, 3) AS number_value
    FROM latest_spans
    ARRAY JOIN arrayFilter(
        event -> tupleElement(event, 4),
        [
            tuple(
                '__span__',
                '',
                CAST(NULL, 'Nullable(Float64)'),
                toUInt8(
                    tupleElement(latest_state, 1) = 0
                    AND (
                        tupleElement(latest_state, 2)
                        OR tupleElement(latest_state, 4)
                        OR tupleElement(latest_state, 6)
                        OR (
                            tupleElement(latest_state, 8)
                            AND tupleElement(latest_state, 9) != ''
                        )
                    )
                )
            ),
            tuple(
                'string',
                toJSONString(tupleElement(latest_state, 3)),
                CAST(NULL, 'Nullable(Float64)'),
                toUInt8(
                    tupleElement(latest_state, 1) = 0
                    AND tupleElement(latest_state, 2)
                )
            ),
            tuple(
                'number',
                toJSONString(tupleElement(latest_state, 5)),
                CAST(
                    toFloat64(tupleElement(latest_state, 5)),
                    'Nullable(Float64)'
                ),
                toUInt8(
                    tupleElement(latest_state, 1) = 0
                    AND tupleElement(latest_state, 4)
                )
            ),
            tuple(
                'boolean',
                if(tupleElement(latest_state, 7), 'true', 'false'),
                CAST(NULL, 'Nullable(Float64)'),
                toUInt8(
                    tupleElement(latest_state, 1) = 0
                    AND tupleElement(latest_state, 6)
                )
            ),
            tuple(
                multiIf(
                    startsWith(trimLeft(tupleElement(latest_state, 9)), '['),
                    'array',
                    startsWith(trimLeft(tupleElement(latest_state, 9)), '{'),
                    'map',
                    'json'
                ),
                tupleElement(latest_state, 9),
                CAST(NULL, 'Nullable(Float64)'),
                toUInt8(
                    tupleElement(latest_state, 1) = 0
                    AND tupleElement(latest_state, 8)
                    AND tupleElement(latest_state, 9) != ''
                )
            )
        ]
    ) AS attribute_event
),
grouped_values AS
(
    SELECT
        attribute_type,
        value_json,
        any(number_value) AS number_value,
        count() AS value_count
    FROM exploded_values
    GROUP BY attribute_type, value_json
),
ranked_values AS
(
    SELECT
        attribute_type,
        value_json,
        value_count,
        sum(value_count) OVER (
            PARTITION BY attribute_type
        ) AS type_count,
        count() OVER (
            PARTITION BY attribute_type
        ) AS unique_values,
        minIf(number_value, isNotNull(number_value)) OVER (
            PARTITION BY attribute_type
        ) AS numeric_min,
        maxIf(number_value, isNotNull(number_value)) OVER (
            PARTITION BY attribute_type
        ) AS numeric_max,
        if(
            sumIf(value_count, isNotNull(number_value)) OVER (
                PARTITION BY attribute_type
            ) = 0,
            CAST(NULL, 'Nullable(Float64)'),
            sumIf(
                number_value * value_count,
                isNotNull(number_value)
            ) OVER (PARTITION BY attribute_type)
                / sumIf(value_count, isNotNull(number_value)) OVER (
                    PARTITION BY attribute_type
                )
        ) AS numeric_avg,
        quantileExactWeightedIf(0.50)(
            number_value, value_count, isNotNull(number_value)
        ) OVER (PARTITION BY attribute_type) AS numeric_p50,
        quantileExactWeightedIf(0.95)(
            number_value, value_count, isNotNull(number_value)
        ) OVER (PARTITION BY attribute_type) AS numeric_p95,
        sumIf(value_count, attribute_type = '__span__') OVER () AS span_count,
        row_number() OVER (
            PARTITION BY attribute_type
            ORDER BY value_count DESC, value_json ASC
        ) AS value_rank
    FROM grouped_values
)
SELECT
    ranked_values.attribute_type AS attribute_type,
    ranked_values.value_json AS value_json,
    ranked_values.value_count AS value_count,
    ranked_values.type_count AS type_count,
    ranked_values.unique_values AS unique_values,
    ranked_values.numeric_min AS numeric_min,
    ranked_values.numeric_max AS numeric_max,
    ranked_values.numeric_avg AS numeric_avg,
    ranked_values.numeric_p50 AS numeric_p50,
    ranked_values.numeric_p95 AS numeric_p95,
    ranked_values.span_count AS span_count
FROM ranked_values
WHERE ranked_values.attribute_type != '__span__'
  AND ranked_values.value_rank <= %(top_values_limit)s
ORDER BY
    ranked_values.type_count DESC,
    indexOf(['string', 'number', 'boolean', 'array', 'map', 'json'], attribute_type),
    ranked_values.value_rank ASC
"""

# Levels 1-2 reuse the original text from ``exploded_values AS`` on, byte for
# byte; slicing keeps the original literal (and its reader filing) untouched.
_TAIL_SQL = EXACT_ATTRIBUTE_DETAIL_SQL[
    EXACT_ATTRIBUTE_DETAIL_SQL.index("exploded_values AS") :
]

_SEMI_JOIN_LATEST_SPANS_SQL = r"""
WITH
%(attribute_key)s AS _attribute_key,
latest_spans AS
(
    /*
     * Identity seed as an IN set.  The key predicate runs only inside the
     * subquery; the replay filters versions by immutable identity columns
     * only, so a later key removal or tombstone still wins in argMax.
     * Binding attribute_key once keeps max-length escaped keys well
     * below ClickHouse's query-size ceiling.
     */
    SELECT
        attribute_source.trace_id AS trace_id,
        attribute_source.id AS id,
        attribute_source.start_time AS start_time,
        argMax(
            tuple(
                attribute_source.is_deleted,
                mapContains(attribute_source.attrs_string, _attribute_key),
                attribute_source.attrs_string[_attribute_key],
                mapContains(attribute_source.attrs_number, _attribute_key),
                attribute_source.attrs_number[_attribute_key],
                mapContains(attribute_source.attrs_bool, _attribute_key),
                attribute_source.attrs_bool[_attribute_key],
                JSONHas(attribute_source.attributes_extra, _attribute_key),
                JSONExtractRaw(
                    attribute_source.attributes_extra,
                    _attribute_key
                )
            ),
            attribute_source._version
        ) AS latest_state
    FROM spans AS attribute_source
    PREWHERE attribute_source.project_id = toUUID(%(project_id)s)
      AND attribute_source.start_time >= %(window_start)s
      AND attribute_source.start_time < %(window_end)s
      AND (
          attribute_source.trace_id,
          attribute_source.id,
          attribute_source.start_time
      ) IN
      (
          SELECT
              candidate_source.trace_id,
              candidate_source.id,
              candidate_source.start_time
          FROM spans AS candidate_source
          PREWHERE candidate_source.project_id = toUUID(%(project_id)s)
            AND candidate_source.start_time >= %(window_start)s
            AND candidate_source.start_time < %(window_end)s
          WHERE
        (
            indexHint(has(mapKeys(candidate_source.attrs_string), _attribute_key))
            AND mapContains(candidate_source.attrs_string, _attribute_key)
        )
        OR (
            indexHint(has(mapKeys(candidate_source.attrs_number), _attribute_key))
            AND mapContains(candidate_source.attrs_number, _attribute_key)
        )
        OR (
            indexHint(has(mapKeys(candidate_source.attrs_bool), _attribute_key))
            AND mapContains(candidate_source.attrs_bool, _attribute_key)
        )
        OR (
            candidate_source.attributes_extra NOT IN ('', '{}', 'null')
            AND JSONHas(candidate_source.attributes_extra, _attribute_key)
        )
      )
    GROUP BY
        attribute_source.trace_id,
        attribute_source.id,
        attribute_source.start_time
),
"""

EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL = _SEMI_JOIN_LATEST_SPANS_SQL + _TAIL_SQL


class _ExactDetailStatement(NamedTuple):
    level: int
    sql: str
    settings: dict[str, Any]
    timeout_ms: int


# The highest statement level this build contains.
_HIGHEST_BUILT_LEVEL = 1


def _runtime_int(name: str) -> int:
    """One numeric runtime setting (``tfc/settings/runtime_setting_specs.py``)."""

    return int(getattr(django_settings, name))


def _query_settings_reach_server(query_executor: object) -> bool:
    """True only when the executor positively identifies the settings lane."""

    client = getattr(query_executor, "client", None)
    readonly = getattr(client, "server_enforced_readonly", None)
    allowed = getattr(client, "allow_query_settings_with_server_readonly", None)
    if not isinstance(readonly, bool) or not isinstance(allowed, bool):
        return False
    return (not readonly) or allowed


def _exact_detail_statement(query_executor: object) -> _ExactDetailStatement:
    """Statement, settings and deadline for the effective level, decided together.

    Level 0 is the original statement, settings and 30 s deadline.  The level
    is clamped to the highest level this build contains, and an executor that
    does not identify the settings lane gets level 0.  The statements, the
    settings dicts and the 30 s constant are read as module globals at call
    time, never captured at import.
    """

    level = min(_runtime_int("EXACT_ATTRIBUTE_DETAIL_SQL_LEVEL"), _HIGHEST_BUILT_LEVEL)
    if level <= 0 or not _query_settings_reach_server(query_executor):
        return _ExactDetailStatement(
            0,
            EXACT_ATTRIBUTE_DETAIL_SQL,
            EXACT_ATTRIBUTE_DETAIL_READ_SETTINGS,
            EXACT_ATTRIBUTE_DETAIL_QUERY_TIMEOUT_MS,
        )
    timeout_ms = min(
        _runtime_int("EXACT_ATTRIBUTE_DETAIL_RESHAPED_TIMEOUT_MS"),
        max(
            EXACT_ATTRIBUTE_DETAIL_QUERY_TIMEOUT_MS,
            _runtime_int("GRAPH_BACKGROUND_WALL_MS"),
        ),
    )
    return _ExactDetailStatement(
        1,
        EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL,
        EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_READ_SETTINGS,
        timeout_ms,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decode_json_value(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        # ClickHouse toJSONString/JSONExtractRaw should always be valid JSON;
        # retaining text is safer than discarding a completed aggregate if an
        # old malformed overflow payload is encountered.
        return raw


def read_exact_attribute_detail(
    *,
    project_id: str,
    attribute_key: str,
    executor: V2AttributeQueryExecutor | None = None,
    window_end: datetime | None = None,
    horizon_days: int = EXACT_ATTRIBUTE_DETAIL_HORIZON_DAYS,
) -> dict[str, Any]:
    """Compute one complete exact detail payload in one ClickHouse statement.

    The statement, its settings and its deadline come together from
    ``_exact_detail_statement`` for the constructed executor.
    """

    started = monotonic()
    key = validate_attribute_key(attribute_key)
    end = _utc(window_end or datetime.now(UTC))
    start = end - timedelta(days=max(1, min(int(horizon_days), 365)))
    query_executor = executor or V2AttributeQueryExecutor()
    statement = _exact_detail_statement(query_executor)
    page = query_executor.execute(
        statement.sql,
        {
            "project_id": str(project_id),
            "attribute_key": key,
            "window_start": start,
            "window_end": end,
            "top_values_limit": EXACT_ATTRIBUTE_DETAIL_TOP_VALUES,
        },
        timeout_ms=statement.timeout_ms,
        settings=statement.settings,
    )
    rows = list(page.data or [])
    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        attribute_type = str(row.get("attribute_type") or "")
        if attribute_type not in _TYPE_PRIORITY:
            raise RuntimeError("exact attribute detail returned an invalid type")
        by_type.setdefault(attribute_type, []).append(row)

    common = {
        "key": key,
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
        "query_window_start": start.isoformat().replace("+00:00", "Z"),
        "query_window_end": end.isoformat().replace("+00:00", "Z"),
        "query_count": 1,
        "query_elapsed_ms": round((monotonic() - started) * 1000, 3),
    }
    if not by_type:
        return {
            **common,
            "type": None,
            "count": 0,
            "unique_values": 0,
            "top_values": [],
        }

    ordered_types = sorted(
        by_type,
        key=lambda value: (
            -int(by_type[value][0].get("type_count") or 0),
            _TYPE_PRIORITY[value],
        ),
    )
    attribute_type = ordered_types[0]
    type_summaries = [
        {
            "type": value,
            "count": int(by_type[value][0].get("type_count") or 0),
            "unique_values": int(by_type[value][0].get("unique_values") or 0),
        }
        for value in ordered_types
    ]
    # ``type_count`` intentionally remains per storage family.  A single
    # latest physical span can therefore contribute to multiple type
    # summaries, while the public count and percentages must use the exact
    # distinct physical-span denominator returned by ClickHouse.
    total = int(rows[0].get("span_count") or 0)
    unique_values = sum(summary["unique_values"] for summary in type_summaries)
    ranked_rows = sorted(
        (row for rows_for_type in by_type.values() for row in rows_for_type),
        key=lambda row: (
            -int(row.get("value_count") or 0),
            _TYPE_PRIORITY[str(row.get("attribute_type"))],
            str(row.get("value_json") or ""),
        ),
    )[:EXACT_ATTRIBUTE_DETAIL_TOP_VALUES]
    top_values = [
        {
            "value": _decode_json_value(row.get("value_json")),
            "type": str(row.get("attribute_type")),
            "count": int(row.get("value_count") or 0),
            "percentage": (
                float(row.get("value_count") or 0) * 100.0 / total if total else 0.0
            ),
        }
        for row in ranked_rows
    ]
    payload: dict[str, Any] = {
        **common,
        "type": attribute_type,
        "types": type_summaries,
        "count": total,
        "unique_values": unique_values,
        "top_values": top_values,
    }
    if "number" in by_type:
        numeric = by_type["number"][0]
        stats = {
            "min": numeric.get("numeric_min"),
            "max": numeric.get("numeric_max"),
            "avg": numeric.get("numeric_avg"),
            "p50": numeric.get("numeric_p50"),
            "p95": numeric.get("numeric_p95"),
        }
        payload.update(stats)
        payload["stats"] = stats
    return payload


__all__ = [
    "EXACT_ATTRIBUTE_DETAIL_HORIZON_DAYS",
    "EXACT_ATTRIBUTE_DETAIL_QUERY_TIMEOUT_MS",
    "EXACT_ATTRIBUTE_DETAIL_READ_SETTINGS",
    "EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_READ_SETTINGS",
    "EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL",
    "EXACT_ATTRIBUTE_DETAIL_SQL",
    "read_exact_attribute_detail",
]
