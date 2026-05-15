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
  - ``spans`` also has ``span_attr_str``/``span_attr_num``/``span_attr_bool``
    Map columns shredded from ``span_attributes``. ``mapContains(...)`` over
    these maps is the cheapest way to test key existence.
"""

from __future__ import annotations

from typing import Iterable, Optional

import structlog

from tracer.services.clickhouse.client import (
    get_clickhouse_client,
    is_clickhouse_enabled,
)

logger = structlog.get_logger(__name__)


def _ch_available() -> bool:
    if not is_clickhouse_enabled():
        return False
    client = get_clickhouse_client()
    return client is not None and client.is_configured


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
                mapContains(span_attr_str,  'fi.simulator.call_execution_id')
             OR mapContains(span_attr_num,  'fi.simulator.call_execution_id')
             OR mapContains(span_attr_bool, 'fi.simulator.call_execution_id')
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
) -> Optional[str]:
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
    # ``metadata`` lives in ``metadata_map`` in CH). We OR all three sources,
    # consistent with the old behaviour.
    query = """
        SELECT toString(id)
        FROM spans
        WHERE project_id = %(project_id)s
          AND provider   = %(provider)s
          AND (
                metadata_map['provider_log_id']                                = %(pid)s
             OR JSONExtractString(span_attributes_raw, 'raw_log', 'id')       = %(pid)s
             OR JSONExtractString(eval_attributes,     'raw_log', 'id')       = %(pid)s
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
