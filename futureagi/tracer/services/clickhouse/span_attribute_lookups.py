"""ClickHouse-backed lookups that previously hit the dropped GIN indexes
(``tracer_obse_span_at_gin`` on ``span_attributes`` and
``tracer_obse_eval_attr_gin`` on ``eval_attributes``).

These helpers exist so the small number of callers that needed JSONB
key-existence / containment lookups don't fall back to a sequential scan
on the 656 GB ``tracer_observation_span`` table now that the GINs are gone.

Each helper degrades gracefully: if ClickHouse is disabled / unavailable
it returns the same shape an empty result would, with an info-level log,
so callers can keep working (though potentially with degraded results
until CH is restored).

Notes on the schema:
  - ``spans`` (the CH table mirroring tracer_observation_span) holds the
    raw JSON in ``span_attributes_raw`` and ``eval_attributes`` (String
    columns, ZSTD-compressed).
  - ``spans`` also has ``attrs_string``/``attrs_number``/``attrs_bool``
    Map columns shredded from ``span_attributes``. ``mapContains(...)`` over
    these maps is the cheapest way to test key existence.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import structlog

from tracer.services.clickhouse.client import (
    ClickHouseClient,
    get_clickhouse_client,
    is_clickhouse_enabled,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AttributeKey:
    """One distinct span attribute key discovered for a project."""

    key: str
    type: str  # "string" | "number" | "boolean"
    count: int


@dataclass(frozen=True)
class TraceAttribute:
    """One attribute key with the distinct values it took across a trace's
    spans. If only one value appears, ``values`` has length 1; cross-span
    conflicts surface as length>1 and the caller can render ``[varies]``.
    """

    key: str
    type: str  # "string" | "number" | "boolean"
    values: list[str]  # stringified for transport — caller may coerce on type


def _ch_available() -> bool:
    if not is_clickhouse_enabled():
        return False
    client = get_clickhouse_client()
    return client is not None and client.is_configured


def list_attribute_keys_for_project(project_id: str) -> list[AttributeKey]:
    """Return every distinct span attribute key seen for the project.

    Walks the three shredded Map columns on the ``spans`` table
    (``attrs_string`` / ``attrs_number`` / ``attrs_bool``) and reports
    each key with its inferred type and total occurrence count, sorted by
    occurrences descending.

    Returns an empty list (with a log) if ClickHouse is unavailable.
    """
    if not project_id:
        return []
    if not is_clickhouse_enabled():
        logger.info(
            "ch_unavailable_for_attribute_keys_lookup",
            project_id=project_id,
        )
        return []

    analytics = AnalyticsQueryService()
    result = analytics.get_span_attribute_keys_ch_for_projects(
        [project_id],
        include_counts=True,
        order_by_count_desc=True,
    )

    logger.info(
        "span_attribute_keys_fetched",
        project_id=project_id,
        key_count=len(result),
    )

    return [
        AttributeKey(
            key=row["key"],
            type=row["type"],
            count=row["count"],
        )
        for row in result
    ]


def list_attributes_for_trace(trace_id: str) -> list[TraceAttribute]:
    """Return distinct (key, type, values[]) per attribute on a single trace.

    Walks the three shredded Map columns (``attrs_string`` /
    ``attrs_number`` / ``attrs_bool``) for every span in the trace,
    groups by key, and emits one row per key with the distinct value set
    seen across that trace's spans. A trace with consistent attribute
    values renders cleanly; cross-span drift (e.g. a flag toggled mid-trace)
    surfaces as ``values=["a", "b"]``.

    Returns an empty list (with a log) if ClickHouse is unavailable.
    """
    if not trace_id:
        return []
    if not is_clickhouse_enabled():
        logger.info(
            "ch_unavailable_for_trace_attributes_lookup",
            trace_id=trace_id,
        )
        return []

    # arrayZip(mapKeys, mapValues) flattened by ARRAY JOIN gives us one
    # row per (span, key, value) tuple. The CTE unions the three typed
    # Maps so we can group by key once across all of them.
    query = """
        WITH attrs AS (
            SELECT pair.1 AS key, toString(pair.2) AS value, 'string' AS type
            FROM spans
            ARRAY JOIN arrayZip(
                mapKeys(attrs_string), mapValues(attrs_string)
            ) AS pair
            WHERE toString(trace_id) = %(trace_id)s

            UNION ALL

            SELECT pair.1 AS key, toString(pair.2) AS value, 'number' AS type
            FROM spans
            ARRAY JOIN arrayZip(
                mapKeys(attrs_number), mapValues(attrs_number)
            ) AS pair
            WHERE toString(trace_id) = %(trace_id)s

            UNION ALL

            SELECT pair.1 AS key, toString(pair.2) AS value, 'boolean' AS type
            FROM spans
            ARRAY JOIN arrayZip(
                mapKeys(attrs_bool), mapValues(attrs_bool)
            ) AS pair
            WHERE toString(trace_id) = %(trace_id)s
        )
        SELECT key, any(type) AS type, groupUniqArray(value) AS values
        FROM attrs
        GROUP BY key
        ORDER BY key
    """
    params = {"trace_id": str(trace_id)}

    try:
        client = ClickHouseClient()
        rows, _column_types, query_time_ms = client.execute_read(query, params)
    except Exception as e:
        logger.warning(
            "ch_trace_attributes_lookup_failed",
            error=str(e),
            trace_id=trace_id,
        )
        return []

    logger.info(
        "trace_attributes_fetched",
        trace_id=trace_id,
        attribute_count=len(rows),
        query_time_ms=query_time_ms,
    )

    return [
        TraceAttribute(key=row[0], type=row[1], values=list(row[2])) for row in rows
    ]


def list_attribute_keys_for_traces(
    project_id: str, trace_ids: Iterable[str]
) -> list[AttributeKey]:
    """Distinct attribute keys across a set of traces (batched, one CH query).

    Returns [{key, type, count}] where count = number of traces carrying
    that key.  Replaces the per-trace loop in the RCA agent's
    list(attribute_keys) handler.
    """
    ids = [str(t) for t in trace_ids if t]
    if not ids or not is_clickhouse_enabled():
        return []

    query = """
        WITH attrs AS (
            SELECT toString(trace_id) AS tid,
                   key, 'string' AS type
            FROM spans
            ARRAY JOIN mapKeys(attrs_string) AS key
            WHERE project_id = %(pid)s AND is_deleted = 0
              AND toString(trace_id) IN %(tids)s

            UNION ALL

            SELECT toString(trace_id) AS tid,
                   key, 'number' AS type
            FROM spans
            ARRAY JOIN mapKeys(attrs_number) AS key
            WHERE project_id = %(pid)s AND is_deleted = 0
              AND toString(trace_id) IN %(tids)s

            UNION ALL

            SELECT toString(trace_id) AS tid,
                   key, 'boolean' AS type
            FROM spans
            ARRAY JOIN mapKeys(attrs_bool) AS key
            WHERE project_id = %(pid)s AND is_deleted = 0
              AND toString(trace_id) IN %(tids)s
        )
        SELECT key, any(type) AS type, uniqExact(tid) AS trace_count
        FROM attrs
        GROUP BY key
        ORDER BY trace_count DESC
    """
    params = {"pid": str(project_id), "tids": ids}

    try:
        client = ClickHouseClient()
        rows, _types, query_time_ms = client.execute_read(query, params)
    except Exception as e:
        logger.warning(
            "ch_batch_attribute_keys_failed",
            error=str(e),
            trace_count=len(ids),
        )
        return []

    logger.info(
        "batch_attribute_keys_fetched",
        trace_count=len(ids),
        key_count=len(rows),
        query_time_ms=query_time_ms,
    )
    return [AttributeKey(key=r[0], type=r[1], count=r[2]) for r in rows]


@dataclass(frozen=True)
class AttributeBucket:
    """One value of an attribute key with its occurrence count across a
    trace set — the count is span occurrences or distinct traces depending
    on the caller's request."""

    value: str
    count: int


def scoped_trace_ids(
    project_id: str,
    trace_ids: Iterable[str],
    filters: list[dict],
) -> list[str] | None:
    """Narrow a trace-id set by canonical filters, evaluated in ClickHouse.

    The blast-radius pattern: callers pass the cluster's stored trace_ids
    (`trace_ids`); this returns the subset whose spans satisfy `filters`
    (attr.* / eval.* / annotation / column), evaluated via the prod-proven
    ClickHouseFilterBuilder against the shredded `spans` table.

    `filters` is the canonical FE/BE contract (list of
    {column_id, filter_config{col_type, filter_type, filter_op, filter_value}}).

    Returns the surviving trace-id list, or None when CH is unavailable
    (caller decides whether to fall back to the unfiltered set or error).
    """
    ids = [str(t) for t in trace_ids if t]
    if not ids:
        return []
    if not filters:
        return ids
    if not is_clickhouse_enabled():
        logger.info("ch_unavailable_for_scoped_trace_ids", project_id=project_id)
        return None

    # Imported lazily — heavy module, only needed when filters are present.
    from tracer.services.clickhouse.query_builders.filters import (
        ClickHouseFilterBuilder,
    )

    fb = ClickHouseFilterBuilder(
        table="spans",
        query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
        project_id=str(project_id),
        score_date_scope=False,
    )
    where, params = fb.translate(filters)
    params = dict(params)
    params["_pid"] = str(project_id)
    params["_tids"] = ids

    where_clause = f" AND ({where})" if where else ""
    query = (
        "SELECT DISTINCT toString(trace_id) FROM spans "
        "WHERE project_id = %(_pid)s "
        "AND is_deleted = 0 "
        "AND toString(trace_id) IN %(_tids)s"
        f"{where_clause}"
    )
    try:
        client = ClickHouseClient()
        rows, _types, query_time_ms = client.execute_read(query, params)
    except Exception as e:
        logger.warning(
            "ch_scoped_trace_ids_failed",
            error=str(e),
            project_id=project_id,
        )
        return None

    logger.info(
        "scoped_trace_ids_fetched",
        project_id=project_id,
        in_count=len(ids),
        out_count=len(rows),
        query_time_ms=query_time_ms,
    )
    return [row[0] for row in rows]


def aggregate_attribute_over_traces(
    project_id: str,
    trace_ids: Iterable[str],
    attr_key: str,
    distinct_traces: bool = True,
) -> list[AttributeBucket]:
    """Value distribution of one attribute key across a set of traces.

    The killer cluster-RCA primitive: "of the failing traces, how do they
    split across attr.<key>?". Walks the three shredded Map columns,
    extracts the value of `attr_key` per span, groups by value.

    distinct_traces=True  → count distinct trace_ids per value (trace_count)
    distinct_traces=False → count span occurrences per value (span_count)

    Returns [] (with a log) if ClickHouse is unavailable.
    """
    ids = [str(t) for t in trace_ids if t]
    if not ids or not attr_key:
        return []
    if not is_clickhouse_enabled():
        logger.info(
            "ch_unavailable_for_attribute_aggregation",
            attr_key=attr_key,
        )
        return []

    count_expr = "uniqExact(trace_id)" if distinct_traces else "count()"
    query = f"""
        WITH vals AS (
            SELECT toString(trace_id) AS trace_id,
                   toString(attrs_string[%(key)s]) AS value
            FROM spans
            WHERE project_id = %(pid)s AND is_deleted = 0
              AND toString(trace_id) IN %(trace_ids)s
              AND mapContains(attrs_string, %(key)s)

            UNION ALL

            SELECT toString(trace_id) AS trace_id,
                   toString(attrs_number[%(key)s]) AS value
            FROM spans
            WHERE project_id = %(pid)s AND is_deleted = 0
              AND toString(trace_id) IN %(trace_ids)s
              AND mapContains(attrs_number, %(key)s)

            UNION ALL

            SELECT toString(trace_id) AS trace_id,
                   toString(attrs_bool[%(key)s]) AS value
            FROM spans
            WHERE project_id = %(pid)s AND is_deleted = 0
              AND toString(trace_id) IN %(trace_ids)s
              AND mapContains(attrs_bool, %(key)s)
        )
        SELECT value, {count_expr} AS cnt
        FROM vals
        GROUP BY value
        ORDER BY cnt DESC
    """
    params = {"key": attr_key, "trace_ids": ids, "pid": str(project_id)}

    try:
        client = ClickHouseClient()
        rows, _column_types, query_time_ms = client.execute_read(query, params)
    except Exception as e:
        logger.warning(
            "ch_attribute_aggregation_failed",
            error=str(e),
            attr_key=attr_key,
        )
        return []

    logger.info(
        "attribute_aggregation_fetched",
        attr_key=attr_key,
        bucket_count=len(rows),
        distinct_traces=distinct_traces,
        query_time_ms=query_time_ms,
    )
    return [AttributeBucket(value=row[0], count=row[1]) for row in rows]


def trace_ids_with_simulator_call_execution_id(
    trace_ids: Iterable[str],
) -> set[str]:
    """Return the subset of ``trace_ids`` that have at least one span carrying
    the ``fi.simulator.call_execution_id`` key in ``span_attributes``
    (with a non-null value).

    Replaces the old ``Exists()`` subquery in
    ``tracer/utils/replay_session.py`` which used
    ``span_attributes__has_key`` + ``span_attributes__contains``.
    """
    trace_id_list = [str(t) for t in trace_ids if t]
    if not trace_id_list:
        return set()
    if not _ch_available():
        logger.info(
            "ch_unavailable_for_simulator_lookup",
            trace_count=len(trace_id_list),
        )
        return set()

    # mapContains is O(map size); for the typed shred path we can fall
    # back to JSONExtractRaw against span_attributes_raw for spans whose
    # value got bucketed into a different map or where the key didn't
    # land in the str map. The OR keeps us safe regardless of which map
    # the key was assigned to (string vs numeric vs bool).
    query = """
        SELECT DISTINCT toString(trace_id)
        FROM spans
        WHERE trace_id IN %(trace_ids)s
          AND (
                mapContains(attrs_string,  'fi.simulator.call_execution_id')
             OR mapContains(attrs_number,  'fi.simulator.call_execution_id')
             OR mapContains(attrs_bool, 'fi.simulator.call_execution_id')
             OR JSONHas(span_attributes_raw, 'fi.simulator.call_execution_id')
          )
          AND JSONExtractRaw(span_attributes_raw,
                             'fi.simulator.call_execution_id') NOT IN ('null', '')
    """
    try:
        client = get_clickhouse_client()
        rows = client.execute(query, params={"trace_ids": trace_id_list})
        return {row[0] for row in rows}
    except Exception as e:
        logger.warning(
            "ch_simulator_lookup_failed",
            error=str(e),
            trace_count=len(trace_id_list),
        )
        return set()


def spans_by_eval_attribute_call_execution_ids(
    call_execution_ids: Iterable[str],
) -> dict[str, list[dict]]:
    """For each call_execution_id, return the spans whose ``eval_attributes``
    contain ``{"fi.simulator.call_execution_id": <call_execution_id>}``.

    Output: ``{call_execution_id: [{"id": str, "trace_id": str,
                                    "eval_attributes": str (raw JSON)}, ...]}``.

    Replaces the OR-of-Q-objects against PG ``eval_attributes__contains``
    in ``simulate/views/run_test.py``.
    """
    ids = [str(c) for c in call_execution_ids if c]
    if not ids:
        return {}
    if not _ch_available():
        logger.info(
            "ch_unavailable_for_eval_attr_lookup",
            call_execution_count=len(ids),
        )
        return {}

    # JSONExtractString for an exact value match — equivalent to PG's
    # ``eval_attributes @> '{"k": "v"}'`` when the value is a scalar string.
    query = """
        SELECT
            toString(id)        AS span_id,
            toString(trace_id)  AS trace_id,
            JSONExtractString(eval_attributes,
                              'fi.simulator.call_execution_id') AS call_exec_id,
            eval_attributes     AS eval_attributes
        FROM spans
        WHERE JSONExtractString(eval_attributes,
                                'fi.simulator.call_execution_id') IN %(ids)s
    """
    out: dict[str, list[dict]] = {}
    try:
        client = get_clickhouse_client()
        rows = client.execute(query, params={"ids": ids})
        for span_id, trace_id, call_exec_id, eval_attrs in rows:
            out.setdefault(call_exec_id, []).append(
                {
                    "id": span_id,
                    "trace_id": trace_id,
                    "eval_attributes": eval_attrs,
                }
            )
        return out
    except Exception as e:
        logger.warning(
            "ch_eval_attr_lookup_failed",
            error=str(e),
            call_execution_count=len(ids),
        )
        return {}


def span_id_by_provider_log_id(
    project_id: str,
    provider: str,
    provider_log_id: str,
) -> str | None:
    """Look up the most recent span id for a ``(project, provider, provider_log_id)``.

    Mirrors the OR-Q lookup in ``tracer/utils/observability_provider.py`` which
    previously used three JSONB filters on PG (``metadata__provider_log_id``,
    ``span_attributes__raw_log__id``, ``eval_attributes__raw_log__id``).

    Returns the span id as a string, or None if not found / CH unavailable.
    """
    if not provider_log_id:
        return None
    if not _ch_available():
        logger.info(
            "ch_unavailable_for_provider_log_lookup",
            provider_log_id=provider_log_id,
        )
        return None

    # The previous PG query also matched ``metadata.provider_log_id`` (note:
    # ``metadata`` lives in ``metadata_map`` in CH) and ``eval_attributes``.
    # The denormalized ``spans`` table has no ``eval_attributes`` column
    # (only the CDC landing table ``tracer_observation_span`` does), so that
    # branch is dropped here: ``metadata_map['provider_log_id']`` reliably
    # carries the provider_log_id written at span creation.
    query = """
        SELECT toString(id)
        FROM spans
        WHERE project_id = %(project_id)s
          AND provider   = %(provider)s
          AND (
                metadata_map['provider_log_id']                                = %(pid)s
             OR JSONExtractString(span_attributes_raw, 'raw_log', 'id')       = %(pid)s
          )
        ORDER BY updated_at DESC
        LIMIT 1
    """
    try:
        client = get_clickhouse_client()
        rows = client.execute(
            query,
            params={
                "project_id": str(project_id),
                "provider": provider,
                "pid": provider_log_id,
            },
        )
        return rows[0][0] if rows else None
    except Exception as e:
        logger.warning(
            "ch_provider_log_lookup_failed",
            error=str(e),
            provider_log_id=provider_log_id,
        )
        return None
