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
from types import SimpleNamespace
from unittest import mock

import pytest
from structlog.testing import capture_logs

from model_hub.models.ai_model import AIModel
from model_hub.services.bulk_selection import (
    BulkSelectionAmbiguousIdentity,
    BulkSelectionReadIncomplete,
    ResolveResult,
    _all_history_time_filter,
    _resolve_span_ids_clickhouse,
    resolve_filtered_span_ids,
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


def _install_fake_builder(
    monkeypatch,
    *,
    rows,
    capture,
    complete=True,
    has_more=False,
    error_code=None,
    supports=True,
):
    """Patch the explicit V2 span builder/service so
    ``_resolve_span_ids_clickhouse`` runs against a fake CH returning ``rows``.
    ``capture`` records the filters / limit the builder saw."""

    class _FakeBuilder:
        def __init__(self, *, filters, **kwargs):
            capture["filters"] = filters
            capture["kwargs"] = kwargs

        def supports_bounded_filter_scan(self):
            return supports

        def bounded_filter_degraded_error_code(self):
            return None

        def build_id_query(self, *, limit=None):
            capture["limit"] = limit
            return "SELECT id FROM spans", {}

    class _FakeAnalytics:
        def execute_ch_query(self, query, params, timeout_ms=None):
            return SimpleNamespace(data=rows)

    def _fake_bounded_read(**kwargs):
        capture["bounded_read"] = kwargs
        return SimpleNamespace(
            rows=rows,
            has_more=has_more,
            complete=complete,
            error_code=error_code,
        )

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_builders.span_list.SpanListQueryBuilderV2",
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
    upper_bound = datetime.fromisoformat(hi)
    assert datetime.utcnow() - timedelta(seconds=2) <= upper_bound <= datetime.utcnow()


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
    assert capture["kwargs"]["bounded_identity_only"] is True
    assert capture["kwargs"]["bounded_internal_scan"] is True
    assert capture["bounded_read"]["page_number"] == 0
    assert capture["bounded_read"]["page_size"] == 11
    assert capture["bounded_read"]["classify_batch_size"] == 200


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
    _install_fake_builder(
        monkeypatch, rows=[{"id": "a"}, {"id": "b"}, {"id": "c"}], capture={}
    )
    res = _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids={"b"},
        cap=10,
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
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=2,
        annotation_label_ids=[],
    )
    assert res.ids == ["a", "b"]
    assert res.truncated is True
    assert res.total_matching == 3


def test_excluded_raw_sentinel_does_not_false_truncate(monkeypatch):
    capture: dict = {}
    _install_fake_builder(
        monkeypatch,
        rows=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
        capture=capture,
    )

    res = _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids={"a"},
        cap=2,
        annotation_label_ids=[],
    )

    assert capture["bounded_read"]["page_size"] == 4
    assert res.ids == ["b", "c"]
    assert res.total_matching == 2
    assert res.truncated is False


def test_more_than_950_exclusions_stay_on_bounded_path(monkeypatch):
    capture: dict = {}
    _install_fake_builder(
        monkeypatch,
        rows=[{"id": "s1"}],
        capture=capture,
    )

    result = _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids={f"excluded-{i}" for i in range(951)},
        cap=10_000,
        annotation_label_ids=[],
    )

    assert result.ids == ["s1"]
    assert capture["bounded_read"]["page_size"] == 10_952
    assert "limit" not in capture


def test_over_budget_exclusions_fail_closed_without_legacy_build(monkeypatch):
    capture: dict = {}
    _install_fake_builder(monkeypatch, rows=[], capture=capture)

    with pytest.raises(BulkSelectionReadIncomplete, match="selection_prefix_too_large"):
        _resolve_span_ids_clickhouse(
            project_id="p1",
            filters=[],
            exclude_ids={f"excluded-{i}" for i in range(2_799)},
            cap=10_000,
            annotation_label_ids=[],
        )

    assert "limit" not in capture
    assert "bounded_read" not in capture


def test_cap_over_10000_fails_closed_without_legacy_build(monkeypatch):
    capture: dict = {}
    _install_fake_builder(monkeypatch, rows=[], capture=capture)

    with pytest.raises(BulkSelectionReadIncomplete, match="selection_prefix_too_large"):
        _resolve_span_ids_clickhouse(
            project_id="p1",
            filters=[],
            exclude_ids=set(),
            cap=10_001,
            annotation_label_ids=[],
        )

    assert "limit" not in capture
    assert "bounded_read" not in capture


def test_same_span_id_under_two_matching_traces_fails_closed(monkeypatch):
    _install_fake_builder(
        monkeypatch,
        rows=[
            {"id": "shared-span", "trace_id": "trace-a"},
            {"id": "shared-span", "trace_id": "trace-b"},
        ],
        capture={},
    )

    with pytest.raises(BulkSelectionAmbiguousIdentity, match="ambiguous_span_identity"):
        _resolve_span_ids_clickhouse(
            project_id="p1",
            filters=[],
            exclude_ids=set(),
            cap=10,
            annotation_label_ids=[],
        )


def test_ch_query_failure_propagates(monkeypatch):
    # CH is the sole backend — a failure must propagate, not silently resolve to
    # empty (there is no PG fallback).
    class _Builder:
        def __init__(self, **kwargs):
            pass

        def supports_bounded_filter_scan(self):
            return True

        def bounded_filter_degraded_error_code(self):
            return None

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_builders.span_list.SpanListQueryBuilderV2",
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


def test_span_incomplete_nonempty_prefix_fails_closed(monkeypatch):
    _install_fake_builder(
        monkeypatch,
        rows=[{"id": "partial-must-not-escape"}],
        capture={},
        complete=False,
        error_code="deadline_exceeded",
    )
    with pytest.raises(RuntimeError, match="deadline_exceeded"):
        _resolve_span_ids_clickhouse(
            project_id="p1",
            filters=[],
            exclude_ids=set(),
            cap=10,
            annotation_label_ids=[],
        )


def test_span_complete_empty_prefix_is_authoritative(monkeypatch):
    _install_fake_builder(monkeypatch, rows=[], capture={})
    result = _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=10,
        annotation_label_ids=[],
    )
    assert result == ResolveResult(ids=[], total_matching=0, truncated=False)


def test_span_multi_filter_payload_reaches_same_bounded_builder(monkeypatch):
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
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
    ]
    capture: dict = {}
    _install_fake_builder(monkeypatch, rows=[{"id": "s1"}], capture=capture)
    _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=filters,
        exclude_ids=set(),
        cap=25,
        annotation_label_ids=[],
    )
    assert capture["filters"] == filters
    assert capture["bounded_read"]["filters"] == filters


def test_span_time_only_filter_uses_bounded_internal_scan(monkeypatch):
    capture: dict = {}
    _install_fake_builder(
        monkeypatch,
        rows=[{"id": "s1"}],
        capture=capture,
    )
    result = _resolve_span_ids_clickhouse(
        project_id="p1",
        filters=[],
        exclude_ids=set(),
        cap=25,
        annotation_label_ids=[],
    )
    assert result.ids == ["s1"]
    assert capture["kwargs"]["bounded_internal_scan"] is True
    assert "bounded_read" in capture
    assert "limit" not in capture


def test_span_unsupported_bounded_shape_fails_closed_without_legacy_build(monkeypatch):
    capture: dict = {}
    _install_fake_builder(
        monkeypatch,
        rows=[{"id": "must-not-escape"}],
        capture=capture,
        supports=False,
    )

    with pytest.raises(BulkSelectionReadIncomplete, match="unsupported_bounded_filter"):
        _resolve_span_ids_clickhouse(
            project_id="p1",
            filters=[{"column_id": "unsupported"}],
            exclude_ids=set(),
            cap=25,
            annotation_label_ids=[],
        )

    assert "limit" not in capture
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
def test_real_span_builder_keeps_candidate_scoped_residual_filters(
    residual_filter, expected_fragment
):
    from tracer.services.clickhouse.query_builders.span_list import (
        SpanListQueryBuilder,
    )

    time_filter = {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": ["2026-01-01", "2026-07-01"],
        },
    }
    builder = SpanListQueryBuilder(
        project_id="00000000-0000-4000-8000-000000000001",
        filters=[time_filter, residual_filter],
        annotation_label_ids=["00000000-0000-4000-8000-000000000092"],
        bounded_identity_only=True,
        bounded_internal_scan=False,
    )

    config_patch, template_patch = _patched_empty_eval_metadata()
    with config_patch, template_patch:
        assert builder.supports_bounded_filter_scan() is True
        query, params = builder.build_filter_match_query(["span-candidate"])
    assert expected_fragment in query
    assert "candidate_span_ids" in query
    assert params["candidate_span_ids"] == ("span-candidate",)


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
