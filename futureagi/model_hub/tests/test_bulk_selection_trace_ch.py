"""CH-dispatch wiring for the trace + voice bulk-select resolvers.

``resolve_filtered_trace_ids`` is ClickHouse-only (the PG tracer tables are being
dropped), so these unit-test the wiring the deleted PG-seeded suite used to
cover: the all-history time injection, the cap+1 truncation sentinel (the trace
``build()`` has no internal +1, unlike voice), exclude, voice/simulator flag
passthrough, fail-closed propagation, the workspace early-return, and the
cross-org guard. The builder SQL itself lives in the tracer builder tests and
real CH parity in the ``ch_rehearsal`` suite — here the builder + CH client are
faked so the *wiring* is asserted deterministically without a live ClickHouse.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from model_hub.models.ai_model import AIModel
from model_hub.services.bulk_selection import (
    ResolveResult,
    _resolve_trace_ids_clickhouse,
    _resolve_voice_call_ids_clickhouse,
    resolve_filtered_trace_ids,
)
from tracer.models.project import Project


class _FakeResult:
    def __init__(self, rows):
        self.data = rows


def _install_fake_trace_builder(monkeypatch, *, rows, capture):
    """Patch TraceListQueryBuilder + AnalyticsQueryService so
    ``_resolve_trace_ids_clickhouse`` runs against a fake CH returning ``rows``.
    ``capture`` records the builder constructor kwargs (page_size, filters)."""

    class _FakeBuilder:
        def __init__(self, **kwargs):
            capture.update(kwargs)

        def build(self):
            return "SELECT trace_id FROM traces", {}

    class _FakeAnalytics:
        def execute_ch_query(self, query, params, timeout_ms=None):
            return _FakeResult(rows)

    monkeypatch.setattr(
        "tracer.services.clickhouse.query_builders.trace_list.TraceListQueryBuilder",
        _FakeBuilder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService",
        _FakeAnalytics,
    )


def _install_fake_voice_builder(monkeypatch, *, rows, capture):
    """Patch VoiceCallListQueryBuilder + AnalyticsQueryService for the voice
    resolver, and neutralize the simulator post-filter (a second CH read)."""

    class _FakeBuilder:
        def __init__(self, **kwargs):
            capture.update(kwargs)

        def build(self):
            return "SELECT trace_id FROM spans", {}

    class _FakeAnalytics:
        def execute_ch_query(self, query, params, timeout_ms=None):
            return _FakeResult(rows)

    monkeypatch.setattr(
        "tracer.services.clickhouse.query_builders.VoiceCallListQueryBuilder",
        _FakeBuilder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService",
        _FakeAnalytics,
    )
    monkeypatch.setattr(
        "model_hub.services.bulk_selection._filter_out_simulator_calls_ch",
        lambda ids, project_id, analytics: ids,
    )


# ---------------------------------------------------------------------------
# _resolve_trace_ids_clickhouse — cap+1 sentinel, exclude, failure
# ---------------------------------------------------------------------------
def test_trace_requests_cap_plus_one_page(monkeypatch):
    # The trace build() LIMIT is exactly page_size (no internal +1, unlike
    # voice), so the resolver MUST request cap+1 or a >cap add silently caps at
    # cap instead of reporting truncation (→ selection_too_large upstream).
    capture: dict = {}
    _install_fake_trace_builder(
        monkeypatch,
        rows=[{"trace_id": f"t{i}"} for i in range(11)],
        capture=capture,
    )
    res = _resolve_trace_ids_clickhouse(
        project_id="p1", filters=[], exclude_ids=set(), cap=10,
        annotation_label_ids=[],
    )
    assert capture["page_size"] == 11  # cap + 1, not cap
    assert res.truncated is True
    assert res.total_matching == 11


def test_trace_cap_sentinel_survives_exclusion(monkeypatch):
    # An excluded sentinel row drops the list to cap; truncated must stay True
    # (more non-excluded rows may lie beyond the fetched window).
    _install_fake_trace_builder(
        monkeypatch,
        rows=[{"trace_id": f"t{i}"} for i in range(11)],
        capture={},
    )
    res = _resolve_trace_ids_clickhouse(
        project_id="p1", filters=[], exclude_ids={"t0"}, cap=10,
        annotation_label_ids=[],
    )
    assert res.ids == [f"t{i}" for i in range(1, 11)]
    assert res.total_matching == 11
    assert res.truncated is True


def test_trace_ch_query_failure_propagates(monkeypatch):
    # CH is the sole backend — a failure must propagate, not resolve to empty.
    class _Boom:
        def __init__(self, **kwargs):
            pass

        def build(self):
            raise RuntimeError("CH down")

    monkeypatch.setattr(
        "tracer.services.clickhouse.query_builders.trace_list.TraceListQueryBuilder",
        _Boom,
    )
    with capture_logs() as logs:
        with pytest.raises(RuntimeError, match="CH down"):
            _resolve_trace_ids_clickhouse(
                project_id="p1", filters=[], exclude_ids=set(), cap=10,
                annotation_label_ids=[],
            )
    # The failure must leave a breadcrumb for log-based alerting before it raises.
    assert any(
        e["event"] == "bulk_selection_resolve_trace_ch_query_failed"
        and e["log_level"] == "warning"
        for e in logs
    )


# ---------------------------------------------------------------------------
# _resolve_voice_call_ids_clickhouse — voice build() has its own internal +1
# ---------------------------------------------------------------------------
def test_voice_truncation_and_flag_passthrough(monkeypatch):
    # The voice build() adds LIMIT cap+1 internally, so the resolver passes
    # page_size=cap; remove_simulation_calls must reach the builder.
    capture: dict = {}
    _install_fake_voice_builder(
        monkeypatch,
        rows=[{"trace_id": f"v{i}"} for i in range(3)],
        capture=capture,
    )
    res = _resolve_voice_call_ids_clickhouse(
        project_id="p1", filters=[], exclude_ids=set(), cap=2,
        remove_simulation_calls=True, annotation_label_ids=[],
    )
    assert capture["page_size"] == 2  # voice adds its own +1
    assert capture["remove_simulation_calls"] is True
    assert res.ids == ["v0", "v1"]
    assert res.truncated is True
    assert res.total_matching == 3


def test_voice_ch_query_failure_propagates(monkeypatch):
    class _Boom:
        def __init__(self, **kwargs):
            pass

        def build(self):
            raise RuntimeError("CH down")

    monkeypatch.setattr(
        "tracer.services.clickhouse.query_builders.VoiceCallListQueryBuilder",
        _Boom,
    )
    with capture_logs() as logs:
        with pytest.raises(RuntimeError, match="CH down"):
            _resolve_voice_call_ids_clickhouse(
                project_id="p1", filters=[], exclude_ids=set(), cap=10,
                remove_simulation_calls=False, annotation_label_ids=[],
            )
    # The failure must leave a breadcrumb for log-based alerting before it raises.
    assert any(
        e["event"] == "bulk_selection_resolve_voice_ch_query_failed"
        and e["log_level"] == "warning"
        for e in logs
    )


# ---------------------------------------------------------------------------
# resolve_filtered_trace_ids — all-history injection + dispatch
# (DB-backed for the PG project/workspace scope guards)
# ---------------------------------------------------------------------------
@pytest.fixture
def observe_project(db, organization, workspace):
    return Project.objects.create(
        name="BulkSel Trace CH Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _capture_trace_resolver(monkeypatch, capture):
    def _fake(**kwargs):
        capture.update(kwargs)
        return ResolveResult(ids=["ch-1"], total_matching=1, truncated=False)

    monkeypatch.setattr(
        "model_hub.services.bulk_selection._resolve_trace_ids_clickhouse", _fake
    )


class TestDispatch:
    def test_injects_all_history_when_no_time_filter(
        self, monkeypatch, observe_project, organization
    ):
        capture: dict = {}
        _capture_trace_resolver(monkeypatch, capture)
        resolve_filtered_trace_ids(
            project_id=observe_project.id, filters=[], organization=organization
        )
        injected = [
            f for f in capture["filters"] if f.get("column_id") == "start_time"
        ]
        assert len(injected) == 1
        assert injected[0]["filter_config"]["filter_value"][0].startswith("1971")

    def test_does_not_inject_when_explicit_time_filter(
        self, monkeypatch, observe_project, organization
    ):
        capture: dict = {}
        _capture_trace_resolver(monkeypatch, capture)
        explicit = {
            "column_id": "start_time",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": ["2024-01-01T00:00:00", "2024-02-01T00:00:00"],
            },
        }
        resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[explicit],
            organization=organization,
        )
        time_filters = [
            f for f in capture["filters"] if f.get("column_id") == "start_time"
        ]
        assert time_filters == [explicit]  # passed through, no 1971 injection

    def test_voice_dispatches_to_voice_resolver(
        self, monkeypatch, observe_project, organization
    ):
        capture: dict = {}

        def _fake_voice(**kwargs):
            capture.update(kwargs)
            return ResolveResult(ids=["voice-1"], total_matching=1, truncated=False)

        def _fake_trace(**kwargs):
            raise AssertionError("a voice call must not hit the trace resolver")

        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_voice_call_ids_clickhouse",
            _fake_voice,
        )
        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_trace_ids_clickhouse",
            _fake_trace,
        )
        res = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            is_voice_call=True,
            remove_simulation_calls=True,
        )
        assert res.ids == ["voice-1"]
        assert capture["remove_simulation_calls"] is True

    def test_ch_empty_returns_empty_no_pg_fallback(
        self, monkeypatch, observe_project, organization
    ):
        # An empty CH result is authoritative — there is no PG fallback.
        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_trace_ids_clickhouse",
            lambda **kwargs: ResolveResult(ids=[], total_matching=0, truncated=False),
        )
        res = resolve_filtered_trace_ids(
            project_id=observe_project.id, filters=[], organization=organization
        )
        assert res.ids == []
        assert res.total_matching == 0

    def test_ch_failure_propagates(self, monkeypatch, observe_project, organization):
        def _boom(**kwargs):
            raise RuntimeError("CH down")

        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_trace_ids_clickhouse", _boom
        )
        with pytest.raises(RuntimeError, match="CH down"):
            resolve_filtered_trace_ids(
                project_id=observe_project.id, filters=[], organization=organization
            )

    def test_workspace_mismatch_short_circuits_before_ch(
        self, monkeypatch, observe_project, organization, user
    ):
        def _boom(**kwargs):
            raise AssertionError("CH must not be reached on workspace mismatch")

        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_trace_ids_clickhouse", _boom
        )
        from accounts.models.workspace import Workspace

        other_ws = Workspace.objects.create(
            name="Other Trace WS",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        res = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            workspace=other_ws,
        )
        assert res.ids == []
        assert res.total_matching == 0

    def test_cross_org_project_raises_before_ch(self, monkeypatch, organization):
        def _boom(**kwargs):
            raise AssertionError("CH must not be reached for a cross-org project")

        monkeypatch.setattr(
            "model_hub.services.bulk_selection._resolve_trace_ids_clickhouse", _boom
        )
        from accounts.models.organization import Organization

        other_org = Organization.objects.create(name="Other Trace Org")
        other_project = Project.objects.create(
            name="Other Trace Project",
            organization=other_org,
            workspace=None,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        with pytest.raises(Project.DoesNotExist):
            resolve_filtered_trace_ids(
                project_id=other_project.id,
                filters=[],
                organization=organization,
            )

    def test_raises_when_user_scoped_filter_without_user(
        self, observe_project, organization
    ):
        # my_annotations / annotator filters need a user; guarded before any read.
        with pytest.raises(ValueError, match="user-scoped"):
            resolve_filtered_trace_ids(
                project_id=observe_project.id,
                filters=[
                    {
                        "column_id": "my_annotations",
                        "filter_config": {
                            "filter_type": "text",
                            "filter_op": "equals",
                            "filter_value": "x",
                        },
                    }
                ],
                organization=organization,
                user=None,
            )
