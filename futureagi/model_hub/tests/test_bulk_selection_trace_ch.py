"""CH-dispatch wiring for the trace + voice bulk-select resolvers.

``resolve_filtered_trace_ids`` is ClickHouse-only (the PG tracer tables are being
dropped), so these unit-test the wiring the deleted PG-seeded suite used to
cover: all-history injection, unique cap+1 truncation, exclusion refill,
current-state filtering, voice/simulator flag passthrough, fail-closed
propagation, the workspace early-return, and the cross-org guard. ClickHouse is
faked so the wiring and bounded-read contract are deterministic.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

import pytest
from structlog.testing import capture_logs

from model_hub.models.ai_model import AIModel
from model_hub.services.bulk_selection import (
    BulkTraceSelectionIncomplete,
    ResolveResult,
    _resolve_trace_ids_clickhouse,
    _resolve_voice_call_ids_clickhouse,
    resolve_filtered_trace_ids,
)
from tracer.models.project import Project


class _FakeResult:
    def __init__(self, rows):
        self.data = rows


def _install_fake_trace_analytics(monkeypatch, handler, calls):
    """Run the bounded trace resolver against a deterministic fake CH."""

    class _FakeAnalytics:
        def execute_ch_query(self, query, params, timeout_ms=None, settings=None):
            call = {
                "query": query,
                "params": params,
                "timeout_ms": timeout_ms,
                "settings": settings,
            }
            calls.append(call)
            outcome = handler(call, len(calls) - 1)
            if isinstance(outcome, Exception):
                raise outcome
            return _FakeResult(outcome)

    monkeypatch.setattr(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService",
        _FakeAnalytics,
    )


def _install_fake_voice_builder(monkeypatch, *, rows, capture):
    """Patch VoiceCallListQueryBuilderV2 + AnalyticsQueryService for the voice
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
        "tracer.services.clickhouse.v2.query_builders.voice_call_list.VoiceCallListQueryBuilderV2",
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
# _resolve_trace_ids_clickhouse — unique cap, exclusion, latest state, failure
# ---------------------------------------------------------------------------
def test_trace_healthy_path_uses_unique_cap_plus_one_with_hard_budget(monkeypatch):
    calls = []
    _install_fake_trace_analytics(
        monkeypatch,
        lambda _call, _index: [{"trace_id": f"t{i}"} for i in range(3)],
        calls,
    )
    res = _resolve_trace_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=2,
        annotation_label_ids=[],
    )
    assert res.ids == ["t0", "t1"]
    assert res.truncated is True
    assert res.total_matching == 3
    assert len(calls) == 1
    assert "FROM spans FINAL" in calls[0]["query"]
    assert "GROUP BY trace_id" in calls[0]["query"]
    assert calls[0]["params"]["bulk_target"] == 3
    assert calls[0]["timeout_ms"] <= 250
    assert calls[0]["settings"]["timeout_overflow_mode"] == "throw"
    assert calls[0]["settings"]["read_overflow_mode"] == "throw"


def test_trace_exclusions_are_applied_before_cap_and_refilled(monkeypatch):
    calls = []
    _install_fake_trace_analytics(
        monkeypatch,
        lambda _call, _index: [
            {"trace_id": "t1"},
            {"trace_id": "t2"},
            {"trace_id": "t3"},
        ],
        calls,
    )
    res = _resolve_trace_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids={"t0"},
        cap=2,
        annotation_label_ids=[],
    )
    assert res.ids == ["t1", "t2"]
    assert res.total_matching == 3
    assert res.truncated is True
    assert "trace_id NOT IN %(bulk_excluded_trace_ids)s" in calls[0]["query"]
    assert calls[0]["params"]["bulk_excluded_trace_ids"] == ("t0",)


def test_trace_duplicate_rows_cannot_consume_unique_cap(monkeypatch):
    calls = []

    def _handler(_call, index):
        if index == 0:
            return TimeoutError("whole-window budget")
        if index == 1:
            # Defensive simulation of unmerged duplicate rows despite the
            # grouped seed contract. Only two unique IDs occupy three rows.
            return [
                {"trace_id": "t1"},
                {"trace_id": "t1"},
                {"trace_id": "t2"},
            ]
        return [{"trace_id": "t3"}]

    _install_fake_trace_analytics(monkeypatch, _handler, calls)
    res = _resolve_trace_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=2,
        annotation_label_ids=[],
    )
    assert res.ids == ["t1", "t2"]
    assert res.total_matching == 3
    assert res.truncated is True
    assert "GROUP BY trace_id" in calls[1]["query"]
    assert calls[2]["params"]["skip_trace_ids"] == ("t1", "t2")


def test_trace_candidate_probe_uses_latest_state_and_rejects_stale_match(
    monkeypatch,
):
    calls = []
    now = datetime.utcnow().replace(microsecond=0)
    time_filter = {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [now - timedelta(minutes=1), now],
        },
    }
    attr_filter = {
        "column_id": "mutable_status",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "live",
        },
    }

    def _handler(call, index):
        if index == 0:
            return TimeoutError("whole-window budget")
        if "max(start_time) AS bulk_order_start_time" in call["query"]:
            return [{"trace_id": "stale"}, {"trace_id": "live"}]
        # The current-state probe rejects the candidate whose old version was
        # the only one matching the mutable attribute.
        return [{"trace_id": "live"}]

    _install_fake_trace_analytics(monkeypatch, _handler, calls)
    res = _resolve_trace_ids_clickhouse(
        project_id="p1",
        filters=[time_filter, attr_filter],
        exclude_ids=set(),
        cap=2,
        annotation_label_ids=[],
    )
    assert res.ids == ["live"]
    assert res.truncated is False
    probe = next(call for call in calls if "SELECT DISTINCT trace_id" in call["query"])
    # Both the candidate root and the any-span attribute membership subquery
    # must collapse ReplacingMergeTree versions before testing is_deleted/attrs.
    assert probe["query"].count("FROM spans FINAL") >= 2
    assert probe["params"]["candidate_trace_ids"] == ("stale", "live")


def test_trace_root_attribute_filter_uses_scalar_seed_and_full_window_probe(
    monkeypatch,
):
    calls = []
    now = datetime.utcnow().replace(microsecond=0)
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
                "filter_value": "status_rejected",
            },
        },
    ]

    def _handler(_call, index):
        if index == 0:
            return TimeoutError("whole-window budget")
        return [{"trace_id": "matched"}]

    _install_fake_trace_analytics(monkeypatch, _handler, calls)
    res = _resolve_trace_ids_clickhouse(
        project_id="p1",
        filters=filters,
        exclude_ids=set(),
        cap=2,
        annotation_label_ids=[],
    )
    assert res.ids == ["matched"]
    assert res.truncated is False
    assert len(calls) == 3
    assert all("FINAL" not in call["query"] for call in calls)
    assert "final_status" not in calls[1]["query"]
    assert "LIMIT 1 BY grouped_trace_id" in calls[1]["query"]
    assert "final_status" in calls[2]["query"]
    assert calls[2]["params"]["candidate_trace_ids"] == ("matched",)


@pytest.mark.django_db
def test_trace_bulk_selection_real_ch_uses_latest_version_and_tombstone(monkeypatch):
    """FINAL must reject an old matching value and a deleted latest version."""

    from tracer.services.clickhouse.query_service import (
        AnalyticsQueryService as RealAnalyticsQueryService,
    )
    from tracer.services.clickhouse.v2 import get_reader, get_v2_config

    database = str(get_v2_config().get("database") or "")
    if not database.startswith("test_"):
        pytest.skip(
            "refusing to run the ClickHouse mutation fixture outside a test database"
        )

    project_id = uuid.uuid4()
    table = f"bulk_trace_latest_{uuid.uuid4().hex[:10]}"
    started_at = datetime.utcnow().replace(microsecond=0)
    matching_trace = "00000000-0000-0000-0000-000000000010"
    stale_trace = "00000000-0000-0000-0000-000000000020"
    deleted_trace = "00000000-0000-0000-0000-000000000030"
    duplicate_trace = "00000000-0000-0000-0000-000000000040"
    columns = [
        "project_id",
        "observation_type",
        "service_name",
        "start_time",
        "trace_id",
        "id",
        "parent_span_id",
        "name",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "is_deleted",
        "_version",
    ]

    with get_reader() as reader:
        ch = reader._client
        ch.command(
            f"""
            CREATE TABLE {table} (
                project_id UUID,
                observation_type LowCardinality(String),
                service_name LowCardinality(String),
                start_time DateTime64(6, 'UTC'),
                trace_id String,
                id String,
                parent_span_id String,
                name String,
                attrs_string Map(LowCardinality(String), String),
                attrs_number Map(LowCardinality(String), Float64),
                attrs_bool Map(LowCardinality(String), UInt8),
                is_deleted UInt8,
                _version UInt64
            )
            ENGINE = ReplacingMergeTree(_version, is_deleted)
            PARTITION BY toDate(start_time)
            ORDER BY (
                project_id, observation_type, service_name,
                toStartOfHour(start_time), trace_id, id
            )
            """
        )
        try:
            ch.command(f"SYSTEM STOP MERGES {table}")

            def _insert(
                trace_id,
                span_id,
                status,
                *,
                version,
                deleted=0,
            ):
                ch.insert(
                    table,
                    [
                        [
                            project_id,
                            "span",
                            "svc",
                            started_at,
                            trace_id,
                            span_id,
                            "",
                            "root",
                            {"final_status": status},
                            {},
                            {},
                            deleted,
                            version,
                        ]
                    ],
                    column_names=columns,
                )

            _insert(matching_trace, "root-match", "status_rejected", version=1)
            _insert(stale_trace, "root-stale", "status_rejected", version=1)
            _insert(stale_trace, "root-stale", "status_approved", version=2)
            _insert(deleted_trace, "root-deleted", "status_rejected", version=1)
            _insert(
                deleted_trace,
                "root-deleted",
                "status_rejected",
                version=2,
                deleted=1,
            )
            _insert(duplicate_trace, "root-dup-a", "status_rejected", version=1)
            _insert(duplicate_trace, "root-dup-b", "status_rejected", version=1)

            # Keep the fixture meaningful: the old pre-FINAL shape sees both
            # the stale value and the live row preceding the tombstone.
            naive = {
                row[0]
                for row in ch.query(
                    f"SELECT trace_id FROM {table} "
                    "WHERE is_deleted = 0 "
                    "AND attrs_string['final_status'] = 'status_rejected'"
                ).result_rows
            }
            assert stale_trace in naive
            assert deleted_trace in naive

            real_analytics = RealAnalyticsQueryService()

            class _IsolatedTableAnalytics:
                def execute_ch_query(
                    self,
                    query,
                    params=None,
                    timeout_ms=10_000,
                    settings=None,
                ):
                    isolated_query = re.sub(
                        r"\bspans\b",
                        table,
                        query,
                    )
                    return real_analytics.execute_ch_query(
                        isolated_query,
                        params,
                        timeout_ms=timeout_ms,
                        settings=settings,
                    )

            monkeypatch.setattr(
                "tracer.services.clickhouse.query_service.AnalyticsQueryService",
                _IsolatedTableAnalytics,
            )
            filters = [
                {
                    "column_id": "start_time",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            started_at - timedelta(minutes=1),
                            started_at + timedelta(minutes=1),
                        ],
                    },
                },
                {
                    "column_id": "final_status",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "status_rejected",
                    },
                },
            ]
            result = _resolve_trace_ids_clickhouse(
                project_id=project_id,
                filters=filters,
                exclude_ids=set(),
                cap=10,
                annotation_label_ids=[],
            )
            assert set(result.ids) == {matching_trace, duplicate_trace}
            assert len(result.ids) == 2
            assert result.truncated is False
        finally:
            ch.command(f"SYSTEM START MERGES {table}")
            ch.command(f"DROP TABLE IF EXISTS {table}")


def test_trace_ch_query_failure_propagates(monkeypatch):
    # CH is the sole backend — a failure must propagate, not resolve to empty.
    _install_fake_trace_analytics(
        monkeypatch,
        lambda _call, _index: RuntimeError("CH down"),
        [],
    )
    with capture_logs() as logs:
        with pytest.raises(RuntimeError, match="CH down"):
            _resolve_trace_ids_clickhouse(
                project_id="p1",
                filters=[],
                exclude_ids=set(),
                cap=10,
                annotation_label_ids=[],
            )
    # The failure must leave a breadcrumb for log-based alerting before it raises.
    assert any(
        e["event"] == "bulk_selection_resolve_trace_ch_query_failed"
        and e["log_level"] == "warning"
        for e in logs
    )


def test_trace_read_timeout_never_returns_partial_or_empty_success(monkeypatch):
    calls = []
    _install_fake_trace_analytics(
        monkeypatch,
        lambda _call, _index: TimeoutError("read budget"),
        calls,
    )
    with pytest.raises(
        BulkTraceSelectionIncomplete,
        match="Could not prove the complete trace selection",
    ):
        _resolve_trace_ids_clickhouse(
            project_id="p1",
            filters=[],
            exclude_ids=set(),
            cap=10,
            annotation_label_ids=[],
        )
    # One exact fast-path attempt followed by bounded retries of the same
    # newest slice down to the one-minute floor.
    assert len(calls) >= 2
    assert all(call["timeout_ms"] <= 750 for call in calls)


def test_trace_future_skewed_root_keeps_bulk_selection_incomplete(monkeypatch):
    now = datetime(2026, 7, 31, 3)

    class _FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return now

    calls = []

    def _handler(_call, index):
        if index == 0:
            return TimeoutError("whole-window budget")
        if index == 1:
            return [{"future_tail_row": 1}]
        raise AssertionError("fallback must not run after a future-tail row")

    monkeypatch.setattr(
        "model_hub.services.bulk_selection.datetime",
        _FrozenDateTime,
    )
    _install_fake_trace_analytics(monkeypatch, _handler, calls)
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

    with pytest.raises(BulkTraceSelectionIncomplete):
        _resolve_trace_ids_clickhouse(
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
    assert "parent_span_id IS NULL" in tail_call["query"]
    assert tail_call["params"]["future_tail_start"] == now + timedelta(minutes=5)
    assert tail_call["params"]["future_tail_end"] == now + timedelta(hours=4)
    assert tail_call["timeout_ms"] == 100
    assert tail_call["settings"]["max_threads"] == 1
    assert tail_call["settings"]["max_memory_usage"] == 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# _resolve_voice_call_ids_clickhouse — bounded trace candidate engine
# ---------------------------------------------------------------------------
def test_voice_truncation_and_flag_passthrough(monkeypatch):
    capture: dict = {}

    def _fake_trace_resolver(**kwargs):
        capture.update(kwargs)
        return ResolveResult(ids=["v0", "v1"], total_matching=3, truncated=True)

    monkeypatch.setattr(
        "model_hub.services.bulk_selection._resolve_trace_ids_clickhouse",
        _fake_trace_resolver,
    )
    res = _resolve_voice_call_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=2,
        remove_simulation_calls=True,
        annotation_label_ids=[],
    )
    assert capture["root_observation_type"] == "conversation"
    assert callable(capture["candidate_post_filter"])
    assert res.ids == ["v0", "v1"]
    assert res.truncated is True
    assert res.total_matching == 3


def test_voice_simulator_rejections_refill_under_shared_trace_budget(monkeypatch):
    calls = []

    def _handler(_call, index):
        if index == 0:
            return [
                {"trace_id": "v0"},
                {"trace_id": "v1"},
                {"trace_id": "v2"},
            ]
        return [{"trace_id": "v3"}, {"trace_id": "v4"}]

    _install_fake_trace_analytics(monkeypatch, _handler, calls)
    monkeypatch.setattr(
        "model_hub.services.bulk_selection._filter_out_simulator_calls_ch",
        lambda ids, project_id, analytics, **kwargs: [
            trace_id for trace_id in ids if trace_id not in {"v0", "v1"}
        ],
    )

    result = _resolve_voice_call_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=2,
        remove_simulation_calls=True,
        annotation_label_ids=[],
    )

    assert result.ids == ["v2", "v3"]
    assert result.truncated is True
    assert result.total_matching == 3
    assert "observation_type = %(bulk_root_observation_type)s" in calls[0]["query"]
    assert calls[0]["params"]["bulk_root_observation_type"] == "conversation"
    assert set(calls[1]["params"]["skip_trace_ids"]) >= {"v0", "v1", "v2"}
    assert all(call["timeout_ms"] <= 750 for call in calls[1:])


def test_voice_ch_query_failure_propagates(monkeypatch):
    monkeypatch.setattr(
        "model_hub.services.bulk_selection._resolve_trace_ids_clickhouse",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("CH down")),
    )
    with capture_logs() as logs:
        with pytest.raises(RuntimeError, match="CH down"):
            _resolve_voice_call_ids_clickhouse(
                project_id="p1",
                filters=[],
                exclude_ids=set(),
                cap=10,
                remove_simulation_calls=False,
                annotation_label_ids=[],
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
        injected = [f for f in capture["filters"] if f.get("column_id") == "start_time"]
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
