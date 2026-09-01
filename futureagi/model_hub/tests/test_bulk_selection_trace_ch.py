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

from types import SimpleNamespace
from unittest import mock

import pytest
from structlog.testing import capture_logs

from model_hub.models.ai_model import AIModel
from model_hub.services.bulk_selection import (
    BulkSelectionReadIncomplete,
    ResolveResult,
    _bounded_bulk_classify_batch_size,
    _bounded_bulk_worst_case_query_count,
    _resolve_trace_ids_clickhouse,
    _resolve_voice_call_ids_clickhouse,
    _supports_bounded_bulk_prefix,
    _use_authoritative_eval_source,
    resolve_filtered_trace_ids,
)
from tracer.models.project import Project


def _patched_empty_eval_metadata():
    empty_configs = mock.MagicMock()
    empty_configs.exists.return_value = False
    empty_configs.filter.return_value = empty_configs
    empty_configs.values_list.return_value = []
    config_manager = mock.MagicMock()
    config_manager.filter.return_value = empty_configs

    empty_templates = mock.MagicMock()
    empty_templates.values.return_value.first.return_value = None
    template_manager = mock.MagicMock()
    template_manager.filter.return_value = empty_templates
    return (
        mock.patch(
            "tracer.models.custom_eval_config.CustomEvalConfig.objects",
            config_manager,
        ),
        mock.patch(
            "model_hub.models.evals_metric.EvalTemplate.no_workspace_objects",
            template_manager,
        ),
    )


class _FakeResult:
    def __init__(self, rows):
        self.data = rows


def _install_fake_trace_builder(
    monkeypatch,
    *,
    rows,
    capture,
    complete=True,
    has_more=False,
    error_code=None,
    supports=True,
):
    """Patch the explicit V2 trace builder/service so
    ``_resolve_trace_ids_clickhouse`` runs against a fake CH returning ``rows``.
    ``capture`` records the builder constructor kwargs (page_size, filters)."""

    class _FakeBuilder:
        def __init__(self, **kwargs):
            capture.update(kwargs)

        def supports_bounded_filter_scan(self):
            return supports

        def bounded_filter_degraded_error_code(self):
            return None

        def build(self):
            capture["legacy_build"] = True
            return "SELECT trace_id FROM traces", {}

    class _FakeAnalytics:
        def execute_ch_query(self, query, params, timeout_ms=None):
            return _FakeResult(rows)

    def _fake_bounded_read(**kwargs):
        capture["bounded_read"] = kwargs
        return SimpleNamespace(
            rows=rows,
            has_more=has_more,
            complete=complete,
            error_code=error_code,
        )

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_builders.trace_list.TraceListQueryBuilderV2",
        _FakeBuilder,
    )
    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
        _fake_bounded_read,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        _FakeAnalytics,
    )


def _install_fake_voice_builder(
    monkeypatch, *, rows, capture, has_more=False, supports=True
):
    """Patch the explicit V2 voice builder/service and bounded selector."""

    class _FakeBuilder:
        def __init__(self, **kwargs):
            capture.update(kwargs)

        def supports_bounded_filter_scan(self):
            return supports

        def bounded_filter_degraded_error_code(self):
            return None

        @staticmethod
        def recommended_filter_classify_batch_size():
            return 50

    class _FakeAnalytics:
        def execute_ch_query(self, query, params, timeout_ms=None):
            return _FakeResult(rows)

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_builders.voice_call_list.VoiceCallListQueryBuilderV2",
        _FakeBuilder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        _FakeAnalytics,
    )
    monkeypatch.setattr(
        "model_hub.services.bulk_selection._read_bounded_bulk_page",
        lambda **kwargs: (
            capture.update({"bounded_read": kwargs})
            or SimpleNamespace(rows=rows, has_more=has_more)
        ),
    )


# ---------------------------------------------------------------------------
# _resolve_trace_ids_clickhouse — cap+1 sentinel, exclude, failure
# ---------------------------------------------------------------------------
def test_trace_requests_cap_plus_one_bounded_prefix(monkeypatch):
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
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=10,
        annotation_label_ids=[],
    )
    assert capture["page_size"] == 11  # builder contract remains cap + 1
    assert capture["bounded_identity_only"] is True
    assert capture["bounded_bulk_scan"] is True
    assert capture["bounded_internal_scan"] is True
    assert capture["bounded_read"]["page_number"] == 0
    assert capture["bounded_read"]["page_size"] == 11
    assert capture["bounded_read"]["classify_batch_size"] == 200
    assert res.truncated is True
    assert res.total_matching == 11


def test_trace_cap_sentinel_survives_exclusion(monkeypatch):
    # The bounded resolver overscans by the exclusion count, so an exact raw
    # result of cap+1 with one excluded row proves cap post-exclusion rows and
    # must not produce a false selection_too_large response.
    capture: dict = {}
    _install_fake_trace_builder(
        monkeypatch,
        rows=[{"trace_id": f"t{i}"} for i in range(11)],
        capture=capture,
    )
    res = _resolve_trace_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids={"t0"},
        cap=10,
        annotation_label_ids=[],
    )
    assert res.ids == [f"t{i}" for i in range(1, 11)]
    assert capture["bounded_read"]["page_size"] == 12
    assert res.total_matching == 10
    assert res.truncated is False


def test_trace_10001_raw_minus_excluded_is_exactly_10000(monkeypatch):
    capture: dict = {}
    _install_fake_trace_builder(
        monkeypatch,
        rows=[{"trace_id": f"t{i}"} for i in range(10_001)],
        capture=capture,
    )

    res = _resolve_trace_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids={"t0"},
        cap=10_000,
        annotation_label_ids=[],
    )

    assert capture["bounded_read"]["page_size"] == 10_002
    assert len(res.ids) == 10_000
    assert "t0" not in res.ids
    assert res.total_matching == 10_000
    assert res.truncated is False


def test_trace_more_than_950_exclusions_stays_on_bounded_path(monkeypatch):
    capture: dict = {}
    _install_fake_trace_builder(
        monkeypatch,
        rows=[{"trace_id": f"t{i}"} for i in range(11)],
        capture=capture,
    )

    res = _resolve_trace_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids={f"excluded-{i}" for i in range(951)},
        cap=10_000,
        annotation_label_ids=[],
    )

    assert capture["bounded_identity_only"] is True
    assert capture["bounded_bulk_scan"] is True
    assert capture["bounded_read"]["page_size"] == 10_952
    assert "legacy_build" not in capture
    assert res.truncated is False


def test_trace_exclusion_prefix_budget_matches_per_seed_classifier_batches():
    # A 12,799-row page asks the selector to prove row 12,800. Sixty-four
    # 200-row seeds plus one 200-row classifier per seed use exactly 128
    # queries. One more raw row needs a 65th seed/classifier pair.
    assert _bounded_bulk_worst_case_query_count(12_799) == 128
    assert _bounded_bulk_worst_case_query_count(12_800) == 130
    assert _supports_bounded_bulk_prefix(cap=10_000, exclude_count=2_798) is True
    assert _supports_bounded_bulk_prefix(cap=10_000, exclude_count=2_799) is False


def test_voice_classifier_batch_scales_only_to_fit_finite_query_budget():
    assert (
        _bounded_bulk_classify_batch_size(cap=25, exclude_count=0, preferred=50) == 50
    )
    assert (
        _bounded_bulk_classify_batch_size(
            cap=10_000,
            exclude_count=0,
            preferred=50,
        )
        == 130
    )
    assert (
        _bounded_bulk_classify_batch_size(
            cap=10_000,
            exclude_count=2_798,
            preferred=50,
        )
        == 200
    )


def test_trace_over_budget_exclusions_fail_closed_without_legacy_build(monkeypatch):
    capture: dict = {}
    _install_fake_trace_builder(monkeypatch, rows=[], capture=capture)

    with pytest.raises(BulkSelectionReadIncomplete, match="selection_prefix_too_large"):
        _resolve_trace_ids_clickhouse(
            project_id="p1",
            filters=[],
            exclude_ids={f"excluded-{i}" for i in range(2_799)},
            cap=10_000,
            annotation_label_ids=[],
        )

    assert "legacy_build" not in capture
    assert "bounded_read" not in capture


def test_trace_cap_over_10000_fails_closed_without_legacy_build(monkeypatch):
    capture: dict = {}
    _install_fake_trace_builder(monkeypatch, rows=[], capture=capture)

    with pytest.raises(BulkSelectionReadIncomplete, match="selection_prefix_too_large"):
        _resolve_trace_ids_clickhouse(
            project_id="p1",
            filters=[],
            exclude_ids=set(),
            cap=10_001,
            annotation_label_ids=[],
        )

    assert "legacy_build" not in capture
    assert "bounded_read" not in capture


def test_trace_ch_query_failure_propagates(monkeypatch):
    # CH is the sole backend — a failure must propagate, not resolve to empty.
    class _Builder:
        def __init__(self, **kwargs):
            pass

        def supports_bounded_filter_scan(self):
            return True

        def bounded_filter_degraded_error_code(self):
            return None

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_builders.trace_list.TraceListQueryBuilderV2",
        _Builder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        lambda: object(),
    )
    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("CH down")),
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


def test_trace_incomplete_nonempty_prefix_fails_closed(monkeypatch):
    _install_fake_trace_builder(
        monkeypatch,
        rows=[{"trace_id": "partial-must-not-escape"}],
        capture={},
        complete=False,
        error_code="scan_budget_exceeded",
    )

    with pytest.raises(RuntimeError, match="scan_budget_exceeded"):
        _resolve_trace_ids_clickhouse(
            project_id="p1",
            filters=[],
            exclude_ids=set(),
            cap=10,
            annotation_label_ids=[],
        )


def test_trace_complete_empty_prefix_is_authoritative(monkeypatch):
    _install_fake_trace_builder(monkeypatch, rows=[], capture={})
    result = _resolve_trace_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=10,
        annotation_label_ids=[],
    )
    assert result == ResolveResult(ids=[], total_matching=0, truncated=False)


def test_trace_multi_filter_payload_reaches_same_bounded_builder(monkeypatch):
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": ["2026-01-01", "2026-07-01"],
            },
        },
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ["Rejected", "Accepted"],
            },
        },
        {
            "column_id": "status",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "ERROR",
            },
        },
    ]
    capture: dict = {}
    _install_fake_trace_builder(monkeypatch, rows=[{"trace_id": "t1"}], capture=capture)

    _resolve_trace_ids_clickhouse(
        project_id="p1",
        filters=filters,
        exclude_ids=set(),
        cap=25,
        annotation_label_ids=[],
    )

    assert capture["filters"] == filters
    assert capture["bounded_read"]["filters"] == filters


def test_trace_time_only_filter_uses_bounded_internal_scan(monkeypatch):
    capture: dict = {}
    _install_fake_trace_builder(
        monkeypatch,
        rows=[{"trace_id": "t1"}],
        capture=capture,
    )
    result = _resolve_trace_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=25,
        annotation_label_ids=[],
    )
    assert result.ids == ["t1"]
    assert capture["bounded_internal_scan"] is True
    assert "bounded_read" in capture
    assert "legacy_build" not in capture


def test_trace_unsupported_bounded_shape_fails_closed_without_legacy_build(monkeypatch):
    capture: dict = {}
    _install_fake_trace_builder(
        monkeypatch,
        rows=[{"trace_id": "must-not-escape"}],
        capture=capture,
        supports=False,
    )

    with pytest.raises(BulkSelectionReadIncomplete, match="unsupported_bounded_filter"):
        _resolve_trace_ids_clickhouse(
            project_id="p1",
            filters=[{"column_id": "unsupported"}],
            exclude_ids=set(),
            cap=25,
            annotation_label_ids=[],
        )

    assert "legacy_build" not in capture
    assert "bounded_read" not in capture


@pytest.mark.parametrize(
    ("residual_filter", "expected_fragment"),
    [
        (
            {
                "column_id": "00000000-0000-4000-8000-000000000091",
                "filter_config": {
                    "col_type": "EVAL_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0.5,
                },
            },
            "SELECT toUUID('00000000-0000-0000-0000-000000000000')",
        ),
        (
            {
                "column_id": "00000000-0000-4000-8000-000000000092",
                "filter_config": {
                    "col_type": "ANNOTATION",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "approved",
                },
            },
            "model_hub_score",
        ),
        (
            {
                "column_id": "user_id",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": "customer",
                },
            },
            "tracer_enduser",
        ),
    ],
)
def test_real_trace_builder_keeps_candidate_scoped_residual_filters(
    residual_filter, expected_fragment
):
    from tracer.services.clickhouse.query_builders.trace_list import (
        TraceListQueryBuilder,
    )

    time_filter = {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": ["2026-01-01", "2026-07-01"],
        },
    }
    builder = TraceListQueryBuilder(
        project_id="00000000-0000-4000-8000-000000000001",
        filters=[time_filter, residual_filter],
        annotation_label_ids=["00000000-0000-4000-8000-000000000092"],
        bounded_identity_only=True,
        bounded_internal_scan=False,
        bounded_bulk_scan=True,
    )

    config_patch, template_patch = _patched_empty_eval_metadata()
    with config_patch, template_patch:
        assert builder.supports_bounded_filter_scan() is True
        assert builder.recommended_filter_classify_batch_size() == 200
        query, params = builder.build_filter_match_query(["trace-candidate"])
    assert expected_fragment in query
    assert "candidate_trace_ids" in query
    assert params["candidate_trace_ids"] == ("trace-candidate",)


# ---------------------------------------------------------------------------
# _resolve_voice_call_ids_clickhouse — bounded direct-write V2 selection
# ---------------------------------------------------------------------------
def test_voice_truncation_and_flag_passthrough(monkeypatch):
    # The bounded prefix needs cap+1 and simulator classification stays at its
    # candidate-scoped fifty-row ceiling.
    capture: dict = {}
    _install_fake_voice_builder(
        monkeypatch,
        rows=[{"trace_id": f"v{i}"} for i in range(3)],
        capture=capture,
    )
    res = _resolve_voice_call_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=2,
        remove_simulation_calls=True,
        annotation_label_ids=[],
    )
    assert capture["page_size"] == 3
    assert capture["remove_simulation_calls"] is True
    assert capture["bounded_read"]["classify_batch_size"] == 50
    assert capture["bounded_read"]["key_field"] == "trace_id"
    assert res.ids == ["v0", "v1"]
    assert res.truncated is True
    assert res.total_matching == 3


def test_voice_default_10000_cap_fits_bounded_simulator_query_budget(monkeypatch):
    capture: dict = {}
    _install_fake_voice_builder(monkeypatch, rows=[], capture=capture)

    result = _resolve_voice_call_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=10_000,
        remove_simulation_calls=True,
        annotation_label_ids=[],
    )

    assert result == ResolveResult(ids=[], total_matching=0, truncated=False)
    assert capture["bounded_read"]["cap"] == 10_000
    assert capture["bounded_read"]["classify_batch_size"] == 130


def test_voice_ch_query_failure_propagates(monkeypatch):
    class _Boom:
        def __init__(self, **kwargs):
            pass

        def supports_bounded_filter_scan(self):
            return True

        def bounded_filter_degraded_error_code(self):
            return None

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_builders.voice_call_list.VoiceCallListQueryBuilderV2",
        _Boom,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        lambda: object(),
    )
    monkeypatch.setattr(
        "model_hub.services.bulk_selection._read_bounded_bulk_page",
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


@pytest.mark.unit
def test_voice_simulator_classifier_emits_only_direct_write_columns():
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    filters = [
        {
            "column_id": "start_time",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    "2026-07-01T00:00:00Z",
                    "2026-08-01T00:00:00Z",
                ],
            },
        }
    ]
    builder = _use_authoritative_eval_source(
        VoiceCallListQueryBuilderV2(
            project_id="00000000-0000-4000-8000-000000000001",
            filters=filters,
            remove_simulation_calls=True,
        )
    )

    query, params = builder.build_filter_match_query(["trace-candidate"])
    normalized = " ".join(query.split())

    assert "JSONExtractRaw( attributes_extra, 'raw_log' )" in normalized
    assert "JSONExtractString( attributes_extra, 'raw_log' )" in normalized
    assert "argMax(attrs_string, _version) AS latest_span_attr_str" in normalized
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in normalized
    assert "attributes_extra AS span_attributes_raw" not in normalized
    assert "_peerdb_version" not in query
    assert "_peerdb_is_deleted" not in query
    assert params["candidate_trace_ids"] == ("trace-candidate",)


@pytest.mark.unit
def test_bulk_v2_filter_uses_configured_authoritative_eval_table(settings):
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    builder = _use_authoritative_eval_source(
        TraceListQueryBuilderV2(
            project_id="00000000-0000-4000-8000-000000000001",
            bounded_identity_only=True,
            bounded_bulk_scan=True,
        )
    )
    filter_builder = builder._FILTER_BUILDER_CLS(
        project_id="00000000-0000-4000-8000-000000000001"
    )
    where, _ = filter_builder.translate(
        [
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]
    )

    assert "FROM tracer_eval_logger AS eval_scan" in where
    assert "ORDER BY eval_scan._peerdb_version DESC" in where
    assert "latest_eval._peerdb_is_deleted = 0" in where
    assert "(latest_eval.deleted = 0 OR latest_eval.deleted IS NULL)" in where
    assert "tracer_eval_logger_v2" not in where


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
