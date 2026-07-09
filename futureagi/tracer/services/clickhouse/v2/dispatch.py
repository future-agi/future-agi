"""
v1 ↔ v2 query-builder dispatch.

Views import the builder via `get_query_builder_class(QueryType.X)` instead of
importing a specific v1 or v2 class. The factory consults the per-query-type
routing settings (`CLICKHOUSE_V2.QUERY_TYPES_V2_*` — see shadow.py) and
returns the right class.

Operator workflow:
  1. Deploy this — `get_query_builder_class()` returns v1 for every query
     type by default (no behavior change).
  2. Add a query type to `CH25_QUERY_TYPES_SHADOW` (comma-separated env var).
     The factory still returns v1 for that type; the shadow harness in
     `shadow.run_with_shadow()` runs v1 + v2 in parallel and logs diffs.
  3. After 24-48h of zero shadow diffs in production logs, move that
     query type to `CH25_QUERY_TYPES_V2_PRIMARY`. v2 becomes the
     authoritative result; v1 still runs in shadow as a safety net.
  4. After another soak, move to `CH25_QUERY_TYPES_V2_ONLY` — v1 stops
     running entirely for that type.
  5. After every query type is V2_ONLY, delete the v1 query builders.

Why a registry table instead of magic naming:
  - Explicit registration makes the swap surface visible — a quick grep
    of this file enumerates exactly which builders have v2 counterparts.
  - Lazy import (via importlib) keeps Django startup cheap and avoids a
    circular-import landmine between v1 and v2.
  - A new query type without a v2 entry falls back cleanly to v1 with a
    warning log — no NameError at the call site.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

import structlog

from tracer.services.clickhouse.v2.shadow import RoutingMode, get_routing_mode

logger = structlog.get_logger(__name__)


# ─── Registry: query type → (v1 module path, v1 class, v2 module path, v2 class) ─
# Keep this small and explicit. Adding a row here is the contract for "this
# query type has a v2 builder and is eligible for the v1→v2 routing flip."


@dataclass(frozen=True)
class _BuilderEntry:
    v1_module: str
    v1_class: str
    v2_module: str | None  # None = no v2 builder yet (falls back to v1)
    v2_class: str | None


_REGISTRY: dict[str, _BuilderEntry] = {
    "SPAN_LIST": _BuilderEntry(
        v1_module="tracer.services.clickhouse.query_builders.span_list",
        v1_class="SpanListQueryBuilder",
        v2_module="tracer.services.clickhouse.v2.query_builders.span_list",
        v2_class="SpanListQueryBuilderV2",
    ),
    "TRACE_LIST": _BuilderEntry(
        v1_module="tracer.services.clickhouse.query_builders.trace_list",
        v1_class="TraceListQueryBuilder",
        v2_module="tracer.services.clickhouse.v2.query_builders.trace_list",
        v2_class="TraceListQueryBuilderV2",
    ),
    "SESSION_LIST": _BuilderEntry(
        v1_module="tracer.services.clickhouse.query_builders",
        v1_class="SessionListQueryBuilder",
        v2_module="tracer.services.clickhouse.v2.query_builders.session_list",
        v2_class="SessionListQueryBuilderV2",
    ),
    "VOICE_CALL_LIST": _BuilderEntry(
        v1_module="tracer.services.clickhouse.query_builders.voice_call_list",
        v1_class="VoiceCallListQueryBuilder",
        v2_module="tracer.services.clickhouse.v2.query_builders.voice_call_list",
        v2_class="VoiceCallListQueryBuilderV2",
    ),
    "DASHBOARD": _BuilderEntry(
        v1_module="tracer.services.clickhouse.query_builders.dashboard",
        v1_class="DashboardQueryBuilder",
        v2_module="tracer.services.clickhouse.v2.query_builders.dashboard",
        v2_class="DashboardQueryBuilderV2",
    ),
    "MONITOR_METRICS": _BuilderEntry(
        v1_module="tracer.services.clickhouse.query_builders.monitor_metrics",
        v1_class="MonitorMetricsQueryBuilder",
        v2_module="tracer.services.clickhouse.v2.query_builders.monitor_metrics",
        v2_class="MonitorMetricsQueryBuilderV2",
    ),
    "EVAL_METRICS": _BuilderEntry(
        v1_module="tracer.services.clickhouse.query_builders.eval_metrics",
        v1_class="EvalMetricsQueryBuilder",
        v2_module="tracer.services.clickhouse.v2.query_builders.eval_metrics",
        v2_class="EvalMetricsQueryBuilderV2",
    ),
    "FILTER_BUILDER": _BuilderEntry(
        v1_module="tracer.services.clickhouse.query_builders.filters",
        v1_class="ClickHouseFilterBuilder",
        v2_module="tracer.services.clickhouse.v2.query_builders.filters",
        v2_class="ClickHouseFilterBuilderV2",
    ),
    "TRACE_DETAIL": _BuilderEntry(
        v1_module="tracer.services.clickhouse.query_builders.trace_detail",
        v1_class="TraceDetailHandler",
        v2_module="tracer.services.clickhouse.v2.query_builders.trace_detail",
        v2_class="TraceDetailHandlerV2",
    ),
    "ANNOTATION_LABELS": _BuilderEntry(
        v1_module="tracer.services.annotation_label_source",
        v1_class="AnnotationLabelScoresPG",
        v2_module="tracer.services.annotation_label_source",
        v2_class="AnnotationLabelScoresCH",
    ),
}


def _load(module: str, klass: str) -> type:
    return getattr(importlib.import_module(module), klass)


def get_query_builder_class(query_type: str) -> type:
    """Resolve the right builder class for `query_type` based on routing settings.

    `query_type` is a string from `QueryType` (in tracer.services.clickhouse.query_service)
    or any of the keys in `_REGISTRY` above. Case-insensitive.

    Returns:
      - the v2 class if routing for this type is V2_ONLY or V2_PRIMARY,
      - the v1 class otherwise (including SHADOW — shadow mode runs both via
        `shadow.run_with_shadow()`, but the user-facing return is v1's result).

    Falls back to v1 with a warning log if the registry doesn't have a v2
    entry for the requested type — keeps unknown/new query types working
    instead of raising AttributeError at the view call site.
    """
    key = query_type.upper() if isinstance(query_type, str) else str(query_type).upper()
    entry = _REGISTRY.get(key)
    if entry is None:
        raise KeyError(
            f"get_query_builder_class: unknown query_type {key!r}. "
            f"Register it in tracer/services/clickhouse/v2/dispatch.py:_REGISTRY first."
        )

    mode = get_routing_mode(key)
    use_v2 = mode in (RoutingMode.V2_ONLY, RoutingMode.V2_PRIMARY)

    if use_v2 and (entry.v2_module is None or entry.v2_class is None):
        logger.warning(
            "dispatch_v2_requested_but_unavailable_falling_back_to_v1",
            query_type=key,
            routing_mode=mode.value,
        )
        return _load(entry.v1_module, entry.v1_class)

    if use_v2:
        return _load(entry.v2_module, entry.v2_class)  # type: ignore[arg-type]
    return _load(entry.v1_module, entry.v1_class)


def get_v1_class(query_type: str) -> type:
    """Always return the v1 class — used by the shadow harness to run v1
    in parallel regardless of routing mode."""
    key = query_type.upper() if isinstance(query_type, str) else str(query_type).upper()
    entry = _REGISTRY[key]
    return _load(entry.v1_module, entry.v1_class)


def get_v2_class(query_type: str) -> type | None:
    """Always return the v2 class, or None if not registered. Used by the
    shadow harness in modes where v2 must run regardless of primary routing."""
    key = query_type.upper() if isinstance(query_type, str) else str(query_type).upper()
    entry = _REGISTRY[key]
    if entry.v2_module is None or entry.v2_class is None:
        return None
    return _load(entry.v2_module, entry.v2_class)


__all__ = [
    "get_query_builder_class",
    "get_v1_class",
    "get_v2_class",
]
