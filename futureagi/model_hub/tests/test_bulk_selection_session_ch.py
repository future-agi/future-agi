"""CH-only dispatch wiring for the session bulk-select resolver.

Session is ClickHouse-only (no PG tracer fallback). These unit-test the wiring
the deleted PG-seeded suite can't: all-history injection, score-filter
intersection, cap+1, failure-propagates, and the pre-CH scope guards (simulator
carve-out, workspace mismatch, cross-org). Real CH parity lives in the
``ch_rehearsal`` Slice-F suite; the builder + CH client are faked here.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from model_hub.models.ai_model import AIModel
from model_hub.services.bulk_selection import (
    ResolveResult,
    _resolve_session_ids_clickhouse,
    resolve_filtered_session_ids,
)
from tracer.models.project import Project, ProjectSourceChoices


class _FakeResult:
    def __init__(self, rows):
        self.data = rows


def _install_fake_session_builder(monkeypatch, *, rows, capture):
    class _FakeBuilder:
        def __init__(self, *, filters, **kwargs):
            capture["filters"] = filters

        def build(self):
            return "SELECT session_id FROM spans", {}

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


def _rows(*sids):
    return [{"session_id": s} for s in sids]


# ---------------------------------------------------------------------------
# _resolve_session_ids_clickhouse wiring
# ---------------------------------------------------------------------------
def test_injects_all_history_1971_when_no_time_filter(monkeypatch):
    capture: dict = {}
    _install_fake_session_builder(monkeypatch, rows=_rows("s1"), capture=capture)

    _resolve_session_ids_clickhouse(
        project_id="p1", non_score_filters=[], score_filters=[],
        exclude_ids=set(), organization=None, cap=10,
    )

    injected = [f for f in capture["filters"] if f.get("column_id") == "start_time"]
    assert len(injected) == 1
    # 1970 would underflow the score subqueries' `- INTERVAL 1 DAY`.
    assert injected[0]["filter_config"]["filter_value"][0].startswith("1971")


def test_excludes_and_cap_plus_one(monkeypatch):
    _install_fake_session_builder(monkeypatch, rows=_rows("a", "b", "c"), capture={})
    res = _resolve_session_ids_clickhouse(
        project_id="p1", non_score_filters=[], score_filters=[],
        exclude_ids={"b"}, organization=None, cap=10,
    )
    assert res.ids == ["a", "c"]

    res2 = _resolve_session_ids_clickhouse(
        project_id="p1", non_score_filters=[], score_filters=[],
        exclude_ids=set(), organization=None, cap=2,
    )
    assert res2.truncated is True
    assert res2.total_matching == 3


def test_score_filters_intersected_via_annotation_score_table(monkeypatch):
    _install_fake_session_builder(monkeypatch, rows=_rows("a", "b", "c"), capture={})
    # Score-label filters are intersected in PG against the annotation Score
    # table (not a tracer table); stub it to keep only "a".
    monkeypatch.setattr(
        "model_hub.services.bulk_selection._apply_session_score_filters_pg",
        lambda ids, score_filters: ["a"],
    )
    res = _resolve_session_ids_clickhouse(
        project_id="p1", non_score_filters=[],
        score_filters=[{"column_id": "label-1", "filter_config": {}}],
        exclude_ids=set(), organization=None, cap=10,
    )
    assert res.ids == ["a"]


def test_ch_failure_propagates(monkeypatch):
    class _Boom:
        def __init__(self, **kwargs):
            pass

        def build(self):
            raise RuntimeError("CH down")

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
        lambda name: _Boom,
    )
    with capture_logs() as logs:
        with pytest.raises(RuntimeError, match="CH down"):
            _resolve_session_ids_clickhouse(
                project_id="p1", non_score_filters=[], score_filters=[],
                exclude_ids=set(), organization=None, cap=10,
            )
    # The failure must leave a breadcrumb for log-based alerting before it raises.
    assert any(
        e["event"] == "bulk_selection_resolve_session_ch_query_failed"
        and e["log_level"] == "warning"
        for e in logs
    )


# ---------------------------------------------------------------------------
# resolve_filtered_session_ids — dispatch / guards (DB-backed)
# ---------------------------------------------------------------------------
@pytest.fixture
def observe_project(db, organization, workspace):
    return Project.objects.create(
        name="BulkSel Session CH Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


class TestDispatch:
    def test_ch_result_returned(self, monkeypatch, observe_project, organization):
        monkeypatch.setattr(
            "model_hub.services.bulk_selection._session_score_label_ids",
            lambda pid: set(),
        )
        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_session_ids_clickhouse",
            lambda **kwargs: ResolveResult(
                ids=["s-1"], total_matching=1, truncated=False
            ),
        )
        res = resolve_filtered_session_ids(
            project_id=observe_project.id, filters=[], organization=organization
        )
        assert res.ids == ["s-1"]

    def test_ch_failure_propagates(self, monkeypatch, observe_project, organization):
        def _boom(**kwargs):
            raise RuntimeError("CH down")

        monkeypatch.setattr(
            "model_hub.services.bulk_selection._session_score_label_ids",
            lambda pid: set(),
        )
        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_session_ids_clickhouse", _boom
        )
        with pytest.raises(RuntimeError, match="CH down"):
            resolve_filtered_session_ids(
                project_id=observe_project.id, filters=[], organization=organization
            )

    def test_simulator_project_returns_empty_before_ch(
        self, monkeypatch, organization, workspace, db
    ):
        def _boom(**kwargs):
            raise AssertionError("CH must not be reached for a simulator project")

        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_session_ids_clickhouse", _boom
        )
        sim = Project.objects.create(
            name="Sim Project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            source=ProjectSourceChoices.SIMULATOR.value,
        )
        res = resolve_filtered_session_ids(
            project_id=sim.id, filters=[], organization=organization
        )
        assert res.ids == []
        assert res.total_matching == 0

    def test_cross_org_raises_before_ch(self, monkeypatch, organization):
        def _boom(**kwargs):
            raise AssertionError("CH must not be reached for cross-org")

        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_session_ids_clickhouse", _boom
        )
        from accounts.models.organization import Organization

        other_org = Organization.objects.create(name="Other Sess Org")
        other_project = Project.objects.create(
            name="Other Sess Project",
            organization=other_org,
            workspace=None,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        with pytest.raises(Project.DoesNotExist):
            resolve_filtered_session_ids(
                project_id=other_project.id, filters=[], organization=organization
            )
