from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings

from tracer.models.eval_task import RowType, RunType
from tracer.selectors.eval_tasks import row_resolver
from tracer.selectors.trace_filter_reads import (
    BoundedFilterPage,
    bounded_numbered_page_depth_exceeded,
    read_bounded_filter_page,
)
from tracer.services.clickhouse.query_builders.filters import EvalFilterMetadata
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    supports_span_filters,
    supports_trace_filters,
    targets_span_filter_domain,
    targets_trace_filter_domain,
)
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallListQueryBuilder,
)
from tracer.services.clickhouse.query_service import QueryResult

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
START = datetime(2026, 1, 1)
END = START + timedelta(days=365)


def _time_filter() -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _attribute_filter(key: str, value: str) -> dict:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _structured_attribute_filter() -> dict:
    return {
        "column_id": "langfuse.trace.tags",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "array",
            "filter_op": "contains",
            "filter_value": ["vip"],
        },
    }


def _has_eval_filter(value: bool | str) -> dict:
    return {
        "column_id": "has_eval",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _has_annotation_filter(value: bool | str) -> dict:
    return {
        "column_id": "has_annotation",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


@pytest.mark.parametrize("value", [False, "false"])
def test_historical_voice_classifier_samples_exact_root_after_eval_filter(
    value: bool | str,
) -> None:
    builder = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _has_eval_filter(value)],
        eval_config_ids=["00000000-0000-4000-8000-000000000088"],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_sampling_salt="task-salt",
        bounded_sampling_rate=25.0,
    )

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=25,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a"])

    assert builder.supports_bounded_filter_scan() is True
    assert "tracer_eval_logger" not in seed_sql
    assert "cityHash64" not in seed_sql
    assert "bounded_sampling_salt" not in seed_params
    assert "trace_id NOT IN" in match_sql
    assert "toString(eval_scan.trace_id) IN %(candidate_trace_ids)s" in match_sql
    assert "toString(root_span_id)" in match_sql
    assert match_params["candidate_trace_ids"] == ("trace-a",)
    assert match_params["bounded_sampling_salt"] == "task-salt"
    assert match_params["bounded_sampling_rate"] == 25.0


@pytest.mark.parametrize(
    ("builder_class", "identity"),
    [
        (SpanListQueryBuilder, "id"),
        (TraceListQueryBuilder, "trace_id"),
        (SessionListQueryBuilder, "session_id"),
    ],
)
def test_internal_bounded_seed_pushes_sampling_before_limit(
    builder_class, identity: str
) -> None:
    builder = builder_class(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
        bounded_internal_scan=True,
        bounded_sampling_salt="task-salt",
        bounded_sampling_rate=25.0,
    )

    sql, params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=25,
    )

    assert builder.supports_bounded_filter_scan() is True
    if builder_class is SpanListQueryBuilder:
        assert "toString(trace_id)" in sql
        assert "toString(id)" in sql
    else:
        assert f"cityHash64(%(bounded_sampling_salt)s, toString({identity}))" in sql
    assert sql.index("cityHash64") < sql.index("LIMIT %(filter_seed_limit)s")
    assert params["bounded_sampling_salt"] == "task-salt"
    assert params["bounded_sampling_rate"] == 25.0


def test_internal_bounded_span_scan_supports_time_only_tasks() -> None:
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter()],
        bounded_internal_scan=True,
        bounded_sampling_salt="task-salt",
        bounded_sampling_rate=100.0,
    )

    seed_sql, _ = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=25,
    )
    match_sql, _ = builder.build_filter_match_query(["span-a"])

    assert "WHERE 1 = 1" in seed_sql
    assert "AND 1 = 1" in match_sql


@pytest.mark.parametrize(
    "row_type",
    [RowType.SPANS, RowType.TRACES, RowType.SESSIONS, RowType.VOICE_CALLS],
)
def test_eval_task_annotation_completeness_threads_all_project_labels(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
) -> None:
    labels = [SimpleNamespace(id="label-a"), SimpleNamespace(id="label-b")]
    captured: dict = {}

    def fake_labels(project_id):
        assert str(project_id) == PROJECT_ID
        return labels

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=0,
            elapsed_ms=1,
            query_count=2,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.utils.helper.get_annotation_labels_for_project", fake_labels
    )
    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    assert (
        row_resolver._resolve_bounded_historical_span_ids(
            object(),
            sql=None,
            params=None,
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "date_range": [START, END],
                "filters": [_has_annotation_filter(False)],
            },
            limit=25,
            batch_size=256,
            row_type=row_type,
        )
        == []
    )

    assert captured["builder"].annotation_label_ids == ["label-a", "label-b"]


def test_eval_task_missing_one_of_two_labels_uses_completeness_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    monkeypatch.setattr(
        "tracer.utils.helper.get_annotation_labels_for_project",
        lambda _project_id: [
            SimpleNamespace(id="label-a"),
            SimpleNamespace(id="label-b"),
        ],
    )

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=0,
            elapsed_ms=1,
            query_count=2,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )
    row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={
            "date_range": [START, END],
            "filters": [_has_annotation_filter(False)],
        },
        limit=25,
        batch_size=256,
        row_type=RowType.TRACES,
    )

    sql, params = captured["builder"].build_filter_match_query(["trace-a"])
    assert "NOT IN" in sql
    assert "HAVING uniqExact(s.label_id) >= 2" in sql
    assert "label-a" in params.values()
    assert "label-b" in params.values()


@pytest.mark.parametrize("row_type", [RowType.SPANS, RowType.TRACES])
def test_time_only_eval_resolution_uses_list_parity_newest_first_reader(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
) -> None:
    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        rows = [
            {
                "trace_id": "newest",
                "start_time": END,
            },
            {
                "trace_id": "older",
                "start_time": END - timedelta(seconds=1),
            },
        ]
        if row_type == RowType.SPANS:
            rows = [{**row, "id": row["trace_id"]} for row in rows]
        return BoundedFilterPage(
            rows=rows,
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=2,
            elapsed_ms=1,
            query_count=2,
            rows_returned=2,
            result_payload_bytes=20,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    ids = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql="must-not-run-legacy-id-order",
        params={"start_date": START, "end_date": END},
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={"date_range": [START, END]},
        limit=25,
        batch_size=11,
        row_type=row_type,
    )

    assert ids == ["newest", "older"]
    assert captured["page_size"] == 25
    assert captured["builder"].filters[0]["column_id"] == "created_at"


def test_task_filters_merge_legacy_and_canonical_lists() -> None:
    canonical = _attribute_filter("prompt_slug", "agent_2_identity_disclosure")
    legacy = _attribute_filter("final_status", "Rejected")

    normalized = row_resolver._task_ui_filters(
        {
            "filters": [canonical],
            "span_attributes_filters": [legacy],
            "date_range": [START, END],
            "trace_id": ["trace-a"],
            "observation_type": ["llm"],
        }
    )

    assert canonical in normalized
    assert legacy in normalized
    assert {item["column_id"] for item in normalized} >= {
        "created_at",
        "trace_id",
        "observation_type",
    }


@pytest.mark.parametrize(
    ("row_type", "identity", "expected_classify_batch"),
    [
        (RowType.SPANS, "id", 200),
        (RowType.TRACES, "trace_id", 10),
    ],
)
def test_bounded_resolver_returns_only_a_complete_latest_state_page(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
    identity: str,
    expected_classify_batch: int,
) -> None:
    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[
                {identity: "row-a", "start_time": END - timedelta(minutes=1)},
                {identity: "row-b", "start_time": END - timedelta(minutes=2)},
            ],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=2,
            elapsed_ms=10,
            query_count=2,
            rows_returned=4,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )
    filters = {
        "filters": [_attribute_filter("prompt_slug", "agent_2_identity_disclosure")],
        "span_attributes_filters": [_attribute_filter("final_status", "Rejected")],
        "date_range": [START, END],
    }

    ids = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql="baseline-protocol-sql",
        params={"start_date": START, "end_date": END},
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters=filters,
        limit=25,
        batch_size=256,
        row_type=row_type,
    )

    assert ids == ["row-a", "row-b"]
    assert captured["deadline_ms"] == 120_000
    assert captured["max_query_count"] == 128
    assert captured["max_candidates"] == 512
    assert captured["classify_batch_size"] == expected_classify_batch
    assert captured.get("retry_wide_read_budget", False) is False
    assert captured["builder"].supports_bounded_filter_scan() is True
    assert captured["builder"]._bounded_identity_only is True
    if row_type == RowType.TRACES:
        trace_builder = captured["builder"]
        assert trace_builder._bounded_bulk_scan is True
        assert trace_builder._bounded_include_filter_witnesses is False
        assert trace_builder.skip_full_window_filter_anchor_probe() is True
        membership_sql, _ = trace_builder.build_filter_match_query_from_seed_rows(
            [
                {
                    "trace_id": "trace-a",
                    "root_span_id": "root-a",
                    "start_time": END - timedelta(minutes=1),
                }
            ]
        )
        assert "filter_witness_0" not in membership_sql
        assert "argMinIf(tuple(grouped_id, latest_start_time)" not in membership_sql


@pytest.mark.parametrize(
    "row_type",
    [RowType.SPANS, RowType.TRACES, RowType.SESSIONS, RowType.VOICE_CALLS],
)
def test_ordinary_historical_workflow_uses_two_minute_aggregate_budget(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
) -> None:
    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=0,
            elapsed_ms=1,
            query_count=1,
            rows_returned=0,
            result_payload_bytes=2,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    assert (
        row_resolver._resolve_bounded_historical_span_ids(
            object(),
            sql=None,
            params=None,
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "filters": [_attribute_filter("final_status", "Rejected")],
                "date_range": [START, END],
            },
            limit=25,
            batch_size=25,
            row_type=row_type,
        )
        == []
    )
    assert captured["workflow_exact"] is False
    assert captured["deadline_ms"] == 120_000
    assert captured["max_query_count"] == 128
    assert captured["query_timeout_ms"] == 3_000


def test_ordinary_trace_witness_proof_reserves_replay_from_two_minute_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    witness_start = END - timedelta(minutes=1)
    membership_row = {
        "trace_id": "trace-a",
        "root_span_id": "root-a",
        "start_time": witness_start,
    }

    def fake_read(**kwargs):
        captured["page"] = kwargs
        return BoundedFilterPage(
            rows=[membership_row],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=1,
            elapsed_ms=1,
            query_count=2,
            rows_returned=1,
            result_payload_bytes=20,
            attempts=(),
        )

    def fake_replay(_analytics, **kwargs):
        captured["replay"] = kwargs
        return [
            {
                **membership_row,
                "filter_witness_0": ("span-a", witness_start),
            }
        ]

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )
    monkeypatch.setattr(
        row_resolver,
        "_replay_historical_trace_filter_witnesses",
        fake_replay,
    )

    result = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={
            "filters": [_attribute_filter("final_status", "Rejected")],
            "date_range": [START, END],
        },
        limit=25,
        batch_size=25,
        row_type=RowType.TRACES,
        include_trace_filter_witnesses=True,
    )

    assert result.ids == ("trace-a",)
    assert captured["page"]["workflow_exact"] is False
    assert captured["page"]["deadline_ms"] == 106_000
    assert captured["page"]["max_query_count"] == 112
    assert captured["page"]["query_timeout_ms"] == 3_000
    assert captured["replay"]["total_deadline_seconds"] == 120.0
    assert captured["replay"]["max_query_count"] == 128


@pytest.mark.parametrize("value", [False, "false"])
def test_bounded_historical_voice_returns_canonical_root_span_ids(
    monkeypatch: pytest.MonkeyPatch,
    value: bool | str,
) -> None:
    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[
                {
                    "trace_id": "trace-b",
                    "root_span_id": "voice-root-b",
                    "start_time": END - timedelta(minutes=1),
                },
                {
                    "trace_id": "trace-a",
                    "root_span_id": "voice-root-a",
                    "start_time": END - timedelta(minutes=2),
                },
            ],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=2,
            elapsed_ms=10,
            query_count=2,
            rows_returned=4,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )
    monkeypatch.setattr(
        row_resolver,
        "_eval_config_ids_for_filters",
        lambda _project_id, _filters: ("00000000-0000-4000-8000-000000000088",),
    )

    ids = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=25.0,
        filters={
            "filters": [_has_eval_filter(value)],
            "date_range": [START, END],
        },
        limit=25,
        batch_size=256,
        row_type=RowType.VOICE_CALLS,
    )

    assert ids == ["voice-root-b", "voice-root-a"]
    assert captured["key_field"] == "trace_id"
    assert captured["classify_batch_size"] == 50
    assert isinstance(captured["builder"], VoiceCallListQueryBuilder)
    assert captured["builder"]._bounded_identity_only is True
    assert captured["builder"]._bounded_sampling_salt == "task-salt"
    assert captured["builder"]._bounded_sampling_rate == 25.0
    assert captured["builder"].eval_config_ids == [
        "00000000-0000-4000-8000-000000000088"
    ]
    match_sql, match_params = captured["builder"].build_filter_match_query(["trace-a"])
    assert "trace_id NOT IN" in match_sql
    assert "toString(eval_scan.trace_id) IN %(candidate_trace_ids)s" in match_sql
    assert "toString(root_span_id)" in match_sql
    assert match_params["candidate_trace_ids"] == ("trace-a",)


def test_bounded_historical_session_selector_keeps_exact_newest_first_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[
                {
                    "session_id": "session-b",
                    "start_time": END - timedelta(minutes=1),
                },
                {
                    "session_id": "session-a",
                    "start_time": END - timedelta(minutes=2),
                },
            ],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=2,
            elapsed_ms=10,
            query_count=2,
            rows_returned=4,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    ids = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=25.0,
        filters={
            "filters": [_attribute_filter("final_status", "Rejected")],
            "date_range": [START, END],
        },
        limit=25,
        batch_size=256,
        row_type=RowType.SESSIONS,
    )

    assert ids == ["session-b", "session-a"]
    assert captured["key_field"] == "session_id"
    assert captured["page_size"] == 25
    assert captured["deadline_ms"] == 120_000
    assert captured["max_query_count"] == 128
    assert captured["max_candidates"] == 512
    assert captured["classify_batch_size"] == 50
    assert captured["builder"]._bounded_internal_scan is True
    assert captured["builder"]._bounded_sampling_salt == "task-salt"
    assert captured["builder"]._bounded_sampling_rate == 25.0


def test_bounded_historical_session_selector_accepts_exact_capped_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_read(**_kwargs):
        return BoundedFilterPage(
            rows=[{"session_id": "must-not-escape", "start_time": END}],
            has_more=True,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=26,
            elapsed_ms=10,
            query_count=2,
            rows_returned=26,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    assert row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={
            "filters": [_attribute_filter("final_status", "Rejected")],
            "date_range": [START, END],
        },
        limit=25,
        batch_size=256,
        row_type=RowType.SESSIONS,
    ) == ["must-not-escape"]


@pytest.mark.parametrize(
    ("row_type", "row"),
    [
        (
            RowType.SESSIONS,
            {"session_id": "session-a", "start_time": END - timedelta(minutes=1)},
        ),
        (
            RowType.VOICE_CALLS,
            {
                "trace_id": "trace-a",
                "root_span_id": "voice-root-a",
                "start_time": END - timedelta(minutes=1),
            },
        ),
    ],
)
def test_high_limit_workflow_uses_finite_exact_contract(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
    row: dict,
) -> None:
    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[row],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=1,
            elapsed_ms=10,
            query_count=2,
            rows_returned=2,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    ids = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={"date_range": [START, END]},
        limit=1_000_000,
        batch_size=256,
        row_type=row_type,
    )

    expected_id = row.get("session_id") or row.get("root_span_id")
    assert ids == [expected_id]
    assert captured["classify_batch_size"] == 50
    assert captured["page_size"] == 1_000_000
    assert captured["workflow_exact"] is True
    assert captured["deadline_ms"] == 170 * 60 * 1000
    assert captured["max_seed_attempts"] == 16_384
    assert captured["max_query_count"] == 32_768
    assert (
        bounded_numbered_page_depth_exceeded(
            page_number=0,
            page_size=captured["page_size"],
            max_seed_attempts=captured["max_seed_attempts"],
            max_candidates=captured["max_candidates"],
            max_query_count=captured["max_query_count"],
            classify_batch_size=captured["classify_batch_size"],
            seed_batch_size=200,
            query_contract_limit=32_768,
        )
        is False
    )


@pytest.mark.parametrize("row_type", [RowType.SESSIONS, RowType.VOICE_CALLS])
def test_high_limit_workflow_reaches_clickhouse_for_empty_corpus(
    row_type: str,
) -> None:
    class EmptyAnalytics:
        calls = 0

        def execute_ch_query(self, _query, _params, **_kwargs):
            self.calls += 1
            return QueryResult([], 0, "clickhouse", 0.0)

    analytics = EmptyAnalytics()
    ids = row_resolver._resolve_bounded_historical_span_ids(
        analytics,
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={"date_range": [START, END]},
        limit=1_000_000,
        batch_size=256,
        row_type=row_type,
    )

    assert ids == []
    # The workflow covers the frozen window in adjacent slices. It remains
    # finite even when no row is available to close an ordered prefix early.
    assert 1 <= analytics.calls <= 16_384


@pytest.mark.parametrize(
    "row_type",
    [RowType.SPANS, RowType.TRACES, RowType.SESSIONS, RowType.VOICE_CALLS],
)
def test_exactly_10k_uses_workflow_query_contract(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
) -> None:
    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=0,
            elapsed_ms=1,
            query_count=1,
            rows_returned=0,
            result_payload_bytes=2,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    assert (
        row_resolver._resolve_bounded_historical_span_ids(
            object(),
            sql=None,
            params=None,
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "filters": [_attribute_filter("final_status", "Rejected")],
                "date_range": [START, END],
            },
            limit=10_000,
            batch_size=1_000,
            row_type=row_type,
        )
        == []
    )
    assert captured["workflow_exact"] is True
    assert captured["page_size"] == 10_000
    assert captured["max_query_count"] == 32_768
    assert captured["max_seed_attempts"] == 16_384
    assert captured["deadline_ms"] == 170 * 60 * 1000


@pytest.mark.parametrize(
    ("row_type", "identity"),
    [(RowType.SPANS, "id"), (RowType.TRACES, "trace_id")],
)
def test_eval_task_reuses_candidate_scoped_map_filter(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
    identity: str,
) -> None:
    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        row = {identity: "selected-id", "start_time": END - timedelta(minutes=1)}
        if row_type == RowType.SPANS:
            row.update({"project_id": PROJECT_ID, "trace_id": "trace-a"})
        return BoundedFilterPage(
            rows=[row],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=1,
            elapsed_ms=1,
            query_count=2,
            rows_returned=2,
            result_payload_bytes=20,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )
    filters = {
        "span_attributes_filters": [
            {
                "column_id": "customer.context",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "map",
                    "filter_op": "contains",
                    "filter_value": {"tier": "vip", "attempt": 2},
                },
            }
        ],
        "date_range": [START, END],
    }

    ids = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql="must-not-run",
        params={"start_date": START, "end_date": END},
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters=filters,
        limit=25,
        batch_size=17,
        row_type=row_type,
    )

    assert ids == ["selected-id"]
    builder = captured["builder"]
    assert builder.supports_bounded_filter_scan() is True
    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=25,
    )
    match_sql, match_params = builder.build_filter_match_query(["selected-id"])
    assert "attributes_extra" not in seed_sql
    assert "latest_filter_key_0" not in seed_params
    assert "JSONExtractRaw(attributes_extra" in match_sql
    assert "vip" not in match_sql
    assert "vip" in match_params.values()


@pytest.mark.parametrize(
    ("builder_class", "identity"),
    [(SpanListQueryBuilder, "id"), (TraceListQueryBuilder, "trace_id")],
)
def test_eval_internal_classifier_projects_only_identity_and_order(
    builder_class, identity: str
) -> None:
    builder = builder_class(
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_sampling_salt="task-salt",
        bounded_sampling_rate=100.0,
    )

    sql, _ = builder.build_filter_match_query([f"{identity}-a"])

    assert f"AS {identity}" in sql
    assert "AS start_time" in sql
    if builder_class is SpanListQueryBuilder:
        assert "latest_trace_id AS trace_id" in sql
    assert "latest_cost AS cost" not in sql
    assert "latest_total_tokens AS total_tokens" not in sql


def test_trace_eval_classifier_projects_one_physical_witness_per_any_span_leaf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    filters = [
        _time_filter(),
        _attribute_filter("final_status", "Rejected"),
        _attribute_filter("customer_tier", "vip"),
    ]
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_sampling_salt="task-salt",
        bounded_sampling_rate=100.0,
    )

    sql, _ = builder.build_filter_match_query(["trace-a"])

    assert "filter_witness_0" in sql
    assert "filter_witness_1" in sql
    assert sql.count("argMinIf(tuple(grouped_id, latest_start_time)") == 2
    assert "tuple(latest_start_time, grouped_id)" in sql

    witness_start = END - timedelta(minutes=1)

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[
                {
                    "trace_id": "trace-a",
                    "root_span_id": "root-a",
                    "start_time": witness_start,
                }
            ],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=1,
            elapsed_ms=1,
            query_count=2,
            rows_returned=2,
            result_payload_bytes=20,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    class ReplayAnalytics:
        def execute_ch_query(self, _query, params, *, timeout_ms, settings):
            assert params["candidate_trace_ids"] == ("trace-a",)
            assert timeout_ms == 3_000
            assert settings["max_execution_time"] == 3
            assert settings["max_threads"] == 1
            assert settings["max_block_size"] == 2_048
            assert settings["preferred_max_column_in_block_size_bytes"] == 1_048_576
            assert "max_rows_to_read" not in settings
            assert settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
            assert settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
            assert settings["max_result_rows"] == 1
            return QueryResult(
                [
                    {
                        "trace_id": "trace-a",
                        "root_span_id": "root-a",
                        "start_time": witness_start,
                        "filter_witness_0": ("span-status", witness_start),
                        "filter_witness_1": ("span-tier", witness_start),
                    }
                ],
                1,
                "clickhouse",
                1.0,
            )

    result = row_resolver._resolve_bounded_historical_span_ids(
        ReplayAnalytics(),
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={
            "filters": filters,
            "date_range": [START, END],
        },
        limit=25,
        batch_size=25,
        row_type=RowType.TRACES,
        include_trace_filter_witnesses=True,
    )

    assert result.ids == ("trace-a",)
    assert [witness.span_id for witness in result.trace_filter_witnesses] == [
        "span-status",
        "span-tier",
    ]
    assert [witness.column_id for witness in result.trace_filter_witnesses] == [
        "final_status",
        "customer_tier",
    ]
    assert captured["deadline_ms"] == 106_000
    assert captured["max_query_count"] == 112
    assert captured["classify_batch_size"] == 10
    phase_one_builder = captured["builder"]
    assert phase_one_builder._bounded_include_filter_witnesses is False
    membership_sql, _ = phase_one_builder.build_filter_match_query_from_seed_rows(
        [
            {
                "trace_id": "trace-a",
                "root_span_id": "root-a",
                "start_time": witness_start,
            }
        ]
    )
    assert "filter_witness_0" not in membership_sql
    assert "argMinIf(tuple(grouped_id, latest_start_time)" not in membership_sql


def test_ui_default_100k_trace_task_accepts_a_complete_sparse_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real 100k wire limit through the real bounded reader.

    The population is deliberately sparse. Success proves the background
    workflow exhausts the frozen window while returning exact any-span
    witnesses in the same buffered classifier pass as membership.
    """

    window_start = END - timedelta(minutes=10)
    root_start = END - timedelta(minutes=1)
    source_rows = {
        "trace-b": {
            "trace_id": "trace-b",
            "root_span_id": "root-b",
            "start_time": root_start,
            "matched_span_id": "status-b",
        },
        "trace-a": {
            "trace_id": "trace-a",
            "root_span_id": "root-a",
            "start_time": root_start - timedelta(seconds=1),
            "matched_span_id": "status-a",
        },
    }

    class SparsePopulationAnalytics:
        calls: list[tuple[str, dict]] = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((query, params))
            assert timeout_ms <= 3_000
            assert settings["max_threads"] == 1
            assert settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
            assert settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024

            candidate_ids = params.get("candidate_trace_ids")
            if candidate_ids is not None:
                assert "filter_witness_0" in query
                assert "argMinIf(tuple(grouped_id, latest_start_time)" in query
                assert "max_rows_to_read" not in settings
                assert settings["max_block_size"] == 2_048
                assert settings["preferred_max_column_in_block_size_bytes"] == 1_048_576
                rows = [
                    {
                        **source_rows[trace_id],
                        "filter_witness_0": (
                            source_rows[trace_id]["matched_span_id"],
                            source_rows[trace_id]["start_time"],
                        ),
                    }
                    for trace_id in candidate_ids
                ]
                return QueryResult(rows, len(rows), "clickhouse", 1.0)

            assert "id AS root_span_id" in query
            assert "parent_span_id IS NULL" in query
            assert "max_rows_to_read" not in settings
            assert settings["max_block_size"] == 8_192
            assert "preferred_max_column_in_block_size_bytes" not in settings
            rows = [
                {
                    "trace_id": row["trace_id"],
                    "root_span_id": row["root_span_id"],
                    "start_time": row["start_time"],
                }
                for row in source_rows.values()
                if params["filter_slice_start"]
                <= row["start_time"]
                < params["filter_slice_end"]
            ]
            return QueryResult(rows, len(rows), "clickhouse", 1.0)

    captured: dict = {}
    real_read = read_bounded_filter_page

    def capture_read(**kwargs):
        captured.update(kwargs)
        return real_read(**kwargs)

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", capture_read
    )

    analytics = SparsePopulationAnalytics()
    result = row_resolver._resolve_bounded_historical_span_ids(
        analytics,
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={
            "filters": [_attribute_filter("final_status", "Rejected")],
            "date_range": [window_start, END],
        },
        limit=100_000,
        batch_size=10_000,
        row_type=RowType.TRACES,
        include_trace_filter_witnesses=True,
    )

    assert result.ids == ("trace-b", "trace-a")
    assert {
        (witness.trace_id, witness.span_id) for witness in result.trace_filter_witnesses
    } == {("trace-a", "status-a"), ("trace-b", "status-b")}
    seed_queries = [
        query
        for query, params in analytics.calls
        if "candidate_trace_ids" not in params
    ]
    membership_queries = [
        query
        for query, params in analytics.calls
        if "candidate_trace_ids" in params and "filter_witness_0" not in query
    ]
    witness_queries = [
        query
        for query, params in analytics.calls
        if "candidate_trace_ids" in params and "filter_witness_0" in query
    ]
    assert len(seed_queries) == 2
    assert len(membership_queries) == 0
    assert len(witness_queries) == 1
    assert captured["classify_batch_size"] == 10
    assert captured["workflow_exact"] is True
    assert captured["deadline_ms"] == 170 * 60 * 1000
    assert "max_rows_to_read" not in captured["read_settings"]
    assert "max_block_size" not in captured["read_settings"]
    assert "preferred_max_column_in_block_size_bytes" not in captured["read_settings"]
    assert captured["classify_read_settings"] == {
        "max_block_size": 2_048,
        "preferred_max_column_in_block_size_bytes": 1_048_576,
    }
    assert captured["builder"]._bounded_include_filter_witnesses is True


@pytest.mark.parametrize(
    "filter_item",
    [
        _attribute_filter("final_status", "Rejected"),
        _structured_attribute_filter(),
    ],
    ids=["scalar-map", "structured-json"],
)
def test_trace_eval_witness_replay_uses_ten_id_batches_with_hard_caps(
    monkeypatch: pytest.MonkeyPatch,
    filter_item: dict,
) -> None:
    witness_start = END - timedelta(minutes=1)
    rows = [
        {
            "trace_id": f"trace-{index:02d}",
            "root_span_id": f"root-{index:02d}",
            "start_time": witness_start - timedelta(seconds=index),
        }
        for index in range(25)
    ]

    def fake_read(**kwargs):
        assert kwargs["max_query_count"] == 112
        assert kwargs["classify_batch_size"] == 10
        assert kwargs["workflow_exact"] is False
        assert kwargs["deadline_ms"] == 106_000
        assert kwargs["builder"]._bounded_include_filter_witnesses is False
        return BoundedFilterPage(
            rows=rows,
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=len(rows),
            elapsed_ms=1,
            query_count=12,
            rows_returned=len(rows),
            result_payload_bytes=1_000,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    class ReplayAnalytics:
        batch_sizes: list[int] = []

        def execute_ch_query(self, _query, params, *, timeout_ms, settings):
            trace_ids = params["candidate_trace_ids"]
            self.batch_sizes.append(len(trace_ids))
            assert len(trace_ids) <= 10
            assert timeout_ms == 3_000
            assert settings == {
                "max_execution_time": 3,
                "timeout_overflow_mode": "throw",
                "max_threads": 1,
                "max_block_size": 2_048,
                "preferred_max_column_in_block_size_bytes": 1_048_576,
                "max_memory_usage": 36 * 1024 * 1024 * 1024,
                "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
                "read_overflow_mode": "throw",
                "result_overflow_mode": "throw",
                "max_result_rows": len(trace_ids),
            }
            by_trace = {row["trace_id"]: row for row in rows}
            replayed = [
                {
                    **by_trace[trace_id],
                    "filter_witness_0": (f"span-{trace_id}", witness_start),
                }
                for trace_id in trace_ids
            ]
            return QueryResult(replayed, len(replayed), "clickhouse", 1.0)

    analytics = ReplayAnalytics()
    result = row_resolver._resolve_bounded_historical_span_ids(
        analytics,
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={
            "filters": [filter_item],
            "date_range": [START, END],
        },
        limit=25,
        batch_size=25,
        row_type=RowType.TRACES,
        include_trace_filter_witnesses=True,
    )

    assert result.ids == tuple(row["trace_id"] for row in rows)
    assert analytics.batch_sizes == [10, 10, 5]
    assert len(result.trace_filter_witnesses) == len(rows)


@pytest.mark.unit
def test_dense_100k_custom_trace_witness_replay_is_a_bounded_fail_safe_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise finite mechanics only; this is not a production latency claim."""

    witness_start = END - timedelta(minutes=1)
    rows = [
        {
            "trace_id": f"trace-{index:06d}",
            "root_span_id": f"root-{index:06d}",
            "start_time": witness_start,
        }
        for index in range(100_000)
    ]

    class Builder:
        @staticmethod
        def build_filter_match_query(batch, *, include_filter_witnesses):
            assert include_filter_witnesses is True
            return "dense-witness", {"candidate_trace_ids": tuple(batch)}

    class Analytics:
        calls = 0

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls += 1
            assert query == "dense-witness"
            assert 1 <= len(params["candidate_trace_ids"]) <= 10
            assert timeout_ms == 3_000
            assert settings["max_execution_time"] == 3
            assert settings["max_threads"] == 1
            assert settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
            assert settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
            assert settings["max_result_rows"] == len(params["candidate_trace_ids"])
            replayed = []
            for trace_id in params["candidate_trace_ids"]:
                suffix = trace_id.removeprefix("trace-")
                replayed.append(
                    {
                        "trace_id": trace_id,
                        "root_span_id": f"root-{suffix}",
                        "start_time": witness_start,
                        "filter_witness_0": (f"span-{suffix}", witness_start),
                    }
                )
            return QueryResult(replayed, len(replayed), "clickhouse", 0.0)

    monkeypatch.setattr(
        row_resolver,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0),
    )
    analytics = Analytics()
    replayed = row_resolver._replay_historical_trace_filter_witnesses(
        analytics,
        builder=Builder(),
        rows=rows,
        phase_one_query_count=1,
        read_started=0.0,
        ui_filters=[_attribute_filter("final_status", "Rejected")],
        project_id=PROJECT_ID,
        witness_batch_size=10,
        witness_wall_ms_per_query=3_500,
        witness_read_settings={
            **row_resolver._EVAL_TASK_FILTER_CLASSIFY_READ_SETTINGS,
            **row_resolver._EVAL_TASK_TRACE_WITNESS_EXTRA_READ_SETTINGS,
        },
        max_query_count=10_001,
        total_deadline_seconds=150 * 60,
        aggregate_deadline_only=True,
    )

    assert analytics.calls == 10_000
    assert len(replayed) == 100_000
    assert replayed[0]["trace_id"] == "trace-000000"
    assert replayed[-1]["trace_id"] == "trace-099999"


@pytest.mark.parametrize(
    ("phase_one_query_count", "max_query_count", "max_witness_queries"),
    [(0, 128, 1), (1, 2, 10_001)],
    ids=["witness-query-cap", "combined-query-cap"],
)
def test_historical_witness_static_query_caps_are_deterministic_rejections(
    monkeypatch: pytest.MonkeyPatch,
    phase_one_query_count: int,
    max_query_count: int,
    max_witness_queries: int,
) -> None:
    rows = [
        {
            "trace_id": f"trace-{index}",
            "root_span_id": f"root-{index}",
            "start_time": END - timedelta(minutes=1),
        }
        for index in range(2)
    ]

    class Analytics:
        def execute_ch_query(self, *_args, **_kwargs):
            raise AssertionError("static cap must reject before the first read")

    monkeypatch.setattr(
        row_resolver,
        "_EVAL_TASK_WORKFLOW_MAX_WITNESS_QUERIES",
        max_witness_queries,
    )

    with pytest.raises(
        row_resolver.EvalTaskSelectionRejected,
        match="Narrow the time range",
    ):
        row_resolver._replay_historical_trace_filter_witnesses(
            Analytics(),
            builder=object(),
            rows=rows,
            phase_one_query_count=phase_one_query_count,
            read_started=0.0,
            ui_filters=[_attribute_filter("final_status", "Rejected")],
            project_id=PROJECT_ID,
            witness_batch_size=1,
            witness_wall_ms_per_query=500,
            witness_read_settings={},
            max_query_count=max_query_count,
            total_deadline_seconds=120.0,
        )


def test_historical_witness_builder_value_error_is_deterministic_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    witness_start = END - timedelta(minutes=1)

    class Builder:
        @staticmethod
        def build_filter_match_query(*_args, **_kwargs):
            raise ValueError("invalid witness builder contract")

    class Analytics:
        def execute_ch_query(self, *_args, **_kwargs):
            raise AssertionError("invalid builder must reject before a CH read")

    monkeypatch.setattr(
        row_resolver,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0),
    )

    with pytest.raises(
        row_resolver.EvalTaskSelectionRejected,
        match="cannot be resolved safely",
    ):
        row_resolver._replay_historical_trace_filter_witnesses(
            Analytics(),
            builder=Builder(),
            rows=[
                {
                    "trace_id": "trace-a",
                    "root_span_id": "root-a",
                    "start_time": witness_start,
                }
            ],
            phase_one_query_count=1,
            read_started=0.0,
            ui_filters=[_attribute_filter("final_status", "Rejected")],
            project_id=PROJECT_ID,
            witness_batch_size=10,
            witness_wall_ms_per_query=500,
            witness_read_settings={},
            total_deadline_seconds=120.0,
        )


def test_historical_bounded_reader_value_error_is_deterministic_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_read(**_kwargs):
        raise ValueError("invalid bounded reader contract")

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
        invalid_read,
    )

    with pytest.raises(
        row_resolver.EvalTaskSelectionRejected,
        match="cannot be resolved safely",
    ):
        row_resolver._resolve_bounded_historical_span_ids(
            object(),
            sql=None,
            params=None,
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "filters": [_attribute_filter("final_status", "Rejected")],
                "date_range": [START, END],
            },
            limit=25,
            batch_size=25,
            row_type=RowType.SPANS,
        )


def test_trace_eval_witness_routes_to_workflow_before_interactive_replay_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_read(**kwargs):
        assert kwargs["max_query_count"] == 32_768
        assert kwargs["workflow_exact"] is True
        assert kwargs["deadline_ms"] == 170 * 60 * 1000
        assert kwargs["classify_batch_size"] == 10
        assert kwargs["builder"]._bounded_include_filter_witnesses is True
        assert "max_rows_to_read" not in kwargs["read_settings"]
        assert "max_block_size" not in kwargs["read_settings"]
        assert "preferred_max_column_in_block_size_bytes" not in kwargs["read_settings"]
        assert kwargs["classify_read_settings"] == {
            "max_block_size": 2_048,
            "preferred_max_column_in_block_size_bytes": 1_048_576,
        }
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=0,
            elapsed_ms=1,
            query_count=1,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    result = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={
            "filters": [_attribute_filter("final_status", "Rejected")],
            "date_range": [START, END],
        },
        limit=2_000,
        batch_size=2_000,
        row_type=RowType.TRACES,
        include_trace_filter_witnesses=True,
    )

    assert result.ids == ()


@pytest.mark.parametrize(
    ("limit", "rejected"),
    [(100_000, False), (100_010, True)],
)
def test_workflow_one_phase_witness_bound_includes_exact_has_more_sentinel(
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
    rejected: bool,
) -> None:
    calls = 0

    def fake_read(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["workflow_exact"] is True
        assert kwargs["classify_batch_size"] == 10
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=0,
            elapsed_ms=1,
            query_count=1,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )
    kwargs = {
        "analytics": object(),
        "sql": None,
        "params": None,
        "project_id": PROJECT_ID,
        "salt": "task-salt",
        "sampling_rate": 100.0,
        "filters": {
            "filters": [_attribute_filter("final_status", "Rejected")],
            "date_range": [START, END],
        },
        "limit": limit,
        "batch_size": limit,
        "row_type": RowType.TRACES,
        "include_trace_filter_witnesses": True,
    }

    if rejected:
        with pytest.raises(
            row_resolver.EvalTaskSelectionRejected,
            match="Narrow the time range",
        ):
            row_resolver._resolve_bounded_historical_span_ids(**kwargs)
        assert calls == 0
    else:
        result = row_resolver._resolve_bounded_historical_span_ids(**kwargs)
        assert result.ids == ()
        assert calls == 1


@pytest.mark.parametrize(
    "malformed_field",
    ["root_span_id", "filter_witness_0", "project_id"],
)
def test_workflow_one_phase_trace_witnesses_fail_closed_on_malformed_rows(
    monkeypatch: pytest.MonkeyPatch,
    malformed_field: str,
) -> None:
    witness_start = END - timedelta(minutes=1)
    row = {
        "project_id": PROJECT_ID,
        "trace_id": "trace-a",
        "root_span_id": "root-a",
        "start_time": witness_start,
        "filter_witness_0": ("span-status", witness_start),
    }
    if malformed_field == "project_id":
        row[malformed_field] = "another-project"
    else:
        row.pop(malformed_field)

    def fake_read(**kwargs):
        assert kwargs["workflow_exact"] is True
        assert kwargs["builder"]._bounded_include_filter_witnesses is True
        return BoundedFilterPage(
            rows=[row],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=1,
            elapsed_ms=1,
            query_count=2,
            rows_returned=1,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    with pytest.raises(
        row_resolver.EvalTaskReadBudgetExceeded,
        match="Narrow the time range",
    ):
        row_resolver._resolve_bounded_historical_span_ids(
            object(),
            sql=None,
            params=None,
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "filters": [_attribute_filter("final_status", "Rejected")],
                "date_range": [START, END],
            },
            limit=41,
            batch_size=41,
            row_type=RowType.TRACES,
            include_trace_filter_witnesses=True,
        )


def test_workflow_one_phase_late_classifier_failure_never_returns_partial_rows() -> (
    None
):
    witness_start = END - timedelta(minutes=1)
    source_rows = {
        f"trace-{index:02d}": {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:02d}",
            "root_span_id": f"root-{index:02d}",
            "start_time": witness_start - timedelta(seconds=index),
        }
        for index in range(15)
    }

    class LateFailureAnalytics:
        classify_calls = 0

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            assert timeout_ms <= 3_000
            assert settings["max_threads"] == 1
            assert settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
            assert settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
            candidate_ids = params.get("candidate_trace_ids")
            if candidate_ids is not None:
                self.classify_calls += 1
                assert "filter_witness_0" in query
                assert len(candidate_ids) <= 10
                if self.classify_calls == 2:
                    raise TimeoutError("simulated late workflow classifier timeout")
                rows = [
                    {
                        **source_rows[trace_id],
                        "filter_witness_0": (
                            f"span-{trace_id}",
                            source_rows[trace_id]["start_time"],
                        ),
                    }
                    for trace_id in candidate_ids
                ]
                return QueryResult(rows, len(rows), "clickhouse", 1.0)

            rows = [
                row
                for row in source_rows.values()
                if params["filter_slice_start"]
                <= row["start_time"]
                < params["filter_slice_end"]
            ]
            return QueryResult(rows, len(rows), "clickhouse", 1.0)

    analytics = LateFailureAnalytics()
    with pytest.raises(
        row_resolver.EvalTaskReadBudgetExceeded,
        match="Narrow the time range",
    ):
        row_resolver._resolve_bounded_historical_span_ids(
            analytics,
            sql=None,
            params=None,
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "filters": [_attribute_filter("final_status", "Rejected")],
                "date_range": [START, END],
            },
            limit=41,
            batch_size=41,
            row_type=RowType.TRACES,
            include_trace_filter_witnesses=True,
        )
    assert analytics.classify_calls == 2


def test_trace_eval_witness_replay_never_returns_a_partial_second_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "trace_id": f"trace-{index:02d}",
            "root_span_id": f"root-{index:02d}",
            "start_time": END - timedelta(seconds=index),
        }
        for index in range(15)
    ]

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
        lambda **_kwargs: BoundedFilterPage(
            rows=rows,
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=len(rows),
            elapsed_ms=1,
            query_count=2,
            rows_returned=len(rows),
            result_payload_bytes=500,
            attempts=(),
        ),
    )

    class SecondBatchFails:
        calls = 0

        def execute_ch_query(self, _query, params, *, timeout_ms, settings):
            self.calls += 1
            assert len(params["candidate_trace_ids"]) <= 10
            assert timeout_ms == 3_000
            assert settings["max_block_size"] == 2_048
            assert settings["preferred_max_column_in_block_size_bytes"] == 1_048_576
            assert settings["max_result_rows"] == len(params["candidate_trace_ids"])
            if self.calls == 2:
                raise ValueError("simulated bounded replay failure")
            replayed = [
                {
                    **rows[index],
                    "filter_witness_0": (
                        f"span-{trace_id}",
                        rows[index]["start_time"],
                    ),
                }
                for index, trace_id in enumerate(params["candidate_trace_ids"])
            ]
            return QueryResult(replayed, len(replayed), "clickhouse", 1.0)

    analytics = SecondBatchFails()
    with pytest.raises(
        row_resolver.EvalTaskSelectionRejected,
        match="cannot be resolved safely",
    ):
        row_resolver._resolve_bounded_historical_span_ids(
            analytics,
            sql=None,
            params=None,
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "filters": [_attribute_filter("final_status", "Rejected")],
                "date_range": [START, END],
            },
            limit=15,
            batch_size=15,
            row_type=RowType.TRACES,
            include_trace_filter_witnesses=True,
        )
    assert analytics.calls == 2


@pytest.mark.parametrize("drift_field", ["root_span_id", "start_time"])
def test_trace_eval_witness_replay_fails_closed_on_canonical_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
    drift_field: str,
) -> None:
    membership_row = {
        "trace_id": "trace-a",
        "root_span_id": "root-a",
        "start_time": END - timedelta(minutes=1),
    }

    def fake_read(**_kwargs):
        return BoundedFilterPage(
            rows=[membership_row],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=1,
            elapsed_ms=1,
            query_count=2,
            rows_returned=1,
            result_payload_bytes=20,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    class DriftAnalytics:
        def execute_ch_query(self, _query, _params, **_kwargs):
            replayed = {
                **membership_row,
                "filter_witness_0": ("span-status", membership_row["start_time"]),
            }
            replayed[drift_field] = (
                "replacement-root"
                if drift_field == "root_span_id"
                else membership_row["start_time"] - timedelta(microseconds=1)
            )
            return QueryResult([replayed], 1, "clickhouse", 1.0)

    with pytest.raises(
        row_resolver.EvalTaskReadBudgetExceeded,
        match="Narrow the time range",
    ):
        row_resolver._resolve_bounded_historical_span_ids(
            DriftAnalytics(),
            sql=None,
            params=None,
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "filters": [_attribute_filter("final_status", "Rejected")],
                "date_range": [START, END],
            },
            limit=25,
            batch_size=25,
            row_type=RowType.TRACES,
            include_trace_filter_witnesses=True,
        )


def test_trace_legacy_observation_type_is_root_scoped_before_cap() -> None:
    filters = row_resolver._task_ui_filters(
        {
            "date_range": [START, END],
            "observation_type": ["llm"],
        },
        row_type=RowType.TRACES,
        bounded_trace_root=True,
    )
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_sampling_salt="task-salt",
        bounded_sampling_rate=100.0,
    )

    seed_sql, seed_params = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=25,
    )
    match_sql, _ = builder.build_filter_match_query(["trace-a"])

    assert "observation_type" in seed_sql
    assert seed_params["latest_filter_param_0"] == ("llm",)
    assert "argMax(observation_type" in match_sql
    assert "SELECT latest_trace_id" not in match_sql


def test_public_internal_root_col_type_cannot_change_trace_semantics() -> None:
    filters = [
        _time_filter(),
        {
            "column_id": "observation_type",
            "filter_config": {
                "col_type": "INTERNAL_ROOT_METRIC",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ["llm"],
            },
        },
    ]

    sql, _ = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
    ).build_filter_match_query(["trace-a"])

    # Without the private marker created only by the eval-task normalizer, the
    # regular public ANY-SPAN observation_type contract remains in force.
    assert "countIf(" in sql
    assert "latest_column_value_0" in sql
    assert "SELECT latest_trace_id" not in sql


@pytest.mark.parametrize("key", ["filters", "span_attributes_filters"])
def test_malformed_saved_filter_entries_fail_closed(key: str) -> None:
    with pytest.raises(ValueError, match="entries must be objects"):
        row_resolver._task_ui_filters({key: ["not-an-object"]})


@pytest.mark.parametrize("filters", ["", [], 0, False])
def test_malformed_falsy_task_filter_wrapper_fails_closed(filters) -> None:
    with pytest.raises(ValueError, match="task filters must be an object"):
        row_resolver._task_ui_filters(filters)
    with pytest.raises(ValueError, match="task filters must be an object"):
        row_resolver._build_sample_query(
            project_id=PROJECT_ID,
            row_type=RowType.SPANS,
            salt="task-salt",
            sampling_rate=100.0,
            filters=filters,
            limit=25,
        )


@pytest.mark.parametrize(
    "filters",
    [
        {"filters": ""},
        {"span_attributes_filters": {}},
        {"date_range": []},
        {"date_range": [START, ""]},
    ],
)
def test_malformed_falsy_saved_filter_fields_fail_closed(filters: dict) -> None:
    with pytest.raises(ValueError):
        row_resolver._task_ui_filters(filters)
    with pytest.raises(ValueError):
        row_resolver._build_sample_query(
            project_id=PROJECT_ID,
            row_type=RowType.SPANS,
            salt="task-salt",
            sampling_rate=100.0,
            filters=filters,
            limit=25,
        )


def test_task_over_10k_proves_population_without_legacy_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def must_not_compile(**_kwargs):
        raise AssertionError("legacy selector must not be compiled")

    def fake_resolve(_analytics, **kwargs):
        captured.update(kwargs)
        return ["selected-id"]

    monkeypatch.setattr(row_resolver, "_build_sample_query", must_not_compile)
    monkeypatch.setattr(
        row_resolver,
        "_resolve_bounded_historical_span_ids",
        fake_resolve,
    )
    task = SimpleNamespace(
        spans_limit=10_001,
        run_type=RunType.HISTORICAL,
        row_type=RowType.SPANS,
        sampling_rate=100.0,
        project_id=PROJECT_ID,
        id="task-id",
        filters={},
        continuous_cursor=None,
        start_time=None,
        created_at=START,
    )

    assert list(row_resolver.iter_desired_rows(task)) == [["selected-id"]]
    assert captured["sql"] is None
    assert captured["params"] is None
    assert captured["limit"] == 10_001


def test_task_over_10k_accepts_exact_newest_prefix_with_more_rows_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[
                {
                    "id": "must-not-escape",
                    "project_id": PROJECT_ID,
                    "trace_id": "trace-a",
                    "start_time": END,
                }
            ],
            has_more=True,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=10_001,
            elapsed_ms=10,
            query_count=80,
            rows_returned=10_001,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    assert row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={"date_range": [START, END]},
        limit=10_001,
        batch_size=256,
        row_type=RowType.SPANS,
    ) == ["must-not-escape"]

    assert captured["page_size"] == 10_001
    assert captured["workflow_exact"] is True


def test_configured_1m_task_returns_complete_small_population_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        return BoundedFilterPage(
            rows=[
                {
                    "id": "span-b",
                    "project_id": PROJECT_ID,
                    "trace_id": "trace-b",
                    "start_time": END,
                },
                {
                    "id": "span-a",
                    "project_id": PROJECT_ID,
                    "trace_id": "trace-a",
                    "start_time": END - timedelta(seconds=1),
                },
            ],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=2,
            elapsed_ms=10,
            query_count=2,
            rows_returned=2,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    ids = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql=None,
        params=None,
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={"date_range": [START, END]},
        limit=1_000_000,
        batch_size=256,
        row_type=RowType.SPANS,
    )

    assert ids == ["span-b", "span-a"]
    assert captured["page_size"] == 1_000_000
    assert captured["workflow_exact"] is True


@pytest.mark.parametrize(
    "row_type",
    [RowType.SPANS, RowType.TRACES, RowType.SESSIONS, RowType.VOICE_CALLS],
)
def test_task_at_10k_routes_directly_to_bounded_selector_without_legacy_sql(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
) -> None:
    captured: dict = {}

    def must_not_compile(**_kwargs):
        raise AssertionError("legacy selector must not be compiled")

    def fake_resolve(_analytics, **kwargs):
        captured.update(kwargs)
        return ["selected-id"]

    monkeypatch.setattr(row_resolver, "_build_sample_query", must_not_compile)
    monkeypatch.setattr(
        row_resolver,
        "_resolve_bounded_historical_span_ids",
        fake_resolve,
    )
    task = SimpleNamespace(
        spans_limit=10_000,
        run_type=RunType.HISTORICAL,
        row_type=row_type,
        sampling_rate=100.0,
        project_id=PROJECT_ID,
        id="task-id",
        filters={
            "date_range": [START, END],
            "filters": [_attribute_filter("final_status", "Rejected")],
        },
        continuous_cursor=None,
        start_time=None,
        created_at=START,
    )

    assert list(row_resolver.iter_desired_rows(task)) == [["selected-id"]]
    assert captured["sql"] is None
    assert captured["params"] is None
    assert captured["limit"] == 10_000


@pytest.mark.parametrize(
    ("row_type", "include_witnesses", "interactive_limit", "workflow_limit"),
    [
        (RowType.SESSIONS, False, 5_799, 5_800),
        (RowType.VOICE_CALLS, False, 1_249, 1_250),
        (RowType.TRACES, True, 40, 41),
    ],
)
def test_eval_task_switches_envelope_at_exact_mechanical_query_boundary(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
    include_witnesses: bool,
    interactive_limit: int,
    workflow_limit: int,
) -> None:
    calls: list[dict] = []

    def fake_read(**kwargs):
        calls.append(kwargs)
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=0,
            elapsed_ms=1,
            query_count=2,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )
    task_filters = {
        "date_range": [START, END],
        "filters": [_attribute_filter("final_status", "Rejected")],
    }

    for limit in (interactive_limit, workflow_limit):
        result = row_resolver._resolve_bounded_historical_span_ids(
            object(),
            sql=None,
            params=None,
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters=task_filters,
            limit=limit,
            batch_size=256,
            row_type=row_type,
            include_trace_filter_witnesses=include_witnesses,
        )
        assert (result.ids if include_witnesses else tuple(result)) == ()

    assert calls[0]["workflow_exact"] is False
    assert calls[0]["max_query_count"] == (112 if row_type == RowType.TRACES else 128)
    assert calls[1]["workflow_exact"] is True
    assert calls[1]["max_query_count"] == 32_768
    assert calls[1]["max_seed_attempts"] == 16_384


def test_time_only_limit_transition_preserves_newest_first_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    ordered_rows = [
        {
            "id": f"span-{index}",
            "trace_id": f"trace-{index}",
            "start_time": END - timedelta(seconds=index),
        }
        for index in range(4)
    ]

    def fake_read(**kwargs):
        calls.append(kwargs)
        row_count = 3 if kwargs["page_size"] == 9_999 else 4
        return BoundedFilterPage(
            rows=ordered_rows[:row_count],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=row_count,
            elapsed_ms=1,
            query_count=2,
            rows_returned=row_count,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )
    common = {
        "sql": "must-not-run-legacy-id-order",
        "params": {"start_date": START, "end_date": END},
        "project_id": PROJECT_ID,
        "salt": "task-salt",
        "sampling_rate": 100.0,
        "filters": {"date_range": [START, END]},
        "batch_size": 256,
        "row_type": RowType.SPANS,
    }

    first = row_resolver._resolve_bounded_historical_span_ids(
        object(), limit=9_999, **common
    )
    second = row_resolver._resolve_bounded_historical_span_ids(
        object(), limit=10_000, **common
    )

    assert first == second[: len(first)]
    assert calls[0]["workflow_exact"] is False
    assert calls[1]["workflow_exact"] is True


@pytest.mark.parametrize(
    ("error_code", "error_type"),
    [
        ("deadline_exceeded", row_resolver.EvalTaskReadBudgetExceeded),
        ("read_budget_exceeded", row_resolver.EvalTaskReadBudgetExceeded),
        ("classification_drift", row_resolver.EvalTaskReadBudgetExceeded),
        ("query_budget_exceeded", row_resolver.EvalTaskSelectionRejected),
        ("scan_budget_exceeded", row_resolver.EvalTaskSelectionRejected),
    ],
)
def test_bounded_resolver_rejects_incomplete_page_without_partial_ids(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
    error_type: type[Exception],
) -> None:
    def fake_read(**_kwargs):
        return BoundedFilterPage(
            rows=[{"id": "must-not-escape", "start_time": END}],
            has_more=False,
            complete=False,
            status="degraded",
            error_code=error_code,
            total_rows_lower_bound=1,
            elapsed_ms=4500,
            query_count=12,
            rows_returned=1,
            result_payload_bytes=10,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    with pytest.raises(error_type, match="Narrow the time range") as captured:
        row_resolver._resolve_bounded_historical_span_ids(
            object(),
            sql="baseline-protocol-sql",
            params={"start_date": START, "end_date": END},
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "filters": [_attribute_filter("final_status", "Rejected")],
                "date_range": [START, END],
            },
            limit=25,
            batch_size=256,
            row_type=RowType.SPANS,
        )
    assert type(captured.value) is error_type


def test_bounded_resolver_sanitizes_plain_timeout_without_partial_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_read(**_kwargs):
        raise TimeoutError("private selector timing diagnostic")

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    with pytest.raises(row_resolver.EvalTaskReadBudgetExceeded) as exc_info:
        row_resolver._resolve_bounded_historical_span_ids(
            object(),
            sql=None,
            params=None,
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "filters": [_attribute_filter("final_status", "Rejected")],
                "date_range": [START, END],
            },
            limit=25,
            batch_size=256,
            row_type=RowType.TRACES,
        )

    assert str(exc_info.value) == row_resolver._SAFE_READ_BUDGET_MESSAGE
    assert "private selector" not in str(exc_info.value)


def test_bounded_span_resolver_rejects_cross_trace_id_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_read(**_kwargs):
        return BoundedFilterPage(
            rows=[
                {"id": "shared", "trace_id": "trace-a", "start_time": END},
                {
                    "id": "shared",
                    "trace_id": "trace-b",
                    "start_time": END - timedelta(seconds=1),
                },
            ],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=2,
            elapsed_ms=10,
            query_count=2,
            rows_returned=4,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    with pytest.raises(
        row_resolver.EvalTaskReadBudgetExceeded,
        match="could not safely distinguish",
    ):
        row_resolver._resolve_bounded_historical_span_ids(
            object(),
            sql="baseline-protocol-sql",
            params={"start_date": START, "end_date": END},
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "filters": [_attribute_filter("final_status", "Rejected")],
                "date_range": [START, END],
            },
            limit=25,
            batch_size=256,
            row_type=RowType.SPANS,
        )


def test_bounded_span_resolver_rejects_same_trace_distinct_physical_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_read(**_kwargs):
        return BoundedFilterPage(
            rows=[
                {"id": "shared", "trace_id": "trace-a", "start_time": END},
                {
                    "id": "shared",
                    "trace_id": "trace-a",
                    "start_time": END - timedelta(seconds=1),
                },
            ],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=2,
            elapsed_ms=10,
            query_count=2,
            rows_returned=4,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    with pytest.raises(
        row_resolver.EvalTaskReadBudgetExceeded,
        match="could not safely distinguish",
    ):
        row_resolver._resolve_bounded_historical_span_ids(
            object(),
            sql="baseline-protocol-sql",
            params={"start_date": START, "end_date": END},
            project_id=PROJECT_ID,
            salt="task-salt",
            sampling_rate=100.0,
            filters={
                "filters": [_attribute_filter("final_status", "Rejected")],
                "date_range": [START, END],
            },
            limit=25,
            batch_size=256,
            row_type=RowType.SPANS,
        )


def test_bounded_span_resolver_dedupes_duplicate_exact_physical_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_read(**_kwargs):
        duplicate = {"id": "shared", "trace_id": "trace-a", "start_time": END}
        return BoundedFilterPage(
            rows=[duplicate, dict(duplicate)],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=1,
            elapsed_ms=10,
            query_count=2,
            rows_returned=2,
            result_payload_bytes=100,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )

    ids = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql="baseline-protocol-sql",
        params={"start_date": START, "end_date": END},
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters={
            "filters": [_attribute_filter("final_status", "Rejected")],
            "date_range": [START, END],
        },
        limit=25,
        batch_size=256,
        row_type=RowType.SPANS,
    )

    assert ids == ["shared"]


@pytest.mark.parametrize("row_type", [RowType.SPANS, RowType.TRACES])
@pytest.mark.parametrize("col_type", ["EVAL_METRIC", "ANNOTATION"])
def test_eval_and_annotation_filters_use_candidate_scoped_bounded_reader(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
    col_type: str,
) -> None:
    captured: dict = {}

    def fake_read(**kwargs):
        captured.update(kwargs)
        identity = "id" if row_type == RowType.SPANS else "trace_id"
        row = {identity: "selected-id", "start_time": END}
        if row_type == RowType.SPANS:
            row.update({"project_id": PROJECT_ID, "trace_id": "trace-a"})
        return BoundedFilterPage(
            rows=[row],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=1,
            elapsed_ms=1,
            query_count=2,
            rows_returned=2,
            result_payload_bytes=20,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", fake_read
    )
    if col_type == "EVAL_METRIC":
        monkeypatch.setattr(
            row_resolver,
            "_eval_filter_metadata_for_filters",
            lambda _project_id, _filters: {
                "00000000-0000-4000-8000-000000000099": EvalFilterMetadata(
                    ("00000000-0000-4000-8000-000000000100",),
                    "SCORE",
                )
            },
        )
    filters = {
        "filters": [
            {
                "column_id": "00000000-0000-4000-8000-000000000099",
                "filter_config": {
                    "col_type": col_type,
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0.5,
                },
            }
        ],
        "date_range": [START, END],
    }

    ids = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql="SELECT exact_legacy_id",
        params={"start_date": START, "end_date": END},
        project_id=PROJECT_ID,
        salt="task-salt",
        sampling_rate=100.0,
        filters=filters,
        limit=25,
        batch_size=17,
        row_type=row_type,
    )

    assert ids == ["selected-id"]
    builder = captured["builder"]
    assert builder.supports_bounded_filter_scan() is True
    assert captured["max_candidates"] == 512
    assert captured["classify_batch_size"] == 200


@pytest.mark.parametrize("row_type", [RowType.SPANS, RowType.TRACES])
@pytest.mark.parametrize("limit", [5_100, 10_000])
def test_shared_candidate_reader_proves_large_eval_prefix_within_query_cap(
    row_type: str,
    limit: int,
) -> None:
    identity_key = "id" if row_type == RowType.SPANS else "trace_id"
    started_at = END - timedelta(minutes=1)
    rows = []
    for index in range(limit + 1):
        identity = f"row-{index:05d}"
        row = {identity_key: identity, "start_time": started_at}
        if row_type == RowType.SPANS:
            row.update(
                {
                    "project_id": PROJECT_ID,
                    "trace_id": f"trace-{index:05d}",
                }
            )
        rows.append(row)

    class SyntheticBuilder:
        def parse_time_range(self, _filters):
            return START, END

        @staticmethod
        def filter_seed_proves_result_order():
            return True

        @staticmethod
        def recommended_filter_classify_batch_size():
            return 200

        @staticmethod
        def bounded_filter_row_identity(row):
            if row_type == RowType.SPANS:
                return (
                    row["project_id"],
                    row["trace_id"],
                    row["id"],
                    row["start_time"],
                )
            return row["trace_id"]

        @staticmethod
        def bounded_filter_row_order_token(row):
            if row_type == RowType.SPANS:
                return (row["id"], row["trace_id"], row["project_id"])
            return row["trace_id"]

        def build_filter_seed_page(
            self,
            *,
            slice_start,
            slice_end,
            limit,
            before_start_time=None,
            before_id=None,
        ):
            return "seed", {
                "slice_start": slice_start,
                "slice_end": slice_end,
                "limit": limit,
                "before_start_time": before_start_time,
                "before_id": before_id,
            }

        @staticmethod
        def build_filter_match_query_from_seed_rows(candidate_rows):
            return "classify", {"candidate_rows": candidate_rows}

    def row_key(row):
        return row["start_time"], SyntheticBuilder.bounded_filter_row_order_token(row)

    class SyntheticAnalytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            assert timeout_ms <= django_settings.FILTER_SELECTOR_QUERY_TIMEOUT_MS
            if query == "classify":
                assert settings["max_result_rows"] == 200
                result_rows = list(params["candidate_rows"])
            else:
                assert 200 <= settings["max_result_rows"] <= 512
                result_rows = [
                    row
                    for row in rows
                    if params["slice_start"] <= row["start_time"] < params["slice_end"]
                ]
                before_start = params["before_start_time"]
                if before_start is not None:
                    result_rows = [
                        row
                        for row in result_rows
                        if row_key(row) < (before_start, params["before_id"])
                    ]
                result_rows = sorted(result_rows, key=row_key, reverse=True)[
                    : params["limit"]
                ]
            return QueryResult(result_rows, len(result_rows), "clickhouse", 0.0)

    page = read_bounded_filter_page(
        builder=SyntheticBuilder(),
        analytics=SyntheticAnalytics(),
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
        key_field=identity_key,
        page_number=0,
        page_size=limit,
        deadline_ms=10_000,
        max_seed_attempts=128,
        max_candidates=200,
        max_query_count=128,
        classify_batch_size=200,
    )

    assert page.complete is True
    assert len(page.rows) == limit
    assert page.has_more is True
    assert page.query_count <= 102


def test_workflow_reader_proves_exact_100k_same_timestamp_prefix() -> None:
    """The advertised 100k task size is a real executable contract.

    All rows deliberately share one timestamp, so the stable identity token is
    the only ordering boundary across 512-row keyset pages. The 100001st row is
    an exact has-more sentinel and must never displace the selected prefix.
    """

    started_at = END - timedelta(minutes=1)
    rows = [
        {
            "id": f"span-{index:06d}",
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:06d}",
            "start_time": started_at,
        }
        for index in range(100_000, -1, -1)
    ]
    row_index = {row["id"]: index for index, row in enumerate(rows)}

    class WorkflowBuilder:
        @staticmethod
        def parse_time_range(_filters):
            return START, END

        @staticmethod
        def filter_seed_proves_result_order():
            return True

        @staticmethod
        def recommended_filter_seed_batch_size():
            return 512

        @staticmethod
        def recommended_filter_classify_batch_size():
            return 200

        @staticmethod
        def bounded_filter_row_identity(row):
            return row["project_id"], row["trace_id"], row["id"], row["start_time"]

        @staticmethod
        def bounded_filter_row_order_token(row):
            return row["id"]

        bounded_filter_seed_identity = bounded_filter_row_identity
        bounded_filter_seed_order_token = bounded_filter_row_order_token

        @staticmethod
        def build_filter_seed_page(
            *,
            slice_start,
            slice_end,
            limit,
            before_start_time=None,
            before_id=None,
        ):
            return "seed", {
                "slice_start": slice_start,
                "slice_end": slice_end,
                "limit": limit,
                "before_start_time": before_start_time,
                "before_id": before_id,
            }

        @staticmethod
        def build_filter_match_query_from_seed_rows(candidate_rows):
            return "classify", {"candidate_rows": candidate_rows}

    class WorkflowAnalytics:
        calls = 0

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls += 1
            assert timeout_ms <= django_settings.FILTER_SELECTOR_QUERY_TIMEOUT_MS
            assert (
                settings["max_threads"] == django_settings.FILTER_SELECTOR_MAX_THREADS
            )
            assert settings["max_result_rows"] <= 512
            if query == "classify":
                return QueryResult(
                    list(params["candidate_rows"]),
                    len(params["candidate_rows"]),
                    "clickhouse",
                    0.0,
                )

            if not (params["slice_start"] <= started_at < params["slice_end"]):
                result_rows = []
            else:
                before_id = params["before_id"]
                start_index = 0 if before_id is None else row_index[before_id] + 1
                result_rows = rows[start_index : start_index + params["limit"]]
            return QueryResult(
                result_rows,
                len(result_rows),
                "clickhouse",
                0.0,
            )

    analytics = WorkflowAnalytics()
    page = read_bounded_filter_page(
        builder=WorkflowBuilder(),
        analytics=analytics,
        filters=[_time_filter(), _attribute_filter("final_status", "Rejected")],
        key_field="id",
        page_number=0,
        page_size=100_000,
        deadline_ms=75 * 60 * 1000,
        max_seed_attempts=4_096,
        max_candidates=512,
        max_query_count=32_768,
        classify_batch_size=200,
        workflow_exact=True,
    )

    assert page.complete is True
    assert page.has_more is True
    assert len(page.rows) == 100_000
    assert page.rows[0]["id"] == "span-100000"
    assert page.rows[-1]["id"] == "span-000001"
    assert all(row["id"] != "span-000000" for row in page.rows)
    assert page.query_count == analytics.calls
    assert page.query_count < 1_000


def test_workflow_query_envelope_is_not_available_to_http_reads() -> None:
    with pytest.raises(ValueError, match="max_query_count"):
        read_bounded_filter_page(
            builder=object(),
            analytics=object(),
            filters=[],
            key_field="id",
            page_number=0,
            page_size=25,
            max_seed_attempts=128,
            max_query_count=129,
        )


def test_population_proof_buffers_dense_10k_sentinel_within_128_queries() -> None:
    started_at = END - timedelta(minutes=1)
    rows = [
        {"trace_id": f"trace-{index:05d}", "start_time": started_at}
        for index in range(10_001)
    ]

    class PopulationBuilder:
        @staticmethod
        def parse_time_range(_filters):
            return END - timedelta(minutes=5), END

        @staticmethod
        def filter_seed_proves_result_order():
            return False

        @staticmethod
        def filter_seed_proves_population_bound():
            return True

        @staticmethod
        def recommended_filter_classify_batch_size():
            return 100

        @staticmethod
        def recommended_filter_seed_batch_size():
            return 512

        @staticmethod
        def bounded_filter_row_identity(row):
            return row["trace_id"]

        @staticmethod
        def bounded_filter_row_order_token(row):
            return row["trace_id"]

        bounded_filter_seed_identity = bounded_filter_row_identity
        bounded_filter_seed_order_token = bounded_filter_row_order_token

        @staticmethod
        def build_filter_seed_page(
            *,
            slice_start,
            slice_end,
            limit,
            before_start_time=None,
            before_id=None,
        ):
            return "direct_seed", {
                "slice_start": slice_start,
                "slice_end": slice_end,
                "limit": limit,
                "before_start_time": before_start_time,
                "before_id": before_id,
            }

        @staticmethod
        def build_filter_match_query_from_seed_rows(candidate_rows):
            return "classify_with_witness", {"candidate_rows": candidate_rows}

    class PopulationAnalytics:
        calls: list[str] = []

        def execute_ch_query(self, query, params, **_kwargs):
            self.calls.append(query)
            if query == "classify_with_witness":
                result_rows = list(params["candidate_rows"])
            else:
                result_rows = sorted(
                    rows,
                    key=lambda row: (row["start_time"], row["trace_id"]),
                    reverse=True,
                )
                if params["before_start_time"] is not None:
                    boundary = params["before_start_time"], params["before_id"]
                    result_rows = [
                        row
                        for row in result_rows
                        if (row["start_time"], row["trace_id"]) < boundary
                    ]
                result_rows = result_rows[: params["limit"]]
            return QueryResult(
                result_rows,
                len(result_rows),
                "clickhouse",
                0.0,
            )

    analytics = PopulationAnalytics()
    page = read_bounded_filter_page(
        builder=PopulationBuilder(),
        analytics=analytics,
        filters=[
            _time_filter(),
            _attribute_filter("final_status", "Rejected"),
        ],
        key_field="trace_id",
        page_number=0,
        page_size=10_000,
        deadline_ms=60_000,
        max_seed_attempts=128,
        max_candidates=512,
        max_query_count=128,
        classify_batch_size=100,
    )

    assert page.complete is True
    assert page.has_more is True
    assert len(page.rows) == 10_000
    assert analytics.calls.count("direct_seed") == 20
    assert analytics.calls.count("classify_with_witness") == 101
    assert page.query_count == 121


@pytest.mark.parametrize(
    ("builder_class", "supports", "targets"),
    [
        (SpanListQueryBuilder, supports_span_filters, targets_span_filter_domain),
        (TraceListQueryBuilder, supports_trace_filters, targets_trace_filter_domain),
    ],
)
def test_legacy_system_metric_alias_uses_its_denormalized_latest_column(
    builder_class,
    supports,
    targets,
) -> None:
    # ``tokens`` is a legacy SYSTEM_METRIC alias for the total_tokens column.
    # Candidate classification must keep that mapping rather than reading a
    # same-named custom attribute or forcing a broad compatibility scan.
    filters = [
        _time_filter(),
        {
            "column_id": "tokens",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 10,
            },
        },
    ]

    assert supports(filters) is True
    assert targets(filters) is True
    builder = builder_class(project_id=PROJECT_ID, filters=filters)
    assert builder.bounded_filter_degraded_error_code() is None
    assert builder.supports_bounded_filter_scan() is True
    sql, _ = builder.build_filter_match_query(["candidate-id"])
    assert "argMax(tuple(total_tokens), _peerdb_version).1" in sql
    assert "latest_filter_key" not in sql
