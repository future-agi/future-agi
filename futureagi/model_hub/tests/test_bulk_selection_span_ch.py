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

import pytest

from model_hub.models.ai_model import AIModel
from model_hub.services.bulk_selection import (
    ResolveResult,
    _resolve_span_ids_clickhouse,
    _span_all_history_filter,
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

        def build_id_query(self, *, limit=None):
            capture["limit"] = limit
            return "SELECT id FROM spans", {}

    class _FakeAnalytics:
        def execute_ch_query(self, query, params, timeout_ms=None):
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
# _span_all_history_filter
# ---------------------------------------------------------------------------
def test_all_history_filter_uses_1971_not_1970():
    f = _span_all_history_filter()
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
        project_id="p1", filters=[], exclude_ids=set(), cap=10,
        annotation_label_ids=[],
    )

    injected = [f for f in capture["filters"] if f.get("column_id") == "start_time"]
    assert len(injected) == 1
    assert injected[0]["filter_config"]["filter_value"][0].startswith("1971")
    assert capture["limit"] == 11  # cap + 1 sentinel


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
        project_id="p1", filters=[explicit], exclude_ids=set(), cap=10,
        annotation_label_ids=[],
    )

    time_filters = [
        f for f in capture["filters"] if f.get("column_id") == "start_time"
    ]
    assert time_filters == [explicit]  # passed through, no 1971 injection


# ---------------------------------------------------------------------------
# _resolve_span_ids_clickhouse — exclude + cap + failure
# ---------------------------------------------------------------------------
def test_excludes_ids(monkeypatch):
    _install_fake_builder(
        monkeypatch, rows=[{"id": "a"}, {"id": "b"}, {"id": "c"}], capture={}
    )
    res = _resolve_span_ids_clickhouse(
        project_id="p1", filters=[], exclude_ids={"b"}, cap=10,
        annotation_label_ids=[],
    )
    assert res.ids == ["a", "c"]
    assert res.truncated is False


def test_cap_plus_one_truncation(monkeypatch):
    # cap=2, CH returns 3 (the cap+1 sentinel) → truncated, capped to 2.
    _install_fake_builder(
        monkeypatch, rows=[{"id": "a"}, {"id": "b"}, {"id": "c"}], capture={}
    )
    res = _resolve_span_ids_clickhouse(
        project_id="p1", filters=[], exclude_ids=set(), cap=2,
        annotation_label_ids=[],
    )
    assert res.ids == ["a", "b"]
    assert res.truncated is True
    assert res.total_matching == 3


def test_ch_query_failure_propagates(monkeypatch):
    # CH is the sole backend — a failure must propagate, not silently resolve to
    # empty (there is no PG fallback).
    class _Boom:
        def __init__(self, **kwargs):
            pass

        def build_id_query(self, *, limit=None):
            raise RuntimeError("CH down")

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
        lambda name: _Boom,
    )
    with pytest.raises(RuntimeError, match="CH down"):
        _resolve_span_ids_clickhouse(
            project_id="p1", filters=[], exclude_ids=set(), cap=10,
            annotation_label_ids=[],
        )


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

    def test_cross_org_project_raises_before_ch(
        self, monkeypatch, organization
    ):
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
