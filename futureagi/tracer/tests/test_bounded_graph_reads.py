from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.selectors.trace_filter_reads import BoundedFilterPage
from tracer.services.clickhouse import bounded_graph_reads, graph_dispatch
from tracer.services.clickhouse.bounded_graph_reads import (
    BoundedGraphReadError,
    GraphCandidateSample,
    aggregate_system_candidate_graph,
    read_graph_candidates,
)
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

PROJECT_ID = "00000000-0000-4000-8000-000000000901"
EVAL_ID = "00000000-0000-4000-8000-000000000902"
LABEL_ID = "00000000-0000-4000-8000-000000000903"
START = datetime(2026, 1, 1, 0, 0)
END = START + timedelta(minutes=5)


def _unix_microseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1)
    delta = value - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _date_filter(start: datetime = START, end: datetime = END) -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def _date_bound_filter(operation: str, value: datetime) -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": operation,
            "filter_value": value.isoformat(),
        },
    }


def _attribute_filter(
    key: str,
    value,
    *,
    filter_type: str = "text",
    filter_op: str = "equals",
) -> dict:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": value,
        },
    }


def _system_text_filter(
    key: str,
    value: str,
    *,
    filter_op: str = "equals",
) -> dict:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": filter_op,
            "filter_value": value,
        },
    }


def _annotation_filter(label_id: str, value: object) -> dict:
    return {
        "column_id": label_id,
        "filter_config": {
            "col_type": "ANNOTATION",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


class _Result(SimpleNamespace):
    def __init__(self, rows):
        super().__init__(data=rows, columns=list(rows[0]) if rows else [])


class _CandidateAnalytics:
    def __init__(self, *, observe_type: str, rows: list[dict]):
        self.observe_type = observe_type
        self.rows = rows
        self.calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params, timeout_ms, settings))
        seed_column = "trace_id" if self.observe_type == "trace" else "id"
        if "filter_anchor_limit" in params:
            anchor_start = params.get("filter_anchor_start")
            anchor_end = params.get("filter_anchor_end")
            anchor_rows = [
                row
                for row in self.rows
                if (
                    anchor_start is None
                    or anchor_end is None
                    or anchor_start <= row["start_time"] < anchor_end
                )
            ]
            if params.get("filter_graph_key_witness"):
                # The production probe follows the physical-key suffix with
                # descending hour/identity order. Model its newest-first
                # witness diversity instead of preserving fixture insertion.
                anchor_rows.sort(
                    key=lambda row: (row["start_time"], str(row[seed_column])),
                    reverse=True,
                )
            if self.observe_type == "span":
                seen = set()
                rows = []
                for row in anchor_rows:
                    identity = (
                        str(row.get("trace_id") or ""),
                        str(row.get("id") or ""),
                        row.get("start_time"),
                    )
                    if identity in seen:
                        continue
                    seen.add(identity)
                    rows.append(
                        {
                            "project_id": PROJECT_ID,
                            "trace_id": identity[0],
                            "id": identity[1],
                            "start_time": identity[2],
                        }
                    )
                    if len(rows) >= params["filter_anchor_limit"]:
                        break
                return _Result(rows)
            return _Result(
                [
                    {"trace_id": trace_id}
                    for trace_id in list(
                        dict.fromkeys(str(row["trace_id"]) for row in anchor_rows)
                    )[: params["filter_anchor_limit"]]
                ]
            )
        if "filter_seed_limit" in params:
            slice_start = params["filter_slice_start"]
            slice_end = params["filter_slice_end"]
            before_start_us = params.get("filter_before_start_us")
            before_time = (
                datetime(1970, 1, 1) + timedelta(microseconds=before_start_us)
                if before_start_us is not None
                else None
            )
            before_id = params.get("filter_before_id")
            candidates = [
                row
                for row in self.rows
                if slice_start <= row["start_time"] < slice_end
                and (
                    before_time is None
                    or (row["start_time"], str(row[seed_column]))
                    < (before_time, str(before_id))
                )
            ]
            candidates.sort(
                key=lambda row: (row["start_time"], str(row[seed_column])),
                reverse=True,
            )
            seed_rows = []
            for row in candidates[: params["filter_seed_limit"]]:
                seed_row = {
                    seed_column: row[seed_column],
                    "start_time": row["start_time"],
                }
                if self.observe_type == "span":
                    seed_row["trace_id"] = row["trace_id"]
                if self.observe_type == "trace" and row.get("root_span_id"):
                    seed_row["root_span_id"] = row["root_span_id"]
                seed_rows.append(seed_row)
            return _Result(seed_rows)
        candidate_key = (
            "candidate_trace_ids"
            if self.observe_type == "trace"
            else "candidate_span_ids"
        )
        allowed = set(params[candidate_key])
        return _Result([row for row in self.rows if str(row[seed_column]) in allowed])


class _LatestRareCandidateAnalytics(_CandidateAnalytics):
    """Model a common raw attribute whose latest-state match is rare."""

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "graph_eval_config_id" in params:
            self.calls.append((query, params, timeout_ms, settings))
            return _Result(
                [
                    {
                        "created_at": params["graph_start_date"] + timedelta(minutes=1),
                        "output_bool": None,
                        "output_float": 0.5,
                        "output_str": None,
                        "output_str_list": "[]",
                        "error": 0,
                    }
                ]
            )
        candidate_key = (
            "candidate_trace_ids"
            if self.observe_type == "trace"
            else "candidate_span_ids"
        )
        if candidate_key not in params:
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )
        self.calls.append((query, params, timeout_ms, settings))
        seed_column = "trace_id" if self.observe_type == "trace" else "id"
        allowed = set(params[candidate_key])
        return _Result(
            [
                row
                for row in self.rows
                if str(row[seed_column]) in allowed and row.get("matches_latest")
            ]
        )


class _CrossStratumTraceAnalytics:
    """Model one trace whose root and matching children occupy other strata."""

    def __init__(self, *, root_time: datetime, child_times: tuple[datetime, ...]):
        self.root_time = root_time
        self.child_times = child_times
        self.calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params, timeout_ms, settings))
        if "filter_anchor_limit" in params:
            if any(
                params["filter_anchor_start"]
                <= child_time
                < params["filter_anchor_end"]
                for child_time in self.child_times
            ):
                return _Result([{"trace_id": "cross-stratum-trace"}])
            return _Result([])
        if "filter_seed_limit" in params:
            if (
                params["filter_slice_start"]
                <= self.root_time
                < params["filter_slice_end"]
            ):
                return _Result(
                    [
                        {
                            "trace_id": "cross-stratum-trace",
                            "root_span_id": "root-span",
                            "start_time": self.root_time,
                        }
                    ]
                )
            return _Result([])
        if "candidate_trace_ids" in params:
            request_start = params["candidate_start_date"]
            request_end = params["candidate_end_date"]
            children_match = all(
                request_start <= child_time < request_end
                for child_time in self.child_times
            )
            if (
                "cross-stratum-trace" in params["candidate_trace_ids"]
                and request_start <= self.root_time < request_end
                and children_match
            ):
                return _Result(
                    [
                        {
                            "trace_id": "cross-stratum-trace",
                            "root_span_id": "root-span",
                            "start_time": self.root_time,
                        }
                    ]
                )
            return _Result([])
        raise AssertionError("unexpected graph query")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("observe_type", "key", "value"),
    [
        ("trace", "final_status", "Rejected"),
        ("span", "prompt_slug", "agent_2_identity_disclosure"),
    ],
)
def test_filtered_graph_candidates_are_finite_latest_state_samples(
    observe_type, key, value
):
    identity_key = "trace_id" if observe_type == "trace" else "id"
    row = {
        identity_key: "trace-1" if observe_type == "trace" else "span-1",
        "root_span_id": "span-1",
        "start_time": START + timedelta(minutes=4),
        "latency_ms": 25.0,
        "cost": 0.1,
        "total_tokens": 11,
        "prompt_tokens": 7,
        "completion_tokens": 4,
        "status": "OK",
    }
    if observe_type == "span":
        row["trace_id"] = "trace-1"
    analytics = _CandidateAnalytics(observe_type=observe_type, rows=[row])

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_date_filter(), _attribute_filter(key, value)],
        observe_type=observe_type,
    )

    assert sample.rows == (row,)
    # Scalar typed Maps are authoritative. JSON/map/array filter types are
    # rejected before a query, so an exhausted scalar range is exact.
    assert sample.query_complete is True
    assert sample.query_status == "complete"
    assert sample.query_error_code is None
    assert len(analytics.calls) >= 2

    seed_query, seed_params, seed_timeout, seed_settings = analytics.calls[0]
    candidate_param = (
        "candidate_trace_ids" if observe_type == "trace" else "candidate_span_ids"
    )
    classify_query, classify_params, classify_timeout, classify_settings = next(
        call for call in analytics.calls if candidate_param in call[1]
    )
    if observe_type == "trace":
        normalized_seed_query = " ".join(seed_query.split())
        assert "SELECT trace_id FROM spans" in normalized_seed_query
        assert "SELECT DISTINCT" not in normalized_seed_query
        assert (
            "ORDER BY observation_type DESC, service_name DESC, "
            "toStartOfHour(start_time) DESC, trace_id DESC, id DESC"
            in normalized_seed_query
        )
        assert "LIMIT 1 BY trace_id" in normalized_seed_query
        assert seed_params["filter_anchor_limit"] == 513
    else:
        assert "LIMIT %(filter_anchor_limit)s" in seed_query
        assert seed_params["filter_anchor_limit"] == 513
        assert "LIMIT 1 BY project_id, trace_id, id, start_time" in seed_query
    assert "argMax(" in classify_query
    assert "FINAL" not in classify_query
    assert classify_params[candidate_param] in {("trace-1",), ("span-1",)}
    assert seed_timeout <= bounded_graph_reads.GRAPH_CANDIDATE_DEADLINE_MS
    assert classify_timeout <= bounded_graph_reads.GRAPH_CANDIDATE_DEADLINE_MS
    assert seed_settings["max_threads"] == classify_settings["max_threads"] == 1


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_short_graph_map_filter_is_candidate_scoped(observe_type: str) -> None:
    identity_key = "trace_id" if observe_type == "trace" else "id"
    row = {
        identity_key: "trace-1" if observe_type == "trace" else "span-1",
        "root_span_id": "span-1",
        "start_time": START + timedelta(minutes=4),
    }
    if observe_type == "span":
        row["trace_id"] = "trace-1"
    analytics = _CandidateAnalytics(observe_type=observe_type, rows=[row])

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(),
            _attribute_filter(
                "customer.context",
                {"tier": "vip", "attempt": 2},
                filter_type="json",
                filter_op="contains",
            ),
        ],
        observe_type=observe_type,
    )

    assert sample.rows == (row,)
    seed_query = analytics.calls[0][0]
    classify_query, classify_params, _, _ = next(
        call
        for call in analytics.calls
        if ("candidate_trace_ids" if observe_type == "trace" else "candidate_span_ids")
        in call[1]
    )
    assert "attributes_extra" not in seed_query
    assert "JSONExtractRaw(attributes_extra" in classify_query
    assert "vip" not in classify_query
    assert "vip" in classify_params.values()


@pytest.mark.unit
def test_zero_width_trace_graph_window_is_exact_without_a_query() -> None:
    analytics = _CandidateAnalytics(observe_type="trace", rows=[])

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START, START),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="trace",
    )

    assert sample.rows == ()
    assert sample.query_complete is True
    assert sample.query_status == "complete"
    assert sample.window_start == sample.window_end == START
    assert analytics.calls == []


@pytest.mark.unit
def test_one_microsecond_trace_graph_window_keeps_exact_membership_bounds() -> None:
    window_end = START + timedelta(microseconds=1)
    row = {
        "trace_id": "trace-short",
        "root_span_id": "root-short",
        "start_time": START,
    }
    analytics = _CandidateAnalytics(observe_type="trace", rows=[row])

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START, window_end),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="trace",
    )

    assert sample.rows == (row,)
    classifier_params = next(
        params for _, params, *_ in analytics.calls if "candidate_trace_ids" in params
    )
    assert classifier_params["candidate_start_date"] == START
    assert classifier_params["candidate_end_date"] == window_end


@pytest.mark.unit
def test_default_long_window_is_frozen_once_for_every_trace_stratum(
    monkeypatch,
) -> None:
    """A missing date filter must not derive a new ``now`` per builder."""

    frozen_start = datetime(2026, 6, 1)
    frozen_end = frozen_start + timedelta(days=30)
    original_parse_time_range = BaseQueryBuilder.parse_time_range
    default_calls = 0

    def drifting_default(filters, *, strict=False):
        nonlocal default_calls
        has_positive_time_leaf = any(
            (item.get("column_id") or item.get("columnId"))
            in {"created_at", "start_time"}
            and not BaseQueryBuilder.is_datetime_complement_filter(item)
            for item in filters
        )
        if has_positive_time_leaf:
            return original_parse_time_range(filters, strict=strict)
        default_calls += 1
        drift = timedelta(microseconds=default_calls)
        return frozen_start + drift, frozen_end + drift

    monkeypatch.setattr(
        BaseQueryBuilder,
        "parse_time_range",
        staticmethod(drifting_default),
    )
    analytics = _CandidateAnalytics(observe_type="trace", rows=[])

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_system_text_filter("call_type", "LLM")],
        observe_type="trace",
    )

    assert sample.rows == ()
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"
    assert (
        sample.sampling_strata_completed
        == sample.sampling_strata
        == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    )
    assert default_calls == 1
    seed_ranges = [
        (params["filter_slice_start"], params["filter_slice_end"])
        for _, params, *_ in analytics.calls
        if "filter_seed_limit" in params
    ]
    assert seed_ranges
    assert all(
        end - start == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
        for start, end in seed_ranges
    )
    assert max(end for _, end in seed_ranges) == frozen_end + timedelta(microseconds=1)


@pytest.mark.unit
def test_long_time_only_graph_covers_sparse_rows_outside_micro_slice() -> None:
    """The synthetic identity leaf must not turn time-only reads into samples."""

    window_start = START - timedelta(days=365)
    sparse_time = window_start + timedelta(days=17, minutes=1)
    row = {
        "trace_id": "sparse-time-only-trace",
        "root_span_id": "sparse-root",
        "start_time": sparse_time,
    }
    analytics = _CandidateAnalytics(observe_type="trace", rows=[row])

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_date_filter(window_start, START)],
        observe_type="trace",
        allow_time_only_seed=True,
    )

    assert sample.rows == (row,)
    assert sample.query_complete is True
    assert sample.query_status == "complete"
    seed_ranges = [
        (params["filter_slice_start"], params["filter_slice_end"])
        for _, params, *_ in analytics.calls
        if "filter_seed_limit" in params
    ]
    assert len(seed_ranges) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    assert any(start <= sparse_time < end for start, end in seed_ranges)
    assert all(
        end - start > bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
        for start, end in seed_ranges
    )


@pytest.mark.unit
def test_long_graph_map_filter_uses_bounded_strata_and_candidate_classifiers() -> None:
    long_start = START - timedelta(days=180)
    row = {
        "id": "span-1",
        "trace_id": "trace-1",
        # The unindexed lane samples the fixed five-minute tail of every
        # temporal stratum; keep this positive witness in the final tail.
        "start_time": END - timedelta(minutes=1),
    }
    analytics = _CandidateAnalytics(observe_type="span", rows=[row])

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(long_start, END),
            _attribute_filter(
                "customer.context",
                {"tier": "vip"},
                filter_type="map",
                filter_op="equals",
            ),
        ],
        observe_type="span",
        deadline_ms=8_000,
    )

    assert sample.rows == (row,)
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert all(
        "attributes_extra" not in query
        for query, params, *_ in analytics.calls
        if "filter_seed_limit" in params
    )
    classifiers = [
        (query, params)
        for query, params, *_ in analytics.calls
        if "candidate_span_ids" in params
    ]
    assert classifiers
    assert all("JSONExtractRaw(attributes_extra" in query for query, _ in classifiers)


@pytest.mark.unit
def test_trace_root_before_child_after_stratum_boundary_uses_full_membership_window():
    window_start = datetime(2026, 1, 1)
    window_end = window_start + timedelta(days=8)
    boundary = window_start + timedelta(days=2)
    root_time = boundary - timedelta(microseconds=1)
    child_time = boundary + timedelta(microseconds=1)
    analytics = _CrossStratumTraceAnalytics(
        root_time=root_time,
        child_times=(child_time,),
    )

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _system_text_filter("call_type", "LLM"),
        ],
        observe_type="trace",
    )

    assert tuple(row["trace_id"] for row in sample.rows) == ("cross-stratum-trace",)
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    seed_calls = [call for call in analytics.calls if "filter_seed_limit" in call[1]]
    assert all("parent_span_id IS NULL" in query for query, *_ in seed_calls)
    assert any(
        params["filter_slice_start"] <= root_time < params["filter_slice_end"]
        for _, params, *_ in seed_calls
    )
    classifier_params = next(
        params for _, params, *_ in analytics.calls if "candidate_trace_ids" in params
    )
    assert classifier_params["candidate_start_date"] == window_start
    assert classifier_params["candidate_end_date"] == window_end


@pytest.mark.unit
def test_trace_multi_child_filters_match_across_separate_temporal_strata():
    window_start = datetime(2026, 1, 1)
    window_end = window_start + timedelta(days=8)
    root_time = window_start + timedelta(hours=6)
    child_times = (
        window_start + timedelta(days=2, hours=6),
        window_start + timedelta(days=6, hours=6),
    )
    analytics = _CrossStratumTraceAnalytics(
        root_time=root_time,
        child_times=child_times,
    )

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            # Keep one genuinely selective value-index companion so this test
            # exercises cross-stratum membership rather than the deliberately
            # sampled text/boolean graph lane.
            _attribute_filter("score", 0.5, filter_type="number"),
            _attribute_filter(
                "customer.context",
                {"tier": "vip"},
                filter_type="json",
                filter_op="contains",
            ),
        ],
        observe_type="trace",
    )

    assert tuple(row["trace_id"] for row in sample.rows) == ("cross-stratum-trace",)
    # Temporal child anchors found this positive candidate, but cannot prove
    # the absence of another trace whose sole child witness lies outside the
    # root window. The legacy candidate sampler must never label that set as
    # exact; exact aggregations use the exhaustive graph reader.
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"
    classifier_query, classifier_params, *_ = next(
        call for call in analytics.calls if "candidate_trace_ids" in call[1]
    )
    assert (
        sum("filter_anchor_limit" in params for _, params, *_ in analytics.calls)
        == bounded_graph_reads.GRAPH_TRACE_STRATA
    )
    assert (
        sum("candidate_trace_ids" in params for _, params, *_ in analytics.calls) == 1
    )
    assert classifier_params["candidate_start_date"] == window_start
    assert classifier_params["candidate_end_date"] == window_end
    assert classifier_query.count("countIf(") >= 3
    assert "filter_witness_0" not in classifier_query
    assert "filter_witness_1" not in classifier_query


@pytest.mark.unit
def test_cross_stratum_trace_sample_is_full_coverage_and_never_marked_exact():
    window_start = datetime(2026, 1, 1)
    window_end = window_start + timedelta(days=8)
    stratum_count = bounded_graph_reads.GRAPH_TRACE_STRATA
    stratum_width = (window_end - window_start) / stratum_count
    root_times: dict[str, datetime] = {}
    child_times: dict[str, datetime] = {}
    for stratum in range(stratum_count):
        for index in range(60):
            trace_id = f"trace-{stratum}-{index:02d}"
            root_times[trace_id] = (
                window_start
                + (stratum_width * stratum)
                + timedelta(hours=6, microseconds=index)
            )
            child_stratum = stratum + 1 if stratum < stratum_count - 1 else stratum - 1
            child_times[trace_id] = (
                window_start
                + (stratum_width * child_stratum)
                + timedelta(hours=12, microseconds=index)
            )

    class _SampleAnalytics:
        def __init__(self):
            self.calls = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((query, params, timeout_ms, settings))
            if "filter_anchor_limit" in params:
                rows = [
                    {"trace_id": trace_id}
                    for trace_id, child_time in child_times.items()
                    if params["filter_anchor_start"]
                    <= child_time
                    < params["filter_anchor_end"]
                ]
                return _Result(rows[: params["filter_anchor_limit"]])
            if "filter_seed_limit" in params:
                rows = [
                    {
                        "trace_id": trace_id,
                        "root_span_id": f"root-{trace_id}",
                        "start_time": root_time,
                    }
                    for trace_id, root_time in root_times.items()
                    if params["filter_slice_start"]
                    <= root_time
                    < params["filter_slice_end"]
                ]
                rows.sort(
                    key=lambda row: (row["start_time"], row["trace_id"]),
                    reverse=True,
                )
                return _Result(rows[: params["filter_seed_limit"]])
            if "candidate_trace_ids" in params:
                request_start = params["candidate_start_date"]
                request_end = params["candidate_end_date"]
                return _Result(
                    [
                        {
                            "trace_id": trace_id,
                            "root_span_id": f"root-{trace_id}",
                            "start_time": root_times[trace_id],
                        }
                        for trace_id in params["candidate_trace_ids"]
                        if request_start <= root_times[trace_id] < request_end
                        and request_start <= child_times[trace_id] < request_end
                    ]
                )
            raise AssertionError("unexpected graph query")

    analytics = _SampleAnalytics()
    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("score", 0.5, filter_type="number"),
            _attribute_filter(
                "customer.context",
                {"tier": "vip"},
                filter_type="json",
                filter_op="contains",
            ),
        ],
        observe_type="trace",
    )

    # The first child-time stratum is genuinely empty; every other stratum
    # contributes the bounded trace sample. Coverage metadata still records
    # all trace probes, including the exhausted empty stratum.
    assert len(sample.rows) == (
        (bounded_graph_reads.GRAPH_TRACE_STRATA - 1)
        * bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM
    )
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"
    assert sample.sampling_strata == bounded_graph_reads.GRAPH_TRACE_STRATA
    assert sample.sampling_strata_completed == sample.sampling_strata
    classifier_calls = [
        call for call in analytics.calls if "candidate_trace_ids" in call[1]
    ]
    classifier_sizes = [
        len(params["candidate_trace_ids"]) for _, params, *_ in classifier_calls
    ]
    expected_union_size = (bounded_graph_reads.GRAPH_TRACE_STRATA - 1) * (
        bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM + 1
    )
    assert sum(classifier_sizes) == expected_union_size
    assert all(
        size <= bounded_graph_reads.GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE
        for size in classifier_sizes
    )
    classifier_ids = [
        trace_id
        for _, params, *_ in classifier_calls
        for trace_id in params["candidate_trace_ids"]
    ]
    assert len(classifier_ids) == len(set(classifier_ids)) == sum(classifier_sizes)
    assert len(classifier_ids) <= (
        bounded_graph_reads.GRAPH_TRACE_STRATA
        * (bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM + 1)
    )
    assert all(
        params["candidate_start_date"] == window_start
        and params["candidate_end_date"] == window_end
        for _, params, *_ in classifier_calls
    )


@pytest.mark.unit
def test_map_number_boolean_and_multiple_predicates_share_one_finite_classifier():
    row = {
        "id": "span-1",
        "trace_id": "trace-1",
        "start_time": START + timedelta(minutes=4),
    }
    analytics = _CandidateAnalytics(observe_type="span", rows=[row])
    filters = [
        _date_filter(),
        _attribute_filter("score", 0.5, filter_type="number", filter_op="greater_than"),
        _attribute_filter("accepted", True, filter_type="boolean"),
        _attribute_filter("final_status", ["Rejected", "Accepted"], filter_op="in"),
    ]

    read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        observe_type="span",
    )
    query, params, _, _ = analytics.calls[1]
    assert "attrs_number" in query
    assert "attrs_bool" in query
    assert "attrs_string" in query
    assert query.count(" AND ") >= 3
    assert ("rejected", "accepted") in params.values()


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_mixed_annotation_graph_reuses_candidate_scoped_list_classifier(
    observe_type,
):
    identity_key = "trace_id" if observe_type == "trace" else "id"
    row = {
        identity_key: "trace-1" if observe_type == "trace" else "span-1",
        "start_time": START + timedelta(minutes=4),
    }
    if observe_type == "trace":
        row["root_span_id"] = "span-1"
    else:
        row["trace_id"] = "trace-1"
    analytics = _CandidateAnalytics(observe_type=observe_type, rows=[row])

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(),
            _attribute_filter("final_status", "Rejected"),
            _annotation_filter(LABEL_ID, "approved"),
        ],
        observe_type=observe_type,
    )

    assert sample.query_complete is True
    assert sample.rows == (row,)
    classify_query, classify_params, _, _ = analytics.calls[1]
    candidate_param = (
        "candidate_trace_ids" if observe_type == "trace" else "candidate_span_ids"
    )
    assert "model_hub_score AS s FINAL" in classify_query
    assert f"%({candidate_param})s" in classify_query
    assert classify_params[candidate_param] == (
        "trace-1" if observe_type == "trace" else "span-1",
    )


@pytest.mark.unit
@pytest.mark.parametrize("filter_type", ["json", "map"])
def test_nested_json_and_map_filters_fail_closed_before_clickhouse_read(
    filter_type,
):
    analytics = _CandidateAnalytics(observe_type="span", rows=[])
    with pytest.raises(UnsupportedFilterShapeError):
        read_graph_candidates(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=[
                _date_filter(),
                _attribute_filter(
                    "attributes_extra",
                    {"nested": {"value": "x"}},
                    filter_type=filter_type,
                ),
            ],
            observe_type="span",
        )
    assert analytics.calls == []


@pytest.mark.unit
def test_customer_final_status_1090_rows_respects_safe_classifier_ceiling():
    filters = [_date_filter(), _attribute_filter("final_status", "Rejected")]
    rows = [
        {
            "trace_id": f"trace-{index:04d}",
            "root_span_id": f"span-{index:04d}",
            "start_time": START + timedelta(seconds=index % 240),
            "latency_ms": index,
        }
        for index in range(1090)
    ]
    analytics = _CandidateAnalytics(observe_type="trace", rows=rows)

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        observe_type="trace",
    )

    builder = bounded_graph_reads.TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_number=0,
        page_size=bounded_graph_reads.GRAPH_CANDIDATE_LIMIT,
        filters=filters,
        bounded_identity_only=True,
    )
    classify_batch_size = int(builder.recommended_filter_classify_batch_size() or 50)
    expected_limit = min(
        bounded_graph_reads.GRAPH_CANDIDATE_LIMIT,
        (classify_batch_size * bounded_graph_reads.GRAPH_TRACE_CLASSIFY_BATCH_BUDGET)
        - 1,
    )
    assert len(sample.rows) == expected_limit
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"
    assert sample.total_rows_lower_bound == expected_limit + 1
    assert all(
        len(call[1].get("candidate_trace_ids", ())) <= classify_batch_size
        for call in analytics.calls
    )


@pytest.mark.unit
def test_sparse_root_trace_filter_is_not_rejected_by_query_count_preflight():
    row = {
        "trace_id": "trace-1",
        "root_span_id": "span-1",
        "start_time": START + timedelta(minutes=4),
        "latency_ms": 25.0,
    }
    analytics = _CandidateAnalytics(observe_type="trace", rows=[row])

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_date_filter(), _system_text_filter("trace_id", "trace-1")],
        observe_type="trace",
    )

    assert sample.query_complete is True
    assert sample.rows == (row,)
    assert len(analytics.calls) >= 2
    seed_query, seed_params, *_ = analytics.calls[0]
    assert "ORDER BY start_time DESC, trace_id DESC" in seed_query
    assert seed_params["filter_seed_limit"] == 512


@pytest.mark.unit
def test_long_unindexed_root_trace_text_filter_uses_bounded_temporal_sample() -> None:
    window_start = END - timedelta(days=14)
    row = {
        "trace_id": "trace-1",
        "root_span_id": "span-1",
        "start_time": END - timedelta(minutes=1),
        "latency_ms": 25.0,
    }
    analytics = _CandidateAnalytics(observe_type="trace", rows=[row])

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, END),
            _system_text_filter("trace_id", "trace-1"),
        ],
        observe_type="trace",
    )

    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"
    assert sample.rows == (row,)
    seed_ranges = [
        (params["filter_slice_start"], params["filter_slice_end"])
        for _, params, *_ in analytics.calls
        if "filter_seed_limit" in params
    ]
    assert seed_ranges
    assert len(seed_ranges) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    assert all(
        end - start == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
        for start, end in seed_ranges
    )


@pytest.mark.unit
def test_4096_matches_complete_at_exact_graph_ceiling():
    rows = [
        {
            "id": f"span-{index:04d}",
            "trace_id": f"trace-{index:04d}",
            "start_time": START + timedelta(milliseconds=index),
        }
        for index in range(4096)
    ]
    analytics = _CandidateAnalytics(observe_type="span", rows=rows)

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_date_filter(), _attribute_filter("final_status", "Rejected")],
        observe_type="span",
    )

    assert len(sample.rows) == bounded_graph_reads.GRAPH_CANDIDATE_LIMIT
    assert sample.query_complete is True
    assert sample.query_status == "complete"
    assert sample.query_error_code is None
    assert sample.metadata()["query_sample_size"] == len(sample.rows)
    assert sample.metadata()["query_total_rows_lower_bound"] == len(sample.rows)


@pytest.mark.unit
def test_1600th_root_trace_returns_visible_sample_instead_of_blank_error():
    rows = [
        {
            "trace_id": f"trace-{index:04d}",
            "root_span_id": f"span-{index:04d}",
            "start_time": START + timedelta(milliseconds=index),
        }
        for index in range(1600)
    ]
    analytics = _CandidateAnalytics(observe_type="trace", rows=rows)

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_date_filter(), _system_text_filter("trace_id", "trace")],
        observe_type="trace",
    )

    assert len(sample.rows) == bounded_graph_reads.GRAPH_TRACE_ROOT_CANDIDATE_LIMIT
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"


@pytest.mark.unit
def test_640th_structured_trace_returns_visible_sample_instead_of_blank_error():
    filters = [
        _date_filter(),
        _attribute_filter(
            "customer.context",
            {"tier": "vip", "attempt": 2},
            filter_type="json",
            filter_op="contains",
        ),
    ]
    rows = [
        {
            "trace_id": f"trace-{index:04d}",
            "root_span_id": f"span-{index:04d}",
            "start_time": START + timedelta(milliseconds=index),
        }
        for index in range(640)
    ]
    analytics = _CandidateAnalytics(observe_type="trace", rows=rows)

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        observe_type="trace",
    )

    builder = bounded_graph_reads.TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_number=0,
        page_size=bounded_graph_reads.GRAPH_CANDIDATE_LIMIT,
        filters=filters,
        bounded_identity_only=True,
    )
    classify_batch_size = int(builder.recommended_filter_classify_batch_size() or 50)
    expected_limit = min(
        bounded_graph_reads.GRAPH_CANDIDATE_LIMIT,
        (classify_batch_size * bounded_graph_reads.GRAPH_TRACE_CLASSIFY_BATCH_BUDGET)
        - 1,
    )
    assert len(sample.rows) == expected_limit
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("window_days", "selector_error", "public_error"),
    [
        (7, "deadline_exceeded", "read_budget_exceeded"),
        (180, "read_budget_exceeded", "read_budget_exceeded"),
        (365, "sample_limit", "sample_limit"),
    ],
)
def test_long_window_incomplete_rows_are_sampled_only_for_cardinality_limits(
    monkeypatch, window_days, selector_error, public_error
):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=window_days)
    partial_row = {
        "trace_id": "trace-proven-match",
        "root_span_id": "span-proven-match",
        "start_time": window_start + timedelta(minutes=1),
    }
    calls = []

    def _incomplete_page(**kwargs):
        calls.append(kwargs)
        return BoundedFilterPage(
            rows=[partial_row],
            has_more=False,
            complete=False,
            status="degraded",
            error_code=selector_error,
            total_rows_lower_bound=1,
            elapsed_ms=1.0 if public_error == "sample_limit" else 3899.0,
            query_count=24,
            rows_returned=25,
            result_payload_bytes=512,
            attempts=(),
        )

    monkeypatch.setattr(
        bounded_graph_reads, "read_bounded_filter_page", _incomplete_page
    )
    if public_error == "sample_limit":
        sample = read_graph_candidates(
            analytics=object(),
            project_id=PROJECT_ID,
            filters=[
                _date_filter(window_start, window_end),
                _attribute_filter("customer.final_status", "Rejected"),
                _attribute_filter("score", 0.5, filter_type="number"),
            ],
            observe_type="trace",
        )
        assert sample.rows == (partial_row,)
        assert sample.query_complete is False
        assert sample.query_status == "sampled"
        assert sample.query_error_code == "sample_limit"
    else:
        sample = read_graph_candidates(
            analytics=object(),
            project_id=PROJECT_ID,
            filters=[
                _date_filter(window_start, window_end),
                _attribute_filter("customer.final_status", "Rejected"),
                _attribute_filter("score", 0.5, filter_type="number"),
            ],
            observe_type="trace",
        )
        assert sample.rows == (partial_row,)
        assert sample.query_complete is False
        assert sample.query_status == "degraded"
        assert sample.query_error_code == public_error
        with pytest.raises(BoundedGraphReadError) as caught:
            graph_dispatch._require_renderable_sample(sample)
        assert caught.value.error_code == public_error

    assert len(calls) == bounded_graph_reads.GRAPH_TRACE_STRATA
    assert all(call["anchor_probe_only"] is True for call in calls)
    assert all(
        call["anchor_probe_limit"]
        == bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM + 1
        for call in calls
    )
    assert all(call["include_incomplete_rows"] is True for call in calls)
    assert all(
        call["page_size"] == bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM
        for call in calls
    )
    assert all(call["max_seed_attempts"] == 1 for call in calls)
    assert all(call["max_query_count"] == 2 for call in calls)
    assert all(
        call["max_candidates"] == bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM + 1
        for call in calls
    )


@pytest.mark.unit
def test_incomplete_read_without_a_proven_match_raises_only_a_sanitized_code(
    monkeypatch,
):
    monkeypatch.setattr(
        bounded_graph_reads,
        "read_bounded_filter_page",
        lambda **_: BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=False,
            status="degraded",
            error_code="deadline_exceeded",
            total_rows_lower_bound=0,
            elapsed_ms=3900.0,
            query_count=2,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        ),
    )

    with pytest.raises(BoundedGraphReadError) as caught:
        read_graph_candidates(
            analytics=object(),
            project_id=PROJECT_ID,
            filters=[
                _date_filter(),
                _attribute_filter("score", 0.5, filter_type="number"),
            ],
            observe_type="span",
        )
    assert caught.value.error_code == "read_budget_exceeded"
    assert "ClickHouse" not in str(caught.value)


@pytest.mark.unit
def test_stratum_anchor_timeout_uses_sanitized_temporal_sample(monkeypatch):
    raw_error = "Code: 159 DB::Exception secret-host SELECT private_payload"
    read_count = 0
    read_calls = []
    warning_calls = []

    def _page_or_failure(**kwargs):
        nonlocal read_count
        read_count += 1
        read_calls.append(kwargs)
        if read_count == 1:
            raise ReadDeadlineExceeded(raw_error)
        return BoundedFilterPage(
            rows=[
                {
                    "trace_id": f"trace-{read_count}",
                    "root_span_id": f"span-{read_count}",
                    "start_time": START + timedelta(minutes=read_count),
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
        bounded_graph_reads, "read_bounded_filter_page", _page_or_failure
    )
    monkeypatch.setattr(
        bounded_graph_reads,
        "logger",
        SimpleNamespace(
            warning=lambda *args, **kwargs: warning_calls.append((args, kwargs))
        ),
    )

    sample = read_graph_candidates(
        analytics=object(),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START, START + timedelta(days=7)),
            _attribute_filter("score", 0.5, filter_type="number"),
        ],
        observe_type="trace",
    )

    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"
    assert (
        sample.sampling_strata_completed
        == sample.sampling_strata
        == bounded_graph_reads.GRAPH_TRACE_STRATA
    )
    assert len(sample.rows) == bounded_graph_reads.GRAPH_TRACE_STRATA
    assert raw_error not in repr(sample)
    assert read_count == bounded_graph_reads.GRAPH_TRACE_STRATA + 1
    assert read_calls[0]["anchor_probe_only"] is True
    assert all(call["anchor_probe_only"] is False for call in read_calls[1:])
    assert all(
        call["builder"].parse_time_range(call["filters"])[1]
        - call["builder"].parse_time_range(call["filters"])[0]
        == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
        for call in read_calls[1:]
    )
    assert warning_calls[0][1]["error_type"] == "ReadDeadlineExceeded"
    assert warning_calls[0][1]["exc_info"] is True
    assert raw_error not in str(warning_calls[0])


@pytest.mark.unit
def test_compiler_error_is_never_recast_as_a_cardinality_sample(monkeypatch):
    from clickhouse_driver.errors import ServerException

    raw_error = "Code: 47 DB::Exception Unknown identifier secret_column"
    compiler_error = ServerException(raw_error, code=47)
    monkeypatch.setattr(
        bounded_graph_reads,
        "read_bounded_filter_page",
        lambda **_: (_ for _ in ()).throw(compiler_error),
    )

    with pytest.raises(ServerException) as caught:
        read_graph_candidates(
            analytics=object(),
            project_id=PROJECT_ID,
            filters=[
                _date_filter(START, START + timedelta(days=180)),
                _attribute_filter("customer.final_status", "Rejected"),
            ],
            observe_type="trace",
        )

    assert caught.value is compiler_error
    sanitized = graph_dispatch.degraded_graph_response("latency", caught.value)
    assert sanitized["query_complete"] is False
    assert sanitized["query_error_code"] == "query_failed"
    assert raw_error not in str(sanitized)


@pytest.mark.unit
@pytest.mark.parametrize("window_days", [14, 180, 365])
def test_sparse_old_and_new_temporal_anchors_never_claim_exact(window_days):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=window_days)
    rows = [
        {
            "trace_id": "trace-old",
            "root_span_id": "span-old",
            "start_time": window_start + timedelta(minutes=1),
        },
        {
            "trace_id": "trace-new",
            "root_span_id": "span-new",
            "start_time": window_end - timedelta(minutes=1),
        },
    ]
    analytics = _CandidateAnalytics(observe_type="trace", rows=rows)

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("score", 0.5, filter_type="number"),
        ],
        observe_type="trace",
    )

    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"
    assert {row["trace_id"] for row in sample.rows} == {"trace-old", "trace-new"}
    anchor_calls = [
        call for call in analytics.calls if "filter_anchor_limit" in call[1]
    ]
    assert len(anchor_calls) == bounded_graph_reads.GRAPH_TRACE_STRATA
    anchor_ranges = [
        (params["filter_anchor_start"], params["filter_anchor_end"])
        for _, params, *_ in anchor_calls
    ]
    assert anchor_ranges[0][0] == window_start
    assert anchor_ranges[-1][1] == window_end
    assert all(
        left_end == right_start
        for (_, left_end), (right_start, _) in zip(
            anchor_ranges,
            anchor_ranges[1:],
            strict=False,
        )
    )
    assert all(
        params["filter_anchor_limit"]
        == bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM + 1
        for _, params, *_ in anchor_calls
    )
    assert not any("filter_slice_start" in call[1] for call in analytics.calls)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lower_op", "upper_op"),
    [
        ("greater_than", "less_than"),
        ("greater_than_or_equal", "less_than_or_equal"),
    ],
)
def test_long_window_scalar_datetime_bounds_are_preserved_by_stratum_anchors(
    lower_op,
    upper_op,
):
    window_start = datetime(2026, 1, 1)
    window_end = window_start + timedelta(days=7)
    row = {
        "trace_id": "trace-middle",
        "root_span_id": "span-middle",
        "start_time": window_start + timedelta(days=3),
    }
    analytics = _CandidateAnalytics(observe_type="trace", rows=[row])
    filters = [
        _date_bound_filter(lower_op, window_start),
        _date_bound_filter(upper_op, window_end),
        _attribute_filter("score", 0.5, filter_type="number"),
    ]

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        observe_type="trace",
    )

    assert sample.rows == (row,)
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"
    assert filters[0]["filter_config"]["filter_op"] == lower_op
    assert filters[1]["filter_config"]["filter_op"] == upper_op
    anchor_ranges = [
        (params["filter_anchor_start"], params["filter_anchor_end"])
        for _, params, *_ in analytics.calls
        if "filter_anchor_limit" in params
    ]
    expected_start = window_start + (
        timedelta(microseconds=1) if lower_op == "greater_than" else timedelta(0)
    )
    expected_end = window_end + (
        timedelta(microseconds=1) if upper_op == "less_than_or_equal" else timedelta(0)
    )
    assert len(anchor_ranges) == bounded_graph_reads.GRAPH_TRACE_STRATA
    assert anchor_ranges[0][0] == expected_start
    assert anchor_ranges[-1][1] == expected_end
    assert all(
        left_end == right_start
        for (_, left_end), (right_start, _) in zip(
            anchor_ranges,
            anchor_ranges[1:],
            strict=False,
        )
    )
    assert not any("filter_slice_start" in call[1] for call in analytics.calls)


@pytest.mark.unit
@pytest.mark.parametrize("window_days", [14, 180, 365])
def test_empty_temporal_child_anchors_do_not_prove_empty_trace_population(window_days):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=window_days)
    analytics = _CandidateAnalytics(observe_type="trace", rows=[])

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("score", 0.5, filter_type="number"),
        ],
        observe_type="trace",
    )

    assert sample.rows == ()
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("window_days", "failure_stratum"),
    [(14, 0), (180, 2), (365, 3)],
)
def test_failure_in_any_stratum_never_becomes_a_renderable_sample(
    monkeypatch,
    window_days,
    failure_stratum,
):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=window_days)
    calls = []

    def _page(**kwargs):
        index = len(calls)
        calls.append(kwargs)
        # The wide key witness, five-minute fallback, and one-minute same-
        # stratum retry must all fail before coverage is considered missing.
        if index in {
            failure_stratum,
            failure_stratum + 1,
            failure_stratum + 2,
        }:
            return BoundedFilterPage(
                rows=[],
                has_more=False,
                complete=False,
                status="degraded",
                error_code="read_budget_exceeded",
                total_rows_lower_bound=0,
                elapsed_ms=1,
                query_count=1,
                rows_returned=0,
                result_payload_bytes=0,
                attempts=(),
            )
        return BoundedFilterPage(
            rows=[
                {
                    "trace_id": f"trace-{index}",
                    "root_span_id": f"root-{index}",
                    "start_time": window_start + timedelta(hours=index + 1),
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

    monkeypatch.setattr(bounded_graph_reads, "read_bounded_filter_page", _page)

    sample = read_graph_candidates(
        analytics=object(),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="trace",
    )

    assert len(calls) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA + 2
    assert sample.query_complete is False
    assert sample.query_status == "degraded"
    assert sample.query_error_code == "read_budget_exceeded"
    assert sample.sampling_strata == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    assert sample.sampling_strata_completed == (
        bounded_graph_reads.GRAPH_ANY_SPAN_STRATA - 1
    )
    with pytest.raises(BoundedGraphReadError) as caught:
        graph_dispatch._require_renderable_sample(sample)
    assert caught.value.error_code == "read_budget_exceeded"


@pytest.mark.unit
def test_sparse_span_anchor_replays_trace_scoped_ids_and_latest_tombstones():
    window_end = START + timedelta(days=7)
    anchor_rows = [
        {
            "project_id": PROJECT_ID,
            "trace_id": "trace-a",
            "id": "shared",
            "start_time": START + timedelta(minutes=1),
        },
        {
            "project_id": PROJECT_ID,
            "trace_id": "trace-b",
            "id": "shared",
            "start_time": START + timedelta(minutes=2),
        },
        {
            "project_id": PROJECT_ID,
            "trace_id": "trace-deleted",
            "id": "gone",
            "start_time": START + timedelta(minutes=3),
        },
    ]
    latest_live = {
        **anchor_rows[1],
        "latency_ms": 11,
        "cost": 0.1,
        "total_tokens": 7,
        "prompt_tokens": 3,
        "completion_tokens": 4,
        "status": "OK",
    }

    class _SparseReplayAnalytics:
        def __init__(self):
            self.calls = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((query, params, timeout_ms, settings))
            if "filter_anchor_limit" in params:
                return _Result(
                    [
                        row
                        for row in anchor_rows
                        if params["filter_anchor_start"]
                        <= row["start_time"]
                        < params["filter_anchor_end"]
                    ]
                )
            return _Result([latest_live])

    analytics = _SparseReplayAnalytics()
    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START, window_end),
            _attribute_filter("score", 0.5, filter_type="number"),
        ],
        observe_type="span",
    )

    assert sample.query_complete is True
    assert sample.rows == (latest_live,)
    anchor_calls = [
        call for call in analytics.calls if "filter_anchor_limit" in call[1]
    ]
    classify_calls = [
        call for call in analytics.calls if "candidate_span_ids" in call[1]
    ]
    assert len(anchor_calls) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    assert len(classify_calls) == 1
    anchor_query, anchor_params, *_ = anchor_calls[0]
    classify_query, classify_params, *_ = classify_calls[0]
    assert "project_id = %(project_id)s" in anchor_query
    assert anchor_params["project_id"] == PROJECT_ID
    assert "argMax(is_deleted" in classify_query
    assert "latest_is_deleted = 0" in classify_query
    assert classify_params["candidate_span_entities"] == (
        ("trace-a", "shared"),
        ("trace-b", "shared"),
        ("trace-deleted", "gone"),
    )
    assert len(classify_params["candidate_span_identities"]) == 3


@pytest.mark.unit
def test_long_sparse_anchor_and_strata_timeout_becomes_degraded_empty(monkeypatch):
    calls = []

    def _timed_out_page(**kwargs):
        calls.append(kwargs)
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=False,
            status="degraded",
            error_code="deadline_exceeded",
            total_rows_lower_bound=0,
            elapsed_ms=500,
            query_count=1,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        )

    monkeypatch.setattr(
        bounded_graph_reads, "read_bounded_filter_page", _timed_out_page
    )

    sample = read_graph_candidates(
        analytics=object(),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START, START + timedelta(days=7)),
            _attribute_filter("score", 0.5, filter_type="number"),
        ],
        observe_type="span",
    )

    assert sample.query_status == "degraded"
    assert sample.query_error_code == "read_budget_exceeded"
    assert sample.rows == ()
    assert sample.sampling_strata_completed == 0
    with pytest.raises(BoundedGraphReadError) as caught:
        graph_dispatch._require_renderable_sample(sample)
    assert caught.value.error_code == "read_budget_exceeded"
    assert calls[0]["anchor_probe_only"] is True
    assert calls[0]["anchor_probe_limit"] == 50
    assert len(calls) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA


@pytest.mark.unit
def test_stale_saturated_span_anchor_uses_bounded_ordered_fallback(monkeypatch):
    calls = []

    def _page(**kwargs):
        calls.append(kwargs)
        stratum_index = (len(calls) - 1) // 2
        if kwargs.get("anchor_probe_only"):
            return BoundedFilterPage(
                rows=[],
                has_more=False,
                complete=False,
                status="degraded",
                error_code="sample_limit",
                total_rows_lower_bound=0,
                elapsed_ms=1,
                query_count=2,
                rows_returned=50,
                result_payload_bytes=500,
                attempts=(),
            )
        return BoundedFilterPage(
            rows=[
                {
                    "project_id": PROJECT_ID,
                    "trace_id": f"trace-live-{stratum_index}",
                    "id": f"span-live-{stratum_index}",
                    "start_time": START + timedelta(hours=stratum_index),
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

    monkeypatch.setattr(bounded_graph_reads, "read_bounded_filter_page", _page)

    sample = read_graph_candidates(
        analytics=object(),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START, START + timedelta(days=14)),
            _attribute_filter("score", 0.5, filter_type="number"),
        ],
        observe_type="span",
    )

    assert sample.query_complete is True
    assert len(sample.rows) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    assert len(calls) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA * 2
    assert all(call["anchor_probe_only"] is True for call in calls[::2])
    assert all(call["anchor_probe_only"] is False for call in calls[1::2])
    assert all(call["max_seed_attempts"] == 1 for call in calls[1::2])
    assert all(call["max_candidates"] == 50 for call in calls[1::2])


@pytest.mark.unit
def test_span_text_map_anchor_stays_optional_for_lists_but_graphs_sample():
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )

    filters = [
        _date_filter(START, START + timedelta(days=7)),
        _attribute_filter("final_status", "Rejected"),
    ]
    list_builder = SpanListQueryBuilderV2(project_id=PROJECT_ID, filters=filters)
    graph_builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_anchor_probe=True,
    )

    assert list_builder.supports_filter_anchor_probe() is True
    assert list_builder.recommended_filter_anchor_probe_limit() is None
    assert list_builder.recommended_filter_anchor_probe_timeout_ms() is None
    assert list_builder.recommended_filter_anchor_probe_strata() is None
    assert list_builder.recommended_filter_anchor_probe_max_bytes_to_read() is None
    assert list_builder.skip_full_window_filter_anchor_probe() is True
    assert graph_builder.supports_filter_anchor_probe() is True
    assert graph_builder.requires_unindexed_graph_sample_slice() is True
    assert graph_builder.recommended_filter_anchor_probe_limit() is None

    slice_start = END - timedelta(minutes=5)
    list_seed_query, list_seed_params = list_builder.build_filter_seed_page(
        slice_start=slice_start,
        slice_end=END,
        limit=50,
    )
    graph_seed_query, graph_seed_params = graph_builder.build_filter_seed_page(
        slice_start=slice_start,
        slice_end=END,
        limit=50,
    )
    assert "attrs_string" in list_seed_query
    assert list_seed_params["latest_filter_key_0"] == "final_status"
    assert "attrs_string" not in graph_seed_query
    assert "latest_filter_key_0" not in graph_seed_params

    graph_match_query, graph_match_params = (
        graph_builder.build_filter_match_query_from_seed_rows(
            [
                {
                    "project_id": PROJECT_ID,
                    "trace_id": "trace-1",
                    "id": "span-1",
                    "start_time": END - timedelta(minutes=1),
                }
            ]
        )
    )
    assert "attrs_string" in graph_match_query
    assert graph_match_params["latest_filter_key_0"] == "final_status"


@pytest.mark.unit
@pytest.mark.parametrize("structured_type", ["json", "call_type"])
def test_span_unindexed_structured_filter_uses_ordered_candidate_only_seed(
    structured_type,
):
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )

    window_start = START - timedelta(days=180)
    structured_filter = (
        _system_text_filter("call_type", "inbound")
        if structured_type == "call_type"
        else _attribute_filter(
            "customer.context",
            {"tier": "vip"},
            filter_type="json",
            filter_op="contains",
        )
    )
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_date_filter(window_start, END), structured_filter],
        bounded_anchor_probe=True,
    )

    assert builder.supports_filter_anchor_probe() is False
    assert builder.requires_unindexed_graph_sample_slice() is True
    seed_query, seed_params = builder.build_filter_seed_page(
        slice_start=END - bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE,
        slice_end=END,
        limit=50,
    )
    assert "ORDER BY start_time DESC, id DESC, trace_id DESC" in seed_query
    assert "JSONExtract" not in seed_query
    assert "attributes_extra" not in seed_query
    assert "inbound" not in seed_params.values()
    assert "vip" not in seed_params.values()

    match_query, match_params = builder.build_filter_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-1",
                "id": "span-1",
                "start_time": END - timedelta(minutes=1),
            }
        ]
    )
    assert "JSONExtract" in match_query
    assert (
        "inbound" in match_params.values()
        if structured_type == "call_type"
        else any(value == "vip" for value in match_params.values())
    )


@pytest.mark.unit
def test_span_mixed_structured_anchor_uses_only_the_indexed_typed_map_leaf():
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )

    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START - timedelta(days=180), END),
            _attribute_filter("final_status", "Rejected"),
            _system_text_filter("call_type", "inbound"),
        ],
        bounded_anchor_probe=True,
    )

    assert builder.supports_filter_anchor_probe() is True
    assert builder.requires_unindexed_graph_sample_slice() is True
    anchor_query, anchor_params = builder.build_filter_anchor_probe(limit=50)
    assert (
        "indexHint(has(mapKeys(attrs_string), %(latest_filter_key_0)s))" in anchor_query
    )
    assert "has(attrs_string.keys, %(latest_filter_key_0)s)" in anchor_query
    assert anchor_params["latest_filter_key_0"] == "final_status"
    assert (
        "arrayMap(x -> lowerUTF8(x), mapValues(attrs_string))" in anchor_query
    )
    assert "arrayMap(x -> lower(x), mapValues(attrs_string))" not in anchor_query
    assert (
        "lowerUTF8(toString(attrs_string[%(latest_filter_key_0)s])) = "
        "%(latest_filter_param_0)s" in anchor_query
    )
    assert anchor_params["latest_filter_param_0"] == "rejected"
    assert "JSONExtract" not in anchor_query
    assert "inbound" not in anchor_params.values()


@pytest.mark.unit
@pytest.mark.parametrize("builder_kind", ["trace", "span"])
@pytest.mark.parametrize("column", ["model", "provider", "status"])
@pytest.mark.parametrize("filter_op", ["equals", "contains", "not_contains"])
def test_wrapped_system_text_predicates_never_claim_an_indexed_graph_anchor(
    builder_kind,
    column,
    filter_op,
) -> None:
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    filters = [
        _date_filter(START - timedelta(days=180), END),
        _system_text_filter(column, "rare-system-value", filter_op=filter_op),
    ]
    builder = (
        TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=filters,
            bounded_identity_only=True,
        )
        if builder_kind == "trace"
        else SpanListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=filters,
            bounded_anchor_probe=True,
        )
    )

    assert builder.supports_filter_anchor_probe() is False
    assert builder.requires_unindexed_graph_sample_slice() is True


@pytest.mark.unit
@pytest.mark.parametrize("builder_kind", ["trace", "span"])
@pytest.mark.parametrize(
    ("filter_op", "value"),
    [
        ("equals", "Rejected"),
        ("in", ["Rejected", "Approved"]),
    ],
)
def test_text_map_key_subcolumn_is_only_an_optional_list_anchor(
    builder_kind,
    filter_op,
    value,
) -> None:
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    filters = [
        _date_filter(START - timedelta(days=180), END),
        _attribute_filter("final_status", value, filter_op=filter_op),
    ]
    builder = (
        TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=filters,
            bounded_identity_only=True,
        )
        if builder_kind == "trace"
        else SpanListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=filters,
            bounded_anchor_probe=True,
        )
    )

    assert builder.supports_filter_anchor_probe() is True
    assert builder.requires_unindexed_graph_sample_slice() is True
    anchor_query, anchor_params = builder.build_filter_anchor_probe(limit=50)
    assert (
        "indexHint(has(mapKeys(attrs_string), %(latest_filter_key_0)s))" in anchor_query
    )
    assert "has(attrs_string.keys, %(latest_filter_key_0)s)" in anchor_query
    assert anchor_params["latest_filter_key_0"] == "final_status"
    assert (
        "arrayMap(x -> lowerUTF8(x), mapValues(attrs_string))" in anchor_query
    )
    assert "arrayMap(x -> lower(x), mapValues(attrs_string))" not in anchor_query
    assert anchor_params["latest_filter_param_0"] == (
        "rejected" if filter_op == "equals" else ("rejected", "approved")
    )
    assert "lowerUTF8(toString(attrs_string[%(latest_filter_key_0)s]))" in anchor_query
    if filter_op == "in":
        assert anchor_params["latest_filter_index_0_0"] == "rejected"
        assert anchor_params["latest_filter_index_0_1"] == "approved"
    else:
        assert "latest_filter_index_0_0" not in anchor_params
        assert "latest_filter_index_0_1" not in anchor_params
    assert "Rejected" not in anchor_params.values()
    assert "Approved" not in anchor_params.values()

    graph_query, graph_params = builder.build_filter_graph_key_witness_probe(limit=50)
    assert "attrs_string" in graph_query
    assert "span_attr_str" not in graph_query
    assert graph_params["filter_graph_key_witness"] == 1
    assert graph_query.upper().count("SETTINGS") == 1
    for assignment in (
        "use_skip_indexes_if_final = 0",
        "optimize_use_projections = 1",
        "optimize_aggregation_in_order = 1",
    ):
        assert graph_query.count(assignment) == 1


@pytest.mark.unit
@pytest.mark.parametrize("builder_kind", ["trace", "span"])
@pytest.mark.parametrize(
    ("filter_type", "filter_op", "value"),
    [
        ("text", "contains", "ject"),
        ("text", "starts_with", "Rej"),
        ("text", "ends_with", "cted"),
        ("text", "is_not_null", None),
        ("boolean", "equals", True),
        ("boolean", "is_not_null", None),
        ("number", "greater_than", 0.5),
    ],
)
def test_key_only_typed_map_shapes_use_the_graph_sample_lane(
    builder_kind,
    filter_type,
    filter_op,
    value,
) -> None:
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    filters = [
        _date_filter(START - timedelta(days=180), END),
        _attribute_filter(
            "custom_value",
            value,
            filter_type=filter_type,
            filter_op=filter_op,
        ),
    ]
    builder = (
        TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=filters,
            bounded_identity_only=True,
        )
        if builder_kind == "trace"
        else SpanListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=filters,
            bounded_anchor_probe=True,
        )
    )

    assert builder.requires_unindexed_graph_sample_slice() is True


@pytest.mark.unit
@pytest.mark.parametrize("builder_kind", ["trace", "span"])
@pytest.mark.parametrize(
    ("filter_op", "value"),
    [("equals", 0.5), ("in", [0.5, 0.75])],
)
def test_numeric_value_index_remains_a_selective_graph_anchor(
    builder_kind,
    filter_op,
    value,
) -> None:
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    filters = [
        _date_filter(START - timedelta(days=180), END),
        _attribute_filter(
            "score",
            value,
            filter_type="number",
            filter_op=filter_op,
        ),
    ]
    builder = (
        TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=filters,
            bounded_identity_only=True,
        )
        if builder_kind == "trace"
        else SpanListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=filters,
            bounded_anchor_probe=True,
        )
    )

    assert builder.supports_filter_anchor_probe() is True
    assert builder.requires_unindexed_graph_sample_slice() is False
    anchor_query, _ = builder.build_filter_anchor_probe(limit=50)
    assert (
        "has(mapValues(attrs_number)" in anchor_query
        or "hasAny(mapValues(attrs_number)" in anchor_query
    )


@pytest.mark.unit
def test_bounded_executor_routes_numeric_trace_graph_to_wide_value_index_strata(
    monkeypatch,
) -> None:
    calls = []

    def _page(**kwargs):
        calls.append(kwargs)
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

    monkeypatch.setattr(bounded_graph_reads, "read_bounded_filter_page", _page)
    analytics = SimpleNamespace(supports_per_query_read_settings=True)
    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START, START + timedelta(days=14)),
            _attribute_filter("score", 0.5, filter_type="number"),
        ],
        observe_type="trace",
    )

    assert sample.query_status == "complete"
    assert sample.sampling_strata == 0
    assert len(calls) == bounded_graph_reads.GRAPH_TRACE_STRATA
    assert all(call["anchor_probe_only"] is True for call in calls)
    assert all(call["anchor_probe_limit"] == 4 for call in calls)
    assert all(call["graph_key_witness_probe"] is False for call in calls)
    assert all(
        call["builder"].parse_time_range(call["filters"])[1]
        - call["builder"].parse_time_range(call["filters"])[0]
        > bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
        for call in calls
    )


@pytest.mark.unit
def test_text_trace_graph_uses_wide_key_witness_strata(monkeypatch) -> None:
    calls = []

    def _page(**kwargs):
        calls.append(kwargs)
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

    monkeypatch.setattr(bounded_graph_reads, "read_bounded_filter_page", _page)
    sample = read_graph_candidates(
        analytics=SimpleNamespace(supports_per_query_read_settings=True),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START, START + timedelta(days=14)),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="trace",
    )

    assert sample.query_status == "complete"
    assert len(calls) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    assert all(call["graph_key_witness_probe"] is True for call in calls)
    assert all(call["anchor_probe_only"] is True for call in calls)
    assert [call["query_timeout_ms"] for call in calls] == [
        bounded_graph_reads.GRAPH_TRACE_KEY_WITNESS_TOTAL_TIMEOUT_MS - index
        for index in range(bounded_graph_reads.GRAPH_ANY_SPAN_STRATA)
    ]
    assert all(
        call["builder"].parse_time_range(call["filters"])[1]
        - call["builder"].parse_time_range(call["filters"])[0]
        > bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
        for call in calls
    )


@pytest.mark.unit
def test_text_trace_key_witness_bounds_union_and_preserves_every_stratum() -> None:
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=14)
    window_width = window_end - window_start
    rows = []
    for stratum in range(bounded_graph_reads.GRAPH_ANY_SPAN_STRATA):
        stratum_start = window_start + (
            window_width * stratum / bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
        )
        stratum_width = window_width / bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
        for index in range(60):
            rows.append(
                {
                    "trace_id": f"trace-{stratum}-{index:03d}",
                    "root_span_id": f"root-{stratum}-{index:03d}",
                    "start_time": stratum_start + (stratum_width * index / 60),
                }
            )

    sample = read_graph_candidates(
        analytics=_CandidateAnalytics(observe_type="trace", rows=rows),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="trace",
    )

    assert (
        len(sample.rows)
        == bounded_graph_reads.GRAPH_TRACE_UNINDEXED_UNION_CANDIDATE_LIMIT
    )
    assert sample.sampling_strata_completed == sample.sampling_strata == 8
    assert graph_dispatch._bounded_trace_decoration_sample(sample) is sample
    represented_strata = {
        min(
            7,
            int((row["start_time"] - window_start) / (window_width / 8)),
        )
        for row in sample.rows
    }
    assert represented_strata == set(range(8))


@pytest.mark.unit
def test_wide_graph_key_budget_failure_falls_back_to_temporal_sample(
    monkeypatch,
) -> None:
    calls = []

    def _page(**kwargs):
        calls.append(kwargs)
        if kwargs.get("graph_key_witness_probe"):
            raise ReadDeadlineExceeded("key witness timeout")
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

    monkeypatch.setattr(bounded_graph_reads, "read_bounded_filter_page", _page)
    sample = read_graph_candidates(
        analytics=SimpleNamespace(supports_per_query_read_settings=True),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START, START + timedelta(days=14)),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="trace",
    )

    assert sample.query_status == "sampled"
    assert calls[0]["graph_key_witness_probe"] is True
    assert calls[0]["anchor_probe_only"] is True
    assert (
        calls[0]["query_timeout_ms"]
        == bounded_graph_reads.GRAPH_TRACE_KEY_WITNESS_QUERY_TIMEOUT_MS
    )
    assert calls[1]["graph_key_witness_probe"] is False
    assert calls[1]["anchor_probe_only"] is False
    assert (
        calls[1]["builder"].parse_time_range(calls[1]["filters"])[1]
        - calls[1]["builder"].parse_time_range(calls[1]["filters"])[0]
        == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
    )


@pytest.mark.unit
def test_graph_key_witness_uses_one_shared_wall_then_samples_remaining_strata(
    monkeypatch,
) -> None:
    calls = []

    def _page(**kwargs):
        calls.append(kwargs)
        is_witness = kwargs.get("graph_key_witness_probe") is True
        witness_number = sum(
            call.get("graph_key_witness_probe") is True for call in calls
        )
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=0,
            # The first optional probe spends 60 ms; the second is clamped to
            # the remaining 40 ms. The other six strata must not launch one.
            elapsed_ms=(
                60
                if is_witness and witness_number == 1
                else kwargs["query_timeout_ms"]
                if is_witness
                else 1
            ),
            query_count=1,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        )

    monkeypatch.setattr(bounded_graph_reads, "read_bounded_filter_page", _page)

    sample = read_graph_candidates(
        analytics=SimpleNamespace(supports_per_query_read_settings=True),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START, START + timedelta(days=365)),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="trace",
    )

    assert len(calls) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    assert [call["graph_key_witness_probe"] for call in calls[:2]] == [True, True]
    assert all(call["graph_key_witness_probe"] is False for call in calls[2:])
    assert (
        calls[0]["query_timeout_ms"]
        == bounded_graph_reads.GRAPH_TRACE_KEY_WITNESS_TOTAL_TIMEOUT_MS
    )
    assert (
        calls[1]["query_timeout_ms"]
        == bounded_graph_reads.GRAPH_TRACE_KEY_WITNESS_TOTAL_TIMEOUT_MS - 60
    )
    assert all(call["query_timeout_ms"] is None for call in calls[2:])
    assert all(
        call["builder"].parse_time_range(call["filters"])[1]
        - call["builder"].parse_time_range(call["filters"])[0]
        == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
        for call in calls[2:]
    )
    assert sample.query_status == "sampled"
    assert sample.sampling_strata_completed == sample.sampling_strata == 8


@pytest.mark.unit
def test_unindexed_span_trace_name_and_indexed_numeric_latency_are_distinguished():
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )

    trace_name_builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START - timedelta(days=180), END),
            _system_text_filter("trace_name", "rare-trace-name"),
        ],
        bounded_anchor_probe=True,
    )
    latency_builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START - timedelta(days=180), END),
            {
                "column_id": "latency_ms",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 1000,
                },
            },
        ],
        bounded_anchor_probe=True,
    )

    assert trace_name_builder.supports_filter_anchor_probe() is False
    assert trace_name_builder.requires_unindexed_graph_sample_slice() is True
    assert latency_builder.supports_filter_anchor_probe() is True
    assert latency_builder.requires_unindexed_graph_sample_slice() is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filter_op", "value", "operator"),
    [
        ("equals", "003B76F1-2B4A-4AF5-B0DC-224D687374D4", "="),
        (
            "in",
            [
                "003b76f1-2b4a-4af5-b0dc-224d687374d4",
                "103b76f1-2b4a-4af5-b0dc-224d687374d4",
            ],
            "IN",
        ),
    ],
)
def test_session_uuid_equality_uses_the_raw_bloom_indexed_seed(
    filter_op,
    value,
    operator,
) -> None:
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    filters = [
        _date_filter(START - timedelta(days=180), END),
        _system_text_filter("session", value, filter_op=filter_op),
    ]
    trace_builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_identity_only=True,
    )
    span_builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_anchor_probe=True,
    )

    # Session is a root predicate for trace browse, so it uses the ordered
    # root seed rather than the any-span anchor probe. Both modes can still
    # use the deployed raw trace_session_id bloom index.
    assert trace_builder.supports_filter_anchor_probe() is False
    assert trace_builder.requires_unindexed_graph_sample_slice() is False
    trace_seed, trace_params = trace_builder.build_filter_seed_page(
        slice_start=START - timedelta(days=180),
        slice_end=END,
        limit=50,
    )
    assert f"trace_session_id {operator} %(latest_filter_param_0)s" in trace_seed
    assert "lowerUTF8(toString(trace_session_id))" not in trace_seed
    assert all(
        str(item) == str(item).lower()
        for item in (
            trace_params["latest_filter_param_0"]
            if isinstance(trace_params["latest_filter_param_0"], tuple)
            else (trace_params["latest_filter_param_0"],)
        )
    )

    assert span_builder.supports_filter_anchor_probe() is True
    assert span_builder.requires_unindexed_graph_sample_slice() is False
    span_anchor, _ = span_builder.build_filter_anchor_probe(limit=50)
    assert f"trace_session_id {operator} %(latest_filter_param_0)s" in span_anchor
    assert "lowerUTF8(toString(trace_session_id))" not in span_anchor


@pytest.mark.unit
def test_identity_only_session_classifier_projects_the_proven_session_id() -> None:
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    session_filter = {
        "column_id": "trace_session_id",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "is_not_null",
            "filter_value": None,
        },
    }
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_date_filter(), session_filter],
        bounded_identity_only=True,
    )

    query, _ = builder.build_filter_match_query(["trace-1"])

    # The latest-state session value is already required to classify the
    # filter. Reusing that alias keeps candidate discovery identity-only;
    # metric graphs may then hydrate only those proven canonical roots.
    assert "latest_column_value_0 AS trace_session_id" in query
    assert query.count("argMax(tuple(trace_session_id), _version).1") == 1
    assert "latest_trace_name" not in query


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filter_op", "value"),
    [
        ("equals", "external-session-not-a-uuid"),
        ("contains", "003b76f1"),
        ("not_in", ["003b76f1-2b4a-4af5-b0dc-224d687374d4"]),
    ],
)
def test_non_uuid_or_non_positive_session_text_never_claims_the_uuid_index(
    filter_op,
    value,
) -> None:
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    filters = [
        _date_filter(START - timedelta(days=180), END),
        _system_text_filter("session", value, filter_op=filter_op),
    ]
    trace_builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_identity_only=True,
    )
    span_builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        bounded_anchor_probe=True,
    )

    assert trace_builder.supports_filter_anchor_probe() is False
    assert trace_builder.requires_unindexed_graph_sample_slice() is True
    assert span_builder.supports_filter_anchor_probe() is False
    assert span_builder.requires_unindexed_graph_sample_slice() is True


@pytest.mark.unit
@pytest.mark.parametrize("shape", ["span_name", "negative_span_name", "null_attr"])
def test_trace_unindexed_any_span_shapes_require_temporal_sample_lane(shape) -> None:
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    if shape == "null_attr":
        predicate = _attribute_filter(
            "final_status",
            None,
            filter_op="is_null",
        )
    else:
        predicate = {
            "column_id": "span_name",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "not_equals" if shape.startswith("negative") else "equals",
                "filter_value": "rare-span-name",
            },
        }
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_date_filter(START - timedelta(days=180), END), predicate],
        bounded_identity_only=True,
    )

    assert builder.supports_filter_anchor_probe() is False
    assert builder.requires_unindexed_graph_sample_slice() is True


@pytest.mark.unit
@pytest.mark.parametrize("has_latest_match", [True, False])
def test_trace_span_name_rare_and_absent_use_repeatable_micro_slices(
    has_latest_match,
) -> None:
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=180)
    window_width = window_end - window_start
    rows = []
    for stratum in range(bounded_graph_reads.GRAPH_ANY_SPAN_STRATA):
        stratum_end = (
            window_end
            if stratum == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA - 1
            else window_start
            + window_width * (stratum + 1) / bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
        )
        rows.append(
            {
                "trace_id": f"trace-{stratum}",
                "root_span_id": f"root-{stratum}",
                "start_time": stratum_end - timedelta(minutes=1),
                "matches_latest": has_latest_match,
            }
        )
    filters = [
        _date_filter(window_start, window_end),
        {
            "column_id": "span_name",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "rare-span-name",
            },
        },
    ]

    def read_once():
        analytics = _LatestRareCandidateAnalytics(observe_type="trace", rows=rows)
        return (
            read_graph_candidates(
                analytics=analytics,
                project_id=PROJECT_ID,
                filters=filters,
                observe_type="trace",
            ),
            analytics,
        )

    first, analytics = read_once()
    second, _ = read_once()

    assert tuple(row["trace_id"] for row in first.rows) == tuple(
        row["trace_id"] for row in second.rows
    )
    assert len(first.rows) == (
        bounded_graph_reads.GRAPH_ANY_SPAN_STRATA if has_latest_match else 0
    )
    assert first.query_complete is False
    assert first.query_status == "sampled"
    assert first.query_error_code == "sample_limit"
    assert first.sampling_strata_completed == first.sampling_strata == 8
    assert not any("filter_anchor_limit" in params for _, params, *_ in analytics.calls)
    seed_ranges = [
        (params["filter_slice_start"], params["filter_slice_end"])
        for _, params, *_ in analytics.calls
        if "filter_seed_limit" in params
    ]
    assert len(seed_ranges) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    assert all(
        end - start == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
        for start, end in seed_ranges
    )


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
@pytest.mark.parametrize("column", ["model", "provider", "status"])
@pytest.mark.parametrize("window_days", [14, 180, 365])
@pytest.mark.parametrize("has_latest_match", [True, False])
def test_wrapped_system_text_rare_and_absent_values_use_repeatable_micro_slices(
    observe_type,
    column,
    window_days,
    has_latest_match,
) -> None:
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=window_days)
    window_width = window_end - window_start
    rows = []
    for stratum in range(bounded_graph_reads.GRAPH_ANY_SPAN_STRATA):
        stratum_end = (
            window_end
            if stratum == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA - 1
            else window_start
            + window_width * (stratum + 1) / bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
        )
        row = {
            "trace_id": f"trace-{stratum}",
            "root_span_id": f"root-{stratum}",
            "start_time": stratum_end - timedelta(minutes=1),
            "matches_latest": has_latest_match,
        }
        if observe_type == "span":
            row["id"] = f"span-{stratum}"
        rows.append(row)
    filters = [
        _date_filter(window_start, window_end),
        _system_text_filter(column, "rare-system-value"),
    ]

    def read_once():
        analytics = _LatestRareCandidateAnalytics(
            observe_type=observe_type,
            rows=rows,
        )
        sample = read_graph_candidates(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=filters,
            observe_type=observe_type,
        )
        return sample, analytics

    first, analytics = read_once()
    second, _ = read_once()
    identity_column = "trace_id" if observe_type == "trace" else "id"

    assert tuple(row[identity_column] for row in first.rows) == tuple(
        row[identity_column] for row in second.rows
    )
    assert len(first.rows) == (
        bounded_graph_reads.GRAPH_ANY_SPAN_STRATA if has_latest_match else 0
    )
    assert first.query_complete is False
    assert first.query_status == "sampled"
    assert first.query_error_code == "sample_limit"
    assert first.sampling_strata_completed == first.sampling_strata == 8
    assert not any("filter_anchor_limit" in params for _, params, *_ in analytics.calls)
    seed_ranges = [
        (params["filter_slice_start"], params["filter_slice_end"])
        for _, params, *_ in analytics.calls
        if "filter_seed_limit" in params
    ]
    assert len(seed_ranges) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    assert all(
        end - start == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
        for start, end in seed_ranges
    )


@pytest.mark.unit
@pytest.mark.parametrize("window_days", [180, 365])
@pytest.mark.parametrize("structured_type", ["json", "call_type"])
@pytest.mark.parametrize("has_latest_match", [True, False])
def test_unindexed_span_graph_rare_and_absent_values_use_repeatable_micro_slices(
    window_days,
    structured_type,
    has_latest_match,
):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=window_days)
    window_width = window_end - window_start
    rows = []
    for stratum in range(bounded_graph_reads.GRAPH_ANY_SPAN_STRATA):
        stratum_end = (
            window_end
            if stratum == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA - 1
            else window_start
            + (window_width * (stratum + 1) / bounded_graph_reads.GRAPH_ANY_SPAN_STRATA)
        )
        slice_start = stratum_end - bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
        for index in range(60):
            rows.append(
                {
                    "id": f"span-{stratum}-{index:03d}",
                    "trace_id": f"trace-{stratum}-{index:03d}",
                    "start_time": slice_start
                    + (bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE * index / 60),
                    "matches_latest": has_latest_match and index == 59,
                }
            )

    structured_filter = (
        _system_text_filter("call_type", "inbound")
        if structured_type == "call_type"
        else _attribute_filter(
            "customer.context",
            {"tier": "vip"},
            filter_type="json",
            filter_op="contains",
        )
    )

    def read_once():
        analytics = _LatestRareCandidateAnalytics(observe_type="span", rows=rows)
        sample = read_graph_candidates(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=[_date_filter(window_start, window_end), structured_filter],
            observe_type="span",
        )
        return sample, analytics

    first, analytics = read_once()
    second, _ = read_once()

    assert tuple(row["id"] for row in first.rows) == tuple(
        row["id"] for row in second.rows
    )
    assert len(first.rows) == (
        bounded_graph_reads.GRAPH_ANY_SPAN_STRATA if has_latest_match else 0
    )
    assert first.query_complete is False
    assert first.query_status == "sampled"
    assert first.query_error_code == "sample_limit"
    assert first.sampling_strata_completed == first.sampling_strata == 8
    graph_dispatch._require_renderable_sample(first)

    assert not any("filter_anchor_limit" in params for _, params, *_ in analytics.calls)
    seed_calls = [
        (query, params)
        for query, params, *_ in analytics.calls
        if "filter_seed_limit" in params
    ]
    assert len(seed_calls) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    assert all("ORDER BY start_time DESC" in query for query, _ in seed_calls)
    assert all("JSONExtract" not in query for query, _ in seed_calls)
    assert all(
        params["filter_slice_end"] - params["filter_slice_start"]
        == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE
        for _, params in seed_calls
    )
    classifier_queries = [
        query for query, params, *_ in analytics.calls if "candidate_span_ids" in params
    ]
    assert classifier_queries
    assert all("JSONExtract" in query for query in classifier_queries)


@pytest.mark.unit
@pytest.mark.parametrize("window_days", [14, 180, 365])
@pytest.mark.parametrize("structured_type", ["map", "json", "call_type"])
def test_common_raw_latest_rare_filter_returns_deterministic_stratified_sample(
    window_days,
    structured_type,
):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=window_days)
    window_width = window_end - window_start
    rows = []
    rows_per_stratum = 100
    for stratum in range(bounded_graph_reads.GRAPH_TRACE_STRATA):
        stratum_start = window_start + (
            window_width * stratum / bounded_graph_reads.GRAPH_TRACE_STRATA
        )
        stratum_width = window_width / bounded_graph_reads.GRAPH_TRACE_STRATA
        for index in range(rows_per_stratum):
            rows.append(
                {
                    "trace_id": f"trace-{stratum}-{index:03d}",
                    "root_span_id": f"root-{stratum}-{index:03d}",
                    "start_time": stratum_start
                    + (stratum_width * index / rows_per_stratum),
                    # One candidate selected by the finite raw sentinel is
                    # still live; every other raw final_status hit is stale.
                    "matches_latest": index == 0,
                }
            )

    structured_filter = (
        {
            "column_id": "call_type",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "inbound",
            },
        }
        if structured_type == "call_type"
        else _attribute_filter(
            "customer.context",
            {"tier": "vip"},
            filter_type=structured_type,
            filter_op="contains" if structured_type == "json" else "equals",
        )
    )
    filters = [
        _date_filter(window_start, window_end),
        _attribute_filter("final_status", "Rejected"),
        _attribute_filter("score", 0.5, filter_type="number"),
        structured_filter,
    ]

    def _read_once():
        analytics = _LatestRareCandidateAnalytics(observe_type="trace", rows=rows)
        sample = read_graph_candidates(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=filters,
            observe_type="trace",
        )
        return sample, analytics

    first, analytics = _read_once()
    second, _ = _read_once()

    assert tuple(row["trace_id"] for row in first.rows) == tuple(
        row["trace_id"] for row in second.rows
    )
    assert len(first.rows) == bounded_graph_reads.GRAPH_TRACE_STRATA
    assert first.query_complete is False
    assert first.query_status == "sampled"
    assert first.query_error_code == "sample_limit"
    assert first.metadata()["query_sampling_strategy"] == (
        "time_stratified_latest_state"
    )
    assert first.metadata()["query_sampling_strata_completed"] == (
        bounded_graph_reads.GRAPH_TRACE_STRATA
    )
    classifier_sizes = [
        len(params["candidate_trace_ids"])
        for _, params, *_ in analytics.calls
        if "candidate_trace_ids" in params
    ]
    # Four four-ID anchor sentinels are replayed through resource-bounded,
    # full-window chunks. Only proven rows are visible; stale raw anchors in
    # this fixture must never leak into the sample.
    assert sum(classifier_sizes) == (
        bounded_graph_reads.GRAPH_TRACE_STRATA
        * (bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM + 1)
    )
    assert all(
        size <= bounded_graph_reads.GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE
        for size in classifier_sizes
    )
    assert tuple(row["trace_id"] for row in first.rows) == tuple(
        row["trace_id"]
        for row in sorted(
            first.rows,
            key=lambda row: (row["start_time"], row["trace_id"]),
            reverse=True,
        )
    )
    assert len(first.rows) == bounded_graph_reads.GRAPH_TRACE_STRATA


@pytest.mark.unit
def test_trace_classifier_failure_is_atomic_and_sanitized() -> None:
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=14)
    window_width = window_end - window_start
    rows = []
    for stratum in range(bounded_graph_reads.GRAPH_TRACE_STRATA):
        stratum_start = window_start + (
            window_width * stratum / bounded_graph_reads.GRAPH_TRACE_STRATA
        )
        for index in range(bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM + 1):
            rows.append(
                {
                    "trace_id": f"trace-{stratum}-{index}",
                    "root_span_id": f"root-{stratum}-{index}",
                    "start_time": stratum_start + timedelta(minutes=index + 1),
                    "matches_latest": True,
                }
            )

    class _FailingClassifier(_LatestRareCandidateAnalytics):
        classifier_calls = 0

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if "candidate_trace_ids" in params:
                self.classifier_calls += 1
                self.calls.append((query, params, timeout_ms, settings))
                raise ReadDeadlineExceeded("private classifier timeout")
            return super().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )

    analytics = _FailingClassifier(observe_type="trace", rows=rows)
    with pytest.raises(BoundedGraphReadError) as caught:
        read_graph_candidates(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=[
                _date_filter(window_start, window_end),
                _attribute_filter("score", 0.5, filter_type="number"),
            ],
            observe_type="trace",
        )

    classifier_calls = [
        call for call in analytics.calls if "candidate_trace_ids" in call[1]
    ]
    assert len(classifier_calls) == 1
    assert caught.value.error_code == "read_budget_exceeded"
    assert "private classifier timeout" not in str(caught.value)


class _TraceUnionClassifierBuilder:
    def build_filter_match_query_from_seed_rows(
        self,
        candidate_rows,
        *,
        include_filter_witnesses,
    ):
        assert include_filter_witnesses is False
        return "SELECT trace_id", {
            "candidate_trace_ids": tuple(row["trace_id"] for row in candidate_rows)
        }


class _TraceUnionClassifierAnalytics:
    def __init__(self, *, fail_after=None):
        self.calls = []
        self.fail_after = fail_after

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params, timeout_ms, settings))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise ReadDeadlineExceeded("private classifier timeout")
        return _Result(
            [
                {
                    "trace_id": trace_id,
                    "root_span_id": f"root-{trace_id}",
                    "start_time": START + timedelta(seconds=index),
                }
                for index, trace_id in enumerate(params["candidate_trace_ids"])
            ]
        )


def _trace_union_stratum(count):
    return bounded_graph_reads._DeferredTraceStratum(
        builder=_TraceUnionClassifierBuilder(),
        candidate_rows=tuple(
            {
                "trace_id": f"trace-{index}",
                "root_span_id": f"seed-root-{index}",
                "start_time": START + timedelta(seconds=index),
            }
            for index in range(count)
        ),
    )


@pytest.mark.unit
def test_trace_union_classifier_receives_finite_1500ms_cap(monkeypatch):
    analytics = _TraceUnionClassifierAnalytics()
    clock = iter([0.0, 0.0, 0.0])
    monkeypatch.setattr(bounded_graph_reads, "monotonic", lambda: next(clock))

    result = bounded_graph_reads._classify_deferred_trace_strata(
        analytics=analytics,
        strata=[_trace_union_stratum(2)],
        distributed_started=0.0,
        deadline_ms=9_500,
        acquisition_query_count=1,
        candidate_rows_per_stratum=5,
        visible_rows_per_stratum=5,
    )

    assert analytics.calls[0][2] == 1_500
    assert set(result[0]) == {"trace-0", "trace-1"}


@pytest.mark.unit
def test_trace_union_classifier_cap_is_clamped_by_remaining_shared_wall(monkeypatch):
    analytics = _TraceUnionClassifierAnalytics()
    clock = iter([8.25, 8.25, 8.25])
    monkeypatch.setattr(bounded_graph_reads, "monotonic", lambda: next(clock))

    bounded_graph_reads._classify_deferred_trace_strata(
        analytics=analytics,
        strata=[_trace_union_stratum(1)],
        distributed_started=0.0,
        deadline_ms=9_500,
        acquisition_query_count=1,
        candidate_rows_per_stratum=5,
        visible_rows_per_stratum=5,
    )

    assert analytics.calls[0][2] == 1_250


@pytest.mark.unit
def test_trace_union_classifier_over_cap_failure_is_typed_and_atomic(monkeypatch):
    # Six identities require two finite batches. The first succeeds privately;
    # the second reaches its enforced cap. No classified prefix may cross the
    # helper's return boundary unless every batch succeeds.
    analytics = _TraceUnionClassifierAnalytics(fail_after=1)
    clock = iter([0.0, 0.0, 0.0, 0.0, 0.0])
    monkeypatch.setattr(bounded_graph_reads, "monotonic", lambda: next(clock))

    with pytest.raises(BoundedGraphReadError) as caught:
        bounded_graph_reads._classify_deferred_trace_strata(
            analytics=analytics,
            strata=[_trace_union_stratum(6)],
            distributed_started=0.0,
            deadline_ms=9_500,
            acquisition_query_count=1,
            candidate_rows_per_stratum=5,
            visible_rows_per_stratum=5,
        )

    assert [call[2] for call in analytics.calls] == [1_500, 1_500]
    assert caught.value.error_code == "read_budget_exceeded"
    assert caught.value.retryable is True
    assert "private classifier timeout" not in str(caught.value)


@pytest.mark.unit
def test_distributed_sample_uses_one_shared_deadline_instead_of_equal_slices(
    monkeypatch,
):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=14)
    calls = []

    def _sample_page(**kwargs):
        calls.append(kwargs)
        return BoundedFilterPage(
            rows=[
                {
                    "trace_id": f"trace-{len(calls)}",
                    "root_span_id": f"root-{len(calls)}",
                    "start_time": window_start + timedelta(hours=len(calls)),
                }
            ],
            has_more=False,
            complete=False,
            status="degraded",
            error_code="sample_limit",
            total_rows_lower_bound=1,
            elapsed_ms=1,
            query_count=1,
            rows_returned=1,
            result_payload_bytes=10,
            attempts=(),
        )

    monkeypatch.setattr(bounded_graph_reads, "read_bounded_filter_page", _sample_page)

    sample = read_graph_candidates(
        analytics=object(),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("score", 0.5, filter_type="number"),
        ],
        observe_type="trace",
    )

    distributed_calls = calls
    old_equal_slice_ms = (
        bounded_graph_reads.GRAPH_CANDIDATE_DEADLINE_MS
        // bounded_graph_reads.GRAPH_TRACE_STRATA
    )
    assert len(distributed_calls) == bounded_graph_reads.GRAPH_TRACE_STRATA
    assert all(call["deadline_ms"] > old_equal_slice_ms for call in distributed_calls)
    assert sample.query_status == "sampled"
    assert sample.sampling_strata_completed == (bounded_graph_reads.GRAPH_TRACE_STRATA)


@pytest.mark.unit
@pytest.mark.parametrize("completed_strata", [0, 1])
def test_partial_or_empty_stratified_deadline_is_not_renderable(
    monkeypatch,
    completed_strata,
):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=14)
    page_calls = 0

    def _page(**kwargs):
        nonlocal page_calls
        page_calls += 1
        return BoundedFilterPage(
            rows=[
                {
                    "trace_id": "trace-proven",
                    "root_span_id": "root-proven",
                    "start_time": window_start + timedelta(minutes=1),
                }
            ],
            has_more=False,
            complete=False,
            status="degraded",
            error_code="sample_limit",
            total_rows_lower_bound=1,
            elapsed_ms=1,
            query_count=2,
            rows_returned=2,
            result_payload_bytes=20,
            attempts=(),
        )

    # distributed_started, first-stratum check, and optional second-stratum
    # check. Crossing the deadline before all planned strata must
    # never turn zero or partial temporal coverage into a sampled graph.
    expired_at = (bounded_graph_reads.GRAPH_CANDIDATE_DEADLINE_MS / 1_000) + 1
    clock = iter([0.0, expired_at] if completed_strata == 0 else [0.0, 0.0, expired_at])
    monkeypatch.setattr(bounded_graph_reads, "monotonic", lambda: next(clock))
    monkeypatch.setattr(bounded_graph_reads, "read_bounded_filter_page", _page)

    sample = read_graph_candidates(
        analytics=object(),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="trace",
    )

    assert sample.query_complete is False
    assert sample.query_status == "degraded"
    assert sample.query_error_code == "read_budget_exceeded"
    assert sample.sampling_strata_completed == completed_strata
    assert len(sample.rows) == completed_strata
    with pytest.raises(BoundedGraphReadError) as caught:
        graph_dispatch._require_renderable_sample(sample)
    assert caught.value.error_code == "read_budget_exceeded"


@pytest.mark.unit
def test_code_307_stratum_anchor_uses_temporal_sample_without_leaking_details(
    monkeypatch,
):
    from clickhouse_driver.errors import ServerException

    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=14)
    calls = []

    def _page(**kwargs):
        calls.append(kwargs)
        if kwargs.get("anchor_probe_only"):
            raise ServerException("private Code 307 query details", code=307)
        return BoundedFilterPage(
            rows=[
                {
                    "trace_id": f"trace-{len(calls)}",
                    "root_span_id": f"root-{len(calls)}",
                    "start_time": window_start + timedelta(hours=len(calls)),
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

    monkeypatch.setattr(bounded_graph_reads, "read_bounded_filter_page", _page)

    sample = read_graph_candidates(
        analytics=object(),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("score", 0.5, filter_type="number"),
        ],
        observe_type="trace",
    )

    assert len(calls) == bounded_graph_reads.GRAPH_TRACE_STRATA + 1
    assert calls[0]["anchor_probe_only"] is True
    assert all(call["anchor_probe_only"] is False for call in calls[1:])
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"
    assert (
        sample.sampling_strata_completed
        == sample.sampling_strata
        == bounded_graph_reads.GRAPH_TRACE_STRATA
    )
    assert len(sample.rows) == bounded_graph_reads.GRAPH_TRACE_STRATA
    assert "private" not in repr(sample)


@pytest.mark.unit
def test_trace_graph_worst_case_acquisition_and_retries_fit_query_ceiling(
    monkeypatch,
):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=14)
    calls = []
    rows = []

    def _page(**kwargs):
        calls.append(kwargs)
        slice_start, slice_end = kwargs["builder"].parse_time_range(kwargs["filters"])
        if kwargs.get("graph_key_witness_probe"):
            return BoundedFilterPage(
                rows=[],
                has_more=False,
                complete=False,
                status="degraded",
                error_code="read_budget_exceeded",
                total_rows_lower_bound=0,
                elapsed_ms=1,
                query_count=1,
                rows_returned=0,
                result_payload_bytes=0,
                attempts=(),
            )
        if slice_end - slice_start == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE:
            return BoundedFilterPage(
                rows=[],
                has_more=False,
                complete=False,
                status="degraded",
                error_code="read_budget_exceeded",
                total_rows_lower_bound=0,
                elapsed_ms=1,
                query_count=1,
                rows_returned=0,
                result_payload_bytes=0,
                attempts=(),
            )

        assert (
            slice_end - slice_start
            == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_RETRY_SLICE
        )
        stratum = sum(call.get("graph_key_witness_probe") is False for call in calls)
        candidates = []
        for index in range(
            bounded_graph_reads.GRAPH_TRACE_ACQUISITION_ROWS_PER_STRATUM + 1
        ):
            row = {
                "trace_id": f"trace-{stratum}-{index}",
                "root_span_id": f"root-{stratum}-{index}",
                "start_time": slice_end - timedelta(seconds=index + 1),
            }
            rows.append(row)
            candidates.append(row)
        return BoundedFilterPage(
            rows=[],
            has_more=True,
            complete=False,
            status="degraded",
            error_code="sample_limit",
            total_rows_lower_bound=len(candidates),
            elapsed_ms=1,
            query_count=1,
            rows_returned=len(candidates),
            result_payload_bytes=20,
            attempts=(),
            deferred_candidate_rows=tuple(candidates),
            classification_deferred=True,
        )

    monkeypatch.setattr(bounded_graph_reads, "read_bounded_filter_page", _page)
    analytics = _CandidateAnalytics(observe_type="trace", rows=rows)

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="trace",
    )

    classifier_calls = [
        call for call in analytics.calls if "candidate_trace_ids" in call[1]
    ]
    assert len(calls) == 1 + (bounded_graph_reads.GRAPH_ANY_SPAN_STRATA * 2)
    assert (
        len(classifier_calls)
        == bounded_graph_reads.GRAPH_TRACE_UNINDEXED_UNION_CLASSIFY_QUERY_BUDGET
    )
    assert all(
        len(params["candidate_trace_ids"])
        <= bounded_graph_reads.GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE
        for _, params, *_ in classifier_calls
    )
    assert sample.query_count == len(calls) + len(classifier_calls)
    assert sample.query_count <= bounded_graph_reads.GRAPH_TRACE_UNION_MAX_QUERY_COUNT
    assert sample.query_status == "sampled"
    assert sample.sampling_strata_completed == sample.sampling_strata == 8
    assert (
        len(sample.rows)
        == bounded_graph_reads.GRAPH_TRACE_UNINDEXED_UNION_CANDIDATE_LIMIT
    )
    graph_dispatch._require_renderable_sample(sample)


@pytest.mark.unit
def test_locked_span_graph_retries_dense_temporal_slice_without_skipping_stratum(
    monkeypatch,
):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=14)
    calls = []

    def _page(**kwargs):
        calls.append(kwargs)
        slice_start, slice_end = kwargs["builder"].parse_time_range(kwargs["filters"])
        slice_width = slice_end - slice_start
        if kwargs.get("graph_key_witness_probe"):
            return BoundedFilterPage(
                rows=[],
                has_more=False,
                complete=False,
                status="degraded",
                error_code="read_budget_exceeded",
                total_rows_lower_bound=0,
                elapsed_ms=1,
                query_count=1,
                rows_returned=0,
                result_payload_bytes=0,
                attempts=(),
            )
        if slice_width == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_SLICE:
            return BoundedFilterPage(
                rows=[],
                has_more=False,
                complete=False,
                status="degraded",
                error_code="read_budget_exceeded",
                total_rows_lower_bound=0,
                elapsed_ms=1,
                query_count=1,
                rows_returned=0,
                result_payload_bytes=0,
                attempts=(),
            )
        assert slice_width == bounded_graph_reads.GRAPH_UNINDEXED_SAMPLE_RETRY_SLICE
        index = len(calls) // 2
        return BoundedFilterPage(
            rows=[
                {
                    "project_id": PROJECT_ID,
                    "trace_id": f"trace-{index}",
                    "id": f"span-{index}",
                    "start_time": slice_end - timedelta(seconds=1),
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

    monkeypatch.setattr(bounded_graph_reads, "read_bounded_filter_page", _page)
    analytics = SimpleNamespace(supports_per_query_read_settings=False)

    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="span",
    )

    assert len(calls) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA * 2
    assert all(call["anchor_probe_only"] is False for call in calls)
    assert all(call["graph_key_witness_probe"] is False for call in calls)
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"
    assert sample.sampling_strata_completed == sample.sampling_strata == 8
    assert len(sample.rows) == 8
    graph_dispatch._require_renderable_sample(sample)


@pytest.mark.unit
def test_locked_trace_temporal_candidates_use_resource_safe_union_batches() -> None:
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=14)
    window_width = window_end - window_start
    rows = []
    for stratum in range(bounded_graph_reads.GRAPH_ANY_SPAN_STRATA):
        stratum_end = window_start + (
            window_width * (stratum + 1) / bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
        )
        for index in range(bounded_graph_reads.GRAPH_ANY_SPAN_ROWS_PER_STRATUM + 1):
            rows.append(
                {
                    "trace_id": f"trace-{stratum}-{index:03d}",
                    "root_span_id": f"root-{stratum}-{index:03d}",
                    "start_time": stratum_end - timedelta(seconds=index + 1),
                }
            )

    analytics = _CandidateAnalytics(observe_type="trace", rows=rows)
    analytics.supports_per_query_read_settings = False
    sample = read_graph_candidates(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="trace",
    )

    seed_calls = [call for call in analytics.calls if "filter_seed_limit" in call[1]]
    classifier_calls = [
        call for call in analytics.calls if "candidate_trace_ids" in call[1]
    ]
    classifier_sizes = [
        len(params["candidate_trace_ids"]) for _, params, *_ in classifier_calls
    ]
    assert len(seed_calls) == bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    assert all(
        params["filter_seed_limit"]
        == bounded_graph_reads.GRAPH_TRACE_ACQUISITION_ROWS_PER_STRATUM + 1
        for _, params, *_ in seed_calls
    )
    assert classifier_sizes
    assert max(classifier_sizes) == (
        bounded_graph_reads.GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE
    )
    assert all(
        size <= bounded_graph_reads.GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE
        for size in classifier_sizes
    )
    assert 50 not in classifier_sizes
    assert all(
        "filter_witness_" not in query and "argMinIf(" not in query
        for query, *_ in classifier_calls
    )
    assert (
        len(sample.rows)
        == bounded_graph_reads.GRAPH_TRACE_UNINDEXED_UNION_CANDIDATE_LIMIT
    )
    assert sample.query_status == "sampled"
    assert sample.sampling_strata_completed == sample.sampling_strata == 8
    assert sample.query_count <= bounded_graph_reads.GRAPH_TRACE_UNION_MAX_QUERY_COUNT
    graph_dispatch._require_renderable_sample(sample)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fetch_name", "namespace"),
    [
        ("fetch_eval_chart_series_ch", "observe-eval-chart-series"),
    ],
)
def test_public_graph_wrappers_use_exact_snapshot_without_inline_reads(
    monkeypatch,
    fetch_name,
    namespace,
):
    calls = []

    def _read_or_schedule(actual_namespace, identity, **kwargs):
        calls.append((actual_namespace, identity, kwargs))
        return kwargs["pending_payload"]

    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        _read_or_schedule,
    )
    filters = [_date_filter(), _attribute_filter("final_status", "Rejected")]
    common = {
        "analytics": object(),
        "project_id": PROJECT_ID,
        "filters": filters,
        "interval": "hour",
    }
    if fetch_name == "fetch_eval_chart_series_ch":
        response = graph_dispatch.fetch_eval_chart_series_ch(
            **common,
            req_data_config={
                "id": EVAL_ID,
                "type": "EVAL",
                "output_type": "CHOICES",
                "choices": ["good", "bad"],
            },
            eval_name="quality",
        )
    assert len(calls) == 1
    actual_namespace, identity, options = calls[0]
    assert actual_namespace == namespace
    assert identity["project_id"] == PROJECT_ID
    assert identity["filters"] == filters
    assert identity["interval"] == "hour"
    assert options["refresh"] is False
    assert response == options["pending_payload"]
    pending_items = response if isinstance(response, list) else [response]
    assert all(item["query_status"] == "pending" for item in pending_items)
    assert all(item["query_complete"] is False for item in pending_items)
    assert all(item["query_sampled"] is False for item in pending_items)
    assert all(item["query_refreshing"] is True for item in pending_items)
    assert all(item["data"] == [] for item in pending_items)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fetch_name", "reader_name"),
    [
        ("fetch_system_metric_graph_ch", "read_exact_system_graph"),
        ("fetch_user_system_metric_graph_ch", "read_exact_user_system_graph"),
        ("fetch_eval_graph_ch", "read_exact_eval_graph"),
        ("fetch_annotation_graph_ch", "read_exact_annotation_graph"),
    ],
)
def test_public_primary_graph_wrappers_use_inline_exact_snapshot_reads(
    monkeypatch,
    fetch_name,
    reader_name,
):
    calls = []

    def direct_reader(**kwargs):
        calls.append(kwargs)
        return {
            "metric_name": "metric",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

    monkeypatch.setattr(graph_dispatch, reader_name, direct_reader)
    filters = [_date_filter(), _attribute_filter("final_status", "Rejected")]
    common = {
        "analytics": object(),
        "project_id": PROJECT_ID,
        "filters": filters,
        "interval": "hour",
    }
    if fetch_name == "fetch_system_metric_graph_ch":
        response = graph_dispatch.fetch_system_metric_graph_ch(
            **common,
            metric_id="latency",
            observe_type="trace",
        )
    elif fetch_name == "fetch_user_system_metric_graph_ch":
        response = graph_dispatch.fetch_user_system_metric_graph_ch(
            **common,
            metric_id="active_users",
        )
    elif fetch_name == "fetch_eval_graph_ch":
        response = graph_dispatch.fetch_eval_graph_ch(
            **common,
            req_data_config={
                "id": EVAL_ID,
                "type": "EVAL",
                "output_type": "SCORE",
            },
            observe_type="trace",
        )
    else:
        response = graph_dispatch.fetch_annotation_graph_ch(
            **common,
            req_data_config={"id": LABEL_ID, "output_type": "float"},
            observe_type="trace",
        )

    assert len(calls) == 1
    assert calls[0]["project_id"] == PROJECT_ID
    assert calls[0]["filters"] == filters
    assert calls[0]["interval"] == "hour"
    assert response["query_status"] == "complete"
    assert response["query_complete"] is True
    assert response["query_sampled"] is False
    assert response["query_exact"] is True
    # exact_snapshot is the generated public enum; the single patched call
    # above proves the implementation remains synchronous and request-owned.
    assert response["query_provenance"] == "exact_snapshot"


@pytest.mark.unit
def test_all_system_metrics_wrapper_uses_one_inline_exact_snapshot_read(monkeypatch):
    calls = []

    def direct_reader(**kwargs):
        calls.append(kwargs)
        return {
            "latency": [],
            "tokens": [],
            "cost": [],
            "traffic": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

    monkeypatch.setattr(
        graph_dispatch,
        "read_exact_all_system_metrics",
        direct_reader,
    )
    filters = [_date_filter(), _attribute_filter("final_status", "Rejected")]

    response = graph_dispatch.fetch_all_system_metrics_ch(
        analytics=object(),
        project_id=PROJECT_ID,
        filters=filters,
        interval="hour",
    )

    assert len(calls) == 1
    assert calls[0]["project_id"] == PROJECT_ID
    assert calls[0]["filters"] == filters
    assert calls[0]["interval"] == "hour"
    assert response["query_status"] == "complete"
    assert response["query_complete"] is True
    assert response["query_sampled"] is False
    assert response["query_exact"] is True
    assert response["query_provenance"] == "exact_snapshot"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("observe_type", "window_days", "row_count"),
    [("trace", 180, 1600), ("span", 365, 4096)],
)
def test_bounded_high_cardinality_long_window_is_sampled_and_distributed(
    observe_type, window_days, row_count
):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=window_days)
    window_width = window_end - window_start
    rows = []
    for index in range(row_count):
        row = {
            "trace_id": f"trace-{index:05d}",
            "root_span_id": f"root-{index:05d}",
            "start_time": window_start + (window_width * index / row_count),
        }
        if observe_type == "span":
            row["id"] = f"span-{index:05d}"
        rows.append(row)

    def _read_once():
        return read_graph_candidates(
            analytics=_CandidateAnalytics(observe_type=observe_type, rows=rows),
            project_id=PROJECT_ID,
            filters=[
                _date_filter(window_start, window_end),
                _attribute_filter("score", 0.5, filter_type="number"),
            ],
            observe_type=observe_type,
        )

    first = _read_once()
    second = _read_once()
    identity_field = "trace_id" if observe_type == "trace" else "id"
    first_ids = tuple(row[identity_field] for row in first.rows)
    second_ids = tuple(row[identity_field] for row in second.rows)

    assert first_ids == second_ids
    rows_per_stratum = (
        bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM
        if observe_type == "trace"
        else bounded_graph_reads.GRAPH_ANY_SPAN_ROWS_PER_STRATUM
    )
    stratum_count = (
        bounded_graph_reads.GRAPH_TRACE_STRATA
        if observe_type == "trace"
        else bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    )
    assert len(first.rows) == stratum_count * rows_per_stratum
    assert first.query_complete is False
    assert first.query_status == "sampled"
    assert first.query_error_code == "sample_limit"
    assert first.sampling_strategy == "time_stratified_latest_state"
    assert first.sampling_strata == stratum_count
    assert first.sampling_strata_completed == stratum_count
    assert first.total_rows_lower_bound >= len(first.rows)
    if observe_type == "trace":
        # The long-window selector already emits at most 40 globally ordered
        # traces, so the decoration guard must preserve that sample unchanged.
        assert graph_dispatch._bounded_trace_decoration_sample(first) is first
    first_analytics = _CandidateAnalytics(observe_type=observe_type, rows=rows)
    read_graph_candidates(
        analytics=first_analytics,
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("score", 0.5, filter_type="number"),
        ],
        observe_type=observe_type,
    )
    anchor_ranges = [
        (params["filter_anchor_start"], params["filter_anchor_end"])
        for _, params, *_ in first_analytics.calls
        if "filter_anchor_limit" in params
    ]
    assert len(anchor_ranges) == stratum_count
    assert anchor_ranges[0][0] == window_start
    assert anchor_ranges[-1][1] == window_end
    assert all(
        left_end == right_start
        for (_, left_end), (right_start, _) in zip(
            anchor_ranges,
            anchor_ranges[1:],
            strict=False,
        )
    )
    assert not any(
        "filter_seed_limit" in params for _, params, *_ in first_analytics.calls
    )
    anchor_queries = [
        query
        for query, params, *_ in first_analytics.calls
        if "filter_anchor_limit" in params
    ]
    expected_anchor_order = (
        (
            "ORDER BY observation_type DESC, service_name DESC, "
            "toStartOfHour(start_time) DESC, trace_id DESC, id DESC"
        )
        if observe_type == "trace"
        else (
            "ORDER BY observation_type DESC, service_name DESC, "
            "toStartOfHour(start_time) DESC, trace_id DESC, id DESC, "
            "start_time DESC"
        )
    )
    assert all(
        expected_anchor_order in " ".join(query.split()) for query in anchor_queries
    )
    expected_limit_by = (
        "LIMIT 1 BY trace_id"
        if observe_type == "trace"
        else "LIMIT 1 BY project_id, trace_id, id, start_time"
    )
    assert all(expected_limit_by in query for query in anchor_queries)
    assert len(first_analytics.calls) <= (stratum_count * 2)
    if observe_type == "trace":
        classifier_calls = [
            call for call in first_analytics.calls if "candidate_trace_ids" in call[1]
        ]
        classifiers = [
            query
            for query, params, *_ in classifier_calls
            if "candidate_trace_ids" in params
        ]
        union_size = sum(
            len(params["candidate_trace_ids"]) for _, params, *_ in classifier_calls
        )
        expected_classifier_count = (
            union_size + bounded_graph_reads.GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE - 1
        ) // bounded_graph_reads.GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE
        assert len(classifier_calls) == expected_classifier_count
        assert all("latest_latency_ms" not in query for query in classifiers)
        assert union_size <= (
            bounded_graph_reads.GRAPH_TRACE_STRATA
            * (bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM + 1)
        )
        assert all(
            len(params["candidate_trace_ids"])
            <= bounded_graph_reads.GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE
            for _, params, *_ in classifier_calls
        )
        assert first.sampling_strata == first.sampling_strata_completed == 4
    else:
        assert first.sampling_strata == first.sampling_strata_completed == 8
    stratum_width = window_width / stratum_count
    assert all(
        any(
            window_start + stratum_width * index
            <= row["start_time"]
            < window_start + stratum_width * (index + 1)
            for row in first.rows
        )
        for index in range(stratum_count)
    )


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_dense_long_window_stratum_overflow_remains_explicitly_incomplete(
    observe_type,
):
    window_end = datetime(2026, 7, 31, 7)
    window_start = window_end - timedelta(days=365)
    stratum_start = window_start + (
        (window_end - window_start)
        * (bounded_graph_reads.GRAPH_ANY_SPAN_STRATA - 1)
        / bounded_graph_reads.GRAPH_ANY_SPAN_STRATA
    )
    rows = []
    for index in range(513):
        row = {
            "trace_id": f"trace-{index:05d}",
            "root_span_id": f"root-{index:05d}",
            "start_time": stratum_start + timedelta(microseconds=index + 1),
        }
        if observe_type == "span":
            row["id"] = f"span-{index:05d}"
        rows.append(row)

    sample = read_graph_candidates(
        analytics=_CandidateAnalytics(observe_type=observe_type, rows=rows),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(window_start, window_end),
            _attribute_filter("score", 0.5, filter_type="number"),
        ],
        observe_type=observe_type,
    )

    rows_per_stratum = (
        bounded_graph_reads.GRAPH_TRACE_ROWS_PER_STRATUM
        if observe_type == "trace"
        else bounded_graph_reads.GRAPH_ANY_SPAN_ROWS_PER_STRATUM
    )
    assert len(sample.rows) == rows_per_stratum
    assert sample.query_complete is False
    assert sample.query_status == "sampled"
    assert sample.query_error_code == "sample_limit"
    assert sample.metadata()["query_sampling_strategy"] == (
        "time_stratified_latest_state"
    )
    assert sample.total_rows_lower_bound >= len(sample.rows)


@pytest.mark.unit
def test_distributed_span_sample_keeps_reused_ids_trace_scoped(monkeypatch):
    window_end = START + timedelta(days=7)
    rows = (
        {"trace_id": "trace-a", "id": "shared", "start_time": START},
        {"trace_id": "trace-b", "id": "shared", "start_time": START},
    )
    monkeypatch.setattr(
        bounded_graph_reads,
        "read_bounded_filter_page",
        lambda **_: BoundedFilterPage(
            rows=list(rows),
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
        ),
    )

    sample = read_graph_candidates(
        analytics=object(),
        project_id=PROJECT_ID,
        filters=[
            _date_filter(START, window_end),
            _attribute_filter("final_status", "Rejected"),
        ],
        observe_type="span",
    )

    assert {(row["trace_id"], row["id"]) for row in sample.rows} == {
        ("trace-a", "shared"),
        ("trace-b", "shared"),
    }


@pytest.mark.unit
def test_system_candidate_graph_covers_all_metric_families_and_zero_fills():
    sample = GraphCandidateSample(
        rows=(
            {
                "id": "span-1",
                "start_time": START + timedelta(minutes=1),
                "latency_ms": 20,
                "cost": 0.2,
                "total_tokens": 10,
                "prompt_tokens": 4,
                "completion_tokens": 6,
                "status": "ERROR",
            },
            {
                "id": "span-2",
                "start_time": START + timedelta(minutes=2),
                "latency_ms": 40,
                "cost": 0.4,
                "total_tokens": 30,
                "prompt_tokens": 12,
                "completion_tokens": 18,
                "status": "OK",
            },
        ),
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        window_start=START,
        window_end=END,
        elapsed_ms=12.0,
        query_count=2,
        rows_returned=4,
        result_payload_bytes=100,
        total_rows_lower_bound=2,
    )
    expected = {
        "latency": 30.0,
        "traffic": 2.0,
        "tokens": 40.0,
        "cost": 0.3,
        "error_rate": 50.0,
        "prompt_tokens": 16.0,
        "completion_tokens": 24.0,
    }
    for metric_id, value in expected.items():
        response = aggregate_system_candidate_graph(
            sample, metric_id=metric_id, interval="hour"
        )
        assert response["data"][0]["value"] == value
        assert response["data"][0]["primary_traffic"] == 2
        assert response["query_complete"] is True


def _sample() -> GraphCandidateSample:
    return GraphCandidateSample(
        rows=(
            {
                "trace_id": "11111111-1111-4111-8111-111111111111",
                "root_span_id": "span-1",
                "start_time": START + timedelta(minutes=1),
            },
        ),
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        window_start=START,
        window_end=END,
        elapsed_ms=5,
        query_count=2,
        rows_returned=2,
        result_payload_bytes=20,
        total_rows_lower_bound=1,
    )


class _DecorationAnalytics:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params, timeout_ms, settings))
        return _Result(self.rows)


class _SequenceAnalytics:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params, timeout_ms, settings))
        return _Result(self.responses.pop(0))


@pytest.mark.unit
def test_trace_metric_batches_merge_exact_nullable_averages(monkeypatch):
    trace_id = "11111111-1111-4111-8111-111111111111"
    analytics = _SequenceAnalytics(
        [
            [
                {
                    "trace_id": trace_id,
                    "id": f"span-{index}",
                    "start_time": START + timedelta(microseconds=index),
                }
                for index in range(4)
            ],
            [
                {
                    "time_bucket": START,
                    "graph_latency_sum": 30,
                    "graph_latency_count": 1,
                    "total_tokens": 10,
                    "graph_cost_sum": 3,
                    "graph_cost_count": 2,
                    "traffic_count": 2,
                    "prompt_tokens": 4,
                    "completion_tokens": 6,
                    "graph_error_count": 1,
                }
            ],
            [
                {
                    "time_bucket": START,
                    "graph_latency_sum": 60,
                    "graph_latency_count": 2,
                    "total_tokens": 20,
                    "graph_cost_sum": 4,
                    "graph_cost_count": 1,
                    "traffic_count": 2,
                    "prompt_tokens": 8,
                    "completion_tokens": 12,
                    "graph_error_count": 0,
                }
            ],
        ]
    )
    monkeypatch.setattr(graph_dispatch, "GRAPH_SPAN_METRIC_BATCH_SIZE", 2)

    metrics, metadata = graph_dispatch._fetch_trace_system_metrics(
        analytics=analytics,
        sample=_sample(),
        project_id=PROJECT_ID,
        interval="hour",
        started=graph_dispatch.monotonic(),
        timeout_ms=1_200,
    )

    assert len(analytics.calls) == 3
    assert all(
        len(call[1]["graph_span_identities"]) == 2 for call in analytics.calls[1:]
    )
    assert all("graph_latency_count" in call[0] for call in analytics.calls[1:])
    assert all("graph_cost_count" in call[0] for call in analytics.calls[1:])
    assert metrics["latency"][0]["value"] == 30
    assert metrics["cost"][0]["value"] == pytest.approx(7 / 3)
    assert metrics["traffic"][0]["traffic"] == 4
    assert metrics["tokens"][0]["value"] == 30
    assert metrics["prompt_tokens"][0]["value"] == 12
    assert metrics["completion_tokens"][0]["value"] == 18
    assert metrics["error_rate"][0]["value"] == 25
    assert metadata["query_count"] == _sample().query_count + 3


@pytest.mark.unit
def test_trace_metric_month_bucket_accepts_clickhouse_date_without_losing_exactness():
    trace_id = "11111111-1111-4111-8111-111111111111"
    analytics = _SequenceAnalytics(
        [
            [
                {
                    "trace_id": trace_id,
                    "id": "span-1",
                    "start_time": START,
                }
            ],
            [
                {
                    # ClickHouse ``toStartOfMonth`` returns Date rather than
                    # DateTime with the production native-client settings.
                    "time_bucket": date(2026, 1, 1),
                    "graph_latency_sum": 25,
                    "graph_latency_count": 1,
                    "total_tokens": 10,
                    "graph_cost_sum": 2,
                    "graph_cost_count": 1,
                    "traffic_count": 1,
                    "prompt_tokens": 4,
                    "completion_tokens": 6,
                    "graph_error_count": 0,
                }
            ],
        ]
    )

    metrics, metadata = graph_dispatch._fetch_trace_system_metrics(
        analytics=analytics,
        sample=_sample(),
        project_id=PROJECT_ID,
        interval="month",
        started=graph_dispatch.monotonic(),
        timeout_ms=1_200,
    )

    assert metrics["latency"][0]["timestamp"] == "2026-01-01T00:00:00"
    assert metrics["latency"][0]["value"] == 25.0
    assert metrics["traffic"][0]["traffic"] == 1
    assert metadata["query_complete"] is True
    assert metadata["query_count"] == _sample().query_count + 2


@pytest.mark.unit
def test_trace_metric_bucket_still_rejects_non_temporal_schema_drift():
    trace_id = "11111111-1111-4111-8111-111111111111"
    analytics = _SequenceAnalytics(
        [
            [{"trace_id": trace_id, "id": "span-1", "start_time": START}],
            [{"time_bucket": "2026-01-01", "traffic_count": 1}],
        ]
    )

    with pytest.raises(
        AssertionError,
        match="trace metric query returned an invalid bucket",
    ):
        graph_dispatch._fetch_trace_system_metrics(
            analytics=analytics,
            sample=_sample(),
            project_id=PROJECT_ID,
            interval="month",
            started=graph_dispatch.monotonic(),
            timeout_ms=1_200,
        )


@pytest.mark.unit
def test_trace_metric_default_batch_keeps_1025_identities_exact():
    trace_id = "11111111-1111-4111-8111-111111111111"
    identities = [
        {
            "trace_id": trace_id,
            "id": f"span-{index}",
            "start_time": START + timedelta(microseconds=index),
        }
        for index in range(1_025)
    ]
    analytics = _SequenceAnalytics(
        [
            identities,
            [
                {
                    "time_bucket": START,
                    "graph_latency_sum": 1_024,
                    "graph_latency_count": 1_024,
                    "total_tokens": 1_024,
                    "graph_cost_sum": 1_024,
                    "graph_cost_count": 1_024,
                    "traffic_count": 1_024,
                    "prompt_tokens": 1_024,
                    "completion_tokens": 1_024,
                    "graph_error_count": 0,
                }
            ],
            [
                {
                    "time_bucket": START,
                    "graph_latency_sum": 1,
                    "graph_latency_count": 1,
                    "total_tokens": 1,
                    "graph_cost_sum": 1,
                    "graph_cost_count": 1,
                    "traffic_count": 1,
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "graph_error_count": 1,
                }
            ],
        ]
    )

    metrics, metadata = graph_dispatch._fetch_trace_system_metrics(
        analytics=analytics,
        sample=_sample(),
        project_id=PROJECT_ID,
        interval="hour",
        started=graph_dispatch.monotonic(),
        timeout_ms=1_200,
    )

    metric_calls = [
        call for call in analytics.calls if "graph_span_identities" in call[1]
    ]
    assert [len(call[1]["graph_span_identities"]) for call in metric_calls] == [
        graph_dispatch.GRAPH_SPAN_METRIC_BATCH_SIZE,
        1,
    ]
    assert metrics["traffic"][0]["traffic"] == 1_025
    assert metrics["latency"][0]["value"] == 1
    assert metrics["error_rate"][0]["value"] == pytest.approx(100 / 1_025)
    assert metadata["query_count"] == _sample().query_count + 3


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_annotation_latest_state_uses_trace_scoped_entities_without_schema_column(
    observe_type, monkeypatch
):
    sample = _sample()
    if observe_type == "span":
        sample = replace(sample, rows=({"id": "colliding-span", "trace_id": "t"},))
    analytics = _DecorationAnalytics(
        [{"created_at": START + timedelta(minutes=1), "value": '{"value": 1}'}]
    )
    score_read = {}

    def _annotation_rows(**kwargs):
        score_read.update(kwargs)
        return [{"created_at": START + timedelta(minutes=1), "value": {"value": 1}}]

    monkeypatch.setattr(
        graph_dispatch,
        "AnnotationLabelScoresProjectPG",
        lambda: SimpleNamespace(annotation_rows_for_candidates=_annotation_rows),
    )

    graph_dispatch._finite_annotation_rows(
        analytics=analytics,
        sample=sample,
        project_id=PROJECT_ID,
        observe_type=observe_type,
        trace_span_identities=(
            (("t", "colliding-span", _unix_microseconds(START)),)
            if observe_type == "trace"
            else ()
        ),
        label_id=LABEL_ID,
        started=graph_dispatch.monotonic(),
    )

    assert analytics.calls == []
    assert score_read["project_id"] == PROJECT_ID
    entity_ids = score_read.get("trace_ids") or score_read.get("span_entities")
    assert entity_ids


@pytest.mark.unit
def test_annotation_score_database_failure_becomes_sanitized_read_budget_error(
    monkeypatch,
):
    from django.db import OperationalError

    source = SimpleNamespace(
        annotation_rows_for_candidates=lambda **_: (_ for _ in ()).throw(
            OperationalError("private database diagnostics")
        )
    )
    monkeypatch.setattr(
        graph_dispatch, "AnnotationLabelScoresProjectPG", lambda: source
    )

    with pytest.raises(BoundedGraphReadError) as raised:
        graph_dispatch._finite_annotation_rows(
            analytics=_DecorationAnalytics([]),
            sample=_sample(),
            project_id=PROJECT_ID,
            observe_type="trace",
            trace_span_identities=(),
            label_id=LABEL_ID,
            started=graph_dispatch.monotonic(),
        )

    assert raised.value.error_code == "read_budget_exceeded"
    assert "private database diagnostics" not in str(raised.value)


@pytest.mark.unit
@pytest.mark.parametrize("scope_builder", ["eval", "annotation"])
def test_external_span_scope_excludes_all_null_trace_bare_id_fallback(scope_builder):
    sample = replace(
        _sample(),
        rows=(
            {"trace_id": "trace-a", "id": "shared", "start_time": START},
            {"trace_id": "trace-b", "id": "shared", "start_time": START},
            {"trace_id": "trace-c", "id": "unique", "start_time": START},
        ),
    )

    if scope_builder == "eval":
        predicate, params = graph_dispatch._eval_entity_scope(sample, "span")
    else:
        predicate, params = graph_dispatch._annotation_entity_scope(sample, "span", ())

    assert params["graph_span_entities"] == (
        ("trace-a", "shared"),
        ("trace-b", "shared"),
        ("trace-c", "unique"),
    )
    assert "NOT isNull(trace_id)" in predicate
    assert "observation_span_id IN" not in predicate
    assert set(params) == {"graph_span_entities"}


@pytest.mark.unit
def test_trace_span_replay_resolves_scope_and_tombstones_from_global_latest_state():
    trace_id = "11111111-1111-4111-8111-111111111111"
    candidate_ids = (
        (trace_id, "stable", START),
        (trace_id, "moved-trace", START + timedelta(seconds=1)),
        (trace_id, "moved-window", START + timedelta(seconds=2)),
        (trace_id, "moved-project", START + timedelta(seconds=3)),
        (trace_id, "tombstoned", START + timedelta(seconds=4)),
    )
    analytics = _SequenceAnalytics(
        [
            [
                {"trace_id": identity[0], "id": identity[1], "start_time": identity[2]}
                for identity in candidate_ids
            ],
            [{"trace_id": trace_id, "id": "stable", "start_time": START}],
        ]
    )

    span_ids, truncated, query_count, rows_returned = (
        graph_dispatch._finite_trace_span_ids(
            analytics=analytics,
            sample=_sample(),
            project_id=PROJECT_ID,
            started=graph_dispatch.monotonic(),
        )
    )

    assert span_ids == ((trace_id, "stable", _unix_microseconds(START)),)
    assert truncated is False
    assert query_count == 2
    assert rows_returned == 6
    seed_query, seed_params, _, _ = analytics.calls[0]
    replay_query, replay_params, _, _ = analytics.calls[1]
    assert seed_params["graph_trace_ids"] == ("11111111-1111-4111-8111-111111111111",)
    assert "project_id = toUUID(%(graph_project_id)s)" in seed_query
    assert "trace_id IN %(graph_trace_ids)s" in seed_query
    assert "start_time >= %(graph_start_date)s" in seed_query
    ordered_candidate_ids = tuple(reversed(candidate_ids))
    assert replay_params["graph_span_ids"] == tuple(
        identity[1] for identity in ordered_candidate_ids
    )
    assert replay_params["graph_span_identities"] == tuple(
        (trace, span, _unix_microseconds(started_at))
        for trace, span, started_at in ordered_candidate_ids
    )
    assert replay_params["graph_span_dates"] == (START.date(),)
    replay_scope = replay_query.split("FROM spans", 1)[1].split(
        "GROUP BY trace_id, id, start_time", 1
    )[0]
    assert "project_id = toUUID(%(graph_project_id)s)" in replay_scope
    assert "trace_id IN %(graph_trace_ids)s" in replay_scope
    assert "toUnixTimestamp64Micro(start_time)" in replay_scope
    assert "IN %(graph_span_identities)s" in replay_scope
    assert "toDate(start_time) IN %(graph_span_dates)s" in replay_scope
    assert "argMax(is_deleted, _version)" in replay_query
    assert "latest_start_time >= %(graph_start_date)s" in replay_query
    assert "latest_is_deleted = 0" in replay_query


@pytest.mark.unit
def test_degraded_response_never_contains_clickhouse_stack_or_raw_message():
    from clickhouse_driver.errors import ServerException

    raw = "Code: 159. DB::Exception Timeout exceeded secret-host stack trace"
    response = graph_dispatch.degraded_graph_response(
        "latency", BoundedGraphReadError("read_budget_exceeded")
    )
    assert response["query_error_code"] == "read_budget_exceeded"
    assert raw not in str(response)
    response = graph_dispatch.degraded_graph_response("latency", RuntimeError(raw))
    assert response["query_error_code"] == "query_failed"
    assert raw not in str(response)

    response = graph_dispatch.degraded_graph_response(
        "latency", ServerException(raw, code=159)
    )
    assert response["query_error_code"] == "read_budget_exceeded"
    assert raw not in str(response)


@pytest.mark.unit
def test_graph_response_contract_distinguishes_sampled_from_degraded():
    from tracer.serializers.filters import ObserveGraphDataResultSerializer

    complete = ObserveGraphDataResultSerializer(
        data={
            "metric_name": "latency",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
        }
    )
    degraded = ObserveGraphDataResultSerializer(
        data={
            "metric_name": "latency",
            "data": [],
            "query_complete": False,
            "query_status": "degraded",
            "query_error_code": "sample_limit",
        }
    )
    sampled = ObserveGraphDataResultSerializer(
        data={
            "metric_name": "latency",
            "data": [
                {
                    "timestamp": "2026-08-03T00:00:00Z",
                    "value": 12,
                    "primary_traffic": 1,
                }
            ],
            "query_complete": False,
            "query_status": "sampled",
            "query_error_code": "sample_limit",
            "query_sampled": True,
            "query_sampling_strategy": "time_stratified_latest_state",
            "query_sampling_strata": 8,
            "query_sampling_strata_completed": 8,
        }
    )
    invalid_sampled_data = ObserveGraphDataResultSerializer(
        data={
            "metric_name": "latency",
            "data": [
                {
                    "timestamp": "2026-08-03T00:00:00Z",
                    "value": 999,
                    "primary_traffic": 999,
                }
            ],
            "query_complete": False,
            "query_status": "degraded",
            "query_error_code": "sample_limit",
        }
    )
    incomplete_sample_coverage = ObserveGraphDataResultSerializer(
        data={
            "metric_name": "latency",
            "data": [],
            "query_complete": False,
            "query_status": "sampled",
            "query_error_code": "sample_limit",
            "query_sampling_strategy": "time_stratified_latest_state",
            "query_sampling_strata": 8,
            "query_sampling_strata_completed": 1,
        }
    )
    assert complete.is_valid(), complete.errors
    assert degraded.is_valid(), degraded.errors
    assert sampled.is_valid(), sampled.errors
    assert not invalid_sampled_data.is_valid()
    assert "data" in invalid_sampled_data.errors
    assert not incomplete_sample_coverage.is_valid()
    assert "query_sampling_strata_completed" in incomplete_sample_coverage.errors


@pytest.mark.unit
def test_graph_contract_empties_sampled_points_without_full_stratum_coverage():
    response = graph_dispatch.enforce_exact_graph_data_contract(
        {
            "metric_name": "latency",
            "data": [{"timestamp": START.isoformat(), "value": 999}],
            "query_complete": False,
            "query_status": "sampled",
            "query_error_code": "sample_limit",
            "query_sampling_strategy": "time_stratified_latest_state",
            "query_sampling_strata": 8,
            "query_sampling_strata_completed": 1,
        }
    )

    assert response["data"] == []
    assert response["query_status"] == "degraded"
    assert response["query_sampled"] is False


@pytest.mark.unit
def test_graph_views_bind_v2_and_have_no_postgres_telemetry_fallback():
    from tracer.views.observation_span import ObservationSpanView
    from tracer.views.trace import TraceView

    for view in (TraceView, ObservationSpanView):
        source = inspect.getsource(view.get_graph_methods)
        assert "V2AnalyticsQueryService" in source
        assert "AnalyticsQueryService()" not in source.replace(
            "V2AnalyticsQueryService()", ""
        )
        assert "_system_metric_graph_postgres" not in source
        assert "str(e)" not in source
        # The sole exception text exposed here is the typed principal-context
        # validation error returned as a 400. ClickHouse and unexpected server
        # failures retain static public messages below.
        assert source.count("str(exc)") == 1
        principal_handler = source.split(
            "except FilterPrincipalContextError as exc:", 1
        )[1]
        assert "bad_request(str(exc))" in principal_handler
        assert '"Graph data could not be loaded"' in source
        assert "isinstance(exc, BoundedGraphReadError)" in source
        assert "is_clickhouse_api_read_unavailable_error(exc)" in source
        assert "raise" in source


@pytest.mark.unit
def test_graph_namespace_validation_never_exposes_parser_exception_text():
    from tracer.views.observation_span import ObservationSpanView
    from tracer.views.project import ProjectView
    from tracer.views.trace import TraceView
    from tracer.views.trace_session import TraceSessionView

    graph_methods = (
        TraceView.get_graph_methods,
        ObservationSpanView.get_graph_methods,
        TraceSessionView.get_session_graph_data,
        ProjectView.get_users_aggregate_graph_data,
    )
    for method in graph_methods:
        source = inspect.getsource(method)
        namespace_handler = source.split("except ValueError:", 1)[1].split(
            "metric_type =", 1
        )[0]
        assert '"property_id is not valid for this graph endpoint"' in (
            namespace_handler
        )
        assert "str(" not in namespace_handler
