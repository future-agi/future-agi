"""Live CH25 proof for bounded bulk IDs and exact trace-root hydration."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver import Client
from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

from model_hub.services.bulk_selection import (
    BulkSelectionAmbiguousIdentity,
    _resolve_span_ids_clickhouse,
)

pytestmark = pytest.mark.integration

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_NATIVE_PORT = int(os.environ.get("CH25_NATIVE_PORT", "19000"))
CH_USER = os.environ.get("CH25_USER", "default")
CH_PASSWORD = os.environ.get("CH25_PASSWORD", "")


@pytest.fixture(scope="module")
def ch_client():
    client = Client(
        host=CH_HOST,
        port=CH_NATIVE_PORT,
        user=CH_USER,
        password=CH_PASSWORD,
        connect_timeout=3,
    )
    try:
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"CH25 is not reachable on {CH_HOST}:{CH_NATIVE_PORT} ({exc!r})")
    return client


@pytest.fixture()
def spans_table(ch_client):
    table = f"_test_bulk_bounded_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            id String,
            project_id UUID,
            project_version_id Nullable(UUID),
            trace_id String,
            parent_span_id Nullable(String),
            trace_name String,
            trace_session_id Nullable(String),
            name String,
            service_name String DEFAULT '',
            observation_type String,
            status Nullable(String),
            start_time DateTime64(6, 'UTC'),
            end_time Nullable(DateTime64(6, 'UTC')),
            latency_ms Nullable(Float64),
            cost Nullable(Float64),
            total_tokens Nullable(Int64),
            prompt_tokens Nullable(Int64),
            completion_tokens Nullable(Int64),
            model Nullable(String),
            provider Nullable(String),
            end_user_id Nullable(UUID),
            created_at DateTime64(6, 'UTC'),
            input String,
            output String,
            attrs_string Map(String, String),
            attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8),
            attributes_extra String,
            metadata Map(String, String),
            is_deleted UInt8,
            _version UInt64
        )
        ENGINE = MergeTree
        ORDER BY (project_id, start_time, id, _version)
        """
    )
    try:
        yield table
    finally:
        ch_client.execute(f"DROP TABLE {table}")


def _time_filter(start: datetime, end: datetime) -> dict:
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def _attribute_filter() -> dict:
    return {
        "column_id": "final_status",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "Rejected",
        },
    }


def _row(
    *,
    project_id: str,
    project_version_id: str,
    trace_id: str,
    root_id: str,
    start_time: datetime,
    version: int,
    deleted: int = 0,
    input_value: str = "",
):
    return (
        root_id,
        project_id,
        project_version_id,
        trace_id,
        None,
        trace_id,
        None,
        "root",
        "span",
        None,
        start_time,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        start_time,
        input_value,
        "",
        {"final_status": "Rejected"},
        {},
        {},
        "{}",
        {},
        deleted,
        version,
    )


_INSERT_COLUMNS = """
    (id, project_id, project_version_id, trace_id, parent_span_id, trace_name,
     trace_session_id, name, observation_type, status, start_time, end_time,
     latency_ms, cost, total_tokens, prompt_tokens, completion_tokens, model,
     provider, end_user_id, created_at, input, output, attrs_string,
     attrs_number, attrs_bool, attributes_extra, metadata, is_deleted, _version)
"""


class _ClickHouseExecutor:
    def __init__(self, client):
        self.client = client

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        values, columns = self.client.execute(
            query,
            params,
            with_column_types=True,
            settings={**settings, "max_execution_time": max(1, timeout_ms // 1000)},
        )
        names = [name for name, _ in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in values]
        )


def test_identity_only_trace_classifier_accepts_200_but_rejects_201(
    ch_client, spans_table
):
    project_id = "00000000-0000-4000-8000-000000000001"
    project_version_id = "00000000-0000-4000-8000-000000000011"
    start = datetime(2026, 7, 1, 0, 0)
    rows = [
        _row(
            project_id=project_id,
            project_version_id=project_version_id,
            trace_id=f"trace-{index:03d}",
            root_id=f"root-{index:03d}",
            start_time=start + timedelta(microseconds=index),
            version=1,
        )
        for index in range(201)
    ]
    ch_client.execute(f"INSERT INTO {spans_table} {_INSERT_COLUMNS} VALUES", rows)

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = spans_table

    builder = LocalTraceBuilder(
        project_id=project_id,
        filters=[
            _time_filter(start - timedelta(minutes=1), start + timedelta(minutes=1)),
            _attribute_filter(),
        ],
        bounded_identity_only=True,
        bounded_internal_scan=False,
        bounded_bulk_scan=True,
    )
    trace_ids = [f"trace-{index:03d}" for index in range(200)]
    query, params = builder.build_filter_match_query(trace_ids)
    result = ch_client.execute(
        query,
        params,
        settings={
            "max_execution_time": 10,
            "max_threads": 1,
            "max_memory_usage": 256 * 1024 * 1024,
        },
    )

    assert len(result) == 200
    # The builder accepts a finite 200-identity input, but the production
    # scheduler deliberately chunks any-span replay into the qualified
    # ten-trace memory envelope.
    assert builder.recommended_filter_classify_batch_size() == 10
    assert "project_id = %(project_id)s" in query
    assert "candidate_start_date" in query and "candidate_end_date" in query
    with pytest.raises(ValueError, match="candidate trace batch exceeds bounded limit"):
        builder.build_filter_match_query([*trace_ids, "trace-200"])


def test_content_hydration_preserves_microsecond_root_version_and_tombstone(
    ch_client, spans_table
):
    project_id = "00000000-0000-4000-8000-000000000002"
    selected_version = "00000000-0000-4000-8000-000000000021"
    other_version = "00000000-0000-4000-8000-000000000022"
    selected_time = datetime(2026, 7, 2, 12, 0, 0, 123456)
    same_second_decoy_time = datetime(2026, 7, 2, 12, 0, 0, 654321)
    tombstone_time = datetime(2026, 7, 2, 12, 1, 0, 123456)
    rows = [
        _row(
            project_id=project_id,
            project_version_id=selected_version,
            trace_id="trace-cross-version",
            root_id="selected-root",
            start_time=selected_time,
            version=1,
            input_value="selected-old",
        ),
        _row(
            project_id=project_id,
            project_version_id=selected_version,
            trace_id="trace-cross-version",
            root_id="selected-root",
            start_time=selected_time,
            version=2,
            input_value="selected-latest",
        ),
        _row(
            project_id=project_id,
            project_version_id=selected_version,
            trace_id="trace-cross-version",
            root_id="selected-root",
            start_time=same_second_decoy_time,
            version=3,
            input_value="wrong-microsecond-root",
        ),
        _row(
            project_id=project_id,
            project_version_id=other_version,
            trace_id="trace-cross-version",
            root_id="other-version-root",
            start_time=selected_time + timedelta(minutes=2),
            version=1,
            input_value="wrong-project-version",
        ),
        _row(
            project_id=project_id,
            project_version_id=selected_version,
            trace_id="trace-tombstoned",
            root_id="tombstoned-root",
            start_time=tombstone_time,
            version=1,
            input_value="stale-live-payload",
        ),
        _row(
            project_id=project_id,
            project_version_id=selected_version,
            trace_id="trace-tombstoned",
            root_id="tombstoned-root",
            start_time=tombstone_time,
            version=2,
            deleted=1,
            input_value="tombstone",
        ),
        _row(
            project_id=project_id,
            project_version_id=other_version,
            trace_id="trace-tombstoned",
            root_id="other-version-live-root",
            start_time=tombstone_time + timedelta(minutes=2),
            version=1,
            input_value="wrong-live-fallback",
        ),
    ]
    ch_client.execute(f"INSERT INTO {spans_table} {_INSERT_COLUMNS} VALUES", rows)

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = spans_table

        @staticmethod
        def _trace_tags_select_sql() -> str:
            # This fixture owns only a spans table. Trace-tag enrichment is an
            # independent page-bounded read and is irrelevant to the root
            # identity/version contract under test.
            return "'[]' AS trace_tags"

        @staticmethod
        def _trace_tags_join_sql() -> str:
            return ""

    builder = LocalTraceBuilder(
        project_id=project_id,
        project_version_id=selected_version,
        filters=[
            _time_filter(
                selected_time - timedelta(minutes=1),
                selected_time + timedelta(minutes=5),
            )
        ],
    )
    builder.start_date, builder.end_date = builder.parse_time_range(builder.filters)
    query, params = builder.build_content_query(
        ["trace-cross-version", "trace-tombstoned"],
        root_identities=[
            (project_id, "trace-cross-version", "selected-root", selected_time),
            (project_id, "trace-tombstoned", "tombstoned-root", tombstone_time),
        ],
    )
    result = ch_client.execute(
        query,
        params,
        with_column_types=True,
        settings={"max_execution_time": 10, "max_threads": 1},
    )
    values, columns = result
    column_names = [name for name, _ in columns]
    mapped_rows = [dict(zip(column_names, row, strict=True)) for row in values]
    rows_by_trace = {row["trace_id"]: row for row in mapped_rows}

    assert set(rows_by_trace) == {"trace-cross-version"}
    assert rows_by_trace["trace-cross-version"]["input"] == "selected-latest"
    assert "wrong-microsecond-root" not in str(rows_by_trace)
    assert "wrong-project-version" not in str(rows_by_trace)
    assert "stale-live-payload" not in str(rows_by_trace)


def test_trace_and_span_seed_keysets_do_not_skip_same_second_microseconds(
    ch_client, spans_table
):
    project_id = "00000000-0000-4000-8000-000000000003"
    project_version_id = "00000000-0000-4000-8000-000000000031"
    second = datetime(2026, 7, 3, 12, 0, 0)
    rows = [
        _row(
            project_id=project_id,
            project_version_id=project_version_id,
            trace_id=f"trace-micro-{index:03d}",
            root_id=f"span-micro-{index:03d}",
            start_time=second + timedelta(microseconds=index + 1),
            version=1,
        )
        for index in range(30)
    ]
    ch_client.execute(f"INSERT INTO {spans_table} {_INSERT_COLUMNS} VALUES", rows)
    filters = [
        _time_filter(second - timedelta(seconds=1), second + timedelta(seconds=1)),
        _attribute_filter(),
    ]

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = spans_table

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = spans_table

    analytics = _ClickHouseExecutor(ch_client)
    common = {
        "analytics": analytics,
        "filters": filters,
        "page_number": 0,
        "page_size": 20,
        "deadline_ms": 5_000,
        "max_seed_attempts": 10,
        "max_candidates": 10,
        "max_query_count": 20,
        "classify_batch_size": 10,
    }
    trace_page = read_bounded_filter_page(
        builder=LocalTraceBuilder(
            project_id=project_id,
            filters=filters,
            bounded_identity_only=True,
            bounded_bulk_scan=True,
        ),
        key_field="trace_id",
        **common,
    )
    span_page = read_bounded_filter_page(
        builder=LocalSpanBuilder(
            project_id=project_id,
            filters=filters,
            bounded_identity_only=True,
        ),
        key_field="id",
        **common,
    )

    assert trace_page.complete is True and trace_page.has_more is True
    assert span_page.complete is True and span_page.has_more is True
    assert [row["trace_id"] for row in trace_page.rows] == [
        f"trace-micro-{index:03d}" for index in range(29, 9, -1)
    ]
    assert [row["id"] for row in span_page.rows] == [
        f"span-micro-{index:03d}" for index in range(29, 9, -1)
    ]
    assert trace_page.query_count == 6
    assert span_page.query_count == 6


def test_span_bulk_resolution_replays_exact_123456_microsecond_identity(
    monkeypatch, ch_client, spans_table
):
    project_id = "00000000-0000-4000-8000-000000000004"
    project_version_id = "00000000-0000-4000-8000-000000000041"
    second = datetime(2026, 7, 4, 12, 0, 0)
    selected = _row(
        project_id=project_id,
        project_version_id=project_version_id,
        trace_id="trace-shared",
        root_id="span-shared",
        start_time=second + timedelta(microseconds=123456),
        version=1,
        input_value="selected-content",
    )
    decoy = list(
        _row(
            project_id=project_id,
            project_version_id=project_version_id,
            trace_id="trace-shared",
            root_id="span-shared",
            start_time=second + timedelta(microseconds=654321),
            version=1,
            input_value="wrong-microsecond-content",
        )
    )
    decoy[23] = {"final_status": "Otro"}
    ch_client.execute(
        f"INSERT INTO {spans_table} {_INSERT_COLUMNS} VALUES", [selected, tuple(decoy)]
    )

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = spans_table

    analytics = _ClickHouseExecutor(ch_client)
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_builders.span_list.SpanListQueryBuilderV2",
        LocalSpanBuilder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        lambda: analytics,
    )
    filters = [
        _time_filter(second - timedelta(seconds=1), second + timedelta(seconds=1)),
        _attribute_filter(),
    ]
    result = _resolve_span_ids_clickhouse(
        project_id=project_id,
        filters=filters,
        exclude_ids=set(),
        cap=25,
        annotation_label_ids=[],
    )

    assert result.ids == ["span-shared"]
    assert result.total_matching == 1
    assert result.truncated is False

    content_builder = LocalSpanBuilder(project_id=project_id, filters=filters)
    content_query, content_params = content_builder.build_content_query(
        ["span-shared"],
        span_identities=[
            (
                project_id,
                "trace-shared",
                "span-shared",
                second + timedelta(microseconds=123456),
            )
        ],
    )
    content_rows = analytics.execute_ch_query(
        content_query,
        content_params,
        timeout_ms=5_000,
        settings={"max_threads": 1},
    ).data
    assert len(content_rows) == 1
    assert content_rows[0]["input"] == "selected-content"
    assert content_rows[0]["start_time"].microsecond == 123456


def test_span_bulk_resolution_rejects_same_bare_id_under_two_traces(
    monkeypatch, ch_client, spans_table
):
    project_id = "00000000-0000-4000-8000-000000000005"
    project_version_id = "00000000-0000-4000-8000-000000000051"
    second = datetime(2026, 7, 5, 12, 0, 0)
    rows = [
        _row(
            project_id=project_id,
            project_version_id=project_version_id,
            trace_id=trace_id,
            root_id="shared-bare-span-id",
            start_time=second + timedelta(microseconds=microsecond),
            version=1,
        )
        for trace_id, microsecond in (("trace-a", 123456), ("trace-b", 654321))
    ]
    ch_client.execute(f"INSERT INTO {spans_table} {_INSERT_COLUMNS} VALUES", rows)

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = spans_table

    analytics = _ClickHouseExecutor(ch_client)
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_builders.span_list.SpanListQueryBuilderV2",
        LocalSpanBuilder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        lambda: analytics,
    )
    filters = [
        _time_filter(second - timedelta(seconds=1), second + timedelta(seconds=1)),
        _attribute_filter(),
    ]

    with pytest.raises(BulkSelectionAmbiguousIdentity, match="ambiguous_span_identity"):
        _resolve_span_ids_clickhouse(
            project_id=project_id,
            filters=filters,
            exclude_ids=set(),
            cap=25,
            annotation_label_ids=[],
        )
