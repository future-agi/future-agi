from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from tracer.selectors.trace_filter_reads import read_bounded_filter_neighbors
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

START = datetime(2025, 1, 1)
END = START + timedelta(days=365)
PROJECT_ID = "00000000-0000-4000-8000-000000000001"


def _row(row_id: str, start_time: datetime) -> dict[str, Any]:
    return {"id": row_id, "start_time": start_time}


class _NavigationBuilder:
    def __init__(
        self,
        *,
        target: dict[str, Any],
        raw_rows: list[dict[str, Any]],
        classified_rows: dict[str, dict[str, Any] | None] | None = None,
        start: datetime = START,
        end: datetime = END,
        classify_batch_size: int = 100,
    ) -> None:
        self.target = target
        self.raw_rows = list(raw_rows)
        self.classified_rows = (
            classified_rows
            if classified_rows is not None
            else {str(row["id"]): row for row in raw_rows}
        )
        self.start = start
        self.end = end
        self.classify_batch_size = classify_batch_size

    def parse_time_range(self, filters):
        return self.start, self.end

    @staticmethod
    def bounded_filter_row_identity(row):
        return str(row["id"])

    bounded_filter_seed_identity = bounded_filter_row_identity

    @staticmethod
    def bounded_filter_row_order_token(row):
        return str(row["id"])

    bounded_filter_seed_order_token = bounded_filter_row_order_token

    def recommended_filter_classify_batch_size(self) -> int:
        return self.classify_batch_size

    def build_filter_navigation_target_query(self, *, target_id, result_limit=2):
        return "target", {"target_id": target_id, "limit": result_limit}

    def build_filter_navigation_seed_page(
        self,
        *,
        direction,
        slice_start,
        slice_end,
        limit,
        cursor_start_time=None,
        cursor_order_token=None,
    ):
        return "seed", {
            "direction": direction,
            "slice_start": slice_start,
            "slice_end": slice_end,
            "limit": limit,
            "cursor_start_time": cursor_start_time,
            "cursor_order_token": cursor_order_token,
        }

    @staticmethod
    def build_filter_match_query_from_seed_rows(seed_rows):
        return "match", {"seed_rows": list(seed_rows)}


class _NavigationExecutor:
    supports_per_query_read_settings = True

    def __init__(
        self,
        builder: _NavigationBuilder,
        *,
        target_rows: list[dict[str, Any]] | None = None,
        failures: dict[str, Exception] | None = None,
    ) -> None:
        self.builder = builder
        self.target_rows = [builder.target] if target_rows is None else target_rows
        self.failures = failures or {}
        self.seed_windows: list[tuple[str, datetime, datetime]] = []

    @staticmethod
    def _result(rows) -> QueryResult:
        data = list(rows)
        return QueryResult(
            data=data,
            row_count=len(data),
            backend_used="clickhouse",
            query_time_ms=0.0,
        )

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if failure := self.failures.get(query):
            raise failure
        if query == "target":
            return self._result(self.target_rows)
        if query == "match":
            rows = []
            for seed_row in params["seed_rows"]:
                classified = self.builder.classified_rows.get(str(seed_row["id"]))
                if classified is not None:
                    rows.append(classified)
            return self._result(rows)
        if query != "seed":
            raise AssertionError(f"unexpected query {query!r}")

        direction = params["direction"]
        slice_start = params["slice_start"]
        slice_end = params["slice_end"]
        cursor = (
            params["cursor_start_time"],
            params["cursor_order_token"],
        )
        self.seed_windows.append((direction, slice_start, slice_end))
        rows = [
            row
            for row in self.builder.raw_rows
            if slice_start <= row["start_time"] < slice_end
        ]
        if cursor[0] is not None:
            if direction == "older":
                rows = [row for row in rows if (row["start_time"], row["id"]) < cursor]
            else:
                rows = [row for row in rows if (row["start_time"], row["id"]) > cursor]
        rows.sort(
            key=lambda row: (row["start_time"], row["id"]),
            reverse=direction == "older",
        )
        return self._result(rows[: params["limit"]])


def _read(
    builder: _NavigationBuilder,
    executor: _NavigationExecutor,
    **overrides: Any,
):
    kwargs = {
        "builder": builder,
        "analytics": executor,
        "filters": [],
        "key_field": "id",
        "target_id": str(builder.target["id"]),
        "deadline_ms": 20_000,
        "scan_limit": 4_095,
        "page_size": 200,
        "max_query_count": 128,
    }
    kwargs.update(overrides)
    return read_bounded_filter_neighbors(
        **kwargs,
    )


@pytest.mark.unit
def test_one_year_empty_newer_side_proves_exact_boundary_within_budget() -> None:
    target = _row("target", START)
    builder = _NavigationBuilder(target=target, raw_rows=[target])
    executor = _NavigationExecutor(builder)

    neighbors = _read(builder, executor)

    assert neighbors.complete is True
    assert neighbors.error_code is None
    assert neighbors.newer is None
    assert neighbors.older is None
    assert neighbors.query_count <= 128
    newer_widths = [
        end - start for kind, start, end in executor.seed_windows if kind == "newer"
    ]
    assert newer_widths
    assert max(newer_widths) > timedelta(days=2)


@pytest.mark.unit
def test_one_year_far_older_match_completes_inside_half_query_budget() -> None:
    target = _row("target", END - timedelta(microseconds=1))
    far_older = _row("far-older", START + timedelta(microseconds=1))
    builder = _NavigationBuilder(target=target, raw_rows=[target, far_older])
    executor = _NavigationExecutor(builder)

    neighbors = _read(builder, executor)

    assert neighbors.complete is True
    assert neighbors.error_code is None
    assert neighbors.older == far_older
    assert neighbors.newer is None
    assert neighbors.query_count <= 128
    older_windows = [window for window in executor.seed_windows if window[0] == "older"]
    assert len(older_windows) <= 24
    assert max(end - start for _, start, end in older_windows) > timedelta(days=2)


@pytest.mark.unit
def test_cross_slice_root_drift_does_not_skip_a_closer_neighbor() -> None:
    start = datetime(2026, 7, 1)
    end = start + timedelta(days=30)
    target = _row("target", end - timedelta(hours=1))
    raw_drift = _row("drift", target["start_time"] - timedelta(minutes=1))
    drifted_result = _row("drift", target["start_time"] - timedelta(minutes=10))
    closer = _row("closer", target["start_time"] - timedelta(minutes=6))
    builder = _NavigationBuilder(
        target=target,
        raw_rows=[target, raw_drift, closer],
        classified_rows={"drift": drifted_result, "closer": closer},
        start=start,
        end=end,
    )
    executor = _NavigationExecutor(builder)

    neighbors = _read(builder, executor)

    assert neighbors.complete is True
    assert neighbors.error_code is None
    assert neighbors.older == closer
    assert neighbors.older != drifted_result


@pytest.mark.unit
def test_newer_side_reuses_query_budget_left_by_empty_older_side() -> None:
    target = _row("target", START)
    raw_newer = [
        _row(f"candidate-{index:04d}", START + timedelta(minutes=index))
        for index in range(1, 2_705)
    ]
    nearest_match = raw_newer[2_600]
    builder = _NavigationBuilder(
        target=target,
        raw_rows=[target, *raw_newer],
        classified_rows={str(nearest_match["id"]): nearest_match},
        classify_batch_size=50,
    )
    executor = _NavigationExecutor(builder)

    neighbors = _read(builder, executor)

    assert neighbors.complete is True
    assert neighbors.error_code is None
    assert neighbors.newer == nearest_match
    # Target + the empty older boundary consume two statements. The newer
    # proof then needs more than the old fixed 63-query half, but stays inside
    # the unchanged 128-query global ceiling by reusing the older remainder.
    assert 65 < neighbors.query_count <= 128


@pytest.mark.unit
def test_equal_timestamp_ties_are_exact_in_both_directions() -> None:
    timestamp = START + timedelta(days=1)
    older = _row("a-older", timestamp)
    target = _row("m-target", timestamp)
    newer = _row("z-newer", timestamp)
    builder = _NavigationBuilder(
        target=target,
        raw_rows=[newer, target, older],
        start=timestamp - timedelta(minutes=1),
        end=timestamp + timedelta(minutes=1),
    )
    executor = _NavigationExecutor(builder)

    neighbors = _read(builder, executor)

    assert neighbors.complete is True
    assert neighbors.error_code is None
    assert neighbors.older == older
    assert neighbors.current == target
    assert neighbors.newer == newer


@pytest.mark.unit
def test_filtered_out_target_fails_closed_before_neighbor_scans() -> None:
    target = _row("filtered-out", START + timedelta(days=1))
    builder = _NavigationBuilder(target=target, raw_rows=[target])
    executor = _NavigationExecutor(builder, target_rows=[])

    neighbors = _read(builder, executor)

    assert neighbors.complete is False
    assert neighbors.error_code == "target_not_found"
    assert neighbors.current is None
    assert neighbors.older is None
    assert neighbors.newer is None
    assert neighbors.query_count == 1
    assert executor.seed_windows == []


@pytest.mark.unit
def test_deadline_failure_uses_stable_code_and_exposes_no_partial_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _row("target", START + timedelta(days=1))
    builder = _NavigationBuilder(target=target, raw_rows=[target])
    executor = _NavigationExecutor(builder)
    clock = iter((0.0, 1.0))
    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.monotonic",
        lambda: next(clock),
    )

    neighbors = _read(builder, executor, deadline_ms=50)

    assert neighbors.complete is False
    assert neighbors.error_code == "deadline_exceeded"
    assert neighbors.current is None
    assert neighbors.older is None
    assert neighbors.newer is None
    assert neighbors.query_count == 0


@pytest.mark.unit
def test_query_budget_failure_uses_stable_code_and_exposes_no_neighbors() -> None:
    target = _row("target", START + timedelta(days=1))
    builder = _NavigationBuilder(target=target, raw_rows=[target])
    executor = _NavigationExecutor(builder)

    neighbors = _read(builder, executor, max_query_count=1)

    assert neighbors.complete is False
    assert neighbors.error_code == "query_budget_exceeded"
    assert neighbors.current == target
    assert neighbors.older is None
    assert neighbors.newer is None
    assert neighbors.query_count == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (ReadDeadlineExceeded("private ClickHouse timeout"), "read_budget_exceeded"),
        (RuntimeError("private ClickHouse stack"), "query_failed"),
    ],
)
def test_query_failures_are_sanitized_and_expose_no_neighbors(
    failure: Exception,
    expected_code: str,
) -> None:
    target = _row("target", START + timedelta(days=1))
    builder = _NavigationBuilder(target=target, raw_rows=[target])
    executor = _NavigationExecutor(builder, failures={"seed": failure})

    neighbors = _read(builder, executor)

    assert neighbors.complete is False
    assert neighbors.error_code == expected_code
    assert "ClickHouse" not in neighbors.error_code
    assert neighbors.current == target
    assert neighbors.older is None
    assert neighbors.newer is None
    assert neighbors.query_count == 2


@pytest.mark.unit
def test_each_direction_has_an_independent_row_cap() -> None:
    target_time = START + timedelta(days=1)
    target = _row("target", target_time)
    older_rows = [
        _row(f"older-{index}", target_time - timedelta(seconds=index))
        for index in range(1, 5)
    ]
    newer_rows = [
        _row(f"newer-{index}", target_time + timedelta(seconds=index))
        for index in range(1, 5)
    ]
    older_match = older_rows[-1]
    newer_match = newer_rows[-1]
    builder = _NavigationBuilder(
        target=target,
        raw_rows=[target, *older_rows, *newer_rows],
        classified_rows={
            str(older_match["id"]): older_match,
            str(newer_match["id"]): newer_match,
        },
        start=target_time - timedelta(minutes=1),
        end=target_time + timedelta(minutes=1),
        classify_batch_size=2,
    )
    executor = _NavigationExecutor(builder)

    neighbors = _read(builder, executor, scan_limit=4, page_size=2)

    assert neighbors.complete is True
    assert neighbors.error_code is None
    assert neighbors.older == older_match
    assert neighbors.newer == newer_match
    # The ceiling is per direction: each side may consume four raw rows.
    assert neighbors.rows_scanned == 8


@pytest.mark.unit
def test_newer_cross_slice_root_drift_does_not_skip_a_closer_neighbor() -> None:
    start = datetime(2026, 7, 1)
    end = start + timedelta(days=30)
    target = _row("target", start + timedelta(hours=1))
    raw_drift = _row("drift", target["start_time"] + timedelta(minutes=1))
    drifted_result = _row("drift", target["start_time"] + timedelta(minutes=10))
    closer = _row("closer", target["start_time"] + timedelta(minutes=6))
    builder = _NavigationBuilder(
        target=target,
        raw_rows=[target, raw_drift, closer],
        classified_rows={"drift": drifted_result, "closer": closer},
        start=start,
        end=end,
    )
    executor = _NavigationExecutor(builder)

    neighbors = _read(builder, executor)

    assert neighbors.complete is True
    assert neighbors.error_code is None
    assert neighbors.newer == closer
    assert neighbors.newer != drifted_result


@pytest.mark.unit
def test_trace_navigation_replays_typed_map_and_structured_json_together() -> None:
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START.isoformat(), END.isoformat()],
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
        {
            "column_id": "customer.context",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "map",
                "filter_op": "contains",
                "filter_value": {"tier": "vip", "attempt": 2},
            },
        },
    ]
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
    )

    target_sql, target_params = builder.build_filter_navigation_target_query(
        target_id="trace-target",
        result_limit=2,
    )
    seed_sql, seed_params = builder.build_filter_navigation_seed_page(
        direction="older",
        slice_start=START,
        slice_end=END,
        limit=200,
    )
    match_sql, match_params = builder.build_filter_match_query_from_seed_rows(
        [
            {
                "trace_id": "trace-neighbor",
                "root_span_id": "root-neighbor",
                "start_time": START + timedelta(days=1),
            }
        ]
    )

    for sql in (target_sql, match_sql):
        assert "attrs_string" in sql
        assert "JSONExtractRaw(attributes_extra" in sql
        assert "final_status" not in sql
        assert "customer.context" not in sql
        assert sql.count("SETTINGS") == 1
    assert target_params["candidate_trace_ids"] == ("trace-target",)
    assert target_params["latest_filter_key_0"] == "final_status"
    assert target_params["latest_filter_param_0"] == "rejected"
    assert target_params["latest_filter_key_1"] == "customer.context"
    assert match_params["candidate_trace_ids"] == ("trace-neighbor",)
    # Seeds stay root/time/keyset-only; expensive typed/structured predicates
    # are replayed solely over the finite candidate identities.
    assert "JSONExtract" not in seed_sql
    assert "attrs_string" not in seed_sql
    assert "latest_filter_key_0" not in seed_params
