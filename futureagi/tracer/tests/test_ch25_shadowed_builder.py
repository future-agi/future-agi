"""
Pin the ShadowedQueryBuilder (multi-phase shadow wrapper).

Views that build multiple SQL phases (build → build_count_query →
build_content_query) hold ONE ShadowedQueryBuilder instance and call
.run("method") per phase. The wrapper handles the v1↔v2 routing per call.

Fakes again so tests run without a CH cluster.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django.test.utils import override_settings


class _FakeV1:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def build(self):
        return ("SELECT 'v1_main' AS s", {"p": 1})

    def build_count_query(self):
        return ("SELECT 'v1_count' AS s, count() FROM x", {})

    def build_content_query(self, ids):
        return (f"SELECT 'v1_content' AS s, id FROM x WHERE id IN ({len(ids)})", {})

    @classmethod
    def pivot_eval(cls, rows):
        return f"v1_pivoted_{len(rows)}"


class _FakeV2(_FakeV1):
    def build(self):
        return ("SELECT 'v2_main' AS s", {"p": 1})

    def build_count_query(self):
        return ("SELECT 'v2_count' AS s, count() FROM x", {})

    def build_content_query(self, ids):
        return (f"SELECT 'v2_content' AS s, id FROM x WHERE id IN ({len(ids)})", {})


@pytest.fixture(autouse=True)
def register_fake_dispatch_entry():
    from tracer.services.clickhouse.v2.dispatch import _BuilderEntry, _REGISTRY
    _REGISTRY["FAKE_MULTI"] = _BuilderEntry(
        v1_module="tracer.tests.test_ch25_shadowed_builder",
        v1_class="_FakeV1",
        v2_module="tracer.tests.test_ch25_shadowed_builder",
        v2_class="_FakeV2",
    )
    yield
    del _REGISTRY["FAKE_MULTI"]


def _override_routing(**kw):
    base = {
        "QUERY_TYPES_V2_PRIMARY": "",
        "QUERY_TYPES_V2_ONLY":    "",
        "QUERY_TYPES_SHADOW":     "",
        "QUERY_TYPES_DISABLED":   "",
    }
    base.update(kw)
    return override_settings(CLICKHOUSE_V2=base)


class _FakeAnalytics:
    def __init__(self):
        self.executed = []

    def execute_ch_query(self, sql, params, timeout_ms=None):
        self.executed.append(sql)
        return f"result_for[{sql}]"


def test_multi_phase_v1_only_uses_v1_for_every_phase():
    from tracer.services.clickhouse.v2.shadowed_builder import ShadowedQueryBuilder
    a = _FakeAnalytics()
    with _override_routing():  # default V1_ONLY
        ctx = ShadowedQueryBuilder(
            query_type="FAKE_MULTI", builder_kwargs={}, analytics=a,
        )
        r1 = ctx.run("build")
        r2 = ctx.run("build_count_query")
        r3 = ctx.run("build_content_query", ["a", "b", "c"])
    assert "result_for[SELECT 'v1_main' AS s]" == r1
    assert "result_for[SELECT 'v1_count' AS s, count() FROM x]" == r2
    assert "result_for[SELECT 'v1_content' AS s, id FROM x WHERE id IN (3)]" == r3
    # Three executions, all v1
    assert len(a.executed) == 3
    assert all("v1_" in s for s in a.executed)


def test_multi_phase_v2_only_uses_v2_for_every_phase():
    from tracer.services.clickhouse.v2.shadowed_builder import ShadowedQueryBuilder
    a = _FakeAnalytics()
    with _override_routing(QUERY_TYPES_V2_ONLY="FAKE_MULTI"):
        ctx = ShadowedQueryBuilder(
            query_type="FAKE_MULTI", builder_kwargs={}, analytics=a,
        )
        r1 = ctx.run("build")
        r2 = ctx.run("build_count_query")
    assert "v2_main" in r1
    assert "v2_count" in r2
    assert all("v2_" in s for s in a.executed)


def test_multi_phase_shadow_runs_both_returns_v1_per_call():
    from tracer.services.clickhouse.v2.shadowed_builder import ShadowedQueryBuilder
    a = _FakeAnalytics()
    with _override_routing(QUERY_TYPES_SHADOW="FAKE_MULTI"):
        ctx = ShadowedQueryBuilder(
            query_type="FAKE_MULTI", builder_kwargs={}, analytics=a,
        )
        r = ctx.run("build")
    # User-facing return is v1
    assert "v1_main" in r
    # But BOTH executions hit analytics — one v1 SQL, one v2 SQL.
    assert len(a.executed) == 2
    assert sum("v1_main" in s for s in a.executed) == 1
    assert sum("v2_main" in s for s in a.executed) == 1


def test_multi_phase_v2_primary_returns_v2():
    from tracer.services.clickhouse.v2.shadowed_builder import ShadowedQueryBuilder
    a = _FakeAnalytics()
    with _override_routing(QUERY_TYPES_V2_PRIMARY="FAKE_MULTI"):
        ctx = ShadowedQueryBuilder(
            query_type="FAKE_MULTI", builder_kwargs={}, analytics=a,
        )
        r = ctx.run("build")
    assert "v2_main" in r
    # Both v1 and v2 were executed (shadow safety net)
    assert len(a.executed) == 2


def test_classmethod_passthrough():
    """Static helpers (pivot_eval_results etc.) work via classmethod_call."""
    from tracer.services.clickhouse.v2.shadowed_builder import ShadowedQueryBuilder
    a = _FakeAnalytics()
    with _override_routing():
        ctx = ShadowedQueryBuilder(
            query_type="FAKE_MULTI", builder_kwargs={}, analytics=a,
        )
        result = ctx.classmethod_call("pivot_eval", [1, 2, 3, 4])
    assert result == "v1_pivoted_4"


def test_disabled_mode_returns_none():
    from tracer.services.clickhouse.v2.shadowed_builder import ShadowedQueryBuilder
    a = _FakeAnalytics()
    with _override_routing(QUERY_TYPES_DISABLED="FAKE_MULTI"):
        ctx = ShadowedQueryBuilder(
            query_type="FAKE_MULTI", builder_kwargs={}, analytics=a,
        )
        assert ctx.run("build") is None
    assert a.executed == []


def test_shadow_fails_loudly_if_v1_raises():
    from tracer.services.clickhouse.v2.shadowed_builder import ShadowedQueryBuilder

    class _AnalyticsRaisesOnV1:
        def __init__(self): self.calls = 0
        def execute_ch_query(self, sql, params, timeout_ms=None):
            self.calls += 1
            if "v1_" in sql:
                raise RuntimeError("v1 boom")
            return "v2_result"

    a = _AnalyticsRaisesOnV1()
    with _override_routing(QUERY_TYPES_SHADOW="FAKE_MULTI"):
        ctx = ShadowedQueryBuilder(
            query_type="FAKE_MULTI", builder_kwargs={}, analytics=a,
        )
        with pytest.raises(RuntimeError, match="v1 failed"):
            ctx.run("build")


def test_v2_primary_falls_back_to_v1_on_v2_failure():
    from tracer.services.clickhouse.v2.shadowed_builder import ShadowedQueryBuilder

    class _AnalyticsRaisesOnV2:
        def __init__(self): self.calls = 0
        def execute_ch_query(self, sql, params, timeout_ms=None):
            self.calls += 1
            if "v2_" in sql:
                raise RuntimeError("v2 boom")
            return "v1_result"

    a = _AnalyticsRaisesOnV2()
    with _override_routing(QUERY_TYPES_V2_PRIMARY="FAKE_MULTI"):
        ctx = ShadowedQueryBuilder(
            query_type="FAKE_MULTI", builder_kwargs={}, analytics=a,
        )
        r = ctx.run("build")
    assert r == "v1_result"
    assert a.calls == 2  # both attempted
