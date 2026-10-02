"""
Pin the v1↔v2 dispatch behavior.

Tests the factory in tracer/services/clickhouse/v2/dispatch.py: given a
query type + settings, the right builder class comes back.
"""

from __future__ import annotations

import pytest

from tracer.services.clickhouse.v2.dispatch import (
    get_query_builder_class,
    get_v1_class,
    get_v2_class,
)


def _override(**routing_overrides):
    """Helper: temporarily set CLICKHOUSE_V2 routing settings via Django."""
    from django.test.utils import override_settings

    base = {
        "QUERY_TYPES_V2_PRIMARY": "",
        "QUERY_TYPES_V2_ONLY": "",
        "QUERY_TYPES_SHADOW": "",
        "QUERY_TYPES_DISABLED": "",
    }
    base.update(routing_overrides)
    return override_settings(CLICKHOUSE_V2=base)


# ─── Default routing → v1 ────────────────────────────────────────────────────
def test_unrouted_query_type_returns_v1_class():
    with _override():
        cls = get_query_builder_class("SPAN_LIST")
    assert cls.__name__ == "SpanListQueryBuilder"


def test_unrouted_works_for_every_registered_type():
    """Smoke: every query type in the registry resolves to its v1 class by default."""
    from tracer.services.clickhouse.v2.dispatch import _REGISTRY

    with _override():
        for qt in _REGISTRY:
            cls = get_query_builder_class(qt)
            assert cls.__name__ == _REGISTRY[qt].v1_class, f"{qt} → wrong class"


# ─── V2_ONLY routing → v2 ────────────────────────────────────────────────────
def test_v2_only_routing_returns_v2_class():
    with _override(QUERY_TYPES_V2_ONLY="SPAN_LIST"):
        cls = get_query_builder_class("SPAN_LIST")
    assert cls.__name__ == "SpanListQueryBuilderV2"


def test_v2_primary_routing_returns_v2_class():
    with _override(QUERY_TYPES_V2_PRIMARY="TRACE_LIST"):
        cls = get_query_builder_class("TRACE_LIST")
    assert cls.__name__ == "TraceListQueryBuilderV2"


def test_shadow_routing_returns_v1_class_for_user_facing_call():
    """SHADOW mode means BOTH run, but the user-facing result is v1.
    The shadow harness (shadow.run_with_shadow) does the parallel v2 run.
    The factory returns v1 — that's the contract.
    """
    with _override(QUERY_TYPES_SHADOW="DASHBOARD"):
        cls = get_query_builder_class("DASHBOARD")
    assert cls.__name__ == "DashboardQueryBuilder"


# ─── Case-insensitive query-type lookups ─────────────────────────────────────
def test_case_insensitive_lookup():
    with _override(QUERY_TYPES_V2_ONLY="span_list"):
        cls = get_query_builder_class("SPAN_LIST")
    assert cls.__name__ == "SpanListQueryBuilderV2"


def test_lowercase_query_type_resolves():
    with _override(QUERY_TYPES_V2_ONLY="SPAN_LIST"):
        cls = get_query_builder_class("span_list")
    assert cls.__name__ == "SpanListQueryBuilderV2"


# ─── Unknown query type → KeyError (loud, not silent fallback) ───────────────
def test_unknown_query_type_raises():
    with pytest.raises(KeyError, match="DOES_NOT_EXIST"):
        get_query_builder_class("DOES_NOT_EXIST")


# ─── Convenience helpers ─────────────────────────────────────────────────────
def test_get_v1_class_ignores_routing():
    with _override(QUERY_TYPES_V2_ONLY="SPAN_LIST"):
        cls = get_v1_class("SPAN_LIST")
    assert cls.__name__ == "SpanListQueryBuilder"


def test_get_v2_class_ignores_routing():
    with _override():  # default routing = v1 everywhere
        cls = get_v2_class("SPAN_LIST")
    assert cls is not None
    assert cls.__name__ == "SpanListQueryBuilderV2"


def test_get_v2_class_returns_none_when_unregistered():
    from tracer.services.clickhouse.v2.dispatch import (
        _REGISTRY,
        _BuilderEntry,
    )

    # Add a stub entry pointing at no v2 module
    _REGISTRY["UNREGISTERED_V2"] = _BuilderEntry(
        v1_module="tracer.services.clickhouse.query_builders.span_list",
        v1_class="SpanListQueryBuilder",
        v2_module=None,
        v2_class=None,
    )
    try:
        assert get_v2_class("UNREGISTERED_V2") is None
    finally:
        del _REGISTRY["UNREGISTERED_V2"]


@pytest.mark.parametrize(
    "query_type",
    [
        "TRACE_LIST",
        "SPAN_LIST",
        "VOICE_CALL_LIST",
        "SESSION_LIST",
        "DASHBOARD",
        "TRACE_DETAIL",
    ],
)
def test_dispatched_v2_builders_select_matching_ch25_service(monkeypatch, query_type):
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.v2 import query_service as query_service_module

    fallback = object.__new__(AnalyticsQueryService)
    direct_write_service = object()
    monkeypatch.setattr(
        query_service_module,
        "V2AnalyticsQueryService",
        lambda: direct_write_service,
    )

    with _override(QUERY_TYPES_V2_ONLY=query_type):
        builder_class = get_query_builder_class(query_type)

    assert (
        query_service_module.query_service_for_builder(
            query_type, builder_class, fallback
        )
        is direct_write_service
    )


def test_query_service_selection_is_query_type_aware(monkeypatch):
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.v2 import query_service as query_service_module

    fallback = object.__new__(AnalyticsQueryService)
    monkeypatch.setattr(
        query_service_module,
        "V2AnalyticsQueryService",
        lambda: pytest.fail("mismatched query type must not select CH25 service"),
    )

    with _override(QUERY_TYPES_V2_ONLY="TRACE_LIST,SPAN_LIST"):
        trace_builder_class = get_query_builder_class("TRACE_LIST")

    assert (
        query_service_module.query_service_for_builder(
            "SPAN_LIST", trace_builder_class, fallback
        )
        is fallback
    )


def test_dispatched_v1_builder_keeps_legacy_service():
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.v2.query_service import query_service_for_builder

    fallback = object.__new__(AnalyticsQueryService)
    with _override():
        builder_class = get_query_builder_class("TRACE_LIST")

    assert query_service_for_builder("TRACE_LIST", builder_class, fallback) is fallback
