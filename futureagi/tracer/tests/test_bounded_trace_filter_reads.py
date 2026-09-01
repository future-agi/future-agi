from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from clickhouse_driver.util.escape import escape_params
from django.conf import settings
from django.test import override_settings

import tracer.selectors.trace_filter_reads as trace_filter_reads
from tracer.selectors.trace_filter_reads import (
    MAX_NUMBERED_PAGE_WORK_ROWS,
    BoundedFilterPage,
    FilterReadAttempt,
    bounded_numbered_page_depth_exceeded,
    numbered_page_depth_exceeded,
    read_bounded_filter_page,
)
from tracer.services.clickhouse.page_dedup import paginate_deduped
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import EvalFilterMetadata
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallListQueryBuilder,
)
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
START = datetime(2025, 1, 1)
END = START + timedelta(days=365)


def test_bounded_selector_operational_limits_are_settings_backed():
    assert (
        trace_filter_reads._QUERY_TIMEOUT_MS
        == settings.FILTER_SELECTOR_QUERY_TIMEOUT_MS
    )
    assert (
        trace_filter_reads._MAX_OPT_IN_QUERY_TIMEOUT_MS
        == settings.FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS
    )
    assert (
        trace_filter_reads._MAX_BUILDER_RECOMMENDED_QUERY_TIMEOUT_MS
        == settings.FILTER_SELECTOR_MAX_BUILDER_QUERY_TIMEOUT_MS
    )
    assert (
        MAX_NUMBERED_PAGE_WORK_ROWS
        == settings.FILTER_SELECTOR_MAX_NUMBERED_PAGE_WORK_ROWS
    )
    assert trace_filter_reads._READ_SETTINGS == {
        "max_threads": settings.FILTER_SELECTOR_MAX_THREADS,
        "max_block_size": settings.OBSERVABILITY_LIST_MAX_BLOCK_SIZE,
        "max_memory_usage": settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES,
        "max_bytes_to_read": settings.OBSERVABILITY_LIST_MAX_BYTES,
        "read_overflow_mode": "throw",
        "max_result_rows": 512,
        "result_overflow_mode": "throw",
    }


def _render_driver_sql(sql: str, params: dict[str, Any]) -> str:
    """Render parameters exactly as clickhouse-driver does before transport."""

    context = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))
    return sql % escape_params(params, context)


def _time_filter(start: datetime = START, end: datetime = END) -> dict[str, Any]:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def _short_time_filter() -> dict[str, Any]:
    return _time_filter(start=END - timedelta(minutes=30), end=END)


@pytest.mark.parametrize(
    ("period", "days"),
    [("today", 1), ("7d", 7), ("30d", 30), ("3m", 90), ("6m", 180), ("12m", 365)],
)
def test_interactive_list_builder_period_matrix_freezes_exact_window(period, days):
    del period
    end = datetime(2026, 8, 11, 12)
    start = end - timedelta(days=days)
    filters = [_time_filter(start=start, end=end)]
    builders = (
        TraceListQueryBuilderV2(project_id=PROJECT_ID, filters=filters),
        SpanListQueryBuilderV2(project_id=PROJECT_ID, filters=filters),
        VoiceCallListQueryBuilderV2(project_id=PROJECT_ID, filters=filters),
        SessionListQueryBuilderV2(project_id=PROJECT_ID, filters=filters),
    )

    for builder in builders:
        assert builder.parse_time_range(filters) == (start, end)


def _has_eval_filter(value: bool | str) -> dict[str, Any]:
    return {
        "column_id": "has_eval",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _has_annotation_filter(value: bool | str) -> dict[str, Any]:
    return {
        "column_id": "has_annotation",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _attribute_filter(
    key: str,
    value: object,
    *,
    filter_type: str = "text",
    operation: str = "equals",
) -> dict[str, Any]:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _system_filter(
    key: str,
    value: object,
    *,
    filter_type: str = "text",
    operation: str = "equals",
) -> dict[str, Any]:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": filter_type,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _annotation_filter(
    label_id: str,
    value: object,
    *,
    filter_type: str = "text",
    operation: str = "equals",
) -> dict[str, Any]:
    return {
        "column_id": label_id,
        "filter_config": {
            "col_type": "ANNOTATION",
            "filter_type": filter_type,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _annotator_filter(
    value: object,
    *,
    operation: str = "equals",
) -> dict[str, Any]:
    return {
        "column_id": "annotator",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "annotator",
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _eval_filter(
    eval_id: str,
    value: object,
    *,
    filter_type: str = "number",
    operation: str = "greater_than",
) -> dict[str, Any]:
    return {
        "column_id": eval_id,
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": filter_type,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _end_user_filter(
    value: object,
    *,
    operation: str = "equals",
) -> dict[str, Any]:
    return {
        "column_id": "user_id",
        "filter_config": {
            "col_type": "TRACE_END_USER",
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": value,
        },
    }


class _ProjectConfigValues(list):
    def first(self):
        return self[0] if self else None


class _ProjectConfigQuery:
    def __init__(
        self,
        configs_by_project: dict[str, tuple[str, ...]],
        *,
        selected_projects: tuple[str, ...] | None = None,
    ) -> None:
        self._configs_by_project = configs_by_project
        self._selected_projects = selected_projects

    def filter(self, **kwargs):
        projects = kwargs.get("project_id__in")
        if projects is None and kwargs.get("project_id") is not None:
            projects = (kwargs["project_id"],)
        if projects is None:
            return self
        return _ProjectConfigQuery(
            self._configs_by_project,
            selected_projects=tuple(str(project_id) for project_id in projects),
        )

    def exists(self):
        projects = self._selected_projects or tuple(self._configs_by_project)
        return any(
            self._configs_by_project.get(project_id, ()) for project_id in projects
        )

    def values_list(self, field, **_kwargs):
        projects = self._selected_projects or tuple(self._configs_by_project)
        config_ids = [
            config_id
            for project_id in projects
            for config_id in self._configs_by_project.get(project_id, ())
        ]
        if field == "eval_template_id":
            return _ProjectConfigValues(["org-template"] if config_ids else [])
        return _ProjectConfigValues(config_ids)


class _ProjectConfigManager:
    def __init__(self, configs_by_project: dict[str, tuple[str, ...]]) -> None:
        self._configs_by_project = configs_by_project

    def filter(self, **kwargs):
        return _ProjectConfigQuery(self._configs_by_project).filter(**kwargs)


class _ScoreTemplateQuery:
    def values(self, *_args):
        return self

    def first(self):
        return {"config": {"output": "SCORE"}}


@override_settings(
    CLICKHOUSE={
        "CH_HOST": "legacy.invalid",
        "CH_PORT": 9000,
        "CH_USERNAME": "legacy-user",
        "CH_PASSWORD": "legacy-password",
        "CH_DATABASE": "legacy-db",
    },
    CLICKHOUSE_V2={
        "CH25_HOST": "direct-write.invalid",
        "CH25_TCP_PORT": 9440,
        "CH25_USER": "direct-write-user",
        "CH25_PASSWORD": "",
        "CH25_DATABASE": "direct-write-db",
        "QUERY_TYPES_V2_ONLY": "TRACE_LIST",
    },
)
def test_dispatched_v2_query_service_uses_split_host_without_legacy_singleton() -> None:
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.v2.dispatch import get_query_builder_class
    from tracer.services.clickhouse.v2.query_service import (
        V2AnalyticsQueryService,
        query_service_for_builder,
        reset_v2_query_client,
    )

    reset_v2_query_client()
    try:
        with mock.patch(
            "tracer.services.clickhouse.query_service.get_clickhouse_client"
        ) as legacy_client:
            builder_class = get_query_builder_class("TRACE_LIST")
            fallback = object.__new__(AnalyticsQueryService)
            service = query_service_for_builder("TRACE_LIST", builder_class, fallback)

        assert isinstance(service, V2AnalyticsQueryService)
        assert service.ch_client.host == "direct-write.invalid"
        assert service.ch_client.port == 9440
        assert service.ch_client.user == "direct-write-user"
        assert service.ch_client.password == ""
        assert service.ch_client.database == "direct-write-db"
        assert V2AnalyticsQueryService().ch_client is service.ch_client
        legacy_client.assert_not_called()
    finally:
        reset_v2_query_client()


def test_customer_final_status_trace_query_uses_indexed_any_span_anchor() -> None:
    filters = [
        _time_filter(),
        _attribute_filter("final_status", ["Rejected"], operation="in"),
    ]
    builder = TraceListQueryBuilder(project_id=PROJECT_ID, filters=filters)

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a"])

    assert (
        "start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s)" in seed_sql
    )
    assert "start_time < fromUnixTimestamp64Micro(%(filter_slice_end_us)s)" in seed_sql
    assert "has(span_attr_str.keys, %(latest_filter_key_0)s)" in seed_sql
    assert "indexHint(has(mapKeys(span_attr_str), %(latest_filter_key_0)s))" in seed_sql
    assert "arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))" in seed_sql
    assert "arrayMap(x -> lower(x), mapValues(span_attr_str))" not in seed_sql
    assert seed_params["latest_filter_key_0"] == "final_status"
    assert seed_params["latest_filter_param_0"] == ("rejected",)
    assert seed_params["latest_filter_index_0_0"] == "rejected"
    assert "parent_span_id IS NULL" not in seed_sql
    assert "id AS matched_span_id" in seed_sql
    assert " FINAL" not in seed_sql
    assert seed_params["filter_seed_limit"] == 100
    assert match_params["candidate_trace_ids"] == ("trace-a",)
    assert "argMax(mapContains(span_attr_str, %(latest_filter_key_0)s)" in match_sql
    assert match_params["latest_filter_key_0"] == "final_status"
    assert "argMax(is_deleted, _peerdb_version)" in match_sql
    assert "argMaxIf(tuple(grouped_id)" in match_sql
    assert "GROUP BY trace_id, id, start_time" in match_sql
    assert "SELECT id\n" not in match_sql
    assert "parent_span_id IS NULL" in match_sql
    assert match_sql.count("%(candidate_trace_ids)s") == 1
    assert "latest_attr_exists_0" in match_sql
    assert match_sql.count("FROM spans") == 1
    assert "SELECT latest_trace_id" not in match_sql
    assert "AND trace_id IN %(candidate_trace_ids)s" in match_sql
    assert "%(candidate_start_date)s - INTERVAL 1 DAY" not in match_sql
    assert "%(candidate_end_date)s + INTERVAL 1 DAY" not in match_sql
    assert builder.filter_seed_proves_result_order() is False
    assert builder.filter_cursor_seed_keyset_is_safe() is True
    assert builder.recommended_filter_seed_batch_size() == 512
    assert builder.recommended_filter_classify_batch_size() == 10
    assert builder.recommended_filter_initial_slice_width() == timedelta(hours=1)
    assert builder.recommended_filter_max_slice_width() == END - START


@pytest.mark.parametrize(
    "builder_cls",
    [SpanListQueryBuilderV2, TraceListQueryBuilderV2],
)
def test_long_filtered_lists_require_cursor_before_clickhouse(builder_cls) -> None:
    long_builder = builder_cls(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", ["Rejected"], operation="in"),
        ],
        page_number=0,
        page_size=25,
    )
    short_builder = builder_cls(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(start=END - timedelta(minutes=30), end=END),
            _attribute_filter("final_status", ["Rejected"], operation="in"),
        ],
        page_number=0,
        page_size=25,
    )
    time_only_builder = builder_cls(
        project_id=PROJECT_ID,
        filters=[_time_filter()],
        page_number=0,
        page_size=25,
    )
    search_builder = None
    if builder_cls is TraceListQueryBuilderV2:
        search_builder = builder_cls(
            project_id=PROJECT_ID,
            filters=[_time_filter()],
            search="Rechazado",
            page_number=0,
            page_size=25,
        )

    assert long_builder.requires_cursor_for_long_filtered_read() is True
    assert short_builder.requires_cursor_for_long_filtered_read() is False
    assert time_only_builder.requires_cursor_for_long_filtered_read() is False
    if search_builder is not None:
        assert search_builder.requires_cursor_for_long_filtered_read() is True


def test_exact_graph_trace_seed_deduplicates_siblings_before_outer_keyset() -> None:
    filters = [
        _time_filter(),
        _attribute_filter("final_status", ["Rejected"], operation="in"),
        _attribute_filter("channel", ["voice"], operation="in"),
    ]
    builder = TraceListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)
    checkpoint = END - timedelta(minutes=2)

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=200,
        before_start_time=checkpoint,
        before_id="trace-m",
        _deduplicate_traces=True,
    )
    match_sql, match_params = builder.build_filter_identity_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "start_time": checkpoint,
            }
        ]
    )

    assert "id AS matched_span_id" not in seed_sql
    assert "max(start_time) AS witness_start_time" in seed_sql
    assert "GROUP BY project_id, trace_id" in seed_sql
    assert seed_sql.count("FROM spans") == 1
    outer_keyset = seed_sql.split(") AS deduplicated_trace_witnesses", 1)[1]
    assert "filter_before_start_us" in outer_keyset
    assert "toUnixTimestamp64Micro(witness_start_time)" in outer_keyset
    assert "trace_id < %(filter_before_id)s" in outer_keyset
    assert seed_params["filter_before_id"] == "trace-m"
    assert seed_params["filter_before_start_us"] == int(
        checkpoint.replace(tzinfo=UTC).timestamp() * 1_000_000
    )
    # The seed uses one necessary positive anchor. Every filter, deletion,
    # and latest physical version remains authoritative in the classifier.
    assert seed_params["latest_filter_key_0"] == "final_status"
    assert "latest_filter_key_1" not in seed_params
    assert match_params["latest_filter_key_0"] == "final_status"
    assert match_params["latest_filter_key_1"] == "channel"
    assert "argMax(is_deleted, _version)" in match_sql
    assert "latest_is_deleted = 0" in match_sql
    assert "latest_attr_exists_0" in match_sql
    assert "latest_attr_exists_1" in match_sql


def test_exact_graph_root_seed_keeps_root_window_and_classifies_children_globally() -> (
    None
):
    filters = [
        _time_filter(),
        _attribute_filter("final_status", ["Rejected"], operation="in"),
    ]
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )
    checkpoint = END - timedelta(minutes=1)

    assert builder.exact_graph_filter_witness_range() == (START, END)
    seed_sql, seed_params = builder.build_filter_ordered_seed_page(
        slice_start=START,
        slice_end=END,
        limit=200,
        before_start_time=checkpoint,
        before_id="trace-m",
    )
    match_sql, match_params = builder.build_filter_identity_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-root-inside-child-three-days-late",
                "start_time": checkpoint,
            }
        ]
    )

    assert seed_params["filter_slice_start"] == START
    assert seed_params["filter_slice_end"] == END
    assert "parent_span_id IS NULL" in seed_sql
    assert "filter_slice_start_us" in seed_sql
    assert "filter_slice_end_us" in seed_sql
    assert "filter_before_start_us" in seed_sql
    assert "LIMIT 1 BY trace_id" in seed_sql
    assert seed_params["filter_seed_limit"] == 200
    assert match_params["candidate_start_date"] == START
    assert match_params["candidate_end_date"] == END
    assert "candidate_witness_start_date_us" not in match_params
    assert "candidate_witness_end_date_us" not in match_params
    physical_scan = match_sql.split("FROM spans", 1)[1].split("GROUP BY", 1)[0]
    assert "trace_id IN %(candidate_trace_ids)s" in physical_scan
    assert "candidate_witness_start_date_us" not in physical_scan
    assert "candidate_witness_end_date_us" not in physical_scan
    assert "start_time >=" not in physical_scan
    assert "start_time <" not in physical_scan
    canonical_root = match_sql.split("AS canonical_root_identity", 1)[0]
    assert "candidate_start_date_us" in canonical_root
    assert "candidate_end_date_us" in canonical_root
    assert "latest_start_time >=" in canonical_root
    assert "latest_start_time <" in canonical_root
    assert "countIf(latest_attr_exists_0" in match_sql
    assert "latest_is_deleted = 0" in match_sql
    assert "argMax(is_deleted, _version)" in match_sql


@pytest.mark.parametrize(
    ("missing_requirement", "expected_error"),
    [
        (
            "bounded_internal_scan",
            "bounded_global_span_witnesses requires internal membership-only",
        ),
        ("bounded_identity_only", "bounded_bulk_scan requires bounded_identity_only"),
        (
            "bounded_bulk_scan",
            "bounded_global_span_witnesses requires internal membership-only",
        ),
        (
            "bounded_include_filter_witnesses",
            "bounded_global_span_witnesses requires internal membership-only",
        ),
    ],
)
def test_global_span_witness_mode_rejects_non_membership_only_builder(
    missing_requirement: str,
    expected_error: str,
) -> None:
    kwargs = {
        "bounded_internal_scan": True,
        "bounded_identity_only": True,
        "bounded_bulk_scan": True,
        "bounded_include_filter_witnesses": False,
        "bounded_global_span_witnesses": True,
    }
    kwargs[missing_requirement] = (
        True if missing_requirement == "bounded_include_filter_witnesses" else False
    )

    with pytest.raises(
        ValueError,
        match=expected_error,
    ):
        TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
            **kwargs,
        )


def test_exact_graph_global_classifier_preserves_org_composite_identity() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    builder = TraceListQueryBuilderV2(
        project_ids=[PROJECT_ID, project_b],
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
        ],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )
    started = END - timedelta(minutes=1)

    sql, params = builder.build_filter_identity_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT_ID,
                "trace_id": "shared-trace",
                "start_time": started,
            },
            {
                "project_id": project_b,
                "trace_id": "shared-trace",
                "start_time": started,
            },
        ]
    )

    assert params["candidate_trace_ids"] == ("shared-trace",)
    assert params["candidate_trace_identities"] == (
        (PROJECT_ID, "shared-trace"),
        (project_b, "shared-trace"),
    )
    physical_scan = sql.split("FROM spans", 1)[1].split("GROUP BY", 1)[0]
    assert "(project_id, trace_id) IN %(candidate_trace_identities)s" in physical_scan
    assert "candidate_witness_start_date_us" not in params
    assert "candidate_witness_end_date_us" not in params
    assert "start_time >=" not in physical_scan
    assert "start_time <" not in physical_scan
    assert sql.count("%(candidate_trace_identities)s") == 1
    assert "(grouped_project_id, grouped_trace_id) IN" not in " ".join(sql.split())
    canonical_root = sql.split("AS canonical_root_identity", 1)[0]
    assert "candidate_start_date_us" in canonical_root
    assert "candidate_end_date_us" in canonical_root


def test_exact_graph_global_classifier_collapses_mutations_before_tombstone_filter() -> (
    None
):
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
        ],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )

    sql, _ = builder.build_filter_identity_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-mutated-after-root-window",
                "start_time": END - timedelta(minutes=1),
            }
        ]
    )

    physical_scan = sql.split("FROM spans", 1)[1].split("GROUP BY", 1)[0]
    assert "is_deleted = 0" not in physical_scan
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in sql
    assert "WHERE latest_is_deleted = 0" in sql
    assert "argMax(mapContains(attrs_string" in sql
    assert "candidate_witness_start_date_us" not in sql


def test_exact_graph_global_classifier_accepts_5k_and_rejects_larger_batch() -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )
    rows = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:030d}",
            "start_time": END - timedelta(minutes=1),
        }
        for index in range(5_001)
    ]

    sql, params = builder.build_filter_identity_match_query_from_seed_rows(rows[:5_000])

    assert sql
    assert len(params["candidate_trace_ids"]) == 5_000
    # clickhouse-driver expands tuple parameters before transport. Repeating
    # this production-sized UUID-shaped tuple used to render a >256-KiB query,
    # which ClickHouse rejected with Code 62 before reading any rows.
    assert sql.count("%(candidate_trace_ids)s") == 1
    rendered_sql = _render_driver_sql(sql, params)
    assert len(rendered_sql.encode()) < 256 * 1024
    with pytest.raises(ValueError, match="candidate trace batch exceeds bounded limit"):
        builder.build_filter_identity_match_query_from_seed_rows(rows)


def test_exact_graph_org_classifier_keeps_composite_batch_below_parser_limit() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    builder = TraceListQueryBuilderV2(
        project_ids=[PROJECT_ID, project_b],
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )
    rows = [
        {
            "project_id": PROJECT_ID if index % 2 == 0 else project_b,
            "trace_id": f"trace-{index:030d}",
            "start_time": END - timedelta(minutes=1),
        }
        for index in range(1_001)
    ]

    sql, params = builder.build_filter_identity_match_query_from_seed_rows(rows[:1_000])

    assert sql.count("%(candidate_trace_ids)s") == 1
    assert sql.count("%(candidate_trace_identities)s") == 1
    assert len(_render_driver_sql(sql, params).encode()) < 256 * 1024
    with pytest.raises(ValueError, match="candidate trace batch exceeds bounded limit"):
        builder.build_filter_identity_match_query_from_seed_rows(rows)


def test_exact_graph_bulk_classifier_accepts_200_and_rejects_201_identities() -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
    )

    rows = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:030d}",
            "start_time": END - timedelta(minutes=1),
        }
        for index in range(201)
    ]
    sql, params = builder.build_filter_identity_match_query_from_seed_rows(rows[:200])

    assert len(params["candidate_trace_ids"]) == 200
    # The production EXPLAIN gate separately measures the fully rendered SQL;
    # this local guard ensures the builder payload stays comfortably below a
    # conservative 64-KiB parser envelope before transport interpolation.
    assert len((sql + repr(params)).encode()) < 64 * 1024
    with pytest.raises(ValueError, match="candidate trace batch exceeds bounded limit"):
        builder.build_filter_identity_match_query_from_seed_rows(rows)


def test_org_user_trace_seed_is_remap_aware_scoped_and_cursor_ordered() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    start = END - timedelta(days=180)
    filters = [
        _time_filter(start, END),
        _end_user_filter("guest-e3dce503"),
    ]
    builder = TraceListQueryBuilderV2(
        project_ids=[PROJECT_ID, project_b],
        filters=filters,
    )

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=start,
        slice_end=END,
        limit=51,
        before_start_time=END - timedelta(minutes=1),
        before_id=("trace-z", project_b),
    )
    match_sql, match_params = builder.build_filter_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "root_span_id": "root-a",
                "start_time": END - timedelta(minutes=2),
            }
        ]
    )

    assert builder.supports_bounded_filter_scan() is True
    assert builder.filter_seed_proves_result_order() is True
    assert builder.recommended_filter_initial_slice_width() == timedelta(days=180)
    assert builder.recommended_filter_max_slice_width() == timedelta(days=180)
    assert "(parent_span_id IS NULL OR parent_span_id = '')" in seed_sql
    assert "trace_id IN (SELECT trace_id FROM spans WHERE end_user_id IN (" in seed_sql
    assert "FROM end_users AS eu FINAL" in seed_sql
    assert "matching_end_user_ids AS" in seed_sql
    assert "matching_end_user_group_ids AS" in seed_sql
    assert "FROM end_user_id_remap AS remap_match FINAL" in seed_sql
    assert "WHERE remap.new_id IN (" in seed_sql
    assert "OVER (PARTITION BY new_id)" not in seed_sql
    assert seed_sql.count("project_id IN %(project_ids)s") >= 3
    assert "start_time >= %(start_date)s - INTERVAL 1 DAY" in seed_sql
    assert "start_time < %(end_date)s + INTERVAL 1 DAY" in seed_sql
    assert "created_at" not in seed_sql
    assert "ORDER BY start_time DESC, trace_id DESC" in seed_sql
    assert "LIMIT 1 BY project_id, trace_id" in seed_sql
    assert "filter_before_project_id" in seed_sql
    assert seed_params["col_1"] == "guest-e3dce503"
    assert seed_params["filter_before_id"] == "trace-z"
    assert seed_params["filter_before_project_id"] == project_b
    # The seed is only a physical superset. The existing finite latest-state
    # classifier remains authoritative and retains the residual user predicate.
    assert "FROM end_users AS eu FINAL" in match_sql
    assert "matching_end_user_ids AS" in match_sql
    assert "matching_end_user_group_ids AS" in match_sql
    assert "FROM end_user_id_remap AS remap_match FINAL" in match_sql
    assert "WHERE remap.new_id IN (" in match_sql
    assert "OVER (PARTITION BY new_id)" not in match_sql
    assert "candidate_trace_ids" in match_sql
    assert match_params["org_residual_0_col_1"] == "guest-e3dce503"


@pytest.mark.parametrize("col_type", ["SYSTEM_METRIC", "TRACE_END_USER"])
def test_external_user_trace_candidate_seed_is_user_first_and_root_ordered(
    col_type: str,
) -> None:
    start = END - timedelta(days=180)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(start, END),
            {
                "column_id": "user_id",
                "filter_config": {
                    "col_type": col_type,
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "45293328",
                },
            },
        ],
    )

    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=start,
        slice_end=END,
        limit=26,
    )
    compact_sql = " ".join(sql.split())

    assert builder.supports_filter_candidate_seed_page() is True
    assert "matching_user_trace_identities AS" in compact_sql
    assert "FROM end_users AS eu FINAL" in compact_sql
    assert "FROM end_user_id_remap AS remap_match FINAL" in compact_sql
    assert "PREWHERE project_id = %(project_id)s" in compact_sql
    assert "AND (end_user_id IN (" in compact_sql
    assert (
        "AND (project_id, trace_id) IN ( SELECT project_id, trace_id "
        "FROM matching_user_trace_identities )"
    ) in compact_sql
    assert "ORDER BY start_time DESC, trace_id DESC" in compact_sql
    assert "LIMIT 1 BY trace_id LIMIT %(filter_seed_limit)s" in compact_sql
    assert params["col_1"] == "45293328"
    assert params["filter_seed_limit"] == 26
    assert "user_candidate_start_us" not in params
    assert "user_candidate_end_us" not in params


@pytest.mark.parametrize("col_type", ["", "SYSTEM_METRIC"])
def test_structural_end_user_id_candidate_seed_uses_direct_uuid_predicate(
    col_type: str,
) -> None:
    end_user_id = "50f8845d-e410-5ceb-9bb5-a0d5e7ca6773"
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(days=30), END),
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "col_type": col_type,
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": end_user_id,
                },
            },
        ],
    )

    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=END - timedelta(days=30),
        slice_end=END,
        limit=26,
    )
    candidate_sql = sql.split("SELECT trace_id, id AS root_span_id", 1)[0]

    assert builder.supports_filter_candidate_seed_page() is True
    assert builder.filter_seed_proves_result_order() is True
    assert builder.filter_candidate_seed_proves_result_order() is True
    assert "matching_user_trace_identities AS" in candidate_sql
    assert "toString(end_user_id) = %(col_1)s" in candidate_sql
    assert "FROM end_users" not in candidate_sql
    assert params["col_1"] == end_user_id


def test_voice_annotator_and_turn_count_use_positive_candidate_seed() -> None:
    annotator_id = "00000000-0000-4000-8000-000000000099"
    filters = [
        _time_filter(),
        {
            "column_id": "turn_count",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 4,
            },
        },
        {
            "column_id": "annotator",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "annotator",
                "filter_op": "equals",
                "filter_value": annotator_id,
            },
        },
    ]
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_size=25,
        filters=filters,
    )

    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=START,
        slice_end=END,
        limit=26,
    )
    compact_sql = " ".join(sql.split())
    match_sql, match_params = builder.build_filter_match_query(["trace-a", "trace-b"])

    assert builder.supports_filter_candidate_seed_page() is True
    assert builder.recommended_filter_initial_slice_width() == END - START
    assert builder.recommended_filter_max_slice_width() == END - START
    assert (
        builder.recommended_filter_query_timeout_ms()
        == settings.INTERACTIVE_READ_DEFAULT_WALL_MS
    )
    assert "model_hub_score AS s FINAL" in compact_sql
    assert "s.annotator_id IN (toUUID(%(uid_1)s))" in compact_sql
    assert "s.created_at >=" not in compact_sql
    assert "observation_type" in compact_sql
    assert "ORDER BY start_time DESC, trace_id DESC" in compact_sql
    assert "LIMIT 1 BY trace_id LIMIT %(filter_seed_limit)s" in compact_sql
    assert params["uid_1"] == annotator_id
    assert params["filter_seed_limit"] == 26
    # Candidate acquisition uses the selective Score relation, but publication
    # still repeats both the annotator and turn-count predicates on the finite
    # candidate batch.
    assert "model_hub_score AS s FINAL" in match_sql
    assert "candidate_trace_ids" in match_sql
    assert 4 in match_params.values()
    assert match_params["candidate_trace_ids"] == ("trace-a", "trace-b")


def test_voice_annotator_is_not_null_uses_positive_candidate_seed() -> None:
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            {
                "column_id": "annotator",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "annotator",
                    "filter_op": "is_not_null",
                    "filter_value": None,
                },
            },
            {
                "column_id": "turn_count",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 1,
                },
            },
        ],
    )

    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=START,
        slice_end=END,
        limit=26,
    )
    compact_sql = " ".join(sql.split())
    match_sql, match_params = builder.build_filter_match_query(["trace-a"])

    assert builder.supports_filter_candidate_seed_page() is True
    assert builder.recommended_filter_initial_slice_width() == END - START
    assert "model_hub_score AS s FINAL" in compact_sql
    assert "isNotNull(s.annotator_id)" in compact_sql
    assert "ORDER BY start_time DESC, trace_id DESC" in compact_sql
    assert params["filter_seed_limit"] == 26
    # Candidate acquisition is only a narrowing witness. The finite
    # classifier still repeats both public predicates before publication.
    assert "isNotNull(s.annotator_id)" in match_sql
    assert "candidate_trace_ids" in match_sql
    assert 1 in match_params.values()


def test_negative_voice_annotator_uses_exact_relation_candidate_seed() -> None:
    annotator_id = "00000000-0000-4000-8000-000000000099"
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "annotator",
                    "filter_op": "not_equals",
                    "filter_value": annotator_id,
                },
            },
        ],
    )

    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=START,
        slice_end=END,
        limit=26,
    )
    compact_sql = " ".join(sql.split())

    assert builder.supports_filter_candidate_seed_page() is True
    assert "model_hub_score AS s FINAL" in compact_sql
    assert "SELECT DISTINCT" in compact_sql
    assert "s.annotator_id IN (toUUID(%(uid_1)s))" in compact_sql
    assert "trace_id NOT IN" in compact_sql
    assert "ORDER BY start_time DESC, trace_id DESC" in compact_sql
    assert params["uid_1"] == annotator_id
    assert params["filter_seed_limit"] == 26


def test_negative_annotator_bulk_selection_uses_relation_seed() -> None:
    annotator_id = "00000000-0000-4000-8000-000000000099"
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "annotator",
                    "filter_op": "not_equals",
                    "filter_value": annotator_id,
                },
            },
        ],
        columns=["trace_id"],
        bounded_identity_only=True,
        bounded_bulk_scan=True,
    )

    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=START,
        slice_end=END,
        limit=201,
    )
    compact_sql = " ".join(sql.split())

    assert builder.supports_filter_candidate_seed_page() is True
    assert "model_hub_score AS s FINAL" in compact_sql
    assert "s.annotator_id IN (toUUID(%(uid_1)s))" in compact_sql
    assert "trace_id NOT IN" in compact_sql
    assert params["uid_1"] == annotator_id
    assert params["filter_seed_limit"] == 201


def test_raw_annotator_span_attribute_never_uses_score_candidate_seed() -> None:
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            {
                "column_id": "annotator",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "raw-annotator-value",
                },
            },
        ],
    )

    assert builder.supports_filter_candidate_seed_page() is False


@pytest.mark.parametrize("column_id", ["end_user_id", "user", "user_id"])
def test_raw_user_named_span_attribute_does_not_use_candidate_seed(
    column_id: str,
) -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(days=30), END),
            {
                "column_id": column_id,
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "raw-provider-value",
                },
            },
        ],
    )

    assert builder.supports_filter_candidate_seed_page() is False
    raw_sql, _ = builder.build_filter_seed_page(
        slice_start=END - timedelta(days=30),
        slice_end=END,
        limit=26,
    )
    assert "mapKeys(attrs_string)" in raw_sql
    assert "attrs_string[" in raw_sql
    assert "end_users" not in raw_sql
    assert "end_user_id_remap" not in raw_sql
    with pytest.raises(ValueError, match="trace user candidate seed is unavailable"):
        builder.build_filter_candidate_seed_page(
            slice_start=END - timedelta(days=30),
            slice_end=END,
            limit=26,
        )


@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_positive_has_eval_candidate_seed_is_project_safe_and_reclassified() -> None:
    config_id = "00000000-0000-4000-8000-000000000088"
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _has_eval_filter(True)],
        eval_config_ids=[config_id],
    )

    with mock.patch(
        "tracer.models.custom_eval_config.CustomEvalConfig.objects"
    ) as config_manager:
        seed_sql, seed_params = builder.build_filter_candidate_seed_page(
            slice_start=START,
            slice_end=END,
            limit=26,
            before_start_time=END - timedelta(minutes=1),
            before_id="trace-z",
        )
        match_sql, match_params = builder.build_filter_match_query(
            ["trace-a", "trace-b"]
        )

    assert builder.supports_filter_candidate_seed_page() is True
    assert builder.filter_candidate_seed_proves_result_order() is True
    assert builder.recommended_filter_initial_slice_width() == END - START
    assert builder.recommended_filter_max_slice_width() == END - START
    assert config_manager.filter.call_count == 0

    # Candidate discovery uses the complete latest/live relation with the
    # endpoint's already-resolved project config set. There is no relation
    # population LIMIT; only the semantic version collapse and ordered root
    # page limit remain.
    assert "FROM tracer_eval_logger_v2 AS eval_scan" in seed_sql
    assert "eval_scan.custom_eval_config_id IN %(project_eval_cfg_1)s" in seed_sql
    assert "ORDER BY eval_scan._version DESC" in seed_sql
    assert "LIMIT 1 BY eval_scan.id" in seed_sql
    assert "latest_eval.is_deleted = 0" in seed_sql
    assert "sp.project_id = %(project_id)s" in seed_sql
    assert "eval_scan.created_at >=" not in seed_sql
    assert "candidate_trace_ids" not in seed_sql
    assert "ORDER BY start_time DESC, trace_id DESC" in seed_sql
    assert "LIMIT 1 BY trace_id" in seed_sql
    assert "LIMIT %(filter_seed_limit)s" in seed_sql
    assert "filter_before_start_us" in seed_sql
    assert "trace_id < %(filter_before_id)s" in seed_sql
    assert seed_params["project_eval_cfg_1"] == (config_id,)
    assert seed_params["filter_seed_limit"] == 26
    assert seed_params["filter_before_id"] == "trace-z"

    # The seed is acquisition-only. The ordinary finite latest-state
    # classifier repeats the same project-safe relation for publication.
    assert "FROM tracer_eval_logger_v2 AS eval_scan" in match_sql
    assert "toString(eval_scan.trace_id) IN %(candidate_trace_ids)s" in match_sql
    assert "LIMIT 1 BY eval_scan.id" in match_sql
    assert "latest_eval.is_deleted = 0" in match_sql
    assert match_params["project_eval_cfg_1"] == (config_id,)
    assert match_params["candidate_trace_ids"] == ("trace-a", "trace-b")


def test_positive_has_annotation_candidate_seed_preserves_all_label_completeness() -> (
    None
):
    label_a = "00000000-0000-4000-8000-000000000091"
    label_b = "00000000-0000-4000-8000-000000000092"
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _has_annotation_filter(True)],
        annotation_label_ids=[label_a, label_b],
    )

    seed_sql, seed_params = builder.build_filter_candidate_seed_page(
        slice_start=START,
        slice_end=END,
        limit=26,
        before_start_time=END - timedelta(minutes=1),
        before_id="trace-z",
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a", "trace-b"])

    assert builder.supports_filter_candidate_seed_page() is True
    assert builder.recommended_filter_initial_slice_width() == END - START
    assert builder.recommended_filter_max_slice_width() == END - START
    for sql in (seed_sql, match_sql):
        assert "FROM model_hub_score AS s FINAL" in sql
        assert "s.deleted = false AND s._peerdb_is_deleted = 0" in sql
        assert "s.tracer_project_id = toUUID(%(project_id)s)" in sql
        assert "sp.trace_id, toString(s.trace_id)" in sql
        assert "s.label_id IN (toUUID(%(lbl_1)s), toUUID(%(lbl_2)s))" in sql
        assert "GROUP BY entity_id HAVING uniqExact(s.label_id) >= 2" in sql
        # Annotation writes may postdate their root; relation membership is
        # exact and intentionally has no request-time or result-count cap.
        assert "s.created_at >=" not in sql

    assert "candidate_trace_ids" not in seed_sql
    assert "toString(trace_id) IN %(candidate_trace_ids)s" in match_sql
    assert seed_sql.count("LIMIT") == 2
    assert "ORDER BY start_time DESC, trace_id DESC" in seed_sql
    assert "LIMIT 1 BY trace_id" in seed_sql
    assert "LIMIT %(filter_seed_limit)s" in seed_sql
    assert seed_params["lbl_1"] == label_a
    assert seed_params["lbl_2"] == label_b
    assert match_params["lbl_1"] == label_a
    assert match_params["lbl_2"] == label_b


def test_known_empty_annotation_label_set_keeps_vacuous_exact_semantics() -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _has_annotation_filter(True)],
        annotation_label_ids=[],
    )

    match_sql, _ = builder.build_filter_match_query(["trace-a"])

    assert builder.supports_filter_candidate_seed_page() is False
    assert builder.recommended_filter_initial_slice_width() is None
    assert builder.recommended_filter_max_slice_width() is None
    assert "model_hub_score" not in match_sql


def test_has_eval_candidate_seed_requires_authoritative_project_config_metadata() -> (
    None
):
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _has_eval_filter(True)],
    )

    assert builder.supports_filter_candidate_seed_page() is False
    assert builder.recommended_filter_initial_slice_width() is None
    with pytest.raises(ValueError, match="trace user candidate seed is unavailable"):
        builder.build_filter_candidate_seed_page(
            slice_start=START,
            slice_end=END,
            limit=26,
        )


@pytest.mark.parametrize(
    "filters",
    [
        [_time_filter(), _has_eval_filter(False)],
        [_time_filter(), _has_annotation_filter("false")],
        [
            _time_filter(),
            {
                **_has_eval_filter(False),
                "filter_config": {
                    **_has_eval_filter(False)["filter_config"],
                    "col_type": "EVAL_METRIC",
                },
            },
        ],
    ],
)
def test_negative_existence_relation_does_not_use_candidate_seed(
    filters: list[dict[str, Any]],
) -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        eval_config_ids=["00000000-0000-4000-8000-000000000088"],
        annotation_label_ids=["00000000-0000-4000-8000-000000000091"],
    )

    assert builder.supports_filter_candidate_seed_page() is False
    assert builder.recommended_filter_initial_slice_width() != END - START
    with pytest.raises(ValueError, match="trace user candidate seed is unavailable"):
        builder.build_filter_candidate_seed_page(
            slice_start=START,
            slice_end=END,
            limit=26,
        )


@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_conjoined_positive_relation_seeds_then_reclassifies_every_filter() -> None:
    config_id = "00000000-0000-4000-8000-000000000088"
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _has_eval_filter(True),
            _attribute_filter("final_status", "Rejected"),
        ],
        eval_config_ids=[config_id],
    )

    seed_sql, _ = builder.build_filter_candidate_seed_page(
        slice_start=START,
        slice_end=END,
        limit=26,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a", "trace-b"])

    assert builder.supports_filter_candidate_seed_page() is True
    assert "tracer_eval_logger_v2 AS eval_scan" in seed_sql
    assert "candidate_trace_ids" not in seed_sql
    assert "tracer_eval_logger_v2 AS eval_scan" in match_sql
    assert "candidate_trace_ids" in match_sql
    assert "rejected" in match_params.values()
    assert match_params["candidate_trace_ids"] == ("trace-a", "trace-b")


@pytest.mark.parametrize(
    "builder_cls",
    [TraceListQueryBuilderV2, VoiceCallListQueryBuilderV2],
    ids=["trace", "voice"],
)
@pytest.mark.parametrize(
    ("filters", "membership_op"),
    [
        (
            [
                _time_filter(),
                _has_eval_filter(True),
                _attribute_filter("final_status", "Rejected"),
            ],
            "IN",
        ),
        ([_time_filter(), _has_eval_filter(False)], "NOT IN"),
    ],
    ids=["conjoined-true", "false"],
)
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_known_project_eval_configs_fence_residual_trace_id_collisions(
    builder_cls: type[TraceListQueryBuilderV2] | type[VoiceCallListQueryBuilderV2],
    filters: list[dict[str, Any]],
    membership_op: str,
) -> None:
    """A same-text trace id cannot borrow an eval owned by another project."""

    config_id = "00000000-0000-4000-8000-000000000088"
    builder = builder_cls(
        project_id=PROJECT_ID,
        filters=filters,
        eval_config_ids=[config_id],
    )

    with mock.patch(
        "tracer.models.custom_eval_config.CustomEvalConfig.objects"
    ) as config_manager:
        sql, params = builder.build_filter_match_query(["shared-trace"])

    assert config_manager.filter.call_count == 0
    assert f"trace_id {membership_op} (" in sql
    assert "eval_scan.custom_eval_config_id IN %(project_eval_cfg_1)s" in sql
    assert "sp.project_id = %(project_id)s" in sql
    assert "toString(eval_scan.trace_id) IN %(candidate_trace_ids)s" in sql
    assert params["project_eval_cfg_1"] == (config_id,)
    assert params["candidate_trace_ids"] == ("shared-trace",)
    if builder_cls is VoiceCallListQueryBuilderV2:
        assert "conversation" in params.values()


@pytest.mark.parametrize(
    "builder_cls",
    [TraceListQueryBuilderV2, VoiceCallListQueryBuilderV2],
    ids=["trace", "voice"],
)
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_known_empty_project_eval_configs_fail_positive_closed_and_negative_open(
    builder_cls: type[TraceListQueryBuilderV2] | type[VoiceCallListQueryBuilderV2],
) -> None:
    positive = builder_cls(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _has_eval_filter(True)],
        eval_config_ids=[],
    )
    negative = builder_cls(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _has_eval_filter(False)],
        eval_config_ids=[],
    )

    with mock.patch(
        "tracer.models.custom_eval_config.CustomEvalConfig.objects"
    ) as config_manager:
        positive_sql, positive_params = positive.build_filter_match_query(
            ["shared-trace"]
        )
        negative_sql, negative_params = negative.build_filter_match_query(
            ["shared-trace"]
        )

    impossible_relation = "SELECT toUUID('00000000-0000-0000-0000-000000000000')"
    assert config_manager.filter.call_count == 0
    assert f"trace_id IN ({impossible_relation})" in positive_sql
    assert f"trace_id NOT IN ({impossible_relation})" in negative_sql
    assert "tracer_eval_logger_v2" not in positive_sql
    assert "tracer_eval_logger_v2" not in negative_sql
    assert not any(key.startswith("project_eval_cfg") for key in positive_params)
    assert not any(key.startswith("project_eval_cfg") for key in negative_params)


@pytest.mark.parametrize(
    "builder_cls",
    [TraceListQueryBuilderV2, VoiceCallListQueryBuilderV2],
    ids=["trace", "voice"],
)
@pytest.mark.parametrize(
    ("filter_value", "membership_op"),
    [(True, "IN"), (False, "NOT IN")],
    ids=["positive", "negative"],
)
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_unknown_project_eval_configs_resolve_strict_fence_for_residual_filters(
    builder_cls: type[TraceListQueryBuilderV2] | type[VoiceCallListQueryBuilderV2],
    filter_value: bool,
    membership_op: str,
) -> None:
    """Legacy callers without metadata still fence same-text trace ids."""

    config_id = "00000000-0000-4000-8000-000000000088"
    builder = builder_cls(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _has_eval_filter(filter_value)],
    )

    with mock.patch(
        "tracer.models.custom_eval_config.CustomEvalConfig.objects"
    ) as config_manager:
        config_manager.filter.return_value.values_list.return_value = [config_id]
        sql, params = builder.build_filter_match_query(["shared-trace"])

    config_manager.filter.assert_called_once_with(
        project_id__in=[PROJECT_ID],
        deleted=False,
    )
    assert f"trace_id {membership_op} (" in sql
    assert "eval_scan.custom_eval_config_id IN %(project_eval_cfg_1)s" in sql
    assert "sp.project_id = %(project_id)s" in sql
    assert "toString(eval_scan.trace_id) IN %(candidate_trace_ids)s" in sql
    assert params["project_eval_cfg_1"] == (config_id,)
    assert params["candidate_trace_ids"] == ("shared-trace",)


@pytest.mark.parametrize(
    ("relation_filter", "eval_config_ids", "annotation_label_ids", "relation_table"),
    [
        (
            _has_eval_filter(True),
            ["00000000-0000-4000-8000-000000000088"],
            [],
            "tracer_eval_logger_v2 AS eval_scan",
        ),
        (
            _has_annotation_filter(True),
            [],
            ["00000000-0000-4000-8000-000000000091"],
            "model_hub_score AS s FINAL",
        ),
    ],
)
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_voice_positive_relation_candidate_seed_keeps_conversation_and_cursor_order(
    relation_filter: dict[str, Any],
    eval_config_ids: list[str],
    annotation_label_ids: list[str],
    relation_table: str,
) -> None:
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), relation_filter],
        eval_config_ids=eval_config_ids,
        annotation_label_ids=annotation_label_ids,
    )

    seed_sql, seed_params = builder.build_filter_candidate_seed_page(
        slice_start=START,
        slice_end=END,
        limit=26,
        before_start_time=END - timedelta(minutes=1),
        before_id="trace-z",
    )
    match_sql, _ = builder.build_filter_match_query(["trace-a"])

    assert builder.supports_filter_candidate_seed_page() is True
    assert builder.filter_candidate_seed_proves_result_order() is True
    assert builder.recommended_filter_initial_slice_width() == END - START
    assert builder.recommended_filter_max_slice_width() == END - START
    assert relation_table in seed_sql
    assert relation_table in match_sql
    assert "lowerUTF8(toString(observation_type))" in seed_sql
    assert seed_params["latest_filter_param_0"] == "conversation"
    assert "ORDER BY start_time DESC, trace_id DESC" in seed_sql
    assert "LIMIT 1 BY trace_id" in seed_sql
    assert "LIMIT %(filter_seed_limit)s" in seed_sql
    assert "filter_before_start_us" in seed_sql
    assert seed_params["filter_before_id"] == "trace-z"
    assert "candidate_trace_ids" not in seed_sql
    assert "candidate_trace_ids" in match_sql

    internal_builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), relation_filter],
        eval_config_ids=eval_config_ids,
        annotation_label_ids=annotation_label_ids,
        bounded_internal_scan=True,
    )
    assert internal_builder.supports_filter_candidate_seed_page() is False
    assert internal_builder.recommended_filter_initial_slice_width() is None
    assert internal_builder.recommended_filter_max_slice_width() is None


@pytest.mark.parametrize("operation", ["in", "not_in"])
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_voice_eval_value_filter_uses_project_scoped_positive_candidate_seed(
    operation: str,
) -> None:
    config_id = "480d5837-49e8-4a39-aad9-93d04790833c"
    eval_filter = _eval_filter(
        config_id,
        ["Passed", "Failed"],
        filter_type="text",
        operation=operation,
    )
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), eval_filter],
        eval_config_ids=[config_id],
        eval_filter_metadata={config_id: EvalFilterMetadata((config_id,), "PASS_FAIL")},
    )

    seed_sql, seed_params = builder.build_filter_candidate_seed_page(
        slice_start=START,
        slice_end=END,
        limit=26,
        before_start_time=END - timedelta(minutes=1),
        before_id="trace-z",
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a", "trace-b"])

    comparison = "NOT IN" if operation == "not_in" else "IN"
    assert builder.supports_filter_candidate_seed_page() is True
    assert builder.recommended_filter_initial_slice_width() == END - START
    assert "tracer_eval_logger_v2 AS eval_scan" in seed_sql
    assert f"output_bool {comparison} %(eval_bool_2)s" in seed_sql
    assert "eval_scan.created_at >=" not in seed_sql
    assert "candidate_trace_ids" not in seed_sql
    assert "lowerUTF8(toString(observation_type))" in seed_sql
    assert seed_params["eval_cfg_1"] == (config_id,)
    assert seed_params["eval_bool_2"] == (1, 0)
    assert seed_params["filter_before_id"] == "trace-z"

    # Candidate-first is acquisition-only. The finite latest-state classifier
    # rechecks the same value operation for the exact page identities.
    assert "tracer_eval_logger_v2 AS eval_scan" in match_sql
    assert f"output_bool {comparison} %(eval_bool_2)s" in match_sql
    assert "toString(eval_scan.trace_id) IN %(candidate_trace_ids)s" in match_sql
    assert match_params["candidate_trace_ids"] == ("trace-a", "trace-b")


@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_voice_eval_and_annotator_conjunction_prefers_score_seed_and_rechecks_eval() -> (
    None
):
    config_id = "480d5837-49e8-4a39-aad9-93d04790833c"
    annotator_id = "e1f8e455-9248-4aec-a510-ead35a946235"
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _eval_filter(
                config_id,
                ["Passed", "Failed"],
                filter_type="text",
                operation="in",
            ),
            _annotator_filter(annotator_id),
        ],
        eval_config_ids=[config_id],
        eval_filter_metadata={config_id: EvalFilterMetadata((config_id,), "PASS_FAIL")},
    )

    seed_sql, seed_params = builder.build_filter_candidate_seed_page(
        slice_start=START,
        slice_end=END,
        limit=26,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-positive"])

    # Annotator is the more selective positive witness even when the eval leaf
    # appears first in the public payload.
    assert builder.supports_filter_candidate_seed_page() is True
    assert "model_hub_score AS s FINAL" in seed_sql
    assert "s.annotator_id IN (toUUID(%(uid_1)s))" in seed_sql
    assert "tracer_eval_logger_v2" not in seed_sql
    assert seed_params["uid_1"] == annotator_id
    assert "lowerUTF8(toString(observation_type))" in seed_sql

    assert "model_hub_score AS s FINAL" in match_sql
    assert "tracer_eval_logger_v2 AS eval_scan" in match_sql
    assert "candidate_trace_ids" in match_sql
    assert match_params["candidate_trace_ids"] == ("trace-positive",)


def test_negated_user_trace_filter_does_not_use_positive_root_seed() -> None:
    filters = [
        _time_filter(END - timedelta(days=180), END),
        _end_user_filter("guest-e3dce503", operation="not_equals"),
    ]
    builder = TraceListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)

    seed_sql, _ = builder.build_filter_seed_page(
        slice_start=END - timedelta(days=2),
        slice_end=END,
        limit=51,
    )
    match_sql, _ = builder.build_filter_match_query(["trace-a"])

    assert builder.supports_filter_candidate_seed_page() is False
    assert "end_users" not in seed_sql
    assert "end_user_id_remap" not in seed_sql
    assert builder.recommended_filter_initial_slice_width() is None
    assert builder.recommended_filter_max_slice_width() is None
    assert "trace_id NOT IN (" in match_sql
    assert "end_user_id_remap" in match_sql


def test_graph_trace_key_witness_is_wide_key_only_and_classifier_stays_exact() -> None:
    filters = [
        _time_filter(END - timedelta(days=14), END),
        _attribute_filter("final_status", ["Rejected"], operation="in"),
        _attribute_filter("channel", "voice"),
    ]
    builder = TraceListQueryBuilder(project_id=PROJECT_ID, filters=filters)

    probe_sql, probe_params = builder.build_filter_graph_key_witness_probe(
        slice_start=END - timedelta(days=2),
        slice_end=END,
        limit=4,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a"])

    assert builder.supports_graph_key_witness_probe() is True
    assert "has(span_attr_str.keys, %(latest_filter_key_0)s)" in probe_sql
    assert "latest_filter_key_1" not in probe_sql
    assert "parent_span_id IS NULL" not in probe_sql
    assert "lowerUTF8" not in probe_sql
    assert "span_attr_str[" not in probe_sql
    assert "latest_filter_param_0" not in probe_sql
    assert "latest_filter_param_1" not in probe_sql
    assert probe_params["latest_filter_key_0"] == "final_status"
    assert "latest_filter_param_0" not in probe_params
    assert "latest_filter_param_1" not in probe_params
    assert "latest_attr_exists_0" in match_sql
    assert "latest_attr_exists_1" in match_sql
    assert match_params["latest_filter_param_0"] == ("rejected",)
    assert match_params["latest_filter_param_1"] == "voice"


def test_graph_span_key_witness_ands_keys_but_not_text_values() -> None:
    filters = [
        _time_filter(END - timedelta(days=14), END),
        _attribute_filter("final_status", ["Rejected"], operation="in"),
        _attribute_filter("channel", "voice"),
    ]
    builder = SpanListQueryBuilder(project_id=PROJECT_ID, filters=filters)

    probe_sql, probe_params = builder.build_filter_graph_key_witness_probe(
        slice_start=END - timedelta(days=2),
        slice_end=END,
        limit=50,
    )

    assert builder.supports_graph_key_witness_probe() is True
    assert "has(span_attr_str.keys, %(latest_filter_key_0)s)" in probe_sql
    assert "has(span_attr_str.keys, %(latest_filter_key_1)s)" in probe_sql
    assert "lowerUTF8" not in probe_sql
    assert "span_attr_str[" not in probe_sql
    assert "latest_filter_param_0" not in probe_sql
    assert "latest_filter_param_1" not in probe_sql
    assert probe_params["latest_filter_key_0"] == "final_status"
    assert probe_params["latest_filter_key_1"] == "channel"


def test_graph_numeric_equality_retains_value_indexed_witness() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(days=14), END),
            _attribute_filter("attempt", 7, filter_type="number"),
        ],
    )

    probe_sql, probe_params = builder.build_filter_graph_key_witness_probe(
        slice_start=END - timedelta(days=2),
        slice_end=END,
        limit=4,
    )

    assert "has(mapValues(span_attr_num), %(latest_filter_param_0)s)" in probe_sql
    assert "span_attr_num[%(latest_filter_key_0)s]" in probe_sql
    assert probe_params["latest_filter_param_0"] == 7


def test_long_window_scalar_trace_uses_exact_classifier_without_witness() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(days=14), END),
            _attribute_filter("final_status", ["Rejected"], operation="in"),
        ],
    )

    assert builder.recommended_filter_seed_batch_size() == 512
    assert builder.recommended_filter_classify_batch_size() == 10
    assert builder.skip_full_window_filter_anchor_probe() is True
    assert builder.recommended_filter_anchor_probe_limit() is None
    assert builder.recommended_filter_anchor_probe_timeout_ms() is None
    assert builder.recommended_filter_anchor_probe_strata() is None
    assert builder.recommended_filter_anchor_probe_max_bytes_to_read() is None
    assert builder.prefer_filter_candidate_witness_probe_first() is False
    assert builder.recommended_filter_candidate_witness_probe_strata() is None
    assert builder.recommended_filter_max_query_count() is None
    assert (
        builder.recommended_filter_candidate_witness_fallback_classify_batch_size()
        == 10
    )
    assert builder.recommended_filter_page_hydration_reserve_ms() == 750
    assert builder.fill_bounded_cursor_page_across_slices() is True
    assert builder.recommended_filter_classify_read_settings() == {
        "max_block_size": 2_048,
        "preferred_max_column_in_block_size_bytes": 1_048_576,
    }


@pytest.mark.parametrize(
    "builder_kwargs",
    [
        {"bounded_internal_scan": True, "bounded_identity_only": True},
        {
            "bounded_internal_scan": True,
            "bounded_identity_only": True,
            "bounded_bulk_scan": True,
        },
        {"bounded_sampling_salt": "sample", "bounded_sampling_rate": 10.0},
        {"page_size": 501},
    ],
    ids=["internal", "bulk", "sampled", "oversized"],
)
def test_non_public_trace_readers_do_not_fill_cursor_pages_across_slices(
    builder_kwargs: dict[str, object],
) -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(END - timedelta(days=14), END)],
        **builder_kwargs,
    )

    assert builder.fill_bounded_cursor_page_across_slices() is False


def test_long_window_attribute_plus_search_keeps_bounded_root_batch() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(days=14), END),
            _attribute_filter("final_status", ["Rejected"], operation="in"),
        ],
        search="checkout",
    )

    assert builder.prefer_filter_candidate_witness_probe_first() is False
    assert builder.recommended_filter_seed_batch_size() == 200


@pytest.mark.parametrize(
    ("filter_type", "operation", "value"),
    [
        ("array", "contains", ["urgent", "vip"]),
        ("map", "equals", {"state": "Rejected"}),
        ("json", "contains", ["urgent", "vip"]),
        ("json", "equals", {"state": "Rejected"}),
    ],
)
def test_structured_trace_classifier_uses_memory_safe_batch_and_block(
    filter_type: str,
    operation: str,
    value: object,
) -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "structured",
                value,
                filter_type=filter_type,
                operation=operation,
            ),
        ],
    )

    assert builder.recommended_filter_classify_batch_size() == 10
    assert builder.recommended_filter_classify_read_settings() == {
        "max_block_size": 2_048,
        "preferred_max_column_in_block_size_bytes": 1_048_576,
    }


def test_extreme_structured_multifilter_keeps_scalar_fast_path_independent() -> None:
    simple_builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
            _attribute_filter("attempt", 7, filter_type="number"),
            _attribute_filter("reviewed", True, filter_type="boolean"),
        ],
    )
    structured_builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            *simple_builder.filters,
            _attribute_filter(
                "labels",
                ["urgent"],
                filter_type="array",
                operation="contains",
            ),
            _attribute_filter(
                "payload",
                {"state": "Rejected"},
                filter_type="map",
                operation="contains",
            ),
        ],
    )

    assert simple_builder.recommended_filter_classify_batch_size() == 10
    assert simple_builder.recommended_filter_classify_read_settings() == {
        "max_block_size": 2_048,
        "preferred_max_column_in_block_size_bytes": 1_048_576,
    }
    assert structured_builder.recommended_filter_classify_batch_size() == 10
    assert structured_builder.recommended_filter_classify_read_settings() == {
        "max_block_size": 2_048,
        "preferred_max_column_in_block_size_bytes": 1_048_576,
    }


@pytest.mark.parametrize(
    ("include_filter_witnesses", "population_proof", "fallback_batch"),
    [(False, False, 10), (True, True, None)],
    ids=["normal", "population-proof"],
)
def test_structured_eval_bulk_uses_safe_batch_and_block_cap(
    include_filter_witnesses: bool,
    population_proof: bool,
    fallback_batch: int | None,
) -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
            _attribute_filter(
                "payload",
                {"state": "Rejected"},
                filter_type="map",
            ),
        ],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=include_filter_witnesses,
        bounded_population_proof=population_proof,
    )

    assert builder.recommended_filter_classify_batch_size() == 10
    assert builder.recommended_filter_classify_read_settings() == {
        "max_block_size": 2_048,
        "preferred_max_column_in_block_size_bytes": 1_048_576,
    }
    assert (
        builder.recommended_filter_candidate_witness_fallback_classify_batch_size()
        == fallback_batch
    )


def test_customer_scalar_custom_attributes_use_ten_trace_batches_everywhere() -> None:
    filters = [
        _time_filter(),
        _attribute_filter("call.total_turns", 2, filter_type="number"),
        _attribute_filter(
            "conversation.transcript.16.message.role",
            ["assistant"],
            operation="in",
        ),
    ]
    interactive = TraceListQueryBuilder(project_id=PROJECT_ID, filters=filters)
    bulk = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
    )

    assert interactive.recommended_filter_classify_batch_size() == 10
    assert bulk.recommended_filter_classify_batch_size() == 10
    assert (
        bulk.recommended_filter_candidate_witness_fallback_classify_batch_size() == 10
    )


@pytest.mark.parametrize(
    ("filter_type", "operation", "value", "map_column"),
    [
        ("text", "equals", "Rejected", "span_attr_str"),
        ("text", "in", ["Rejected", "Queued"], "span_attr_str"),
        ("number", "equals", 7, "span_attr_num"),
        ("boolean", "in", [True, False], "span_attr_bool"),
    ],
)
def test_trace_candidate_witness_probe_resolves_finite_typed_map_latest_state(
    filter_type: str,
    operation: str,
    value: object,
    map_column: str,
) -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "final_status",
                value,
                filter_type=filter_type,
                operation=operation,
            ),
        ],
    )

    sql, params = builder.build_filter_candidate_witness_probe(
        [{"trace_id": "trace-a"}, {"trace_id": "trace-b"}]
    )

    assert "SELECT\n" in sql
    assert "grouped_trace_id AS trace_id" in sql
    assert "project_id = %(project_id)s" in sql
    assert "argMax(is_deleted, _peerdb_version) AS latest_is_deleted" in sql
    assert "latest_is_deleted = 0" in sql
    assert "trace_id IN %(filter_candidate_trace_ids)s" in sql
    assert "grouped_trace_id IN %(filter_candidate_trace_ids)s" in sql
    assert "filter_candidate_start_us" not in sql
    assert "filter_candidate_end_us" not in sql
    assert (
        f"argMax(mapContains({map_column}, %(latest_filter_key_0)s), "
        "_peerdb_version)" in sql
    )
    assert " FINAL" not in sql
    assert params["filter_candidate_trace_ids"] == ("trace-a", "trace-b")
    assert params["filter_candidate_witness_limit"] == 2
    assert "filter_candidate_start_us" not in params
    assert "filter_candidate_end_us" not in params
    assert params["latest_filter_key_0"] == "final_status"
    # A plain typed-Map scalar classifier is faster than a year-window witness
    # on large tenants, so the query remains available for internal callers but
    # is not selected speculatively for the interactive list.
    assert builder.prefer_filter_candidate_witness_probe_first() is False
    assert builder.recommended_filter_candidate_witness_probe_strata() is None
    assert builder.recommended_filter_candidate_witness_probe_timeout_ms() is None
    assert builder.recommended_filter_candidate_witness_probe_max_bytes() is None
    assert builder.recommended_filter_candidate_witness_probe_total_ms() is None
    assert builder.recommended_filter_max_query_count() is None
    assert (
        builder.recommended_filter_candidate_witness_fallback_classify_batch_size()
        == 10
    )

    slice_start = START + timedelta(days=100)
    slice_end = START + timedelta(days=110)
    slice_sql, slice_params = builder.build_filter_candidate_witness_probe(
        [{"trace_id": "trace-a"}],
        slice_start=slice_start,
        slice_end=slice_end,
    )
    assert "filter_candidate_start_us" not in slice_params
    assert "filter_candidate_end_us" not in slice_params
    assert "filter_candidate_start_us" not in slice_sql
    assert "filter_candidate_end_us" not in slice_sql
    assert builder.filter_candidate_witness_replays_global_membership() is True

    with pytest.raises(ValueError, match="provided together"):
        builder.build_filter_candidate_witness_probe(
            [{"trace_id": "trace-a"}],
            slice_start=slice_start,
        )
    outside_sql, outside_params = builder.build_filter_candidate_witness_probe(
        [{"trace_id": "trace-a"}],
        slice_start=START - timedelta(days=365),
        slice_end=END + timedelta(days=365),
    )
    assert "filter_candidate_start_us" not in outside_sql
    assert "filter_candidate_end_us" not in outside_params


def test_nested_array_path_keeps_interactive_candidate_witness() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "conversation.transcript.16.message.role",
                ["assistant"],
                operation="in",
            ),
        ],
    )

    assert builder.prefer_filter_candidate_witness_probe_first() is True
    assert builder.recommended_filter_candidate_witness_probe_strata() == 1
    assert builder.recommended_filter_max_query_count() == 128


@pytest.mark.parametrize(
    "builder_cls",
    [
        TraceListQueryBuilder,
        TraceListQueryBuilderV2,
        VoiceCallListQueryBuilder,
        VoiceCallListQueryBuilderV2,
    ],
)
def test_long_exact_text_attribute_uses_finite_candidate_witness(
    builder_cls,
) -> None:
    recording_url = (
        "https://storage.vapi.ai/019db06c-d54a-7003-9810-cf01cc4aa9d1-1776781471202"
    )
    builder = builder_cls(
        project_id=PROJECT_ID,
        page_size=25,
        filters=[
            _time_filter(),
            _attribute_filter(
                "conversation.recording.mono.assistant",
                [recording_url],
                operation="in",
            ),
        ],
    )

    assert builder.prefer_filter_candidate_witness_probe_first() is True
    assert builder.recommended_filter_seed_batch_size() == 512
    assert builder.recommended_filter_max_query_count() == 128
    assert builder.recommended_filter_candidate_witness_probe_strata() == 1
    if isinstance(builder, VoiceCallListQueryBuilder):
        assert builder.recommended_filter_cursor_seed_batch_size() == 512

    sql, params = builder.build_filter_candidate_witness_probe(
        [{"project_id": PROJECT_ID, "trace_id": "trace-a"}]
    )
    assert "trace_id IN %(filter_candidate_trace_ids)s" in sql
    assert params["latest_filter_key_0"] == "conversation.recording.mono.assistant"
    assert params["latest_filter_param_0"] == (recording_url,)


@pytest.mark.parametrize(
    "value,operation",
    [
        (["Rejected"], "in"),
        (["x" * 64], "not_in"),
        (["x" * 64, "short"], "in"),
    ],
)
def test_scalar_text_candidate_witness_keeps_nonselective_shapes_on_exact_path(
    value: object,
    operation: str,
) -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "final_status",
                value,
                operation=operation,
            ),
        ],
    )

    assert builder.prefer_filter_candidate_witness_probe_first() is False


def test_scalar_first_multi_filter_witness_selects_nested_leaf() -> None:
    nested_key = "conversation.transcript.16.message.role"
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", ["Rejected"], operation="in"),
            _attribute_filter(nested_key, ["assistant"], operation="in"),
        ],
    )

    sql, params = builder.build_filter_candidate_witness_probe(
        [{"trace_id": "trace-a"}]
    )

    assert builder.prefer_filter_candidate_witness_probe_first() is True
    assert params["latest_filter_key_1"] == nested_key
    assert "latest_filter_key_1" in sql
    assert "latest_filter_key_0" not in sql


def test_negative_nested_leaf_does_not_enable_scalar_interactive_witness() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", ["Rejected"], operation="in"),
            _attribute_filter(
                "conversation.transcript.16.message.role",
                "assistant",
                operation="not_equals",
            ),
        ],
    )

    assert builder.prefer_filter_candidate_witness_probe_first() is False
    assert builder.recommended_filter_candidate_witness_probe_strata() is None


def test_org_trace_candidate_witness_probe_keeps_composite_identity() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    builder = TraceListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        filters=[
            _time_filter(),
            _attribute_filter("final_status", ["Rejected"], operation="in"),
        ],
    )

    sql, params = builder.build_filter_candidate_witness_probe(
        [
            {"project_id": PROJECT_ID, "trace_id": "shared-trace"},
            {"project_id": project_b, "trace_id": "shared-trace"},
        ]
    )

    assert "grouped_project_id AS project_id" in sql
    assert "grouped_trace_id AS trace_id" in sql
    assert "project_id IN %(project_ids)s" in sql
    assert "(project_id, trace_id) IN %(filter_candidate_trace_identities)s" in sql
    assert params["filter_candidate_trace_identities"] == (
        (PROJECT_ID, "shared-trace"),
        (project_b, "shared-trace"),
    )
    assert params["filter_candidate_witness_limit"] == 2


def test_trace_candidate_witness_probe_supports_exact_structured_map_state() -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "payload",
                {"state": "Rejected"},
                filter_type="map",
                operation="contains",
            ),
        ],
    )

    sql, params = builder.build_filter_candidate_witness_probe(
        [{"trace_id": "trace-a"}]
    )

    assert builder.prefer_filter_candidate_witness_probe_first() is True
    assert builder.recommended_filter_candidate_witness_probe_strata() == 1
    assert "trace_id IN %(filter_candidate_trace_ids)s" in sql
    assert "JSONHas(attributes_extra, %(latest_filter_key_0)s)" in sql
    assert "latest_json_map_value_0" in sql
    assert "HAVING max(toUInt8(latest_is_deleted = 0" in sql
    assert "UNION ALL" not in sql
    assert params["latest_filter_key_0"] == "payload"
    assert params["latest_filter_map_key_0_0"] == "state"
    assert params["latest_filter_map_value_0_0_string"] == "Rejected"


@pytest.mark.parametrize(
    "builder",
    [
        TraceListQueryBuilder(
            project_id=PROJECT_ID,
            filters=[
                _time_filter(),
                _attribute_filter("final_status", "Rejected", operation="not_equals"),
            ],
        ),
        TraceListQueryBuilder(
            project_id=PROJECT_ID,
            filters=[_time_filter(), _system_filter("trace_name", "checkout")],
        ),
        TraceListQueryBuilder(
            project_id=PROJECT_ID,
            filters=[_time_filter(), _annotation_filter("label-a", "Rejected")],
        ),
        TraceListQueryBuilder(
            project_id=PROJECT_ID,
            filters=[
                _time_filter(),
                _attribute_filter("final_status", "Rejected"),
            ],
            bounded_identity_only=True,
        ),
        TraceListQueryBuilder(
            project_id=PROJECT_ID,
            filters=[
                _time_filter(),
                _attribute_filter("final_status", "Rejected"),
            ],
            bounded_internal_scan=True,
        ),
    ],
    ids=[
        "negative",
        "root",
        "residual",
        "identity-only",
        "internal",
    ],
)
def test_trace_candidate_witness_probe_is_unavailable_for_unsafe_shapes(
    builder: TraceListQueryBuilder,
) -> None:
    assert builder.prefer_filter_candidate_witness_probe_first() is False
    assert builder.recommended_filter_candidate_witness_probe_strata() is None
    assert (
        builder.recommended_filter_candidate_witness_fallback_classify_batch_size()
        is None
    )
    assert builder.build_filter_candidate_witness_probe([{"trace_id": "trace-a"}]) == (
        "",
        {},
    )


def test_trace_candidate_latest_anchor_prefilters_multi_filter_and() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
            _attribute_filter("country", "ES"),
        ],
    )

    probe_sql, _ = builder.build_filter_candidate_witness_probe(
        [{"trace_id": "trace-a"}]
    )
    classifier_sql, _ = builder.build_filter_identity_match_query_from_seed_rows(
        [{"trace_id": "trace-a"}]
    )

    assert builder.prefer_filter_candidate_witness_probe_first() is False
    assert "latest_filter_key_0" in probe_sql
    # Only one necessary leaf is allowed in each temporal stratum. The exact
    # classifier below retains both leaves, including when sibling spans in
    # different strata satisfy them independently.
    assert "latest_filter_key_1" not in probe_sql
    assert probe_sql.count("FROM spans") == 1
    assert "UNION ALL" not in probe_sql
    assert probe_sql.count("max(toUInt8(latest_is_deleted = 0") == 1
    assert "latest_filter_key_0" in classifier_sql
    assert "latest_filter_key_1" in classifier_sql


def test_trace_candidate_witness_probe_rejects_unbounded_or_invalid_candidates() -> (
    None
):
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
        ],
    )

    too_many = [{"trace_id": f"trace-{index}"} for index in range(513)]
    assert builder.build_filter_candidate_witness_probe(too_many) == ("", {})
    assert builder.build_filter_candidate_witness_probe([{}]) == ("", {})


def test_short_window_trace_keeps_full_sparse_anchor_probe() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(hours=1), END),
            _attribute_filter("final_status", ["Rejected"], operation="in"),
        ],
    )

    assert builder.skip_full_window_filter_anchor_probe() is False
    assert builder.recommended_filter_anchor_probe_limit() is None
    assert builder.recommended_filter_anchor_probe_timeout_ms() is None
    assert builder.recommended_filter_anchor_probe_strata() is None
    assert builder.recommended_filter_anchor_probe_max_bytes_to_read() is None
    assert builder.prefer_filter_candidate_witness_probe_first() is False


def test_eval_trace_any_span_classifier_uses_production_safe_batch() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(days=14), END),
            _attribute_filter("final_status", ["Rejected"], operation="in"),
        ],
        bounded_identity_only=True,
        bounded_bulk_scan=True,
    )

    # Acquiring identity-only roots is cheap; latest-state any-span replay is
    # the high-read phase and must be split independently.
    assert builder.recommended_filter_seed_batch_size() == 200
    assert builder.recommended_filter_classify_batch_size() == 10
    assert builder.skip_full_window_filter_anchor_probe() is True
    assert builder.recommended_filter_anchor_probe_limit() is None
    assert builder.recommended_filter_anchor_probe_timeout_ms() is None
    assert builder.recommended_filter_anchor_probe_strata() is None
    assert builder.recommended_filter_anchor_probe_max_bytes_to_read() is None
    assert (
        builder.supports_filter_candidate_witness_prefilter_without_hydration() is False
    )
    assert builder.prefer_filter_candidate_witness_probe_first() is False


def test_eval_trace_membership_only_classifier_buffers_safe_typed_map_probe() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(days=14), END),
            _attribute_filter("final_status", ["Rejected"], operation="in"),
        ],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
    )

    assert builder.recommended_filter_classify_batch_size() == 10
    assert (
        builder.supports_filter_candidate_witness_prefilter_without_hydration() is True
    )
    assert (
        builder.use_buffered_identity_filter_classification_without_hydration() is True
    )
    assert builder.skip_full_window_filter_anchor_probe() is True
    assert builder.recommended_filter_anchor_probe_limit() is None
    assert builder.recommended_filter_anchor_probe_timeout_ms() is None
    assert builder.recommended_filter_anchor_probe_strata() is None
    assert builder.recommended_filter_anchor_probe_max_bytes_to_read() is None
    assert builder.prefer_filter_candidate_witness_probe_first() is True
    assert builder.recommended_filter_candidate_witness_probe_strata() == 1
    assert builder.recommended_filter_max_query_count() == 128
    assert (
        builder.recommended_filter_candidate_witness_fallback_classify_batch_size()
        == 10
    )
    probe_sql, probe_params = builder.build_filter_candidate_witness_probe(
        [{"trace_id": "trace-a"}, {"trace_id": "trace-b"}]
    )
    assert "grouped_trace_id AS trace_id" in probe_sql
    assert "trace_id IN %(filter_candidate_trace_ids)s" in probe_sql
    assert "mapContains(span_attr_str, %(latest_filter_key_0)s)" in probe_sql
    assert "argMax(is_deleted, _peerdb_version)" in probe_sql
    assert probe_params["filter_candidate_trace_ids"] == ("trace-a", "trace-b")


@pytest.mark.parametrize(
    ("filters", "extra_kwargs"),
    [
        (
            [
                _time_filter(),
                _attribute_filter("final_status", "Rejected", operation="not_equals"),
            ],
            {},
        ),
        ([_time_filter(), _system_filter("trace_name", "checkout")], {}),
        ([_time_filter(), _annotation_filter("label-a", "Rejected")], {}),
        (
            [_time_filter(), _attribute_filter("final_status", "Rejected")],
            {"search": "checkout"},
        ),
        (
            [_time_filter(), _attribute_filter("final_status", "Rejected")],
            {"bounded_include_filter_witnesses": True},
        ),
        (
            [_time_filter(), _attribute_filter("final_status", "Rejected")],
            {"bounded_population_proof": True},
        ),
    ],
    ids=[
        "negative",
        "root",
        "residual",
        "search",
        "witness-carrying",
        "population-proof",
    ],
)
def test_eval_trace_candidate_prefilter_rejects_non_membership_only_shapes(
    filters: list[dict[str, Any]],
    extra_kwargs: dict[str, Any],
) -> None:
    builder_kwargs = {
        "bounded_internal_scan": True,
        "bounded_identity_only": True,
        "bounded_bulk_scan": True,
        "bounded_include_filter_witnesses": False,
        **extra_kwargs,
    }
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        **builder_kwargs,
    )

    assert builder.prefer_filter_candidate_witness_probe_first() is False
    assert (
        builder.use_buffered_identity_filter_classification_without_hydration() is False
    )
    assert builder.build_filter_candidate_witness_probe([{"trace_id": "trace-a"}]) == (
        "",
        {},
    )


def test_eval_trace_membership_prefilter_accepts_structured_map_state() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "payload",
                {"state": "Rejected"},
                filter_type="map",
                operation="contains",
            ),
        ],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
    )

    sql, _ = builder.build_filter_candidate_witness_probe([{"trace_id": "trace-a"}])

    assert builder.prefer_filter_candidate_witness_probe_first() is True
    assert builder.recommended_filter_candidate_witness_probe_strata() == 1
    assert builder.recommended_filter_max_query_count() == 128
    assert "latest_json_map_value_0" in sql


def test_eval_trace_any_span_large_prefix_fails_closed_at_query_ceiling() -> None:
    common = {
        "page_number": 0,
        "max_seed_attempts": 128,
        "max_candidates": 512,
        "max_query_count": 128,
        "classify_batch_size": 20,
        "seed_batch_size": 200,
    }

    # This helper is only the mechanical preflight estimate: this bulk builder
    # has no optional anchor, and the estimate does not model classifier chunks
    # restarting per seed page. Passing this boundary is not an end-to-end
    # guarantee; runtime accounting still fails closed if the actual 128-read
    # budget is exhausted.
    assert bounded_numbered_page_depth_exceeded(page_size=2_459, **common) is False
    # The next public row is rejected by even that optimistic estimate before
    # CH is contacted, instead of widening back to the unsafe classifier batch.
    assert bounded_numbered_page_depth_exceeded(page_size=2_460, **common) is True


def test_call_type_trace_filter_skips_unindexed_window_anchor() -> None:
    filters = [_time_filter(), _system_filter("call_type", "inbound")]
    builder = TraceListQueryBuilder(project_id=PROJECT_ID, filters=filters)

    ordered_sql, _ = builder.build_filter_ordered_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=50,
    )
    match_sql, _ = builder.build_filter_match_query(["trace-a"])

    assert builder.supports_filter_anchor_probe() is False
    with pytest.raises(ValueError, match="indexed any-span filter"):
        builder.build_filter_anchor_probe(limit=513)
    assert "JSONExtract" not in ordered_sql
    assert "parent_span_id IS NULL" in ordered_sql
    assert "JSONExtract" in match_sql
    assert builder.recommended_filter_seed_batch_size() == 50
    assert builder.recommended_filter_classify_batch_size() == 20
    assert builder.recommended_filter_anchor_probe_limit() is None
    assert builder.recommended_filter_anchor_probe_timeout_ms() is None
    assert builder.recommended_filter_unindexed_micro_seed_width() == timedelta(
        minutes=5
    )
    assert builder.recommended_filter_unindexed_micro_seed_strata() == 4
    micro_sql, micro_params = builder.build_filter_unindexed_micro_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=26,
    )
    assert "JSONExtractString(span_attributes_raw, 'raw_log', 'type')" in micro_sql
    assert micro_params["filter_seed_limit"] == 26
    with pytest.raises(ValueError, match="exceeds micro-slice"):
        builder.build_filter_unindexed_micro_seed_page(
            slice_start=END - timedelta(minutes=6),
            slice_end=END,
            limit=26,
        )
    graph_builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_identity_only=True,
    )
    assert graph_builder.recommended_filter_unindexed_micro_seed_width() is None


def test_call_type_span_filter_has_exact_micro_seed_only() -> None:
    filters = [_time_filter(), _system_filter("call_type", "inbound")]
    builder = SpanListQueryBuilder(project_id=PROJECT_ID, filters=filters)

    ordinary_sql, _ = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=26,
    )
    micro_sql, micro_params = builder.build_filter_unindexed_micro_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=26,
    )

    assert "JSONExtract" not in ordinary_sql
    assert "JSONExtractString(span_attributes_raw, 'raw_log', 'type')" in micro_sql
    assert micro_params["filter_seed_limit"] == 26
    assert builder.recommended_filter_unindexed_micro_seed_width() == timedelta(
        minutes=5
    )
    assert builder.recommended_filter_unindexed_micro_seed_strata() == 4
    assert builder.filter_unindexed_micro_seed_proves_result_order() is True
    graph_builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_anchor_probe=True,
    )
    assert graph_builder.recommended_filter_unindexed_micro_seed_width() is None


def test_time_only_span_cursor_exposes_tightly_bounded_sparse_probe() -> None:
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter()],
        page_size=25,
        bounded_internal_scan=True,
    )

    assert builder.allow_filter_anchor_probe_for_initial_continuation() is True
    assert builder.supports_filter_anchor_probe() is True
    assert builder.recommended_filter_anchor_probe_limit() == 26
    assert builder.recommended_filter_anchor_probe_timeout_ms() == 300
    assert builder.recommended_filter_anchor_probe_strata() == 1
    assert builder.recommended_filter_max_query_count() == 49
    assert (
        builder.recommended_filter_anchor_probe_max_bytes_to_read() == 96 * 1024 * 1024
    )

    sql, params = builder.build_filter_anchor_probe(limit=26)
    normalized_sql = " ".join(sql.split())
    assert "WHERE 1 = 1" in normalized_sql
    assert "ORDER BY" not in normalized_sql
    assert "LIMIT 1 BY" not in normalized_sql
    limit_clause = "LIMIT %(filter_anchor_limit)s"
    assert limit_clause in normalized_sql
    assert normalized_sql.index(limit_clause) < normalized_sql.index("SETTINGS")
    assert params["filter_anchor_limit"] == 26

    numbered_builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter()],
        page_size=25,
    )
    assert (
        numbered_builder.allow_filter_anchor_probe_for_initial_continuation() is False
    )
    assert numbered_builder.supports_filter_anchor_probe() is False

    filtered_builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
        page_size=25,
        bounded_internal_scan=True,
    )
    assert (
        filtered_builder.allow_filter_anchor_probe_for_initial_continuation() is False
    )


@pytest.mark.parametrize(
    "attribute_filter",
    [
        _attribute_filter("final_status", ["Rejected"], operation="in"),
        _attribute_filter("reviewed", True, filter_type="boolean"),
    ],
)
def test_long_window_span_string_and_bool_skip_speculative_full_window_anchor(
    attribute_filter: dict[str, Any],
) -> None:
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), attribute_filter],
        page_size=25,
    )

    assert builder.supports_filter_anchor_probe() is True
    assert builder.recommended_filter_anchor_probe_limit() is None
    assert builder.recommended_filter_anchor_probe_timeout_ms() is None
    assert builder.recommended_filter_anchor_probe_strata() is None
    assert builder.recommended_filter_anchor_probe_max_bytes_to_read() is None
    assert builder.skip_full_window_filter_anchor_probe() is True


@pytest.mark.parametrize(
    ("operation", "value"),
    [("equals", 7), ("in", [7, 8])],
)
def test_long_window_span_numeric_value_index_retains_bounded_anchor(
    operation: str,
    value: object,
) -> None:
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "attempt",
                value,
                filter_type="number",
                operation=operation,
            ),
        ],
        page_size=25,
    )

    assert builder.recommended_filter_anchor_probe_limit() == 64
    assert builder.recommended_filter_anchor_probe_timeout_ms() == 300
    assert builder.recommended_filter_anchor_probe_strata() == 4
    assert (
        builder.recommended_filter_anchor_probe_max_bytes_to_read() == 96 * 1024 * 1024
    )
    assert builder.skip_full_window_filter_anchor_probe() is False


def test_long_window_span_mixed_text_numeric_uses_numeric_value_index_anchor() -> None:
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
            _attribute_filter("attempt", 7, filter_type="number"),
        ],
        page_size=25,
    )

    assert builder.recommended_filter_anchor_probe_limit() == 64
    assert builder.skip_full_window_filter_anchor_probe() is False
    sql, _params = builder.build_filter_anchor_probe(limit=64)
    assert "has(mapValues(attrs_number)" in sql
    assert "attrs_string" in sql


def test_long_window_span_error_status_uses_indexed_full_window_anchor() -> None:
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _system_filter("status", "ERROR")],
        page_size=25,
        bounded_internal_scan=True,
    )

    assert builder.allow_filter_anchor_probe_for_initial_continuation() is True
    assert builder.supports_filter_anchor_probe() is True
    assert builder.recommended_filter_anchor_probe_limit() == 64
    assert builder.recommended_filter_anchor_probe_timeout_ms() == 300
    assert builder.recommended_filter_anchor_probe_strata() == 4
    assert builder.skip_full_window_filter_anchor_probe() is False
    sql, _params = builder.build_filter_anchor_probe(limit=64)
    assert "status" in sql
    assert "ERROR" not in sql  # the value remains a bound parameter


def test_long_window_trace_error_status_uses_global_indexed_anchor() -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _system_filter("status", "ERROR")],
        page_size=25,
        bounded_internal_scan=True,
    )

    assert builder.allow_filter_anchor_probe_for_initial_continuation() is True
    assert builder.supports_filter_anchor_probe() is True
    assert builder.filter_anchor_probe_proves_complete_population() is True
    assert builder.recommended_filter_anchor_probe_limit() == 64
    assert builder.recommended_filter_anchor_probe_timeout_ms() == 900
    assert builder.recommended_filter_anchor_probe_strata() == 1
    assert builder.skip_full_window_filter_anchor_probe() is False

    sql, params = builder.build_filter_anchor_probe(limit=64)
    normalized_sql = " ".join(sql.split())
    assert "status IN %(trace_error_status_anchor_values_0)s" in normalized_sql
    assert "filter_anchor_start" not in normalized_sql
    assert "start_time >=" not in normalized_sql
    assert params["trace_error_status_anchor_values_0"] == ("ERROR",)
    assert params["filter_anchor_limit"] == 64


def test_long_window_voice_error_status_forwards_global_indexed_anchor() -> None:
    filters = [_time_filter(), _system_filter("status", "ERROR")]
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        page_size=15,
    )

    assert builder.allow_filter_anchor_probe_for_initial_continuation() is True
    assert builder.supports_filter_anchor_probe() is True
    assert builder.filter_anchor_probe_proves_complete_population() is True
    assert builder.recommended_filter_anchor_probe_limit() == 64
    assert builder.recommended_filter_anchor_probe_timeout_ms() == 900
    assert builder.recommended_filter_anchor_probe_strata() == 1
    assert builder.skip_full_window_filter_anchor_probe() is False

    sql, params = builder.build_filter_anchor_probe(limit=64)
    normalized_sql = " ".join(sql.split())
    assert "status IN %(trace_error_status_anchor_values_0)s" in normalized_sql
    assert "start_time >=" not in normalized_sql
    assert params["trace_error_status_anchor_values_0"] == ("ERROR",)
    assert params["filter_anchor_limit"] == 64


def test_long_window_trace_exact_text_uses_complete_indexed_anchor() -> None:
    recording_url = (
        "https://storage.vapi.ai/019db06c-d54a-7003-9810-cf01cc4aa9d1-"
        "1776781471202"
    )
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "conversation.recording.mono.assistant",
                [recording_url],
                operation="in",
            ),
        ],
        page_size=25,
    )

    assert builder.allow_filter_anchor_probe_for_initial_continuation() is True
    assert builder.supports_filter_anchor_probe() is True
    assert builder.filter_anchor_probe_proves_complete_population() is True
    assert builder.recommended_filter_anchor_probe_limit() == 64
    assert builder.recommended_filter_anchor_probe_timeout_ms() is None
    assert builder.recommended_filter_anchor_probe_strata() == 1
    assert builder.recommended_filter_anchor_probe_max_bytes_to_read() is None
    assert builder.skip_full_window_filter_anchor_probe() is False

    sql, params = builder.build_filter_anchor_probe(limit=64)
    normalized_sql = " ".join(sql.split())
    assert "attrs_string" in normalized_sql
    assert "span_attr_str" not in normalized_sql
    assert "start_time >=" not in normalized_sql
    assert "filter_anchor_start" not in params
    assert "LIMIT 1 BY trace_id" in normalized_sql
    assert params["latest_filter_key_0"] == (
        "conversation.recording.mono.assistant"
    )
    assert params["latest_filter_param_0"] == (recording_url,)
    assert params["filter_anchor_limit"] == 64


def test_long_window_voice_exact_text_forwards_complete_indexed_anchor() -> None:
    recording_url = (
        "https://storage.vapi.ai/019db06c-d54a-7003-9810-cf01cc4aa9d1-"
        "1776781471202"
    )
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "conversation.recording.mono.assistant",
                [recording_url],
                operation="in",
            ),
        ],
        page_size=25,
    )

    assert builder.allow_filter_anchor_probe_for_initial_continuation() is True
    assert builder.supports_filter_anchor_probe() is True
    assert builder.filter_anchor_probe_proves_complete_population() is True
    assert builder.recommended_filter_anchor_probe_limit() == 64
    assert builder.recommended_filter_anchor_probe_timeout_ms() is None
    assert builder.recommended_filter_anchor_probe_strata() == 1
    assert builder.recommended_filter_anchor_probe_max_bytes_to_read() is None
    assert builder.skip_full_window_filter_anchor_probe() is False

    sql, params = builder.build_filter_anchor_probe(limit=64)
    normalized_sql = " ".join(sql.split())
    assert "attrs_string" in normalized_sql
    assert "span_attr_str" not in normalized_sql
    assert "start_time >=" not in normalized_sql
    assert "filter_anchor_start" not in params
    assert "LIMIT 1 BY trace_id" in normalized_sql
    assert params["latest_filter_key_0"] == (
        "conversation.recording.mono.assistant"
    )
    assert params["latest_filter_param_0"] == (recording_url,)
    assert params["filter_anchor_limit"] == 64


@pytest.mark.parametrize(
    "builder_kwargs,window_start",
    [
        ({}, END - timedelta(hours=1)),
        (
            {
                "bounded_identity_only": True,
                "bounded_bulk_scan": True,
            },
            START,
        ),
        (
            {
                "bounded_sampling_rate": 10.0,
                "bounded_sampling_salt": "sample",
            },
            START,
        ),
    ],
)
def test_complete_exact_text_anchor_stays_out_of_excluded_read_modes(
    builder_kwargs: dict[str, object],
    window_start: datetime,
) -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(window_start, END),
            _attribute_filter(
                "conversation.recording.mono.assistant",
                ["https://storage.vapi.ai/" + "a" * 64],
                operation="in",
            ),
        ],
        page_size=25,
        **builder_kwargs,
    )

    assert builder.allow_filter_anchor_probe_for_initial_continuation() is False
    assert builder.filter_anchor_probe_proves_complete_population() is False


def test_complete_exact_text_anchor_selects_long_leaf_among_siblings() -> None:
    recording_url = "https://storage.vapi.ai/" + "b" * 64
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
            _attribute_filter(
                "conversation.recording.mono.assistant",
                [recording_url],
                operation="in",
            ),
        ],
        page_size=25,
    )

    sql, params = builder.build_filter_anchor_probe(limit=64)

    assert "attrs_string" in sql
    assert params["latest_filter_key_0"] == (
        "conversation.recording.mono.assistant"
    )
    assert params["latest_filter_param_0"] == (recording_url,)
    assert "latest_filter_key_1" not in params


def test_voice_custom_attribute_filter_skips_temporal_trace_anchor() -> None:
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(minutes=5), END),
            _attribute_filter("attempt", 7, filter_type="number"),
        ],
        page_size=25,
    )

    assert builder.filter_anchor_probe_proves_complete_population() is False
    assert builder.supports_filter_anchor_probe() is False


def test_long_window_voice_empty_error_anchor_completes_in_one_query() -> None:
    filters = [_time_filter(), _system_filter("status", "ERROR")]
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        page_size=15,
    )
    analytics = mock.Mock(supports_per_query_read_settings=True)
    analytics.execute_ch_query.return_value = QueryResult(
        [],
        0,
        "clickhouse",
        1.0,
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=analytics,
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=15,
        deadline_ms=9_500,
        max_seed_attempts=24,
        max_candidates=512,
        max_query_count=128,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is True
    assert page.status == "complete"
    assert page.error_code is None
    assert page.rows == []
    assert page.has_more is False
    assert page.continuation_slice_end is None
    analytics.execute_ch_query.assert_called_once()
    anchor_sql = analytics.execute_ch_query.call_args.args[0]
    assert "trace_error_status_anchor_values_0" in anchor_sql


def test_long_window_trace_completed_status_keeps_temporal_scanner() -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _system_filter("status", "OK")],
        page_size=25,
        bounded_internal_scan=True,
    )

    assert builder.allow_filter_anchor_probe_for_initial_continuation() is False
    assert builder.filter_anchor_probe_proves_complete_population() is False
    assert builder.recommended_filter_anchor_probe_limit() is None


def test_long_window_span_completed_status_keeps_temporal_scanner() -> None:
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _system_filter("status", "OK")],
        page_size=25,
        bounded_internal_scan=True,
    )

    assert builder.allow_filter_anchor_probe_for_initial_continuation() is False
    assert builder.recommended_filter_anchor_probe_limit() is None


def test_long_window_span_numeric_range_has_no_full_window_value_anchor() -> None:
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "attempt",
                7,
                filter_type="number",
                operation="greater_than",
            ),
        ],
        page_size=25,
    )

    assert builder.supports_filter_anchor_probe() is False
    assert builder.recommended_filter_anchor_probe_limit() is None
    assert builder.skip_full_window_filter_anchor_probe() is False


def test_short_window_span_string_filter_retains_default_anchor_contract() -> None:
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(minutes=30), END),
            _attribute_filter("final_status", "Rejected"),
        ],
        page_size=25,
    )

    assert builder.recommended_filter_anchor_probe_limit() is None
    assert builder.skip_full_window_filter_anchor_probe() is False


def test_negative_only_trace_filter_skips_long_window_anchor() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected", operation="not_equals"),
        ],
    )

    assert builder.supports_filter_anchor_probe() is False
    assert builder.recommended_filter_anchor_probe_limit() is None
    assert builder.recommended_filter_anchor_probe_timeout_ms() is None


def test_map_plus_json_anchor_uses_only_indexed_map_leaf() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
            _system_filter("call_type", "inbound"),
        ],
    )

    anchor_sql, anchor_params = builder.build_filter_anchor_probe(limit=513)
    normalized_anchor_sql = " ".join(anchor_sql.split())

    assert builder.supports_filter_anchor_probe() is True
    assert "SELECT trace_id FROM spans" in normalized_anchor_sql
    assert "SELECT DISTINCT" not in normalized_anchor_sql
    assert (
        "ORDER BY observation_type DESC, service_name DESC, "
        "toStartOfHour(start_time) DESC, trace_id DESC, id DESC"
        in normalized_anchor_sql
    )
    assert "LIMIT 1 BY trace_id" in normalized_anchor_sql
    assert normalized_anchor_sql.index("ORDER BY") < normalized_anchor_sql.index(
        "LIMIT 1 BY trace_id"
    )
    assert "has(span_attr_str.keys, %(latest_filter_key_0)s)" in anchor_sql
    assert "JSONExtract" not in anchor_sql
    assert anchor_params["latest_filter_key_0"] == "final_status"
    assert "latest_filter_param_1" not in anchor_params
    assert builder.recommended_filter_seed_batch_size() == 200
    assert builder.recommended_filter_classify_batch_size() == 10


def test_trace_candidate_classifier_keeps_root_window_and_global_child_witnesses() -> (
    None
):
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
    )

    sql, params = builder.build_filter_match_query(["trace-a"])

    prewhere = sql.split("GROUP BY trace_id, id, start_time", 1)[0]
    assert "candidate_witness_start_date_us" not in prewhere
    assert "candidate_witness_end_date_us" not in prewhere
    assert params["candidate_start_date"] == START
    assert params["candidate_end_date"] == END
    assert params["candidate_start_date_us"] == 1_735_689_600_000_000
    assert params["candidate_end_date_us"] == 1_767_225_600_000_000
    assert "candidate_witness_start_date_us" not in params
    assert "candidate_witness_end_date_us" not in params


def test_root_seed_replay_does_not_trust_one_raw_physical_root_id() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
    )

    sql, params = builder.build_filter_match_query_from_seed_rows(
        [
            {
                "trace_id": "trace-a",
                "root_span_id": "root-a",
                "start_time": END - timedelta(minutes=1),
            }
        ]
    )

    assert params["candidate_trace_ids"] == ("trace-a",)
    assert "candidate_root_span_ids" not in params
    assert "id IN %(candidate_root_span_ids)s" not in sql
    assert "trace_id IN %(candidate_trace_ids)s" in sql
    assert "argMaxIf(tuple(grouped_id)" in sql
    assert "SELECT id\n" not in sql
    assert sql.count("FROM spans") == 1


def test_trace_page_classifies_identity_only_then_hydrates_exact_roots() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
    )
    seed_rows = [
        {
            "trace_id": "trace-a",
            "root_span_id": "root-a",
            "start_time": END - timedelta(minutes=1),
        }
    ]

    identity_sql, identity_params = (
        builder.build_filter_identity_match_query_from_seed_rows(seed_rows)
    )
    hydration_sql, hydration_params = builder.build_filter_page_hydration_query(
        seed_rows
    )

    assert builder.use_identity_only_filter_classification() is True
    assert "canonical_root_identity.1 AS root_span_id" in identity_sql
    assert "canonical_root_identity.2 AS start_time" in identity_sql
    assert "latest_trace_name" not in identity_sql
    assert "latest_total_tokens" not in identity_sql
    assert "latest_attr_exists_0" in identity_sql
    assert identity_params["candidate_trace_ids"] == ("trace-a",)
    assert "page_hydration_root_identities" in hydration_params
    assert hydration_params["page_hydration_root_identities"] == (
        (
            PROJECT_ID,
            "trace-a",
            "root-a",
            int(seed_rows[0]["start_time"].replace(tzinfo=UTC).timestamp() * 1_000_000),
        ),
    )
    assert "toDate(start_time) IN %(page_hydration_root_dates)s" in hydration_sql
    assert "toUnixTimestamp64Micro(start_time)" in hydration_sql
    assert "latest_trace_name AS trace_name" in hydration_sql
    assert "latest_total_tokens AS total_tokens" in hydration_sql
    assert "latest_attr_exists_0" not in hydration_sql
    assert " FINAL" not in hydration_sql


def test_existing_identity_only_trace_consumer_does_not_add_page_hydration() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
        bounded_identity_only=True,
    )

    sql, _ = builder.build_filter_match_query(["trace-a"])

    assert builder.use_identity_only_filter_classification() is False
    # Graph/eval/task callers set this mode explicitly and retain their
    # established 50-row envelope; only normal list pages use 100.
    assert builder.recommended_filter_classify_batch_size() == 10
    assert "canonical_root_identity.1 AS root_span_id" in sql
    assert "canonical_root_identity.2 AS start_time" in sql
    assert "filter_witness_0" in sql


def test_ch25_rewrites_identity_classifier_and_exact_root_hydration() -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
    )
    rows = [
        {
            "project_id": PROJECT_ID,
            "trace_id": "trace-a",
            "root_span_id": "root-a",
            "start_time": END - timedelta(minutes=1),
        }
    ]

    identity_sql, _ = builder.build_filter_identity_match_query_from_seed_rows(rows)
    hydration_sql, _ = builder.build_filter_page_hydration_query(rows)

    for sql in (identity_sql, hydration_sql):
        assert "_peerdb_version" not in sql
        assert "_peerdb_is_deleted" not in sql
        assert "_version" in sql
        assert "SETTINGS" in sql
    assert "canonical_root_identity.1 AS root_span_id" in identity_sql
    assert "toUnixTimestamp64Micro(start_time)" in hydration_sql


def test_org_trace_builder_keeps_project_in_seed_classifier_and_page_keys() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    filters = [_time_filter(), _attribute_filter("final_status", "Rejected")]
    builder = TraceListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        filters=filters,
    )
    started = END - timedelta(minutes=1)
    seed_rows = [
        {
            "project_id": PROJECT_ID,
            "trace_id": "shared-trace",
            "root_span_id": "root-a",
            "start_time": started,
        },
        {
            "project_id": project_b,
            "trace_id": "shared-trace",
            "root_span_id": "root-b",
            "start_time": started,
        },
    ]

    anchor_sql, _ = builder.build_filter_anchor_probe(limit=513)
    ordered_sql, ordered_params = builder.build_filter_ordered_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=50,
        before_start_time=started,
        before_id=("shared-trace", project_b),
    )
    match_sql, match_params = builder.build_filter_match_query_from_seed_rows(seed_rows)
    unfiltered = TraceListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        filters=[_time_filter()],
    )
    list_sql, _ = unfiltered.build()
    count_sql, _ = unfiltered.build_count_query()
    attributes_sql, _ = unfiltered.build_span_attributes_query(["shared-trace"])

    normalized_anchor_sql = " ".join(anchor_sql.split())
    assert "SELECT project_id, trace_id FROM spans" in normalized_anchor_sql
    assert "SELECT DISTINCT" not in normalized_anchor_sql
    assert (
        "ORDER BY project_id DESC, observation_type DESC, service_name DESC, "
        "toStartOfHour(start_time) DESC, trace_id DESC, id DESC"
        in normalized_anchor_sql
    )
    assert "LIMIT 1 BY project_id, trace_id" in normalized_anchor_sql
    assert "SELECT project_id, trace_id, id AS root_span_id" in ordered_sql
    assert (
        "ORDER BY start_time DESC, trace_id DESC, toString(project_id) DESC"
        in ordered_sql
    )
    assert "LIMIT 1 BY project_id, trace_id" in ordered_sql
    assert "toString(project_id) < %(filter_before_project_id)s" in ordered_sql
    assert ordered_params["filter_before_id"] == "shared-trace"
    assert ordered_params["filter_before_project_id"] == project_b
    assert match_params["candidate_trace_identities"] == (
        (PROJECT_ID, "shared-trace"),
        (project_b, "shared-trace"),
    )
    assert (
        match_sql.count("(project_id, trace_id) IN %(candidate_trace_identities)s") == 1
    )
    assert match_sql.count("%(candidate_trace_identities)s") == 1
    assert "(grouped_project_id, grouped_trace_id) IN" not in " ".join(
        match_sql.split()
    )
    assert "GROUP BY project_id, trace_id, id, start_time" in match_sql
    assert "GROUP BY grouped_project_id, grouped_trace_id" in match_sql
    assert (
        "ORDER BY start_time DESC, trace_id DESC, toString(project_id) DESC"
        in match_sql
    )
    assert "LIMIT 2" in match_sql
    assert builder.bounded_filter_row_identity(seed_rows[0]) == (
        PROJECT_ID,
        "shared-trace",
    )
    assert builder.bounded_filter_row_order_token(seed_rows[0]) == (
        "shared-trace",
        PROJECT_ID,
    )
    assert "ORDER BY start_time DESC, trace_id DESC, project_id DESC" in list_sql
    assert "uniq(project_id, trace_id) AS total" in count_sql
    assert "toString(project_id) AS project_id" in attributes_sql

    single_project = TraceListQueryBuilder(project_id=PROJECT_ID, filters=filters)
    assert single_project.bounded_filter_row_identity(seed_rows[0]) == "shared-trace"
    assert single_project.bounded_filter_row_order_token(seed_rows[0]) == (
        "shared-trace"
    )


def test_span_match_compiles_typed_map_json_and_multi_filter_at_latest_state() -> None:
    filters = [
        _time_filter(),
        _system_filter("status", ["SUCCESS"], operation="in"),
        _attribute_filter("customer.tier", "enterprise"),
        _attribute_filter(
            "quality", 0.8, filter_type="number", operation="greater_than"
        ),
        _attribute_filter("reviewed", True, filter_type="boolean"),
        _system_filter("call_type", "inbound"),
    ]
    builder = SpanListQueryBuilder(project_id=PROJECT_ID, filters=filters)

    sql, params = builder.build_filter_match_query(["span-a", "span-b"])

    assert params["candidate_span_ids"] == ("span-a", "span-b")
    assert "argMax(tuple(status), _peerdb_version).1" in sql
    assert "argMax(mapContains(span_attr_str, %(latest_filter_key_1)s)" in sql
    assert "argMax(mapContains(span_attr_num, %(latest_filter_key_2)s)" in sql
    assert "argMax(mapContains(span_attr_bool, %(latest_filter_key_3)s)" in sql
    assert params["latest_filter_key_1"] == "customer.tier"
    assert params["latest_filter_key_2"] == "quality"
    assert params["latest_filter_key_3"] == "reviewed"
    assert "JSONExtractString(span_attributes_raw, 'raw_log', 'type')" in sql
    assert "argMax(is_deleted, _peerdb_version)" in sql
    assert "latest_column_value_0" in sql
    assert "latest_attr_exists_1" in sql
    assert "latest_attr_exists_2" in sql
    assert "latest_attr_exists_3" in sql
    assert "latest_expression_value_4" in sql
    assert "observation_type = 'conversation'" in sql
    assert "GROUP BY project_id, trace_id, id, start_time" in sql


def test_span_seed_replay_uses_trace_scoped_otel_identity() -> None:
    project_version_id = "00000000-0000-4000-8000-000000000099"
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        project_version_id=project_version_id,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
    )
    assert builder.supports_bounded_filter_scan() is True

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
        before_start_time=END - timedelta(minutes=1),
        before_id=("shared-span-id", "trace-z", PROJECT_ID),
    )
    match_sql, params = builder.build_filter_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT_ID,
                "id": "shared-span-id",
                "trace_id": "trace-a",
                "start_time": END - timedelta(minutes=1),
            },
            {
                "project_id": PROJECT_ID,
                "id": "shared-span-id",
                "trace_id": "trace-b",
                "start_time": END - timedelta(minutes=2),
            },
        ]
    )

    assert "SELECT project_id, id, trace_id, start_time" in seed_sql
    assert "project_version_id = %(project_version_id)s" in seed_sql
    assert seed_params["project_version_id"] == project_version_id
    assert "LIMIT 1 BY project_id, trace_id, id, start_time" in seed_sql
    assert "ORDER BY start_time DESC, id DESC, trace_id DESC," in seed_sql
    assert "toString(project_id) DESC" in seed_sql
    assert "toString(project_id) < %(filter_before_project_id)s" in seed_sql
    assert seed_params["filter_before_project_id"] == PROJECT_ID
    assert params["candidate_span_ids"] == ("shared-span-id",)
    assert params["candidate_span_trace_ids"] == ("trace-a", "trace-b")
    first_start = END - timedelta(minutes=1)
    second_start = END - timedelta(minutes=2)
    assert params["candidate_span_identities"] == (
        (
            PROJECT_ID,
            "trace-a",
            "shared-span-id",
            int(first_start.replace(tzinfo=UTC).timestamp() * 1_000_000),
        ),
        (
            PROJECT_ID,
            "trace-b",
            "shared-span-id",
            int(second_start.replace(tzinfo=UTC).timestamp() * 1_000_000),
        ),
    )
    assert params["candidate_span_dates"] == (first_start.date(),)
    assert "toUnixTimestamp64Micro(start_time)" in match_sql
    assert "IN %(candidate_span_identities)s" in match_sql
    assert "ORDER BY start_time DESC, id DESC, trace_id DESC," in match_sql
    assert "toString(project_id) DESC" in match_sql
    assert "project_version_id = %(project_version_id)s" in match_sql
    assert params["project_version_id"] == project_version_id
    assert "GROUP BY project_id, trace_id, id, start_time" in match_sql


def test_trace_and_span_seed_slice_bounds_preserve_microseconds() -> None:
    slice_start = START + timedelta(microseconds=123_456)
    slice_end = START + timedelta(seconds=1, microseconds=654_321)
    filters = [
        _time_filter(slice_start, slice_end),
        _attribute_filter("final_status", "Rejected"),
    ]

    trace_builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
    )
    trace_sql, trace_params = trace_builder.build_filter_seed_page(
        slice_start=slice_start,
        slice_end=slice_end,
        limit=1,
    )
    span_builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_anchor_probe=True,
    )
    span_sql, span_params = span_builder.build_filter_seed_page(
        slice_start=slice_start,
        slice_end=slice_end,
        limit=1,
    )

    for sql, params in ((trace_sql, trace_params), (span_sql, span_params)):
        assert (
            "start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s)" in sql
        )
        assert "start_time < fromUnixTimestamp64Micro(%(filter_slice_end_us)s)" in sql
        assert params["filter_slice_start"] == slice_start
        assert params["filter_slice_end"] == slice_end
        assert params["filter_slice_start_us"] == 1_735_689_600_123_456
        assert params["filter_slice_end_us"] == 1_735_689_601_654_321

    trace_anchor_sql, trace_anchor_params = trace_builder.build_filter_anchor_probe(
        limit=2
    )
    span_anchor_sql, span_anchor_params = span_builder.build_filter_anchor_probe(
        limit=2
    )
    for sql, params in (
        (trace_anchor_sql, trace_anchor_params),
        (span_anchor_sql, span_anchor_params),
    ):
        assert (
            "start_time >= fromUnixTimestamp64Micro(%(filter_anchor_start_us)s)" in sql
        )
        assert "start_time < fromUnixTimestamp64Micro(%(filter_anchor_end_us)s)" in sql
        assert params["filter_anchor_start_us"] == 1_735_689_600_123_456
        assert params["filter_anchor_end_us"] == 1_735_689_601_654_321

    trace_match_sql, trace_match_params = trace_builder.build_filter_match_query(
        ["trace-a"]
    )
    span_match_sql, span_match_params = span_builder.build_filter_match_query(
        ["span-a"]
    )
    for sql, params in (
        (trace_match_sql, trace_match_params),
        (span_match_sql, span_match_params),
    ):
        assert (
            "latest_start_time >= "
            "fromUnixTimestamp64Micro(%(candidate_start_date_us)s)" in sql
        )
        assert (
            "latest_start_time < "
            "fromUnixTimestamp64Micro(%(candidate_end_date_us)s)" in sql
        )
        assert params["candidate_start_date_us"] == 1_735_689_600_123_456
        assert params["candidate_end_date_us"] == 1_735_689_601_654_321


def test_v2_span_seed_uses_typed_value_witness_before_exact_replay() -> None:
    filters = [
        _time_filter(),
        _attribute_filter("final_status", ["Rejected"], operation="in"),
    ]
    builder = SpanListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)

    sql, params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
    )

    assert "has(attrs_string.keys, %(latest_filter_key_0)s)" in sql
    assert "indexHint(has(mapKeys(attrs_string), %(latest_filter_key_0)s))" in sql
    assert "arrayMap(x -> lowerUTF8(x), mapValues(attrs_string))" in sql
    assert "arrayMap(x -> lower(x), mapValues(attrs_string))" not in sql
    assert params["latest_filter_key_0"] == "final_status"
    assert params["latest_filter_param_0"] == ("rejected",)
    assert params["latest_filter_index_0_0"] == "rejected"

    prompt_builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("prompt_slug", "agent_2_identity_disclosure"),
        ],
    )
    prompt_sql, prompt_params = prompt_builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
    )
    assert "has(attrs_string.keys, %(latest_filter_key_0)s)" in prompt_sql
    assert (
        "indexHint(has(mapKeys(attrs_string), %(latest_filter_key_0)s))" in prompt_sql
    )
    assert "arrayMap(x -> lowerUTF8(x), mapValues(attrs_string))" in prompt_sql
    assert "arrayMap(x -> lower(x), mapValues(attrs_string))" not in prompt_sql
    assert prompt_params["latest_filter_param_0"] == "agent_2_identity_disclosure"
    assert prompt_params["latest_filter_key_0"] == "prompt_slug"


@pytest.mark.parametrize(
    "filter_type,value", [("text", "x"), ("number", 1.5), ("boolean", True)]
)
def test_native_map_attribute_types_remain_bounded(
    filter_type: str, value: object
) -> None:
    filters = [
        _time_filter(),
        _attribute_filter("typed_key", value, filter_type=filter_type),
    ]
    builder = SpanListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)

    assert builder.supports_bounded_filter_scan() is True
    assert builder.bounded_filter_degraded_error_code() is None


@pytest.mark.parametrize(
    "filters",
    [
        [
            _time_filter(),
            _attribute_filter(
                "overflow_payload", {"nested": [1, 2]}, filter_type="json"
            ),
        ],
        [
            _time_filter(),
            _attribute_filter("typed_key", "x"),
            _attribute_filter(
                "overflow_payload", {"nested": [1, 2]}, filter_type="json"
            ),
        ],
    ],
)
def test_attributes_extra_json_filter_fails_closed_with_explicit_degradation(
    filters: list[dict[str, Any]],
) -> None:
    builder = SpanListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)

    assert builder.supports_bounded_filter_scan() is False
    assert builder.bounded_filter_degraded_error_code() == "unsupported_filter_shape"
    with pytest.raises(ValueError, match="unsupported_filter_shape"):
        builder.build()


def test_text_filter_treats_sql_wildcards_as_literal_user_text() -> None:
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "customer.note",
                r"50%_off\\today",
                operation="contains",
            ),
        ],
    )

    sql, params = builder.build_filter_match_query(["span-a"])

    assert "positionUTF8(" in sql
    assert " LIKE " not in sql
    assert r"50%_off\\today" in params.values()


def test_trace_attribute_can_match_only_a_child_span() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("customer.final_status", ["Rejected"], operation="in"),
        ],
    )

    seed_sql, _ = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
    )
    match_sql, _ = builder.build_filter_match_query(["trace-with-child-value"])

    assert "has(span_attr_str.keys, %(latest_filter_key_0)s)" in seed_sql
    assert "parent_span_id IS NULL" not in seed_sql
    assert "HAVING countIf(" in match_sql
    assert "GROUP BY trace_id, id, start_time" in match_sql
    assert "mapContains(span_attr_str, %(latest_filter_key_0)s)" in match_sql


@pytest.mark.parametrize("key", ["final_status", "country"])
def test_covered_rollup_names_retain_public_any_span_semantics(key: str) -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter(key, "value")],
    )

    seed_sql, _ = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
    )
    match_sql, _ = builder.build_filter_match_query(["trace-a"])

    assert "parent_span_id IS NULL" not in seed_sql
    assert "has(span_attr_str.keys, %(latest_filter_key_0)s)" in seed_sql
    assert "HAVING countIf(" in match_sql
    assert builder.filter_seed_proves_result_order() is False


def test_trace_mixed_root_and_any_span_filters_keep_distinct_scopes() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _system_filter("trace_name", "Café"),
            _attribute_filter("customer.final_status", "Rejected"),
        ],
    )

    seed_sql, _ = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
    )
    match_sql, params = builder.build_filter_match_query(["trace-a"])

    assert "lowerUTF8(toString(trace_name))" not in seed_sql
    assert "has(span_attr_str.keys, %(latest_filter_key_1)s)" in seed_sql
    assert "argMax(trace_name, _peerdb_version)" in match_sql
    assert "mapContains(span_attr_str, %(latest_filter_key_1)s)" in match_sql
    assert params["latest_filter_param_0"] == "café"
    assert params["latest_filter_param_1"] == "rejected"


def test_mixed_attribute_and_annotation_stays_in_one_bounded_trace_classifier() -> None:
    label_id = "00000000-0000-4000-8000-000000000099"
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
            _annotation_filter(label_id, "approved"),
        ],
    )

    assert builder.supports_bounded_filter_scan() is True
    assert builder.bounded_filter_degraded_error_code() is None
    seed_sql, _ = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
    )
    match_sql, params = builder.build_filter_match_query(["trace-a"])

    assert "model_hub_score" not in seed_sql
    assert "has(span_attr_str.keys, %(latest_filter_key_0)s)" in seed_sql
    assert "model_hub_score AS s FINAL" in match_sql
    assert "s.tracer_project_id = toUUID(%(project_id)s)" in match_sql
    assert "latest_attr_exists_0" in match_sql
    assert "%(candidate_trace_ids)s" in match_sql
    assert "toString(if(" in match_sql
    assert "toString(s.observation_span_id) IN (" in match_sql
    assert "toString(trace_id) IN %(candidate_trace_ids)s" in match_sql
    assert params["candidate_trace_ids"] == ("trace-a",)
    assert params["ann_label_1"] == label_id


def test_eval_residual_is_candidate_scoped_inside_same_trace_match_query() -> None:
    eval_id = "00000000-0000-4000-8000-000000000088"
    template_id = "00000000-0000-4000-8000-000000000087"

    class _Values(list):
        def first(self):
            return self[0] if self else None

    class _ConfigQuery:
        def filter(self, **_kwargs):
            return self

        def exists(self):
            return True

        def values_list(self, field, **_kwargs):
            return _Values([template_id if field == "eval_template_id" else eval_id])

    class _TemplateQuery:
        def values(self, *_args):
            return self

        def first(self):
            return {"config": {"output": "SCORE"}}

    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _system_filter("status", "SUCCESS"),
            _eval_filter(eval_id, 75),
        ],
    )

    with (
        mock.patch(
            "tracer.models.custom_eval_config.CustomEvalConfig.objects.filter",
            return_value=_ConfigQuery(),
        ),
        mock.patch(
            "model_hub.models.evals_metric.EvalTemplate.no_workspace_objects.filter",
            return_value=_TemplateQuery(),
        ),
    ):
        sql, params = builder.build_filter_match_query(["trace-a", "trace-b"])

    assert builder.supports_bounded_filter_scan() is True
    assert "argMax(tuple(status), _peerdb_version).1" in sql
    assert "custom_eval_config_id IN" in sql
    assert "toString(eval_scan.trace_id) IN %(candidate_trace_ids)s" in sql
    assert params["candidate_trace_ids"] == ("trace-a", "trace-b")
    assert params["eval_cfg_1"] == (eval_id,)


def _org_collision_seed_rows(project_b: str) -> list[dict[str, Any]]:
    started = END - timedelta(minutes=1)
    return [
        {
            "project_id": project_id,
            "trace_id": "shared-trace",
            "root_span_id": root_span_id,
            "start_time": started,
        }
        for project_id, root_span_id in (
            (PROJECT_ID, "root-a"),
            (project_b, "root-b"),
        )
    ]


def test_org_annotation_residual_correlates_shared_trace_by_project() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    label_id = "00000000-0000-4000-8000-000000000099"
    builder = TraceListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        filters=[_time_filter(), _annotation_filter(label_id, "approved")],
    )

    sql, params = builder.build_filter_match_query_from_seed_rows(
        _org_collision_seed_rows(project_b)
    )

    assert sql.count("tracer_project_id = toUUID(") >= 2
    assert sql.count("outer_project_id)s) AND") == 2
    assert params["org_residual_0_candidate_trace_ids"] == ("shared-trace",)
    assert params["org_residual_1_candidate_trace_ids"] == ("shared-trace",)
    assert params["org_residual_0_project_id"] == PROJECT_ID
    assert params["org_residual_1_project_id"] == project_b
    assert params["org_residual_0_outer_project_id"] == PROJECT_ID
    assert params["org_residual_1_outer_project_id"] == project_b


def test_org_trace_has_annotation_uses_each_projects_disjoint_labels() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    label_a = "00000000-0000-4000-8000-000000000091"
    label_b = "00000000-0000-4000-8000-000000000092"
    builder = TraceListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        annotation_label_ids=[label_a, label_b],
        annotation_label_ids_by_project={
            PROJECT_ID: [label_a],
            project_b: [label_b],
        },
        filters=[_time_filter(), _has_annotation_filter(True)],
    )

    sql, params = builder.build_filter_match_query_from_seed_rows(
        _org_collision_seed_rows(project_b)
    )

    assert params["org_residual_0_lbl_1"] == label_a
    assert params["org_residual_1_lbl_1"] == label_b
    assert "org_residual_0_lbl_2" not in params
    assert "org_residual_1_lbl_2" not in params
    assert sql.count("HAVING uniqExact(s.label_id) >= 1") == 2
    assert sql.count("outer_project_id)s) AND") == 2
    assert params["org_residual_0_project_id"] == PROJECT_ID
    assert params["org_residual_1_project_id"] == project_b


def test_known_empty_project_label_set_never_falls_back_to_score_existence() -> None:
    from tracer.services.clickhouse.query_builders.filters import (
        ClickHouseFilterBuilder,
    )

    positive = ClickHouseFilterBuilder(
        project_id=PROJECT_ID,
        annotation_label_ids=[],
        annotation_label_set_known=True,
    )
    negative = ClickHouseFilterBuilder(
        project_id=PROJECT_ID,
        annotation_label_ids=[],
        annotation_label_set_known=True,
    )

    positive_sql, _ = positive.translate([_has_annotation_filter(True)])
    negative_sql, _ = negative.translate([_has_annotation_filter(False)])

    assert positive_sql == "1 = 1"
    assert negative_sql == "0 = 1"
    assert "model_hub_score" not in positive_sql + negative_sql


def test_org_eval_residual_does_not_admit_same_trace_from_other_project() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    eval_id = "00000000-0000-4000-8000-000000000088"
    config_manager = _ProjectConfigManager({PROJECT_ID: (eval_id,), project_b: ()})
    builder = TraceListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        filters=[_time_filter(), _eval_filter(eval_id, 75)],
    )

    with (
        mock.patch(
            "tracer.models.custom_eval_config.CustomEvalConfig.objects",
            config_manager,
        ),
        mock.patch(
            "model_hub.models.evals_metric.EvalTemplate.no_workspace_objects.filter",
            return_value=_ScoreTemplateQuery(),
        ),
    ):
        sql, params = builder.build_filter_match_query_from_seed_rows(
            _org_collision_seed_rows(project_b)
        )

    assert sql.count("outer_project_id)s) AND") == 2
    assert "org_residual_0_eval_cfg_1" in params
    assert params["org_residual_0_eval_cfg_1"] == (eval_id,)
    assert "org_residual_1_eval_cfg_1" not in params
    assert "SELECT toUUID('00000000-0000-0000-0000-000000000000')" in sql
    assert params["org_residual_0_outer_project_id"] == PROJECT_ID
    assert params["org_residual_1_outer_project_id"] == project_b


def test_org_has_eval_residual_uses_project_owned_configs_for_shared_trace() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    config_id = "00000000-0000-4000-8000-000000000088"
    config_manager = _ProjectConfigManager({PROJECT_ID: (config_id,), project_b: ()})
    builder = TraceListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        filters=[
            _time_filter(),
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
        ],
    )

    with mock.patch(
        "tracer.models.custom_eval_config.CustomEvalConfig.objects",
        config_manager,
    ):
        sql, params = builder.build_filter_match_query_from_seed_rows(
            _org_collision_seed_rows(project_b)
        )

    assert params["org_residual_0_project_eval_cfg_1"] == (config_id,)
    assert "org_residual_1_project_eval_cfg_1" not in params
    assert "eval_scan.custom_eval_config_id" in sql
    assert sql.count("outer_project_id)s) AND") == 2


@pytest.mark.parametrize("filter_value", [False, "false"])
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_trace_has_eval_false_is_latest_state_candidate_scoped(
    filter_value: bool | str,
) -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _has_eval_filter(filter_value)],
        eval_config_ids=["00000000-0000-4000-8000-000000000088"],
    )

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=END,
        limit=50,
        before_start_time=END - timedelta(minutes=1),
        before_id="trace-z",
    )
    sql, params = builder.build_filter_match_query(["trace-a", "trace-b"])

    assert builder.supports_bounded_filter_scan() is True
    assert builder.supports_filter_candidate_seed_page() is False
    assert "tracer_eval_logger" not in seed_sql
    assert seed_params["filter_before_id"] == "trace-z"
    assert "trace_id NOT IN (" in sql
    assert "FROM tracer_eval_logger_v2 AS eval_scan" in sql
    assert "toString(eval_scan.trace_id) IN %(candidate_trace_ids)s" in sql
    # Trace time binds the canonical root. An eval may be written later, so
    # candidate-scoped has_eval membership must inspect its complete history.
    assert "eval_scan.created_at >= %(start_date)s - INTERVAL 7 DAY" not in sql
    assert "ORDER BY eval_scan._version DESC" in sql
    assert "LIMIT 1 BY eval_scan.id" in sql
    assert "latest_eval.is_deleted = 0" in sql
    assert "tracer_eval_logger_v2 FINAL" not in sql
    assert params["candidate_trace_ids"] == ("trace-a", "trace-b")


@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_trace_has_eval_true_regression_and_false_combination_remain_exact() -> None:
    positive = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _has_eval_filter(True)],
        eval_config_ids=["00000000-0000-4000-8000-000000000088"],
    )
    positive_sql, _ = positive.build_filter_match_query(["trace-a"])

    combined = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
            _has_eval_filter(False),
        ],
        eval_config_ids=["00000000-0000-4000-8000-000000000088"],
    )
    combined_sql, combined_params = combined.build_filter_match_query(["trace-a"])

    assert "trace_id IN (" in positive_sql
    assert "trace_id NOT IN (" not in positive_sql
    assert "trace_id NOT IN (" in combined_sql
    assert "attrs_string" in combined_sql
    assert "rejected" in combined_params.values()
    assert combined_params["candidate_trace_ids"] == ("trace-a",)


def test_org_has_eval_false_keeps_every_branch_tenant_and_candidate_scoped() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    config_id = "00000000-0000-4000-8000-000000000088"
    config_manager = _ProjectConfigManager({PROJECT_ID: (config_id,), project_b: ()})
    builder = TraceListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        filters=[_time_filter(), _has_eval_filter(False)],
    )

    with mock.patch(
        "tracer.models.custom_eval_config.CustomEvalConfig.objects",
        config_manager,
    ):
        sql, params = builder.build_filter_match_query_from_seed_rows(
            _org_collision_seed_rows(project_b)
        )

    assert "trace_id NOT IN" in sql
    assert sql.count("outer_project_id)s) AND") == 2
    assert params["org_residual_0_candidate_trace_ids"] == ("shared-trace",)
    assert params["org_residual_0_project_eval_cfg_1"] == (config_id,)
    assert "org_residual_1_project_eval_cfg_1" not in params
    # The project-B branch has no owned eval configs, so it reduces exactly to
    # NOT IN an impossible UUID without reading the eval table at all.
    assert "trace_id NOT IN (SELECT toUUID(" in sql


def test_org_end_user_negative_residual_is_project_scoped_for_shared_trace() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    builder = TraceListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        filters=[
            _time_filter(),
            _end_user_filter("customer@example.com", operation="not_equals"),
        ],
    )

    sql, params = builder.build_filter_match_query_from_seed_rows(
        _org_collision_seed_rows(project_b)
    )

    assert sql.count("trace_id NOT IN") == 2
    assert sql.count("outer_project_id)s) AND") == 2
    assert params["org_residual_0_project_id"] == PROJECT_ID
    assert params["org_residual_1_project_id"] == project_b
    assert params["org_residual_0_candidate_trace_ids"] == ("shared-trace",)
    assert params["org_residual_1_candidate_trace_ids"] == ("shared-trace",)


def test_org_combined_residuals_keep_each_shared_trace_branch_project_local() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    label_id = "00000000-0000-4000-8000-000000000099"
    eval_id = "00000000-0000-4000-8000-000000000088"
    config_manager = _ProjectConfigManager(
        {PROJECT_ID: (eval_id,), project_b: (eval_id,)}
    )
    builder = TraceListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        filters=[
            _time_filter(),
            _annotation_filter(label_id, "approved"),
            _eval_filter(eval_id, 75),
            _end_user_filter("customer@example.com"),
        ],
    )

    with (
        mock.patch(
            "tracer.models.custom_eval_config.CustomEvalConfig.objects",
            config_manager,
        ),
        mock.patch(
            "model_hub.models.evals_metric.EvalTemplate.no_workspace_objects.filter",
            return_value=_ScoreTemplateQuery(),
        ),
    ):
        sql, params = builder.build_filter_match_query_from_seed_rows(
            _org_collision_seed_rows(project_b)
        )

    assert sql.count("model_hub_score AS s FINAL") >= 2
    assert sql.count(" AS eval_scan") >= 2
    assert sql.count("tracer_enduser FINAL") == 2
    assert sql.count("outer_project_id)s) AND") == 2
    assert params["org_residual_0_project_id"] == PROJECT_ID
    assert params["org_residual_1_project_id"] == project_b
    assert params["org_residual_0_candidate_trace_ids"] == ("shared-trace",)
    assert params["org_residual_1_candidate_trace_ids"] == ("shared-trace",)
    assert not re.search(r"%\(project_id\)s", sql)


def test_span_annotation_classifier_scopes_score_and_span_sides_to_candidates() -> None:
    label_id = "00000000-0000-4000-8000-000000000077"
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _annotation_filter(label_id, 3, filter_type="number")],
    )

    sql, params = builder.build_filter_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "id": "span-a",
                "start_time": END - timedelta(minutes=1),
            },
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-b",
                "id": "span-b",
                "start_time": END - timedelta(minutes=2),
            },
        ]
    )

    assert builder.supports_bounded_filter_scan() is True
    assert "toString(if(" in sql
    assert "scored_sp.id = s.observation_span_id" in sql
    assert "scored_sp.trace_id" in sql
    assert "IN %(candidate_span_entities)s" in sql
    # Raw Score rows can lack trace_id, so their pre-join candidate probe uses
    # span ids as a safe superset; the resolved tuple above is the exact guard.
    assert "toString(s.observation_span_id) IN %(candidate_span_ids)s" in sql
    assert "(toString(trace_id), toString(id)) IN %(candidate_span_entities)s" in sql
    assert params["candidate_span_ids"] == ("span-a", "span-b")
    assert params["candidate_span_entities"] == (
        ("trace-a", "span-a"),
        ("trace-b", "span-b"),
    )
    assert params["ann_label_1"] == label_id


def _org_span_collision_seed_rows(project_b: str) -> list[dict[str, Any]]:
    started = END - timedelta(minutes=1)
    return [
        {
            "project_id": project_id,
            "trace_id": "shared-trace",
            "id": "shared-span",
            "start_time": started,
        }
        for project_id in (PROJECT_ID, project_b)
    ]


def test_org_span_annotation_residual_isolates_same_textual_identity() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    label_id = "00000000-0000-4000-8000-000000000077"
    builder = SpanListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        filters=[_time_filter(), _annotation_filter(label_id, "approved")],
    )

    sql, params = builder.build_filter_match_query_from_seed_rows(
        _org_span_collision_seed_rows(project_b)
    )

    assert sql.count("tracer_project_id = toUUID(") >= 2
    assert sql.count("outer_project_id)s) AND") == 2
    assert params["org_span_residual_0_project_id"] == PROJECT_ID
    assert params["org_span_residual_1_project_id"] == project_b
    assert params["org_span_residual_0_candidate_span_ids"] == ("shared-span",)
    assert params["org_span_residual_1_candidate_span_ids"] == ("shared-span",)
    assert params["org_span_residual_0_candidate_span_entities"] == (
        ("shared-trace", "shared-span"),
    )
    assert params["org_span_residual_1_candidate_span_entities"] == (
        ("shared-trace", "shared-span"),
    )
    assert not re.search(r"%\(project_id\)s", sql)


def test_org_span_has_annotation_uses_each_projects_disjoint_labels() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    label_a = "00000000-0000-4000-8000-000000000091"
    label_b = "00000000-0000-4000-8000-000000000092"
    builder = SpanListQueryBuilder(
        project_ids=[PROJECT_ID, project_b],
        annotation_label_ids=[label_a, label_b],
        annotation_label_ids_by_project={
            PROJECT_ID: [label_a],
            project_b: [label_b],
        },
        filters=[_time_filter(), _has_annotation_filter(True)],
    )

    sql, params = builder.build_filter_match_query_from_seed_rows(
        _org_span_collision_seed_rows(project_b)
    )

    assert params["org_span_residual_0_lbl_1"] == label_a
    assert params["org_span_residual_1_lbl_1"] == label_b
    assert "org_span_residual_0_lbl_2" not in params
    assert "org_span_residual_1_lbl_2" not in params
    assert sql.count("HAVING uniqExact(s.label_id) >= 1") == 2
    assert params["org_span_residual_0_project_id"] == PROJECT_ID
    assert params["org_span_residual_1_project_id"] == project_b


def test_has_eval_span_residual_matches_candidate_span_not_its_whole_trace() -> None:
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        eval_config_ids=["00000000-0000-4000-8000-000000000093"],
        filters=[
            _time_filter(),
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
        ],
    )

    sql, params = builder.build_filter_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "id": "span-a",
                "start_time": END - timedelta(minutes=1),
            }
        ]
    )

    assert "tuple(trace_id, id) IN (" in sql
    assert (
        "SELECT DISTINCT tuple(toString(latest_eval.trace_id), "
        "toString(latest_eval.observation_span_id))" in sql
    )
    assert "sp.trace_id = toString(latest_eval.trace_id)" in sql
    assert "sp.id = toString(latest_eval.observation_span_id)" in sql
    assert (
        "(toString(eval_scan.trace_id), "
        "toString(eval_scan.observation_span_id)) "
        "IN %(candidate_span_entities)s" in sql
    )
    assert "LIMIT 1 BY eval_scan.id" in sql
    assert params["candidate_span_ids"] == ("span-a",)
    assert params["candidate_span_entities"] == (("trace-a", "span-a"),)


@pytest.mark.parametrize("filter_value", [False, "false"])
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_has_eval_false_span_residual_is_exact_pair_scoped_on_page_n(
    filter_value: bool | str,
) -> None:
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        eval_config_ids=["00000000-0000-4000-8000-000000000094"],
        filters=[_time_filter(), _has_eval_filter(filter_value)],
    )
    started = END - timedelta(minutes=1)

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=END,
        limit=50,
        before_start_time=started,
        before_id=("span-z", "trace-z", PROJECT_ID),
    )
    sql, params = builder.build_filter_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "id": "span-a",
                "start_time": started - timedelta(seconds=1),
            }
        ]
    )

    assert builder.supports_bounded_filter_scan() is True
    assert "tracer_eval_logger" not in seed_sql
    assert seed_params["filter_before_id"] == "span-z"
    assert seed_params["filter_before_trace_id"] == "trace-z"
    assert seed_params["filter_before_project_id"] == PROJECT_ID
    assert "tuple(trace_id, id) NOT IN (" in sql
    assert (
        "(toString(eval_scan.trace_id), "
        "toString(eval_scan.observation_span_id)) "
        "IN %(candidate_span_entities)s" in sql
    )
    assert "eval_scan.created_at >= %(start_date)s - INTERVAL 7 DAY" in sql
    assert "LIMIT 1 BY eval_scan.id" in sql
    assert "latest_eval.is_deleted = 0" in sql
    assert params["candidate_span_ids"] == ("span-a",)
    assert params["candidate_span_entities"] == (("trace-a", "span-a"),)


def test_legacy_system_aliases_keep_latest_state_without_broad_fallback() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _system_filter(
                "gen_ai.usage.total_tokens",
                500,
                filter_type="number",
                operation="greater_than",
            ),
            _system_filter("legacy.customer.level", "gold"),
        ],
    )

    seed_sql, _ = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
    )
    match_sql, params = builder.build_filter_match_query(["trace-a"])

    assert "total_tokens" not in seed_sql
    assert "has(span_attr_str.keys, %(latest_filter_key_1)s)" in seed_sql
    assert "argMax(tuple(total_tokens), _peerdb_version).1" in match_sql
    assert "argMax(mapContains(span_attr_str" in match_sql
    assert params["latest_filter_key_1"] == "legacy.customer.level"


def test_trace_any_span_root_seed_and_single_latest_state_scan() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("customer.final_status", "Rejected"),
            _attribute_filter("customer.country", "ES"),
        ],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_sampling_salt="task-salt",
        bounded_sampling_rate=25.0,
    )

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a"])

    first_seed_filter = "has(span_attr_str.keys, %(latest_filter_key_0)s)"
    second_seed_filter = "has(span_attr_str.keys, %(latest_filter_key_1)s)"
    assert first_seed_filter in seed_sql and second_seed_filter not in seed_sql
    assert "parent_span_id IS NULL" not in seed_sql
    assert seed_sql.index("cityHash64") < seed_sql.index("LIMIT %(filter_seed_limit)s")
    assert "toString(trace_id)" in seed_sql
    assert seed_params["latest_filter_key_0"] == "customer.final_status"
    assert "latest_filter_key_1" not in seed_params
    assert seed_params["bounded_sampling_salt"] == "task-salt"
    assert seed_params["bounded_sampling_rate"] == 25.0
    # Both leaves and canonical-root selection share one latest-state scan,
    # while independent countIf leaves allow separate children to satisfy them.
    assert match_sql.count("FROM spans") == 1
    assert match_sql.count("GROUP BY grouped_trace_id") == 1
    assert match_sql.count("countIf(") == 3
    # Canonical-root selection remains constrained to the exact half-open
    # request window. Any-span membership and the exact witness identities are
    # global within the finite trace-ID batch, matching list, graph, and task
    # semantics even when a child arrives days after its root.
    any_span_leaf_count = 2
    assert match_sql.count("argMinIf(") == any_span_leaf_count
    assert (
        match_sql.count(
            "latest_start_time >= fromUnixTimestamp64Micro(%(candidate_start_date_us)s)"
        )
        == 2
    )
    assert (
        match_sql.count(
            "latest_start_time < fromUnixTimestamp64Micro(%(candidate_end_date_us)s)"
        )
        == 2
    )
    assert (
        match_sql.count(
            "latest_start_time >= fromUnixTimestamp64Micro(%(candidate_witness_start_date_us)s)"
        )
        == 0
    )
    assert (
        match_sql.count(
            "latest_start_time < fromUnixTimestamp64Micro(%(candidate_witness_end_date_us)s)"
        )
        == 0
    )
    first_match_filter = "mapContains(span_attr_str, %(latest_filter_key_0)s)"
    second_match_filter = "mapContains(span_attr_str, %(latest_filter_key_1)s)"
    assert first_match_filter in match_sql and second_match_filter in match_sql
    assert "AND trace_id IN %(candidate_trace_ids)s" in match_sql
    assert "%(candidate_start_date)s - INTERVAL 1 DAY" not in match_sql
    assert "%(candidate_end_date)s + INTERVAL 1 DAY" not in match_sql
    assert "SELECT id\n" not in match_sql
    assert match_params["candidate_trace_ids"] == ("trace-a",)
    assert builder.filter_seed_proves_result_order() is False
    assert builder.recommended_filter_classify_batch_size() == 10


def test_trace_candidate_classifier_enforces_production_proven_512_trace_cap() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
    )

    with pytest.raises(ValueError, match="candidate trace batch"):
        builder.build_filter_match_query([f"trace-{index:03d}" for index in range(513)])


def test_span_candidate_classifier_enforces_200_identity_hard_cap() -> None:
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
    )

    with pytest.raises(ValueError, match="candidate .* batch exceeds bounded limit"):
        builder.build_filter_match_query([f"identity-{index}" for index in range(201)])


def test_unicode_text_equality_and_membership_use_utf8_case_folding() -> None:
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("customer.tier", "ÉLITE"),
            _system_filter("status", ["ÉXITO"], operation="in"),
        ],
    )

    sql, params = builder.build_filter_match_query(["span-a"])

    assert "lowerUTF8(toString(latest_attr_value_0))" in sql
    assert "lowerUTF8(toString(latest_column_value_1))" in sql
    assert params["latest_filter_param_0"] == "élite"
    assert params["latest_filter_param_1"] == ("éxito",)


def test_attribute_key_is_bound_and_preserved_for_all_map_expressions() -> None:
    key = "café final status '50%_\\path"
    value = "Rejected%_\\literal"
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter(key, value)],
    )

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
    )
    match_sql, match_params = builder.build_filter_match_query(["span-a"])

    for sql in (seed_sql, match_sql):
        assert key not in sql
        assert "%(latest_filter_key_0)s" in sql
    assert "has(attrs_string.keys, %(latest_filter_key_0)s)" in seed_sql
    assert "attrs_string[%(latest_filter_key_0)s]" in seed_sql
    assert "argMax(mapContains(attrs_string, %(latest_filter_key_0)s)" in match_sql
    assert "argMax(attrs_string[%(latest_filter_key_0)s], _version)" in match_sql
    assert seed_params["latest_filter_key_0"] == key
    assert seed_params["latest_filter_param_0"] == value.lower()
    assert match_params["latest_filter_key_0"] == key
    assert match_params["latest_filter_param_0"] == value.lower()


@pytest.mark.parametrize(
    "key",
    ["bad\x00key", "bad\nkey", "x" * 4097, "bad\ud800key"],
)
def test_attribute_key_control_invalid_utf8_and_length_fail_closed(key: str) -> None:
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter(key, "value")],
    )

    assert builder.supports_bounded_filter_scan() is False


def test_negative_text_operators_are_literal_utf8_predicates() -> None:
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter(
                "customer.note",
                "Café%_\\path",
                operation="not_contains",
            ),
            _system_filter("status", ["ÉCHEC"], operation="not_in"),
        ],
    )

    sql, params = builder.build_filter_match_query(["span-a"])

    assert "positionUTF8(" in sql
    assert ") = 0" in sql
    assert "lowerUTF8(toString(latest_column_value_1)) NOT IN" in sql
    assert " LIKE " not in sql
    assert params["latest_filter_param_0"] == "Café%_\\path"
    assert params["latest_filter_param_1"] == ("échec",)


@pytest.mark.parametrize(
    "bad_filter",
    [
        _attribute_filter("customer.tier", [], operation="in"),
        _attribute_filter("customer.tier", "", operation="contains"),
        _attribute_filter("reviewed", "true", filter_type="boolean"),
        _attribute_filter("quality", "not-a-number", filter_type="number"),
    ],
)
def test_empty_or_malformed_filter_values_emit_no_bounded_query(
    bad_filter: dict[str, Any],
) -> None:
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), bad_filter],
    )

    assert builder.supports_bounded_filter_scan() is False
    with pytest.raises(ValueError, match="unsupported bounded span filter scan"):
        builder.build_filter_match_query(["span-a"])


def test_call_type_json_sources_match_provider_normalization() -> None:
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _system_filter("call_type", "inbound")],
    )

    sql, _ = builder.build_filter_match_query(["span-a"])

    assert "JSONExtractString(span_attributes_raw, 'raw_log', 'type')" in sql
    assert (
        "JSONExtractString(JSONExtractString(span_attributes_raw, 'raw_log'), "
        "'type')" in sql
    )
    assert "JSONExtractString(span_attr_str['raw_log'], 'type')" in sql
    assert "= 'inboundPhoneCall', 'inbound'" in sql
    assert "= 'inboundPhoneCall', 'inbound', 'outbound')" in sql
    assert "outboundPhoneCall" not in sql
    assert "'raw_log', 'direction'" in sql


def test_v2_bounded_builders_emit_only_ch25_columns() -> None:
    trace_builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
        ],
    )
    trace_seed_sql, _ = trace_builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=100,
    )
    trace_match_sql, _ = trace_builder.build_filter_match_query(["trace-a"])

    span_builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("customer.tier", "Élite"),
            _attribute_filter("quality", 0.8, filter_type="number"),
            _attribute_filter("reviewed", True, filter_type="boolean"),
            _system_filter("call_type", "inbound"),
        ],
    )
    span_match_sql, _ = span_builder.build_filter_match_query(["span-a"])

    assert (
        "start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s)"
        in trace_seed_sql
    )
    assert "attrs_string" in trace_match_sql
    assert "attrs_string" in span_match_sql
    assert "attrs_number" in span_match_sql
    assert "attrs_bool" in span_match_sql
    assert "JSONExtractString(attributes_extra, 'raw_log', 'type')" in span_match_sql
    assert "_version" in trace_match_sql
    assert "_version" in span_match_sql
    assert "is_deleted" in trace_seed_sql
    for sql in (trace_seed_sql, trace_match_sql, span_match_sql):
        assert "_peerdb_" not in sql
        assert "span_attr_str" not in sql
        assert "span_attr_num" not in sql
        assert "span_attr_bool" not in sql
        assert "span_attributes_raw" not in sql


def test_trace_custom_sort_never_falls_back_to_legacy_query() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
        sort_params=[{"column_id": "latency", "order": "desc"}],
    )

    assert builder.supports_bounded_filter_scan() is False
    assert (
        builder.bounded_filter_degraded_error_code() == "unsupported_filter_modifiers"
    )
    with pytest.raises(ValueError, match="unsafe legacy filtered trace read blocked"):
        builder.build()


def test_trace_search_is_a_literal_latest_root_predicate_in_bounded_reads() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter()],
        search="100%_D",
    )

    assert builder.supports_bounded_filter_scan() is True
    assert builder.bounded_filter_degraded_error_code() is None
    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=50,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a"])

    literal_seed = (
        "positionUTF8(lowerUTF8(toString(trace_name)), "
        "lowerUTF8(toString(%(latest_filter_param_0)s))) > 0"
    )
    literal_latest = (
        "positionUTF8(lowerUTF8(toString(latest_column_value_0)), "
        "lowerUTF8(toString(%(latest_filter_param_0)s))) > 0"
    )
    assert literal_seed in seed_sql
    assert literal_latest in match_sql
    assert "argMax(trace_name, _peerdb_version) AS latest_column_value_0" in match_sql
    assert seed_params["latest_filter_param_0"] == "100%_D"
    assert match_params["latest_filter_param_0"] == "100%_D"
    assert "ILIKE" not in seed_sql + match_sql
    assert "100%_D" not in seed_sql + match_sql

    with pytest.raises(ValueError, match="bounded_search_required"):
        builder.build()
    with pytest.raises(ValueError, match="bounded_search_required"):
        builder.build_count_query()


def test_trace_search_and_any_span_filter_share_bounded_classifier() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
        ],
        search="SyntheticAgent",
    )

    anchor_sql, anchor_params = builder.build_filter_anchor_probe(limit=513)
    ordered_sql, ordered_params = builder.build_filter_ordered_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=50,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a"])

    # The sparse/common anchor stays on the indexed child attribute. Search is
    # a root predicate, so it belongs in the ordered-root seed and classifier.
    assert "has(span_attr_str.keys, %(latest_filter_key_0)s)" in anchor_sql
    assert "latest_filter_param_1" not in anchor_params
    assert "positionUTF8(lowerUTF8(toString(trace_name))" in ordered_sql
    assert ordered_params["latest_filter_param_1"] == "SyntheticAgent"
    assert "latest_attr_value_0" in match_sql
    assert "latest_column_value_1" in match_sql
    assert match_params["latest_filter_param_0"] == "rejected"
    assert match_params["latest_filter_param_1"] == "SyntheticAgent"
    assert builder.filter_seed_proves_result_order() is False


def test_trace_search_with_custom_sort_remains_fail_closed() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter()],
        search="needle",
        sort_params=[{"column_id": "latency", "order": "desc"}],
    )

    assert builder.supports_bounded_filter_scan() is False
    assert (
        builder.bounded_filter_degraded_error_code() == "unsupported_filter_modifiers"
    )


@pytest.mark.parametrize("filters", [[], [_time_filter()]])
def test_trace_empty_or_time_only_custom_sort_never_uses_bounded_order(
    filters: list[dict[str, Any]],
) -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        sort_params=[{"column_id": "latency", "order": "desc"}],
    )

    assert builder.supports_bounded_filter_scan() is False
    assert (
        builder.bounded_filter_degraded_error_code() == "unsupported_filter_modifiers"
    )
    with pytest.raises(ValueError, match="unsupported bounded trace filter scan"):
        builder.build_filter_seed_page(
            slice_start=END - timedelta(minutes=5),
            slice_end=END,
            limit=50,
        )


def test_trace_project_version_filter_is_scoped_in_bounded_seed_and_replay() -> None:
    project_version_id = "00000000-0000-4000-8000-000000000002"
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        project_version_id=project_version_id,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
    )

    assert builder.supports_bounded_filter_scan() is True
    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=50,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a"])

    assert "project_version_id = %(project_version_id)s" in seed_sql
    assert "project_version_id = %(project_version_id)s" in match_sql
    assert seed_params["project_version_id"] == project_version_id
    assert match_params["project_version_id"] == project_version_id


@pytest.mark.parametrize(
    "modifier",
    [
        {"sort_params": [{"column_id": "latency", "order": "desc"}]},
        {"end_user_id": "00000000-0000-4000-8000-000000000002"},
    ],
)
def test_span_supported_filter_modifiers_never_fall_back_to_legacy_query(
    modifier: dict[str, Any],
) -> None:
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
        **modifier,
    )

    assert builder.supports_bounded_filter_scan() is False
    assert (
        builder.bounded_filter_degraded_error_code() == "unsupported_filter_modifiers"
    )
    with pytest.raises(ValueError, match="unsafe legacy filtered span read blocked"):
        builder.build()


@pytest.mark.parametrize("filters", [[], [_time_filter()]])
def test_span_empty_or_time_only_custom_sort_uses_project_time_bounded_top_n(
    filters: list[dict[str, Any]],
) -> None:
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        sort_params=[{"column_id": "latency", "order": "desc"}],
    )

    assert builder.supports_bounded_filter_scan() is False
    assert builder.bounded_filter_degraded_error_code() is None
    sql, _ = builder.build()
    assert "ORDER BY latency_ms DESC" in sql
    with pytest.raises(ValueError, match="unsupported bounded span filter scan"):
        builder.build_filter_seed_page(
            slice_start=END - timedelta(minutes=5),
            slice_end=END,
            limit=50,
        )


def test_span_unfiltered_end_user_uses_remap_aware_legacy_path() -> None:
    end_user_id = "00000000-0000-4000-8000-000000000002"
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter()],
        end_user_id=end_user_id,
    )

    assert builder.supports_bounded_filter_scan() is False
    assert builder.bounded_filter_degraded_error_code() is None
    list_sql, list_params = builder.build()
    count_sql, count_params = builder.build_count_query()
    for sql in (list_sql, count_sql):
        assert "end_user_id_remap" in sql
        assert "resolved_end_user_id = %(end_user_id)s" in sql
    assert list_params["end_user_id"] == end_user_id
    assert count_params["end_user_id"] == end_user_id


@override_settings(CLICKHOUSE_V2={"QUERY_TYPES_DISABLED": "TRACE_LIST"})
def test_observe_trace_list_uses_v2_builder_when_routing_is_disabled() -> None:
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    strict_request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
        query_params={"allow_sampled": "false"},
    )
    bounded = BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=0,
        elapsed_ms=2.0,
        query_count=1,
        rows_returned=0,
        result_payload_bytes=0,
        attempts=(),
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded,
        ) as bounded_read,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        omitted_status, omitted_payload = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )
        explicit_false_response = view._list_traces_of_session_clickhouse(
            strict_request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
                "allow_sampled": False,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )
        status, payload = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
                "allow_sampled": True,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert omitted_status == "ok"
    assert omitted_payload["metadata"]["query_complete"] is True
    assert omitted_payload["metadata"]["total_rows_is_lower_bound"] is False
    assert explicit_false_response[0] == "ok"
    assert explicit_false_response[1]["metadata"]["total_rows_is_lower_bound"] is False
    assert status == "ok"
    assert isinstance(bounded_read.call_args.kwargs["builder"], TraceListQueryBuilderV2)
    assert payload["metadata"]["query_complete"] is True
    assert 0 <= payload["metadata"]["query_elapsed_ms"] < 3_000
    assert payload["metadata"]["query_count"] == 1
    assert payload["metadata"]["query_rows_returned"] == 0
    assert payload["metadata"]["query_result_payload_bytes"] == 0
    assert payload["metadata"]["total_rows_is_lower_bound"] is False
    analytics.execute_ch_query.assert_not_called()


@override_settings(
    CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "TRACE_LIST"},
)
@pytest.mark.parametrize("row_count", [1, 201])
def test_trace_list_nonempty_page_enrichments_share_wall_budget(
    row_count: int,
) -> None:
    from tracer.views.trace import (
        TRACE_LIST_CANDIDATE_DEADLINE_MS,
        TRACE_LIST_ENRICHMENT_CHUNK_SIZE,
        TRACE_LIST_ENRICHMENT_MAX_WORKERS,
        TRACE_LIST_ENRICHMENT_TIMEOUT_MS,
        TRACE_LIST_READ_SETTINGS,
        TRACE_LIST_WALL_DEADLINE_MS,
        TraceView,
    )

    started = END - timedelta(minutes=1)
    rows = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "trace_name": f"trace-{index}",
            "span_name": f"root-{index}",
            "observation_type": "llm",
            "status": "OK",
            "start_time": started - timedelta(microseconds=index),
            "latency_ms": 12.0,
            "cost": 0.001,
        }
        for index in range(row_count)
    ]
    bounded = BoundedFilterPage(
        rows=rows,
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=row_count,
        elapsed_ms=2.0,
        query_count=1,
        rows_returned=row_count,
        result_payload_bytes=10,
        attempts=(),
    )

    class RecordingAnalytics:
        def __init__(self):
            self.calls = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((params, timeout_ms, settings))
            if "content_trace_ids" in params:
                data = [
                    {
                        "trace_id": trace_id,
                        "input": "in",
                        "output": "out",
                        "attrs_string": {},
                        "attrs_number": {},
                        "attrs_bool": {},
                        "attributes_extra": "{}",
                        "metadata": "{}",
                        "trace_tags": [],
                    }
                    for trace_id in params["content_trace_ids"]
                ]
            else:
                data = []
            return QueryResult(data, len(data), "clickhouse", 0.0)

    analytics = RecordingAnalytics()
    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )

    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded,
        ) as bounded_read,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        status_name, payload = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": row_count,
                "allow_sampled": True,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert status_name == "ok"
    assert [row["trace_id"] for row in payload["table"]] == [
        f"trace-{index}" for index in range(row_count)
    ]
    expected_chunks = (
        row_count + TRACE_LIST_ENRICHMENT_CHUNK_SIZE - 1
    ) // TRACE_LIST_ENRICHMENT_CHUNK_SIZE
    assert payload["metadata"]["query_count"] == 2 * expected_chunks + 2
    assert 0 <= payload["metadata"]["query_elapsed_ms"] < 3_000
    assert (
        bounded_read.call_args.kwargs["deadline_ms"] <= TRACE_LIST_CANDIDATE_DEADLINE_MS
    )
    assert (
        TRACE_LIST_CANDIDATE_DEADLINE_MS
        == TRACE_LIST_WALL_DEADLINE_MS
        == settings.INTERACTIVE_READ_DEFAULT_WALL_MS
    )
    assert TRACE_LIST_ENRICHMENT_MAX_WORKERS == 2
    assert len(analytics.calls) == 2 * expected_chunks + 1
    content_chunks = [
        params["content_trace_ids"]
        for params, _timeout_ms, _settings in analytics.calls
        if "content_trace_ids" in params
    ]
    attribute_chunks = [
        (
            params["attr_trace_ids"]
            if "attr_trace_ids" in params
            else tuple(
                trace_id for _project_id, trace_id in params["attr_trace_identities"]
            )
        )
        for params, _timeout_ms, _settings in analytics.calls
        if "attr_trace_ids" in params or "attr_trace_identities" in params
    ]
    assert len(content_chunks) == expected_chunks
    assert len(attribute_chunks) == expected_chunks
    assert all(
        0 < len(chunk) <= TRACE_LIST_ENRICHMENT_CHUNK_SIZE
        for chunk in (*content_chunks, *attribute_chunks)
    )
    expected_enrichment_chunks = [
        tuple(
            f"trace-{index}"
            for index in range(
                chunk_start,
                min(chunk_start + TRACE_LIST_ENRICHMENT_CHUNK_SIZE, row_count),
            )
        )
        for chunk_start in range(0, row_count, TRACE_LIST_ENRICHMENT_CHUNK_SIZE)
    ]
    # Content and attribute queries share a two-worker pool. Their invocation
    # order is intentionally nondeterministic, but every ordered page chunk
    # must be submitted exactly once.
    assert sorted(tuple(chunk) for chunk in content_chunks) == sorted(
        expected_enrichment_chunks
    )
    assert sorted(tuple(chunk) for chunk in attribute_chunks) == sorted(
        expected_enrichment_chunks
    )
    assert all(
        0 < timeout_ms <= TRACE_LIST_ENRICHMENT_TIMEOUT_MS
        for _, timeout_ms, _ in analytics.calls
    )
    assert all(
        settings == TRACE_LIST_READ_SETTINGS for _, _, settings in analytics.calls
    )


@override_settings(
    CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "TRACE_LIST"},
)
def test_page_500_slow_candidate_admits_every_exact_enrichment_wave():
    """The reviewed ceiling admits the modeled 9.3 s max-page exact replay.

    The virtual two-worker scheduler is deterministic: the candidate consumes
    3 s, each CH statement consumes its full 900 ms cap, and the optional user
    resolver consumes two statements. Five content chunks, five packed
    attribute chunks, packed evals, annotations, and user replay therefore use
    fourteen worker slots / two workers = seven waves. No required future may
    be silently omitted.
    """

    import concurrent.futures

    from tracer.views.trace import (
        TRACE_LIST_ENRICHMENT_MAX_WORKERS,
        TRACE_LIST_ENRICHMENT_TIMEOUT_MS,
        TRACE_LIST_WALL_DEADLINE_MS,
        TraceView,
    )

    candidate_ms = 3_000
    query_ms = 900
    started = END - timedelta(minutes=1)
    rows = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": started - timedelta(microseconds=index),
        }
        for index in range(500)
    ]
    bounded = BoundedFilterPage(
        rows=rows,
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=500,
        elapsed_ms=float(candidate_ms),
        query_count=1,
        rows_returned=500,
        result_payload_bytes=5_000,
        attempts=(),
    )

    class VirtualDeadline:
        def __init__(self):
            self.total_ms = 0
            self.now_ms = 0

        def elapsed_ms(self):
            return float(self.now_ms)

        def remaining_ms(self, cap_ms=None, *, floor_ms=25):
            remaining = self.total_ms - self.now_ms
            if remaining < floor_ms:
                raise ReadDeadlineExceeded("virtual deadline exceeded")
            return remaining if cap_ms is None else min(cap_ms, remaining)

    deadline = VirtualDeadline()

    class DeadlineFactory:
        @staticmethod
        def start(total_ms):
            deadline.total_ms = total_ms
            return deadline

    class VirtualPool:
        instances = []

        def __init__(self, *, max_workers):
            assert max_workers == TRACE_LIST_ENRICHMENT_MAX_WORKERS == 2
            self.worker_ready_ms = [candidate_ms, candidate_ms]
            self.submit_count = 0
            self.__class__.instances.append(self)

        def submit(self, fn, *args, **kwargs):
            worker = min(
                range(len(self.worker_ready_ms)),
                key=self.worker_ready_ms.__getitem__,
            )
            slots = (
                2
                if getattr(fn, "__name__", "")
                == "resolve_user_ids_for_trace_identities"
                else 1
            )
            self.worker_ready_ms[worker] += slots * query_ms
            deadline.now_ms = max(deadline.now_ms, self.worker_ready_ms[worker])
            self.submit_count += 1
            future = concurrent.futures.Future()
            try:
                future.set_result(fn(*args, **kwargs))
            except Exception as exc:
                future.set_exception(exc)
            return future

        def shutdown(self, **_kwargs):
            return None

    class ExactAnalytics:
        def __init__(self):
            self.timeouts = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.timeouts.append(timeout_ms)
            if "content_trace_ids" in params:
                data = [
                    {
                        "project_id": PROJECT_ID,
                        "trace_id": trace_id,
                        "input": f"input-{trace_id}",
                        "output": f"output-{trace_id}",
                        "metadata": "{}",
                    }
                    for trace_id in params["content_trace_ids"]
                ]
            elif "user_trace_identities" in params:
                data = [
                    {
                        "project_id": PROJECT_ID,
                        "trace_id": "trace-0",
                        "resolved_end_user_id": "user-physical",
                        "physical_end_user_ids": ["user-physical"],
                    }
                ]
            else:
                data = []
            return QueryResult(data, len(data), "clickhouse", 0.0)

        def get_span_trace_map(self, *args, **kwargs):
            return {}

    configs = [
        SimpleNamespace(
            id=f"config-{index}",
            project_id=PROJECT_ID,
            name=f"config-{index}",
            eval_template=SimpleNamespace(
                id=f"template-{index}",
                config={"output": "SCORE"},
                choices=None,
            ),
        )
        for index in range(11)
    ]
    label = SimpleNamespace(
        id="00000000-0000-4000-8000-000000000101",
        type="text",
        name="label-a",
        settings={},
    )
    analytics = ExactAnalytics()
    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )

    def delayed_candidate(**_kwargs):
        deadline.now_ms = candidate_ms
        return bounded

    with (
        mock.patch("tracer.views.trace.ReadDeadline", DeadlineFactory),
        mock.patch(
            "tracer.views.trace.concurrent.futures.ThreadPoolExecutor", VirtualPool
        ),
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project",
            return_value=[label],
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.views.trace._annotation_score_span_ids",
            return_value=["scored-span"],
        ),
        mock.patch(
            "tracer.views.trace.update_column_config_based_on_eval_config",
            side_effect=lambda config, _evals: config,
        ),
        mock.patch(
            "tracer.views.trace.update_span_column_config_based_on_annotations",
            side_effect=lambda config, _labels: config,
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            side_effect=delayed_candidate,
        ),
    ):
        eval_config.objects.filter.return_value.select_related.return_value = configs
        status_name, payload = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 500,
                "allow_sampled": True,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert status_name == "ok"
    assert len(payload["table"]) == 500
    assert TRACE_LIST_WALL_DEADLINE_MS == settings.INTERACTIVE_READ_DEFAULT_WALL_MS
    assert deadline.now_ms == 9_300
    assert VirtualPool.instances[0].submit_count == 13
    assert all(
        0 < timeout <= TRACE_LIST_ENRICHMENT_TIMEOUT_MS
        for timeout in analytics.timeouts
    )


@override_settings(
    CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "TRACE_LIST"},
)
def test_trace_cursor_order_is_backward_compatible_only_in_single_project() -> None:
    from tracer.services.clickhouse.list_cursor import ListCursorError
    from tracer.views.trace import (
        _decode_trace_list_cursor_order,
        _trace_list_cursor_order_for_row,
    )

    started = END - timedelta(minutes=1)
    row = {
        "project_id": PROJECT_ID,
        "trace_id": "shared-trace",
        "start_time": started,
    }

    assert _trace_list_cursor_order_for_row(row, org_scope=False) == (
        started,
        "shared-trace",
    )
    assert (
        _decode_trace_list_cursor_order((started, "shared-trace"), org_scope=False)
        == "shared-trace"
    )
    assert _trace_list_cursor_order_for_row(row, org_scope=True) == (
        started,
        "shared-trace",
        PROJECT_ID,
    )
    assert _decode_trace_list_cursor_order(
        (started, "shared-trace", PROJECT_ID), org_scope=True
    ) == ("shared-trace", PROJECT_ID)

    with pytest.raises(ListCursorError) as legacy_org:
        _decode_trace_list_cursor_order((started, "shared-trace"), org_scope=True)
    assert legacy_org.value.code == "invalid_cursor"
    assert str(legacy_org.value) == "The continuation cursor is invalid."

    with pytest.raises(ListCursorError) as org_replayed_as_single:
        _decode_trace_list_cursor_order(
            (started, "shared-trace", PROJECT_ID), org_scope=False
        )
    assert org_replayed_as_single.value.code == "invalid_cursor"


@override_settings(
    CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "TRACE_LIST"},
)
def test_org_trace_content_same_trace_id_is_merged_by_project_identity() -> None:
    from tracer.views.trace import TraceView

    project_b = "00000000-0000-4000-8000-000000000002"
    config_a = SimpleNamespace(
        id="config-a",
        project_id=PROJECT_ID,
        name="quality-a",
        eval_template=SimpleNamespace(
            id="template-a", config={"output": "score"}, choices=None
        ),
    )
    config_b = SimpleNamespace(
        id="config-b",
        project_id=project_b,
        name="quality-b",
        eval_template=SimpleNamespace(
            id="template-b", config={"output": "score"}, choices=None
        ),
    )
    started = END - timedelta(minutes=1)
    bounded = BoundedFilterPage(
        rows=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "shared-trace",
                "root_span_id": "root-a",
                "start_time": started,
            },
            {
                "project_id": project_b,
                "trace_id": "shared-trace",
                "root_span_id": "root-b",
                "start_time": started,
            },
        ],
        has_more=True,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=2,
        elapsed_ms=2.0,
        query_count=1,
        rows_returned=2,
        result_payload_bytes=20,
        attempts=(),
    )

    class OrgAnalytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if "content_trace_ids" in params:
                rows = [
                    {
                        "project_id": PROJECT_ID,
                        "trace_id": "shared-trace",
                        "input": "tenant-a-input",
                    },
                    {
                        "project_id": project_b,
                        "trace_id": "shared-trace",
                        "input": "tenant-b-input",
                    },
                ]
            elif "eval_config_ids" in params:
                rows = [
                    {
                        "trace_id": "shared-trace",
                        "eval_config_id": "config-a",
                        "avg_score": 0.1,
                        "pass_rate": None,
                        "success_count": 1,
                        "error_count": 0,
                        "eval_count": 1,
                        "str_lists": [],
                    },
                    {
                        "trace_id": "shared-trace",
                        "eval_config_id": "config-b",
                        "avg_score": 0.9,
                        "pass_rate": None,
                        "success_count": 1,
                        "error_count": 0,
                        "eval_count": 1,
                        "str_lists": [],
                    },
                ]
            elif "attr_trace_ids" in params or "attr_trace_identities" in params:
                rows = [
                    {
                        "project_id": PROJECT_ID,
                        "trace_id": "shared-trace",
                        "attributes_extra": '{"tenant_marker":"tenant-a"}',
                    },
                    {
                        "project_id": project_b,
                        "trace_id": "shared-trace",
                        "attributes_extra": '{"tenant_marker":"tenant-b"}',
                    },
                ]
            else:
                rows = []
            return QueryResult(rows, len(rows), "clickhouse", 0.0)

    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    validated_data = {
        "filters": [_time_filter()],
        "attribute_keys": ["tenant_marker"],
        "page_number": 0,
        "page_size": 25,
        "cursor_mode": True,
        "allow_sampled": True,
    }

    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_by_project",
            return_value={PROJECT_ID: [], project_b: []},
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded,
        ),
    ):
        eval_config.objects.filter.return_value.select_related.return_value = [
            config_a,
            config_b,
        ]
        status_name, payload = view._list_traces_of_session_clickhouse(
            request,
            project_id=None,
            validated_data=validated_data,
            analytics=OrgAnalytics(),
            org_project_ids=[PROJECT_ID, project_b],
            org=organization,
        )

    assert status_name == "ok"
    rows_by_project = {row["project_id"]: row for row in payload["table"]}
    assert rows_by_project[PROJECT_ID]["input"] == "tenant-a-input"
    assert rows_by_project[PROJECT_ID]["tenant_marker"] == "tenant-a"
    assert rows_by_project[PROJECT_ID]["config-a"] == 10.0
    assert "config-b" not in rows_by_project[PROJECT_ID]
    assert rows_by_project[project_b]["input"] == "tenant-b-input"
    assert rows_by_project[project_b]["tenant_marker"] == "tenant-b"
    assert rows_by_project[project_b]["config-b"] == 90.0
    assert "config-a" not in rows_by_project[project_b]
    assert payload["metadata"]["total_rows_is_lower_bound"] is True
    assert payload["metadata"]["has_more"] is True
    from tracer.services.clickhouse.list_cursor import (
        cursor_scope_for_request,
        decode_list_cursor,
    )

    cursor = decode_list_cursor(
        payload["metadata"]["next_cursor"],
        resource="observe_traces",
        scope=cursor_scope_for_request(request, project_ids=[PROJECT_ID, project_b]),
        query=validated_data,
        page_size=25,
    )
    assert cursor.order == (
        started.replace(tzinfo=UTC),
        "shared-trace",
        project_b,
    )


@override_settings(
    CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "TRACE_LIST"},
)
def test_org_trace_content_equal_count_with_missing_and_extra_identity_fails_closed():
    from tracer.views.trace import TraceView

    project_b = "00000000-0000-4000-8000-000000000002"
    project_extra = "00000000-0000-4000-8000-000000000003"
    started = END - timedelta(minutes=1)
    bounded = BoundedFilterPage(
        rows=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "shared-trace",
                "root_span_id": "root-a",
                "start_time": started,
            },
            {
                "project_id": project_b,
                "trace_id": "shared-trace",
                "root_span_id": "root-b",
                "start_time": started,
            },
        ],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=2,
        elapsed_ms=2.0,
        query_count=1,
        rows_returned=2,
        result_payload_bytes=20,
        attempts=(),
    )

    class IdentityDriftAnalytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if "content_trace_ids" in params:
                rows = [
                    {
                        "project_id": PROJECT_ID,
                        "trace_id": "shared-trace",
                        "input": "tenant-a-input",
                    },
                    {
                        "project_id": project_extra,
                        "trace_id": "shared-trace",
                        "input": "unexpected-tenant-input",
                    },
                ]
            else:
                rows = []
            return QueryResult(rows, len(rows), "clickhouse", 0.0)

    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )

    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded,
        ),
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_traces_of_session_clickhouse(
            request,
            project_id=None,
            validated_data={
                "filters": [_time_filter()],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": True,
                "allow_sampled": True,
            },
            analytics=IdentityDriftAnalytics(),
            org_project_ids=[PROJECT_ID, project_b, project_extra],
            org=organization,
        )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert response[2]["code"] == "service_unavailable"
    assert "unexpected-tenant-input" not in str(response)


@override_settings(
    CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "TRACE_LIST"},
)
def test_trace_list_enrichment_timeout_is_sanitized_503_not_empty_200() -> None:
    from tracer.views.trace import TraceView

    started = END - timedelta(minutes=1)
    bounded = BoundedFilterPage(
        rows=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "root_span_id": "root-a",
                "start_time": started,
            }
        ],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=1,
        elapsed_ms=2.0,
        query_count=1,
        rows_returned=1,
        result_payload_bytes=10,
        attempts=(),
    )

    class TimeoutAnalytics:
        def execute_ch_query(self, *args, **kwargs):
            raise ReadDeadlineExceeded("private ClickHouse host and stack")

    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )

    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded,
        ),
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
            },
            analytics=TimeoutAnalytics(),
            org_project_ids=None,
            org=organization,
        )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert response[2]["code"] == "service_unavailable"
    assert "private ClickHouse" not in str(response)


@override_settings(CLICKHOUSE_V2={})
def test_non_observe_trace_list_uses_v2_builder_without_routing_config() -> None:
    """Prototype/eval trace filtering must not call the blocked broad query."""

    from tracer.views.trace import TRACE_LIST_CANDIDATE_DEADLINE_MS, TraceView

    project_version_id = "00000000-0000-4000-8000-000000000099"
    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    strict_request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
        query_params={"allow_sampled": "false"},
    )
    view.request = request
    bounded = BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=0,
        elapsed_ms=2.0,
        query_count=2,
        rows_returned=0,
        result_payload_bytes=0,
        attempts=(),
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.trace.ProjectVersion") as project_version,
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded,
        ) as bounded_read,
    ):
        project_version.objects.get.return_value = SimpleNamespace(
            project_id=PROJECT_ID
        )
        eval_config.objects.filter.return_value.select_related.return_value = []
        omitted_status, omitted_payload = view._list_traces_clickhouse(
            request,
            project_version_id,
            analytics,
            {
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "sort_params": [],
                "page_number": 3,
                "page_size": 25,
            },
        )
        explicit_false_response = view._list_traces_clickhouse(
            strict_request,
            project_version_id,
            analytics,
            {
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "sort_params": [],
                "page_number": 3,
                "page_size": 25,
                "allow_sampled": False,
            },
        )
        status, payload = view._list_traces_clickhouse(
            request,
            project_version_id,
            analytics,
            {
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "sort_params": [],
                "page_number": 3,
                "page_size": 25,
                "allow_sampled": True,
            },
        )

    assert omitted_status == "ok"
    assert omitted_payload["metadata"]["query_complete"] is True
    assert omitted_payload["metadata"]["total_rows_is_lower_bound"] is False
    assert explicit_false_response[0] == "ok"
    assert explicit_false_response[1]["metadata"]["total_rows_is_lower_bound"] is False
    assert status == "ok"
    bounded_kwargs = bounded_read.call_args.kwargs
    assert isinstance(bounded_kwargs["builder"], TraceListQueryBuilderV2)
    assert bounded_kwargs["builder"].project_version_id == project_version_id
    assert bounded_kwargs["page_number"] == 3
    assert bounded_kwargs["page_size"] == 25
    assert payload["metadata"]["query_complete"] is True
    assert payload["metadata"]["query_count"] == 2
    assert payload["metadata"]["total_rows_is_lower_bound"] is False
    assert 0 < bounded_kwargs["deadline_ms"] <= TRACE_LIST_CANDIDATE_DEADLINE_MS
    analytics.get_eval_config_ids_with_data_ch.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


@override_settings(
    CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "TRACE_LIST"},
)
def test_eval_task_project_version_enrichments_share_deadline_and_caps() -> None:
    from tracer.views.trace import (
        TRACE_LIST_ENRICHMENT_TIMEOUT_MS,
        TRACE_LIST_READ_SETTINGS,
        TraceView,
    )

    project_version_id = "00000000-0000-4000-8000-000000000099"
    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    view.request = request
    bounded = BoundedFilterPage(
        rows=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "root_span_id": "root-a",
                "start_time": END - timedelta(minutes=1),
            }
        ],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=1,
        elapsed_ms=2.0,
        query_count=2,
        rows_returned=1,
        result_payload_bytes=64,
        attempts=(),
    )

    class CapturingAnalytics:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def execute_ch_query(self, query, params, timeout_ms, settings=None):
            self.calls.append(
                {
                    "query": query,
                    "timeout_ms": timeout_ms,
                    "settings": settings,
                }
            )
            if "user_trace_identities" in params:
                end_user_id = "00000000-0000-0000-0000-000000000010"
                rows = [
                    {
                        "project_id": PROJECT_ID,
                        "trace_id": "trace-a",
                        "resolved_end_user_id": end_user_id,
                        "physical_end_user_ids": [end_user_id],
                    }
                ]
            elif "user_physical_identities" in params:
                rows = [
                    {
                        "project_id": PROJECT_ID,
                        "end_user_id": "00000000-0000-0000-0000-000000000010",
                        "user_id": "user-a",
                        "version": END,
                    }
                ]
            else:
                rows = [
                    {
                        "trace_id": "trace-a",
                        "input": "input-a",
                        "output": "output-a",
                        "trace_tags": [],
                        "attrs_string": {},
                        "attrs_number": {},
                        "attrs_bool": {},
                        "attributes_extra": {},
                    }
                ]
            return QueryResult(
                data=rows,
                row_count=len(rows),
                backend_used="clickhouse",
                query_time_ms=1.0,
            )

    analytics = CapturingAnalytics()
    with (
        mock.patch("tracer.views.trace.ProjectVersion") as project_version,
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded,
        ),
    ):
        project_version.objects.get.return_value = SimpleNamespace(
            project_id=PROJECT_ID
        )
        eval_config.objects.filter.return_value.select_related.return_value = []
        status_name, payload = view._list_traces_clickhouse(
            request,
            project_version_id,
            analytics,
            {
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "sort_params": [],
                "page_number": 0,
                "page_size": 25,
                "allow_sampled": True,
            },
        )

    assert status_name == "ok"
    assert len(analytics.calls) == 3
    assert all(
        0 < call["timeout_ms"] <= TRACE_LIST_ENRICHMENT_TIMEOUT_MS
        for call in analytics.calls
    )
    assert all(call["settings"] == TRACE_LIST_READ_SETTINGS for call in analytics.calls)
    assert payload["table"][0]["user_id"] == "user-a"


def test_eval_task_trace_list_incomplete_page_fails_closed_before_enrichment() -> None:
    """Partial selector rows must never escape as a successful task choice."""

    from tracer.views.trace import TraceView

    project_version_id = "00000000-0000-4000-8000-000000000099"
    view = TraceView.__new__(TraceView)
    view._gm = mock.MagicMock()
    view._gm.custom_error_response.return_value = ("error", 503)
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    view.request = request
    incomplete = BoundedFilterPage(
        rows=[
            {
                "trace_id": "must-not-escape",
                "start_time": END - timedelta(minutes=1),
            }
        ],
        has_more=False,
        complete=False,
        status="degraded",
        error_code="deadline_exceeded",
        total_rows_lower_bound=1,
        elapsed_ms=4500.0,
        query_count=4,
        rows_returned=1,
        result_payload_bytes=10,
        attempts=(),
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.trace.ProjectVersion") as project_version,
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=incomplete,
        ),
    ):
        project_version.objects.get.return_value = SimpleNamespace(
            project_id=PROJECT_ID
        )
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_traces_clickhouse(
            request,
            project_version_id,
            analytics,
            {
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "sort_params": [],
                "page_number": 0,
                "page_size": 25,
            },
        )

    assert response == ("error", 503)
    view._gm.custom_error_response.assert_called_once_with(
        503,
        "Filtered trace data is temporarily unavailable. Please retry.",
        code="service_unavailable",
    )
    view._gm.success_response.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


@override_settings(
    CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "", "QUERY_TYPES_V2_PRIMARY": ""},
)
def test_task_create_prompt_slug_equals_uses_bounded_span_route_contract() -> None:
    from tracer.views.observation_span import ObservationSpanView

    view = ObservationSpanView.__new__(ObservationSpanView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    strict_request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
        query_params={"allow_sampled": "false"},
    )
    bounded = BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=0,
        elapsed_ms=2.0,
        query_count=1,
        rows_returned=0,
        result_payload_bytes=0,
        attempts=(),
    )
    v2_analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded,
        ) as bounded_read,
        mock.patch(
            "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
            side_effect=AssertionError("SPAN_LIST dispatch must not be consulted"),
        ) as dispatch,
        mock.patch(
            "tracer.services.clickhouse.v2.query_service.query_service_for_builder",
            side_effect=AssertionError("SPAN_LIST service remap must not be consulted"),
        ) as service_remap,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        omitted_status, omitted_payload = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("prompt_slug", "agent_2_identity_disclosure"),
                ],
                "page_number": 0,
                "page_size": 50,
            },
            analytics=v2_analytics,
            org_project_ids=None,
            org=organization,
        )
        explicit_false_response = view._list_spans_clickhouse(
            strict_request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("prompt_slug", "agent_2_identity_disclosure"),
                ],
                "page_number": 0,
                "page_size": 50,
                "allow_sampled": False,
            },
            analytics=v2_analytics,
            org_project_ids=None,
            org=organization,
        )
        status, payload = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("prompt_slug", "agent_2_identity_disclosure"),
                ],
                "page_number": 0,
                "page_size": 50,
                "allow_sampled": True,
            },
            analytics=v2_analytics,
            org_project_ids=None,
            org=organization,
        )

    assert omitted_status == "ok"
    assert omitted_payload["metadata"]["query_complete"] is True
    assert omitted_payload["metadata"]["total_rows_is_lower_bound"] is True
    assert explicit_false_response[0] == "error"
    assert explicit_false_response[1][0] == 503
    assert status == "ok"
    bounded_kwargs = bounded_read.call_args.kwargs
    assert isinstance(bounded_kwargs["builder"], SpanListQueryBuilderV2)
    assert bounded_kwargs["page_number"] == 0
    assert bounded_kwargs["page_size"] == 50
    assert bounded_kwargs["analytics"] is v2_analytics
    assert bounded_kwargs["filters"][1] == _attribute_filter(
        "prompt_slug", "agent_2_identity_disclosure"
    )
    assert payload["metadata"]["query_complete"] is True
    assert 0 <= payload["metadata"]["query_elapsed_ms"] < 3_000
    assert payload["metadata"]["query_count"] == 1
    assert payload["metadata"]["query_rows_returned"] == 0
    assert payload["metadata"]["query_result_payload_bytes"] == 0
    assert payload["metadata"]["total_rows_is_lower_bound"] is True
    v2_analytics.execute_ch_query.assert_not_called()
    dispatch.assert_not_called()
    service_remap.assert_not_called()


@override_settings(
    CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "", "QUERY_TYPES_V2_PRIMARY": ""},
)
def test_non_observe_span_list_uses_direct_v2_builder_without_dispatch() -> None:
    from tracer.views.observation_span import ObservationSpanView

    view = ObservationSpanView.__new__(ObservationSpanView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    request = SimpleNamespace()
    strict_request = SimpleNamespace(query_params={"allow_sampled": "false"})
    analytics = mock.MagicMock()
    bounded = BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=0,
        elapsed_ms=1.0,
        query_count=1,
        rows_returned=0,
        result_payload_bytes=0,
        attempts=(),
    )

    with (
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded,
        ) as bounded_read,
        mock.patch(
            "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
            side_effect=AssertionError("SPAN_LIST dispatch must not be consulted"),
        ) as dispatch,
        mock.patch(
            "tracer.services.clickhouse.v2.query_service.query_service_for_builder",
            side_effect=AssertionError("SPAN_LIST service remap must not be consulted"),
        ) as service_remap,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        omitted_status, omitted_payload = view._list_spans_non_observe_clickhouse(
            request,
            "00000000-0000-4000-8000-000000000099",
            SimpleNamespace(project_id=PROJECT_ID),
            analytics,
            {
                "filters": [_time_filter()],
                "page_number": 0,
                "page_size": 25,
            },
        )
        explicit_false_response = view._list_spans_non_observe_clickhouse(
            strict_request,
            "00000000-0000-4000-8000-000000000099",
            SimpleNamespace(project_id=PROJECT_ID),
            analytics,
            {
                "filters": [_time_filter()],
                "page_number": 0,
                "page_size": 25,
                "allow_sampled": False,
            },
        )
        status_name, payload = view._list_spans_non_observe_clickhouse(
            request,
            "00000000-0000-4000-8000-000000000099",
            SimpleNamespace(project_id=PROJECT_ID),
            analytics,
            {
                "filters": [_time_filter()],
                "page_number": 0,
                "page_size": 25,
                "allow_sampled": True,
            },
        )

    assert omitted_status == "ok"
    assert omitted_payload["metadata"]["query_complete"] is True
    assert omitted_payload["metadata"]["total_rows_is_lower_bound"] is True
    assert explicit_false_response[0] == "error"
    assert explicit_false_response[1][0] == 503
    assert status_name == "ok"
    assert payload["metadata"]["total_rows"] == 0
    assert payload["metadata"]["total_rows_is_lower_bound"] is True
    assert isinstance(bounded_read.call_args.kwargs["builder"], SpanListQueryBuilderV2)
    assert bounded_read.call_args.kwargs["analytics"] is analytics
    dispatch.assert_not_called()
    service_remap.assert_not_called()


@override_settings(
    CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "SPAN_LIST"},
)
def test_span_list_nonempty_page_content_shares_wall_budget() -> None:
    from tracer.views.observation_span import (
        SPAN_LIST_CANDIDATE_DEADLINE_MS,
        SPAN_LIST_ENRICHMENT_TIMEOUT_MS,
        SPAN_LIST_READ_SETTINGS,
        SPAN_LIST_WALL_DEADLINE_MS,
        ObservationSpanView,
    )

    started = END - timedelta(minutes=1)
    row = {
        "project_id": PROJECT_ID,
        "trace_id": "trace-a",
        "id": "span-a",
        "start_time": started,
        "created_at": started,
        "name": "span-a",
        "observation_type": "llm",
        "status": "OK",
        "cost": 0.001,
    }
    bounded = BoundedFilterPage(
        rows=[row],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=1,
        elapsed_ms=2.0,
        query_count=1,
        rows_returned=1,
        result_payload_bytes=10,
        attempts=(),
    )

    class RecordingAnalytics:
        def __init__(self):
            self.calls = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((params, timeout_ms, settings))
            data = [
                {
                    "project_id": PROJECT_ID,
                    "trace_id": "trace-a",
                    "id": "span-a",
                    "start_time": started,
                    "input": "in",
                    "output": "out",
                    "attributes_extra": "{}",
                    "attrs_string": {},
                    "attrs_number": {},
                    "attrs_bool": {},
                }
            ]
            return QueryResult(data, len(data), "clickhouse", 0.0)

    analytics = RecordingAnalytics()
    view = ObservationSpanView.__new__(ObservationSpanView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )

    with (
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded,
        ) as bounded_read,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        status_name, payload = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
                "allow_sampled": True,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert status_name == "ok"
    assert payload["table"][0]["span_id"] == "span-a"
    assert payload["metadata"]["query_count"] == 2
    assert 0 <= payload["metadata"]["query_elapsed_ms"] < SPAN_LIST_WALL_DEADLINE_MS
    assert (
        bounded_read.call_args.kwargs["deadline_ms"] <= SPAN_LIST_CANDIDATE_DEADLINE_MS
    )
    assert bounded_read.call_args.kwargs.get("retry_wide_read_budget", False) is False
    assert SPAN_LIST_CANDIDATE_DEADLINE_MS <= SPAN_LIST_WALL_DEADLINE_MS
    assert SPAN_LIST_ENRICHMENT_TIMEOUT_MS <= SPAN_LIST_WALL_DEADLINE_MS
    assert len(analytics.calls) == 1
    assert 0 < analytics.calls[0][1] <= SPAN_LIST_ENRICHMENT_TIMEOUT_MS
    assert analytics.calls[0][2] == SPAN_LIST_READ_SETTINGS


@override_settings(
    CLICKHOUSE_V2={"QUERY_TYPES_V2_ONLY": "SPAN_LIST"},
)
def test_span_numbered_page_does_not_retry_failed_wide_reads() -> None:
    from tracer.views.observation_span import (
        SPAN_LIST_CANDIDATE_DEADLINE_MS,
        ObservationSpanView,
    )

    view = ObservationSpanView.__new__(ObservationSpanView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_incomplete_empty_page("read_budget_exceeded"),
        ) as bounded_read,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 3,
                "page_size": 25,
                "allow_sampled": True,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert response[2] == {"code": "service_unavailable"}
    assert "DB::Exception" not in str(response)
    assert bounded_read.call_args.kwargs["page_number"] == 3
    assert bounded_read.call_args.kwargs.get("retry_wide_read_budget", False) is False
    assert (
        bounded_read.call_args.kwargs["deadline_ms"] <= SPAN_LIST_CANDIDATE_DEADLINE_MS
    )
    analytics.execute_ch_query.assert_not_called()


def test_trace_route_returns_sanitized_degraded_page_for_filtered_sort() -> None:
    from tracer.views.trace import TraceView

    class SortedTraceBuilder(TraceListQueryBuilderV2):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.sort_params = [{"column_id": "latency", "order": "desc"}]

    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.services.clickhouse.v2.query_builders.trace_list."
            "TraceListQueryBuilderV2",
            SortedTraceBuilder,
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page"
        ) as bounded_read,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert response[2]["code"] == "service_unavailable"
    bounded_read.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


def test_span_route_returns_sanitized_degraded_page_for_filtered_end_user() -> None:
    from tracer.views.observation_span import ObservationSpanView

    class EndUserSpanBuilder(SpanListQueryBuilderV2):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.end_user_id = "00000000-0000-4000-8000-000000000002"

    view = ObservationSpanView.__new__(ObservationSpanView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.views.observation_span.SpanListQueryBuilderV2",
            EndUserSpanBuilder,
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page"
        ) as bounded_read,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert response[2]["code"] == "service_unavailable"
    bounded_read.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


@dataclass
class _FakeBuilder:
    rows: list[dict[str, Any]]
    start: datetime = START
    end: datetime = END
    key_field: str = "id"
    match_rows: list[dict[str, Any]] | None = None
    seed_proves_order: bool = True
    recommended_batch_size: int | None = None
    recommended_seed_batch_size: int | None = None

    def parse_time_range(
        self, _filters: list[dict[str, Any]]
    ) -> tuple[datetime, datetime]:
        return self.start, self.end

    def build_filter_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        return "seed", {
            "slice_start": slice_start,
            "slice_end": slice_end,
            "limit": limit,
            "before_start_time": before_start_time,
            "before_id": before_id,
        }

    def build_filter_match_query(
        self, candidate_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        return "match", {"candidate_ids": tuple(candidate_ids)}

    def filter_seed_proves_result_order(self) -> bool:
        return self.seed_proves_order

    def recommended_filter_classify_batch_size(self) -> int | None:
        return self.recommended_batch_size

    def recommended_filter_seed_batch_size(self) -> int:
        return self.recommended_seed_batch_size or self.recommended_batch_size or 200


class _FakeExecutor:
    def __init__(self, builder: _FakeBuilder, *, fail: Exception | None = None):
        self.builder = builder
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> QueryResult:
        self.calls.append((query, params))
        if self.fail is not None:
            raise self.fail
        if query == "match":
            wanted = set(params["candidate_ids"])
            source = (
                self.builder.rows
                if self.builder.match_rows is None
                else self.builder.match_rows
            )
            rows = [row for row in source if row["id"] in wanted]
        else:
            rows = [
                row
                for row in self.builder.rows
                if params["slice_start"] <= row["start_time"] < params["slice_end"]
            ]
            rows.sort(key=lambda row: (row["start_time"], row["id"]), reverse=True)
            before_time = params["before_start_time"]
            before_id = params["before_id"]
            if before_time is not None:
                rows = [
                    row
                    for row in rows
                    if (row["start_time"], row["id"]) < (before_time, before_id)
                ]
            rows = rows[: params["limit"]]
        return QueryResult(rows, len(rows), "clickhouse", 1.0)


@dataclass
class _ClassifierSettingsFakeBuilder(_FakeBuilder):
    @staticmethod
    def recommended_filter_classify_read_settings() -> dict[str, int]:
        return {
            "max_block_size": 2_048,
            "preferred_max_column_in_block_size_bytes": 1_048_576,
        }


@dataclass
class _RecommendedQueryCountFakeBuilder(_FakeBuilder):
    @staticmethod
    def recommended_filter_max_query_count() -> int:
        return 128


@dataclass
class _WideInitialSliceFakeBuilder(_FakeBuilder):
    @staticmethod
    def recommended_filter_initial_slice_width() -> timedelta:
        return timedelta(hours=1)


@dataclass
class _CursorZeroProbeWideInitialFakeBuilder(_WideInitialSliceFakeBuilder):
    @staticmethod
    def supports_filter_exact_zero_probe() -> bool:
        return True

    @staticmethod
    def recommended_filter_exact_zero_probe_timeout_ms() -> int:
        return 1_500

    @staticmethod
    def recommended_filter_exact_zero_probe_max_bytes() -> int:
        return 256 * 1024 * 1024

    @staticmethod
    def build_filter_exact_zero_probe() -> tuple[str, dict[str, Any]]:
        return "zero_probe", {}


class _ClassifierSettingsFakeExecutor(_FakeExecutor):
    def __init__(self, builder: _FakeBuilder):
        super().__init__(builder)
        self.settings_by_query: list[tuple[str, dict[str, Any]]] = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.settings_by_query.append((query, dict(settings)))
        return super().execute_ch_query(
            query,
            params,
            timeout_ms=timeout_ms,
            settings=settings,
        )


@dataclass
class _IdentityHydrationFakeBuilder(_FakeBuilder):
    @staticmethod
    def use_identity_only_filter_classification() -> bool:
        return True

    @staticmethod
    def recommended_filter_page_hydration_reserve_ms() -> int:
        return 300

    @staticmethod
    def build_filter_identity_match_query_from_seed_rows(rows):
        return "match_identity", {"candidate_ids": tuple(row["id"] for row in rows)}

    @staticmethod
    def build_filter_page_hydration_query(rows):
        return "hydrate", {"candidate_ids": tuple(row["id"] for row in rows)}

    @staticmethod
    def bounded_filter_page_hydration_identity(row):
        start_time = row["start_time"]
        if start_time.tzinfo is not None:
            start_time = start_time.astimezone(UTC).replace(tzinfo=None)
        return row["id"], row["root_span_id"], start_time


@dataclass
class _CursorPageFillIdentityHydrationFakeBuilder(_IdentityHydrationFakeBuilder):
    @staticmethod
    def fill_bounded_cursor_page_across_slices() -> bool:
        return True


@dataclass
class _WideGenericCursorIdentityHydrationFakeBuilder(
    _CursorPageFillIdentityHydrationFakeBuilder
):
    @staticmethod
    def recommended_filter_cursor_seed_batch_size() -> int:
        return 80


@dataclass
class _NewestPartitionTraceFakeBuilder(_IdentityHydrationFakeBuilder):
    """Model the default trace list's tail-first adaptive traversal."""

    def recommended_filter_max_slice_width(self) -> timedelta:
        return self.end - self.start

    @staticmethod
    def allow_repeated_eager_identity_prefix_flushes() -> bool:
        return True


@dataclass
class _CandidateFirstIdentityHydrationFakeBuilder(_IdentityHydrationFakeBuilder):
    @staticmethod
    def supports_filter_candidate_seed_page() -> bool:
        return True

    @staticmethod
    def filter_candidate_seed_proves_result_order() -> bool:
        return True

    @staticmethod
    def recommended_filter_cursor_seed_batch_size() -> int:
        return 101

    def build_filter_candidate_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        return "candidate_seed", {
            "slice_start": slice_start,
            "slice_end": slice_end,
            "limit": limit,
            "before_start_time": before_start_time,
            "before_id": before_id,
        }

    def build_filter_seed_page(self, **_kwargs):
        raise AssertionError("generic chronological seed must not run")

    def recommended_filter_initial_slice_width(self) -> timedelta:
        return self.end - self.start

    def recommended_filter_max_slice_width(self) -> timedelta:
        return self.end - self.start


class _IdentityHydrationFakeExecutor(_FakeExecutor):
    def __init__(
        self,
        builder,
        *,
        hydration_rows=None,
        reverse_hydration=False,
        clock=None,
        durations_ms=None,
    ):
        super().__init__(builder)
        self.hydration_rows = hydration_rows
        self.reverse_hydration = reverse_hydration
        self.clock = clock
        self.durations_ms = dict(durations_ms or {})
        self.timeouts: list[tuple[str, int]] = []
        self.settings_by_query: list[tuple[str, dict[str, Any]]] = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.timeouts.append((query, timeout_ms))
        self.settings_by_query.append((query, dict(settings)))
        if query in {"match_identity", "hydrate"}:
            self.calls.append((query, params))
            wanted = set(params["candidate_ids"])
            source = (
                self.hydration_rows
                if query == "hydrate" and self.hydration_rows is not None
                else (
                    self.builder.rows
                    if self.builder.match_rows is None
                    else self.builder.match_rows
                )
            )
            rows = [row for row in source if row["id"] in wanted]
            if query == "match_identity":
                rows = [
                    {
                        "id": row["id"],
                        "root_span_id": row["root_span_id"],
                        "start_time": row["start_time"],
                    }
                    for row in rows
                ]
            elif self.reverse_hydration:
                rows = list(reversed(rows))
            result = QueryResult(rows, len(rows), "clickhouse", 1.0)
        else:
            result = super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )

        duration_ms = int(self.durations_ms.get(query, 0))
        if self.clock is not None and duration_ms:
            self.clock.advance_ms(min(duration_ms, timeout_ms))
            if duration_ms >= timeout_ms:
                raise ReadDeadlineExceeded(f"{query} timeout")
        return result


def test_candidate_first_seed_keeps_exact_classifier_and_page_hydration() -> None:
    row = {
        "id": "trace-user",
        "root_span_id": "root-user",
        "start_time": END - timedelta(days=180),
        "name": "user trace",
    }
    builder = _CandidateFirstIdentityHydrationFakeBuilder(
        rows=[row],
        start=END - timedelta(days=365),
        end=END,
        key_field="id",
        recommended_batch_size=80,
        recommended_seed_batch_size=200,
    )
    executor = _IdentityHydrationFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(END - timedelta(days=365), END)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=30_000,
        max_candidates=512,
        max_seed_attempts=128,
        max_query_count=128,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is True
    assert page.rows == [row]
    assert page.has_more is False
    assert [query for query, _ in executor.calls] == [
        "candidate_seed",
        "match_identity",
        "hydrate",
    ]
    seed_params = executor.calls[0][1]
    assert seed_params["slice_start"] == END - timedelta(days=365)
    assert seed_params["slice_end"] == END
    assert seed_params["limit"] == 101
    assert [attempt.kind for attempt in page.attempts] == [
        "seed",
        "classify",
        "hydrate",
    ]
    assert all(
        "max_rows_to_read" not in settings for _, settings in executor.settings_by_query
    )
    assert all(
        query_settings["max_bytes_to_read"] == settings.OBSERVABILITY_LIST_MAX_BYTES
        and query_settings["max_memory_usage"]
        == settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES
        and query_settings["max_threads"] == 1
        and 0 < query_settings["max_result_rows"] <= 10_000
        for _, query_settings in executor.settings_by_query
    )


@dataclass
class _CandidateWitnessHydrationFakeBuilder(_IdentityHydrationFakeBuilder):
    @staticmethod
    def prefer_filter_candidate_witness_probe_first():
        return True

    @staticmethod
    def build_filter_candidate_witness_probe(rows):
        return "prefilter", {"candidate_ids": tuple(row["id"] for row in rows)}


@dataclass
class _CandidateWitnessUnhydratedFakeBuilder(_FakeBuilder):
    """Model the membership-only eval selector's final identity projection."""

    @staticmethod
    def supports_filter_candidate_witness_prefilter_without_hydration():
        return True

    @staticmethod
    def use_buffered_identity_filter_classification_without_hydration():
        return True

    @staticmethod
    def prefer_filter_candidate_witness_probe_first():
        return True

    @staticmethod
    def build_filter_candidate_witness_probe(rows):
        return "prefilter", {"candidate_ids": tuple(row["id"] for row in rows)}


@dataclass
class _StratifiedCandidateWitnessFakeBuilder(_CandidateWitnessUnhydratedFakeBuilder):
    @staticmethod
    def recommended_filter_candidate_witness_probe_strata():
        return 8

    @staticmethod
    def recommended_filter_candidate_witness_fallback_classify_batch_size():
        return 2

    @staticmethod
    def build_filter_candidate_witness_probe(
        rows,
        *,
        slice_start=None,
        slice_end=None,
    ):
        return "prefilter", {
            "candidate_ids": tuple(row["id"] for row in rows),
            "slice_start": slice_start,
            "slice_end": slice_end,
        }


class _CandidateWitnessHydrationFakeExecutor(_IdentityHydrationFakeExecutor):
    def __init__(
        self,
        builder,
        *,
        witness_ids=(),
        fail_prefilter: bool | Exception = False,
        **kwargs,
    ):
        super().__init__(builder, **kwargs)
        self.witness_ids = set(witness_ids)
        self.fail_prefilter = fail_prefilter
        self.prefilter_settings = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if query != "prefilter":
            return super().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )
        self.calls.append((query, params))
        self.timeouts.append((query, timeout_ms))
        self.prefilter_settings.append(dict(settings))
        if self.fail_prefilter:
            if isinstance(self.fail_prefilter, Exception):
                raise self.fail_prefilter
            raise ReadDeadlineExceeded("candidate witness budget")
        rows = [
            {"id": candidate_id}
            for candidate_id in params["candidate_ids"]
            if candidate_id in self.witness_ids
        ]
        return QueryResult(rows, len(rows), "clickhouse", 1.0)


class _StratifiedCandidateWitnessFakeExecutor(_CandidateWitnessHydrationFakeExecutor):
    def __init__(
        self,
        builder,
        *,
        witness_times=None,
        blocked_instant=None,
        extra_identity=None,
        **kwargs,
    ):
        super().__init__(builder, **kwargs)
        self.witness_times = dict(witness_times or {})
        self.blocked_instant = blocked_instant
        self.extra_identity = extra_identity

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if query != "prefilter":
            return super().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )
        self.calls.append((query, params))
        self.timeouts.append((query, timeout_ms))
        self.prefilter_settings.append(dict(settings))
        slice_start = params["slice_start"]
        slice_end = params["slice_end"]
        if (
            self.blocked_instant is not None
            and slice_start <= self.blocked_instant < slice_end
        ):
            raise ReadDeadlineExceeded("candidate witness stratum budget")
        rows = [
            {"id": candidate_id}
            for candidate_id in params["candidate_ids"]
            if candidate_id in self.witness_times
            and slice_start <= self.witness_times[candidate_id] < slice_end
        ]
        if self.extra_identity is not None:
            rows.append({"id": self.extra_identity})
        return QueryResult(rows, len(rows), "clickhouse", 1.0)


@dataclass
class _OrgIdentityHydrationFakeBuilder(_IdentityHydrationFakeBuilder):
    @staticmethod
    def bounded_filter_row_identity(row):
        return row["project_id"], row["trace_id"]

    @staticmethod
    def bounded_filter_row_order_token(row):
        return row["trace_id"], row["project_id"]

    bounded_filter_seed_identity = bounded_filter_row_identity
    bounded_filter_seed_order_token = bounded_filter_row_order_token

    @staticmethod
    def build_filter_identity_match_query_from_seed_rows(rows):
        return "match_identity_org", {
            "candidate_identities": tuple(
                (row["project_id"], row["trace_id"]) for row in rows
            )
        }

    @staticmethod
    def build_filter_page_hydration_query(rows):
        return "hydrate_org", {
            "candidate_identities": tuple(
                (row["project_id"], row["trace_id"]) for row in rows
            )
        }

    @staticmethod
    def bounded_filter_page_hydration_identity(row):
        start_time = row["start_time"]
        if start_time.tzinfo is not None:
            start_time = start_time.astimezone(UTC).replace(tzinfo=None)
        return (
            row["project_id"],
            row["trace_id"],
            row["root_span_id"],
            start_time,
        )


class _OrgIdentityHydrationFakeExecutor(_FakeExecutor):
    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        del timeout_ms, settings
        self.calls.append((query, params))
        identity = self.builder.bounded_filter_row_identity
        order_token = self.builder.bounded_filter_row_order_token
        if query in {"match_identity_org", "hydrate_org"}:
            wanted = set(params["candidate_identities"])
            rows = [row for row in self.builder.rows if identity(row) in wanted]
            if query == "match_identity_org":
                rows = [
                    {
                        "project_id": row["project_id"],
                        "trace_id": row["trace_id"],
                        "root_span_id": row["root_span_id"],
                        "start_time": row["start_time"],
                    }
                    for row in rows
                ]
            else:
                rows = list(reversed(rows))
            return QueryResult(rows, len(rows), "clickhouse", 1.0)

        rows = [
            row
            for row in self.builder.rows
            if params["slice_start"] <= row["start_time"] < params["slice_end"]
        ]
        rows.sort(key=lambda row: (row["start_time"], order_token(row)), reverse=True)
        if params["before_start_time"] is not None:
            boundary = params["before_start_time"], params["before_id"]
            rows = [
                row for row in rows if (row["start_time"], order_token(row)) < boundary
            ]
        rows = rows[: params["limit"]]
        return QueryResult(rows, len(rows), "clickhouse", 1.0)


class _UnindexedAnySpanFakeBuilder(_FakeBuilder):
    def supports_filter_anchor_probe(self) -> bool:
        return False

    def build_filter_ordered_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        _, params = self.build_filter_seed_page(
            slice_start=slice_start,
            slice_end=slice_end,
            limit=limit,
            before_start_time=before_start_time,
            before_id=before_id,
        )
        return "ordered_seed", params


class _DistributedMicroSeedFakeBuilder(_UnindexedAnySpanFakeBuilder):
    @staticmethod
    def recommended_filter_unindexed_micro_seed_width() -> timedelta:
        return timedelta(minutes=5)

    @staticmethod
    def recommended_filter_unindexed_micro_seed_strata() -> int:
        return 4

    def build_filter_unindexed_micro_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
    ) -> tuple[str, dict[str, Any]]:
        _, params = self.build_filter_seed_page(
            slice_start=slice_start,
            slice_end=slice_end,
            limit=limit,
        )
        return "micro_seed", params

    @staticmethod
    def filter_unindexed_micro_seed_proves_result_order() -> bool:
        return False


class _OrderedTraceCursorFakeBuilder(_UnindexedAnySpanFakeBuilder):
    @staticmethod
    def filter_cursor_seed_keyset_is_safe() -> bool:
        return True


class _OrderedRootFakeExecutor(_FakeExecutor):
    """Model WHERE keysetting before ORDER BY / LIMIT 1 BY trace."""

    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> QueryResult:
        if query != "ordered_seed":
            return super().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )
        self.calls.append((query, params))
        rows = [
            row
            for row in self.builder.rows
            if params["slice_start"] <= row["start_time"] < params["slice_end"]
        ]
        rows.sort(key=lambda row: (row["start_time"], row["id"]), reverse=True)
        if params["before_start_time"] is not None:
            boundary = params["before_start_time"], params["before_id"]
            rows = [row for row in rows if (row["start_time"], row["id"]) < boundary]
        distinct_traces: list[dict[str, Any]] = []
        seen_trace_ids: set[str] = set()
        for row in rows:
            trace_id = str(row["id"])
            if trace_id in seen_trace_ids:
                continue
            seen_trace_ids.add(trace_id)
            distinct_traces.append(row)
        limited = distinct_traces[: params["limit"]]
        return QueryResult(limited, len(limited), "clickhouse", 1.0)


class _AnchorFakeBuilder(_FakeBuilder):
    def supports_filter_anchor_probe(self) -> bool:
        return True

    @staticmethod
    def build_filter_anchor_probe(
        *,
        limit: int,
        slice_start: datetime | None = None,
        slice_end: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        params = {"limit": limit}
        if slice_start is not None and slice_end is not None:
            params.update(slice_start=slice_start, slice_end=slice_end)
        return "anchor", params

    def build_filter_ordered_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        _, params = self.build_filter_seed_page(
            slice_start=slice_start,
            slice_end=slice_end,
            limit=limit,
            before_start_time=before_start_time,
            before_id=before_id,
        )
        return "ordered_seed", params


class _GraphKeyWitnessFakeBuilder(_AnchorFakeBuilder):
    @staticmethod
    def supports_graph_key_witness_probe() -> bool:
        return True

    @staticmethod
    def build_filter_graph_key_witness_probe(
        *,
        limit: int,
        slice_start: datetime | None = None,
        slice_end: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "graph_key_witness": True}
        if slice_start is not None and slice_end is not None:
            params.update(slice_start=slice_start, slice_end=slice_end)
        return "anchor", params


class _SkipFullAnchorFakeBuilder(_AnchorFakeBuilder):
    @staticmethod
    def skip_full_window_filter_anchor_probe() -> bool:
        return True


class _SmallAnchorFakeBuilder(_SkipFullAnchorFakeBuilder):
    @staticmethod
    def recommended_filter_anchor_probe_limit() -> int:
        return 64

    @staticmethod
    def recommended_filter_anchor_probe_timeout_ms() -> int:
        return 300

    @staticmethod
    def recommended_filter_anchor_probe_strata() -> int:
        return 4

    @staticmethod
    def recommended_filter_anchor_probe_max_bytes_to_read() -> int:
        return 96 * 1024 * 1024


class _InitialCursorAnchorFakeBuilder(_AnchorFakeBuilder):
    @staticmethod
    def allow_filter_anchor_probe_for_initial_continuation() -> bool:
        return True

    @staticmethod
    def recommended_filter_anchor_probe_limit() -> int:
        return 3

    @staticmethod
    def recommended_filter_anchor_probe_timeout_ms() -> int:
        return 300

    @staticmethod
    def recommended_filter_anchor_probe_strata() -> int:
        return 1

    @staticmethod
    def recommended_filter_anchor_probe_max_bytes_to_read() -> int:
        return 96 * 1024 * 1024


class _InitialCursorBudgetAnchorFakeBuilder(_InitialCursorAnchorFakeBuilder):
    @staticmethod
    def recommended_filter_max_query_count() -> int:
        return 5


class _AnchorFakeExecutor(_FakeExecutor):
    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> QueryResult:
        if query != "anchor":
            return super().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )
        self.calls.append((query, params))
        rows = self.builder.rows
        if "slice_start" in params:
            rows = [
                row
                for row in rows
                if params["slice_start"] <= row["start_time"] < params["slice_end"]
            ]
        rows = rows[: params["limit"]]
        return QueryResult(rows, len(rows), "clickhouse", 1.0)


class _TimedAnchorFakeExecutor(_AnchorFakeExecutor):
    def __init__(self, builder: _FakeBuilder, *, fail_anchor: bool = False):
        super().__init__(builder)
        self.fail_anchor = fail_anchor
        self.timeouts: list[tuple[str, int]] = []
        self.settings: list[tuple[str, dict[str, Any]]] = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.timeouts.append((query, timeout_ms))
        self.settings.append((query, settings))
        if query == "anchor" and self.fail_anchor:
            self.calls.append((query, params))
            raise ReadDeadlineExceeded("anchor timeout")
        return super().execute_ch_query(
            query, params, timeout_ms=timeout_ms, settings=settings
        )


@dataclass
class _ManualMonotonic:
    seconds: float = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance_ms(self, milliseconds: int) -> None:
        self.seconds += milliseconds / 1000


class _ProductionTimedAnchorFakeExecutor(_TimedAnchorFakeExecutor):
    """Advance a manual clock and enforce the selector's statement timeout."""

    def __init__(
        self,
        builder: _FakeBuilder,
        *,
        clock: _ManualMonotonic,
        anchor_durations_ms: list[int],
        seed_duration_ms: int,
        match_duration_ms: int,
    ):
        super().__init__(builder)
        self.clock = clock
        self.anchor_durations_ms = list(anchor_durations_ms)
        self.seed_duration_ms = seed_duration_ms
        self.match_duration_ms = match_duration_ms

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        result = super().execute_ch_query(
            query, params, timeout_ms=timeout_ms, settings=settings
        )
        if query == "anchor":
            duration_ms = self.anchor_durations_ms.pop(0)
        elif query in {"seed", "ordered_seed"}:
            duration_ms = self.seed_duration_ms
        else:
            duration_ms = self.match_duration_ms
        self.clock.advance_ms(min(duration_ms, timeout_ms))
        if duration_ms >= timeout_ms:
            raise ReadDeadlineExceeded(f"{query} timeout")
        return result


class _PhysicalCursorFakeBuilder(_FakeBuilder):
    """Model the full direct-write span identity and public order tuple."""

    @staticmethod
    def bounded_filter_row_identity(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["project_id"],
            row["trace_id"],
            row["id"],
            row["start_time"],
        )

    @staticmethod
    def bounded_filter_row_order_token(row: dict[str, Any]) -> tuple[str, ...]:
        return row["id"], row["trace_id"], row["project_id"]

    bounded_filter_seed_identity = bounded_filter_row_identity
    bounded_filter_seed_order_token = bounded_filter_row_order_token

    def build_filter_match_query_from_seed_rows(
        self, rows: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        identities = tuple(self.bounded_filter_row_identity(row) for row in rows)
        return "match_physical", {"candidate_identities": identities}


class _PhysicalCursorFakeExecutor(_FakeExecutor):
    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> QueryResult:
        del timeout_ms, settings
        self.calls.append((query, params))
        identity = self.builder.bounded_filter_row_identity

        def order(row: dict[str, Any]) -> tuple[Any, ...]:
            return (
                row["start_time"],
                self.builder.bounded_filter_row_order_token(row),
            )

        if query == "match_physical":
            wanted = set(params["candidate_identities"])
            rows = [row for row in self.builder.rows if identity(row) in wanted]
        else:
            rows = [
                row
                for row in self.builder.rows
                if params["slice_start"] <= row["start_time"] < params["slice_end"]
            ]
            rows.sort(key=order, reverse=True)
            if params["before_start_time"] is not None:
                boundary = (
                    params["before_start_time"],
                    params["before_id"],
                )
                rows = [row for row in rows if order(row) < boundary]
            rows = rows[: params["limit"]]
        return QueryResult(rows, len(rows), "clickhouse", 1.0)


class _OrgTraceCursorFakeBuilder(_FakeBuilder):
    """Model organization trace identity and its signed result order."""

    @staticmethod
    def bounded_filter_row_identity(row: dict[str, Any]) -> tuple[str, str]:
        return str(row["project_id"]), str(row["trace_id"])

    @staticmethod
    def bounded_filter_row_order_token(row: dict[str, Any]) -> tuple[str, str]:
        return str(row["trace_id"]), str(row["project_id"])

    bounded_filter_seed_identity = bounded_filter_row_identity
    bounded_filter_seed_order_token = bounded_filter_row_order_token

    def build_filter_match_query_from_seed_rows(
        self, rows: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        identities = tuple(self.bounded_filter_row_identity(row) for row in rows)
        return "match_org_traces", {"candidate_identities": identities}


class _OrgTraceCursorFakeExecutor(_FakeExecutor):
    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> QueryResult:
        del timeout_ms, settings
        self.calls.append((query, params))
        identity = self.builder.bounded_filter_row_identity
        order_token = self.builder.bounded_filter_row_order_token

        def order(row: dict[str, Any]) -> tuple[Any, ...]:
            return row["start_time"], order_token(row)

        if query == "match_org_traces":
            wanted = set(params["candidate_identities"])
            rows = [row for row in self.builder.rows if identity(row) in wanted]
        else:
            rows = [
                row
                for row in self.builder.rows
                if params["slice_start"] <= row["start_time"] < params["slice_end"]
            ]
            rows.sort(key=order, reverse=True)
            if params["before_start_time"] is not None:
                boundary = params["before_start_time"], params["before_id"]
                rows = [row for row in rows if order(row) < boundary]
            rows = rows[: params["limit"]]
        return QueryResult(rows, len(rows), "clickhouse", 1.0)


class _EmptyExecutor:
    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> QueryResult:
        return QueryResult([], 0, "clickhouse", 0.0)


@pytest.mark.parametrize(
    "builder_cls,key_field",
    [
        (TraceListQueryBuilder, "trace_id"),
        (SpanListQueryBuilder, "id"),
        (SessionListQueryBuilder, "session_id"),
    ],
)
def test_default_window_is_pinned_for_empty_bounded_reads(
    builder_cls, key_field
) -> None:
    first_window = (START, END)
    drifted_window = (
        START + timedelta(microseconds=1),
        END + timedelta(microseconds=1),
    )
    filters = [_attribute_filter("final_status", "Rejected")]

    with mock.patch.object(
        BaseQueryBuilder,
        "parse_time_range",
        side_effect=[first_window, drifted_window],
    ) as parse_time_range:
        builder = builder_cls(project_id=PROJECT_ID, filters=filters)
        page = read_bounded_filter_page(
            builder=builder,
            analytics=_EmptyExecutor(),
            filters=filters,
            key_field=key_field,
            page_number=0,
            page_size=25,
            deadline_ms=5_000,
        )

    assert parse_time_range.call_count == 1
    assert page.complete is True
    assert page.rows == []
    assert page.error_code is None


def test_bounded_reader_rejects_mechanically_impossible_prefix_before_ch() -> None:
    builder = _FakeBuilder([])
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=12_800,
        deadline_ms=10_000,
        max_seed_attempts=128,
        max_candidates=200,
        max_query_count=128,
        classify_batch_size=200,
    )

    assert page.complete is False
    assert page.error_code == "page_depth_exceeded"
    assert page.query_count == 0
    assert executor.calls == []


def test_builder_can_widen_only_the_first_exact_seed_slice() -> None:
    rows = [
        {
            "id": "newest",
            "root_span_id": "root-newest",
            "start_time": END - timedelta(minutes=10),
        },
        {
            "id": "sentinel",
            "root_span_id": "root-sentinel",
            "start_time": END - timedelta(minutes=45),
        },
    ]
    builder = _WideInitialSliceFakeBuilder(
        rows,
        start=END - timedelta(days=14),
        end=END,
        recommended_batch_size=2,
        recommended_seed_batch_size=2,
    )
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=1,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.has_more is True
    assert [row["id"] for row in page.rows] == ["newest"]
    assert [query for query, _params in executor.calls] == ["seed", "match"]
    assert executor.calls[0][1]["slice_start"] == END - timedelta(hours=1)
    assert executor.calls[0][1]["slice_end"] == END


def test_default_trace_page_is_window_invariant_and_reads_only_newest_slice() -> None:
    rows = [
        {
            "id": f"trace-{index:02d}",
            "root_span_id": f"root-{index:02d}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(30)
    ]
    pages = []
    executors = []

    for days in (30, 365):
        builder = _NewestPartitionTraceFakeBuilder(
            rows=rows,
            start=END - timedelta(days=days),
            end=END,
            recommended_batch_size=80,
            recommended_seed_batch_size=50,
        )
        executor = _IdentityHydrationFakeExecutor(builder)
        pages.append(
            read_bounded_filter_page(
                builder=builder,
                analytics=executor,
                filters=[_time_filter(builder.start, builder.end)],
                key_field="id",
                page_number=0,
                page_size=20,
                deadline_ms=9_500,
            )
        )
        executors.append(executor)

    assert all(page.complete and page.has_more for page in pages)
    assert [[row["id"] for row in page.rows] for page in pages] == [
        [f"trace-{index:02d}" for index in range(20)],
        [f"trace-{index:02d}" for index in range(20)],
    ]
    for executor in executors:
        assert [query for query, _params in executor.calls] == [
            "seed",
            "match_identity",
            "hydrate",
        ]
        seed_params = executor.calls[0][1]
        assert seed_params["slice_start"] == END - timedelta(minutes=5)
        assert seed_params["slice_end"] == END


def test_default_trace_page_expands_only_after_newer_candidates_are_rejected() -> None:
    rejected_rows = [
        {
            "id": f"rejected-{index:02d}",
            "root_span_id": f"rejected-root-{index:02d}",
            "start_time": END - timedelta(minutes=1, seconds=index),
        }
        for index in range(25)
    ]
    matching_rows = [
        {
            "id": f"match-{index:02d}",
            "root_span_id": f"match-root-{index:02d}",
            "start_time": END - timedelta(minutes=16, seconds=index),
        }
        for index in range(22)
    ]
    builder = _NewestPartitionTraceFakeBuilder(
        rows=[*rejected_rows, *matching_rows],
        match_rows=matching_rows,
        start=END - timedelta(days=365),
        end=END,
        recommended_batch_size=80,
        recommended_seed_batch_size=50,
    )
    executor = _IdentityHydrationFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(builder.start, builder.end)],
        key_field="id",
        page_number=0,
        page_size=20,
        deadline_ms=9_500,
    )

    assert page.complete is True
    assert page.has_more is True
    assert [row["id"] for row in page.rows] == [
        f"match-{index:02d}" for index in range(20)
    ]
    seed_params = [params for query, params in executor.calls if query == "seed"]
    assert [params["slice_end"] - params["slice_start"] for params in seed_params] == [
        timedelta(minutes=5),
        timedelta(minutes=10),
        timedelta(minutes=20),
    ]
    assert seed_params[-1]["slice_start"] == END - timedelta(minutes=35)


def test_default_trace_builder_allows_tail_first_walk_to_cover_full_window() -> None:
    filters = [_time_filter(END - timedelta(days=365), END)]
    builder = TraceListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)

    assert builder.recommended_filter_initial_slice_width() is None
    assert builder.recommended_filter_max_slice_width() == timedelta(days=365)

    custom_sort = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        sort_params=[{"column_id": "latency", "sort": "desc"}],
    )
    assert custom_sort.recommended_filter_max_slice_width() is None

    project_version = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        project_version_id="00000000-0000-4000-8000-000000000099",
        filters=filters,
    )
    assert project_version.recommended_filter_max_slice_width() == timedelta(days=365)


def test_time_only_bulk_identity_scan_uses_one_finite_full_window_seed() -> None:
    filters = [_time_filter(END - timedelta(days=365), END)]
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
    )

    assert builder.recommended_filter_initial_slice_width() == timedelta(days=365)
    assert builder.recommended_filter_max_slice_width() == timedelta(days=365)
    assert builder.should_retry_filter_wide_read_budget() is True

    query, params = builder.build_filter_ordered_seed_page(
        slice_start=END - timedelta(days=365),
        slice_end=END,
        limit=201,
    )
    compact_query = " ".join(query.split())
    assert "PREWHERE project_id = %(project_id)s" in compact_query
    assert "ORDER BY start_time DESC, trace_id DESC" in compact_query
    assert "LIMIT %(filter_seed_limit)s" in compact_query
    assert params["filter_seed_limit"] == 201


def test_filtered_bulk_identity_scan_keeps_bounded_slice_defaults() -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(days=365), END),
            _attribute_filter("final_status", "Rejected"),
        ],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
    )

    assert builder.recommended_filter_initial_slice_width() is None
    assert builder.recommended_filter_max_slice_width() is None
    assert builder.should_retry_filter_wide_read_budget() is False


def test_wide_bulk_seed_retry_stays_off_for_ordinary_and_multi_project_reads() -> None:
    filters = [_time_filter(END - timedelta(days=365), END)]
    ordinary = TraceListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)
    multi_project_bulk = TraceListQueryBuilderV2(
        project_ids=[PROJECT_ID, "00000000-0000-4000-8000-000000000002"],
        filters=filters,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
    )

    assert ordinary.should_retry_filter_wide_read_budget() is False
    assert multi_project_bulk.should_retry_filter_wide_read_budget() is False


def test_session_numbered_page_ceiling_is_deterministic() -> None:
    common = {
        "page_size": 30,
        "max_candidates": 200,
        "classify_batch_size": 200,
        "seed_batch_size": 200,
    }

    assert bounded_numbered_page_depth_exceeded(page_number=0, **common) is False
    assert bounded_numbered_page_depth_exceeded(page_number=1, **common) is False
    # Page 158 needs a 4,771-row prefix and exactly 48 seed/classify reads.
    assert bounded_numbered_page_depth_exceeded(page_number=158, **common) is False
    # Page 159 needs 4,801 rows, beyond 24 x 200 finite seed candidates.
    assert bounded_numbered_page_depth_exceeded(page_number=159, **common) is True


def test_numbered_page_budget_can_reserve_a_speculative_anchor_query() -> None:
    common = {
        "page_number": 0,
        "page_size": 200,
        "max_query_count": 3,
        "classify_batch_size": 200,
        "seed_batch_size": 200,
    }

    # A 201-row prefix needs one ordered seed plus two classifiers. When an
    # anchor may run first, the same page cannot fit the three-query ceiling.
    assert bounded_numbered_page_depth_exceeded(**common) is False
    assert (
        bounded_numbered_page_depth_exceeded(
            **common,
            reserved_query_count=1,
        )
        is True
    )


def test_voice_numbered_page_ceiling_is_deterministic() -> None:
    builder = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID,
        page_number=0,
        page_size=30,
        filters=[_time_filter()],
        eval_config_ids=[],
        annotation_label_ids=[],
    )
    classify_batch_size = int(builder.recommended_filter_classify_batch_size() or 50)
    common = {
        "page_size": 30,
        "classify_batch_size": classify_batch_size,
        "seed_batch_size": builder.recommended_filter_seed_batch_size(),
    }

    # Public voice page 71 needs a 2,131-row prefix and exactly 48 reads.
    assert bounded_numbered_page_depth_exceeded(page_number=70, **common) is False
    # Public voice page 72 needs 49 reads and cannot fit the finite query budget.
    assert bounded_numbered_page_depth_exceeded(page_number=71, **common) is True


@pytest.mark.parametrize(
    ("page_size", "ceiling_page", "first_rejected_page"),
    [
        (1, 4_998, 4_999),
        (500, 8, 9),
    ],
)
def test_global_numbered_page_work_ceiling_scales_with_page_size(
    page_size: int,
    ceiling_page: int,
    first_rejected_page: int,
) -> None:
    assert (ceiling_page + 2) * page_size == MAX_NUMBERED_PAGE_WORK_ROWS
    assert (
        numbered_page_depth_exceeded(
            page_number=ceiling_page,
            page_size=page_size,
        )
        is False
    )
    assert (
        numbered_page_depth_exceeded(
            page_number=first_rejected_page,
            page_size=page_size,
        )
        is True
    )


def test_internal_page_zero_candidate_reads_keep_their_own_finite_budget() -> None:
    assert (
        bounded_numbered_page_depth_exceeded(
            page_number=0,
            page_size=4_095,
        )
        is False
    )


@pytest.mark.parametrize(
    "builder_class",
    [TraceListQueryBuilder, SpanListQueryBuilder],
)
def test_exact_ceiling_preserves_trace_span_prefix_membership(builder_class) -> None:
    page_number = 8
    page_size = 500
    builder = builder_class(
        project_id=PROJECT_ID,
        page_number=page_number,
        page_size=page_size,
    )

    _, params = builder.build()
    rows = [{"id": f"row-{index}"} for index in range(params["limit"])]
    page, has_more = paginate_deduped(rows, "id", page_number, page_size)

    assert params["limit"] == MAX_NUMBERED_PAGE_WORK_ROWS
    assert [row["id"] for row in page] == [
        f"row-{index}" for index in range(4_000, 4_500)
    ]
    assert has_more is True


def test_exact_ceiling_preserves_session_offset_membership() -> None:
    builder = SessionListQueryBuilder(
        project_id=PROJECT_ID,
        page_number=8,
        page_size=500,
    )

    _, params = builder.build()

    assert params["offset"] == 4_000
    assert params["limit"] == 501


def _page_depth_exceeded_page() -> BoundedFilterPage:
    return BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=False,
        status="degraded",
        error_code="page_depth_exceeded",
        total_rows_lower_bound=0,
        elapsed_ms=0.0,
        query_count=0,
        rows_returned=0,
        result_payload_bytes=0,
        attempts=(),
    )


def _complete_empty_page() -> BoundedFilterPage:
    return BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=0,
        elapsed_ms=0.0,
        query_count=0,
        rows_returned=0,
        result_payload_bytes=0,
        attempts=(),
    )


def _incomplete_empty_page(
    error_code: str = "scan_budget_exceeded",
) -> BoundedFilterPage:
    return BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=False,
        status="degraded",
        error_code=error_code,
        total_rows_lower_bound=0,
        elapsed_ms=4_500.0,
        query_count=8,
        rows_returned=400,
        result_payload_bytes=8_192,
        attempts=(),
    )


def _observe_trace_request(
    query_params: dict[str, str] | None = None,
) -> SimpleNamespace:
    organization = SimpleNamespace(pk="org-a")
    return SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(pk="user-a", organization=organization),
        query_params=query_params or {},
    )


def _call_observe_trace_list_with_bounded_page(
    *,
    bounded_page: BoundedFilterPage,
    validated_data: dict[str, Any],
    request: SimpleNamespace | None = None,
    analytics: Any | None = None,
) -> tuple[Any, mock.MagicMock, Any, SimpleNamespace]:
    from tracer.views.trace import TraceView

    request = request or _observe_trace_request()
    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    analytics = analytics or mock.MagicMock()

    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded_page,
        ) as bounded_reader,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data=validated_data,
            analytics=analytics,
            org_project_ids=None,
            org=request.organization,
        )

    return response, bounded_reader, analytics, request


def test_observe_trace_legacy_first_page_keeps_bounded_compatibility() -> None:
    validated_data = {
        "filters": [
            _time_filter(),
            _attribute_filter("final_status", "Rechazado"),
        ],
        "page_number": 0,
        "page_size": 25,
    }

    response, bounded_reader, analytics, _request = (
        _call_observe_trace_list_with_bounded_page(
            bounded_page=_complete_empty_page(),
            validated_data=validated_data,
        )
    )

    assert response[0] == "ok"
    assert "cursor_mode" not in validated_data
    bounded_reader.assert_called_once()
    assert bounded_reader.call_args.kwargs["bounded_continuation"] is False
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is False
    analytics.execute_ch_query.assert_not_called()


def test_observe_trace_legacy_deep_page_keeps_bounded_compatibility() -> None:
    response, bounded_reader, analytics, _request = (
        _call_observe_trace_list_with_bounded_page(
            bounded_page=_complete_empty_page(),
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rechazado"),
                ],
                "page_number": 1,
                "page_size": 25,
            },
        )
    )

    assert response[0] == "ok"
    bounded_reader.assert_called_once()
    assert bounded_reader.call_args.kwargs["page_number"] == 1
    assert bounded_reader.call_args.kwargs["bounded_continuation"] is False
    analytics.execute_ch_query.assert_not_called()


def test_observe_trace_explicit_cursor_opt_out_is_rejected_before_clickhouse() -> None:
    response, bounded_reader, analytics, _request = (
        _call_observe_trace_list_with_bounded_page(
            bounded_page=_complete_empty_page(),
            request=_observe_trace_request({"cursor_mode": "false"}),
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rechazado"),
                ],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": False,
            },
        )
    )

    assert response[0] == "error"
    assert response[1][0] == 422
    assert response[2] == {"code": "cursor_required"}
    bounded_reader.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


def test_observe_trace_long_cursor_unsupported_filter_fails_before_clickhouse() -> None:
    from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
        UnsupportedFilterShapeError,
    )
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    request = _observe_trace_request()
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch("tracer.views.trace.snapshot_cursor_supported", return_value=False),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page"
        ) as bounded_reader,
        pytest.raises(UnsupportedFilterShapeError, match="not cursor-safe"),
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rechazado"),
                ],
                "page_number": 0,
                "page_size": 25,
            },
            analytics=analytics,
            org_project_ids=None,
            org=request.organization,
        )

    bounded_reader.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


def test_observe_trace_long_filter_cursor_reaches_bounded_reader() -> None:
    response, bounded_reader, analytics, _request = (
        _call_observe_trace_list_with_bounded_page(
            bounded_page=_complete_empty_page(),
            request=_observe_trace_request({"cursor_mode": "true"}),
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rechazado"),
                ],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": True,
            },
        )
    )

    assert response[0] == "ok"
    bounded_reader.assert_called_once()
    assert bounded_reader.call_args.kwargs["bounded_continuation"] is True
    assert bounded_reader.call_args.kwargs["carry_continuation_slice_width"] is True
    assert bounded_reader.call_args.kwargs.get("retry_wide_read_budget", False) is False
    analytics.execute_ch_query.assert_not_called()


def test_observe_span_legacy_first_page_keeps_bounded_compatibility() -> None:
    from tracer.views.observation_span import ObservationSpanView

    view = ObservationSpanView.__new__(ObservationSpanView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
        query_params={},
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_complete_empty_page(),
        ) as bounded_reader,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        validated_data = {
            "filters": [
                _time_filter(),
                _attribute_filter("final_status", "Rechazado"),
            ],
            "page_number": 0,
            "page_size": 25,
        }
        response = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data=validated_data,
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert response[0] == "ok"
    assert "cursor_mode" not in validated_data
    bounded_reader.assert_called_once()
    assert bounded_reader.call_args.kwargs["bounded_continuation"] is False
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is False
    assert bounded_reader.call_args.kwargs["builder"]._bounded_internal_scan is False
    analytics.execute_ch_query.assert_not_called()


def test_observe_span_explicit_cursor_opt_out_is_rejected_before_clickhouse() -> None:
    from tracer.views.observation_span import ObservationSpanView

    view = ObservationSpanView.__new__(ObservationSpanView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
        query_params={"cursor_mode": "false"},
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page"
        ) as bounded_reader,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rechazado"),
                ],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": False,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert response[0] == "error"
    assert response[1][0] == 422
    assert response[2] == {"code": "cursor_required"}
    bounded_reader.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


def test_observe_span_long_cursor_unsupported_filter_fails_before_clickhouse() -> None:
    from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
        UnsupportedFilterShapeError,
    )
    from tracer.views.observation_span import ObservationSpanView

    view = ObservationSpanView.__new__(ObservationSpanView)
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
        query_params={},
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.views.observation_span.snapshot_cursor_supported",
            return_value=False,
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page"
        ) as bounded_reader,
        pytest.raises(UnsupportedFilterShapeError, match="not cursor-safe"),
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rechazado"),
                ],
                "page_number": 0,
                "page_size": 25,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    bounded_reader.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


def test_observe_span_long_filter_cursor_reaches_bounded_reader() -> None:
    from tracer.views.observation_span import ObservationSpanView

    view = ObservationSpanView.__new__(ObservationSpanView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
        query_params={"cursor_mode": "true"},
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_complete_empty_page(),
        ) as bounded_reader,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rechazado"),
                ],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": True,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert response[0] == "ok"
    bounded_reader.assert_called_once()
    assert bounded_reader.call_args.kwargs["bounded_continuation"] is True
    assert bounded_reader.call_args.kwargs.get("retry_wide_read_budget", False) is False
    analytics.execute_ch_query.assert_not_called()


def test_observe_span_cursor_publishes_safe_checkpoint_after_failed_attempt() -> None:
    from tracer.views.observation_span import ObservationSpanView

    bounded_page = BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=False,
        status="degraded",
        error_code="query_timeout",
        total_rows_lower_bound=0,
        elapsed_ms=30_000.0,
        query_count=2,
        rows_returned=25,
        result_payload_bytes=1_024,
        attempts=(
            FilterReadAttempt(
                kind="classify",
                slice_start=START,
                slice_end=END,
                elapsed_ms=30_000.0,
                rows_returned=0,
                result_payload_bytes=0,
                error_code="query_timeout",
            ),
        ),
        continuation_slice_start=START,
        continuation_slice_end=END,
    )
    view = ObservationSpanView.__new__(ObservationSpanView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
        query_params={"cursor_mode": "true", "allow_sampled": "false"},
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded_page,
        ) as bounded_reader,
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": True,
                "allow_sampled": False,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert response[0] == "ok"
    payload = response[1]
    assert payload["table"] == []
    assert payload["metadata"]["query_complete"] is True
    assert payload["metadata"]["query_status"] == "complete"
    assert payload["metadata"]["query_error_code"] is None
    assert payload["metadata"]["has_more"] is True
    assert isinstance(payload["metadata"]["next_cursor"], str)
    bounded_reader.assert_called_once()
    analytics.execute_ch_query.assert_not_called()


def test_org_user_trace_endpoint_proves_six_month_empty_page_in_one_seed() -> None:
    """A sparse/no-match user page must not walk ninety two-day slices."""

    from tracer.views.trace import TraceView

    project_b = "00000000-0000-4000-8000-000000000002"
    start = END - timedelta(days=180)
    filters = [_time_filter(start, END), _end_user_filter("guest-e3dce503")]
    analytics = mock.MagicMock()
    analytics.execute_ch_query.return_value = QueryResult(
        data=[],
        row_count=0,
        backend_used="clickhouse",
        query_time_ms=1.0,
    )
    request = _observe_trace_request({"cursor_mode": "true"})
    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )

    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_traces_of_session_clickhouse(
            request,
            project_id=None,
            validated_data={
                "filters": filters,
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": True,
                "allow_sampled": False,
            },
            analytics=analytics,
            org_project_ids=[PROJECT_ID, project_b],
            org=request.organization,
        )

    assert response[0] == "ok"
    assert response[1]["table"] == []
    assert response[1]["metadata"]["total_rows"] == 0
    assert analytics.execute_ch_query.call_count == 1
    seed_sql = analytics.execute_ch_query.call_args.args[0]
    seed_params = analytics.execute_ch_query.call_args.args[1]
    compact_seed_sql = " ".join(seed_sql.split())
    assert "matching_user_trace_identities AS" in compact_seed_sql
    assert (
        "AND (project_id, trace_id) IN ( SELECT project_id, trace_id "
        "FROM matching_user_trace_identities )"
    ) in compact_seed_sql
    assert "FROM end_users AS eu FINAL" in seed_sql
    assert "matching_end_user_ids AS" in seed_sql
    assert "matching_end_user_group_ids AS" in seed_sql
    assert "FROM end_user_id_remap AS remap_match FINAL" in seed_sql
    assert "WHERE remap.new_id IN (" in seed_sql
    assert "OVER (PARTITION BY new_id)" not in seed_sql
    assert "end_user_id IN (" in seed_sql
    assert "created_at" not in seed_sql
    assert seed_params["filter_slice_start"] == start
    assert seed_params["filter_slice_end"] == END
    assert seed_params["col_1"] == "guest-e3dce503"


def test_observe_trace_empty_cursor_page_without_checkpoint_fails_closed() -> None:
    response, bounded_reader, analytics, _request = (
        _call_observe_trace_list_with_bounded_page(
            bounded_page=_incomplete_empty_page("query_budget_exceeded"),
            request=_observe_trace_request({"allow_sampled": "true"}),
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": True,
                "allow_sampled": True,
            },
        )
    )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert response[2] == {"code": "service_unavailable"}
    assert "DB::Exception" not in str(response)
    assert "ClickHouse" not in str(response)
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is True
    analytics.execute_ch_query.assert_not_called()


def test_observe_trace_terminal_cursor_uses_global_seen_total() -> None:
    from tracer.services.clickhouse.list_cursor import (
        cursor_scope_for_request,
        encode_list_cursor,
    )

    filters = [
        _time_filter(),
        _attribute_filter("final_status", "Rejected"),
    ]
    cursor_data = {
        "filters": filters,
        "page_number": 0,
        "page_size": 25,
        "cursor_mode": True,
    }

    initial_response, *_ = _call_observe_trace_list_with_bounded_page(
        bounded_page=_complete_empty_page(),
        request=_observe_trace_request({"cursor_mode": "true"}),
        validated_data=cursor_data,
    )
    assert initial_response[0] == "ok"
    assert initial_response[1]["metadata"]["total_rows"] == 0
    assert initial_response[1]["metadata"]["total_rows_exact"] == 0

    numbered_response, *_ = _call_observe_trace_list_with_bounded_page(
        bounded_page=_complete_empty_page(),
        request=_observe_trace_request(
            {"allow_sampled": "false", "cursor_mode": "false"}
        ),
        validated_data={
            "filters": filters,
            "page_number": 3,
            "page_size": 25,
            "allow_sampled": False,
            "cursor_mode": False,
        },
    )
    assert numbered_response[0] == "error"
    assert numbered_response[1][0] == 422
    assert numbered_response[2] == {"code": "cursor_required"}

    resumed_request = _observe_trace_request({"cursor_mode": "true"})
    cursor = encode_list_cursor(
        resource="observe_traces",
        scope=cursor_scope_for_request(
            resumed_request,
            project_ids=[PROJECT_ID],
        ),
        query=cursor_data,
        page_size=25,
        window_start=START.replace(tzinfo=UTC),
        window_end=END.replace(tzinfo=UTC),
        order=(END.replace(tzinfo=UTC), "trace-z"),
        seen_rows=75,
    )
    resumed_request.query_params["cursor"] = cursor
    resumed_response, *_ = _call_observe_trace_list_with_bounded_page(
        bounded_page=_complete_empty_page(),
        request=resumed_request,
        validated_data={**cursor_data, "cursor": cursor},
    )

    assert resumed_response[0] == "ok"
    resumed_metadata = resumed_response[1]["metadata"]
    assert resumed_metadata["total_rows"] == 75
    assert resumed_metadata["total_rows_exact"] == 75
    assert resumed_metadata["total_rows_is_lower_bound"] is False
    assert resumed_metadata["has_more"] is False
    assert resumed_metadata["next_cursor"] is None


@pytest.mark.parametrize(
    ("query_params", "validated_allow_sampled"),
    [
        ({"cursor_mode": "true"}, None),
        ({"cursor_mode": "true", "allow_sampled": "false"}, False),
    ],
)
def test_observe_trace_exact_cursor_chunk_is_enriched_ordered_and_continuable(
    query_params: dict[str, str],
    validated_allow_sampled: bool | None,
) -> None:
    newer = END - timedelta(minutes=1)
    older = END - timedelta(minutes=2)
    bounded_page = BoundedFilterPage(
        rows=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-newer",
                "root_span_id": "root-newer",
                "trace_name": "newer",
                "span_name": "root-newer",
                "observation_type": "llm",
                "status": "OK",
                "start_time": newer,
                "latency_ms": 12.0,
                "cost": 0.001,
            },
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-older",
                "root_span_id": "root-older",
                "trace_name": "older",
                "span_name": "root-older",
                "observation_type": "llm",
                "status": "OK",
                "start_time": older,
                "latency_ms": 14.0,
                "cost": 0.002,
            },
        ],
        # The selector found and classified one full page, but could not prove
        # whether another matching row exists inside its bounded read budget.
        has_more=True,
        complete=False,
        status="degraded",
        error_code="query_budget_exceeded",
        total_rows_lower_bound=3,
        elapsed_ms=4_500.0,
        query_count=8,
        rows_returned=400,
        result_payload_bytes=8_192,
        attempts=(),
        continuation_slice_start=START,
        continuation_slice_end=END,
    )

    class RecordingAnalytics:
        def __init__(self) -> None:
            self.calls: list[tuple[dict[str, Any], int, dict[str, Any]]] = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((params, timeout_ms, settings))
            if "content_trace_ids" in params:
                data = [
                    {
                        "trace_id": trace_id,
                        "input": f"input-{trace_id}",
                        "output": f"output-{trace_id}",
                        "attrs_string": {},
                        "attrs_number": {},
                        "attrs_bool": {},
                        "attributes_extra": "{}",
                        "metadata": "{}",
                        "trace_tags": [],
                    }
                    for trace_id in ("trace-newer", "trace-older")
                ]
            else:
                data = []
            return QueryResult(data, len(data), "clickhouse", 0.0)

    analytics = RecordingAnalytics()
    validated_data: dict[str, Any] = {
        "filters": [
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
        ],
        "page_number": 0,
        "page_size": 2,
        "cursor_mode": True,
    }
    if validated_allow_sampled is not None:
        validated_data["allow_sampled"] = validated_allow_sampled

    view_response, bounded_reader, _analytics, _request = (
        _call_observe_trace_list_with_bounded_page(
            bounded_page=bounded_page,
            request=_observe_trace_request(query_params),
            validated_data=validated_data,
            analytics=analytics,
        )
    )

    assert view_response[0] == "ok"
    payload = view_response[1]
    assert [row["trace_id"] for row in payload["table"]] == [
        "trace-newer",
        "trace-older",
    ]
    assert [row["input"] for row in payload["table"]] == [
        "input-trace-newer",
        "input-trace-older",
    ]
    assert len(payload["table"]) == 2
    assert payload["metadata"]["total_rows"] == 3
    assert payload["metadata"]["total_rows_is_lower_bound"] is True
    assert payload["metadata"]["total_rows_exact"] is None
    assert payload["metadata"]["query_complete"] is True
    assert payload["metadata"]["query_status"] == "complete"
    assert payload["metadata"]["query_error_code"] is None
    assert payload["metadata"]["has_more"] is True
    assert isinstance(payload["metadata"]["next_cursor"], str)
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is True
    assert len(analytics.calls) == 3


def test_observe_trace_cursor_publishes_safe_checkpoint_after_failed_attempt() -> None:
    bounded_page = BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=False,
        status="degraded",
        error_code="query_timeout",
        total_rows_lower_bound=0,
        elapsed_ms=30_000.0,
        query_count=2,
        rows_returned=25,
        result_payload_bytes=1_024,
        attempts=(
            FilterReadAttempt(
                kind="classify",
                slice_start=START,
                slice_end=END,
                elapsed_ms=30_000.0,
                rows_returned=0,
                result_payload_bytes=0,
                error_code="query_timeout",
            ),
        ),
        continuation_slice_start=START,
        continuation_slice_end=END,
    )

    response, bounded_reader, analytics, _request = (
        _call_observe_trace_list_with_bounded_page(
            bounded_page=bounded_page,
            request=_observe_trace_request(
                {"cursor_mode": "true", "allow_sampled": "false"}
            ),
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 0,
                "page_size": 25,
                "cursor_mode": True,
                "allow_sampled": False,
            },
        )
    )

    assert response[0] == "ok"
    payload = response[1]
    assert payload["table"] == []
    assert payload["metadata"]["query_complete"] is True
    assert payload["metadata"]["query_status"] == "complete"
    assert payload["metadata"]["query_error_code"] is None
    assert payload["metadata"]["has_more"] is True
    assert isinstance(payload["metadata"]["next_cursor"], str)
    bounded_reader.assert_called_once()
    analytics.execute_ch_query.assert_not_called()


@pytest.mark.parametrize(
    ("query_params", "validated_allow_sampled"),
    [({}, None), ({"allow_sampled": "false"}, False)],
)
def test_observe_trace_incomplete_page_remains_fail_closed_without_explicit_sample(
    query_params: dict[str, str],
    validated_allow_sampled: bool | None,
) -> None:
    validated_data: dict[str, Any] = {
        "filters": [
            _short_time_filter(),
            _attribute_filter("final_status", "Rejected"),
        ],
        "page_number": 0,
        "page_size": 25,
    }
    if validated_allow_sampled is not None:
        validated_data["allow_sampled"] = validated_allow_sampled

    response, bounded_reader, analytics, _request = (
        _call_observe_trace_list_with_bounded_page(
            bounded_page=_incomplete_empty_page("query_budget_exceeded"),
            request=_observe_trace_request(query_params),
            validated_data=validated_data,
        )
    )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert response[2] == {"code": "service_unavailable"}
    assert "DB::Exception" not in str(response)
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is False
    assert bounded_reader.call_args.kwargs.get("retry_wide_read_budget", False) is False
    analytics.execute_ch_query.assert_not_called()


def test_observe_trace_later_page_does_not_publish_an_incomplete_sample() -> None:
    response, bounded_reader, analytics, _request = (
        _call_observe_trace_list_with_bounded_page(
            bounded_page=_incomplete_empty_page("query_budget_exceeded"),
            request=_observe_trace_request({"allow_sampled": "true"}),
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page_number": 1,
                "page_size": 25,
                "allow_sampled": True,
            },
        )
    )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert response[2] == {"code": "service_unavailable"}
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is False
    analytics.execute_ch_query.assert_not_called()


def test_observe_trace_cursor_continuation_without_safe_checkpoint_fails_closed() -> (
    None
):
    from tracer.services.clickhouse.list_cursor import (
        cursor_scope_for_request,
        encode_list_cursor,
    )

    request = _observe_trace_request({"allow_sampled": "true"})
    validated_data = {
        "filters": [
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
        ],
        "page_number": 0,
        "page_size": 25,
        "cursor_mode": True,
        "allow_sampled": True,
    }
    cursor = encode_list_cursor(
        resource="observe_traces",
        scope=cursor_scope_for_request(request, project_ids=[PROJECT_ID]),
        query=validated_data,
        page_size=25,
        window_start=START.replace(tzinfo=UTC),
        window_end=END.replace(tzinfo=UTC),
        order=(END.replace(tzinfo=UTC), "trace-z"),
        seen_rows=25,
    )
    validated_data["cursor"] = cursor
    request.query_params["cursor"] = cursor

    response, bounded_reader, analytics, _request = (
        _call_observe_trace_list_with_bounded_page(
            bounded_page=_incomplete_empty_page("query_budget_exceeded"),
            request=request,
            validated_data=validated_data,
        )
    )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert response[2] == {"code": "service_unavailable"}
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is True
    analytics.execute_ch_query.assert_not_called()


def test_voice_legacy_first_page_keeps_bounded_compatibility() -> None:
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    analytics = mock.MagicMock()
    validated_data = {
        "filters": [
            _time_filter(),
            _attribute_filter("final_status", "Rechazado"),
        ],
        "page": 1,
        "page_size": 25,
    }

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_complete_empty_page(),
        ) as bounded_reader,
    ):
        response = view._list_voice_calls_clickhouse(
            SimpleNamespace(query_params={}),
            project_id=PROJECT_ID,
            validated_data=validated_data,
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert response.status_code == 200
    assert "cursor_mode" not in validated_data
    bounded_reader.assert_called_once()
    assert bounded_reader.call_args.kwargs["bounded_continuation"] is False
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is False
    analytics.execute_ch_query.assert_not_called()


def test_voice_explicit_cursor_opt_out_is_rejected_before_clickhouse() -> None:
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    analytics = mock.MagicMock()

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page"
        ) as bounded_reader,
    ):
        response = view._list_voice_calls_clickhouse(
            SimpleNamespace(query_params={"cursor_mode": "false"}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rechazado"),
                ],
                "page": 1,
                "page_size": 25,
                "cursor_mode": False,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert response[0] == "error"
    assert response[1][0] == 422
    assert response[2] == {"code": "cursor_required"}
    bounded_reader.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


def test_voice_long_cursor_unsupported_filter_fails_before_clickhouse() -> None:
    from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
        UnsupportedFilterShapeError,
    )
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    analytics = mock.MagicMock()

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch("tracer.views.trace.snapshot_cursor_supported", return_value=False),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page"
        ) as bounded_reader,
        pytest.raises(UnsupportedFilterShapeError, match="not cursor-safe"),
    ):
        view._list_voice_calls_clickhouse(
            SimpleNamespace(query_params={}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rechazado"),
                ],
                "page": 1,
                "page_size": 25,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )

    bounded_reader.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


@override_settings(CLICKHOUSE_V2={"QUERY_TYPES_DISABLED": "VOICE_CALL_LIST"})
def test_voice_list_uses_v2_builder_when_routing_is_disabled() -> None:
    from tracer.serializers.trace import TraceVoiceCallListResponseSerializer
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    analytics = mock.MagicMock()

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_complete_empty_page(),
        ) as bounded_reader,
    ):
        omitted_response = view._list_voice_calls_clickhouse(
            SimpleNamespace(query_params={}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [_time_filter()],
                "page": 71,
                "page_size": 30,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )
        explicit_false_response = view._list_voice_calls_clickhouse(
            SimpleNamespace(query_params={"allow_sampled": "false"}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [_time_filter()],
                "page": 71,
                "page_size": 30,
                "allow_sampled": False,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )
        response = view._list_voice_calls_clickhouse(
            SimpleNamespace(query_params={"allow_sampled": "true"}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [_time_filter()],
                "page": 71,
                "page_size": 30,
                "allow_sampled": True,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert omitted_response.status_code == 200
    assert omitted_response.data["count_is_lower_bound"] is True
    assert explicit_false_response.status_code == 503
    assert response.status_code == 200
    assert response.data["current_page"] == 71
    assert response.data["query_status"] == "complete"
    assert "query_error_code" not in response.data
    response_serializer = TraceVoiceCallListResponseSerializer(data=response.data)
    assert response_serializer.is_valid(), response_serializer.errors
    assert isinstance(
        bounded_reader.call_args.kwargs["builder"], VoiceCallListQueryBuilderV2
    )
    assert bounded_reader.call_args.kwargs["analytics"] is analytics
    assert bounded_reader.call_count == 3
    assert bounded_reader.call_args.kwargs["page_number"] == 70
    assert bounded_reader.call_args.kwargs["page_size"] == 30


@pytest.mark.parametrize("explicit_strict_total", [False, True])
def test_voice_cursor_freezes_snapshot_and_continues_by_root_order(
    explicit_strict_total: bool,
) -> None:
    from tracer.services.clickhouse.list_cursor import (
        cursor_scope_for_request,
        decode_list_cursor,
    )
    from tracer.views.trace import TraceView

    first_started = END - timedelta(minutes=1)
    second_started = END - timedelta(minutes=2)
    first_page = BoundedFilterPage(
        rows=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-b",
                "root_span_id": "root-b",
                "span_id": "root-b",
                "start_time": first_started,
                "end_time": first_started + timedelta(seconds=5),
            }
        ],
        has_more=True,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=2,
        elapsed_ms=10.0,
        query_count=2,
        rows_returned=2,
        result_payload_bytes=200,
        attempts=(),
    )
    terminal_page = BoundedFilterPage(
        rows=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "root_span_id": "root-a",
                "span_id": "root-a",
                "start_time": second_started,
                "end_time": second_started + timedelta(seconds=5),
            }
        ],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=1,
        elapsed_ms=8.0,
        query_count=2,
        rows_returned=1,
        result_payload_bytes=100,
        attempts=(),
    )
    view = TraceView.__new__(TraceView)
    analytics = mock.MagicMock()
    cursor_rows_by_span_id = {
        row["span_id"]: row for row in [*first_page.rows, *terminal_page.rows]
    }

    def hydrate_cursor_page(_query, params, **_kwargs):
        hydrated = []
        for span_id in params["content_span_ids"]:
            selected = cursor_rows_by_span_id[span_id]
            hydrated.append(
                {
                    "project_id": selected["project_id"],
                    "trace_id": selected["trace_id"],
                    "span_id": span_id,
                    "start_time": selected["start_time"],
                    "span_attributes": "{}",
                    "attrs_string": {},
                    "attrs_number": {},
                    "attrs_bool": {},
                    "provider": "vapi",
                }
            )
        return QueryResult(
            data=hydrated,
            row_count=len(hydrated),
            backend_used="clickhouse",
            query_time_ms=1.0,
        )

    analytics.execute_ch_query.side_effect = hydrate_cursor_page
    request_query = {"cursor_mode": "true"}
    initial_data = {
        "filters": [_time_filter()],
        "page": 1,
        "page_size": 1,
        "cursor_mode": True,
    }
    if explicit_strict_total:
        request_query["allow_sampled"] = "false"
        initial_data["allow_sampled"] = False
    request = _observe_trace_request(request_query)

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.views.trace.ObservabilityService.process_raw_logs",
            return_value={"status": "completed"},
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            side_effect=[first_page, terminal_page],
        ) as bounded_reader,
    ):
        first_response = view._list_voice_calls_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data=initial_data,
            remove_simulation_calls=False,
            analytics=analytics,
        )
        cursor = first_response.data["next_cursor"]
        decoded = decode_list_cursor(
            cursor,
            resource="voice_calls",
            scope=cursor_scope_for_request(request, project_ids=[PROJECT_ID]),
            query=initial_data,
            page_size=1,
        )
        continuation_data = {
            **initial_data,
            "cursor": cursor,
        }
        continuation_request_query = {
            **request_query,
            "cursor": cursor,
        }
        continuation_request = _observe_trace_request(continuation_request_query)
        second_response = view._list_voice_calls_clickhouse(
            continuation_request,
            project_id=PROJECT_ID,
            validated_data=continuation_data,
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert first_response.status_code == 200
    assert decoded.order == (first_started.replace(tzinfo=UTC), "trace-b")
    assert decoded.seen_rows == 1
    assert first_response.data["count"] == 2
    assert first_response.data["count_is_lower_bound"] is True
    assert second_response.status_code == 200
    assert [row["trace_id"] for row in second_response.data["results"]] == ["trace-a"]
    assert second_response.data["current_page"] == 2
    assert second_response.data["count"] == 2
    assert second_response.data["count_is_lower_bound"] is False
    assert second_response.data["next_cursor"] is None
    continuation_call = bounded_reader.call_args_list[1].kwargs
    assert continuation_call["page_number"] == 0
    assert continuation_call["cursor_start_time"] == first_started.replace(tzinfo=UTC)
    assert continuation_call["cursor_order_token"] == "trace-b"
    assert "additional_table_filters" not in continuation_call["read_settings"]


def test_voice_page_size_500_cursor_publishes_safe_exact_partial_chunk() -> None:
    from tracer.views.trace import TraceView

    started = END - timedelta(minutes=1)
    bounded_page = BoundedFilterPage(
        rows=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "root_span_id": "root-a",
                "span_id": "root-a",
                "start_time": started,
                "end_time": started + timedelta(seconds=12),
                "provider": "vapi",
            }
        ],
        has_more=False,
        complete=False,
        status="degraded",
        error_code="query_budget_exceeded",
        total_rows_lower_bound=1,
        elapsed_ms=4_500.0,
        query_count=48,
        rows_returned=501,
        result_payload_bytes=8_192,
        attempts=(),
        continuation_slice_start=START,
        continuation_slice_end=END,
        continuation_before_start_time=started,
        continuation_before_id="trace-a",
    )
    content_result = QueryResult(
        data=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "span_id": "root-a",
                "start_time": started,
                "span_attributes": "{}",
                "attrs_string": {},
                "attrs_number": {},
                "attrs_bool": {},
                "provider": "vapi",
            }
        ],
        row_count=1,
        backend_used="clickhouse",
        query_time_ms=1.0,
    )
    view = TraceView.__new__(TraceView)
    analytics = mock.MagicMock()
    analytics.execute_ch_query.return_value = content_result

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.views.trace.ObservabilityService.process_raw_logs",
            return_value={"status": "completed"},
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded_page,
        ) as bounded_reader,
    ):
        response = view._list_voice_calls_clickhouse(
            _observe_trace_request({"cursor_mode": "true"}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page": 1,
                "page_size": 500,
                "cursor_mode": True,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert response.status_code == 200
    assert [row["trace_id"] for row in response.data["results"]] == ["trace-a"]
    assert response.data["query_complete"] is True
    assert response.data["query_status"] == "complete"
    assert "query_error_code" not in response.data
    assert response.data["has_more"] is True
    assert response.data["count_is_lower_bound"] is True
    assert isinstance(response.data["next_cursor"], str)
    assert bounded_reader.call_args.kwargs["page_size"] == 500
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is True
    assert bounded_reader.call_args.kwargs["bounded_continuation"] is True
    assert bounded_reader.call_args.kwargs["carry_continuation_slice_width"] is True


def test_voice_cursor_publishes_safe_checkpoint_after_failed_attempt() -> None:
    from tracer.views.trace import TraceView

    bounded_page = BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=False,
        status="degraded",
        error_code="query_timeout",
        total_rows_lower_bound=0,
        elapsed_ms=30_000.0,
        query_count=2,
        rows_returned=25,
        result_payload_bytes=1_024,
        attempts=(
            FilterReadAttempt(
                kind="classify",
                slice_start=START,
                slice_end=END,
                elapsed_ms=30_000.0,
                rows_returned=0,
                result_payload_bytes=0,
                error_code="query_timeout",
            ),
        ),
        continuation_slice_start=START,
        continuation_slice_end=END,
    )
    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs)
    )
    analytics = mock.MagicMock()

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded_page,
        ) as bounded_reader,
    ):
        response = view._list_voice_calls_clickhouse(
            _observe_trace_request({"cursor_mode": "true", "allow_sampled": "false"}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page": 1,
                "page_size": 25,
                "cursor_mode": True,
                "allow_sampled": False,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert response.status_code == 200
    assert response.data["results"] == []
    assert response.data["query_complete"] is True
    assert response.data["query_status"] == "complete"
    assert "query_error_code" not in response.data
    assert response.data["has_more"] is True
    assert isinstance(response.data["next_cursor"], str)
    bounded_reader.assert_called_once()
    analytics.execute_ch_query.assert_not_called()


def test_voice_first_page_explicit_sample_publishes_sanitized_degradation() -> None:
    from tracer.serializers.trace import TraceVoiceCallListResponseSerializer
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    analytics = mock.MagicMock()

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_incomplete_empty_page(),
        ) as bounded_reader,
    ):
        response = view._list_voice_calls_clickhouse(
            SimpleNamespace(query_params={"allow_sampled": "true"}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page": 1,
                "page_size": 15,
                "allow_sampled": True,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert response.status_code == 200
    assert response.data["results"] == []
    assert response.data["count"] == 0
    assert response.data["count_is_lower_bound"] is True
    assert response.data["query_complete"] is False
    assert response.data["query_status"] == "degraded"
    assert response.data["query_error_code"] == "scan_budget_exceeded"
    assert "DB::Exception" not in str(response.data)
    assert "ClickHouse" not in str(response.data)
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is True
    response_serializer = TraceVoiceCallListResponseSerializer(data=response.data)
    assert response_serializer.is_valid(), response_serializer.errors
    analytics.execute_ch_query.assert_not_called()


def test_voice_first_page_explicit_sample_hydrates_only_proven_rows() -> None:
    from tracer.views.trace import TraceView

    started = END - timedelta(minutes=1)
    bounded_page = BoundedFilterPage(
        rows=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "root_span_id": "root-a",
                "span_id": "root-a",
                "start_time": started,
                "end_time": started + timedelta(seconds=12),
                "provider": "vapi",
            }
        ],
        has_more=False,
        complete=False,
        status="degraded",
        error_code="scan_budget_exceeded",
        total_rows_lower_bound=1,
        elapsed_ms=4_500.0,
        query_count=8,
        rows_returned=400,
        result_payload_bytes=8_192,
        attempts=(),
    )
    content_result = QueryResult(
        data=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "span_id": "root-a",
                "start_time": started,
                "span_attributes": '{"final_status":"Rejected"}',
                "attrs_string": {},
                "attrs_number": {},
                "attrs_bool": {},
                "provider": "vapi",
            }
        ],
        row_count=1,
        backend_used="clickhouse",
        query_time_ms=1.0,
    )
    view = TraceView.__new__(TraceView)
    analytics = mock.MagicMock()
    analytics.execute_ch_query.return_value = content_result

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.views.trace.ObservabilityService.process_raw_logs",
            return_value={"status": "completed"},
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded_page,
        ) as bounded_reader,
    ):
        response = view._list_voice_calls_clickhouse(
            SimpleNamespace(query_params={"allow_sampled": "true"}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _short_time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                ],
                "page": 1,
                "page_size": 15,
                "allow_sampled": True,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["query_complete"] is False
    assert response.data["query_status"] == "degraded"
    assert response.data["query_error_code"] == "scan_budget_exceeded"
    assert [row["trace_id"] for row in response.data["results"]] == ["trace-a"]
    assert response.data["results"][0]["final_status"] == "Rejected"
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is True
    analytics.execute_ch_query.assert_called_once()


def test_voice_page_size_500_hydrates_content_in_bounded_batches() -> None:
    from tracer.views.trace import TraceView

    page_rows = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:03d}",
            "root_span_id": f"root-{index:03d}",
            "span_id": f"root-{index:03d}",
            "start_time": END - timedelta(microseconds=index + 1),
            "end_time": END - timedelta(microseconds=index + 1),
            "provider": "vapi",
        }
        for index in range(500)
    ]
    row_by_span_id = {row["span_id"]: row for row in page_rows}
    bounded_page = BoundedFilterPage(
        rows=page_rows,
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=500,
        elapsed_ms=1_000.0,
        query_count=12,
        rows_returned=500,
        result_payload_bytes=32_000,
        attempts=(),
    )

    def hydrate_batch(_query, params, **_kwargs):
        rows = []
        for span_id in params["content_span_ids"]:
            selected = row_by_span_id[span_id]
            rows.append(
                {
                    "project_id": PROJECT_ID,
                    "trace_id": selected["trace_id"],
                    "span_id": span_id,
                    "start_time": selected["start_time"],
                    "span_attributes": "{}",
                    "attrs_string": {},
                    "attrs_number": {},
                    "attrs_bool": {},
                    "provider": "vapi",
                }
            )
        return QueryResult(
            data=rows,
            row_count=len(rows),
            backend_used="clickhouse",
            query_time_ms=10.0,
        )

    view = TraceView.__new__(TraceView)
    analytics = mock.MagicMock()
    analytics.execute_ch_query.side_effect = hydrate_batch

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.views.trace.ObservabilityService.process_raw_logs",
            return_value={"status": "completed"},
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded_page,
        ),
    ):
        response = view._list_voice_calls_clickhouse(
            SimpleNamespace(query_params={"allow_sampled": "true"}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [_time_filter()],
                "page": 1,
                "page_size": 500,
                "allow_sampled": True,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert response.status_code == 200
    assert len(response.data["results"]) == 500
    assert response.data["count"] == 500
    assert analytics.execute_ch_query.call_count == 3
    assert [
        len(call.args[1]["content_root_identities"])
        for call in analytics.execute_ch_query.call_args_list
    ] == [200, 200, 100]
    assert [
        call.kwargs["settings"]["max_result_rows"]
        for call in analytics.execute_ch_query.call_args_list
    ] == [200, 200, 100]


def _voice_hydration_rows(count: int) -> list[dict[str, Any]]:
    return [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:03d}",
            "root_span_id": f"root-{index:03d}",
            "span_id": f"root-{index:03d}",
            "start_time": END - timedelta(microseconds=index + 1),
            "end_time": END - timedelta(microseconds=index),
            "provider": "vapi",
        }
        for index in range(count)
    ]


def _run_voice_hydration_case(
    page_rows: list[dict[str, Any]],
    hydrate_side_effect: Any,
) -> tuple[Any, mock.MagicMock, mock.MagicMock]:
    from tracer.views.trace import TraceView

    bounded_page = BoundedFilterPage(
        rows=page_rows,
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=len(page_rows),
        elapsed_ms=100.0,
        query_count=2,
        rows_returned=len(page_rows),
        result_payload_bytes=512,
        attempts=(),
    )
    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    analytics = mock.MagicMock()
    analytics.execute_ch_query.side_effect = hydrate_side_effect

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.views.trace.ObservabilityService.process_raw_logs",
            return_value={"status": "completed"},
        ) as process_raw_logs,
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded_page,
        ),
    ):
        response = view._list_voice_calls_clickhouse(
            _observe_trace_request({"cursor_mode": "true"}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [_time_filter()],
                "page": 1,
                "page_size": max(25, len(page_rows)),
                "cursor_mode": True,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )
    return response, analytics, process_raw_logs


def test_voice_content_hydration_rejects_mixed_missing_root_identity() -> None:
    page_rows = _voice_hydration_rows(2)
    page_rows[1].pop("root_span_id")
    page_rows[1].pop("span_id")

    response, analytics, process_raw_logs = _run_voice_hydration_case(
        page_rows,
        AssertionError("hydration must not run for an incomplete identity"),
    )

    assert response[0] == "error"
    assert response[1][0] == 503
    analytics.execute_ch_query.assert_not_called()
    process_raw_logs.assert_not_called()


def test_voice_content_hydration_recursively_splits_only_code241_exactly() -> None:
    page_rows = _voice_hydration_rows(6)
    row_by_span_id = {row["span_id"]: row for row in page_rows}
    attempted_batch_sizes = []

    def hydrate(_query, params, **kwargs):
        span_ids = list(params["content_span_ids"])
        attempted_batch_sizes.append(len(span_ids))
        assert kwargs["settings"]["max_block_size"] == 8_192
        assert "preferred_max_column_in_block_size_bytes" not in kwargs["settings"]
        if len(span_ids) > 1:
            raise ReadDeadlineExceeded("Code: 241. Memory limit exceeded")
        selected = row_by_span_id[span_ids[0]]
        return QueryResult(
            data=[
                {
                    "project_id": PROJECT_ID,
                    "trace_id": selected["trace_id"],
                    "span_id": selected["span_id"],
                    "start_time": selected["start_time"],
                    "span_attributes": f'{{"marker":"{selected["span_id"]}"}}',
                    "attrs_string": {},
                    "attrs_number": {},
                    "attrs_bool": {},
                    "provider": "vapi",
                }
            ],
            row_count=1,
            backend_used="clickhouse",
            query_time_ms=25.0,
        )

    response, analytics, process_raw_logs = _run_voice_hydration_case(
        page_rows, hydrate
    )

    assert response.status_code == 200
    assert [row["trace_id"] for row in response.data["results"]] == [
        row["trace_id"] for row in page_rows
    ]
    assert [row["marker"] for row in response.data["results"]] == [
        row["span_id"] for row in page_rows
    ]
    assert attempted_batch_sizes == [6, 3, 1, 2, 1, 1, 3, 1, 2, 1, 1]
    assert analytics.execute_ch_query.call_count == 11
    assert process_raw_logs.call_count == 6


@pytest.mark.parametrize("mismatch", ["duplicate_missing", "unrequested"])
def test_voice_content_hydration_rejects_equal_count_identity_mismatch(
    mismatch: str,
) -> None:
    page_rows = _voice_hydration_rows(2)

    def hydrate(_query, _params, **_kwargs):
        first = page_rows[0]
        second = page_rows[1]
        returned = (
            [first, first] if mismatch == "duplicate_missing" else [first, second]
        )
        if mismatch == "unrequested":
            returned = [
                first,
                {
                    **second,
                    "trace_id": "trace-unrequested",
                    "span_id": "root-unrequested",
                },
            ]
        return QueryResult(
            data=[
                {
                    "project_id": row["project_id"],
                    "trace_id": row["trace_id"],
                    "span_id": row["span_id"],
                    "start_time": row["start_time"],
                    "span_attributes": "{}",
                    "attrs_string": {},
                    "attrs_number": {},
                    "attrs_bool": {},
                    "provider": "vapi",
                }
                for row in returned
            ],
            row_count=2,
            backend_used="clickhouse",
            query_time_ms=25.0,
        )

    response, analytics, process_raw_logs = _run_voice_hydration_case(
        page_rows, hydrate
    )

    assert response[0] == "error"
    assert response[1][0] == 503
    analytics.execute_ch_query.assert_called_once()
    process_raw_logs.assert_not_called()


def test_voice_content_hydration_does_not_split_a_later_timeout() -> None:
    page_rows = _voice_hydration_rows(6)
    attempted_batch_sizes = []

    def hydrate(_query, params, **_kwargs):
        attempted_batch_sizes.append(len(params["content_span_ids"]))
        if len(attempted_batch_sizes) == 1:
            raise ReadDeadlineExceeded("Code: 241. Memory limit exceeded")
        raise ReadDeadlineExceeded("read deadline exceeded")

    response, analytics, process_raw_logs = _run_voice_hydration_case(
        page_rows, hydrate
    )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert attempted_batch_sizes == [6, 3]
    assert analytics.execute_ch_query.call_count == 2
    process_raw_logs.assert_not_called()


def test_voice_content_hydration_attempt_cap_is_atomic() -> None:
    page_rows = _voice_hydration_rows(33)
    row_by_span_id = {row["span_id"]: row for row in page_rows}

    def hydrate(_query, params, **_kwargs):
        span_ids = list(params["content_span_ids"])
        if len(span_ids) > 1:
            raise ReadDeadlineExceeded("Code: 241. Memory limit exceeded")
        selected = row_by_span_id[span_ids[0]]
        return QueryResult(
            data=[
                {
                    "project_id": PROJECT_ID,
                    "trace_id": selected["trace_id"],
                    "span_id": selected["span_id"],
                    "start_time": selected["start_time"],
                    "span_attributes": "{}",
                    "attrs_string": {},
                    "attrs_number": {},
                    "attrs_bool": {},
                    "provider": "vapi",
                }
            ],
            row_count=1,
            backend_used="clickhouse",
            query_time_ms=25.0,
        )

    response, analytics, process_raw_logs = _run_voice_hydration_case(
        page_rows, hydrate
    )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert analytics.execute_ch_query.call_count == 64
    process_raw_logs.assert_not_called()


def test_voice_content_identity_normalizes_naive_and_aware_utc() -> None:
    from tracer.views.trace import _voice_content_identity

    naive = datetime(2026, 6, 7, 12, 34, 56, 789012)
    aware = naive.replace(tzinfo=UTC)

    assert _voice_content_identity(PROJECT_ID, "trace", "span", naive) == (
        _voice_content_identity(PROJECT_ID, "trace", "span", aware)
    )


def test_voice_content_hydration_budget_failure_is_atomic_and_sanitized() -> None:
    from tracer.views.trace import TraceView

    started = END - timedelta(minutes=1)
    bounded_page = BoundedFilterPage(
        rows=[
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "root_span_id": "root-a",
                "span_id": "root-a",
                "start_time": started,
                "end_time": started + timedelta(seconds=12),
                "provider": "vapi",
            }
        ],
        has_more=True,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=2,
        elapsed_ms=1_000.0,
        query_count=4,
        rows_returned=1,
        result_payload_bytes=256,
        attempts=(),
    )
    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    analytics = mock.MagicMock()
    analytics.execute_ch_query.side_effect = ReadDeadlineExceeded(
        "Code: 241. Memory limit exceeded; secret-host.internal"
    )

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=bounded_page,
        ),
    ):
        response = view._list_voice_calls_clickhouse(
            _observe_trace_request({"cursor_mode": "true"}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [_time_filter()],
                "page": 1,
                "page_size": 25,
                "cursor_mode": True,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert response == (
        "error",
        (503, "Voice call data is temporarily unavailable. Please retry."),
        {"code": "service_unavailable"},
    )
    assert "secret-host" not in str(response)
    analytics.execute_ch_query.assert_called_once()


@pytest.mark.parametrize(
    ("query_params", "validated_allow_sampled"),
    [({}, None), ({"allow_sampled": "false"}, False)],
)
def test_voice_incomplete_page_remains_fail_closed_without_explicit_sample(
    query_params: dict[str, str],
    validated_allow_sampled: bool | None,
) -> None:
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    analytics = mock.MagicMock()
    validated_data = {
        "filters": [_time_filter()],
        "page": 1,
        "page_size": 15,
    }
    if validated_allow_sampled is not None:
        validated_data["allow_sampled"] = validated_allow_sampled

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_incomplete_empty_page(),
        ) as bounded_reader,
    ):
        response = view._list_voice_calls_clickhouse(
            SimpleNamespace(query_params=query_params),
            project_id=PROJECT_ID,
            validated_data=validated_data,
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert response[2] == {"code": "service_unavailable"}
    assert "DB::Exception" not in str(response)
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is False
    assert bounded_reader.call_args.kwargs.get("retry_wide_read_budget", False) is False
    analytics.execute_ch_query.assert_not_called()


def test_voice_later_page_does_not_publish_an_incomplete_sample() -> None:
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    view._gm = SimpleNamespace(
        custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
    )
    analytics = mock.MagicMock()

    with (
        mock.patch(
            "tracer.views.trace.get_project_eval_configs", return_value=([], [])
        ),
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_incomplete_empty_page(),
        ) as bounded_reader,
    ):
        response = view._list_voice_calls_clickhouse(
            SimpleNamespace(query_params={"allow_sampled": "true"}),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [_time_filter()],
                "page": 2,
                "page_size": 15,
                "allow_sampled": True,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert response[0] == "error"
    assert response[1][0] == 503
    assert response[2] == {"code": "service_unavailable"}
    assert bounded_reader.call_args.kwargs["include_incomplete_rows"] is False
    analytics.execute_ch_query.assert_not_called()


def test_voice_first_unsupported_numbered_page_is_typed_422_before_ch() -> None:
    from tracer.selectors.trace_filter_reads import PAGE_DEPTH_EXCEEDED_MESSAGE
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    view._gm = mock.MagicMock()
    view._gm.custom_error_response.return_value = ("error", 422)
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.trace.get_project_eval_configs") as eval_configs,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project"
        ) as annotation_labels,
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page"
        ) as bounded_reader,
    ):
        response = view._list_voice_calls_clickhouse(
            SimpleNamespace(),
            project_id=PROJECT_ID,
            validated_data={
                "filters": [_time_filter()],
                "page": 72,
                "page_size": 30,
            },
            remove_simulation_calls=False,
            analytics=analytics,
        )

    assert response == ("error", 422)
    view._gm.custom_error_response.assert_called_once_with(
        422,
        PAGE_DEPTH_EXCEEDED_MESSAGE,
        code="page_depth_exceeded",
    )
    eval_configs.assert_not_called()
    annotation_labels.assert_not_called()
    bounded_reader.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


def test_observe_trace_page_depth_is_typed_422_without_ch_enrichment() -> None:
    from tracer.selectors.trace_filter_reads import PAGE_DEPTH_EXCEEDED_MESSAGE
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    view._gm = mock.MagicMock()
    view._gm.custom_error_response.return_value = ("error", 422)
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_page_depth_exceeded_page(),
        ),
    ):
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data={
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                    _attribute_filter("tenant_tier", "enterprise"),
                ],
                "page_number": 999,
                "page_size": 25,
            },
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert response == ("error", 422)
    view._gm.custom_error_response.assert_called_once_with(
        422,
        PAGE_DEPTH_EXCEEDED_MESSAGE,
        code="page_depth_exceeded",
    )
    analytics.execute_ch_query.assert_not_called()


def test_prototype_trace_page_depth_is_typed_422_without_ch_enrichment() -> None:
    from tracer.selectors.trace_filter_reads import PAGE_DEPTH_EXCEEDED_MESSAGE
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    view._gm = mock.MagicMock()
    view._gm.custom_error_response.return_value = ("error", 422)
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    view.request = request
    analytics = mock.MagicMock()

    with (
        mock.patch("tracer.views.trace.ProjectVersion") as project_version,
        mock.patch("tracer.views.trace.CustomEvalConfig") as eval_config,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=_page_depth_exceeded_page(),
        ),
    ):
        project_version.objects.get.return_value = SimpleNamespace(
            project_id=PROJECT_ID
        )
        eval_config.objects.filter.return_value.select_related.return_value = []
        response = view._list_traces_clickhouse(
            request,
            "00000000-0000-4000-8000-000000000099",
            analytics,
            {
                "filters": [
                    _time_filter(),
                    _attribute_filter("final_status", "Rejected"),
                    _attribute_filter("tenant_tier", "enterprise"),
                ],
                "sort_params": [],
                "page_number": 999,
                "page_size": 25,
            },
        )

    assert response == ("error", 422)
    view._gm.custom_error_response.assert_called_once_with(
        422,
        PAGE_DEPTH_EXCEEDED_MESSAGE,
        code="page_depth_exceeded",
    )
    analytics.execute_ch_query.assert_not_called()


@pytest.mark.parametrize("prototype", [False, True])
def test_span_deep_filtered_page_preflight_returns_422_before_ch(
    prototype: bool,
) -> None:
    from tracer.selectors.trace_filter_reads import PAGE_DEPTH_EXCEEDED_MESSAGE
    from tracer.views.observation_span import ObservationSpanView

    view = ObservationSpanView.__new__(ObservationSpanView)
    view._gm = mock.MagicMock()
    view._gm.custom_error_response.return_value = ("error", 422)
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    analytics = mock.MagicMock()
    validated_data = {
        "filters": [
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
            _attribute_filter("tenant_tier", "enterprise"),
        ],
        "page_number": 999,
        "page_size": 25,
    }

    if prototype:
        response = view._list_spans_non_observe_clickhouse(
            request,
            "00000000-0000-4000-8000-000000000099",
            SimpleNamespace(project_id=PROJECT_ID),
            analytics,
            validated_data,
        )
    else:
        response = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data=validated_data,
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert response == ("error", 422)
    view._gm.custom_error_response.assert_called_once_with(
        422,
        PAGE_DEPTH_EXCEEDED_MESSAGE,
        code="page_depth_exceeded",
    )
    analytics.execute_ch_query.assert_not_called()
    analytics.get_eval_config_ids_with_data_ch.assert_not_called()


def test_session_deep_filtered_page_preflight_returns_422_before_ch() -> None:
    from tracer.selectors.trace_filter_reads import PAGE_DEPTH_EXCEEDED_MESSAGE
    from tracer.views.trace_session import TraceSessionView

    view = TraceSessionView.__new__(TraceSessionView)
    view._gm = mock.MagicMock()
    view._gm.custom_error_response.return_value = ("error", 422)
    request = SimpleNamespace(
        validated_query_data={
            "project_id": PROJECT_ID,
            "filters": [
                _time_filter(),
                _attribute_filter("final_status", "Rejected"),
                _attribute_filter("tenant_tier", "enterprise"),
            ],
            "sort_params": [],
            "page_number": 159,
            "page_size": 30,
        }
    )

    with mock.patch(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService"
    ) as analytics_cls:
        response = TraceSessionView.list_sessions.__wrapped__(view, request)

    assert response == ("error", 422)
    view._gm.custom_error_response.assert_called_once_with(
        422,
        PAGE_DEPTH_EXCEEDED_MESSAGE,
        code="page_depth_exceeded",
    )
    analytics_cls.assert_not_called()


@pytest.mark.parametrize("prototype", [False, True])
def test_trace_deep_unfiltered_page_preflight_returns_422_before_ch(
    prototype: bool,
) -> None:
    from tracer.selectors.trace_filter_reads import PAGE_DEPTH_EXCEEDED_MESSAGE
    from tracer.views.trace import TraceView

    view = TraceView.__new__(TraceView)
    view._gm = mock.MagicMock()
    view._gm.custom_error_response.return_value = ("error", 422)
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    view.request = request
    analytics = mock.MagicMock()
    validated_data = {
        "filters": [],
        "sort_params": [],
        "page_number": 9,
        "page_size": 500,
    }

    if prototype:
        response = view._list_traces_clickhouse(
            request,
            "00000000-0000-4000-8000-000000000099",
            analytics,
            validated_data,
        )
    else:
        response = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data=validated_data,
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert response == ("error", 422)
    view._gm.custom_error_response.assert_called_once_with(
        422,
        PAGE_DEPTH_EXCEEDED_MESSAGE,
        code="page_depth_exceeded",
    )
    analytics.execute_ch_query.assert_not_called()


@pytest.mark.parametrize("prototype", [False, True])
def test_span_deep_unfiltered_page_preflight_returns_422_before_ch(
    prototype: bool,
) -> None:
    from tracer.selectors.trace_filter_reads import PAGE_DEPTH_EXCEEDED_MESSAGE
    from tracer.views.observation_span import ObservationSpanView

    view = ObservationSpanView.__new__(ObservationSpanView)
    view._gm = mock.MagicMock()
    view._gm.custom_error_response.return_value = ("error", 422)
    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    analytics = mock.MagicMock()
    validated_data = {
        "filters": [],
        "page_number": 9,
        "page_size": 500,
    }

    if prototype:
        response = view._list_spans_non_observe_clickhouse(
            request,
            "00000000-0000-4000-8000-000000000099",
            SimpleNamespace(project_id=PROJECT_ID),
            analytics,
            validated_data,
        )
    else:
        response = view._list_spans_clickhouse(
            request,
            project_id=PROJECT_ID,
            validated_data=validated_data,
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert response == ("error", 422)
    view._gm.custom_error_response.assert_called_once_with(
        422,
        PAGE_DEPTH_EXCEEDED_MESSAGE,
        code="page_depth_exceeded",
    )
    analytics.execute_ch_query.assert_not_called()


def test_session_deep_unfiltered_page_preflight_returns_422_before_ch() -> None:
    from tracer.selectors.trace_filter_reads import PAGE_DEPTH_EXCEEDED_MESSAGE
    from tracer.views.trace_session import TraceSessionView

    view = TraceSessionView.__new__(TraceSessionView)
    view._gm = mock.MagicMock()
    view._gm.custom_error_response.return_value = ("error", 422)
    request = SimpleNamespace(
        validated_query_data={
            "project_id": PROJECT_ID,
            "filters": [],
            "sort_params": [],
            "page_number": 9,
            "page_size": 500,
        }
    )

    with mock.patch(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService"
    ) as analytics_cls:
        response = TraceSessionView.list_sessions.__wrapped__(view, request)

    assert response == ("error", 422)
    view._gm.custom_error_response.assert_called_once_with(
        422,
        PAGE_DEPTH_EXCEEDED_MESSAGE,
        code="page_depth_exceeded",
    )
    analytics_cls.assert_not_called()


def test_safe_legacy_upper_only_multifilter_can_prove_exact_empty_without_ch() -> None:
    builder = _FakeBuilder([], start=END, end=START)
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
            _attribute_filter("tenant_tier", "enterprise"),
        ],
        key_field="id",
        page_number=0,
        page_size=25,
    )

    assert page.complete is True
    assert page.rows == []
    assert page.total_rows_lower_bound == 0
    assert page.query_count == 0
    assert executor.calls == []


def _rows(*minute_offsets: int) -> list[dict[str, Any]]:
    return [
        {"id": f"span-{index}", "start_time": END - timedelta(minutes=offset)}
        for index, offset in enumerate(minute_offsets)
    ]


def test_classifier_read_setting_caps_only_classifier_statements() -> None:
    rows = _rows(1, 2)
    builder = _ClassifierSettingsFakeBuilder(rows)
    executor = _ClassifierSettingsFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=1,
        deadline_ms=5_000,
        max_seed_attempts=1,
        max_candidates=2,
        max_query_count=2,
        read_settings={"max_block_size": 4_096},
    )

    assert [row["id"] for row in page.rows] == ["span-0"]
    assert [query for query, _ in executor.settings_by_query] == ["seed", "match"]
    assert executor.settings_by_query[0][1]["max_block_size"] == 4_096
    assert (
        "preferred_max_column_in_block_size_bytes"
        not in executor.settings_by_query[0][1]
    )
    assert executor.settings_by_query[1][1]["max_block_size"] == 2_048
    assert (
        executor.settings_by_query[1][1]["preferred_max_column_in_block_size_bytes"]
        == 1_048_576
    )


def test_builder_query_count_recommendation_preserves_sparse_exact_fallback() -> None:
    rows = _rows(*range(1, 61))
    builder = _RecommendedQueryCountFakeBuilder(
        rows,
        match_rows=[rows[-1]],
        recommended_batch_size=1,
        recommended_seed_batch_size=60,
    )
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.rows == [rows[-1]]
    assert page.query_count > 48
    assert page.query_count <= 128


def test_graph_only_incomplete_rows_do_not_change_exact_list_default() -> None:
    rows = _rows(1, 2, 3)
    builder = _FakeBuilder(rows, seed_proves_order=False)
    common = {
        "builder": builder,
        "filters": [_time_filter()],
        "key_field": "id",
        "page_number": 0,
        "page_size": 1,
        "deadline_ms": 5_000,
        "max_seed_attempts": 1,
        "max_candidates": 2,
        "max_query_count": 2,
        "classify_batch_size": 2,
    }

    exact_page = read_bounded_filter_page(
        analytics=_FakeExecutor(builder),
        **common,
    )
    graph_page = read_bounded_filter_page(
        analytics=_FakeExecutor(builder),
        include_incomplete_rows=True,
        **common,
    )

    assert exact_page.complete is False
    assert exact_page.rows == []
    assert graph_page.complete is False
    assert [row["id"] for row in graph_page.rows] == ["span-0"]
    assert graph_page.has_more is True

    with pytest.raises(ValueError, match="only for page zero"):
        read_bounded_filter_page(
            analytics=_FakeExecutor(builder),
            **{**common, "page_number": 1, "include_incomplete_rows": True},
        )


def test_year_cursor_keeps_one_hour_seed_skips_zero_probe_and_resumes_exactly() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(minutes=5 * (index + 1)),
        }
        for index in range(6)
    ]
    first_builder = _CursorZeroProbeWideInitialFakeBuilder(
        rows,
        start=END - timedelta(days=365),
        end=END,
        match_rows=[],
        recommended_batch_size=2,
        recommended_seed_batch_size=2,
    )

    class WidthBoundExecutor(_FakeExecutor):
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            assert query != "zero_probe"
            if query == "seed" and params["slice_end"] - params[
                "slice_start"
            ] > timedelta(hours=1):
                self.calls.append((query, params))
                raise ReadDeadlineExceeded("Code: 307. Memory limit exceeded")
            return super().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )

    first_executor = WidthBoundExecutor(first_builder)
    first = read_bounded_filter_page(
        builder=first_builder,
        analytics=first_executor,
        filters=[_time_filter(END - timedelta(days=365), END)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=8_000,
        max_seed_attempts=1,
        max_candidates=2,
        max_query_count=50,
        classify_batch_size=2,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    first_seed_params = first_executor.calls[0][1]
    assert [query for query, _ in first_executor.calls] == ["seed", "match"]
    assert first_seed_params["slice_start"] == END - timedelta(hours=1)
    assert first_seed_params["slice_end"] == END
    assert first.rows == []
    assert first.complete is False
    assert first.continuation_slice_start == END - timedelta(hours=1)
    assert first.continuation_slice_end == END
    assert first.continuation_before_start_time == rows[1]["start_time"]
    assert first.continuation_before_id == rows[1]["id"]

    second_builder = _CursorZeroProbeWideInitialFakeBuilder(
        rows,
        start=END - timedelta(days=365),
        end=END,
        recommended_batch_size=2,
        recommended_seed_batch_size=2,
    )
    second_executor = WidthBoundExecutor(second_builder)
    second = read_bounded_filter_page(
        builder=second_builder,
        analytics=second_executor,
        filters=[_time_filter(END - timedelta(days=365), END)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=8_000,
        max_seed_attempts=1,
        max_candidates=2,
        max_query_count=50,
        classify_batch_size=2,
        include_incomplete_rows=True,
        bounded_continuation=True,
        continuation_slice_start=first.continuation_slice_start,
        continuation_slice_end=first.continuation_slice_end,
        continuation_before_start_time=first.continuation_before_start_time,
        continuation_before_id=first.continuation_before_id,
    )

    assert second_executor.calls, second
    second_seed_params = second_executor.calls[0][1]
    assert second_seed_params["slice_start"] == END - timedelta(hours=1)
    assert second_seed_params["slice_end"] == END
    assert second_seed_params["before_start_time"] == rows[1]["start_time"]
    assert second_seed_params["before_id"] == rows[1]["id"]
    assert [row["id"] for row in second.rows] == [rows[2]["id"], rows[3]["id"]]
    first_candidate_ids = {
        candidate_id
        for query, params in first_executor.calls
        if query == "match"
        for candidate_id in params["candidate_ids"]
    }
    second_candidate_ids = {
        candidate_id
        for query, params in second_executor.calls
        if query == "match"
        for candidate_id in params["candidate_ids"]
    }
    assert first_candidate_ids == {rows[0]["id"], rows[1]["id"]}
    assert second_candidate_ids == {rows[2]["id"], rows[3]["id"]}
    assert first_candidate_ids.isdisjoint(second_candidate_ids)


def test_year_cursor_empty_checkpoint_advances_page_n_without_repeating_slice() -> None:
    class WidthBoundExecutor(_FakeExecutor):
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            assert query != "zero_probe"
            if query == "seed" and params["slice_end"] - params[
                "slice_start"
            ] > timedelta(days=366):
                self.calls.append((query, params))
                raise ReadDeadlineExceeded("Code: 307. Memory limit exceeded")
            return super().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )

    rows = [
        {
            "id": "older-trace",
            "root_span_id": "older-root",
            "start_time": END - timedelta(minutes=90),
        }
    ]
    first_builder = _CursorZeroProbeWideInitialFakeBuilder(
        rows,
        start=END - timedelta(days=365),
        end=END,
        match_rows=[],
        recommended_batch_size=2,
        recommended_seed_batch_size=2,
    )
    first_executor = WidthBoundExecutor(first_builder)
    first = read_bounded_filter_page(
        builder=first_builder,
        analytics=first_executor,
        filters=[_time_filter(END - timedelta(days=365), END)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=8_000,
        max_seed_attempts=1,
        max_candidates=2,
        max_query_count=50,
        classify_batch_size=2,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    first_seed_params = first_executor.calls[0][1]
    assert [query for query, _ in first_executor.calls] == ["seed"]
    assert first_seed_params["slice_start"] == END - timedelta(hours=1)
    assert first_seed_params["slice_end"] == END
    assert first.rows == []
    assert first.complete is False
    assert first.continuation_slice_start is None
    assert first.continuation_slice_end == END - timedelta(hours=1)
    assert first.continuation_before_start_time is None
    assert first.continuation_before_id is None

    second_builder = _CursorZeroProbeWideInitialFakeBuilder(
        rows,
        start=END - timedelta(days=365),
        end=END,
        match_rows=[],
        recommended_batch_size=2,
        recommended_seed_batch_size=2,
    )
    second_executor = WidthBoundExecutor(second_builder)
    second = read_bounded_filter_page(
        builder=second_builder,
        analytics=second_executor,
        filters=[_time_filter(END - timedelta(days=365), END)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=8_000,
        max_seed_attempts=1,
        max_candidates=2,
        max_query_count=50,
        classify_batch_size=2,
        include_incomplete_rows=True,
        bounded_continuation=True,
        continuation_slice_end=first.continuation_slice_end,
    )

    second_seed_params = second_executor.calls[0][1]
    assert second_seed_params["slice_start"] == END - timedelta(hours=2)
    assert second_seed_params["slice_end"] == END - timedelta(hours=1)
    assert second.complete is False
    assert second.rows == []
    assert second.continuation_slice_start is None
    assert second.continuation_slice_end == END - timedelta(hours=2)


def test_empty_cursor_carries_adaptive_slice_growth_across_requests() -> None:
    """Sparse exact scans must not restart at one hour on every HTTP page."""

    request_start = END - timedelta(days=365)
    first_builder = _CursorZeroProbeWideInitialFakeBuilder(
        [],
        start=request_start,
        end=END,
        match_rows=[],
        recommended_batch_size=2,
        recommended_seed_batch_size=2,
    )
    first_executor = _FakeExecutor(first_builder)
    first = read_bounded_filter_page(
        builder=first_builder,
        analytics=first_executor,
        filters=[_time_filter(request_start, END)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=8_000,
        max_seed_attempts=1,
        max_candidates=2,
        max_query_count=50,
        classify_batch_size=2,
        include_incomplete_rows=True,
        bounded_continuation=True,
        carry_continuation_slice_width=True,
    )

    assert first_executor.calls[0][1]["slice_start"] == END - timedelta(hours=1)
    assert first_executor.calls[0][1]["slice_end"] == END
    assert first.continuation_slice_start == END - timedelta(hours=3)
    assert first.continuation_slice_end == END - timedelta(hours=1)
    assert first.continuation_before_start_time is None

    second_builder = _CursorZeroProbeWideInitialFakeBuilder(
        [],
        start=request_start,
        end=END,
        match_rows=[],
        recommended_batch_size=2,
        recommended_seed_batch_size=2,
    )
    second_executor = _FakeExecutor(second_builder)
    second = read_bounded_filter_page(
        builder=second_builder,
        analytics=second_executor,
        filters=[_time_filter(request_start, END)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=8_000,
        max_seed_attempts=1,
        max_candidates=2,
        max_query_count=50,
        classify_batch_size=2,
        include_incomplete_rows=True,
        bounded_continuation=True,
        carry_continuation_slice_width=True,
        continuation_slice_start=first.continuation_slice_start,
        continuation_slice_end=first.continuation_slice_end,
    )

    assert second_executor.calls[0][1]["slice_start"] == END - timedelta(hours=3)
    assert second_executor.calls[0][1]["slice_end"] == END - timedelta(hours=1)
    assert second.continuation_slice_start == END - timedelta(hours=7)
    assert second.continuation_slice_end == END - timedelta(hours=3)
    assert second.continuation_before_start_time is None


def test_cursor_seed_uses_page_sentinel_and_ten_candidate_classify_batches() -> None:
    rows = [
        {
            "id": f"trace-{index:02d}",
            "root_span_id": f"root-{index:02d}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(30)
    ]
    builder = _CursorZeroProbeWideInitialFakeBuilder(
        rows,
        start=END - timedelta(days=365),
        end=END,
        match_rows=[],
        recommended_batch_size=10,
        recommended_seed_batch_size=200,
    )

    class WidthBoundExecutor(_FakeExecutor):
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            assert query != "zero_probe"
            if query == "seed" and params["slice_end"] - params[
                "slice_start"
            ] > timedelta(hours=1):
                self.calls.append((query, params))
                raise ReadDeadlineExceeded("Code: 307. Memory limit exceeded")
            return super().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )

    executor = WidthBoundExecutor(builder)
    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(END - timedelta(days=365), END)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=8_000,
        max_seed_attempts=1,
        max_candidates=200,
        max_query_count=24,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert executor.calls[0][0] == "seed"
    assert executor.calls[0][1]["limit"] == 26
    classify_batches = [
        params["candidate_ids"] for query, params in executor.calls if query == "match"
    ]
    assert [len(batch) for batch in classify_batches] == [10, 10, 6]
    assert page.complete is False
    assert page.continuation_slice_start == END - timedelta(hours=1)
    assert page.continuation_slice_end == END
    assert page.continuation_before_start_time == rows[25]["start_time"]
    assert page.continuation_before_id == rows[25]["id"]


def test_year_empty_cursor_chain_is_gap_free_and_terminates() -> None:
    request_start = END - timedelta(days=365)
    cursor_start_time = None
    cursor_order_token = None
    continuation_slice_start = None
    continuation_slice_end = None
    continuation_before_start_time = None
    continuation_before_id = None
    seed_intervals: list[tuple[datetime, datetime]] = []
    pages: list[BoundedFilterPage] = []

    for _ in range(12):
        builder = _CursorZeroProbeWideInitialFakeBuilder(
            [],
            start=request_start,
            end=END,
            recommended_batch_size=2,
            recommended_seed_batch_size=2,
        )
        executor = _FakeExecutor(builder)
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter(request_start, END)],
            key_field="id",
            page_number=0,
            page_size=2,
            deadline_ms=8_000,
            max_seed_attempts=24,
            max_candidates=2,
            max_query_count=50,
            classify_batch_size=2,
            include_incomplete_rows=True,
            bounded_continuation=True,
            cursor_start_time=cursor_start_time,
            cursor_order_token=cursor_order_token,
            continuation_slice_start=continuation_slice_start,
            continuation_slice_end=continuation_slice_end,
            continuation_before_start_time=continuation_before_start_time,
            continuation_before_id=continuation_before_id,
        )
        pages.append(page)
        assert all(query != "zero_probe" for query, _params in executor.calls)
        seed_intervals.extend(
            (params["slice_start"], params["slice_end"])
            for query, params in executor.calls
            if query == "seed"
        )

        if page.complete:
            break

        assert page.rows == []
        assert page.has_more is False
        assert page.continuation_slice_end is not None
        if cursor_start_time is None:
            # Empty transport chunks retain one public order boundary while
            # the private scan checkpoint advances beneath it.
            cursor_start_time = (
                page.continuation_before_start_time or page.continuation_slice_end
            )
            cursor_order_token = page.continuation_before_id or "\U0010ffff"
        continuation_slice_start = page.continuation_slice_start
        continuation_slice_end = page.continuation_slice_end
        continuation_before_start_time = page.continuation_before_start_time
        continuation_before_id = page.continuation_before_id
    else:
        pytest.fail("year-long empty cursor chain did not terminate")

    assert len(pages) == 10
    assert all(not page.complete for page in pages[:-1])
    assert pages[-1].complete is True
    assert pages[-1].rows == []
    assert pages[-1].has_more is False
    assert pages[-1].continuation_slice_start is None
    assert pages[-1].continuation_slice_end is None
    assert pages[-1].continuation_before_start_time is None
    assert pages[-1].continuation_before_id is None
    assert seed_intervals[0][1] == END
    assert seed_intervals[-1][0] == request_start
    assert all(
        newer_start == older_end
        for (newer_start, _newer_end), (_older_start, older_end) in zip(
            seed_intervals,
            seed_intervals[1:],
            strict=False,
        )
    )


def test_cursor_chain_reaches_later_matches_without_duplicate_or_skip() -> None:
    request_start = END - timedelta(days=120)
    match_time = END - timedelta(days=50)
    rows = [
        {
            "id": row_id,
            "root_span_id": f"root-{row_id}",
            "start_time": match_time - timedelta(microseconds=index),
        }
        for index, row_id in enumerate(("trace-c", "trace-b", "trace-a"))
    ]
    cursor_start_time = None
    cursor_order_token = None
    continuation_slice_start = None
    continuation_slice_end = None
    continuation_before_start_time = None
    continuation_before_id = None
    pages: list[BoundedFilterPage] = []
    published_ids: list[str] = []

    for _ in range(8):
        builder = _CursorZeroProbeWideInitialFakeBuilder(
            rows,
            start=request_start,
            end=END,
            recommended_batch_size=2,
            recommended_seed_batch_size=2,
        )
        executor = _FakeExecutor(builder)
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter(request_start, END)],
            key_field="id",
            page_number=0,
            page_size=2,
            deadline_ms=8_000,
            max_seed_attempts=24,
            max_candidates=3,
            max_query_count=50,
            classify_batch_size=3,
            include_incomplete_rows=True,
            bounded_continuation=True,
            cursor_start_time=cursor_start_time,
            cursor_order_token=cursor_order_token,
            continuation_slice_start=continuation_slice_start,
            continuation_slice_end=continuation_slice_end,
            continuation_before_start_time=continuation_before_start_time,
            continuation_before_id=continuation_before_id,
        )
        pages.append(page)
        assert all(query != "zero_probe" for query, _params in executor.calls)
        published_ids.extend(row["id"] for row in page.rows)

        if page.complete and not page.has_more:
            break

        if page.rows:
            cursor_start_time = page.rows[-1]["start_time"]
            cursor_order_token = page.rows[-1]["id"]
        elif cursor_start_time is None:
            cursor_start_time = (
                page.continuation_before_start_time or page.continuation_slice_end
            )
            cursor_order_token = page.continuation_before_id or "\U0010ffff"

        if page.has_more:
            continuation_slice_start = None
            continuation_slice_end = None
            continuation_before_start_time = None
            continuation_before_id = None
        else:
            assert page.continuation_slice_end is not None
            continuation_slice_start = page.continuation_slice_start
            continuation_slice_end = page.continuation_slice_end
            continuation_before_start_time = page.continuation_before_start_time
            continuation_before_id = page.continuation_before_id
    else:
        pytest.fail("later-match cursor chain did not terminate")

    assert len(pages) == 4
    assert pages[0].rows == []
    assert pages[0].complete is False
    assert [row["id"] for row in pages[1].rows] == ["trace-c", "trace-b"]
    assert pages[1].complete is True
    assert pages[1].has_more is True
    assert [row["id"] for row in pages[2].rows] == ["trace-a"]
    assert pages[2].complete is False
    assert pages[2].continuation_slice_end is not None
    assert pages[3].rows == []
    assert pages[3].complete is True
    assert pages[3].has_more is False
    assert published_ids == ["trace-c", "trace-b", "trace-a"]
    assert len(published_ids) == len(set(published_ids))


def test_bounded_continuation_resumes_after_last_fully_classified_seed_page() -> None:
    rows = _rows(1, 2, 3, 4, 5)
    builder = _FakeBuilder(
        rows,
        recommended_batch_size=2,
        recommended_seed_batch_size=2,
    )

    class FailOnThirdStatement(_FakeExecutor):
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if len(self.calls) == 2:
                self.calls.append((query, params))
                raise ReadDeadlineExceeded("Code: 159. Timeout exceeded")
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )

    first = read_bounded_filter_page(
        builder=builder,
        analytics=FailOnThirdStatement(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        max_seed_attempts=3,
        max_candidates=2,
        max_query_count=6,
        classify_batch_size=2,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert first.complete is False
    assert [row["id"] for row in first.rows] == ["span-0", "span-1"]
    assert first.has_more is False
    assert first.continuation_slice_start is not None
    assert first.continuation_slice_end is not None
    assert first.continuation_before_start_time == rows[1]["start_time"]
    assert first.continuation_before_id == "span-1"

    second = read_bounded_filter_page(
        builder=builder,
        analytics=_FakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        max_seed_attempts=3,
        max_candidates=2,
        max_query_count=6,
        classify_batch_size=2,
        include_incomplete_rows=True,
        bounded_continuation=True,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=first.rows[-1]["id"],
        continuation_slice_start=first.continuation_slice_start,
        continuation_slice_end=first.continuation_slice_end,
        continuation_before_start_time=first.continuation_before_start_time,
        continuation_before_id=first.continuation_before_id,
    )

    assert [row["id"] for row in second.rows] == ["span-2", "span-3"]
    combined = [row["id"] for row in [*first.rows, *second.rows]]
    assert combined == ["span-0", "span-1", "span-2", "span-3"]
    assert len(combined) == len(set(combined))


def test_empty_bounded_continuation_reserves_exact_checkpoint_tail() -> None:
    rows = _rows(1, 2, 3, 4)
    builder = _IdentityHydrationFakeBuilder(
        rows,
        match_rows=[],
        recommended_batch_size=2,
        recommended_seed_batch_size=2,
    )
    clock = _ManualMonotonic()
    executor = _IdentityHydrationFakeExecutor(
        builder,
        clock=clock,
        # The first batch is fully classified with 500 ms left before the
        # classification deadline. That would admit a short-token statement
        # under the former 250 ms floor, but is not enough for the production
        # classifier's complete 1.5-second statement envelope.
        durations_ms={"seed": 500, "match_identity": 700},
    )

    with mock.patch("tracer.selectors.trace_filter_reads.monotonic", new=clock):
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter()],
            key_field="id",
            page_number=0,
            page_size=2,
            deadline_ms=2_000,
            max_seed_attempts=3,
            max_candidates=2,
            max_query_count=6,
            classify_batch_size=2,
            include_incomplete_rows=True,
            bounded_continuation=True,
        )

    assert page.complete is False
    assert page.rows == []
    assert page.error_code == "deadline_exceeded"
    assert page.continuation_slice_start is not None
    assert page.continuation_slice_end is not None
    assert page.continuation_before_start_time == rows[1]["start_time"]
    assert page.continuation_before_id == "span-1"
    assert [query for query, _ in executor.calls] == ["seed", "match_identity"]
    assert executor.timeouts[1][0] == "match_identity"
    assert executor.timeouts[1][1] == 1_200
    assert page.elapsed_ms <= 1_200


def test_cursor_commits_each_exact_classifier_sub_batch() -> None:
    rows = [
        {
            "id": f"trace-{index:02d}",
            "root_span_id": f"root-{index:02d}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(26)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        match_rows=[],
        recommended_batch_size=10,
        recommended_seed_batch_size=200,
    )
    clock = _ManualMonotonic()
    executor = _IdentityHydrationFakeExecutor(
        builder,
        clock=clock,
        durations_ms={"seed": 600, "match_identity": 700},
    )

    with mock.patch("tracer.selectors.trace_filter_reads.monotonic", new=clock):
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter()],
            key_field="id",
            page_number=0,
            page_size=25,
            deadline_ms=2_600,
            max_seed_attempts=3,
            max_candidates=200,
            max_query_count=12,
            include_incomplete_rows=True,
            bounded_continuation=True,
        )

    assert page.complete is False
    assert page.rows == []
    assert page.error_code == "deadline_exceeded"
    assert page.continuation_slice_start is not None
    assert page.continuation_slice_end is not None
    assert page.continuation_before_start_time == rows[9]["start_time"]
    assert page.continuation_before_id == rows[9]["id"]
    assert [query for query, _ in executor.calls] == ["seed", "match_identity"]


def test_partial_identity_cursor_page_is_hydrated_before_publication() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(minutes=index + 1),
            "trace_name": f"presented-{index}",
        }
        for index in range(5)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        recommended_batch_size=2,
        recommended_seed_batch_size=2,
    )

    class FailSecondSeed(_IdentityHydrationFakeExecutor):
        def __init__(self, page_builder):
            super().__init__(page_builder)
            self.seed_calls = 0

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if query == "seed":
                self.seed_calls += 1
                if self.seed_calls == 2:
                    self.calls.append((query, params))
                    raise ReadDeadlineExceeded("Code: 159. Timeout exceeded")
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=FailSecondSeed(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        max_seed_attempts=3,
        max_candidates=2,
        max_query_count=6,
        classify_batch_size=2,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is False
    assert [row["id"] for row in page.rows] == ["trace-0", "trace-1"]
    assert [row["trace_name"] for row in page.rows] == [
        "presented-0",
        "presented-1",
    ]
    assert page.continuation_slice_end is not None
    assert "hydrate" in [attempt.kind for attempt in page.attempts]


def test_sparse_identity_cursor_publishes_each_hydrated_classified_slice() -> None:
    request_start = END - timedelta(minutes=30)
    rows = [
        {
            "id": row_id,
            "root_span_id": f"root-{row_id}",
            "start_time": END - timedelta(minutes=minute),
            "trace_name": f"presented-{row_id}",
        }
        for row_id, minute in (
            ("newer-nonmatch", 1),
            ("match-a", 6),
            ("match-b", 7),
            ("match-c", 16),
        )
    ]
    expected_matches = rows[1:]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=request_start,
        end=END,
        match_rows=expected_matches,
        recommended_batch_size=10,
        recommended_seed_batch_size=10,
    )
    cursor_start_time = None
    cursor_order_token = None
    continuation_slice_start = None
    continuation_slice_end = None
    continuation_before_start_time = None
    continuation_before_id = None
    pages: list[BoundedFilterPage] = []
    published_ids: list[str] = []

    for _ in range(4):
        executor = _IdentityHydrationFakeExecutor(builder, reverse_hydration=True)
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter(request_start, END)],
            key_field="id",
            page_number=0,
            page_size=5,
            deadline_ms=5_000,
            max_seed_attempts=24,
            max_candidates=10,
            max_query_count=50,
            classify_batch_size=10,
            include_incomplete_rows=True,
            bounded_continuation=True,
            cursor_start_time=cursor_start_time,
            cursor_order_token=cursor_order_token,
            continuation_slice_start=continuation_slice_start,
            continuation_slice_end=continuation_slice_end,
            continuation_before_start_time=continuation_before_start_time,
            continuation_before_id=continuation_before_id,
        )
        pages.append(page)
        published_ids.extend(row["id"] for row in page.rows)

        if page.complete:
            break

        assert page.has_more is False
        assert page.continuation_slice_end is not None
        assert page.rows
        assert [attempt.kind for attempt in page.attempts][-1] == "hydrate"
        cursor_start_time = page.rows[-1]["start_time"]
        cursor_order_token = page.rows[-1]["id"]
        continuation_slice_start = page.continuation_slice_start
        continuation_slice_end = page.continuation_slice_end
        continuation_before_start_time = page.continuation_before_start_time
        continuation_before_id = page.continuation_before_id
    else:
        pytest.fail("sparse hydrated cursor chain did not terminate")

    assert len(pages) == 3
    assert [row["id"] for row in pages[0].rows] == ["match-a", "match-b"]
    assert [row["id"] for row in pages[1].rows] == ["match-c"]
    assert pages[0].continuation_slice_end == END - timedelta(minutes=15)
    assert pages[1].continuation_slice_end == END - timedelta(minutes=20)
    assert pages[2].complete is True
    assert pages[2].rows == []
    assert pages[2].continuation_slice_end is None
    assert published_ids == [row["id"] for row in expected_matches]
    assert len(published_ids) == len(set(published_ids))
    assert all(
        row["trace_name"].startswith("presented-")
        for page in pages
        for row in page.rows
    )


def test_opted_in_identity_cursor_fills_across_classified_slices() -> None:
    request_start = END - timedelta(minutes=30)
    rows = [
        {
            "id": row_id,
            "root_span_id": f"root-{row_id}",
            "start_time": END - timedelta(minutes=minute),
            "trace_name": f"presented-{row_id}",
        }
        for row_id, minute in (
            ("newer-nonmatch", 1),
            ("match-a", 6),
            ("match-b", 7),
            ("match-c", 16),
        )
    ]
    expected_matches = rows[1:]
    builder = _CursorPageFillIdentityHydrationFakeBuilder(
        rows,
        start=request_start,
        end=END,
        match_rows=expected_matches,
        recommended_batch_size=10,
        recommended_seed_batch_size=10,
    )
    executor = _IdentityHydrationFakeExecutor(builder, reverse_hydration=True)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(request_start, END)],
        key_field="id",
        page_number=0,
        page_size=5,
        deadline_ms=5_000,
        max_seed_attempts=24,
        max_candidates=10,
        max_query_count=50,
        classify_batch_size=10,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is True
    assert [row["id"] for row in page.rows] == [row["id"] for row in expected_matches]
    assert page.has_more is False
    assert page.continuation_slice_end is None
    assert [attempt.kind for attempt in page.attempts].count("hydrate") == 1


def test_generic_cursor_honors_explicit_wider_seed_recommendation() -> None:
    request_start = END - timedelta(hours=2)
    rows = [
        {
            "id": f"trace-{index:03d}",
            "root_span_id": f"root-{index:03d}",
            "start_time": END - timedelta(minutes=index + 1),
        }
        for index in range(80)
    ]
    builder = _WideGenericCursorIdentityHydrationFakeBuilder(
        rows,
        start=request_start,
        end=END,
        recommended_batch_size=10,
        recommended_seed_batch_size=10,
    )
    executor = _IdentityHydrationFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(request_start, END)],
        key_field="id",
        page_number=0,
        page_size=15,
        deadline_ms=5_000,
        max_seed_attempts=24,
        max_candidates=100,
        max_query_count=50,
        classify_batch_size=10,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is True
    assert executor.calls[0][0] == "seed"
    assert executor.calls[0][1]["limit"] == 80


def test_sparse_identity_cursor_waits_for_the_whole_active_slice() -> None:
    request_start = END - timedelta(minutes=30)
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(minutes=6, microseconds=index),
            "trace_name": f"presented-{index}",
        }
        for index in range(7)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=request_start,
        end=END,
        match_rows=rows[:1],
        recommended_batch_size=10,
        recommended_seed_batch_size=6,
    )
    executor = _IdentityHydrationFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(request_start, END)],
        key_field="id",
        page_number=0,
        page_size=5,
        deadline_ms=5_000,
        max_seed_attempts=24,
        max_candidates=10,
        max_query_count=50,
        classify_batch_size=10,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    seed_calls = [params for query, params in executor.calls if query == "seed"]
    assert len(seed_calls) == 3
    assert seed_calls[1]["before_start_time"] is None
    assert seed_calls[1]["before_id"] is None
    assert seed_calls[2]["before_start_time"] == rows[5]["start_time"]
    assert seed_calls[2]["before_id"] == rows[5]["id"]
    assert seed_calls[2]["slice_end"] == END - timedelta(minutes=5)
    assert [row["id"] for row in page.rows] == ["trace-0"]
    assert page.complete is False
    assert page.continuation_slice_start is None
    assert page.continuation_slice_end == END - timedelta(minutes=15)


@pytest.mark.parametrize(
    ("bounded_continuation", "seed_proves_order"),
    [(False, True), (True, False)],
)
def test_short_slice_prefix_does_not_change_non_cursor_or_unordered_contracts(
    bounded_continuation: bool,
    seed_proves_order: bool,
) -> None:
    request_start = END - timedelta(minutes=30)
    rows = [
        {
            "id": row_id,
            "root_span_id": f"root-{row_id}",
            "start_time": END - timedelta(minutes=minute),
            "trace_name": f"presented-{row_id}",
        }
        for row_id, minute in (("match-a", 6), ("match-b", 16))
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=request_start,
        end=END,
        seed_proves_order=seed_proves_order,
        recommended_batch_size=10,
        recommended_seed_batch_size=10,
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=_IdentityHydrationFakeExecutor(builder),
        filters=[_time_filter(request_start, END)],
        key_field="id",
        page_number=0,
        page_size=5,
        deadline_ms=5_000,
        max_seed_attempts=24,
        max_candidates=10,
        max_query_count=50,
        classify_batch_size=10,
        include_incomplete_rows=bounded_continuation,
        bounded_continuation=bounded_continuation,
    )

    assert page.complete is True
    assert [row["id"] for row in page.rows] == ["match-a", "match-b"]
    assert page.has_more is False
    assert page.continuation_slice_end is None


@pytest.mark.parametrize(
    ("failure_mode", "expected_error_code"),
    [("timeout", "read_budget_exceeded"), ("drift", "classification_drift")],
)
def test_sparse_identity_hydration_failure_rewinds_before_unpublished_match(
    failure_mode: str,
    expected_error_code: str,
) -> None:
    request_start = END - timedelta(minutes=30)
    rows = [
        {
            "id": row_id,
            "root_span_id": f"root-{row_id}",
            "start_time": END - timedelta(minutes=minute),
            "trace_name": f"presented-{row_id}",
        }
        for row_id, minute in (
            ("newer-nonmatch", 1),
            ("match-a", 6),
            ("match-b", 16),
        )
    ]
    expected_matches = rows[1:]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=request_start,
        end=END,
        match_rows=expected_matches,
        recommended_batch_size=10,
        recommended_seed_batch_size=10,
    )

    if failure_mode == "timeout":

        class FailHydration(_IdentityHydrationFakeExecutor):
            def execute_ch_query(self, query, params, *, timeout_ms, settings):
                if query == "hydrate":
                    self.calls.append((query, params))
                    raise ReadDeadlineExceeded("Code: 159. Timeout exceeded")
                return super().execute_ch_query(
                    query, params, timeout_ms=timeout_ms, settings=settings
                )

        failing_executor = FailHydration(builder)
    else:
        drifted_match = {**rows[1], "root_span_id": "replacement-root"}
        failing_executor = _IdentityHydrationFakeExecutor(
            builder,
            hydration_rows=[drifted_match],
        )

    failed = read_bounded_filter_page(
        builder=builder,
        analytics=failing_executor,
        filters=[_time_filter(request_start, END)],
        key_field="id",
        page_number=0,
        page_size=5,
        deadline_ms=5_000,
        max_seed_attempts=24,
        max_candidates=10,
        max_query_count=50,
        classify_batch_size=10,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert failed.complete is False
    assert failed.rows == []
    assert failed.has_more is False
    assert failed.error_code == expected_error_code
    # The successful scan reached the end of the matching 5-15 minute slice,
    # but its failed publication must resume at the preceding empty boundary.
    assert failed.continuation_slice_start is None
    assert failed.continuation_slice_end == END - timedelta(minutes=5)
    assert failed.continuation_before_start_time is None
    assert failed.continuation_before_id is None

    cursor_start_time = failed.continuation_slice_end
    cursor_order_token = "\U0010ffff"
    continuation_slice_end = failed.continuation_slice_end
    published_ids: list[str] = []
    for _ in range(4):
        page = read_bounded_filter_page(
            builder=builder,
            analytics=_IdentityHydrationFakeExecutor(builder),
            filters=[_time_filter(request_start, END)],
            key_field="id",
            page_number=0,
            page_size=5,
            deadline_ms=5_000,
            max_seed_attempts=24,
            max_candidates=10,
            max_query_count=50,
            classify_batch_size=10,
            include_incomplete_rows=True,
            bounded_continuation=True,
            cursor_start_time=cursor_start_time,
            cursor_order_token=cursor_order_token,
            continuation_slice_end=continuation_slice_end,
        )
        published_ids.extend(row["id"] for row in page.rows)
        if page.complete:
            break
        assert page.continuation_slice_end is not None
        cursor_start_time = page.rows[-1]["start_time"]
        cursor_order_token = page.rows[-1]["id"]
        continuation_slice_end = page.continuation_slice_end
    else:
        pytest.fail("rewound hydration cursor chain did not terminate")

    assert published_ids == [row["id"] for row in expected_matches]
    assert len(published_ids) == len(set(published_ids))


def test_graph_stratum_anchor_classifies_one_finite_sentinel_without_ordered_seed():
    rows = _rows(1, 2, 3)
    builder = _AnchorFakeBuilder(rows, seed_proves_order=False)
    executor = _AnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        max_seed_attempts=1,
        max_candidates=3,
        max_query_count=2,
        classify_batch_size=3,
        include_incomplete_rows=True,
        anchor_probe_only=True,
        anchor_probe_limit=3,
    )

    assert [row["id"] for row in page.rows] == ["span-0", "span-1"]
    assert page.complete is False
    assert page.has_more is True
    assert page.error_code == "sample_limit"
    assert [query for query, _ in executor.calls] == ["anchor", "match"]
    assert executor.calls[0][1]["limit"] == 3


def test_long_window_list_skips_full_anchor_but_keeps_graph_custom_anchor() -> None:
    rows = _rows(1, 2, 3)
    builder = _SkipFullAnchorFakeBuilder(rows, seed_proves_order=False)

    list_executor = _AnchorFakeExecutor(builder)
    list_page = read_bounded_filter_page(
        builder=builder,
        analytics=list_executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert list_page.complete is True
    assert [query for query, _ in list_executor.calls] == ["ordered_seed", "match"]

    paged_rows: list[list[str]] = []
    for page_number in (0, 1):
        page_executor = _AnchorFakeExecutor(builder)
        page = read_bounded_filter_page(
            builder=builder,
            analytics=page_executor,
            filters=[_time_filter()],
            key_field="id",
            page_number=page_number,
            page_size=1,
            deadline_ms=5_000,
        )
        paged_rows.append([row["id"] for row in page.rows])
        assert "anchor" not in [query for query, _ in page_executor.calls]

    assert paged_rows == [["span-0"], ["span-1"]]
    assert set(paged_rows[0]).isdisjoint(paged_rows[1])

    graph_executor = _AnchorFakeExecutor(builder)
    graph_page = read_bounded_filter_page(
        builder=builder,
        analytics=graph_executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        max_seed_attempts=1,
        max_candidates=3,
        max_query_count=2,
        classify_batch_size=3,
        include_incomplete_rows=True,
        anchor_probe_only=True,
        anchor_probe_limit=3,
    )

    assert graph_page.complete is False
    assert [query for query, _ in graph_executor.calls] == ["anchor", "match"]

    default_anchor_only_executor = _AnchorFakeExecutor(builder)
    read_bounded_filter_page(
        builder=builder,
        analytics=default_anchor_only_executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        include_incomplete_rows=True,
        anchor_probe_only=True,
    )
    assert [query for query, _ in default_anchor_only_executor.calls] == [
        "anchor",
        "match",
    ]


def test_lower_anchor_limit_is_opt_in_and_keeps_list_sentinel_unchanged() -> None:
    rows = _rows(1, 2)
    builder = _AnchorFakeBuilder(rows, seed_proves_order=False)
    executor = _AnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert [row["id"] for row in page.rows] == ["span-0", "span-1"]
    assert executor.calls[0] == ("anchor", {"limit": 513})

    with pytest.raises(ValueError, match="requires anchor_probe_only"):
        read_bounded_filter_page(
            builder=builder,
            analytics=_AnchorFakeExecutor(builder),
            filters=[_time_filter()],
            key_field="id",
            page_number=0,
            page_size=2,
            anchor_probe_limit=3,
        )


def test_long_window_sparse_indexed_anchor_is_exact_under_small_timeout() -> None:
    rows = _rows(1, 2, 3)
    builder = _SmallAnchorFakeBuilder(rows, seed_proves_order=False)
    executor = _TimedAnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert [row["id"] for row in page.rows] == ["span-0", "span-1"]
    assert [query for query, _ in executor.calls] == [
        "anchor",
        "anchor",
        "anchor",
        "anchor",
        "match",
    ]
    assert executor.calls[0][1]["limit"] == 64
    anchor_calls = [params for query, params in executor.calls if query == "anchor"]
    assert [params["limit"] for params in anchor_calls] == [64, 61, 61, 61]
    assert anchor_calls[0]["slice_end"] == END
    assert anchor_calls[-1]["slice_start"] == START
    assert all(
        newer["slice_start"] == older["slice_end"]
        for newer, older in zip(anchor_calls, anchor_calls[1:], strict=False)
    )
    assert all(
        0 < timeout <= 300 for query, timeout in executor.timeouts if query == "anchor"
    )
    assert all(
        settings["max_bytes_to_read"] == 96 * 1024 * 1024
        for query, settings in executor.settings
        if query == "anchor"
    )


def test_initial_cursor_sparse_anchor_closes_exact_page_without_scan_cursor() -> None:
    rows = _rows(1, 2)
    builder = _InitialCursorAnchorFakeBuilder(rows)
    executor = _TimedAnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=2_200,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is True
    assert page.status == "complete"
    assert page.error_code is None
    assert [row["id"] for row in page.rows] == ["span-0", "span-1"]
    assert page.has_more is False
    assert page.continuation_slice_end is None
    assert [query for query, _ in executor.calls] == ["anchor", "match"]
    assert executor.timeouts[0] == ("anchor", 300)
    assert executor.settings[0][1]["max_bytes_to_read"] == 96 * 1024 * 1024
    assert executor.settings[0][1]["max_threads"] == 1


def test_initial_cursor_sparse_anchor_deduplicates_raw_versions_before_classify() -> (
    None
):
    latest = {"id": "span-0", "start_time": END - timedelta(minutes=1)}
    raw_versions = [dict(latest), dict(latest)]
    builder = _InitialCursorAnchorFakeBuilder(raw_versions, match_rows=[latest])
    executor = _TimedAnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=2_200,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is True
    assert page.rows == [latest]
    assert executor.calls == [
        ("anchor", {"limit": 3}),
        ("match", {"candidate_ids": ("span-0",)}),
    ]


def test_initial_cursor_empty_anchor_proves_exact_empty_window() -> None:
    builder = _InitialCursorAnchorFakeBuilder([])
    executor = _TimedAnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=2_200,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is True
    assert page.rows == []
    assert page.total_rows_lower_bound == 0
    assert page.has_more is False
    assert page.continuation_slice_end is None
    assert [query for query, _ in executor.calls] == ["anchor"]


def test_initial_cursor_common_anchor_falls_back_to_narrow_exact_seed() -> None:
    rows = _rows(1, 2, 3)
    builder = _InitialCursorAnchorFakeBuilder(rows)
    executor = _TimedAnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=2_200,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is True
    assert page.error_code is None
    assert [row["id"] for row in page.rows] == ["span-0", "span-1"]
    assert page.has_more is True
    assert [query for query, _ in executor.calls] == [
        "anchor",
        "ordered_seed",
        "match",
    ]


def test_resumed_cursor_never_repeats_full_window_sparse_anchor() -> None:
    builder = _InitialCursorAnchorFakeBuilder([])
    executor = _TimedAnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=2_200,
        max_seed_attempts=1,
        include_incomplete_rows=True,
        continuation_slice_end=END - timedelta(days=31),
        bounded_continuation=True,
    )

    assert page.complete is False
    assert page.error_code == "scan_budget_exceeded"
    assert page.continuation_slice_end is not None
    assert [query for query, _ in executor.calls] == ["ordered_seed"]


def test_initial_cursor_sparse_anchor_timeout_preserves_exact_fallback() -> None:
    rows = _rows(1, 2, 3)
    builder = _InitialCursorAnchorFakeBuilder(rows)
    executor = _TimedAnchorFakeExecutor(builder, fail_anchor=True)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=2_200,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is False
    assert page.error_code == "read_budget_exceeded"
    assert [row["id"] for row in page.rows] == ["span-0", "span-1"]
    assert [query for query, _ in executor.calls] == [
        "anchor",
        "ordered_seed",
        "match",
    ]
    assert page.attempts[0].error_code == "read_budget_exceeded"


def test_saturated_initial_anchor_preserves_full_exact_fallback_query_budget() -> None:
    first = {"id": "span-new", "start_time": END - timedelta(minutes=1)}
    match = {"id": "span-match", "start_time": END - timedelta(minutes=7)}
    old = {"id": "span-old", "start_time": END - timedelta(days=30)}
    builder = _InitialCursorBudgetAnchorFakeBuilder(
        [first, match, old],
        match_rows=[match],
    )
    executor = _TimedAnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=1,
        deadline_ms=5_000,
        max_seed_attempts=2,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.error_code == "scan_budget_exceeded"
    assert page.continuation_slice_end is not None
    assert [row["id"] for row in page.rows] == ["span-match"]
    assert [query for query, _ in executor.calls] == [
        "anchor",
        "ordered_seed",
        "match",
        "ordered_seed",
        "match",
    ]


def test_long_window_common_indexed_anchor_falls_back_to_ordered_roots() -> None:
    rows = _rows(*range(1, 71))
    builder = _SmallAnchorFakeBuilder(rows, seed_proves_order=False)
    executor = _TimedAnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert [row["id"] for row in page.rows] == ["span-0", "span-1"]
    assert [query for query, _ in executor.calls] == [
        "anchor",
        "ordered_seed",
        "match",
    ]
    assert executor.calls[0][1]["limit"] == 64


def test_partitioned_anchor_uses_one_row_global_sentinel_at_stratum_boundary() -> None:
    rows = _rows(*range(1, 64))
    rows.append({"id": "older", "start_time": END - timedelta(days=100)})
    builder = _SmallAnchorFakeBuilder(rows, seed_proves_order=False)
    executor = _TimedAnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is True
    anchor_params = [params for query, params in executor.calls if query == "anchor"]
    assert [params["limit"] for params in anchor_params] == [64, 1]
    assert [query for query, _ in executor.calls[2:]] == ["ordered_seed", "match"]


def test_partitioned_anchors_share_one_wall_budget_before_exact_fallback() -> None:
    rows = _rows(1, 2, 3)
    builder = _SmallAnchorFakeBuilder(rows, seed_proves_order=False)
    clock = _ManualMonotonic()
    executor = _ProductionTimedAnchorFakeExecutor(
        builder,
        clock=clock,
        # Every stratum is individually under the former 300 ms cap. Four
        # independent allowances would spend 1.16 s, leaving too little of the
        # production 2.2 s list deadline for these healthy exact reads.
        anchor_durations_ms=[290, 290, 290, 290],
        seed_duration_ms=550,
        match_duration_ms=550,
    )

    with mock.patch("tracer.selectors.trace_filter_reads.monotonic", new=clock):
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter()],
            key_field="id",
            page_number=0,
            page_size=2,
            deadline_ms=2_200,
        )

    assert page.complete is True
    assert page.error_code is None
    assert [row["id"] for row in page.rows] == ["span-0", "span-1"]
    assert [query for query, _ in executor.calls] == [
        "anchor",
        "ordered_seed",
        "match",
    ]
    assert executor.timeouts[0] == ("anchor", 300)
    assert executor.anchor_durations_ms == [290, 290, 290]
    assert page.elapsed_ms == pytest.approx(1_390)


@pytest.mark.parametrize(
    ("first_anchor_ms", "expected_anchor_calls"),
    [
        (274, 2),
        (277, 1),
    ],
)
def test_partitioned_anchor_wall_budget_honors_minimum_statement_boundary(
    first_anchor_ms: int,
    expected_anchor_calls: int,
) -> None:
    rows = _rows(1, 2, 3)
    builder = _SmallAnchorFakeBuilder(rows, seed_proves_order=False)
    clock = _ManualMonotonic()
    executor = _ProductionTimedAnchorFakeExecutor(
        builder,
        clock=clock,
        anchor_durations_ms=[first_anchor_ms, 290, 290, 290],
        seed_duration_ms=10,
        match_duration_ms=10,
    )

    with mock.patch("tracer.selectors.trace_filter_reads.monotonic", new=clock):
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter()],
            key_field="id",
            page_number=0,
            page_size=2,
            deadline_ms=2_200,
        )

    assert page.complete is (expected_anchor_calls == 1)
    assert page.error_code == (
        None if expected_anchor_calls == 1 else "read_budget_exceeded"
    )
    anchor_timeouts = [
        timeout for query, timeout in executor.timeouts if query == "anchor"
    ]
    assert len(anchor_timeouts) == expected_anchor_calls
    assert anchor_timeouts[0] == 300
    if expected_anchor_calls == 2:
        assert 25 <= anchor_timeouts[1] <= 26
    assert [query for query, _ in executor.calls[-2:]] == [
        "ordered_seed",
        "match",
    ]


def test_anchor_budget_boundary_skips_probe_but_preserves_fallback() -> None:
    rows = _rows(*range(1, 301))
    builder = _SmallAnchorFakeBuilder(rows, seed_proves_order=False)
    executor = _TimedAnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=200,
        deadline_ms=5_000,
        max_query_count=3,
    )

    assert page.complete is True
    assert page.error_code is None
    assert [query for query, _ in executor.calls] == [
        "ordered_seed",
        "match",
        "match",
    ]


def test_long_window_indexed_anchor_timeout_fallback_remains_incomplete() -> None:
    rows = _rows(1, 2, 3)
    builder = _SmallAnchorFakeBuilder(rows, seed_proves_order=False)
    executor = _TimedAnchorFakeExecutor(builder, fail_anchor=True)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is False
    assert page.error_code == "read_budget_exceeded"
    assert page.rows == []
    assert [query for query, _ in executor.calls] == [
        "anchor",
        "ordered_seed",
        "match",
    ]
    assert executor.timeouts[0] == ("anchor", 300)
    assert page.attempts[0].error_code == "read_budget_exceeded"


def test_graph_explicit_anchor_limit_overrides_long_window_list_recommendation() -> (
    None
):
    rows = _rows(1, 2, 3)
    builder = _SmallAnchorFakeBuilder(rows, seed_proves_order=False)
    executor = _TimedAnchorFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        max_seed_attempts=1,
        max_candidates=3,
        max_query_count=2,
        classify_batch_size=3,
        include_incomplete_rows=True,
        anchor_probe_only=True,
        anchor_probe_limit=3,
    )

    assert page.complete is False
    assert page.error_code == "sample_limit"
    assert executor.calls[0] == ("anchor", {"limit": 3})
    assert executor.timeouts[0][1] > 300


def test_small_anchor_preserves_numbered_and_cursor_page_order() -> None:
    rows = _rows(1, 2, 3, 4, 5)
    builder = _SmallAnchorFakeBuilder(rows, seed_proves_order=False)

    first_executor = _TimedAnchorFakeExecutor(builder)
    first = read_bounded_filter_page(
        builder=builder,
        analytics=first_executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )
    numbered_executor = _TimedAnchorFakeExecutor(builder)
    numbered = read_bounded_filter_page(
        builder=builder,
        analytics=numbered_executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=1,
        page_size=2,
        deadline_ms=5_000,
    )
    cursor_executor = _TimedAnchorFakeExecutor(builder)
    cursor = read_bounded_filter_page(
        builder=builder,
        analytics=cursor_executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=first.rows[-1]["id"],
    )

    assert [row["id"] for row in first.rows] == ["span-0", "span-1"]
    assert [row["id"] for row in numbered.rows] == ["span-2", "span-3"]
    assert [row["id"] for row in cursor.rows] == ["span-2", "span-3"]
    assert first.complete and numbered.complete and cursor.complete
    assert "anchor" not in [query for query, _ in cursor_executor.calls]


def test_bounded_reader_keeps_page_zero_and_page_n_disjoint() -> None:
    rows = _rows(1, 2, 3, 4, 5, 6, 7)
    builder = _FakeBuilder(rows)

    first = read_bounded_filter_page(
        builder=builder,
        analytics=_FakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )
    second = read_bounded_filter_page(
        builder=builder,
        analytics=_FakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=1,
        page_size=2,
        deadline_ms=5_000,
    )

    assert [row["id"] for row in first.rows] == ["span-0", "span-1"]
    assert [row["id"] for row in second.rows] == ["span-2", "span-3"]
    assert {row["id"] for row in first.rows}.isdisjoint(
        row["id"] for row in second.rows
    )
    assert first.has_more is True
    assert second.has_more is True


def test_cursor_keyset_handles_equal_timestamps_without_duplicates_or_skips() -> None:
    timestamp = END - timedelta(minutes=1)
    rows = [
        {"id": row_id, "start_time": timestamp}
        for row_id in ("span-d", "span-c", "span-b", "span-a")
    ]
    builder = _FakeBuilder(rows)

    first = read_bounded_filter_page(
        builder=builder,
        analytics=_FakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
    )
    second = read_bounded_filter_page(
        builder=builder,
        analytics=_FakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=first.rows[-1]["id"],
    )

    assert [row["id"] for row in first.rows] == ["span-d", "span-c"]
    assert [row["id"] for row in second.rows] == ["span-b", "span-a"]
    assert {row["id"] for row in first.rows}.isdisjoint(
        row["id"] for row in second.rows
    )
    assert second.has_more is False


def test_session_rollup_cursor_does_not_skip_resurrected_old_seed_on_page_two() -> None:
    """An insert-only t1 seed must not be rewritten to its exact live t100.

    The first 101 rollup positions are occupied by B sessions, so A is not
    classified on page one. A's old root at t1 was later tombstoned and its
    current live root is at t100. Replacing its seed order with t100 on page
    two would put it above the signed page-one cursor and reject it forever.
    """

    window_start = END - timedelta(days=1)
    session_a_seed_start = window_start + timedelta(seconds=1)
    session_a_exact_start = END - timedelta(microseconds=1)
    rollup_rows = [
        {
            "id": f"session-b-{index:03d}",
            "start_time": window_start + timedelta(seconds=index + 1),
        }
        for index in range(1, 102)
    ]
    rollup_rows.append({"id": "session-a", "start_time": session_a_seed_start})

    @dataclass
    class SessionRollupCursorBuilder(_FakeBuilder):
        @staticmethod
        def supports_filter_candidate_seed_page() -> bool:
            return True

        @staticmethod
        def filter_candidate_seed_proves_result_order() -> bool:
            return True

        @staticmethod
        def recommended_filter_cursor_seed_batch_size() -> int:
            return 101

        @staticmethod
        def recommended_filter_classify_batch_size() -> int:
            return 50

        @staticmethod
        def bounded_filter_row_order_token(row: dict[str, Any]) -> str:
            return str(row.get("_seed_order_id") or row["id"])

        def recommended_filter_initial_slice_width(self) -> timedelta:
            return self.end - self.start

        def recommended_filter_max_slice_width(self) -> timedelta:
            return self.end - self.start

        def build_filter_candidate_seed_page(
            self,
            *,
            slice_start: datetime,
            slice_end: datetime,
            limit: int,
            before_start_time: datetime | None = None,
            before_id: str | None = None,
        ) -> tuple[str, dict[str, Any]]:
            return "session_rollup_seed", {
                "slice_start": slice_start,
                "slice_end": slice_end,
                "limit": limit,
                "before_start_time": before_start_time,
                "before_id": before_id,
            }

        @staticmethod
        def build_filter_match_query_from_seed_rows(rows):
            return "session_exact_from_seed", {
                "seed_rows": tuple((row["id"], row["start_time"]) for row in rows)
            }

    class SessionRollupCursorExecutor:
        def __init__(self, builder):
            self.builder = builder

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            del timeout_ms, settings
            if query == "session_exact_from_seed":
                return QueryResult(
                    [
                        {
                            "id": session_id,
                            # Public/signed order remains the raw rollup tuple.
                            "start_time": seed_start,
                            "_seed_order_start": seed_start,
                            "_seed_order_id": session_id,
                            # Presentation may still expose exact current state.
                            "exact_start_time": (
                                session_a_exact_start
                                if session_id == "session-a"
                                else seed_start
                            ),
                        }
                        for session_id, seed_start in params["seed_rows"]
                    ],
                    len(params["seed_rows"]),
                    "clickhouse",
                    1.0,
                )
            rows = [
                row
                for row in self.builder.rows
                if params["slice_start"] <= row["start_time"] < params["slice_end"]
            ]
            rows.sort(key=lambda row: (row["start_time"], row["id"]), reverse=True)
            if params["before_start_time"] is not None:
                boundary = params["before_start_time"], params["before_id"]
                rows = [
                    row for row in rows if (row["start_time"], row["id"]) < boundary
                ]
            rows = rows[: params["limit"]]
            return QueryResult(rows, len(rows), "clickhouse", 1.0)

    builder = SessionRollupCursorBuilder(
        rollup_rows,
        start=window_start,
        end=END,
        key_field="id",
    )

    first = read_bounded_filter_page(
        builder=builder,
        analytics=SessionRollupCursorExecutor(builder),
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=100,
        deadline_ms=5_000,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )
    first_cursor = (
        first.rows[-1]["_seed_order_start"],
        first.rows[-1]["_seed_order_id"],
    )
    second = read_bounded_filter_page(
        builder=builder,
        analytics=SessionRollupCursorExecutor(builder),
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=100,
        deadline_ms=5_000,
        include_incomplete_rows=True,
        cursor_start_time=first_cursor[0],
        cursor_order_token=first_cursor[1],
        bounded_continuation=True,
    )

    first_ids = [row["id"] for row in first.rows]
    second_ids = [row["id"] for row in second.rows]
    assert first.complete is True
    assert first.has_more is True
    assert len(first_ids) == 100
    assert "session-a" not in first_ids
    assert second.complete is True
    assert second_ids == ["session-b-001", "session-a"]
    assert second.rows[-1]["exact_start_time"] > first_cursor[0]
    combined_ids = [*first_ids, *second_ids]
    assert len(combined_ids) == 102
    assert len(combined_ids) == len(set(combined_ids))


def test_org_trace_page_keeps_same_trace_id_from_both_projects() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    timestamp = END - timedelta(minutes=1)
    rows = [
        {
            "project_id": project_id,
            "trace_id": trace_id,
            "id": root_id,
            "start_time": timestamp,
        }
        for project_id, trace_id, root_id in (
            (project_b, "shared-trace", "root-b"),
            (PROJECT_ID, "shared-trace", "root-a"),
            (PROJECT_ID, "earlier-trace", "root-c"),
        )
    ]
    builder = _OrgTraceCursorFakeBuilder(rows, key_field="trace_id")

    page = read_bounded_filter_page(
        builder=builder,
        analytics=_OrgTraceCursorFakeExecutor(builder),
        filters=[_time_filter()],
        key_field="trace_id",
        page_number=0,
        page_size=2,
    )

    assert [(row["project_id"], row["trace_id"]) for row in page.rows] == [
        (project_b, "shared-trace"),
        (PROJECT_ID, "shared-trace"),
    ]
    assert page.has_more is True


def test_org_trace_cursor_continues_across_project_collision_without_skip() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    timestamp = END - timedelta(minutes=1)
    rows = [
        {
            "project_id": project_id,
            "trace_id": trace_id,
            "id": root_id,
            "start_time": timestamp,
        }
        for project_id, trace_id, root_id in (
            (project_b, "shared-trace", "root-b"),
            (PROJECT_ID, "shared-trace", "root-a"),
            (PROJECT_ID, "earlier-trace", "root-c"),
        )
    ]
    builder = _OrgTraceCursorFakeBuilder(rows, key_field="trace_id")

    pages = []
    cursor_order_token = None
    for _ in range(3):
        page = read_bounded_filter_page(
            builder=builder,
            analytics=_OrgTraceCursorFakeExecutor(builder),
            filters=[_time_filter()],
            key_field="trace_id",
            page_number=0,
            page_size=1,
            cursor_start_time=(timestamp if cursor_order_token is not None else None),
            cursor_order_token=cursor_order_token,
        )
        pages.append(page)
        last_row = page.rows[-1]
        cursor_order_token = (
            str(last_row["trace_id"]),
            str(last_row["project_id"]),
        )

    identities = [
        (page.rows[0]["project_id"], page.rows[0]["trace_id"]) for page in pages
    ]
    assert identities == [
        (project_b, "shared-trace"),
        (PROJECT_ID, "shared-trace"),
        (PROJECT_ID, "earlier-trace"),
    ]
    assert len(set(identities)) == 3
    assert [page.has_more for page in pages] == [True, True, False]


def test_org_span_cursor_tie_uses_canonical_project_string_order() -> None:
    project_low = "00000000-0000-4000-8000-000000000001"
    project_high = "00000000-0000-4000-8000-000000000010"
    timestamp = END - timedelta(minutes=1)
    rows = [
        {
            "id": "shared-span",
            "trace_id": "shared-trace",
            "project_id": project_id,
            "start_time": timestamp,
        }
        for project_id in (project_low, project_high)
    ]
    builder = _PhysicalCursorFakeBuilder(rows)

    first = read_bounded_filter_page(
        builder=builder,
        analytics=_PhysicalCursorFakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=1,
    )
    second = read_bounded_filter_page(
        builder=builder,
        analytics=_PhysicalCursorFakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=1,
        cursor_start_time=timestamp,
        cursor_order_token=("shared-span", "shared-trace", project_high),
    )

    assert [first.rows[0]["project_id"], second.rows[0]["project_id"]] == [
        project_high,
        project_low,
    ]
    assert first.has_more is True
    assert second.has_more is False


def test_cursor_keyset_preserves_same_identity_rows_one_microsecond_apart() -> None:
    newest = END - timedelta(minutes=1)
    rows = [
        {
            "id": "same-span",
            "trace_id": "same-trace",
            "project_id": "same-project",
            "start_time": newest,
        },
        {
            "id": "same-span",
            "trace_id": "same-trace",
            "project_id": "same-project",
            "start_time": newest - timedelta(microseconds=1),
        },
    ]
    builder = _PhysicalCursorFakeBuilder(rows)

    first = read_bounded_filter_page(
        builder=builder,
        analytics=_PhysicalCursorFakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=1,
    )
    second = read_bounded_filter_page(
        builder=builder,
        analytics=_PhysicalCursorFakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=1,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=("same-span", "same-trace", "same-project"),
    )

    assert first.rows[0]["start_time"] == newest
    assert second.rows[0]["start_time"] == newest - timedelta(microseconds=1)
    assert second.has_more is False


def test_live_keyset_cursor_excludes_newer_insert_and_includes_current_tail() -> None:
    rows = [
        {
            "id": f"span-{row_id}",
            "start_time": END - timedelta(minutes=offset),
            "_version": 9,
        }
        for row_id, offset in (("d", 1), ("c", 2), ("b", 3), ("a", 4))
    ]
    builder = _FakeBuilder(rows)
    first = read_bounded_filter_page(
        builder=builder,
        analytics=_FakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
    )
    # A live keyset continuation does not replay rows newer than page one's
    # checkpoint, while a newly visible row in the remaining tail participates
    # in the current-latest continuation.
    builder.rows.extend(
        [
            {
                "id": "span-live-new",
                "start_time": END - timedelta(seconds=1),
                "_version": 11,
            },
            {
                "id": "span-live-tail",
                "start_time": END - timedelta(minutes=3, seconds=30),
                "_version": 10,
            },
        ]
    )
    second = read_bounded_filter_page(
        builder=builder,
        analytics=_FakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=2,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=first.rows[-1]["id"],
    )

    assert [row["id"] for row in first.rows] == ["span-d", "span-c"]
    assert [row["id"] for row in second.rows] == ["span-b", "span-live-tail"]
    assert "span-live-new" not in {row["id"] for row in second.rows}


def test_bounded_reader_crosses_sparse_tail_with_adjacent_slices() -> None:
    rows = _rows(60 * 24 * 200)
    builder = _FakeBuilder(rows)
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    seed_attempts = [attempt for attempt in page.attempts if attempt.kind == "seed"]
    assert [row["id"] for row in page.rows] == ["span-0"]
    assert page.complete is True
    assert len(seed_attempts) > 1
    assert all(
        newer.slice_start == older.slice_end
        for newer, older in zip(seed_attempts, seed_attempts[1:], strict=False)
    )


def test_bounded_reader_covers_a_year_without_a_whole_window_query() -> None:
    builder = _FakeBuilder([])

    page = read_bounded_filter_page(
        builder=builder,
        analytics=_FakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    seed_attempts = [attempt for attempt in page.attempts if attempt.kind == "seed"]
    assert page.complete is True
    assert seed_attempts[0].slice_end == END
    assert seed_attempts[-1].slice_start == START
    assert len(seed_attempts) <= 24
    assert all(
        attempt.slice_end - attempt.slice_start < END - START
        for attempt in seed_attempts
    )


def test_bounded_reader_page_n_is_exact_in_a_one_year_sparse_tail() -> None:
    rows = _rows(60 * 24 * 180, 60 * 24 * 240, 60 * 24 * 320)
    builder = _FakeBuilder(rows)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=_FakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=1,
        page_size=1,
        deadline_ms=5_000,
    )

    assert [row["id"] for row in page.rows] == ["span-1"]
    assert page.complete is True
    assert page.has_more is True


def test_any_span_seed_exhausts_sparse_year_before_root_ordered_page_n() -> None:
    """Child timestamps cannot close a root-ordered trace page prefix.

    The two newest matching children belong to old roots. A much older matching
    child belongs to the newest root. The reader must therefore exhaust every
    adjacent child-match slice before returning either numbered root page.
    """

    window_start = END - timedelta(days=365)
    seed_rows = [
        {"id": "trace-old-a", "start_time": END - timedelta(minutes=1)},
        {"id": "trace-old-b", "start_time": END - timedelta(minutes=2)},
        {
            "id": "trace-newest",
            "start_time": window_start + timedelta(days=200),
        },
    ]
    root_rows = [
        {"id": "trace-old-a", "start_time": window_start + timedelta(days=10)},
        {"id": "trace-old-b", "start_time": window_start + timedelta(days=5)},
        {"id": "trace-newest", "start_time": window_start + timedelta(days=20)},
    ]
    builder = _FakeBuilder(
        seed_rows,
        start=window_start,
        end=END,
        match_rows=root_rows,
        seed_proves_order=False,
    )

    first_executor = _FakeExecutor(builder)
    first = read_bounded_filter_page(
        builder=builder,
        analytics=first_executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=1,
        deadline_ms=5_000,
    )
    second_executor = _FakeExecutor(builder)
    second = read_bounded_filter_page(
        builder=builder,
        analytics=second_executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=1,
        page_size=1,
        deadline_ms=5_000,
    )

    assert [row["id"] for row in first.rows] == ["trace-newest"]
    assert [row["id"] for row in second.rows] == ["trace-old-a"]
    assert first.complete is True and second.complete is True
    for executor in (first_executor, second_executor):
        seed_calls = [params for query, params in executor.calls if query == "seed"]
        assert min(call["slice_start"] for call in seed_calls) == window_start


def test_unindexed_any_span_reader_starts_with_ordered_root_batches() -> None:
    rows = _rows(1, 2, 3)
    builder = _UnindexedAnySpanFakeBuilder(rows, seed_proves_order=False)
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=1,
        deadline_ms=5_000,
    )

    seed_queries = [query for query, _ in executor.calls if query != "match"]
    assert page.complete is True
    assert [row["id"] for row in page.rows] == ["span-0"]
    assert seed_queries == ["ordered_seed"]


def test_unindexed_micro_seeds_are_distributed_and_empty_never_proves_absence() -> None:
    window_start = END - timedelta(days=120)
    builder = _DistributedMicroSeedFakeBuilder(
        [],
        start=window_start,
        end=END,
        seed_proves_order=False,
    )
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=1,
        deadline_ms=5_000,
    )

    micro_calls = [params for query, params in executor.calls if query == "micro_seed"]
    assert len(micro_calls) == 4
    assert all(
        params["slice_end"] - params["slice_start"] == timedelta(minutes=5)
        for params in micro_calls
    )
    assert min(
        params["slice_end"] for params in micro_calls
    ) == window_start + timedelta(days=30)
    assert any(query == "ordered_seed" for query, _params in executor.calls)
    assert page.complete is True
    assert page.rows == []


def test_unindexed_micro_seed_finds_old_candidate_before_ordered_proof() -> None:
    window_start = END - timedelta(days=120)
    old_match = {
        "id": "old-json-match",
        "start_time": window_start + timedelta(days=30, minutes=-1),
    }
    builder = _DistributedMicroSeedFakeBuilder(
        [old_match],
        start=window_start,
        end=END,
        seed_proves_order=False,
    )
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=1,
        deadline_ms=5_000,
    )

    call_names = [query for query, _params in executor.calls]
    assert call_names[:4] == ["micro_seed"] * 4
    assert call_names.index("match") < call_names.index("ordered_seed")
    assert [row["id"] for row in page.rows] == ["old-json-match"]
    assert page.complete is True


def test_unindexed_micro_seed_skips_when_statement_caps_cannot_be_enforced() -> None:
    window_start = END - timedelta(days=120)
    builder = _DistributedMicroSeedFakeBuilder(
        [],
        start=window_start,
        end=END,
        seed_proves_order=False,
    )
    executor = _FakeExecutor(builder)
    executor.supports_per_query_read_settings = False

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=1,
        deadline_ms=5_000,
    )

    call_names = [query for query, _params in executor.calls]
    assert "micro_seed" not in call_names
    assert call_names
    assert set(call_names) == {"ordered_seed"}
    assert page.complete is True
    assert page.rows == []


def test_recommended_anchor_skips_when_statement_caps_cannot_be_enforced() -> None:
    window_start = END - timedelta(days=120)
    builder = _SmallAnchorFakeBuilder(
        [],
        start=window_start,
        end=END,
        seed_proves_order=False,
    )
    executor = _TimedAnchorFakeExecutor(builder)
    executor.supports_per_query_read_settings = False

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=1,
        deadline_ms=5_000,
    )

    call_names = [query for query, _params in executor.calls]
    assert "anchor" not in call_names
    assert call_names
    assert set(call_names) == {"ordered_seed"}
    assert page.complete is True
    assert page.rows == []


def test_graph_key_witness_probe_rejects_locked_read_settings_before_query() -> None:
    row = {"id": "trace-a", "start_time": END - timedelta(days=30)}
    builder = _GraphKeyWitnessFakeBuilder(
        [row],
        start=END - timedelta(days=120),
        end=END,
        seed_proves_order=False,
    )
    executor = _AnchorFakeExecutor(builder)
    executor.supports_per_query_read_settings = False

    with pytest.raises(
        ValueError,
        match="graph key witness requires enforced per-query read limits",
    ):
        read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter(start=builder.start, end=builder.end)],
            key_field="id",
            page_number=0,
            page_size=1,
            deadline_ms=5_000,
            max_candidates=2,
            max_seed_attempts=1,
            max_query_count=2,
            classify_batch_size=2,
            include_incomplete_rows=True,
            anchor_probe_only=True,
            anchor_probe_limit=2,
            graph_key_witness_probe=True,
        )

    assert executor.calls == []


def test_any_span_customer_match_set_uses_200_candidate_query_budget() -> None:
    """1,063 matches need six classifier batches, not eleven 100-row batches."""

    window_start = END - timedelta(days=7)
    seed_rows = [
        {
            "id": f"trace-{index:04d}",
            # The reader uses the half-open request window [start, end).
            "start_time": END - timedelta(seconds=(index + 1) / 10),
        }
        for index in range(1_063)
    ]
    builder = _FakeBuilder(
        seed_rows,
        start=window_start,
        end=END,
        seed_proves_order=False,
    )
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    seed_calls = [params for query, params in executor.calls if query == "seed"]
    classify_calls = [params for query, params in executor.calls if query == "match"]
    assert page.complete is True
    assert len(page.rows) == 25
    assert all(call["limit"] == 200 for call in seed_calls)
    assert len(classify_calls) == 6
    assert max(len(call["candidate_ids"]) for call in classify_calls) == 200
    assert sum(len(call["candidate_ids"]) for call in classify_calls) == 1_063
    assert page.query_count <= 24


def test_builder_batch_recommendation_caps_seed_and_classifier_working_set() -> None:
    rows = [
        {
            "id": f"trace-{index:04d}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(100)
    ]
    builder = _FakeBuilder(rows, recommended_batch_size=50)
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    seed_calls = [params for query, params in executor.calls if query == "seed"]
    classify_calls = [params for query, params in executor.calls if query == "match"]
    assert page.complete is True
    assert len(page.rows) == 25
    assert [call["limit"] for call in seed_calls] == [50]
    assert [len(call["candidate_ids"]) for call in classify_calls] == [50]


def test_ordered_trace_seed_uses_two_hundred_but_stops_after_proven_prefix() -> None:
    timestamp = END - timedelta(minutes=1)
    rows = [
        {"id": f"trace-{index:03d}", "start_time": timestamp} for index in range(200)
    ]
    builder = _UnindexedAnySpanFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        seed_proves_order=False,
        recommended_batch_size=50,
        recommended_seed_batch_size=200,
    )
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    seed_calls = [params for query, params in executor.calls if query == "ordered_seed"]
    classify_calls = [params for query, params in executor.calls if query == "match"]
    assert page.complete is True
    assert [row["id"] for row in page.rows] == [
        f"trace-{index:03d}" for index in range(199, 174, -1)
    ]
    assert page.has_more is True
    assert [call["limit"] for call in seed_calls] == [200]
    assert [len(call["candidate_ids"]) for call in classify_calls] == [50]
    assert page.query_count == 2


def test_ordered_trace_seed_closes_sparse_query_33_tail_with_unchanged_classifier() -> (
    None
):
    window_start = END - timedelta(minutes=5)
    rows = [
        {
            "id": f"trace-{index:04d}",
            "start_time": END - timedelta(microseconds=index + 1),
        }
        for index in range(800)
    ]
    builder = _UnindexedAnySpanFakeBuilder(
        rows,
        start=window_start,
        end=END,
        match_rows=[],
        seed_proves_order=False,
        recommended_batch_size=50,
        recommended_seed_batch_size=200,
    )
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
        max_query_count=32,
    )

    seed_calls = [params for query, params in executor.calls if query == "ordered_seed"]
    classify_calls = [params for query, params in executor.calls if query == "match"]
    assert page.complete is True
    assert page.rows == []
    assert [call["limit"] for call in seed_calls] == [200] * 5
    assert [len(call["candidate_ids"]) for call in classify_calls] == [50] * 16
    assert page.query_count == 21


def test_ordered_trace_inner_prefix_is_exact_for_page_n() -> None:
    rows = [
        {
            "id": f"trace-{index:03d}",
            "start_time": END - timedelta(microseconds=index + 1),
        }
        for index in range(200)
    ]
    builder = _UnindexedAnySpanFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        seed_proves_order=False,
        recommended_batch_size=50,
        recommended_seed_batch_size=200,
    )
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=1,
        page_size=25,
        deadline_ms=5_000,
    )

    classify_calls = [params for query, params in executor.calls if query == "match"]
    assert page.complete is True
    assert [row["id"] for row in page.rows] == [
        f"trace-{index:03d}" for index in range(25, 50)
    ]
    assert page.has_more is True
    assert [len(call["candidate_ids"]) for call in classify_calls] == [50, 50]
    assert page.query_count == 3


def test_ordered_trace_inner_prefix_keeps_cursor_pages_disjoint() -> None:
    rows = [
        {
            "id": f"trace-{index:03d}",
            "start_time": END - timedelta(microseconds=index + 1),
        }
        for index in range(300)
    ]
    builder = _UnindexedAnySpanFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        seed_proves_order=False,
        recommended_batch_size=50,
        recommended_seed_batch_size=200,
    )
    first_executor = _FakeExecutor(builder)
    first = read_bounded_filter_page(
        builder=builder,
        analytics=first_executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )
    second_executor = _FakeExecutor(builder)
    second = read_bounded_filter_page(
        builder=builder,
        analytics=second_executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=first.rows[-1]["id"],
    )

    assert [row["id"] for row in first.rows] == [
        f"trace-{index:03d}" for index in range(25)
    ]
    assert [row["id"] for row in second.rows] == [
        f"trace-{index:03d}" for index in range(25, 50)
    ]
    assert {row["id"] for row in first.rows}.isdisjoint(
        row["id"] for row in second.rows
    )
    assert first.query_count == second.query_count == 2


def test_trace_cursor_keysets_before_limit_by_and_keeps_older_live_root() -> None:
    tied = END - timedelta(minutes=1)
    raw_rows = [
        {"id": trace_id, "root_span_id": root_id, "start_time": start_time}
        for trace_id, root_id, start_time in (
            # The stale live version of this physical root is visible to the
            # non-FINAL seed, but its newer tombstone makes the classifier pick
            # root-old. On page two the public keyset excludes root-new while
            # root-old remains a valid physical seed below the cursor.
            ("trace-zz", "root-new-tombstoned", tied),
            ("trace-y", "root-y", tied),
            ("trace-x", "root-x", tied),
            ("trace-w", "root-w", tied),
            ("trace-zz", "root-old-live", tied - timedelta(minutes=1)),
            ("trace-v", "root-v", tied - timedelta(minutes=2)),
        )
    ]
    match_rows = [
        # trace-zz's tied, newer raw root is tombstoned. Its alternate live root
        # is older than the public cursor and must remain visible on page two.
        {"id": "trace-zz", "start_time": tied - timedelta(minutes=1)},
        {"id": "trace-y", "start_time": tied},
        {"id": "trace-x", "start_time": tied},
        {"id": "trace-w", "start_time": tied},
        {"id": "trace-v", "start_time": tied - timedelta(minutes=2)},
    ]
    builder = _OrderedTraceCursorFakeBuilder(
        raw_rows,
        start=END - timedelta(minutes=5),
        end=END,
        match_rows=match_rows,
        seed_proves_order=False,
    )

    first = read_bounded_filter_page(
        builder=builder,
        analytics=_OrderedRootFakeExecutor(builder),
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )
    second_executor = _OrderedRootFakeExecutor(builder)
    second = read_bounded_filter_page(
        builder=builder,
        analytics=second_executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=first.rows[-1]["id"],
    )

    assert [row["id"] for row in first.rows] == ["trace-y", "trace-x"]
    assert [row["id"] for row in second.rows] == ["trace-w", "trace-zz"]
    assert first.complete is True and second.complete is True
    assert {row["id"] for row in first.rows}.isdisjoint(
        row["id"] for row in second.rows
    )
    second_seed = next(
        params for query, params in second_executor.calls if query == "ordered_seed"
    )
    assert second_seed["slice_end"] == tied + timedelta(microseconds=1)
    assert second_seed["before_start_time"] == tied
    assert second_seed["before_id"] == "trace-x"


def test_trace_cursor_page_one_hundred_keeps_constant_query_work() -> None:
    rows = [
        {
            "id": f"trace-{index:04d}",
            "start_time": END - timedelta(microseconds=index + 1),
        }
        for index in range(2_600)
    ]
    builder = _OrderedTraceCursorFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        seed_proves_order=False,
        recommended_batch_size=50,
        recommended_seed_batch_size=200,
    )
    cursor_start_time = None
    cursor_order_token = None
    seen: list[str] = []
    query_counts: list[int] = []

    for _page_index in range(101):
        executor = _OrderedRootFakeExecutor(builder)
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter(start=builder.start, end=builder.end)],
            key_field="id",
            page_number=0,
            page_size=25,
            deadline_ms=5_000,
            cursor_start_time=cursor_start_time,
            cursor_order_token=cursor_order_token,
        )
        assert page.complete is True
        assert len(page.rows) == 25
        assert page.has_more is True
        query_counts.append(page.query_count)
        seen.extend(str(row["id"]) for row in page.rows)
        cursor_start_time = page.rows[-1]["start_time"]
        cursor_order_token = page.rows[-1]["id"]

    assert seen == [f"trace-{index:04d}" for index in range(2_525)]
    assert query_counts == [2] * 101


def test_ordered_trace_inner_prefix_does_not_trust_tombstoned_raw_cutoff() -> None:
    rows = [
        {
            "id": f"trace-{index:03d}",
            "start_time": END - timedelta(microseconds=index + 1),
        }
        for index in range(200)
    ]
    # The 26th raw candidate resolves to an older alternate live root after
    # its newer raw root is tombstoned. The first 50-ID classifier has enough
    # matches numerically, but its public cutoff is older than the raw boundary,
    # so the next classifier must run and admit trace-050 ahead of it.
    match_rows = [dict(row) for row in rows[:26]]
    match_rows[-1]["start_time"] = END - timedelta(seconds=1)
    match_rows.append(dict(rows[50]))
    builder = _UnindexedAnySpanFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        match_rows=match_rows,
        seed_proves_order=False,
        recommended_batch_size=50,
        recommended_seed_batch_size=200,
    )
    executor = _FakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    classify_calls = [params for query, params in executor.calls if query == "match"]
    assert page.complete is True
    assert [row["id"] for row in page.rows] == [
        f"trace-{index:03d}" for index in range(25)
    ]
    assert page.has_more is True
    assert [len(call["candidate_ids"]) for call in classify_calls] == [50, 50]
    assert page.query_count == 3


def test_selector_hydrates_only_the_proven_identity_page() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
            "trace_name": f"presented-{index}",
        }
        for index in range(3)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )
    executor = _IdentityHydrationFakeExecutor(builder, reverse_hydration=True)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.has_more is True
    assert [row["id"] for row in page.rows] == ["trace-0", "trace-1"]
    assert [row["trace_name"] for row in page.rows] == [
        "presented-0",
        "presented-1",
    ]
    assert [query for query, _ in executor.calls] == [
        "seed",
        "match_identity",
        "hydrate",
    ]
    assert [attempt.kind for attempt in page.attempts] == [
        "seed",
        "classify",
        "hydrate",
    ]


def test_identity_hydration_preserves_numbered_page_n() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
            "trace_name": f"presented-{index}",
        }
        for index in range(6)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=6,
        recommended_seed_batch_size=6,
    )
    executor = _IdentityHydrationFakeExecutor(builder, reverse_hydration=True)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=1,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.has_more is True
    assert [row["id"] for row in page.rows] == ["trace-2", "trace-3"]
    hydration_call = next(
        params for query, params in executor.calls if query == "hydrate"
    )
    assert hydration_call["candidate_ids"] == ("trace-2", "trace-3")


def test_identity_classifier_batches_sparse_candidates_across_adjacent_slices() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - offset,
        }
        for index, offset in enumerate(
            (
                timedelta(minutes=1),
                timedelta(minutes=6),
                timedelta(minutes=16),
            )
        )
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=20),
        end=END,
        match_rows=[],
        recommended_batch_size=4,
        recommended_seed_batch_size=4,
    )
    executor = _IdentityHydrationFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    classify_calls = [
        params for query, params in executor.calls if query == "match_identity"
    ]
    assert page.complete is True
    assert page.rows == []
    assert [query for query, _ in executor.calls] == [
        "seed",
        "seed",
        "seed",
        "match_identity",
    ]
    assert classify_calls[0]["candidate_ids"] == (
        "trace-0",
        "trace-1",
        "trace-2",
    )


def test_sparse_identity_classifier_uses_only_one_eager_partial_flush() -> None:
    rows = [
        {
            "id": f"trace-{minute:02d}-{index:02d}",
            "root_span_id": f"root-{minute:02d}-{index:02d}",
            "start_time": END - timedelta(minutes=minute, microseconds=index),
        }
        for minute in (1, 6, 16)
        for index in range(30)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=20),
        end=END,
        match_rows=[],
        recommended_batch_size=100,
        recommended_seed_batch_size=100,
    )
    executor = _IdentityHydrationFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    classify_calls = [
        params for query, params in executor.calls if query == "match_identity"
    ]
    assert page.complete is True
    assert page.rows == []
    assert [len(call["candidate_ids"]) for call in classify_calls] == [30, 60]


def test_sparse_buffer_flush_preserves_exact_page_order_and_hydration() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - offset,
            "trace_name": f"presented-{index}",
        }
        for index, offset in enumerate(
            (
                timedelta(minutes=1),
                timedelta(minutes=6),
                timedelta(minutes=16),
            )
        )
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=20),
        end=END,
        recommended_batch_size=100,
        recommended_seed_batch_size=100,
    )
    executor = _IdentityHydrationFakeExecutor(builder, reverse_hydration=True)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.has_more is True
    assert [row["id"] for row in page.rows] == ["trace-0", "trace-1"]
    assert [query for query, _ in executor.calls] == [
        "seed",
        "seed",
        "seed",
        "match_identity",
        "hydrate",
    ]
    classifier = next(
        params for query, params in executor.calls if query == "match_identity"
    )
    assert classifier["candidate_ids"] == ("trace-0", "trace-1", "trace-2")
    hydration = next(params for query, params in executor.calls if query == "hydrate")
    assert hydration["candidate_ids"] == ("trace-0", "trace-1")


def test_sparse_candidates_are_prefiltered_before_full_window_classification() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(6)
    ]
    builder = _CandidateWitnessHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        match_rows=[],
        recommended_batch_size=2,
        recommended_seed_batch_size=6,
    )
    executor = _CandidateWitnessHydrationFakeExecutor(
        builder,
        # This raw witness is deliberately stale: the exact classifier's
        # latest-state result is empty, so it must never become public.
        witness_ids={"trace-2"},
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.rows == []
    assert [query for query, _ in executor.calls] == [
        "seed",
        "prefilter",
        "match_identity",
    ]
    exact_batches = [
        params["candidate_ids"]
        for query, params in executor.calls
        if query == "match_identity"
    ]
    assert exact_batches == [("trace-2",)]
    assert [attempt.kind for attempt in page.attempts] == [
        "seed",
        "prefilter",
        "classify",
    ]


def test_unhydrated_membership_selector_returns_only_exact_prefilter_survivors() -> (
    None
):
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(6)
    ]
    exact_row = rows[2]
    builder = _CandidateWitnessUnhydratedFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        # The raw probe also returns one stale witness. Only the exact latest-
        # state classifier may publish a selector identity.
        match_rows=[exact_row],
        recommended_batch_size=2,
        recommended_seed_batch_size=6,
    )
    executor = _CandidateWitnessHydrationFakeExecutor(
        builder,
        witness_ids={"trace-2", "trace-4"},
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.rows == [exact_row]
    assert [query for query, _ in executor.calls] == ["seed", "prefilter", "match"]
    assert executor.calls[-1][1]["candidate_ids"] == ("trace-2", "trace-4")
    assert [attempt.kind for attempt in page.attempts] == [
        "seed",
        "prefilter",
        "classify",
    ]


def test_unhydrated_membership_buffers_sparse_slices_before_prefilter() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - offset,
        }
        for index, offset in enumerate(
            (
                timedelta(minutes=1),
                timedelta(minutes=6),
                timedelta(minutes=16),
            )
        )
    ]
    builder = _CandidateWitnessUnhydratedFakeBuilder(
        rows,
        start=END - timedelta(minutes=20),
        end=END,
        # One raw witness is stale; only the exact survivor may be published.
        match_rows=[rows[0]],
        recommended_batch_size=100,
        recommended_seed_batch_size=100,
    )
    executor = _CandidateWitnessHydrationFakeExecutor(
        builder,
        witness_ids={"trace-0", "trace-2"},
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.rows == [rows[0]]
    assert [query for query, _ in executor.calls] == [
        "seed",
        "seed",
        "seed",
        "prefilter",
        "match",
    ]
    assert executor.calls[-2][1]["candidate_ids"] == (
        "trace-0",
        "trace-1",
        "trace-2",
    )
    assert executor.calls[-1][1]["candidate_ids"] == ("trace-0", "trace-2")
    assert "hydrate" not in [query for query, _ in executor.calls]


def test_unhydrated_membership_prefilter_failure_falls_back_to_exact_classifier() -> (
    None
):
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END
            - timedelta(
                minutes=(1, 6, 16)[index % 3],
                microseconds=index,
            ),
        }
        for index in range(205)
    ]
    builder = _CandidateWitnessUnhydratedFakeBuilder(
        rows,
        start=END - timedelta(minutes=20),
        end=END,
        match_rows=[rows[1]],
        # Any-span trace anchors are child-ordered, not public-root ordered.
        seed_proves_order=False,
        recommended_batch_size=100,
        recommended_seed_batch_size=100,
    )
    executor = _CandidateWitnessHydrationFakeExecutor(
        builder,
        fail_prefilter=True,
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is False
    assert page.error_code == "read_budget_exceeded"
    assert page.rows == []
    assert [query for query, _ in executor.calls] == [
        "seed",
        "seed",
        "seed",
        "prefilter",
        "match",
        "match",
        "match",
    ]
    exact_batches = [
        params["candidate_ids"] for query, params in executor.calls if query == "match"
    ]
    assert [len(batch) for batch in exact_batches] == [100, 100, 5]
    assert set().union(*(set(batch) for batch in exact_batches)) == {
        row["id"] for row in rows
    }
    prefilter_attempt = next(
        attempt for attempt in page.attempts if attempt.kind == "prefilter"
    )
    assert prefilter_attempt.error_code == "read_budget_exceeded"


def test_broad_candidate_witness_prefilter_falls_through_to_exact_classifier() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(3)
    ]
    builder = _CandidateWitnessHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )
    executor = _CandidateWitnessHydrationFakeExecutor(
        builder,
        witness_ids={row["id"] for row in rows},
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert [row["id"] for row in page.rows] == ["trace-0", "trace-1"]
    assert [query for query, _ in executor.calls] == [
        "seed",
        "prefilter",
        "match_identity",
        "hydrate",
    ]
    assert (
        next(timeout for query, timeout in executor.timeouts if query == "prefilter")
        == 250
    )
    assert len(executor.prefilter_settings) == 1
    assert executor.prefilter_settings[0]["max_bytes_to_read"] == 96 * 1024 * 1024
    assert executor.prefilter_settings[0]["max_threads"] == 1


def test_cursor_candidate_witness_returns_only_exact_hydrated_matches() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
            "trace_name": f"presented-{index}",
        }
        for index in range(4)
    ]
    builder = _CandidateWitnessHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        match_rows=[rows[0], rows[2]],
        recommended_batch_size=4,
        recommended_seed_batch_size=4,
    )
    executor = _CandidateWitnessHydrationFakeExecutor(
        builder,
        witness_ids={"trace-0", "trace-2"},
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is True
    assert [row["id"] for row in page.rows] == ["trace-0", "trace-2"]
    assert [query for query, _ in executor.calls].count("prefilter") == 2
    assert [query for query, _ in executor.calls].count("match_identity") == 1
    assert [query for query, _ in executor.calls].count("hydrate") == 1


def test_cursor_candidate_witness_exact_zero_returns_resumable_checkpoint() -> None:
    class WideCursorWitnessBuilder(_CandidateWitnessHydrationFakeBuilder):
        @staticmethod
        def recommended_filter_candidate_witness_fallback_classify_batch_size():
            return 100

        def recommended_filter_initial_slice_width(self):
            return self.end - self.start

        def recommended_filter_max_slice_width(self):
            return self.end - self.start

    rows = [
        {
            "id": f"trace-{index:03d}",
            "root_span_id": f"root-{index:03d}",
            "start_time": END - timedelta(microseconds=index + 1),
        }
        for index in range(100)
    ]
    builder = WideCursorWitnessBuilder(
        rows,
        start=END - timedelta(days=365),
        end=END,
        match_rows=[],
        recommended_batch_size=100,
        recommended_seed_batch_size=100,
    )

    first_executor = _CandidateWitnessHydrationFakeExecutor(builder, witness_ids=set())
    first = read_bounded_filter_page(
        builder=builder,
        analytics=first_executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
        max_query_count=4,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert first.complete is False
    assert first.rows == []
    assert first.error_code == "query_budget_exceeded"
    assert first.continuation_slice_start == builder.start
    assert first.continuation_slice_end == builder.end
    assert first.continuation_before_start_time == rows[25]["start_time"]
    assert first.continuation_before_id == rows[25]["id"]
    assert [query for query, _ in first_executor.calls] == [
        "seed",
        "prefilter",
        "seed",
    ]

    resumed_executor = _CandidateWitnessHydrationFakeExecutor(
        builder,
        witness_ids=set(),
    )
    resumed = read_bounded_filter_page(
        builder=builder,
        analytics=resumed_executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
        max_query_count=16,
        include_incomplete_rows=True,
        continuation_slice_start=first.continuation_slice_start,
        continuation_slice_end=first.continuation_slice_end,
        continuation_before_start_time=first.continuation_before_start_time,
        continuation_before_id=first.continuation_before_id,
        bounded_continuation=True,
    )

    assert resumed.complete is True
    assert resumed.rows == []
    assert resumed.has_more is False
    assert resumed.continuation_slice_end is None
    assert "prefilter" in [query for query, _ in resumed_executor.calls]


def test_cursor_candidate_witness_commits_survivor_prefix_before_later_failure() -> (
    None
):
    rows = [
        {
            "id": f"trace-{index:03d}",
            "root_span_id": f"root-{index:03d}",
            "start_time": END - timedelta(microseconds=index + 1),
        }
        for index in range(30)
    ]
    builder = _CandidateWitnessHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        match_rows=rows[1:],
        recommended_batch_size=10,
        recommended_seed_batch_size=30,
    )
    witness_ids = {row["id"] for row in rows[1:]}

    class FailSecondClassifier(_CandidateWitnessHydrationFakeExecutor):
        def __init__(self):
            super().__init__(builder, witness_ids=witness_ids)
            self.classifier_calls = 0

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if query == "match_identity":
                self.classifier_calls += 1
                if self.classifier_calls == 2:
                    self.calls.append((query, params))
                    self.timeouts.append((query, timeout_ms))
                    raise ReadDeadlineExceeded("later classifier budget")
            return super().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )

    first_executor = FailSecondClassifier()
    first = read_bounded_filter_page(
        builder=builder,
        analytics=first_executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
        max_query_count=6,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert first.complete is False
    assert first.error_code == "read_budget_exceeded"
    assert [row["id"] for row in first.rows] == [
        f"trace-{index:03d}" for index in range(1, 11)
    ]
    assert first.continuation_slice_start == builder.start
    assert first.continuation_slice_end == builder.end
    assert first.continuation_before_start_time == rows[10]["start_time"]
    assert first.continuation_before_id == rows[10]["id"]
    assert [query for query, _ in first_executor.calls] == [
        "seed",
        "prefilter",
        "match_identity",
        "match_identity",
        "hydrate",
    ]

    resumed_executor = _CandidateWitnessHydrationFakeExecutor(
        builder,
        witness_ids=witness_ids,
    )
    resumed = read_bounded_filter_page(
        builder=builder,
        analytics=resumed_executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
        max_query_count=16,
        include_incomplete_rows=True,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=first.rows[-1]["id"],
        continuation_slice_start=first.continuation_slice_start,
        continuation_slice_end=first.continuation_slice_end,
        continuation_before_start_time=first.continuation_before_start_time,
        continuation_before_id=first.continuation_before_id,
        bounded_continuation=True,
    )

    assert resumed.complete is True
    assert [row["id"] for row in resumed.rows] == [
        f"trace-{index:03d}" for index in range(11, 30)
    ]
    assert resumed.has_more is False
    assert resumed.continuation_slice_end is None
    combined_ids = [row["id"] for row in [*first.rows, *resumed.rows]]
    assert combined_ids == [f"trace-{index:03d}" for index in range(1, 30)]
    assert len(combined_ids) == len(set(combined_ids))


def test_candidate_witness_read_failure_falls_back_to_exact_classifier() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(4)
    ]
    builder = _CandidateWitnessHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        match_rows=[],
        recommended_batch_size=2,
        recommended_seed_batch_size=4,
    )
    executor = _CandidateWitnessHydrationFakeExecutor(
        builder,
        fail_prefilter=True,
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is False
    assert page.error_code == "read_budget_exceeded"
    assert page.rows == []
    assert [query for query, _ in executor.calls] == [
        "seed",
        "prefilter",
        "match_identity",
        "match_identity",
    ]
    assert executor.calls[-1][1]["candidate_ids"] == ("trace-2", "trace-3")
    assert page.attempts[1].kind == "prefilter"
    assert page.attempts[1].error_code == "read_budget_exceeded"
    assert [attempt.kind for attempt in page.attempts[2:]] == [
        "classify",
        "classify",
    ]


def test_candidate_witness_uses_custom_attribute_block_cap() -> None:
    class MemorySafeCandidateBuilder(_CandidateWitnessHydrationFakeBuilder):
        @staticmethod
        def recommended_filter_classify_read_settings():
            return {
                "max_block_size": 2_048,
                "preferred_max_column_in_block_size_bytes": 1_048_576,
            }

    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(3)
    ]
    builder = MemorySafeCandidateBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )
    executor = _CandidateWitnessHydrationFakeExecutor(
        builder,
        witness_ids={row["id"] for row in rows},
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert executor.prefilter_settings[0]["max_block_size"] == 2_048
    assert (
        executor.prefilter_settings[0]["preferred_max_column_in_block_size_bytes"]
        == 1_048_576
    )


def test_disabled_candidate_witness_stays_disabled_after_empty_classifier() -> None:
    class DisabledWitnessBuilder(_CandidateWitnessHydrationFakeBuilder):
        @staticmethod
        def prefer_filter_candidate_witness_probe_first():
            return False

    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(4)
    ]
    builder = DisabledWitnessBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        match_rows=[],
        recommended_batch_size=2,
        recommended_seed_batch_size=2,
    )
    executor = _CandidateWitnessHydrationFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.rows == []
    assert "prefilter" not in [query for query, _ in executor.calls]
    assert len([query for query, _ in executor.calls if query == "match_identity"]) == 2


def test_candidate_witness_skips_probe_without_enforced_query_limits() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(3)
    ]
    builder = _CandidateWitnessHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        match_rows=[],
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )
    executor = _CandidateWitnessHydrationFakeExecutor(builder)
    executor.supports_per_query_read_settings = False

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.rows == []
    assert [query for query, _ in executor.calls] == ["seed", "match_identity"]
    assert [attempt.kind for attempt in page.attempts] == ["seed", "classify"]
    assert executor.prefilter_settings == []


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("private guarded-executor resource diagnostic"),
        TimeoutError("private guarded-executor timeout diagnostic"),
    ],
    ids=["runtime", "timeout"],
)
def test_candidate_witness_runtime_failure_falls_back_to_exact_classifier(
    failure: Exception,
) -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(3)
    ]
    builder = _CandidateWitnessHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        match_rows=[],
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )
    executor = _CandidateWitnessHydrationFakeExecutor(
        builder,
        fail_prefilter=failure,
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is False
    assert page.error_code == "prefilter_unavailable"
    assert page.rows == []
    assert [query for query, _ in executor.calls] == [
        "seed",
        "prefilter",
        "match_identity",
    ]
    assert [attempt.kind for attempt in page.attempts] == [
        "seed",
        "prefilter",
        "classify",
    ]
    assert page.attempts[1].error_code == "prefilter_unavailable"
    assert "private guarded-executor" not in repr(page)


def _stratified_witness_rows(window_end: datetime) -> list[dict[str, Any]]:
    return [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": window_end - timedelta(seconds=index + 1),
        }
        for index in range(4)
    ]


def test_candidate_witness_full_window_failure_splits_without_losing_absence_proof() -> (
    None
):
    class OneShotThenSplitBuilder(_StratifiedCandidateWitnessFakeBuilder):
        @staticmethod
        def recommended_filter_candidate_witness_probe_strata():
            return 1

    class FailInitialFullWindowExecutor(_StratifiedCandidateWitnessFakeExecutor):
        def __init__(self, builder, **kwargs):
            super().__init__(builder, **kwargs)
            self.failed_initial_probe = False

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if query == "prefilter" and not self.failed_initial_probe:
                self.failed_initial_probe = True
                self.calls.append((query, params))
                self.timeouts.append((query, timeout_ms))
                self.prefilter_settings.append(dict(settings))
                raise ReadDeadlineExceeded("full-window witness budget")
            return super().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )

    window_start = END - timedelta(hours=8)
    midpoint = window_start + (END - window_start) / 2
    rows = _stratified_witness_rows(END)
    builder = OneShotThenSplitBuilder(
        rows,
        start=window_start,
        end=END,
        match_rows=[rows[0], rows[1]],
        recommended_batch_size=4,
        recommended_seed_batch_size=4,
    )
    executor = FailInitialFullWindowExecutor(
        builder,
        witness_times={
            "trace-0": END - timedelta(minutes=30),
            "trace-1": window_start + timedelta(minutes=30),
        },
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=3,
        deadline_ms=5_000,
        max_query_count=32,
    )

    prefilters = [params for query, params in executor.calls if query == "prefilter"]
    assert prefilters == [
        {
            "candidate_ids": tuple(row["id"] for row in rows),
            "slice_start": None,
            "slice_end": None,
        },
        {
            "candidate_ids": tuple(row["id"] for row in rows),
            "slice_start": midpoint,
            "slice_end": END,
        },
        {
            "candidate_ids": ("trace-1", "trace-2", "trace-3"),
            "slice_start": window_start,
            "slice_end": midpoint,
        },
    ]
    assert [
        params["candidate_ids"] for query, params in executor.calls if query == "match"
    ] == [("trace-0", "trace-1")]
    assert page.complete is False
    assert page.error_code == "read_budget_exceeded"


def test_candidate_witness_strata_cover_boundaries_before_excluding() -> None:
    window_start = END - timedelta(hours=8)
    rows = _stratified_witness_rows(END)
    builder = _StratifiedCandidateWitnessFakeBuilder(
        rows,
        start=window_start,
        end=END,
        match_rows=[rows[0], rows[1]],
        recommended_batch_size=4,
        recommended_seed_batch_size=4,
    )
    executor = _StratifiedCandidateWitnessFakeExecutor(
        builder,
        witness_times={
            # One newest-stratum witness and one exactly on an adjacent
            # half-open boundary; two candidates have no raw witness at all.
            "trace-0": END - timedelta(minutes=30),
            "trace-1": END - timedelta(hours=4),
        },
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=3,
        deadline_ms=5_000,
        max_query_count=32,
    )

    prefilters = [params for query, params in executor.calls if query == "prefilter"]
    assert len(prefilters) == 8
    assert prefilters[0]["slice_start"] == END - timedelta(hours=1)
    assert prefilters[0]["slice_end"] == END
    assert prefilters[-1]["slice_start"] == window_start
    assert all(
        newer["slice_start"] == older["slice_end"]
        for newer, older in zip(prefilters, prefilters[1:], strict=False)
    )
    assert (
        sum(
            params["slice_start"] <= END - timedelta(hours=4) < params["slice_end"]
            for params in prefilters
        )
        == 1
    )
    exact = [params for query, params in executor.calls if query == "match"]
    assert exact == [{"candidate_ids": ("trace-0", "trace-1")}]
    assert page.complete is True


def test_candidate_witness_small_stratified_batch_uses_one_exact_query() -> None:
    class OneBatchFallbackBuilder(_StratifiedCandidateWitnessFakeBuilder):
        @staticmethod
        def recommended_filter_candidate_witness_fallback_classify_batch_size():
            return 100

    window_start = END - timedelta(hours=8)
    rows = _stratified_witness_rows(END)
    builder = OneBatchFallbackBuilder(
        rows,
        start=window_start,
        end=END,
        match_rows=rows,
        # Eval trace membership uses the production-qualified 100-ID exact
        # fallback behind the optional long-window witness probe.
        recommended_batch_size=100,
        recommended_seed_batch_size=4,
    )
    executor = _StratifiedCandidateWitnessFakeExecutor(
        builder,
        # This would fail the newest optional stratum if one were attempted.
        blocked_instant=END - timedelta(minutes=30),
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=3,
        deadline_ms=5_000,
        max_query_count=32,
    )

    assert [query for query, _ in executor.calls] == ["seed", "match"]
    assert executor.calls[-1][1]["candidate_ids"] == tuple(row["id"] for row in rows)
    assert [attempt.kind for attempt in page.attempts] == ["seed", "classify"]
    assert all(attempt.error_code is None for attempt in page.attempts)
    assert page.complete is True


def test_candidate_witness_stratum_failure_discards_every_partial_negative() -> None:
    window_start = END - timedelta(hours=8)
    rows = _stratified_witness_rows(END)
    builder = _StratifiedCandidateWitnessFakeBuilder(
        rows,
        start=window_start,
        end=END,
        match_rows=rows,
        recommended_batch_size=4,
        recommended_seed_batch_size=4,
    )
    executor = _StratifiedCandidateWitnessFakeExecutor(
        builder,
        witness_times={"trace-0": END - timedelta(minutes=30)},
        # The oldest stratum and both adaptive descendants fail. Successful
        # newer strata are therefore only partial evidence and must not remove
        # trace-1/2/3 from exact classification.
        blocked_instant=window_start + timedelta(minutes=15),
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=3,
        deadline_ms=5_000,
        max_query_count=64,
    )

    exact_batches = [
        params["candidate_ids"] for query, params in executor.calls if query == "match"
    ]
    assert exact_batches == [("trace-0", "trace-1"), ("trace-2", "trace-3")]
    assert any(attempt.error_code for attempt in page.attempts)
    prefilters = [params for query, params in executor.calls if query == "prefilter"]
    assert len(prefilters) == 11
    assert any(
        params["slice_end"] - params["slice_start"] == timedelta(minutes=30)
        for params in prefilters
    )
    assert any(
        params["slice_end"] - params["slice_start"] == timedelta(minutes=15)
        for params in prefilters
    )
    assert page.complete is False
    assert page.error_code == "read_budget_exceeded"


def test_candidate_witness_extra_identity_invalidates_optional_proof() -> None:
    window_start = END - timedelta(hours=8)
    rows = _stratified_witness_rows(END)
    builder = _StratifiedCandidateWitnessFakeBuilder(
        rows,
        start=window_start,
        end=END,
        match_rows=rows,
        recommended_batch_size=4,
        recommended_seed_batch_size=4,
    )
    executor = _StratifiedCandidateWitnessFakeExecutor(
        builder,
        witness_times={
            "trace-0": END - timedelta(minutes=30),
            "trace-1": END - timedelta(minutes=30),
            "trace-2": END - timedelta(minutes=30),
        },
        # Without strict subset validation, this extra identity plus one
        # missing candidate could satisfy the cardinality early-stop and
        # falsely exclude trace-3.
        extra_identity="foreign-trace",
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=3,
        deadline_ms=5_000,
        max_query_count=32,
    )

    assert len([query for query, _ in executor.calls if query == "prefilter"]) == 1
    assert [
        params["candidate_ids"] for query, params in executor.calls if query == "match"
    ] == [("trace-0", "trace-1"), ("trace-2", "trace-3")]
    assert page.complete is True


def test_candidate_witness_no_match_multi_seed_preserves_exact_query_budget() -> None:
    class TwentyIdentityFallbackBuilder(_StratifiedCandidateWitnessFakeBuilder):
        @staticmethod
        def recommended_filter_candidate_witness_fallback_classify_batch_size():
            return 20

    window_start = END - timedelta(hours=8)
    rows = [
        {
            "id": f"trace-{index:04d}",
            "root_span_id": f"root-{index:04d}",
            "start_time": END - timedelta(microseconds=index + 1),
        }
        for index in range(600)
    ]
    builder = TwentyIdentityFallbackBuilder(
        rows,
        start=window_start,
        end=END,
        match_rows=[],
        recommended_batch_size=100,
        recommended_seed_batch_size=200,
    )
    executor = _StratifiedCandidateWitnessFakeExecutor(builder, witness_times={})

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
        max_query_count=48,
    )

    prefilters = [params for query, params in executor.calls if query == "prefilter"]
    exact_batches = [
        params["candidate_ids"] for query, params in executor.calls if query == "match"
    ]
    # A second eight-stratum batch would cross the relative 12-attempt cap, so
    # it skips speculation up front instead of running a partial proof.
    assert len(prefilters) == 8
    assert all(len(batch) <= 20 for batch in exact_batches)
    assert page.rows == []
    assert page.complete is True
    assert page.query_count <= 48


@pytest.mark.parametrize(
    "probe_mode", ["broad", "locked", "query-reserved", "relative-cap"]
)
def test_candidate_witness_unsafe_optional_outcomes_use_exact_twenty_shape(
    probe_mode: str,
) -> None:
    window_start = END - timedelta(hours=8)
    rows = _stratified_witness_rows(END)
    builder = _StratifiedCandidateWitnessFakeBuilder(
        rows,
        start=window_start,
        end=END,
        match_rows=rows,
        recommended_batch_size=4,
        recommended_seed_batch_size=4,
    )
    executor = _StratifiedCandidateWitnessFakeExecutor(
        builder,
        witness_times=(
            {row["id"]: END - timedelta(minutes=30) for row in rows}
            if probe_mode == "broad"
            else {}
        ),
    )
    if probe_mode == "locked":
        executor.supports_per_query_read_settings = False

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=window_start, end=END)],
        key_field="id",
        page_number=0,
        page_size=3,
        deadline_ms=5_000,
        max_query_count=(
            3
            if probe_mode == "query-reserved"
            else 24
            if probe_mode == "relative-cap"
            else 32
        ),
    )

    prefilter_count = len(
        [query for query, _ in executor.calls if query == "prefilter"]
    )
    assert prefilter_count == (1 if probe_mode == "broad" else 0)
    assert [
        params["candidate_ids"] for query, params in executor.calls if query == "match"
    ] == [("trace-0", "trace-1"), ("trace-2", "trace-3")]
    assert page.query_count <= (
        3
        if probe_mode == "query-reserved"
        else 24
        if probe_mode == "relative-cap"
        else 32
    )
    assert page.complete is True


def test_identity_hydration_keeps_same_text_org_traces_distinct() -> None:
    project_b = "00000000-0000-4000-8000-000000000002"
    rows = [
        {
            "project_id": project_id,
            "trace_id": "shared-trace",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=1),
            "trace_name": f"presented-{index}",
        }
        for index, project_id in enumerate((PROJECT_ID, project_b))
    ]
    rows.append(
        {
            "project_id": PROJECT_ID,
            "trace_id": "older-trace",
            "root_span_id": "root-older",
            "start_time": END - timedelta(seconds=2),
            "trace_name": "presented-older",
        }
    )
    builder = _OrgIdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        key_field="trace_id",
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )
    executor = _OrgIdentityHydrationFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="trace_id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert [(row["project_id"], row["trace_id"]) for row in page.rows] == [
        (project_b, "shared-trace"),
        (PROJECT_ID, "shared-trace"),
    ]
    hydration = next(
        params for query, params in executor.calls if query == "hydrate_org"
    )
    assert set(hydration["candidate_identities"]) == {
        (PROJECT_ID, "shared-trace"),
        (project_b, "shared-trace"),
    }


def test_identity_hydration_preserves_cursor_disjointness() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
            "trace_name": f"presented-{index}",
        }
        for index in range(6)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )
    first = read_bounded_filter_page(
        builder=builder,
        analytics=_IdentityHydrationFakeExecutor(builder),
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )
    second = read_bounded_filter_page(
        builder=builder,
        analytics=_IdentityHydrationFakeExecutor(builder),
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=first.rows[-1]["id"],
    )

    assert first.complete is True and second.complete is True
    assert [row["id"] for row in first.rows] == ["trace-0", "trace-1"]
    assert [row["id"] for row in second.rows] == ["trace-2", "trace-3"]
    assert {row["id"] for row in first.rows}.isdisjoint(
        row["id"] for row in second.rows
    )


@pytest.mark.parametrize("drift_field", ["root_span_id", "start_time"])
def test_identity_hydration_fails_closed_on_canonical_root_drift(drift_field) -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
            "trace_name": f"presented-{index}",
        }
        for index in range(3)
    ]
    hydration_rows = [dict(row) for row in rows]
    if drift_field == "root_span_id":
        hydration_rows[0][drift_field] = "replacement-root"
    else:
        hydration_rows[0][drift_field] -= timedelta(microseconds=1)
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=_IdentityHydrationFakeExecutor(
            builder, hydration_rows=hydration_rows
        ),
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is False
    assert page.rows == []
    assert page.has_more is False
    assert page.error_code == "classification_drift"


def test_identity_hydration_normalizes_aware_and_naive_root_time() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
            "trace_name": f"presented-{index}",
        }
        for index in range(3)
    ]
    hydration_rows = [
        {**row, "start_time": row["start_time"].replace(tzinfo=UTC)} for row in rows
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=_IdentityHydrationFakeExecutor(
            builder, hydration_rows=hydration_rows
        ),
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert [row["id"] for row in page.rows] == ["trace-0", "trace-1"]


def test_identity_hydration_reserves_query_and_wall_budget() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
            "trace_name": f"presented-{index}",
        }
        for index in range(3)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )
    clock = _ManualMonotonic()
    executor = _IdentityHydrationFakeExecutor(
        builder,
        clock=clock,
        durations_ms={"seed": 1_000, "match_identity": 850, "hydrate": 299},
    )

    with mock.patch("tracer.selectors.trace_filter_reads.monotonic", new=clock):
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter(start=builder.start, end=builder.end)],
            key_field="id",
            page_number=0,
            page_size=2,
            deadline_ms=2_200,
            max_query_count=3,
        )

    assert page.complete is True
    assert [query for query, _ in executor.calls] == [
        "seed",
        "match_identity",
        "hydrate",
    ]
    assert executor.timeouts[0] == (
        "seed",
        min(settings.FILTER_SELECTOR_QUERY_TIMEOUT_MS, 2_200),
    )
    assert 1_199 <= executor.timeouts[1][1] <= 1_200
    assert executor.timeouts[2] == ("hydrate", 300)
    assert page.elapsed_ms == pytest.approx(2_149)

    preflight_executor = _IdentityHydrationFakeExecutor(builder)
    preflight = read_bounded_filter_page(
        builder=builder,
        analytics=preflight_executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        max_query_count=2,
    )
    assert preflight.complete is False
    assert preflight.error_code == "query_budget_exceeded"
    assert [query for query, _ in preflight_executor.calls] == [
        "seed",
        "match_identity",
    ]


def test_identity_hydration_timeout_is_sanitized_and_never_returns_identity_rows() -> (
    None
):
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(3)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )
    clock = _ManualMonotonic()
    executor = _IdentityHydrationFakeExecutor(
        builder,
        clock=clock,
        durations_ms={"hydrate": 301},
    )

    with mock.patch("tracer.selectors.trace_filter_reads.monotonic", new=clock):
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter(start=builder.start, end=builder.end)],
            key_field="id",
            page_number=0,
            page_size=2,
            deadline_ms=2_200,
            max_query_count=3,
        )

    assert page.complete is False
    assert page.rows == []
    assert page.error_code == "read_budget_exceeded"
    assert page.attempts[-1].kind == "hydrate"
    assert page.attempts[-1].error_code == "read_budget_exceeded"


def test_absent_identity_filter_can_use_the_full_query_budget() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(3)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        match_rows=[],
        recommended_batch_size=4,
        recommended_seed_batch_size=4,
    )
    executor = _IdentityHydrationFakeExecutor(builder)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5_000,
        max_candidates=4,
        max_query_count=2,
    )

    assert page.complete is True
    assert page.rows == []
    assert page.error_code is None
    assert page.query_count == 2
    assert [query for query, _ in executor.calls] == ["seed", "match_identity"]


def test_late_first_identity_match_fails_closed_before_hydration() -> None:
    rows = [
        {
            "id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(3)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=3,
        recommended_seed_batch_size=3,
    )
    clock = _ManualMonotonic()
    executor = _IdentityHydrationFakeExecutor(
        builder,
        clock=clock,
        durations_ms={"seed": 500, "match_identity": 250},
    )

    with mock.patch("tracer.selectors.trace_filter_reads.monotonic", new=clock):
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter(start=builder.start, end=builder.end)],
            key_field="id",
            page_number=0,
            page_size=2,
            deadline_ms=1_000,
            max_query_count=3,
        )

    assert page.complete is False
    assert page.rows == []
    assert page.error_code == "deadline_exceeded"
    assert [query for query, _ in executor.calls] == ["seed", "match_identity"]


def test_identity_hydration_supports_the_api_page_size_500_envelope() -> None:
    rows = [
        {
            "id": f"trace-{index:03d}",
            "root_span_id": f"root-{index:03d}",
            "start_time": END - timedelta(microseconds=index + 1),
            "trace_name": f"presented-{index:03d}",
        }
        for index in range(501)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=50,
        recommended_seed_batch_size=501,
    )
    executor = _IdentityHydrationFakeExecutor(builder, reverse_hydration=True)

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=500,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.has_more is True
    assert len(page.rows) == 500
    assert page.rows[0]["id"] == "trace-000"
    assert page.rows[-1]["id"] == "trace-499"
    classify_calls = [
        params for query, params in executor.calls if query == "match_identity"
    ]
    assert [len(call["candidate_ids"]) for call in classify_calls] == [50] * 10 + [1]
    hydration_call = next(
        params for query, params in executor.calls if query == "hydrate"
    )
    assert len(hydration_call["candidate_ids"]) == 500
    assert page.query_count == 13


def test_cursor_page_size_500_returns_exact_hydrated_chunks_without_repeats() -> None:
    rows = [
        {
            "id": f"trace-{index:03d}",
            "root_span_id": f"root-{index:03d}",
            "start_time": END - timedelta(microseconds=index + 1),
            "trace_name": f"presented-{index:03d}",
        }
        for index in range(501)
    ]
    builder = _IdentityHydrationFakeBuilder(
        rows,
        start=END - timedelta(minutes=5),
        end=END,
        recommended_batch_size=10,
        recommended_seed_batch_size=501,
    )

    numbered_executor = _IdentityHydrationFakeExecutor(builder)
    numbered = read_bounded_filter_page(
        builder=builder,
        analytics=numbered_executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=500,
        deadline_ms=5_000,
        max_query_count=4,
    )
    assert numbered.complete is False
    assert numbered.rows == []
    assert numbered.error_code == "page_depth_exceeded"
    assert numbered.query_count == 0
    assert numbered_executor.calls == []

    first_executor = _IdentityHydrationFakeExecutor(builder)
    first = read_bounded_filter_page(
        builder=builder,
        analytics=first_executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=500,
        deadline_ms=5_000,
        max_query_count=4,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert first.complete is False
    assert first.error_code == "query_budget_exceeded"
    assert [row["id"] for row in first.rows] == [
        f"trace-{index:03d}" for index in range(20)
    ]
    assert first.continuation_slice_start == builder.start
    assert first.continuation_slice_end == builder.end
    assert first.continuation_before_start_time == rows[19]["start_time"]
    assert first.continuation_before_id == rows[19]["id"]
    assert first.query_count == 4

    second_executor = _IdentityHydrationFakeExecutor(builder)
    second = read_bounded_filter_page(
        builder=builder,
        analytics=second_executor,
        filters=[_time_filter(start=builder.start, end=builder.end)],
        key_field="id",
        page_number=0,
        page_size=500,
        deadline_ms=5_000,
        max_query_count=4,
        include_incomplete_rows=True,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=first.rows[-1]["id"],
        continuation_slice_start=first.continuation_slice_start,
        continuation_slice_end=first.continuation_slice_end,
        continuation_before_start_time=first.continuation_before_start_time,
        continuation_before_id=first.continuation_before_id,
        bounded_continuation=True,
    )

    assert second.complete is False
    assert second.error_code == "query_budget_exceeded"
    assert [row["id"] for row in second.rows] == [
        f"trace-{index:03d}" for index in range(20, 40)
    ]
    assert not ({row["id"] for row in first.rows} & {row["id"] for row in second.rows})
    assert second.continuation_before_start_time == rows[39]["start_time"]
    assert second.continuation_before_id == rows[39]["id"]


def test_read_budget_failure_is_degraded_sanitized_and_not_retried() -> None:
    builder = _FakeBuilder([])
    executor = _FakeExecutor(
        builder,
        fail=ReadDeadlineExceeded(
            "Code: 159. Timeout exceeded; secret-host.internal; SELECT customer_payload"
        ),
    )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.rows == []
    assert page.complete is False
    assert page.status == "degraded"
    assert page.error_code == "read_budget_exceeded"
    assert len(executor.calls) == 1
    assert page.query_count == 1
    assert page.attempts[0].error_code == "read_budget_exceeded"
    assert "secret-host" not in repr(page)
    assert "SELECT customer_payload" not in repr(page)


def test_opt_in_retry_never_hides_a_failed_eval_read() -> None:
    start = END - timedelta(hours=2)
    builder = _FakeBuilder([], start=start, end=END)

    class WidthBoundExecutor(_FakeExecutor):
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if query == "seed" and params["slice_end"] - params[
                "slice_start"
            ] > timedelta(minutes=30):
                self.calls.append((query, params))
                raise ReadDeadlineExceeded("Code: 159. Timeout exceeded")
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )

    executor = WidthBoundExecutor(builder)
    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=start, end=END)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
        max_seed_attempts=64,
        max_query_count=64,
        retry_wide_read_budget=True,
    )

    assert page.complete is False
    assert page.error_code == "read_budget_exceeded"
    assert page.rows == []
    assert any(
        attempt.error_code == "read_budget_exceeded" for attempt in page.attempts
    )
    successful_seeds = [
        attempt
        for attempt in page.attempts
        if attempt.kind == "seed" and attempt.error_code is None
    ]
    assert successful_seeds[0].slice_end == END
    assert successful_seeds[-1].slice_start == start


def test_opt_in_numbered_retry_never_hides_a_failed_read() -> None:
    start = END - timedelta(hours=2)
    builder = _WideInitialSliceFakeBuilder([], start=start, end=END)

    class WidthBoundExecutor(_FakeExecutor):
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if query == "seed" and params["slice_end"] - params[
                "slice_start"
            ] > timedelta(minutes=30):
                self.calls.append((query, params))
                raise ReadDeadlineExceeded("Code: 159. Timeout exceeded")
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=WidthBoundExecutor(builder),
        filters=[_time_filter(start=start, end=END)],
        key_field="id",
        page_number=3,
        page_size=25,
        deadline_ms=5_000,
        max_seed_attempts=64,
        max_query_count=64,
        retry_wide_read_budget=True,
    )

    assert page.complete is False
    assert page.error_code == "read_budget_exceeded"
    failed_wide_seeds = [
        attempt
        for attempt in page.attempts
        if attempt.kind == "seed" and attempt.error_code == "read_budget_exceeded"
    ]
    assert len(failed_wide_seeds) == 1
    successful_intervals = [
        (attempt.slice_start, attempt.slice_end)
        for attempt in page.attempts
        if attempt.kind == "seed" and attempt.error_code is None
    ]
    assert successful_intervals[0] == (END - timedelta(minutes=5), END)
    assert successful_intervals[-1][0] == start
    assert all(
        interval_end - interval_start <= timedelta(minutes=30)
        for interval_start, interval_end in successful_intervals
    )
    assert all(
        older_end == newer_start
        for (newer_start, _newer_end), (_older_start, older_end) in zip(
            successful_intervals,
            successful_intervals[1:],
            strict=False,
        )
    )


def test_cursor_does_not_retry_a_failed_wide_seed() -> None:
    start = END - timedelta(hours=2)
    builder = _WideInitialSliceFakeBuilder([], start=start, end=END)

    class WidthBoundExecutor(_FakeExecutor):
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if query == "seed" and params["slice_end"] - params[
                "slice_start"
            ] > timedelta(minutes=30):
                self.calls.append((query, params))
                raise ReadDeadlineExceeded("Code: 307. Memory limit exceeded")
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )

    executor = WidthBoundExecutor(builder)
    page = read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(start=start, end=END)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
        max_seed_attempts=64,
        max_query_count=64,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )

    assert page.complete is False
    assert page.error_code == "read_budget_exceeded"
    assert page.rows == []
    assert page.continuation_slice_start is None
    assert page.continuation_slice_end is None
    assert [query for query, _params in executor.calls] == ["seed"]
    assert page.attempts[0].error_code == "read_budget_exceeded"
    successful_seeds = [
        attempt
        for attempt in page.attempts
        if attempt.kind == "seed" and attempt.error_code is None
    ]
    successful_intervals = [
        (attempt.slice_start, attempt.slice_end) for attempt in successful_seeds
    ]
    assert successful_intervals == []


def test_programming_errors_are_not_hidden_as_read_budget_failures() -> None:
    builder = _FakeBuilder([])
    executor = _FakeExecutor(builder, fail=KeyError("bad query plan"))

    with pytest.raises(KeyError, match="bad query plan"):
        read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_time_filter()],
            key_field="id",
            page_number=0,
            page_size=25,
            deadline_ms=5_000,
        )


def test_attempt_ledger_exposes_separate_timing_query_rows_and_bytes() -> None:
    builder = _FakeBuilder(_rows(1))
    page = read_bounded_filter_page(
        builder=builder,
        analytics=_FakeExecutor(builder),
        filters=[_time_filter()],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.query_count == len(page.attempts)
    assert page.elapsed_ms >= 0
    assert all(attempt.elapsed_ms >= 0 for attempt in page.attempts)
    assert all(attempt.query_count == 1 for attempt in page.attempts)
    assert sum(attempt.rows_returned for attempt in page.attempts) == page.rows_returned
    assert (
        sum(attempt.result_payload_bytes for attempt in page.attempts)
        == page.result_payload_bytes
    )
