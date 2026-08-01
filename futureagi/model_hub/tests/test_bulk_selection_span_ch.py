"""CH-dispatch wiring for the span bulk-select resolver.

Unit-tests the pieces ``_force_pg_fallback`` hides in
``test_bulk_selection_span.py``: the all-history time injection, CH-first /
PG-fallback branching, the workspace early-return, exclude, and cap+1
truncation. The builder SQL itself is covered in
``tracer/tests/test_span_list_builder_comprehensive.py`` and real CH parity in
the ``ch_rehearsal`` suite — here the builder + CH client are faked so the
*wiring* is asserted deterministically without a live ClickHouse.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from structlog.testing import capture_logs

from model_hub.models.ai_model import AIModel
from model_hub.services.bulk_selection import (
    BulkSelectionIncomplete,
    ResolveResult,
    _all_history_time_filter,
    _resolve_span_ids_clickhouse,
    resolve_filtered_span_ids,
)
from tracer.models.project import Project


class _FakeResult:
    def __init__(self, rows):
        self.data = rows


def _install_fake_builder(monkeypatch, *, rows, capture):
    """Patch SPAN_LIST dispatch + AnalyticsQueryService so
    ``_resolve_span_ids_clickhouse`` runs against a fake CH returning ``rows``.
    ``capture`` records the filters / limit the builder saw."""

    class _FakeBuilder:
        def __init__(self, *, filters, **kwargs):
            capture["filters"] = filters
            capture["kwargs"] = kwargs

        def build_id_query(self, *, limit=None, **kwargs):
            capture["limit"] = limit
            capture["build_kwargs"] = kwargs
            return "SELECT id FROM spans", {}

    class _FakeAnalytics:
        def execute_ch_query(self, query, params, timeout_ms=None, settings=None):
            capture["timeout_ms"] = timeout_ms
            capture["settings"] = settings
            return _FakeResult(rows)

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
        lambda name: _FakeBuilder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService",
        _FakeAnalytics,
    )


# ---------------------------------------------------------------------------
# _all_history_time_filter
# ---------------------------------------------------------------------------
def test_all_history_filter_uses_1971_not_1970():
    f = _all_history_time_filter()
    assert f["column_id"] == "start_time"
    assert f["filter_config"]["filter_op"] == "between"
    lo, hi = f["filter_config"]["filter_value"]
    # 1970-01-01 - INTERVAL 1 DAY underflows the CH DateTime epoch; 1971 is safe.
    assert lo.startswith("1971-01-01")
    assert hi.startswith("2099-")


# ---------------------------------------------------------------------------
# _resolve_span_ids_clickhouse — all-history injection
# ---------------------------------------------------------------------------
def test_injects_all_history_when_no_time_filter(monkeypatch):
    capture: dict = {}
    _install_fake_builder(monkeypatch, rows=[{"id": "s1"}], capture=capture)

    _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=10,
        annotation_label_ids=[],
    )

    injected = [f for f in capture["filters"] if f.get("column_id") == "start_time"]
    assert len(injected) == 1
    assert injected[0]["filter_config"]["filter_value"][0].startswith("1971")
    assert capture["limit"] == 11  # cap + 1 sentinel
    assert capture["build_kwargs"]["latest_state"] is True
    assert capture["timeout_ms"] <= 250
    assert capture["settings"]["timeout_overflow_mode"] == "throw"


def test_does_not_inject_when_explicit_time_filter(monkeypatch):
    capture: dict = {}
    _install_fake_builder(monkeypatch, rows=[{"id": "s1"}], capture=capture)
    explicit = {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": ["2024-01-01T00:00:00", "2024-02-01T00:00:00"],
        },
    }

    _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=[explicit],
        exclude_ids=set(),
        cap=10,
        annotation_label_ids=[],
    )

    time_filters = [f for f in capture["filters"] if f.get("column_id") == "start_time"]
    assert time_filters == [explicit]  # passed through, no 1971 injection


# ---------------------------------------------------------------------------
# _resolve_span_ids_clickhouse — exclude + cap + failure
# ---------------------------------------------------------------------------
def test_excludes_ids(monkeypatch):
    capture: dict = {}
    _install_fake_builder(monkeypatch, rows=[{"id": "a"}, {"id": "c"}], capture=capture)
    res = _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids={"b"},
        cap=10,
        annotation_label_ids=[],
    )
    assert res.ids == ["a", "c"]
    assert res.truncated is False
    exclusion = next(
        item for item in capture["filters"] if item.get("column_id") == "span_id"
    )
    assert exclusion["filter_config"]["filter_op"] == "not_in"
    assert exclusion["filter_config"]["filter_value"] == ["b"]


def test_exclusion_concentrated_first_page_is_refilled_before_limit(monkeypatch):
    capture: dict = {}
    universe = ["a", "b", "c", "d", "e", "f"]

    class _FakeBuilder:
        def __init__(self, *, filters, **kwargs):
            capture["filters"] = filters

        def build_id_query(self, *, limit=None, **kwargs):
            skip = next(
                item["filter_config"]["filter_value"]
                for item in capture["filters"]
                if item.get("column_id") == "span_id"
            )
            return "SELECT id FROM spans FINAL", {
                "skip": tuple(skip),
                "limit": limit,
            }

    class _FakeAnalytics:
        def execute_ch_query(self, query, params, timeout_ms=None, settings=None):
            rows = [
                {"id": span_id} for span_id in universe if span_id not in params["skip"]
            ][: params["limit"]]
            return _FakeResult(rows)

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
        lambda name: _FakeBuilder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService",
        _FakeAnalytics,
    )

    res = _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids={"a", "b", "c"},
        cap=2,
        annotation_label_ids=[],
    )

    assert res.ids == ["d", "e"]
    assert res.truncated is True
    assert res.total_matching == 3


def test_cap_plus_one_truncation(monkeypatch):
    # cap=2, CH returns 3 (the cap+1 sentinel) → truncated, capped to 2.
    _install_fake_builder(
        monkeypatch, rows=[{"id": "a"}, {"id": "b"}, {"id": "c"}], capture={}
    )
    res = _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=2,
        annotation_label_ids=[],
    )
    assert res.ids == ["a", "b"]
    assert res.truncated is True
    assert res.total_matching == 3


def test_final_status_uses_scalar_latest_id_page_with_prelimit_exclusion(
    monkeypatch,
):
    now = datetime.utcnow().replace(microsecond=0)
    capture = {}

    class _ScalarBuilder:
        def __init__(self, *, filters, **kwargs):
            self.filters = filters
            capture["filters"] = filters

        def supports_latest_attribute_page(self):
            return True

        def build_latest_attribute_id_page(self, **kwargs):
            capture["scalar_kwargs"] = kwargs
            return "SELECT id FROM spans GROUP BY id", {
                "skip": tuple(sorted(kwargs["exclude_span_ids"]))
            }

        def build_id_query(self, **kwargs):  # pragma: no cover - safety tripwire
            raise AssertionError("final_status must not use the FINAL id builder")

    class _Analytics:
        def execute_ch_query(self, query, params, timeout_ms=None, settings=None):
            capture["query"] = query
            capture["params"] = params
            return _FakeResult([{"id": "matched"}])

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
        lambda name: _ScalarBuilder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService",
        _Analytics,
    )
    filters = [
        {
            "column_id": "start_time",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [now - timedelta(minutes=1), now],
            },
        },
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "approved",
            },
        },
    ]

    result = _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=filters,
        exclude_ids={"excluded"},
        cap=2,
        annotation_label_ids=[],
    )

    assert result.ids == ["matched"]
    assert result.truncated is False
    assert "FINAL" not in capture["query"]
    assert capture["params"]["skip"] == ("excluded",)
    assert capture["scalar_kwargs"]["limit"] == 3


def test_ch_query_failure_propagates(monkeypatch):
    # CH is the sole backend — a failure must propagate, not silently resolve to
    # empty (there is no PG fallback).
    class _Boom:
        def __init__(self, **kwargs):
            pass

        def build_id_query(self, *, limit=None, **kwargs):
            raise RuntimeError("CH down")

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
        lambda name: _Boom,
    )
    with capture_logs() as logs:
        with pytest.raises(RuntimeError, match="CH down"):
            _resolve_span_ids_clickhouse(
                project_id="p1",
                filters=[],
                exclude_ids=set(),
                cap=10,
                annotation_label_ids=[],
            )
    # The failure must leave a breadcrumb for log-based alerting before it raises.
    assert any(
        e["event"] == "bulk_selection_resolve_span_ch_query_failed"
        and e["log_level"] == "warning"
        for e in logs
    )


def test_read_budget_failure_never_returns_partial_or_empty(monkeypatch):
    class _Builder:
        def __init__(self, **kwargs):
            pass

        def build_id_query(self, **kwargs):
            return "SELECT id FROM spans FINAL", {}

    class _TimeoutAnalytics:
        def execute_ch_query(self, *args, **kwargs):
            raise TimeoutError("bounded read")

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
        lambda name: _Builder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService",
        _TimeoutAnalytics,
    )

    with pytest.raises(
        BulkSelectionIncomplete,
        match="Could not prove the complete span selection",
    ):
        _resolve_span_ids_clickhouse(
            project_id="p1",
            filters=[],
            exclude_ids=set(),
            cap=10,
            annotation_label_ids=[],
        )


def test_future_skewed_span_keeps_bulk_selection_incomplete(monkeypatch):
    now = datetime(2026, 7, 31, 3)

    class _FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return now

    calls = []

    class _Builder:
        def __init__(self, **kwargs):
            pass

        def build_id_query(self, **kwargs):
            return "SELECT id FROM spans FINAL", {}

    class _Analytics:
        def execute_ch_query(
            self,
            query,
            params,
            timeout_ms=None,
            settings=None,
        ):
            calls.append(
                {
                    "query": query,
                    "params": dict(params),
                    "timeout_ms": timeout_ms,
                    "settings": settings,
                }
            )
            if len(calls) == 1:
                raise TimeoutError("whole-window budget")
            if len(calls) == 2:
                return _FakeResult([{"future_tail_row": 1}])
            raise AssertionError("fallback must not run after a future-tail row")

    monkeypatch.setattr(
        "model_hub.services.bulk_selection.datetime",
        _FrozenDateTime,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
        lambda name: _Builder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService",
        _Analytics,
    )
    filters = [
        {
            "column_id": "start_time",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    now - timedelta(days=7),
                    now + timedelta(hours=4),
                ],
            },
        }
    ]

    with pytest.raises(BulkSelectionIncomplete):
        _resolve_span_ids_clickhouse(
            project_id="p1",
            filters=filters,
            exclude_ids=set(),
            cap=10,
            annotation_label_ids=[],
        )

    assert len(calls) == 2
    tail_call = calls[1]
    assert "FROM spans" in tail_call["query"]
    assert "FINAL" not in tail_call["query"]
    assert "parent_span_id" not in tail_call["query"]
    assert tail_call["params"]["future_tail_start"] == now + timedelta(minutes=5)
    assert tail_call["params"]["future_tail_end"] == now + timedelta(hours=4)
    assert tail_call["timeout_ms"] == 100
    assert tail_call["settings"]["max_threads"] == 1
    assert tail_call["settings"]["max_memory_usage"] == 64 * 1024 * 1024


def test_slice_keeps_whole_window_filter_scope(monkeypatch):
    now = datetime.utcnow().replace(microsecond=0)
    whole_window = {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [now - timedelta(minutes=2), now],
        },
    }
    annotation_filter = {
        "column_id": "has_annotation",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": True,
        },
    }
    builder_calls = []
    query_calls = []

    class _Builder:
        def __init__(self, *, filters, **kwargs):
            self.filters = filters

        def build_id_query(self, **kwargs):
            builder_calls.append({"filters": self.filters, "kwargs": kwargs})
            return (
                "SELECT id, start_time AS eval_order_start_time "
                "FROM spans FINAL WHERE project_id = %(project_id)s",
                {"project_id": "p1"},
            )

    class _Analytics:
        def execute_ch_query(self, query, params, timeout_ms=None, settings=None):
            query_calls.append({"query": query, "params": params})
            if len(query_calls) == 1:
                raise TimeoutError("whole window")
            return _FakeResult([])

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
        lambda name: _Builder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService",
        _Analytics,
    )

    result = _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=[whole_window, annotation_filter],
        exclude_ids=set(),
        cap=2,
        annotation_label_ids=[],
    )

    assert result.ids == []
    assert builder_calls[1]["kwargs"]["limit"] is None
    assert whole_window in builder_calls[1]["filters"]
    assert annotation_filter in builder_calls[1]["filters"]
    assert "%(bulk_slice_start)s" in query_calls[1]["query"]
    assert query_calls[1]["params"]["bulk_slice_start"] == now - timedelta(minutes=2)
    assert query_calls[1]["params"]["bulk_slice_end"] == now


# ---------------------------------------------------------------------------
# resolve_filtered_span_ids — CH-only dispatch (DB-backed for the PG scope guards)
# ---------------------------------------------------------------------------
@pytest.fixture
def observe_project(db, organization, workspace):
    return Project.objects.create(
        name="BulkSel Span CH Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


class TestDispatch:
    def test_ch_result_is_returned(self, monkeypatch, observe_project, organization):
        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_span_ids_clickhouse",
            lambda **kwargs: ResolveResult(
                ids=["ch-1", "ch-2"], total_matching=2, truncated=False
            ),
        )
        res = resolve_filtered_span_ids(
            project_id=observe_project.id, filters=[], organization=organization
        )
        assert res.ids == ["ch-1", "ch-2"]

    def test_ch_empty_returns_empty_no_pg_fallback(
        self, monkeypatch, observe_project, organization
    ):
        # An empty CH result is authoritative — there is no PG fallback to add
        # phantom rows.
        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_span_ids_clickhouse",
            lambda **kwargs: ResolveResult(ids=[], total_matching=0, truncated=False),
        )
        res = resolve_filtered_span_ids(
            project_id=observe_project.id, filters=[], organization=organization
        )
        assert res.ids == []
        assert res.total_matching == 0

    def test_ch_failure_propagates(self, monkeypatch, observe_project, organization):
        def _boom(**kwargs):
            raise RuntimeError("CH down")

        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_span_ids_clickhouse", _boom
        )
        with pytest.raises(RuntimeError, match="CH down"):
            resolve_filtered_span_ids(
                project_id=observe_project.id, filters=[], organization=organization
            )

    def test_workspace_mismatch_short_circuits_before_ch(
        self, monkeypatch, observe_project, organization, user
    ):
        # A non-matching workspace must return empty WITHOUT dispatching to CH.
        def _boom(**kwargs):
            raise AssertionError("CH must not be reached on workspace mismatch")

        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_span_ids_clickhouse", _boom
        )
        from accounts.models.workspace import Workspace

        other_ws = Workspace.objects.create(
            name="Other WS",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        res = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            workspace=other_ws,
        )
        assert res.ids == []
        assert res.total_matching == 0

    def test_cross_org_project_raises_before_ch(self, monkeypatch, organization):
        # Cross-tenant: a project in another org must not resolve — guarded at
        # the PG project lookup, before any CH read.
        def _boom(**kwargs):
            raise AssertionError("CH must not be reached for a cross-org project")

        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_span_ids_clickhouse", _boom
        )
        from accounts.models.organization import Organization

        other_org = Organization.objects.create(name="Other Span Org")
        other_project = Project.objects.create(
            name="Other Span Project",
            organization=other_org,
            workspace=None,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        with pytest.raises(Project.DoesNotExist):
            resolve_filtered_span_ids(
                project_id=other_project.id,
                filters=[],
                organization=organization,
            )
