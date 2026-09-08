"""Offline SQL/budget/cursor contracts for optional raw-root gap discovery."""

from datetime import datetime, timedelta

import pytest

from tracer.selectors import trace_filter_reads as selector
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_bounded_trace_filter_reads import (
    _attribute_filter,
    _FakeBuilder,
    _FakeExecutor,
    _IdentityHydrationFakeBuilder,
    _time_filter,
)

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
END = datetime(2026, 9, 5, 7, 12, 0, 123456)
START = END - timedelta(days=7)
EPOCH = datetime(1970, 1, 1)
AUTO = object()


def _us(value):
    delta = value - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


class _ProbeBuilder(_FakeBuilder):
    def supports_filter_root_time_discovery(self):
        return True

    def build_filter_root_time_discovery_query(self, *, slice_start, slice_end):
        return "root_probe", {"slice_start": slice_start, "slice_end": slice_end}

    def recommended_filter_initial_slice_width(self):
        return timedelta(hours=1)


class _HydrationProbeBuilder(_ProbeBuilder, _IdentityHydrationFakeBuilder):
    pass


class _ProbeExecutor(_FakeExecutor):
    def __init__(
        self,
        builder,
        *,
        result=AUTO,
        fail_probe=None,
        fail_seed=None,
        clock=None,
        delays=None,
    ):
        super().__init__(builder)
        self.probe_result = result
        self.fail_probe = fail_probe
        self.fail_seed = fail_seed
        self.clock = clock
        self.delays = delays or {}
        self.envelopes = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.envelopes.append((query, timeout_ms, dict(settings)))
        if self.clock is not None:
            # Transport latency is wall time too, even when the server's
            # bounded aggregate itself was fast enough to return successfully.
            self.clock[0] += self.delays.get(query, 0) / 1000
        if query == "root_probe":
            self.calls.append((query, params))
            if self.fail_probe:
                raise self.fail_probe
            if self.probe_result is AUTO:
                times = [
                    row["start_time"]
                    for row in self.builder.rows
                    if params["slice_start"] <= row["start_time"] < params["slice_end"]
                ]
                rows = [{"newest_raw_root_us": _us(max(times)) if times else None}]
            else:
                rows = self.probe_result
            return QueryResult(rows, len(rows), "clickhouse", 0.1)
        if query == "seed" and self.fail_seed:
            self.calls.append((query, params))
            raise self.fail_seed
        if query in {"match_identity", "hydrate"}:
            self.calls.append((query, params))
            source = (
                self.builder.rows
                if self.builder.match_rows is None
                else self.builder.match_rows
            )
            rows = [row for row in source if row["id"] in params["candidate_ids"]]
            return QueryResult(rows, len(rows), "clickhouse", 0.1)
        return super().execute_ch_query(
            query, params, timeout_ms=timeout_ms, settings=settings
        )


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(selector, "monotonic", lambda: clock[0])
    monkeypatch.setattr(selector, "_BOUNDED_CONTINUATION_MIN_QUERY_HEADROOM_MS", 3000)
    return clock


def _builder(rows=(), *, start=START, end=END, **kwargs):
    return _ProbeBuilder(
        list(rows), start=start, end=end, recommended_seed_batch_size=4, **kwargs
    )


def _read(builder, executor=None, **kwargs):
    executor = executor or _ProbeExecutor(builder)
    options = {
        "builder": builder,
        "analytics": executor,
        "filters": [_time_filter(builder.start, builder.end)],
        "key_field": "id",
        "page_number": 0,
        "page_size": 1,
        "deadline_ms": 5000,
        "max_seed_attempts": 24,
        "max_query_count": 64,
        "max_candidates": 4,
        "classify_batch_size": 4,
        "include_incomplete_rows": True,
        "bounded_continuation": True,
        "root_time_discovery": True,
    }
    options.update(kwargs)
    return selector.read_bounded_filter_page(**options)


@pytest.mark.parametrize(
    "op", ["equals", "not_equals", "not_in", "is_null", "is_not_null"]
)
@pytest.mark.parametrize("days", [7, 30, 365])
def test_builder_uses_only_scoped_raw_root_time_not_attribute_or_child_filters(
    op, days
):
    filters = [
        _time_filter(END - timedelta(days=days), END),
        _attribute_filter("company_id", ["a"] if op == "not_in" else "a", operation=op),
    ]
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=filters, project_version_id="version"
    )
    sql, params = builder.build_filter_root_time_discovery_query(
        slice_start=END - timedelta(hours=24), slice_end=END
    )
    assert "maxOrNull(toUnixTimestamp64Micro(start_time)) AS newest_raw_root_us" in sql
    assert "project_id = %(project_id)s" in sql
    assert "project_version_id = %(project_version_id)s" in sql
    assert params["project_id"] == PROJECT and params["project_version_id"] == "version"
    assert params["root_discovery_start_us"] == _us(END - timedelta(hours=24))
    assert params["root_discovery_end_us"] == _us(END)
    assert "WHERE is_deleted = 0" in sql
    assert "parent_span_id IS NULL OR parent_span_id = ''" in sql
    for forbidden in (
        "FINAL",
        "GROUP BY",
        "ORDER BY",
        "LIMIT",
        "company_id",
        "attrs_",
        "JSON",
        "SAMPLE",
        "trace_id IN",
    ):
        assert forbidden not in sql


def test_builder_keeps_org_scope_and_refuses_overwide_or_out_of_range_probe():
    builder = TraceListQueryBuilderV2(
        project_ids=[PROJECT], filters=[_time_filter(START, END)]
    )
    sql, params = builder.build_filter_root_time_discovery_query(
        slice_start=END - timedelta(days=1), slice_end=END
    )
    assert "project_id IN %(project_ids)s" in sql and params["project_ids"]
    with pytest.raises(ValueError, match="24 hours"):
        builder.build_filter_root_time_discovery_query(slice_start=START, slice_end=END)
    with pytest.raises(ValueError, match="request window"):
        builder.build_filter_root_time_discovery_query(
            slice_start=START - timedelta(seconds=1), slice_end=START
        )


@pytest.mark.parametrize(
    "options",
    [
        {"bounded_sampling_rate": 50, "bounded_sampling_salt": "test"},
        {"bounded_identity_only": True},
        {"bounded_internal_scan": True},
        {"search": "anything"},
        {"sort_params": [{"column_id": "cost"}]},
    ],
)
def test_builder_does_not_enable_internal_sampled_or_custom_order_paths(options):
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=[_time_filter(START, END)], **options
    )
    assert not builder.supports_filter_root_time_discovery()
    with pytest.raises(ValueError, match="unavailable"):
        builder.build_filter_root_time_discovery_query(
            slice_start=END - timedelta(days=1), slice_end=END
        )


def test_null_tail_advances_only_three_proven_days_and_survives_seed_failure():
    builder = _builder()
    executor = _ProbeExecutor(builder, fail_seed=ReadDeadlineExceeded("seed budget"))
    page = _read(builder, executor)
    assert not page.complete and not page.rows
    assert page.continuation_slice_end == END - timedelta(days=3)
    assert page.continuation_before_start_time is None
    assert page.error_code == "read_budget_exceeded"
    assert [a.kind for a in page.attempts] == ["root_time_discovery"] * 3 + ["seed"]
    assert page.attempts[0].slice_start == END - timedelta(days=1)
    assert page.attempts[0].slice_end == END
    assert page.query_count == 4 and page.rows_returned == 3
    for index, attempt in enumerate(page.attempts[:3]):
        assert attempt.slice_start == END - timedelta(days=index + 1)
        assert attempt.slice_end == END - timedelta(days=index)


def test_completed_null_is_exhaustion_only_if_probe_covers_entire_remainder():
    builder = _builder(start=END - timedelta(hours=6))
    page = _read(builder)
    assert page.complete and not page.rows and page.error_code is None
    assert page.query_count == 1 and page.attempts[0].kind == "root_time_discovery"


def test_null_tail_reprobes_until_hit_then_keeps_exact_older_match():
    row = {
        "id": "older-match",
        "root_span_id": "root",
        "start_time": END - timedelta(hours=36),
    }
    builder = _builder([row])
    executor = _ProbeExecutor(builder)
    page = _read(builder, executor)
    assert page.complete and page.rows == [row]
    assert sum(query == "root_probe" for query, _ in executor.calls) == 2
    seeds = [params for query, params in executor.calls if query == "seed"]
    assert seeds[0]["slice_end"] == row["start_time"].replace(
        minute=0, second=0, microsecond=0
    ) + timedelta(hours=1)
    assert any(
        params["slice_start"] <= row["start_time"] < params["slice_end"]
        for params in seeds
    )


def test_admitted_probe_and_later_queries_share_the_same_count_budget():
    builder = _builder()
    executor = _ProbeExecutor(builder)
    page = _read(builder, executor, max_query_count=3)
    assert not page.complete and page.error_code == "query_budget_exceeded"
    assert page.query_count == len(executor.calls) == 3
    assert [query for query, _ in executor.calls] == ["root_probe", "seed", "seed"]
    assert page.continuation_slice_end > START


def test_resumed_empty_checkpoint_advances_next_day_not_whole_year():
    builder = _builder(start=END - timedelta(days=365))
    executor = _ProbeExecutor(builder, fail_seed=ReadDeadlineExceeded("seed budget"))
    remaining_end = END - timedelta(days=3)
    page = _read(
        builder,
        executor,
        continuation_slice_end=remaining_end,
        continuation_slice_start=remaining_end - timedelta(hours=2),
        carry_continuation_slice_width=True,
    )
    assert executor.calls[0][1] == {
        "slice_start": remaining_end - timedelta(days=1),
        "slice_end": remaining_end,
    }
    assert page.continuation_slice_end == remaining_end - timedelta(days=3)


def test_repeated_absence_probes_share_original_optional_wall_budget(_clock):
    builder = _builder()
    executor = _ProbeExecutor(
        builder,
        clock=_clock,
        delays={"root_probe": 600},
        fail_seed=ReadDeadlineExceeded("seed budget"),
    )
    page = _read(builder, executor)
    probes = [entry for entry in executor.envelopes if entry[0] == "root_probe"]
    assert [timeout for _, timeout, _ in probes] == [1000, 400]
    assert page.continuation_slice_end == END - timedelta(days=2)
    assert not page.complete


def test_positive_jump_keeps_full_hour_and_does_not_publish_probe_as_a_match():
    newest = END - timedelta(hours=20, microseconds=1)
    hour = newest.replace(minute=0, second=0, microsecond=0)
    builder = _builder([{"id": "stale-root", "start_time": newest}], match_rows=[])
    executor = _ProbeExecutor(builder, fail_seed=ReadDeadlineExceeded("seed budget"))
    page = _read(builder, executor)
    assert not page.rows and not page.complete
    seed = executor.calls[1][1]
    assert seed["slice_start"] == hour and seed["slice_end"] == hour + timedelta(
        hours=1
    )
    assert seed["before_start_time"] is None and seed["before_id"] is None
    assert page.continuation_slice_start == hour
    assert page.continuation_slice_end == hour + timedelta(hours=1)


def test_subsecond_equal_time_roots_keep_trace_keyset_and_exact_classifier():
    stamp = END - timedelta(hours=12)
    rows = [
        {"id": identity, "root_span_id": identity, "start_time": stamp}
        for identity in ["trace-a", "trace-b", "trace-c"]
    ]
    builder = _builder(rows)
    executor = _ProbeExecutor(builder)
    first = _read(builder, executor)
    assert first.complete and first.has_more and first.rows[0]["id"] == "trace-c"
    second_executor = _ProbeExecutor(builder)
    second = _read(
        builder, second_executor, cursor_start_time=stamp, cursor_order_token="trace-c"
    )
    assert second.complete and second.rows[0]["id"] == "trace-b"
    assert all(query != "root_probe" for query, _ in second_executor.calls)
    assert second_executor.calls[0][1]["before_start_time"] == stamp


@pytest.mark.parametrize(
    "kind",
    ["tombstoned", "root-to-child", "time-corrected", "alternate-root", "reassigned"],
)
def test_stale_raw_hit_never_replaces_latest_state_classification(kind):
    stamp = END - timedelta(hours=10)
    raw = {"id": "trace", "root_span_id": "raw", "start_time": stamp}
    live = {
        **raw,
        "root_span_id": "current",
        "start_time": stamp - timedelta(minutes=5),
    }
    matches = [live] if kind in {"time-corrected", "alternate-root"} else []
    builder = _builder([raw], match_rows=matches)
    actual = _read(builder)
    baseline = _read(builder, root_time_discovery=False)
    assert actual.rows == baseline.rows
    assert actual.complete == baseline.complete
    assert all(row.get("root_span_id") != "raw" for row in actual.rows)


@pytest.mark.parametrize(
    "error", [ReadDeadlineExceeded("probe cap"), TimeoutError("transport cap")]
)
def test_probe_budget_failure_is_optional_charged_and_does_not_skip_boundary(error):
    builder = _builder(start=END - timedelta(hours=2))
    executor = _ProbeExecutor(builder, fail_probe=error)
    page = _read(builder, executor)
    assert page.complete and page.error_code is None
    assert executor.calls[1][1]["slice_end"] == END
    assert sum(query == "root_probe" for query, _ in executor.calls) == 1
    assert page.attempts[0].error_code is not None
    assert page.query_count == len(executor.calls)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{}],
        [{"newest_raw_root_us": None}] * 2,
        [{"newest_raw_root_us": False}],
        [{"newest_raw_root_us": "1"}],
        [{"newest_raw_root_us": _us(END)}],
        [{"newest_raw_root_us": _us(START)}],
    ],
)
def test_missing_partial_or_invalid_aggregate_is_not_an_absence_proof(rows):
    builder = _builder()
    with pytest.raises(ValueError, match="root discovery"):
        _read(builder, _ProbeExecutor(builder, result=rows))


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("compiler defect"),
        KeyError("missing column"),
        ValueError("bad SQL"),
    ],
)
def test_programming_errors_are_not_swallowed(error):
    builder = _builder()
    with pytest.raises(type(error)):
        _read(builder, _ProbeExecutor(builder, fail_probe=error))


def test_probe_uses_stricter_caller_caps_and_throw_modes():
    builder = _builder(start=END - timedelta(hours=2))
    executor = _ProbeExecutor(builder)
    _read(
        builder,
        executor,
        query_timeout_ms=500,
        read_settings={
            "max_threads": 4,
            "max_memory_usage": 123456,
            "max_bytes_to_read": 234567,
            "read_overflow_mode": "break",
            "result_overflow_mode": "break",
            "timeout_overflow_mode": "break",
        },
    )
    _, timeout, limits = executor.envelopes[0]
    assert timeout == 500
    assert limits["max_threads"] == 1
    assert limits["max_memory_usage"] == 123456
    assert limits["max_bytes_to_read"] == 234567
    assert limits["max_result_rows"] == 1
    assert all(
        limits[key] == "throw"
        for key in (
            "read_overflow_mode",
            "result_overflow_mode",
            "timeout_overflow_mode",
        )
    )


def test_probe_cannot_raise_looser_caller_memory_or_byte_limits():
    builder = _builder(start=END - timedelta(hours=2))
    executor = _ProbeExecutor(builder)
    _read(
        builder,
        executor,
        read_settings={
            "max_memory_usage": 4 * 1024**3,
            "max_bytes_to_read": 4 * 1024**3,
        },
    )
    _, timeout, limits = executor.envelopes[0]
    assert timeout <= 1000
    assert limits["max_memory_usage"] == limits["max_bytes_to_read"] == 1024**3


def test_successful_jump_retains_checkpoint_when_transport_consumes_headroom(_clock):
    builder = _builder()
    executor = _ProbeExecutor(builder, clock=_clock, delays={"root_probe": 2100})
    page = _read(builder, executor)
    assert not page.complete and page.error_code == "deadline_exceeded"
    assert page.continuation_slice_end == END - timedelta(days=1)
    assert page.query_count == 1


def test_result_returned_after_request_deadline_does_not_commit_new_checkpoint(_clock):
    builder = _builder()
    executor = _ProbeExecutor(builder, clock=_clock, delays={"root_probe": 5100})
    page = _read(builder, executor)
    assert not page.complete and page.error_code == "deadline_exceeded"
    assert page.continuation_slice_end is None


@pytest.mark.parametrize(
    "setting",
    [
        "default_off",
        "limits_not_enforced",
        "low_time",
        "low_queries",
        "unfinished_keyset",
    ],
)
def test_probe_admission_preserves_original_path(setting):
    builder = _builder()
    executor = _ProbeExecutor(builder, fail_seed=ReadDeadlineExceeded("seed budget"))
    kwargs = {}
    if setting == "default_off":
        kwargs["root_time_discovery"] = False
    elif setting == "limits_not_enforced":
        executor.supports_per_query_read_settings = False
    elif setting == "low_time":
        kwargs["deadline_ms"] = 3900
    elif setting == "low_queries":
        kwargs["max_query_count"] = 2
    else:
        kwargs.update(
            continuation_slice_end=END,
            continuation_slice_start=END - timedelta(hours=2),
            continuation_before_start_time=END - timedelta(minutes=10),
            continuation_before_id="trace",
        )
    _read(builder, executor, **kwargs)
    assert all(query != "root_probe" for query, _ in executor.calls)


def test_probe_reserves_identity_page_hydration_query():
    builder = _HydrationProbeBuilder(
        [], start=START, end=END, recommended_seed_batch_size=4
    )
    executor = _ProbeExecutor(builder, fail_seed=ReadDeadlineExceeded("seed budget"))
    _read(builder, executor, max_query_count=3)
    assert all(query != "root_probe" for query, _ in executor.calls)


def test_opt_in_is_rejected_outside_exact_bounded_cursor():
    with pytest.raises(ValueError, match="exact bounded cursor"):
        _read(_builder(), bounded_continuation=False)
