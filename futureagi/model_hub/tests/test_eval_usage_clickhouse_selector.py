from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from clickhouse_driver import Client
from clickhouse_driver.errors import NetworkError, ServerException
from tracer.services.clickhouse import trace_project_scope
from tracer.services.clickhouse.client import ClickHouseClient

from model_hub.selectors import eval_usage
from model_hub.selectors.eval_usage import read_eval_usage


class _FakeClient:
    def __init__(
        self,
        *,
        total_runs=9,
        avg_duration=0.25,
        avg_score=0.75,
    ):
        self.calls = []
        self.lock = threading.Lock()
        self.total_runs = total_runs
        self.avg_duration = avg_duration
        self.avg_score = avg_score

    def execute_read(self, query, params, *, timeout_ms, settings):
        with self.lock:
            self.calls.append((query, params, timeout_ms, settings))
        if "AS total_runs" in query:
            return [(self.total_runs,)], [], 1.0
        if "toStartOfInterval" in query:
            return (
                [
                    (
                        datetime(2026, 8, 1, tzinfo=UTC),
                        3,
                        self.avg_duration * 3,
                        3,
                        self.avg_score * 3,
                        3,
                        2,
                        1,
                        2,
                        1,
                    )
                ],
                [],
                1.0,
            )
        if "AS older_count" in query and "AS newer_count" in query:
            return [(1, 2)], [], 1.0
        if "AS page_window_count" in query:
            return [(3,)], [], 1.0
        row_count = min(int(params.get("page_selection_limit", 1)), 3)
        return (
            [
                (
                    str(uuid.uuid4()),
                    '"{\\"output\\":{\\"output\\":0.75}}"',
                    "success",
                    datetime(2026, 8, 1, tzinfo=UTC),
                )
                for _ in range(row_count)
            ],
            [],
            1.0,
        )


class _HeavyFullWindowClient:
    def __init__(self, *, start: datetime, end: datetime, total_rows: int):
        self.start = start
        self.end = end
        self.total_rows = total_rows
        self.calls = []

    def execute_read(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, dict(params), timeout_ms, settings))
        if "AS total_runs" in query:
            return [(self.total_rows,)], [], 1.0
        if "toStartOfInterval" in query:
            return (
                [
                    (
                        params["start_date"],
                        self.total_rows,
                        self.total_rows * 2.0,
                        self.total_rows,
                        self.total_rows * 0.5,
                        self.total_rows,
                        self.total_rows,
                        0,
                        self.total_rows,
                        0,
                    )
                ],
                [],
                1.0,
            )
        if "AS older_count" in query and "AS newer_count" in query:
            newer = self.total_rows // 2
            return [(self.total_rows - newer, newer)], [], 1.0
        if "AS page_window_count" in query:
            return [(self.total_rows,)], [], 1.0
        if "SELECT min(id), max(id), count()" in query:
            return [(1, self.total_rows, self.total_rows)], [], 1.0
        count = min(
            int(params.get("page_selection_limit", 0)),
            self.total_rows,
        )
        return (
            [
                (
                    str(uuid.uuid4()),
                    {},
                    "success",
                    params["end_date"] - timedelta(microseconds=index + 1),
                )
                for index in range(count)
            ],
            [],
            1.0,
        )


class _DenseSeekClient:
    def __init__(self, *, start: datetime, end: datetime, total_rows: int):
        self.start = start
        self.end = end
        self.total_rows = total_rows
        self.calls = []

    def _window_count(self, start: datetime, end: datetime) -> int:
        full_width = self.end - self.start
        if full_width <= timedelta(0):
            return self.total_rows
        fraction = (end - start) / full_width
        return max(0, round(self.total_rows * fraction))

    def execute_read(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, dict(params), timeout_ms, settings))
        if "AS total_runs" in query:
            return [(self.total_rows,)], [], 1.0
        if "toStartOfInterval" in query:
            return (
                [
                    (
                        self.start,
                        self.total_rows,
                        float(self.total_rows),
                        self.total_rows,
                        float(self.total_rows),
                        self.total_rows,
                        self.total_rows,
                        0,
                        self.total_rows,
                        0,
                    )
                ],
                [],
                1.0,
            )
        if "AS older_count" in query and "AS newer_count" in query:
            midpoint = params["page_window_midpoint"]
            older = self._window_count(params["page_window_start"], midpoint)
            newer = self._window_count(midpoint, params["page_window_end"])
            return [(older, newer)], [], 1.0
        if "AS page_window_count" in query:
            return (
                [
                    (
                        self._window_count(
                            params["page_window_start"],
                            params["page_window_end"],
                        ),
                    )
                ],
                [],
                1.0,
            )
        if "SELECT min(id), max(id), count()" in query:
            return [(1, self.total_rows, self.total_rows)], [], 1.0
        if "AS lower_count" in query and "AS upper_count" in query:
            low = int(params["page_id_low"])
            high = int(params["page_id_high"])
            midpoint = int(params["page_id_midpoint"])
            return (
                [
                    (
                        max(0, min(high, midpoint) - low + 1),
                        max(0, high - max(low, midpoint + 1) + 1),
                    )
                ],
                [],
                1.0,
            )

        requested = int(params["page_selection_limit"])
        if "page_seek_created_at" in params:
            available = requested
        elif "page_id_low" in params and "page_id_high" in params:
            available = int(params["page_id_high"]) - int(params["page_id_low"]) + 1
        else:
            available = self._window_count(
                params["page_window_start"],
                params["page_window_end"],
            )
        count = min(requested, available)
        return (
            [
                (
                    str(uuid.uuid4()),
                    {"selection_rank": index},
                    "success",
                    params["page_window_end"] - timedelta(microseconds=1),
                )
                for index in range(count)
            ],
            [],
            1.0,
        )


@pytest.mark.unit
def test_eval_usage_queries_are_project_scoped_bounded_and_page_only(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: fake)
    now = datetime(2026, 8, 2, tzinfo=UTC)

    result = read_eval_usage(
        organization_id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
        template_id=str(uuid.uuid4()),
        start_date=now - timedelta(days=30),
        end_date=now,
        bucket_minutes=1440,
        page=0,
        page_size=25,
    )

    assert result.total_runs == 9
    assert result.completeness == eval_usage.EvalUsageReadCompleteness.COMPLETE
    assert result.unavailable_fields == ()
    assert result.runs_period == 3
    assert result.logs[0].config == {"output": {"output": 0.75}}
    assert len(fake.calls) == 3
    assert all("usage_version_ceiling" not in query for query, *_ in fake.calls)
    for query, params, timeout_ms, settings in fake.calls:
        assert 0 < timeout_ms <= eval_usage.QUERY_TIMEOUT_MS
        assert "additional_table_filters" not in settings
        assert "usage_apicalllog FINAL" not in query
        assert "PREWHERE organization_id = toUUID" in query
        assert "workspace_id = toUUID" in query
        assert "source_id = %(template_id)s" in query
        assert "ORDER BY _peerdb_version DESC" in query
        assert "LIMIT 1 BY id" in query
        assert "WHERE _peerdb_is_deleted = 0 AND deleted = 0" in query
        assert params["project_ids"]
    page_query = next(query for query, *_ in fake.calls if "toString(log_id)" in query)
    assert "LIMIT %(page_selection_limit)s" in page_query
    assert " OFFSET " not in page_query
    total_query = next(query for query, *_ in fake.calls if "AS total_runs" in query)
    assert "trace_dict" not in total_query
    assert "FROM traces" not in total_query
    assert "IN %(project_ids)s" not in total_query
    assert "created_at >=" not in total_query
    assert "created_at <" not in total_query
    period_queries = [query for query, *_ in fake.calls if "AS total_runs" not in query]
    assert all(
        "trace_dict" not in query
        and "FROM traces" in query
        and "INNER JOIN (" in query
        and "AS bounded_trace_candidates" in query
        and "PREWHERE trace_project_scan.project_id IN %(project_ids)s" in query
        and "SELECT DISTINCT toUUIDOrZero(eval_trace_id) AS trace_id" in query
        and "GROUP BY trace_project_scan.id" in query
        and "LEFT JOIN (" in query
        and "allowed_trace_projects.trace_id" in query
        and "toUUIDOrZero(current_usage.eval_trace_id)" in query
        and "allowed_trace_projects.project_id IN %(project_ids)s" in query
        and query.count("FROM usage_apicalllog") == 2
        and query.index("LIMIT 1 BY id")
        < query.index("allowed_trace_projects.project_id IN")
        for query in period_queries
    )
    chart_query = next(
        query for query in period_queries if "toStartOfInterval" in query
    )
    assert "created_at >= %(start_date)s" in chart_query
    assert "created_at < %(end_date)s" in chart_query
    assert "created_at >= %(page_window_start)s" in page_query
    assert "created_at < %(page_window_end)s" in page_query


@pytest.mark.unit
def test_eval_usage_heavy_12m_uses_three_full_window_statements(monkeypatch):
    end = datetime(2026, 8, 1, tzinfo=UTC)
    start = end - timedelta(days=365)
    fake = _HeavyFullWindowClient(
        start=start,
        end=end + timedelta(microseconds=1),
        total_rows=12_000_000,
    )
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: fake)

    result = read_eval_usage(
        organization_id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
        template_id=str(uuid.uuid4()),
        start_date=start,
        end_date=end,
        bucket_minutes=1440,
        page=0,
        page_size=25,
    )

    total_calls = [call for call in fake.calls if "AS total_runs" in call[0]]
    chart_calls = [call for call in fake.calls if "toStartOfInterval" in call[0]]
    page_calls = [call for call in fake.calls if "toString(log_id)" in call[0]]
    assert len(fake.calls) == 3
    assert len(total_calls) == 1
    assert len(chart_calls) == 1
    assert len(page_calls) == 1
    assert result.total_runs == 12_000_000
    assert result.runs_period == 12_000_000
    assert len(result.logs) == 25
    assert result.completeness == eval_usage.EvalUsageReadCompleteness.COMPLETE
    assert all(
        call_params["start_date"] == start
        and call_params["end_date"] == end + timedelta(microseconds=1)
        and "partition_start" not in call_params
        and "partition_end" not in call_params
        and "additional_table_filters" not in call_settings
        for _query, call_params, _timeout, call_settings in fake.calls
    )
    assert all(
        "max_rows_to_read" not in call_settings
        and call_settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
        for _query, _params, _timeout, call_settings in fake.calls
    )


@pytest.mark.unit
def test_eval_usage_page_n_uses_bounded_seek_without_offset(monkeypatch):
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = start + timedelta(days=93)
    fake = _HeavyFullWindowClient(
        start=start,
        end=end,
        total_rows=50,
    )
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: fake)

    result = read_eval_usage(
        organization_id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
        template_id=str(uuid.uuid4()),
        start_date=start,
        end_date=end - timedelta(microseconds=1),
        bucket_minutes=1440,
        page=1,
        page_size=15,
    )

    page_calls = [call for call in fake.calls if "toString(log_id)" in call[0]]
    seek_calls = [call for call in fake.calls if "AS older_count" in call[0]]
    assert len(result.logs) == 15
    assert len(page_calls) == 1
    assert len(seek_calls) == 1
    assert page_calls[0][1]["page_selection_limit"] == 30
    assert "partition_start" not in page_calls[0][1]
    assert "partition_end" not in page_calls[0][1]
    assert all(" OFFSET " not in query for query, *_ in fake.calls)
    assert all("page_offset" not in params for _query, params, *_ in fake.calls)


@pytest.mark.unit
def test_eval_usage_deep_page_seeks_to_small_time_leaf(monkeypatch):
    start = datetime(2025, 8, 1, tzinfo=UTC)
    end = start + timedelta(days=365)
    fake = _DenseSeekClient(start=start, end=end, total_rows=50_000)
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: fake)

    result = read_eval_usage(
        organization_id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
        template_id=str(uuid.uuid4()),
        start_date=start,
        end_date=end - timedelta(microseconds=1),
        bucket_minutes=1440,
        page=1200,
        page_size=25,
    )

    seek_calls = [call for call in fake.calls if "AS older_count" in call[0]]
    page_calls = [call for call in fake.calls if "toString(log_id)" in call[0]]
    assert len(result.logs) == 25
    assert result.completeness == eval_usage.EvalUsageReadCompleteness.COMPLETE
    assert result.unavailable_fields == ()
    assert len(seek_calls) == 3
    assert len(page_calls) == 1
    assert page_calls[0][1]["page_selection_limit"] == 5025
    assert page_calls[0][1]["page_selection_limit"] <= (
        eval_usage._MAX_PAGE_SELECTION_ROWS + 25
    )
    assert all(" OFFSET " not in query for query, *_ in fake.calls)
    assert all("SAMPLE" not in query.upper() for query, *_ in fake.calls)
    assert all(
        "additional_table_filters" not in settings for *_, settings in fake.calls
    )
    assert all(
        "FROM traces" in query
        and "AS bounded_trace_candidates" in query
        and query.count("FROM usage_apicalllog") == 2
        for query, *_ in seek_calls + page_calls
    )
    assert len([call for call in fake.calls if "toStartOfInterval" in call[0]]) == 1


@pytest.mark.unit
def test_eval_usage_same_microsecond_deep_page_seeks_by_id(monkeypatch):
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start + timedelta(microseconds=1)
    fake = _DenseSeekClient(start=start, end=end, total_rows=50_000)
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: fake)

    result = read_eval_usage(
        organization_id=str(uuid.uuid4()),
        workspace_id=None,
        project_ids=[str(uuid.uuid4())],
        template_id=str(uuid.uuid4()),
        start_date=start,
        end_date=start,
        bucket_minutes=60,
        page=1200,
        page_size=25,
    )

    id_seek_calls = [call for call in fake.calls if "AS lower_count" in call[0]]
    page_calls = [call for call in fake.calls if "toString(log_id)" in call[0]]
    assert len(result.logs) == 25
    assert len(id_seek_calls) == 3
    assert len(page_calls) == 1
    assert page_calls[0][1]["page_id_low"] == 18_751
    assert page_calls[0][1]["page_id_high"] == 25_000
    assert page_calls[0][1]["page_selection_limit"] == 5025
    assert "id >= %(page_id_low)s" in page_calls[0][0]
    assert "id <= %(page_id_high)s" in page_calls[0][0]
    assert all(" OFFSET " not in query for query, *_ in fake.calls)


@pytest.mark.unit
def test_eval_usage_page_fills_exactly_across_seek_leaf_boundary(monkeypatch):
    start = datetime(2025, 8, 1, tzinfo=UTC)
    end = start + timedelta(days=365)
    fake = _DenseSeekClient(start=start, end=end, total_rows=30_010)
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: fake)

    result = read_eval_usage(
        organization_id=str(uuid.uuid4()),
        workspace_id=None,
        project_ids=[str(uuid.uuid4())],
        template_id=str(uuid.uuid4()),
        start_date=start,
        end_date=end - timedelta(microseconds=1),
        bucket_minutes=1440,
        page=600,
        page_size=25,
    )

    page_calls = [call for call in fake.calls if "toString(log_id)" in call[0]]
    assert len(result.logs) == 25
    assert len(page_calls) == 2
    assert page_calls[0][1]["page_selection_limit"] > 7_500
    assert page_calls[0][1]["page_selection_limit"] <= (
        eval_usage._MAX_PAGE_SELECTION_ROWS + 25
    )
    assert page_calls[1][1]["page_selection_limit"] < 25
    assert "created_at < %(page_seek_created_at)s" in page_calls[1][0]
    assert all(" OFFSET " not in query for query, *_ in fake.calls)


@pytest.mark.unit
def test_eval_usage_maps_one_exact_chart_aggregate(monkeypatch):
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start + timedelta(days=2)

    class ExactAggregateClient(_HeavyFullWindowClient):
        def execute_read(self, query, params, *, timeout_ms, settings):
            if "toStartOfInterval" in query:
                self.calls.append((query, dict(params), timeout_ms, settings))
                return (
                    [
                        (
                            start,
                            5,
                            13.0,
                            5,
                            3.0,
                            5,
                            3,
                            2,
                            3,
                            2,
                        )
                    ],
                    [],
                    1.0,
                )
            return super().execute_read(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )

    fake = ExactAggregateClient(start=start, end=end, total_rows=5)
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: fake)

    result = read_eval_usage(
        organization_id=str(uuid.uuid4()),
        workspace_id=None,
        project_ids=[str(uuid.uuid4())],
        template_id=str(uuid.uuid4()),
        start_date=start,
        end_date=end - timedelta(microseconds=1),
        bucket_minutes=1440,
        page=0,
        page_size=5,
    )

    assert result.runs_period == 5
    assert result.success_count == 3
    assert result.error_count == 2
    assert len(result.chart) == 1
    assert result.chart[0].calls == 5
    assert result.chart[0].avg_duration == pytest.approx(13.0 / 5.0)
    assert result.chart[0].avg_score == pytest.approx(3.0 / 5.0)
    assert result.chart[0].pass_count == 3
    assert result.chart[0].fail_count == 2
    assert len(result.logs) == 5
    assert len(fake.calls) == 3


@pytest.mark.unit
def test_eval_usage_full_window_budget_failure_is_not_split_or_published(monkeypatch):
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start + timedelta(days=2)

    class FullWindowFailureClient(_HeavyFullWindowClient):
        def execute_read(self, query, params, *, timeout_ms, settings):
            if "toStartOfInterval" in query:
                self.calls.append((query, dict(params), timeout_ms, settings))
                raise ServerException("full window exceeds budget", code=241)
            return super().execute_read(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )

    fake = FullWindowFailureClient(start=start, end=end, total_rows=2)
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: fake)

    with pytest.raises(eval_usage.EvalUsageReadError) as raised:
        read_eval_usage(
            organization_id=str(uuid.uuid4()),
            workspace_id=None,
            project_ids=[str(uuid.uuid4())],
            template_id=str(uuid.uuid4()),
            start_date=start,
            end_date=end - timedelta(microseconds=1),
            bucket_minutes=1440,
            page=0,
            page_size=25,
        )

    assert raised.value.code == eval_usage.EvalUsageReadErrorCode.DEADLINE_EXCEEDED
    assert raised.value.operations == ("chart",)
    assert len([call for call in fake.calls if "toStartOfInterval" in call[0]]) == 1
    assert not any("toString(log_id)" in call[0] for call in fake.calls)


@pytest.mark.unit
def test_latest_trace_project_relation_requires_finite_candidates():
    with pytest.raises(ValueError, match="bounded trace-ID candidate"):
        trace_project_scope.latest_live_trace_projects_sql(candidate_trace_ids_sql="")


@pytest.mark.unit
def test_eval_usage_exact_total_can_be_zero(monkeypatch):
    fake = _FakeClient(total_runs=0)
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: fake)
    now = datetime(2026, 8, 2, tzinfo=UTC)

    result = read_eval_usage(
        organization_id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
        template_id=str(uuid.uuid4()),
        start_date=now - timedelta(days=1),
        end_date=now,
        bucket_minutes=60,
        page=0,
        page_size=25,
    )

    assert result.total_runs == 0
    assert result.completeness == eval_usage.EvalUsageReadCompleteness.COMPLETE
    assert result.unavailable_fields == ()
    assert len(fake.calls) == 1
    assert "AS total_runs" in fake.calls[0][0]


@pytest.mark.unit
def test_eval_usage_normalizes_non_finite_empty_averages(monkeypatch):
    fake = _FakeClient(avg_duration=float("nan"), avg_score=float("inf"))
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: fake)
    now = datetime(2026, 8, 2, tzinfo=UTC)

    result = read_eval_usage(
        organization_id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        project_ids=[str(uuid.uuid4())],
        template_id=str(uuid.uuid4()),
        start_date=now - timedelta(days=1),
        end_date=now,
        bucket_minutes=60,
        page=0,
        page_size=25,
    )

    assert result.chart[0].avg_duration is None
    assert result.chart[0].avg_score is None


@pytest.mark.unit
def test_eval_usage_connect_stall_returns_within_one_wall_deadline(monkeypatch):
    fake = _FakeClient()
    release = threading.Event()
    lock = threading.Lock()
    acquisitions = 0

    def acquire_client():
        nonlocal acquisitions
        with lock:
            acquisition = acquisitions
            acquisitions += 1
        if acquisition == 0:
            release.wait(timeout=5)
        return fake

    monkeypatch.setattr(eval_usage, "READ_TIMEOUT_MS", 75)
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", acquire_client)
    now = datetime(2026, 8, 2, tzinfo=UTC)

    started = time.monotonic()
    try:
        with pytest.raises(eval_usage.EvalUsageReadError) as raised:
            read_eval_usage(
                organization_id=str(uuid.uuid4()),
                workspace_id=str(uuid.uuid4()),
                project_ids=[str(uuid.uuid4())],
                template_id=str(uuid.uuid4()),
                start_date=now - timedelta(days=1),
                end_date=now,
                bucket_minutes=60,
                page=0,
                page_size=25,
            )
    finally:
        release.set()

    assert raised.value.code == eval_usage.EvalUsageReadErrorCode.DEADLINE_EXCEEDED
    assert time.monotonic() - started < 0.5


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (
            ServerException("private timeout query", code=159),
            eval_usage.EvalUsageReadErrorCode.DEADLINE_EXCEEDED,
        ),
        (
            NetworkError("private network detail"),
            eval_usage.EvalUsageReadErrorCode.QUERY_FAILED,
        ),
    ],
)
def test_eval_usage_clickhouse_failures_are_typed(monkeypatch, failure, expected_code):
    class FailingClient:
        def execute_read(self, *_args, **_kwargs):
            raise failure

    monkeypatch.setattr(eval_usage, "get_clickhouse_client", FailingClient)
    now = datetime(2026, 8, 2, tzinfo=UTC)

    with pytest.raises(eval_usage.EvalUsageReadError) as raised:
        read_eval_usage(
            organization_id=str(uuid.uuid4()),
            workspace_id=str(uuid.uuid4()),
            project_ids=[str(uuid.uuid4())],
            template_id=str(uuid.uuid4()),
            start_date=now - timedelta(days=1),
            end_date=now,
            bucket_minutes=60,
            page=0,
            page_size=25,
        )

    assert raised.value.code == expected_code
    assert raised.value.operations == ("total",)


@pytest.mark.unit
def test_eval_usage_programming_defect_re_raises_original_type(monkeypatch):
    class BuggyClient:
        def execute_read(self, *_args, **_kwargs):
            raise KeyError("application bug")

    monkeypatch.setattr(eval_usage, "get_clickhouse_client", BuggyClient)
    now = datetime(2026, 8, 2, tzinfo=UTC)

    with pytest.raises(KeyError, match="application bug"):
        read_eval_usage(
            organization_id=str(uuid.uuid4()),
            workspace_id=str(uuid.uuid4()),
            project_ids=[str(uuid.uuid4())],
            template_id=str(uuid.uuid4()),
            start_date=now - timedelta(days=1),
            end_date=now,
            bucket_minutes=60,
            page=0,
            page_size=25,
        )


@pytest.mark.unit
def test_eval_usage_empty_project_set_fails_closed_for_trace_rows(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: fake)
    now = datetime(2026, 8, 2, tzinfo=UTC)

    read_eval_usage(
        organization_id=str(uuid.uuid4()),
        workspace_id=None,
        project_ids=[],
        template_id=str(uuid.uuid4()),
        start_date=now - timedelta(days=1),
        end_date=now,
        bucket_minutes=60,
        page=0,
        page_size=25,
    )

    assert all(
        params["project_ids"] == ("00000000-0000-0000-0000-000000000000",)
        for _query, params, _timeout, _settings in fake.calls
    )


@pytest.fixture(scope="module")
def ch_client():
    host = os.environ.get("CH25_HOST", "127.0.0.1")
    port = int(os.environ.get("CH25_NATIVE_PORT", "19000"))
    client = Client(host=host, port=port, connect_timeout=3)
    try:
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"CH25 unavailable on {host}:{port}: {exc!r}")
    try:
        yield client
    finally:
        client.disconnect_connection()


@pytest.mark.integration
def test_eval_usage_real_ch25_latest_tombstone_and_project_scope(
    ch_client,
    monkeypatch,
):
    suffix = uuid.uuid4().hex[:10]
    usage_table = f"_test_eval_usage_{suffix}"
    trace_source = f"_test_eval_usage_trace_{suffix}"
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    project_id = uuid.uuid4()
    other_project_id = uuid.uuid4()
    template_id = str(uuid.uuid4())
    trace_id = uuid.uuid4()
    other_trace_id = uuid.uuid4()
    now = datetime.now(UTC).replace(microsecond=0)

    ch_client.execute(
        f"""
        CREATE TABLE {usage_table} (
            id Int64,
            log_id UUID,
            organization_id UUID,
            workspace_id Nullable(UUID),
            source_id String,
            status String,
            config String,
            eval_trace_id String,
            deleted UInt8,
            created_at DateTime64(6, 'UTC'),
            _peerdb_synced_at DateTime64(6, 'UTC'),
            _peerdb_is_deleted UInt8,
            _peerdb_version Int64
        ) ENGINE = MergeTree
        -- Match the live historical table layout. The selector must not rely
        -- on tenant/source/time being part of the primary key.
        ORDER BY id
        """
    )
    ch_client.execute(
        f"""
        CREATE TABLE {trace_source} (
            id UUID,
            project_id UUID,
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        ORDER BY (project_id, id)
        """
    )
    try:
        ch_client.execute(
            f"INSERT INTO {trace_source} VALUES",
            [
                (trace_id, project_id, 0, 1),
                (other_trace_id, other_project_id, 0, 1),
            ],
        )
        rows = [
            # Live selected-project trace row.
            (
                1,
                uuid.uuid4(),
                organization_id,
                workspace_id,
                template_id,
                "success",
                '{"duration":0.25,"output":{"output":0.85}}',
                str(trace_id),
                0,
                now - timedelta(hours=2),
                now - timedelta(hours=2),
                0,
                1,
            ),
            # Older live version followed by a tombstone: must not resurrect.
            (
                2,
                uuid.uuid4(),
                organization_id,
                workspace_id,
                template_id,
                "success",
                '{"output":{"output":"Passed"}}',
                str(trace_id),
                0,
                now - timedelta(hours=1),
                now - timedelta(hours=1),
                0,
                1,
            ),
            (
                2,
                uuid.uuid4(),
                organization_id,
                workspace_id,
                template_id,
                "success",
                '{"output":{"output":"Passed"}}',
                str(trace_id),
                1,
                now - timedelta(hours=1),
                now - timedelta(minutes=59),
                1,
                2,
            ),
            # Sibling-project trace row: project dictionary must exclude it.
            (
                3,
                uuid.uuid4(),
                organization_id,
                workspace_id,
                template_id,
                "error",
                '{"output":{"output":"Failed"}}',
                str(other_trace_id),
                0,
                now - timedelta(minutes=30),
                now - timedelta(minutes=30),
                0,
                1,
            ),
            # Non-trace playground row retains historical usage semantics.
            (
                4,
                uuid.uuid4(),
                organization_id,
                workspace_id,
                template_id,
                "success",
                '{"response_time":250,"output":{"output":{"label":"Passed","score":1.0}}}',
                "",
                0,
                now - timedelta(minutes=15),
                now - timedelta(minutes=15),
                0,
                1,
            ),
        ]
        ch_client.execute(f"INSERT INTO {usage_table} VALUES", rows)
        monkeypatch.setattr(eval_usage, "_USAGE_TABLE", usage_table)
        monkeypatch.setattr(trace_project_scope, "_TRACE_TABLE", trace_source)
        read_client = ClickHouseClient(
            host=os.environ.get("CH25_HOST", "127.0.0.1"),
            port=int(os.environ.get("CH25_NATIVE_PORT", "19000")),
            database="default",
        )
        monkeypatch.setattr(
            eval_usage,
            "get_clickhouse_client",
            lambda: read_client,
        )

        result = read_eval_usage(
            organization_id=str(organization_id),
            workspace_id=str(workspace_id),
            project_ids=[str(project_id)],
            template_id=template_id,
            start_date=now - timedelta(days=1),
            end_date=now,
            bucket_minutes=60,
            page=0,
            page_size=25,
        )

        # total_runs preserves the original org/workspace/template contract,
        # so it includes the live sibling-project row as well as the two rows
        # rendered by the project-scoped selected-period response.
        assert result.total_runs == 3
        assert result.completeness == eval_usage.EvalUsageReadCompleteness.COMPLETE
        assert result.unavailable_fields == ()
        assert result.runs_period == 2
        assert result.success_count == 2
        assert result.error_count == 0
        assert len(result.logs) == 2
        assert sum(bucket.calls for bucket in result.chart) == 2
    finally:
        if "read_client" in locals():
            read_client.close()
        ch_client.execute(f"DROP TABLE IF EXISTS {trace_source}")
        ch_client.execute(f"DROP TABLE IF EXISTS {usage_table}")
