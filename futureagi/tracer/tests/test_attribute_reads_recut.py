"""Focused contracts for bounded CH25 attribute discovery/value reads."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import call as mock_call
from urllib.parse import urlencode

import pytest
from clickhouse_driver.errors import ServerException
from django.conf import settings
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.serializers.observation_span import (
    ObservationAttributeListQuerySerializer,
    ObservationAttributeListResponseSerializer,
)
from tracer.serializers.span_attributes import (
    SpanAttributeDetailQuerySerializer,
    SpanAttributeDetailResponseSerializer,
    SpanAttributeKeysResponseSerializer,
    SpanAttributeProjectQuerySerializer,
)
from tracer.services.clickhouse.attribute_cursor_state import (
    ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS,
)
from tracer.services.clickhouse.attribute_reads import (
    _LATEST_CARDINALITY_SQL,
    _LATEST_JSON_TARGET_SQL,
    _LATEST_TARGET_SQL,
    _STRATIFIED_CANDIDATE_SQL,
    ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT,
    ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT,
    ATTRIBUTE_KEY_CURSOR_EXACT_MAX_CANDIDATE_PAGES,
    ATTRIBUTE_KEY_CURSOR_EXACT_MAX_EMPTY_SEGMENT,
    ATTRIBUTE_KEY_CURSOR_EXACT_MIN_SEGMENT,
    ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_LIMIT,
    ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_PAGES,
    ATTRIBUTE_KEY_CURSOR_MAX_TOKEN_BYTES,
    ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
    ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS,
    ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS,
    ATTRIBUTE_READ_CANDIDATE_LIMIT,
    ATTRIBUTE_READ_EXACT_KEY_QUERY_TIMEOUT_MS,
    ATTRIBUTE_READ_EXPLICIT_SEGMENT,
    ATTRIBUTE_READ_JSON_QUERY_TIMEOUT_MS,
    ATTRIBUTE_READ_MAX_PROJECTS,
    ATTRIBUTE_READ_MAX_QUERY_COUNT,
    ATTRIBUTE_READ_MAX_VALUES,
    ATTRIBUTE_READ_METADATA_TIMEOUT_MS,
    ATTRIBUTE_READ_QUERY_TIMEOUT_MS,
    ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT,
    ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT,
    ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT,
    ATTRIBUTE_READ_WALL_TIMEOUT_MS,
    ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT,
    ATTRIBUTE_VALUE_CURSOR_DENSE_CANDIDATE_LIMIT,
    ATTRIBUTE_VALUE_CURSOR_DISTINCT_GROWTH_QUERY_TIME_MS,
    ATTRIBUTE_VALUE_CURSOR_DISTINCT_GUARD_MARGIN_MS,
    ATTRIBUTE_VALUE_CURSOR_DISTINCT_INITIAL_SEGMENT,
    ATTRIBUTE_VALUE_CURSOR_DISTINCT_MAX_SEGMENT,
    ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT,
    ATTRIBUTE_VALUE_CURSOR_DISTINCT_RESOURCE_TARGET_FRACTION,
    ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS,
    ATTRIBUTE_VALUE_CURSOR_INITIAL_SEGMENT,
    ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_LIMIT,
    ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_PAGES,
    ATTRIBUTE_VALUE_CURSOR_MAX_EMPTY_SEGMENT,
    ATTRIBUTE_VALUE_CURSOR_MAX_PAGE_SIZE,
    ATTRIBUTE_VALUE_CURSOR_MAX_SEARCH_PROOFS,
    ATTRIBUTE_VALUE_CURSOR_MAX_UNSEARCHED_CONTINUATION_PROOFS,
    ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
    ATTRIBUTE_VALUE_CURSOR_PROOF_MAX_RESULT_ROWS,
    ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS,
    AttributeCardinalityRead,
    AttributeDetailRead,
    AttributeKeyCursorPageRead,
    AttributeKeyRead,
    AttributeKeyRow,
    AttributeQueryPage,
    AttributeReadMetadata,
    AttributeReadSelector,
    AttributeValueCursorPageRead,
    AttributeValueRead,
    AttributeValueRow,
    IncompleteLatestStateReplay,
    InvalidAttributeKey,
    V2AttributeQueryExecutor,
    _unix_microseconds,
    adaptive_attribute_windows,
    attribute_key_cursor_digest,
    attribute_key_type_cursor_digest,
    attribute_value_cursor_digest,
    merge_read_metadata,
    validate_attribute_key,
)
from tracer.services.clickhouse.list_cursor import (
    cursor_scope_for_request,
    decode_list_cursor,
    encode_list_cursor,
)
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.utils.attribute_suggestion_contract import (
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)
from tracer.utils.filter_operators import (
    JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES,
    JSON_ARRAY_FILTER_MAX_TOTAL_STRING_UTF8_BYTES,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
PROJECT_A = "c4de3065-12b5-488c-a814-aa1c8e3f856f"
PROJECT_B = "790063cd-bc6a-4ad0-866b-35f11b5bc29b"


def test_production_attribute_reads_use_reviewed_thirty_second_wall():
    assert ATTRIBUTE_READ_WALL_TIMEOUT_MS == 30_000
    assert ATTRIBUTE_READ_QUERY_TIMEOUT_MS == ATTRIBUTE_READ_WALL_TIMEOUT_MS
    assert ATTRIBUTE_READ_EXACT_KEY_QUERY_TIMEOUT_MS == ATTRIBUTE_READ_WALL_TIMEOUT_MS
    assert ATTRIBUTE_READ_JSON_QUERY_TIMEOUT_MS == ATTRIBUTE_READ_WALL_TIMEOUT_MS
    # These probes are optional accelerators. Their failures publish no cursor
    # progress, and their shorter caps preserve time for the exact fallback.
    assert (
        ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS < ATTRIBUTE_READ_WALL_TIMEOUT_MS
    )
    assert ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS < ATTRIBUTE_READ_WALL_TIMEOUT_MS
    assert ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS < ATTRIBUTE_READ_WALL_TIMEOUT_MS
    assert ATTRIBUTE_READ_METADATA_TIMEOUT_MS < ATTRIBUTE_READ_WALL_TIMEOUT_MS
    assert ATTRIBUTE_VALUE_CURSOR_INITIAL_SEGMENT == timedelta(seconds=5)
    assert ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT == timedelta(seconds=5)
    assert ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS == 30_000


def test_interactive_property_selector_wall_leaves_http_transport_headroom():
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        wall_timeout_ms=ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS,
    )

    selector._begin_operation()

    assert selector._deadline == pytest.approx(
        selector._clock() + (ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS / 1000),
        abs=0.01,
    )


@dataclass(frozen=True)
class QueryCall:
    sql: str
    params: dict[str, Any]
    timeout_ms: int
    settings: dict[str, Any]


class RecordingExecutor:
    def __init__(self, responder=None, *, distinct_responder=None):
        self.calls: list[QueryCall] = []
        self.responder = responder or (lambda *_: [])
        self.distinct_responder = distinct_responder

    def execute(self, query, params, *, timeout_ms, settings):
        call = QueryCall(query, dict(params), timeout_ms, dict(settings))
        self.calls.append(call)
        if "distinct_limit" in call.params:
            # Existing physical-cursor fixtures predate the optional temporal
            # proof. Default to its exact overflow sentinel so those tests keep
            # exercising the physical fallback; accelerator-specific tests
            # provide an explicit responder below.
            result = (
                self.distinct_responder(call, len(self.calls))
                if self.distinct_responder is not None
                else [{} for _ in range(call.params["distinct_limit"])]
            )
        else:
            result = self.responder(call, len(self.calls))
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, AttributeQueryPage):
            return result
        return AttributeQueryPage(data=list(result), query_time_ms=1.0)


def _metadata(
    *,
    complete: bool = True,
    error_code: str | None = None,
    sampled: bool = False,
):
    return AttributeReadMetadata(
        query_complete=complete,
        query_status="complete" if complete else "sampled" if sampled else "degraded",
        query_error_code=error_code,
        query_window_start=NOW - timedelta(days=365),
        query_window_end=NOW,
        query_count=2,
    )


def test_attribute_metadata_distinguishes_samples_from_incomplete_reads():
    sampled = _metadata(
        complete=False,
        error_code="sample_limit",
        sampled=True,
    )
    degraded = _metadata(
        complete=False,
        error_code="read_budget_exceeded",
    )

    assert sampled.public_payload()["query_status"] == "sampled"
    assert degraded.public_payload()["query_status"] == "degraded"


def test_retained_attribute_window_start_normalizes_only_datetime_or_none():
    from tracer.views.span_attributes import retained_attribute_window_start

    naive_start = datetime(2025, 8, 1)
    assert retained_attribute_window_start(naive_start, window_end=NOW) == (
        naive_start.replace(tzinfo=UTC)
    )
    assert retained_attribute_window_start(None, window_end=NOW) == (
        NOW - timedelta(microseconds=1)
    )
    with pytest.raises(TypeError, match="must be a datetime or None"):
        retained_attribute_window_start(MagicMock(), window_end=NOW)


def test_merged_attribute_metadata_never_hides_a_degraded_phase():
    exact = _metadata()
    sampled = _metadata(
        complete=False,
        error_code="sample_limit",
        sampled=True,
    )
    degraded = _metadata(
        complete=False,
        error_code="read_budget_exceeded",
    )

    sampled_merge = merge_read_metadata(exact, sampled)
    degraded_merge = merge_read_metadata(sampled, degraded)

    assert sampled_merge.query_status == "sampled"
    assert sampled_merge.query_error_code == "sample_limit"
    assert degraded_merge.query_status == "degraded"
    assert degraded_merge.query_error_code == "read_budget_exceeded"


def test_metadata_merge_never_infers_sampled_from_incomplete_complete_status():
    inconsistent = replace(
        _metadata(complete=False, error_code="query_failed"),
        query_status="complete",
    )

    merged = merge_read_metadata(_metadata(), inconsistent)

    assert merged.query_complete is False
    assert merged.query_status == "degraded"
    assert merged.query_error_code == "query_failed"


def _target_row(
    project_id: str,
    span_id: str,
    *,
    trace_id: str | None = None,
    start_time: datetime | None = None,
    is_deleted: int = 0,
    string: Any = None,
    number: Any = None,
    boolean: Any = None,
    legacy_raw: Any = None,
    latest_version: int = 1,
):
    legacy_text = (
        legacy_raw
        if isinstance(legacy_raw, str)
        else json.dumps(legacy_raw, ensure_ascii=False, separators=(",", ":"))
        if legacy_raw is not None
        else ""
    )
    return {
        "project_id": project_id,
        "id": span_id,
        "start_time": start_time or NOW - timedelta(days=1),
        "is_deleted": is_deleted,
        "trace_id": (
            trace_id if trace_id is not None else f"trace-{project_id}-{span_id}"
        ),
        "trace_session_id": "",
        "parent_span_id": "",
        "string_present": string is not None,
        "string_value": string or "",
        "number_present": number is not None,
        "number_value": number or 0,
        "boolean_present": boolean is not None,
        "boolean_value": boolean or 0,
        "legacy_present": legacy_raw is not None,
        "legacy_value_raw": legacy_text,
        "legacy_value_fingerprint": hashlib.sha256(
            legacy_text.encode("utf-8")
        ).hexdigest(),
        "latest_version": latest_version,
    }


def _distinct_value_group(
    value_type: str,
    value: Any,
    *,
    count: int = 1,
) -> dict[str, Any]:
    return {
        "value_type": value_type,
        "value_string": value if value_type == "string" else "",
        "value_number": value if value_type == "number" else 0,
        "value_boolean": value if value_type == "boolean" else 0,
        "value_json_raw": value if value_type == "json" else "",
        "value_count": count,
    }


def _candidate(
    project_id: str,
    span_id: str,
    *,
    trace_id: str | None = None,
    start_time: datetime | None = None,
    candidate_version: int = 1,
):
    return {
        "project_id": project_id,
        "trace_id": (
            trace_id if trace_id is not None else f"trace-{project_id}-{span_id}"
        ),
        "id": span_id,
        "start_time": start_time or NOW - timedelta(days=1),
        "candidate_version": candidate_version,
    }


def _candidate_key(row: dict[str, Any]) -> tuple[datetime, str, str, str]:
    return (
        row["start_time"],
        str(row["id"]),
        str(row["trace_id"]),
        str(row["project_id"]),
    )


def _keyset_candidate_page(
    rows: list[dict[str, Any]], call: QueryCall
) -> list[dict[str, Any]]:
    """Apply the candidate SQL's physical-identity order to fixture rows."""

    unique = {
        (
            str(row["project_id"]),
            str(row["trace_id"]),
            str(row["id"]),
            row["start_time"],
        ): row
        for row in rows
    }
    ordered = sorted(unique.values(), key=_candidate_key, reverse=True)
    if "candidate_before_start_us" in call.params:
        before = (
            datetime.fromtimestamp(
                call.params["candidate_before_start_us"] / 1_000_000,
                tz=UTC,
            ),
            call.params["candidate_before_id"],
            call.params["candidate_before_trace_id"],
            call.params["candidate_before_project_id"],
        )
        ordered = [row for row in ordered if _candidate_key(row) < before]
    return ordered[: call.params["candidate_limit"]]


def _geometric_slice_widths(
    horizon: timedelta,
    *,
    initial: timedelta,
    maximum: timedelta,
) -> tuple[timedelta, ...]:
    """Return the adjacent widths an empty adaptive cursor should certify."""

    remaining = horizon
    width = initial
    slices = []
    while remaining > timedelta():
        proven = min(width, remaining)
        slices.append(proven)
        remaining -= proven
        width = min(width * 2, maximum)
    return tuple(slices)


def _value_cursor_executor(
    candidates: list[dict[str, Any]],
    value_by_id: dict[str, str],
    *,
    fail_candidate_limit: int | None = None,
    fail_replay_limit: int | None = None,
) -> RecordingExecutor:
    by_id = {str(row["id"]): row for row in candidates}

    def respond(call, _):
        if "segment_start" in call.params:
            candidate_limit = call.params["candidate_limit"] - 1
            if candidate_limit == fail_candidate_limit:
                return ReadDeadlineExceeded("expanded candidate exceeded its budget")
            in_segment = [
                row
                for row in candidates
                if call.params["segment_start"]
                <= row["start_time"]
                < call.params["segment_end"]
            ]
            return _keyset_candidate_page(in_segment, call)
        if len(call.params["candidate_ids_0"]) == fail_replay_limit:
            return ReadDeadlineExceeded("expanded replay exceeded its budget")
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=by_id[span_id]["trace_id"],
                start_time=by_id[span_id]["start_time"],
                string=value_by_id[span_id],
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    return RecordingExecutor(respond)


class _ProjectScope:
    def __init__(self, project_ids):
        self.project_ids = project_ids

    def values_list(self, *_args, **_kwargs):
        return self.project_ids


def _authenticated_get(path: str, data: dict[str, Any]):
    request = APIRequestFactory().get(path, data)
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    return request


def _direct_pg_read(_deadline, read):
    return read()


def test_adaptive_windows_are_adjacent_half_open_7d_14d_30d_6mo_1yr_bands():
    windows = adaptive_attribute_windows(NOW)

    assert windows == (
        (NOW - timedelta(days=7), NOW),
        (NOW - timedelta(days=14), NOW - timedelta(days=7)),
        (NOW - timedelta(days=30), NOW - timedelta(days=14)),
        (NOW - timedelta(days=180), NOW - timedelta(days=30)),
        (NOW - timedelta(days=365), NOW - timedelta(days=180)),
    )
    assert all(
        left[0] == right[1] for left, right in zip(windows, windows[1:], strict=False)
    )


@pytest.mark.parametrize("days", [7, 14])
def test_explicit_dense_windows_sample_six_hour_strata_across_full_range(days: int):
    executor = RecordingExecutor()

    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).discover_keys(
        [PROJECT_A],
        exact_key="missing",
        window_start=NOW - timedelta(days=days),
        window_end=NOW,
    )

    segments = [
        (call.params["segment_start"], call.params["segment_end"])
        for call in executor.calls
    ]
    assert len(segments) == ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT
    assert len(set(segments)) == len(segments)
    assert segments[0] == (NOW - timedelta(hours=6), NOW)
    assert (
        NOW - timedelta(days=days),
        NOW - timedelta(days=days) + timedelta(hours=6),
    ) in segments[: ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT - 1]
    assert all(end - start == timedelta(hours=6) for start, end in segments)
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"


@pytest.mark.parametrize("operation", ["keys", "values"])
def test_explicit_temporal_sampling_accepts_value_in_oldest_six_hour_slice(
    operation: str,
):
    window_start = NOW - timedelta(days=7)
    oldest_value_time = window_start + timedelta(minutes=1)
    candidate = _candidate(
        PROJECT_A,
        "oldest-six-hour-value",
        start_time=oldest_value_time,
    )

    def respond(call, _):
        if "segment_start" in call.params:
            return (
                [candidate]
                if call.params["segment_start"]
                <= oldest_value_time
                < call.params["segment_end"]
                else []
            )
        return [
            _target_row(
                PROJECT_A,
                "oldest-six-hour-value",
                start_time=oldest_value_time,
                string="Rechazado",
            )
        ]

    selector = AttributeReadSelector(
        RecordingExecutor(respond),
        now=NOW,
        typed_only=True,
    )
    if operation == "keys":
        read = selector.discover_keys(
            [PROJECT_A],
            exact_key="final_status",
            window_start=window_start,
            window_end=NOW,
        )
        assert read.rows == (AttributeKeyRow("final_status", "string", 1),)
    else:
        read = selector.read_values(
            [PROJECT_A],
            "final_status",
            window_start=window_start,
            window_end=NOW,
        )
        assert read.rows == (AttributeValueRow("Rechazado", "string", 1),)

    assert read.metadata.query_window_start == window_start
    assert read.metadata.query_window_end == NOW


@pytest.mark.parametrize(
    ("horizon_days", "expected_band_count"),
    [(7, 1), (14, 2), (180, 4), (365, 5)],
)
def test_exact_typed_first_probe_covers_each_requested_horizon_band(
    horizon_days: int,
    expected_band_count: int,
) -> None:
    executor = RecordingExecutor()

    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).discover_keys(
        [PROJECT_A],
        exact_key="final_status",
        horizon_days=horizon_days,
    )

    expected_windows = adaptive_attribute_windows(NOW, horizon_days=horizon_days)
    assert read.rows == ()
    assert read.metadata.query_complete is True
    assert len(expected_windows) == expected_band_count
    assert [
        (call.params["segment_start"], call.params["segment_end"])
        for call in executor.calls
    ] == list(expected_windows)


def test_empty_key_inventory_walks_five_bounded_ch25_segments():
    executor = RecordingExecutor()
    read = AttributeReadSelector(executor, now=NOW).discover_keys([PROJECT_A])

    assert read.rows == ()
    assert read.metadata.query_complete is True
    assert len(executor.calls) == 10
    assert [
        (call.params["segment_start"], call.params["segment_end"])
        for call in executor.calls
    ] == [
        segment
        for segment in adaptive_attribute_windows(NOW)
        for _lane in ("typed", "json")
    ]
    typed_calls = [
        call for call in executor.calls if "length(attrs_string.keys)" in call.sql
    ]
    json_calls = [
        call for call in executor.calls if "attributes_extra NOT IN" in call.sql
    ]
    assert len(typed_calls) == len(json_calls) == 5
    for call in executor.calls:
        upper_sql = call.sql.upper()
        assert "FROM SPANS" in upper_sql
        assert (
            "PREWHERE ATTRIBUTE_SOURCE.PROJECT_ID = "
            "TOUUID(%(SCOPE_PROJECT_ID)S)" in upper_sql
        )
        assert call.params["scope_project_id"] == PROJECT_A
        assert (
            "START_TIME >= FROMUNIXTIMESTAMP64MICRO(%(SEGMENT_START_US)S)" in upper_sql
        )
        assert "START_TIME < FROMUNIXTIMESTAMP64MICRO(%(SEGMENT_END_US)S)" in upper_sql
        assert call.params["segment_start_us"] == _unix_microseconds(
            call.params["segment_start"]
        )
        assert call.params["segment_end_us"] == _unix_microseconds(
            call.params["segment_end"]
        )
        assert " FINAL " not in f" {upper_sql} "
        assert "ARRAY JOIN" not in upper_sql
        assert "SELECT DISTINCT" not in upper_sql
        assert "LIMIT 1 BY PROJECT_ID, TRACE_ID, ID, START_TIME" not in upper_sql
        assert "GROUP BY" not in upper_sql
        assert "ATTRIBUTE_SOURCE.PROJECT_ID ASC" in upper_sql
        assert "OBSERVATION_TYPE ASC" in upper_sql
        assert "SERVICE_NAME ASC" in upper_sql
        assert "TOSTARTOFHOUR(ATTRIBUTE_SOURCE.START_TIME) ASC" in upper_sql
        assert 0 < call.timeout_ms <= ATTRIBUTE_READ_QUERY_TIMEOUT_MS
        assert call.settings["max_threads"] == 1
        assert call.settings["optimize_use_projections"] == 0
        assert call.settings["allow_experimental_projection_optimization"] == 0
        assert call.settings["use_skip_indexes"] == 0
        assert call.settings["optimize_read_in_order"] == 1
        assert call.settings["max_block_size"] == 8_192
        assert call.settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
        assert call.settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
        assert "max_rows_to_read" not in call.settings
        assert call.settings["max_result_rows"] == ATTRIBUTE_READ_CANDIDATE_LIMIT + 1
        assert call.settings["timeout_overflow_mode"] == "throw"
    assert all("attributes_extra" not in call.sql for call in typed_calls)
    assert all("attrs_string" not in call.sql for call in json_calls)
    assert all(
        0 < call.timeout_ms <= ATTRIBUTE_READ_JSON_QUERY_TIMEOUT_MS
        for call in json_calls
    )


def test_streaming_candidate_avoids_datetime_bucket_type_coercion():
    """Integer DateTime64 bounds preserve precision without dateDiff."""

    assert (
        "start_time >= fromUnixTimestamp64Micro(%(segment_start_us)s)"
        in _STRATIFIED_CANDIDATE_SQL
    )
    assert (
        "start_time < fromUnixTimestamp64Micro(%(segment_end_us)s)"
        in _STRATIFIED_CANDIDATE_SQL
    )
    assert "dateDiff(" not in _STRATIFIED_CANDIDATE_SQL


def test_latest_cardinality_replay_has_one_grouping_clause():
    assert (
        _LATEST_CARDINALITY_SQL.count("GROUP BY project_id, trace_id, id, start_time")
        == 1
    )


def test_latest_json_hydration_returns_clickhouse_sha256_fingerprint():
    fingerprint_projection = (
        "lower(hex(SHA256(tupleElement(latest_state, {index}))))\n"
        "            AS legacy_value_fingerprint"
    )

    assert fingerprint_projection.format(index=13) in _LATEST_TARGET_SQL
    assert fingerprint_projection.format(index=3) in _LATEST_JSON_TARGET_SQL


def test_cardinality_uses_targeted_session_lane_when_dense_generic_sample_has_none():
    generic_candidates = [
        _candidate(
            PROJECT_A,
            f"generic-{index}",
            trace_id="generic-trace",
            start_time=NOW - timedelta(minutes=index + 1),
        )
        for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1)
    ]
    targeted_candidates = [
        _candidate(
            PROJECT_A,
            f"session-{index}",
            trace_id=f"session-trace-{index}",
            start_time=NOW - timedelta(hours=1, minutes=index),
        )
        for index in range(2)
    ]

    def respond(call, call_number):
        if call_number == 1:
            return generic_candidates
        if call_number == 2:
            return [
                _target_row(
                    PROJECT_A,
                    span_id,
                    trace_id="generic-trace",
                    start_time=next(
                        row["start_time"]
                        for row in generic_candidates
                        if row["id"] == span_id
                    ),
                )
                for span_id in call.params["candidate_ids_0"]
            ]
        if call_number == 3:
            assert "isNotNull(trace_session_id)" in call.sql
            assert "trace_session_id != toUUID" in call.sql
            assert call.settings.get("use_skip_indexes", 1) == 1
            return targeted_candidates
        if call_number == 4:
            rows = []
            for candidate in targeted_candidates:
                row = _target_row(
                    PROJECT_A,
                    candidate["id"],
                    trace_id=candidate["trace_id"],
                    start_time=candidate["start_time"],
                )
                row["trace_session_id"] = "25e06345-d983-4041-b991-720bd1a437bd"
                rows.append(row)
            return rows
        pytest.fail(f"unexpected cardinality query {call_number}")

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).sample_cardinality([PROJECT_A])

    assert read.max_spans_per_trace == ATTRIBUTE_READ_CANDIDATE_LIMIT
    assert read.max_traces_per_session == 2
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_count == 4


def test_cardinality_keyset_pages_past_cleared_targeted_session_rows():
    generic_candidates = [
        _candidate(
            PROJECT_A,
            f"generic-{index}",
            trace_id="generic-trace",
            start_time=NOW - timedelta(minutes=index + 1),
        )
        for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1)
    ]
    stale_session_candidates = [
        _candidate(
            PROJECT_A,
            f"stale-session-{index:03d}",
            trace_id=f"stale-session-trace-{index:03d}",
            start_time=NOW - timedelta(hours=2, seconds=index),
        )
        for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1)
    ]
    live_session_candidate = _candidate(
        PROJECT_A,
        "live-session",
        trace_id="live-session-trace",
        start_time=NOW - timedelta(hours=3),
    )
    stale_by_id = {row["id"]: row for row in stale_session_candidates}

    def stale_latest_rows(call):
        rows = []
        for index, span_id in enumerate(call.params["candidate_ids_0"]):
            candidate = stale_by_id[span_id]
            rows.append(
                _target_row(
                    PROJECT_A,
                    span_id,
                    trace_id=candidate["trace_id"],
                    start_time=candidate["start_time"],
                    # Alternate tombstones with live rows whose latest version
                    # cleared the raw candidate's former session id.
                    is_deleted=index % 2,
                )
            )
        return rows

    def respond(call, call_number):
        if call_number == 1:
            return generic_candidates
        if call_number == 2:
            return [
                _target_row(
                    PROJECT_A,
                    span_id,
                    trace_id="generic-trace",
                    start_time=next(
                        row["start_time"]
                        for row in generic_candidates
                        if row["id"] == span_id
                    ),
                )
                for span_id in call.params["candidate_ids_0"]
            ]
        if call_number == 3:
            assert "isNotNull(trace_session_id)" in call.sql
            assert "candidate_before_start_us" not in call.params
            assert call.settings["optimize_read_in_order"] == 1
            return stale_session_candidates
        if call_number == 4:
            return stale_latest_rows(call)
        if call_number == 5:
            # An unordered storage-order sample is not a valid cursor for the
            # deterministic latest-first query, so continuation restarts at
            # ordered page one.
            assert "ORDER BY\n        start_time DESC" in call.sql
            assert "candidate_before_start_us" not in call.params
            assert "optimize_read_in_order" not in call.settings
            return stale_session_candidates
        if call_number == 6:
            return stale_latest_rows(call)
        if call_number == 7:
            ordered_cursor = stale_session_candidates[
                ATTRIBUTE_READ_CANDIDATE_LIMIT - 1
            ]
            assert call.params["candidate_before_start_us"] == _unix_microseconds(
                ordered_cursor["start_time"]
            )
            assert call.params["candidate_before_id"] == ordered_cursor["id"]
            assert (
                call.params["candidate_before_trace_id"] == ordered_cursor["trace_id"]
            )
            assert call.params["candidate_before_project_id"] == PROJECT_A
            return [live_session_candidate]
        if call_number == 8:
            row = _target_row(
                PROJECT_A,
                live_session_candidate["id"],
                trace_id=live_session_candidate["trace_id"],
                start_time=live_session_candidate["start_time"],
            )
            row["trace_session_id"] = "25e06345-d983-4041-b991-720bd1a437bd"
            return [row]
        pytest.fail(f"unexpected cardinality query {call_number}")

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).sample_cardinality([PROJECT_A])

    assert read.max_spans_per_trace == ATTRIBUTE_READ_CANDIDATE_LIMIT
    assert read.max_traces_per_session == 1
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_count == 8


def test_trace_only_cardinality_does_not_run_targeted_session_lane():
    generic_candidates = [
        _candidate(
            PROJECT_A,
            f"generic-{index}",
            trace_id="generic-trace",
            start_time=NOW - timedelta(minutes=index + 1),
        )
        for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1)
    ]

    def respond(call, call_number):
        if call_number == 1:
            return generic_candidates
        if call_number == 2:
            return [
                _target_row(
                    PROJECT_A,
                    span_id,
                    trace_id="generic-trace",
                    start_time=next(
                        row["start_time"]
                        for row in generic_candidates
                        if row["id"] == span_id
                    ),
                )
                for span_id in call.params["candidate_ids_0"]
            ]
        pytest.fail("trace-only cardinality unexpectedly queried the session lane")

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).sample_cardinality(
        [PROJECT_A], ensure_session_sample=False
    )

    assert read.max_spans_per_trace == ATTRIBUTE_READ_CANDIDATE_LIMIT
    assert read.max_traces_per_session == 0
    assert read.metadata.query_count == 2


def test_v2_executor_reuses_the_process_singleton_ch25_pool(monkeypatch):
    class FakeClient:
        def execute_read(self, query, params, *, timeout_ms, settings):
            return [("ok",)], [("value", "String")], 2.5

    client = FakeClient()
    calls = 0

    def get_client():
        nonlocal calls
        calls += 1
        return client

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.get_v2_query_client", get_client
    )

    first = V2AttributeQueryExecutor()
    second = V2AttributeQueryExecutor()
    page = first.execute(
        "SELECT 1",
        {},
        timeout_ms=100,
        settings={"max_threads": 1},
    )

    assert calls == 2
    assert first.client is client
    assert second.client is client
    assert page.data == [{"value": "ok"}]
    assert page.read_rows is None
    assert page.read_bytes is None


def test_v2_executor_adds_native_read_progress_when_client_supports_it():
    class ProgressClient:
        def execute_read_with_progress(self, query, params, *, timeout_ms, settings):
            return (
                [("ok",)],
                [("value", "String")],
                529.732,
                148_494,
                595_674_646,
            )

        def execute_read(self, *_args, **_kwargs):
            pytest.fail("progress-aware client must use the additive read method")

    page = V2AttributeQueryExecutor(client=ProgressClient()).execute(
        "SELECT 1",
        {},
        timeout_ms=2_500,
        settings={"max_threads": 1},
    )

    assert page == AttributeQueryPage(
        data=[{"value": "ok"}],
        query_time_ms=529.732,
        read_rows=148_494,
        read_bytes=595_674_646,
    )


def test_v2_executor_normalizes_builtin_driver_timeout_to_read_deadline():
    class TimeoutClient:
        def execute_read(self, query, params, *, timeout_ms, settings):
            raise TimeoutError("private driver timeout detail")

    executor = V2AttributeQueryExecutor(client=TimeoutClient())

    with pytest.raises(ReadDeadlineExceeded, match="ClickHouse query timed out"):
        executor.execute(
            "SELECT 1",
            {},
            timeout_ms=100,
            settings={"max_threads": 1},
        )


@pytest.mark.parametrize(
    ("candidate_call", "start_days"),
    [(4, 90), (5, 250)],
    ids=["six-month-band", "one-year-band"],
)
def test_general_exact_key_probe_finds_rare_key_in_later_band(
    candidate_call, start_days
):
    candidate_queries = 0

    def respond(call, _):
        nonlocal candidate_queries
        if "segment_start" in call.params:
            candidate_queries += 1
            return (
                [
                    _candidate(
                        PROJECT_A,
                        "rare-span",
                        start_time=NOW - timedelta(days=start_days),
                    )
                ]
                if candidate_queries == candidate_call
                else []
            )
        return [
            _target_row(
                PROJECT_A,
                "rare-span",
                start_time=NOW - timedelta(days=start_days),
                string="Rejected",
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW, typed_only=True).discover_keys(
        [PROJECT_A], exact_key="final_status"
    )

    assert read.rows == (AttributeKeyRow("final_status", "string", 1),)
    assert read.metadata.query_complete is True
    assert (
        read.metadata.query_window_start
        == adaptive_attribute_windows(NOW)[candidate_call - 1][0]
    )
    assert len(executor.calls) == candidate_call + 1
    for call in executor.calls:
        assert call.params["attribute_key"] == "final_status"
        assert "final_status" not in call.sql
    candidate_sql = next(
        call.sql for call in executor.calls if "segment_start" in call.params
    )
    assert "indexHint(has(mapKeys(attrs_string), %(attribute_key)s))" in candidate_sql
    assert "has(attrs_string.keys, %(attribute_key)s)" in candidate_sql
    assert "length(attrs_string.keys)" not in candidate_sql
    assert "argMin(" not in candidate_sql
    assert "LIMIT 1 BY project_id, trace_id, id, start_time" not in candidate_sql
    assert "GROUP BY" not in candidate_sql
    assert "attribute_source.project_id ASC" in candidate_sql
    assert "toStartOfHour(attribute_source.start_time) ASC" in candidate_sql


def test_exact_key_probe_keyset_pages_past_seed_stale_latest_states() -> None:
    key = "final_status"
    first_page = [
        _candidate(
            PROJECT_A,
            f"stale-{index:04d}",
            trace_id=f"trace-stale-{index:04d}",
            start_time=NOW - timedelta(seconds=index + 1),
        )
        for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT)
    ]
    live_candidate = _candidate(
        PROJECT_A,
        "rare-live",
        trace_id="trace-rare-live",
        start_time=NOW - timedelta(seconds=ATTRIBUTE_READ_CANDIDATE_LIMIT + 1),
    )
    starts_by_id = {
        str(row["id"]): row["start_time"] for row in [*first_page, live_candidate]
    }

    def respond(call, _):
        if "segment_start" in call.params:
            # The +1 row is a truncation sentinel, not part of the replay.
            return _keyset_candidate_page([*first_page, live_candidate], call)
        candidate_ids = call.params["candidate_ids_0"]
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=(
                    "trace-rare-live" if span_id == "rare-live" else f"trace-{span_id}"
                ),
                start_time=starts_by_id[span_id],
                string="Rejected" if span_id == "rare-live" else None,
            )
            for span_id in candidate_ids
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).discover_keys([PROJECT_A], exact_key=key, horizon_days=7)

    assert read.rows == (AttributeKeyRow(key, "string", 1),)
    assert read.metadata.query_complete is True
    # One cheap storage-order probe is replayed first. Because it is entirely
    # stale, continuation restarts at ordered page one, then advances from that
    # ordered page's own cursor.
    assert read.metadata.query_count == 6
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert len(candidate_calls) == 3
    assert "attribute_source.project_id ASC" in candidate_calls[0].sql
    assert "LIMIT 1 BY" not in candidate_calls[0].sql
    assert "candidate_before_start_us" not in candidate_calls[0].params
    assert "toString(attribute_source.project_id) DESC" in candidate_calls[1].sql
    assert "candidate_before_start_us" not in candidate_calls[1].params
    cursor = candidate_calls[2].params
    assert cursor["candidate_before_id"] == first_page[-1]["id"]
    assert cursor["candidate_before_trace_id"] == first_page[-1]["trace_id"]
    assert cursor["candidate_before_project_id"] == PROJECT_A
    assert "candidate_before_start_us" in cursor
    assert (
        "toString(attribute_source.project_id) "
        "< %(candidate_before_project_id)s" in candidate_calls[2].sql
    )
    assert "project_id < toUUID(%(candidate_before_project_id)s)" not in (
        candidate_calls[2].sql
    )
    assert "NOT IN" not in candidate_calls[2].sql
    assert all(key not in call.sql for call in executor.calls)
    assert all(call.params["attribute_key"] == key for call in executor.calls)


def test_storage_order_sample_is_never_reused_as_descending_keyset_cursor() -> None:
    """A differently ordered sample cannot skip a newer live fallback row."""

    key = "final_status"
    storage_sample = [
        _candidate(
            PROJECT_A,
            f"storage-stale-{index:04d}",
            trace_id=f"trace-storage-stale-{index:04d}",
            start_time=NOW - timedelta(days=3, seconds=index + 1),
        )
        for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1)
    ]
    live = _candidate(
        PROJECT_A,
        "newer-live",
        trace_id="trace-newer-live",
        start_time=NOW - timedelta(days=1),
    )
    by_id = {str(row["id"]): row for row in [*storage_sample, live]}

    def respond(call, _):
        if "segment_start" in call.params:
            if "LIMIT 1 BY" not in call.sql:
                # Deliberately unlike the descending fallback order. Seeding a
                # descending keyset from this sample's last row would skip live.
                return storage_sample
            return _keyset_candidate_page([*storage_sample, live], call)
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=by_id[span_id]["trace_id"],
                start_time=by_id[span_id]["start_time"],
                string="Rejected" if span_id == "newer-live" else None,
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).discover_keys([PROJECT_A], exact_key=key, horizon_days=7)

    assert read.rows == (AttributeKeyRow(key, "string", 1),)
    assert read.metadata.query_error_code == "sample_limit"
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert len(candidate_calls) == 2
    assert "LIMIT 1 BY" not in candidate_calls[0].sql
    assert "LIMIT 1 BY" in candidate_calls[1].sql
    assert "candidate_before_start_us" not in candidate_calls[1].params


def test_exact_key_keyset_is_bounded_and_lossless_past_page_cap():
    key = "final_status"
    physical_count = 5 * ATTRIBUTE_READ_CANDIDATE_LIMIT + 1
    candidates = [
        _candidate(
            PROJECT_A,
            f"shared-{index // 2:04d}",
            trace_id=f"trace-{index:04d}",
            # Four identities share every timestamp; two also share an id.
            start_time=NOW - timedelta(seconds=index // 4 + 1),
        )
        for index in range(physical_count)
    ]
    ordered = sorted(candidates, key=_candidate_key, reverse=True)
    live_identity = (
        ordered[-1]["trace_id"],
        ordered[-1]["id"],
        _unix_microseconds(ordered[-1]["start_time"]),
    )
    by_identity = {
        (row["trace_id"], row["id"], _unix_microseconds(row["start_time"])): row
        for row in candidates
    }
    replayed: list[tuple[str, str, int]] = []

    def respond(call, _):
        if "segment_start" in call.params:
            # A duplicate raw version of one physical span must not create a
            # second candidate after LIMIT 1 BY.
            return _keyset_candidate_page([*candidates, candidates[17]], call)
        encoded = list(call.params["candidate_physical_identities_0"])
        replayed.extend(encoded)
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=trace_id,
                start_time=by_identity[(trace_id, span_id, start_us)]["start_time"],
                string="Rejected" if identity == live_identity else None,
            )
            for identity in encoded
            for trace_id, span_id, start_us in [identity]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).discover_keys([PROJECT_A], exact_key=key, horizon_days=7)

    assert read.rows == (AttributeKeyRow(key, "string", 1),)
    assert read.metadata.query_complete is True
    assert read.metadata.query_count == 14
    # The storage-order sample is intentionally replayed before the ordered
    # restart. Identity-keyed merging prevents duplicate counts.
    assert len(replayed) == physical_count + ATTRIBUTE_READ_CANDIDATE_LIMIT
    assert len(set(replayed)) == physical_count
    assert set(replayed) == set(by_identity)

    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert len(candidate_calls) == 7
    assert "LIMIT 1 BY" not in candidate_calls[0].sql
    assert "candidate_before_start_us" not in candidate_calls[1].params
    assert all(
        "excluded_candidate_identities" not in call.params for call in candidate_calls
    )
    assert all(" NOT IN " not in call.sql for call in candidate_calls)
    assert (
        max(len(call.sql) + len(repr(call.params)) for call in candidate_calls) < 8_192
    )
    continuation_sizes = [
        len(call.sql) + len(repr(call.params)) for call in candidate_calls[2:]
    ]
    assert max(continuation_sizes) - min(continuation_sizes) < 64


def test_exact_key_continuation_stops_at_hard_page_cap_and_degrades() -> None:
    candidate_page = 0
    starts_by_id: dict[str, datetime] = {}

    def respond(call, _):
        nonlocal candidate_page
        if "segment_start" in call.params:
            page = candidate_page
            candidate_page += 1
            rows = [
                _candidate(
                    PROJECT_A,
                    f"stale-{page:02d}-{index:04d}",
                    trace_id=f"trace-stale-{page:02d}-{index:04d}",
                    start_time=NOW - timedelta(seconds=page * 1_000 + index + 1),
                )
                for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1)
            ]
            starts_by_id.update((str(row["id"]), row["start_time"]) for row in rows)
            return rows
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=f"trace-{span_id}",
                start_time=starts_by_id[span_id],
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).discover_keys([PROJECT_A], exact_key="final_status")

    assert read.rows == ()
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    assert candidate_page == (
        len(adaptive_attribute_windows(NOW))
        + ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT
    )
    assert read.metadata.query_count == 2 * candidate_page
    final_candidate_call = [
        call for call in executor.calls if "segment_start" in call.params
    ][-1]
    assert set(final_candidate_call.params) >= {
        "candidate_before_start_us",
        "candidate_before_id",
        "candidate_before_trace_id",
        "candidate_before_project_id",
    }
    # Keyset state stays constant-size when the candidate sample size changes.
    assert len(repr(final_candidate_call.params)) < 2_048
    assert "NOT IN" not in final_candidate_call.sql


def test_exact_key_page_cap_is_global_across_horizon_bands() -> None:
    candidate_page = 0
    starts_by_id: dict[str, datetime] = {}

    def respond(call, _):
        nonlocal candidate_page
        if "segment_start" in call.params:
            page = candidate_page
            candidate_page += 1
            segment_end = call.params["segment_end"]
            rows = [
                _candidate(
                    PROJECT_A,
                    f"stale-{page:02d}-{index:04d}",
                    trace_id=f"trace-stale-{page:02d}-{index:04d}",
                    start_time=segment_end - timedelta(seconds=index + 1),
                )
                for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1)
            ]
            starts_by_id.update((str(row["id"]), row["start_time"]) for row in rows)
            return rows
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=f"trace-{span_id}",
                start_time=starts_by_id[span_id],
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).discover_keys([PROJECT_A], exact_key="final_status")

    assert read.rows == ()
    assert read.metadata.query_complete is False
    assert read.metadata.query_error_code == "sample_limit"
    assert candidate_page == (
        len(adaptive_attribute_windows(NOW))
        + ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT
    )
    assert read.metadata.query_count == 2 * candidate_page
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    # Every horizon gets its cheap first probe before the six-page ordered
    # continuation budget is shared round-robin across them.
    assert len({call.params["segment_start"] for call in candidate_calls}) == 5
    assert adaptive_attribute_windows(NOW)[-1][0] in {
        call.params["segment_start"] for call in candidate_calls
    }
    assert sum("LIMIT 1 BY" in call.sql for call in candidate_calls) == (
        ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT
    )


def test_json_enabled_adaptive_exact_key_runs_typed_continuation_before_json():
    starts_by_id: dict[str, datetime] = {}
    live_id = "adaptive-live-final-status"
    continuation_calls = 0

    def respond(call, _):
        nonlocal continuation_calls
        if "segment_start" in call.params:
            if "mapKeys(attrs_string)" not in call.sql:
                pytest.fail("JSON sampling ran before typed continuation completed")
            if "LIMIT 1 BY" in call.sql:
                continuation_calls += 1
                if continuation_calls == ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT:
                    row = _candidate(
                        PROJECT_A,
                        live_id,
                        start_time=call.params["segment_end"] - timedelta(minutes=1),
                    )
                    starts_by_id[live_id] = row["start_time"]
                    return [row]
                rows = [
                    _candidate(
                        PROJECT_A,
                        f"adaptive-continuation-{continuation_calls:02d}-{index:04d}",
                        start_time=call.params["segment_end"]
                        - timedelta(minutes=continuation_calls, seconds=index + 1),
                    )
                    for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1)
                ]
                starts_by_id.update((str(row["id"]), row["start_time"]) for row in rows)
                return rows
            rows = [
                _candidate(
                    PROJECT_A,
                    f"adaptive-stale-{call.params['segment_start']:%j}-{index:04d}",
                    start_time=call.params["segment_end"]
                    - timedelta(seconds=index + 1),
                )
                for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1)
            ]
            starts_by_id.update((str(row["id"]), row["start_time"]) for row in rows)
            return rows
        return [
            _target_row(
                PROJECT_A,
                span_id,
                start_time=starts_by_id[span_id],
                string="Rechazado" if span_id == live_id else None,
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).discover_keys(
        [PROJECT_A], exact_key="final_status"
    )

    assert read.rows == (AttributeKeyRow("final_status", "string", 1),)
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert len(candidate_calls) == (
        len(adaptive_attribute_windows(NOW))
        + ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT
    )
    assert sum("LIMIT 1 BY" in call.sql for call in candidate_calls) == (
        ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT
    )
    assert all("mapKeys(attrs_string)" in call.sql for call in candidate_calls)


def test_explicit_fourteen_day_stale_typed_key_uses_last_page_before_json():
    starts_by_id: dict[str, datetime] = {}
    live_id = "explicit-live-final-status"

    def respond(call, _):
        if "segment_start" in call.params:
            if "mapKeys(attrs_string)" not in call.sql:
                pytest.fail("JSON sampling consumed the typed continuation page")
            if "LIMIT 1 BY" in call.sql:
                row = _candidate(
                    PROJECT_A,
                    live_id,
                    start_time=call.params["segment_end"] - timedelta(minutes=1),
                )
                starts_by_id[live_id] = row["start_time"]
                return [row]
            rows = [
                _candidate(
                    PROJECT_A,
                    f"explicit-stale-{call.params['segment_start']:%j}-{index:04d}",
                    start_time=call.params["segment_end"]
                    - timedelta(seconds=index + 1),
                )
                for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1)
            ]
            starts_by_id.update((str(row["id"]), row["start_time"]) for row in rows)
            return rows
        return [
            _target_row(
                PROJECT_A,
                span_id,
                start_time=starts_by_id[span_id],
                string="Rechazado" if span_id == live_id else None,
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).discover_keys(
        [PROJECT_A],
        exact_key="final_status",
        window_start=NOW - timedelta(days=14),
        window_end=NOW,
    )

    assert read.rows == (AttributeKeyRow("final_status", "string", 1),)
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert len(candidate_calls) == ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT
    assert sum("LIMIT 1 BY" in call.sql for call in candidate_calls) == 1
    assert all("mapKeys(attrs_string)" in call.sql for call in candidate_calls)
    assert read.metadata.query_count == ATTRIBUTE_READ_MAX_QUERY_COUNT


def test_browse_inventory_stops_after_first_verified_dense_sample():
    def respond(call, _):
        if "segment_start" in call.params:
            if call.params["segment_start"] != adaptive_attribute_windows(NOW)[0][0]:
                return []
            return [
                _candidate(PROJECT_A, f"sampled-span-{index:04d}")
                for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1)
            ]
        return [
            {
                "project_id": PROJECT_A,
                "id": span_id,
                "start_time": NOW - timedelta(days=1),
                "is_deleted": 0,
                "trace_id": f"trace-{PROJECT_A}-{span_id}",
                "trace_session_id": "",
                "parent_span_id": "",
                "string_keys": ["final_status"],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": "{}",
            }
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).discover_keys([PROJECT_A])

    assert read.rows == (
        AttributeKeyRow("final_status", "string", ATTRIBUTE_READ_CANDIDATE_LIMIT),
    )
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_count == 2
    assert read.metadata.query_window_start == NOW - timedelta(days=7)
    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert "LIMIT 1 BY project_id, trace_id, id, start_time" not in candidate_call.sql
    assert "GROUP BY" not in candidate_call.sql
    assert "attribute_source.project_id ASC" in candidate_call.sql
    assert candidate_call.settings["optimize_read_in_order"] == 1
    assert candidate_call.params["candidate_limit"] == (
        ATTRIBUTE_READ_CANDIDATE_LIMIT + 1
    )


def test_latest_replay_uses_index_pruning_and_exact_physical_identities():
    candidates = [
        _candidate(PROJECT_A, "duplicate-id"),
        _candidate(PROJECT_B, "duplicate-id"),
        _candidate(PROJECT_A, "string-second"),
        _candidate(PROJECT_A, "number"),
        _candidate(PROJECT_A, "boolean"),
        _candidate(PROJECT_A, "legacy-string"),
        _candidate(PROJECT_A, "legacy-number"),
        _candidate(PROJECT_A, "legacy-boolean"),
        _candidate(PROJECT_A, "cleared"),
        _candidate(PROJECT_A, "legacy-object"),
    ]
    latest = [
        # Same id in two projects: one live value and one opposite tombstone.
        _target_row(PROJECT_A, "duplicate-id", string="Rejected"),
        _target_row(
            PROJECT_B,
            "duplicate-id",
            is_deleted=1,
            string="must-not-resurrect",
        ),
        _target_row(PROJECT_A, "string-second", string="Rejected"),
        _target_row(PROJECT_A, "number", number=42),
        _target_row(PROJECT_A, "boolean", boolean=True),
        _target_row(PROJECT_A, "legacy-string", legacy_raw='"legacy"'),
        _target_row(PROJECT_A, "legacy-number", legacy_raw="7"),
        _target_row(PROJECT_A, "legacy-boolean", legacy_raw="false"),
        _target_row(PROJECT_A, "cleared"),
        _target_row(PROJECT_A, "legacy-object", legacy_raw='{"x": 1}'),
    ]
    typed_candidate_ids = {
        "duplicate-id",
        "string-second",
        "number",
        "boolean",
        "cleared",
    }
    json_candidate_ids = {
        "legacy-string",
        "legacy-number",
        "legacy-boolean",
        "legacy-object",
    }

    def respond(call, _):
        if "segment_start" in call.params:
            if call.params["segment_start"] != adaptive_attribute_windows(NOW)[0][0]:
                return []
            selected_ids = (
                json_candidate_ids
                if "JSONHas(attributes_extra" in call.sql
                else typed_candidate_ids
            )
            return [row for row in candidates if row["id"] in selected_ids]

        wanted: set[tuple[str, str, str, int]] = set()
        index = 0
        while f"candidate_project_{index}" in call.params:
            project_id = call.params[f"candidate_project_{index}"]
            wanted.update(
                (project_id, trace_id, span_id, start_us)
                for trace_id, span_id, start_us in call.params[
                    f"candidate_physical_identities_{index}"
                ]
            )
            index += 1
        return [
            row
            for row in latest
            if (
                row["project_id"],
                row["trace_id"],
                row["id"],
                _unix_microseconds(row["start_time"]),
            )
            in wanted
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).read_values(
        [PROJECT_A, PROJECT_B], "final_status"
    )

    assert read.rows == (
        AttributeValueRow("Rejected", "string", 2),
        AttributeValueRow(42.0, "number", 1),
        AttributeValueRow(True, "boolean", 1),
    )
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    replay = next(call for call in executor.calls if "segment_start" not in call.params)
    replay_prewhere = replay.sql.split("PREWHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "start_time >=" not in replay_prewhere
    assert "start_time <" not in replay_prewhere
    assert "toString(project_id)" not in replay_prewhere
    assert "toString(id)" not in replay_prewhere
    assert "project_id = toUUID(%(candidate_project_0)s)" in replay_prewhere
    assert "project_id = toUUID(%(candidate_project_1)s)" in replay_prewhere
    assert "id IN %(candidate_ids_0)s" in replay_prewhere
    assert "trace_id IN %(candidate_trace_ids_0)s" in replay_prewhere
    assert "toDate(start_time) IN %(candidate_dates_0)s" in replay_prewhere
    assert (
        "(trace_id, id, toUnixTimestamp64Micro(start_time)) "
        "IN %(candidate_physical_identities_0)s" in replay_prewhere
    )
    assert replay.params["candidate_project_0"] == PROJECT_A
    assert replay.params["candidate_project_1"] == PROJECT_B
    assert "duplicate-id" in replay.params["candidate_ids_0"]
    assert replay.params["candidate_ids_1"] == ("duplicate-id",)
    assert replay.params["candidate_trace_ids_1"] == (
        f"trace-{PROJECT_B}-duplicate-id",
    )
    assert replay.params["candidate_dates_1"] == ((NOW - timedelta(days=1)).date(),)
    assert replay.params["candidate_physical_identities_1"] == (
        (
            f"trace-{PROJECT_B}-duplicate-id",
            "duplicate-id",
            _unix_microseconds(NOW - timedelta(days=1)),
        ),
    )
    assert PROJECT_A not in replay.sql
    assert PROJECT_B not in replay.sql
    assert replay.settings["max_threads"] == 1
    assert replay.settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
    assert replay.settings["max_result_rows"] == 6
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert all(
        not (
            "mapContains(attrs_string" in call.sql
            and "JSONHas(attributes_extra" in call.sql
        )
        for call in candidate_calls
    )


def test_reused_span_ids_keep_trace_and_start_time_scoped_tombstones():
    first = NOW - timedelta(days=1)
    second = first + timedelta(minutes=1)
    candidates = [
        _candidate(
            PROJECT_A,
            "shared",
            trace_id="trace-a",
            start_time=first,
        ),
        _candidate(
            PROJECT_A,
            "shared",
            trace_id="trace-b",
            start_time=first,
        ),
        _candidate(
            PROJECT_A,
            "shared",
            trace_id="trace-a",
            start_time=second,
        ),
        _candidate(
            PROJECT_A,
            "empty-trace",
            trace_id="",
            start_time=first,
        ),
    ]
    latest = [
        _target_row(
            PROJECT_A,
            "shared",
            trace_id="trace-a",
            start_time=first,
            string="Rejected",
        ),
        _target_row(
            PROJECT_A,
            "shared",
            trace_id="trace-b",
            start_time=first,
            is_deleted=1,
            string="must-not-resurrect",
        ),
        _target_row(
            PROJECT_A,
            "shared",
            trace_id="trace-a",
            start_time=second,
            string="Rejected",
        ),
        _target_row(
            PROJECT_A,
            "empty-trace",
            trace_id="",
            start_time=first,
            string="Rejected",
        ),
    ]
    emitted = False

    def respond(call, _):
        nonlocal emitted
        if "segment_start" in call.params:
            if emitted:
                return []
            emitted = True
            return candidates
        wanted = set(call.params["candidate_physical_identities_0"])
        return [
            row
            for row in latest
            if (
                row["trace_id"],
                row["id"],
                _unix_microseconds(row["start_time"]),
            )
            in wanted
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).read_values([PROJECT_A], "final_status")

    assert read.rows == (AttributeValueRow("Rejected", "string", 3),)
    replay = next(call for call in executor.calls if "segment_start" not in call.params)
    assert replay.params["candidate_ids_0"] == ("shared", "empty-trace")
    assert replay.params["candidate_trace_ids_0"] == ("trace-a", "trace-b", "")
    assert replay.params["candidate_physical_identities_0"] == (
        ("trace-a", "shared", _unix_microseconds(first)),
        ("trace-b", "shared", _unix_microseconds(first)),
        ("trace-a", "shared", _unix_microseconds(second)),
        ("", "empty-trace", _unix_microseconds(first)),
    )
    assert "GROUP BY project_id, trace_id, id, start_time" in replay.sql


def test_detail_read_uses_latest_versions_and_does_not_resurrect_tombstones():
    """Candidates can come from old live versions; only replayed state counts."""

    emitted = False

    def respond(call, _):
        nonlocal emitted
        if "segment_start" in call.params:
            if emitted:
                return []
            emitted = True
            return [
                _candidate(PROJECT_A, "later-deleted"),
                _candidate(PROJECT_A, "later-updated"),
                _candidate(PROJECT_A, "still-live"),
            ]
        latest = [
            _target_row(
                PROJECT_A,
                "later-deleted",
                is_deleted=1,
                string="stale-value",
            ),
            _target_row(PROJECT_A, "later-updated", string="new-value"),
            _target_row(PROJECT_A, "still-live", string="new-value"),
        ]
        wanted = set(call.params["candidate_physical_identities_0"])
        return [
            row
            for row in latest
            if (
                row["trace_id"],
                row["id"],
                _unix_microseconds(row["start_time"]),
            )
            in wanted
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).read_detail([PROJECT_A], "final_status")

    assert read == AttributeDetailRead(
        "string",
        (AttributeValueRow("new-value", "string", 2),),
        read.metadata,
    )
    assert read.metadata.query_complete is True
    replay = next(call for call in executor.calls if "segment_start" not in call.params)
    assert "argMax(" in replay.sql
    assert "_version" in replay.sql
    assert " FINAL " not in f" {replay.sql.upper()} "
    assert "max_rows_to_read" not in replay.settings
    assert replay.settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024


def test_typed_map_key_browse_and_legacy_json_scalar_precedence():
    def respond(call, _):
        if "segment_start" in call.params:
            if call.params["segment_start"] != adaptive_attribute_windows(NOW)[0][0]:
                return []
            return [_candidate(PROJECT_A, "wide")]
        return [
            {
                "project_id": PROJECT_A,
                "id": "wide",
                "start_time": NOW - timedelta(days=1),
                "is_deleted": 0,
                "trace_id": f"trace-{PROJECT_A}-wide",
                "trace_session_id": "",
                "parent_span_id": "",
                "string_keys": ["alpha", "shared"],
                "number_keys": ["number", "shared"],
                "boolean_keys": ["enabled"],
                "attributes_extra": json.dumps(
                    {
                        "legacy": "x",
                        "legacy_number": 2,
                        "legacy_boolean": True,
                        "object": {"ignored": True},
                        "array": [1],
                        "null": None,
                        "shared": 10,
                    }
                ),
            }
        ]

    read = AttributeReadSelector(RecordingExecutor(respond), now=NOW).discover_keys(
        [PROJECT_A]
    )

    assert {(row.key, row.type) for row in read.rows} == {
        ("alpha", "string"),
        ("shared", "string"),
        ("number", "number"),
        ("enabled", "boolean"),
        ("legacy", "string"),
        ("legacy_number", "number"),
        ("legacy_boolean", "boolean"),
    }
    assert read.metadata.query_complete is False
    assert read.metadata.query_error_code == "sample_limit"


def test_exact_structured_json_key_is_not_reported_as_complete_empty():
    def respond(call, _):
        if "segment_start" in call.params:
            if (
                call.params["segment_start"] != adaptive_attribute_windows(NOW)[0][0]
                or "mapKeys(attrs_string)" in call.sql
            ):
                return []
            return [_candidate(PROJECT_A, "structured")]
        return [
            _target_row(
                PROJECT_A,
                "structured",
                legacy_raw='{"nested":true}',
            )
        ]

    executor = RecordingExecutor(respond)
    selector = AttributeReadSelector(executor, now=NOW)
    key_read = selector.discover_keys([PROJECT_A], exact_key="structured")

    assert key_read.rows == ()
    assert key_read.metadata.query_complete is False
    assert key_read.metadata.query_error_code == "sample_limit"
    json_seed = next(
        call
        for call in executor.calls
        if "segment_start" in call.params and "mapKeys(attrs_string)" not in call.sql
    )
    assert "attributes_extra" not in json_seed.sql
    assert "JSONHas(" not in json_seed.sql
    assert json_seed.params["candidate_limit"] == (
        ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1
    )
    assert json_seed.settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
    assert "max_rows_to_read" not in json_seed.settings


def test_explicit_fourteen_day_exact_json_key_uses_one_bounded_identity_sample():
    starts_by_id: dict[str, datetime] = {}

    def respond(call, _):
        if "segment_start" in call.params:
            if "mapKeys(attrs_string)" in call.sql:
                return []
            rows = [
                _candidate(
                    PROJECT_A,
                    f"raw-json-{index}",
                    start_time=call.params["segment_start"]
                    + timedelta(seconds=index + 1),
                )
                for index in range(ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1)
            ]
            starts_by_id.update((str(row["id"]), row["start_time"]) for row in rows)
            return rows
        return [
            _target_row(
                PROJECT_A,
                span_id,
                start_time=starts_by_id[span_id],
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).discover_keys(
        [PROJECT_A],
        exact_key="absent_json_key",
        window_start=NOW - timedelta(days=14),
        window_end=NOW,
    )

    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    typed_calls = [
        call for call in candidate_calls if "mapKeys(attrs_string)" in call.sql
    ]
    json_calls = [
        call for call in candidate_calls if "mapKeys(attrs_string)" not in call.sql
    ]
    hydration_calls = [
        call for call in executor.calls if "segment_start" not in call.params
    ]

    assert read.rows == ()
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    assert len(typed_calls) == 14
    assert len(json_calls) == 1
    assert len(candidate_calls) == ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT
    assert len(hydration_calls) == 1
    assert read.metadata.query_count == 16
    assert all("attributes_extra" not in call.sql for call in candidate_calls)
    assert all("JSONHas(" not in call.sql for call in candidate_calls)
    assert json_calls[0].params["candidate_limit"] == (
        ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1
    )
    assert json_calls[0].settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
    assert "max_rows_to_read" not in json_calls[0].settings
    assert len(hydration_calls[0].params["candidate_ids_0"]) <= (
        ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT
    )


def test_exact_json_key_read_budget_becomes_an_explicit_sample():
    def respond(call, _):
        if "segment_start" in call.params and "mapKeys(attrs_string)" not in call.sql:
            return ReadDeadlineExceeded("private JSON identity timeout")
        return []

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).discover_keys(
        [PROJECT_A],
        exact_key="rare_json_key",
        horizon_days=7,
    )

    assert read.rows == ()
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_count == 2
    json_call = executor.calls[-1]
    assert "attributes_extra" not in json_call.sql
    assert "JSONHas(" not in json_call.sql
    assert 0 < json_call.timeout_ms <= ATTRIBUTE_READ_JSON_QUERY_TIMEOUT_MS
    assert json_call.settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024


def test_exact_typed_key_read_budget_is_not_published_as_a_sample():
    def respond(call, _):
        if "segment_start" in call.params and "mapKeys(attrs_string)" in call.sql:
            return ReadDeadlineExceeded("private typed identity timeout")
        return []

    executor = RecordingExecutor(respond)

    with pytest.raises(ReadDeadlineExceeded, match="private typed identity timeout"):
        AttributeReadSelector(executor, now=NOW).discover_keys(
            [PROJECT_A],
            exact_key="final_status",
            horizon_days=7,
        )

    assert len(executor.calls) == 1
    assert "mapKeys(attrs_string)" in executor.calls[0].sql
    assert 0 < executor.calls[0].timeout_ms <= ATTRIBUTE_READ_EXACT_KEY_QUERY_TIMEOUT_MS


def test_exact_typed_key_timeout_keeps_query_safety_caps() -> None:
    def respond(call, _):
        if "segment_start" in call.params:
            return [_candidate(PROJECT_A, "typed-final-status")]
        return [_target_row(PROJECT_A, "typed-final-status", string="Rejected")]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).discover_keys(
        [PROJECT_A],
        exact_key="final_status",
        horizon_days=7,
    )

    assert read.rows == (AttributeKeyRow("final_status", "string", 1),)
    assert len(executor.calls) == 2
    assert all(
        0 < call.timeout_ms <= ATTRIBUTE_READ_EXACT_KEY_QUERY_TIMEOUT_MS
        for call in executor.calls
    )
    assert all(call.settings["max_threads"] == 1 for call in executor.calls)
    assert all(
        call.settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
        for call in executor.calls
    )
    assert all(
        call.settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
        for call in executor.calls
    )
    assert all("max_rows_to_read" not in call.settings for call in executor.calls)


def test_structured_json_value_picker_is_explicitly_degraded():
    def respond(call, _):
        if "segment_start" in call.params:
            if (
                call.params["segment_start"] != adaptive_attribute_windows(NOW)[0][0]
                or "candidate_version" in call.sql
            ):
                return []
            return [_candidate(PROJECT_A, "structured")]
        return [
            _target_row(
                PROJECT_A,
                "structured",
                legacy_raw='["one","two"]',
            )
        ]

    read = AttributeReadSelector(RecordingExecutor(respond), now=NOW).read_values(
        [PROJECT_A], "structured"
    )

    assert read.rows == ()
    assert read.metadata.query_complete is False
    assert read.metadata.query_error_code == "sample_limit"


def test_array_filter_picker_surfaces_json_array_and_preserves_typed_maps():
    def respond(call, _):
        if "segment_start" in call.params:
            if call.params["segment_start"] != adaptive_attribute_windows(NOW)[0][0]:
                return []
            return [_candidate(PROJECT_A, "array-and-map")]
        return [
            {
                "project_id": PROJECT_A,
                "id": "array-and-map",
                "start_time": NOW - timedelta(days=1),
                "is_deleted": 0,
                "trace_id": f"trace-{PROJECT_A}-array-and-map",
                "trace_session_id": "",
                "parent_span_id": "",
                "string_keys": ["final_status", "shared"],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": json.dumps(
                    {
                        "json_array": ["one", 2, True],
                        "json_scalar": "not-filterable-from-overflow",
                        "json_object": {"nested": True},
                        "shared": ["typed-map-wins"],
                    }
                ),
            }
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="arrays",
    ).discover_keys([PROJECT_A])

    assert {(row.key, row.type) for row in read.rows} == {
        ("final_status", "string"),
        ("shared", "string"),
        ("json_array", "array"),
    }
    assert read.metadata.query_complete is True
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert any("attributes_extra NOT IN" in call.sql for call in candidate_calls)
    assert any("length(attrs_string.keys)" in call.sql for call in candidate_calls)
    assert all(
        not (
            "attributes_extra NOT IN" in call.sql
            and "length(attrs_string.keys)" in call.sql
        )
        for call in candidate_calls
    )


def test_array_filter_picker_does_not_advertise_json_object_as_filterable():
    def respond(call, _):
        if "segment_start" in call.params:
            if (
                call.params["segment_start"] != adaptive_attribute_windows(NOW)[0][0]
                or "JSONHas(attributes_extra" not in call.sql
            ):
                return []
            return [_candidate(PROJECT_A, "object-only")]
        return [
            _target_row(
                PROJECT_A,
                "object-only",
                legacy_raw='{"nested":true}',
            )
        ]

    read = AttributeReadSelector(
        RecordingExecutor(respond),
        now=NOW,
        typed_only=True,
        json_attribute_mode="arrays",
    ).discover_keys([PROJECT_A], exact_key="json_object")

    assert read.rows == ()
    assert read.metadata.query_complete is True


def test_structured_filter_picker_advertises_json_array_and_object_types():
    def respond(call, _):
        if "segment_start" in call.params:
            if call.params["segment_start"] != adaptive_attribute_windows(NOW)[0][0]:
                return []
            return [_candidate(PROJECT_A, "structured-json")]
        return [
            {
                "project_id": PROJECT_A,
                "id": "structured-json",
                "start_time": NOW - timedelta(days=1),
                "is_deleted": 0,
                "trace_id": f"trace-{PROJECT_A}-structured-json",
                "trace_session_id": "",
                "parent_span_id": "",
                "string_keys": ["typed_map"],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": json.dumps(
                    {
                        "json_array": ["one", 2, True],
                        "json_object": {"tier": "vip"},
                        "json_scalar": "not-indexed",
                    }
                ),
            }
        ]

    read = AttributeReadSelector(
        RecordingExecutor(respond),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    ).discover_keys([PROJECT_A])

    assert {(row.key, row.type) for row in read.rows} == {
        ("typed_map", "string"),
        ("json_array", "array"),
        ("json_object", "map"),
    }
    assert read.metadata.query_complete is True


def test_eval_mapping_inventory_includes_all_json_value_families():
    def respond(call, _):
        if "segment_start" in call.params:
            if call.params["segment_start"] != adaptive_attribute_windows(NOW)[0][0]:
                return []
            return [_candidate(PROJECT_A, "eval-json")]
        return [
            {
                "project_id": PROJECT_A,
                "id": "eval-json",
                "start_time": NOW - timedelta(days=1),
                "is_deleted": 0,
                "trace_id": f"trace-{PROJECT_A}-eval-json",
                "trace_session_id": "",
                "parent_span_id": "",
                "string_keys": ["typed_map"],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": json.dumps(
                    {
                        "json_scalar": "value",
                        "json_array": ["one"],
                        "json_object": {"nested": True},
                        "json_null": None,
                    }
                ),
            }
        ]

    read = AttributeReadSelector(
        RecordingExecutor(respond),
        now=NOW,
        typed_only=True,
        json_attribute_mode="all",
    ).discover_keys([PROJECT_A])

    assert {(row.key, row.type) for row in read.rows} == {
        ("typed_map", "string"),
        ("json_scalar", "string"),
        ("json_array", "array"),
        ("json_object", "json"),
        ("json_null", "json"),
    }
    assert read.metadata.query_complete is True


def test_array_value_picker_flattens_supported_json_scalars_type_exactly():
    def respond(call, _):
        if "segment_start" in call.params:
            if (
                call.params["segment_start"] != adaptive_attribute_windows(NOW)[0][0]
                or "candidate_version" in call.sql
            ):
                return []
            return [
                _candidate(PROJECT_A, "array-one"),
                _candidate(PROJECT_A, "array-two"),
            ]
        return [
            _target_row(
                PROJECT_A,
                "array-one",
                legacy_raw='["one",1,1.0,true,"one",null,{"skip":1}]',
            ),
            _target_row(
                PROJECT_A,
                "array-two",
                legacy_raw='["one",18446744073709551615,false,["skip"]]',
            ),
        ]

    read = AttributeReadSelector(
        RecordingExecutor(respond),
        now=NOW,
        typed_only=True,
        json_attribute_mode="arrays",
    ).read_values([PROJECT_A], "json_array")

    by_value = {(type(row.value).__name__, row.value): row.count for row in read.rows}
    assert by_value == {
        ("str", "one"): 2,
        ("int", 1): 1,
        ("float", 1.0): 1,
        ("bool", True): 1,
        ("int", 18446744073709551615): 1,
        ("bool", False): 1,
    }
    assert all(row.type == "array" for row in read.rows)
    assert read.metadata.query_complete is True


def test_native_value_precedence_is_string_then_number_then_boolean_then_json():
    row = _target_row(
        PROJECT_A,
        "precedence",
        string="native-string",
        number=99,
        boolean=True,
        legacy_raw='"legacy"',
    )

    assert AttributeReadSelector._decode_target_value(row) == (
        "string",
        "native-string",
    )


def test_typed_string_suggestion_cap_retains_key_and_exact_raw_value() -> None:
    at_limit = "é" * (TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES // 2)
    oversized = "z" * (TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES + 1)
    candidate_time = NOW - timedelta(minutes=30)
    candidates = [
        _candidate(PROJECT_A, "at-limit", start_time=candidate_time),
        _candidate(PROJECT_A, "oversized", start_time=candidate_time),
    ]
    latest = [
        _target_row(
            PROJECT_A,
            "at-limit",
            start_time=candidate_time,
            string=at_limit,
        ),
        _target_row(
            PROJECT_A,
            "oversized",
            start_time=candidate_time,
            string=oversized,
        ),
    ]

    def respond(call, _):
        if "segment_start" in call.params:
            return candidates
        return latest

    key_read = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, typed_only=True
    ).discover_keys(
        [PROJECT_A],
        exact_key="payload",
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
    )
    value_read = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, typed_only=True
    ).read_values(
        [PROJECT_A],
        "payload",
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
    )

    assert key_read.rows == (AttributeKeyRow("payload", "string", 2),)
    assert value_read.rows == (AttributeValueRow(at_limit, "string", 1),)
    # The raw typed-map value remains intact for exact user-entered filtering;
    # only the property suggestion consumer applies the size policy.
    assert AttributeReadSelector._decode_target_value(latest[1]) == (
        "string",
        oversized,
    )


def test_typed_only_picker_never_offers_unfilterable_attributes_extra_scalars():
    emitted = False

    def respond(call, _):
        nonlocal emitted
        if "segment_start" in call.params:
            if emitted:
                return []
            emitted = True
            return [_candidate(PROJECT_A, "typed-and-legacy")]
        return [
            {
                "project_id": PROJECT_A,
                "id": "typed-and-legacy",
                "start_time": NOW - timedelta(days=1),
                "is_deleted": 0,
                "trace_id": f"trace-{PROJECT_A}-typed-and-legacy",
                "trace_session_id": "",
                "parent_span_id": "",
                "string_keys": ["final_status"],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": json.dumps({"json_only": "hidden"}),
            }
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).discover_keys([PROJECT_A])

    assert {(row.key, row.type) for row in read.rows} == {("final_status", "string")}
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert candidate_calls
    assert all("attributes_extra" not in call.sql for call in candidate_calls)
    assert all("JSONHas" not in call.sql for call in candidate_calls)
    replay = next(call for call in executor.calls if "segment_start" not in call.params)
    assert "attributes_extra" not in replay.sql
    assert "JSONHas" not in replay.sql
    assert "trace_session_id" not in replay.sql
    assert "parent_span_id" not in replay.sql


def test_typed_only_value_picker_ignores_legacy_scalar_and_avoids_json_seed():
    emitted = False

    def respond(call, _):
        nonlocal emitted
        if "segment_start" in call.params:
            if emitted:
                return []
            emitted = True
            return [_candidate(PROJECT_A, "legacy-only")]
        return [_target_row(PROJECT_A, "legacy-only", legacy_raw='"hidden"')]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).read_values([PROJECT_A], "json_only")

    assert read.rows == ()
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert candidate_calls
    assert all("JSONHas" not in call.sql for call in candidate_calls)
    assert all("attributes_extra" not in call.sql for call in candidate_calls)
    replay = next(call for call in executor.calls if "segment_start" not in call.params)
    assert "JSONHas" not in replay.sql
    assert "JSONExtractRaw" not in replay.sql
    assert "attributes_extra" not in replay.sql
    assert "trace_session_id" not in replay.sql
    assert "parent_span_id" not in replay.sql


def test_value_search_treats_unicode_like_metacharacters_as_literals():
    emitted = False

    def respond(call, _):
        nonlocal emitted
        if "segment_start" in call.params:
            if emitted:
                return []
            emitted = True
            return [_candidate(PROJECT_A, "literal")]
        return [
            _target_row(
                PROJECT_A,
                "literal",
                string="prefix %_\\路径 suffix",
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).read_values(
        [PROJECT_A], "customer.quote'key", search="%_\\路径"
    )

    assert [row.value for row in read.rows] == ["prefix %_\\路径 suffix"]
    assert all("LIKE" not in call.sql.upper() for call in executor.calls)
    assert all("%_\\路径" not in call.sql for call in executor.calls)
    assert all("customer.quote'key" not in call.sql for call in executor.calls)
    assert all(
        call.params["attribute_key"] == "customer.quote'key"
        for call in executor.calls
        if "attribute_key" in call.params
    )
    certificate = next(
        call for call in executor.calls if "max(_version) AS latest_version" in call.sql
    )
    assert "attribute_key" not in certificate.params
    assert all(
        "attribute_search" not in call.params
        for call in executor.calls
        if "segment_start" in call.params
    )


def test_ascii_value_search_uses_key_only_typed_candidates_and_exact_replay():
    candidate_queries = 0

    def respond(call, _):
        nonlocal candidate_queries
        if "segment_start" in call.params:
            candidate_queries += 1
            return (
                [
                    _candidate(
                        PROJECT_A,
                        "rare-value",
                        start_time=NOW - timedelta(days=250),
                    )
                ]
                if candidate_queries == 5
                else []
            )
        return [
            _target_row(
                PROJECT_A,
                "rare-value",
                start_time=NOW - timedelta(days=250),
                string="prefix NeEdLe%_\\path suffix",
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW, typed_only=True).read_values(
        [PROJECT_A],
        "rare.search.key",
        search="needle%_\\path",
    )

    assert [row.value for row in read.rows] == ["prefix NeEdLe%_\\path suffix"]
    candidates = [call for call in executor.calls if "segment_start" in call.params]
    assert len(candidates) == 5
    assert all("attribute_search" not in call.params for call in candidates)
    assert all("positionCaseInsensitiveUTF8" not in call.sql for call in candidates)
    assert all(
        "indexHint(has(mapKeys(attrs_string), %(attribute_key)s))" in call.sql
        and "has(attrs_string.keys, %(attribute_key)s)" in call.sql
        for call in candidates
    )
    assert all("LIKE" not in call.sql.upper() for call in candidates)
    assert all("needle%_\\path" not in call.sql for call in candidates)


def test_value_read_stops_after_first_verified_dense_sample():
    recent = [
        _candidate(
            PROJECT_A,
            f"recent-{index:04d}",
            trace_id=f"trace-recent-{index:04d}",
            start_time=NOW - timedelta(seconds=index + 1),
        )
        for index in range(ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1)
    ]
    older = _candidate(
        PROJECT_A,
        "older-distinct",
        trace_id="trace-older-distinct",
        start_time=NOW - timedelta(days=250),
    )
    rows_by_id = {str(row["id"]): row for row in [*recent, older]}
    recent_start = adaptive_attribute_windows(NOW)[0][0]
    oldest_start = adaptive_attribute_windows(NOW)[-1][0]

    def respond(call, _):
        if "segment_start" in call.params:
            segment_start = call.params["segment_start"]
            if segment_start == recent_start:
                return _keyset_candidate_page(recent, call)
            if segment_start == oldest_start:
                return [older]
            return []
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=rows_by_id[span_id]["trace_id"],
                start_time=rows_by_id[span_id]["start_time"],
                string=(
                    "older-value" if span_id == "older-distinct" else "recent-value"
                ),
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).read_values([PROJECT_A], "final_status")

    assert read.rows == (
        AttributeValueRow(
            "recent-value", "string", ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT
        ),
    )
    assert read.metadata.query_complete is False
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_window_start == NOW - timedelta(days=7)
    recent_candidate_calls = [
        call
        for call in executor.calls
        if call.params.get("segment_start") == recent_start
    ]
    assert len(recent_candidate_calls) == 1
    assert (
        recent_candidate_calls[0].params["candidate_limit"]
        == ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1
    )
    assert "LIMIT 1 BY" not in recent_candidate_calls[0].sql


def test_dense_typed_value_sample_stops_before_json_lane():
    candidates = [
        _candidate(
            PROJECT_A,
            f"typed-{index:03d}",
            trace_id=f"trace-typed-{index:03d}",
            start_time=NOW - timedelta(hours=1, seconds=index),
        )
        for index in range(ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1)
    ]
    by_id = {str(row["id"]): row for row in candidates}

    def respond(call, _):
        if "segment_start" in call.params:
            assert "JSONHas(attributes_extra" not in call.sql
            return candidates
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=by_id[span_id]["trace_id"],
                start_time=by_id[span_id]["start_time"],
                string="Rejected",
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="arrays",
    ).read_values(
        [PROJECT_A],
        "final_status",
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )

    assert read.rows == (
        AttributeValueRow(
            "Rejected",
            "string",
            ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT,
        ),
    )
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_count == 3
    certificate_call = executor.calls[1]
    assert "max(_version) AS latest_version" in certificate_call.sql
    assert "attrs_" not in certificate_call.sql
    assert "attributes_extra" not in certificate_call.sql


def test_filter_value_cursor_page_is_newest_first_and_dedupes_across_pages():
    candidates = [
        _candidate(
            PROJECT_A,
            span_id,
            trace_id=f"trace-{span_id}",
            start_time=NOW - timedelta(minutes=index + 1),
        )
        for index, span_id in enumerate(("new-a", "new-a-2", "new-b", "old-c"))
    ]
    value_by_id = {
        "new-a": "completed",
        "new-a-2": "completed",
        "new-b": "failed",
        "old-c": "queued",
    }
    by_id = {str(row["id"]): row for row in candidates}

    def respond(call, _):
        if "segment_start" in call.params:
            in_segment = [
                row
                for row in candidates
                if call.params["segment_start"]
                <= row["start_time"]
                < call.params["segment_end"]
            ]
            return _keyset_candidate_page(in_segment, call)
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=by_id[span_id]["trace_id"],
                start_time=by_id[span_id]["start_time"],
                string=value_by_id[span_id],
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    first = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=2,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    assert [row.value for row in first.rows] == ["completed", "failed"]
    assert first.metadata.query_complete is True
    assert first.metadata.query_status == "complete"
    assert first.metadata.query_error_code is None
    assert first.has_more is True
    assert first.next_before_identity == (
        PROJECT_A,
        "trace-new-b",
        "new-b",
        NOW - timedelta(minutes=3),
    )

    second_executor = RecordingExecutor(respond)
    second = AttributeReadSelector(
        second_executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=2,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_end=first.next_segment_end,
        before_identity=first.next_before_identity,
        seen_value_digests=first.seen_value_digests,
    )

    assert [row.value for row in second.rows] == ["queued"]
    assert "attribute_version_ceiling" not in second_executor.calls[0].params
    assert all(row.value != "completed" for row in second.rows)


def test_filter_value_cursor_pushes_key_and_search_into_candidate_query():
    executor = RecordingExecutor()

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="Rechazado",
        # No type pin means a historically mixed key remains queryable across
        # every typed Map and structured JSON overflow representation.
        attribute_type=None,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
    )

    assert read.rows == ()
    assert read.has_more is False
    assert read.browse_status == "exhausted"
    assert 1 < len(executor.calls) < ATTRIBUTE_READ_MAX_QUERY_COUNT
    candidate = executor.calls[0]
    assert "AND (1)" not in candidate.sql
    assert "mapContains(attrs_string, %(attribute_key)s)" in candidate.sql
    assert "mapContains(attrs_number, %(attribute_key)s)" in candidate.sql
    assert "mapContains(attrs_bool, %(attribute_key)s)" in candidate.sql
    assert "JSONHas(attributes_extra, %(attribute_key)s)" in candidate.sql
    assert "positionCaseInsensitiveUTF8" in candidate.sql
    assert candidate.params["attribute_key"] == "final_status"
    assert candidate.params["attribute_search"] == "Rechazado"


def test_filter_value_cursor_search_yields_verified_prefix_without_filling_page():
    candidates = [
        _candidate(
            PROJECT_A,
            f"match-{index}",
            start_time=NOW - timedelta(seconds=index + 1),
        )
        for index in range(3)
    ]
    by_id = {str(row["id"]): row for row in candidates}

    def respond(call, _):
        if "segment_start" in call.params:
            return _keyset_candidate_page(
                [
                    row
                    for row in candidates
                    if call.params["segment_start"]
                    <= row["start_time"]
                    < call.params["segment_end"]
                ],
                call,
            )
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=by_id[span_id]["trace_id"],
                start_time=by_id[span_id]["start_time"],
                string="Rechazado",
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="rechazado",
        attribute_type="string",
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert read.rows == (AttributeValueRow("Rechazado", "string", 3),)
    assert read.metadata.query_complete is True
    assert read.metadata.query_status == "complete"
    assert read.has_more is True
    assert read.browse_status == "continuation"
    assert NOW - timedelta(days=365) < read.next_segment_end < NOW
    assert read.next_segment_start is not None
    assert read.next_before_identity is None
    assert len(executor.calls) < ATTRIBUTE_READ_MAX_QUERY_COUNT
    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    certificate_call = next(
        call for call in executor.calls if "max(_version) AS latest_version" in call.sql
    )
    assert "toUInt64(_version) AS candidate_version" in candidate_call.sql
    assert "attributes_extra" not in certificate_call.sql

    terminal = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="rechazado",
        attribute_type="string",
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        segment_end=read.next_segment_end,
        segment_start=read.next_segment_start,
        seen_value_digests=read.seen_value_digests,
    )
    assert terminal.rows == ()
    assert terminal.has_more is False
    assert terminal.next_segment_end == NOW - timedelta(days=365)


def test_filter_value_cursor_pinned_string_avoids_wide_json_replay():
    candidate = _candidate(
        PROJECT_A,
        "transcript-role",
        trace_id="trace-transcript-role",
        start_time=NOW - timedelta(minutes=1),
        candidate_version=7,
    )

    def respond(call, _):
        if "attributes_extra" in call.sql:
            return ServerException("wide replay crossed the byte budget", code=307)
        if "segment_start" in call.params:
            return (
                [candidate]
                if call.params["segment_start"]
                <= candidate["start_time"]
                < call.params["segment_end"]
                else []
            )
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    "transcript-role",
                    trace_id="trace-transcript-role",
                    start_time=candidate["start_time"],
                    latest_version=7,
                )
            ]
        return [
            _target_row(
                PROJECT_A,
                "transcript-role",
                trace_id="trace-transcript-role",
                start_time=candidate["start_time"],
                string="assistant",
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "conversation.transcript.16.message.role",
        page_size=10,
        attribute_type="string",
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
    )

    assert read.rows == (AttributeValueRow("assistant", "string", 1),)
    assert read.metadata.query_complete is True
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    certificate_calls = [
        call for call in executor.calls if "max(_version) AS latest_version" in call.sql
    ]
    hydration_calls = [
        call
        for call in executor.calls
        if "segment_start" not in call.params
        and "max(_version) AS latest_version" not in call.sql
    ]
    assert (
        candidate_calls[0].params["segment_end"]
        - candidate_calls[0].params["segment_start"]
        == ATTRIBUTE_VALUE_CURSOR_INITIAL_SEGMENT
    )
    assert len(certificate_calls) == 1
    assert len(hydration_calls) == 1
    candidate_call = next(
        call
        for call in candidate_calls
        if call.params["segment_start"] <= candidate["start_time"]
    )
    certificate_call = certificate_calls[0]
    hydration_call = hydration_calls[0]
    assert "toUInt64(_version) AS candidate_version" in candidate_call.sql
    assert "max(_version) AS latest_version" in certificate_call.sql
    assert "attrs_" not in certificate_call.sql
    assert "attributes_extra" not in hydration_call.sql
    assert hydration_call.params["candidate_ids_0"] == ("transcript-role",)


def test_filter_value_cursor_pinned_string_does_not_hydrate_stale_candidate():
    candidate = _candidate(
        PROJECT_A,
        "cleared-transcript-role",
        trace_id="trace-cleared-transcript-role",
        start_time=NOW - timedelta(minutes=1),
        candidate_version=3,
    )

    def respond(call, _):
        if "segment_start" in call.params:
            return (
                [candidate]
                if call.params["segment_start"]
                <= candidate["start_time"]
                < call.params["segment_end"]
                else []
            )
        assert "max(_version) AS latest_version" in call.sql
        return [
            _target_row(
                PROJECT_A,
                "cleared-transcript-role",
                trace_id="trace-cleared-transcript-role",
                start_time=candidate["start_time"],
                latest_version=4,
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "conversation.transcript.16.message.role",
        page_size=10,
        attribute_type="string",
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
    )

    assert read.rows == ()
    assert read.has_more is False
    assert read.browse_status == "exhausted"
    certificate_calls = [
        call for call in executor.calls if "max(_version) AS latest_version" in call.sql
    ]
    hydration_calls = [
        call
        for call in executor.calls
        if "segment_start" not in call.params
        and "max(_version) AS latest_version" not in call.sql
    ]
    assert len(certificate_calls) == 1
    assert hydration_calls == []
    assert any(
        "toUInt64(_version) AS candidate_version" in call.sql for call in executor.calls
    )


def test_filter_value_cursor_unpinned_nested_string_avoids_wide_json_replay():
    candidate = _candidate(
        PROJECT_A,
        "unpinned-transcript-role",
        trace_id="trace-unpinned-transcript-role",
        start_time=NOW - timedelta(minutes=1),
        candidate_version=7,
    )

    def respond(call, _):
        # This is the pre-fix combined Map + JSON argMax hydration that crossed
        # the production memory ceiling. The mixed cursor must never issue it.
        if "tupleElement(latest_state, 13) AS legacy_value_raw" in call.sql:
            return ServerException("wide replay crossed the byte budget", code=307)
        if "segment_start" in call.params:
            return (
                [candidate]
                if call.params["segment_start"]
                <= candidate["start_time"]
                < call.params["segment_end"]
                else []
            )
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    candidate["id"],
                    trace_id=candidate["trace_id"],
                    start_time=candidate["start_time"],
                    latest_version=7,
                )
            ]
        assert "attributes_extra" not in call.sql
        return [
            _target_row(
                PROJECT_A,
                candidate["id"],
                trace_id=candidate["trace_id"],
                start_time=candidate["start_time"],
                string="assistant",
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "conversation.transcript.16.message.role",
        page_size=10,
        attribute_type=None,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
    )

    assert read.rows == (AttributeValueRow("assistant", "string", 1),)
    assert read.metadata.query_complete is True
    assert read.browse_status == "exhausted"
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    certificate_calls = [
        call for call in executor.calls if "max(_version) AS latest_version" in call.sql
    ]
    hydration_calls = [
        call
        for call in executor.calls
        if "segment_start" not in call.params
        and "max(_version) AS latest_version" not in call.sql
    ]
    assert len(certificate_calls) == 1
    assert len(hydration_calls) == 1
    candidate_call = next(
        call
        for call in candidate_calls
        if call.params["segment_start"] <= candidate["start_time"]
    )
    certificate_call = certificate_calls[0]
    typed_hydration_call = hydration_calls[0]
    assert "toUInt64(_version) AS candidate_version" in candidate_call.sql
    assert "max(_version) AS latest_version" in certificate_call.sql
    assert "attrs_" not in certificate_call.sql
    assert "attributes_extra" not in certificate_call.sql
    assert "attributes_extra" not in typed_hydration_call.sql


def test_filter_value_cursor_unpinned_recuts_baseline_before_exact_hydration():
    candidate = _candidate(
        PROJECT_A,
        "whatfix-style-value",
        trace_id="trace-whatfix-style-value",
        start_time=NOW - timedelta(seconds=1),
        candidate_version=7,
    )
    candidate_calls = []

    def respond(call, _):
        if "segment_start" in call.params:
            candidate_calls.append(call)
            width = call.params["segment_end"] - call.params["segment_start"]
            if width == ATTRIBUTE_READ_EXPLICIT_SEGMENT:
                assert call.timeout_ms == ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS
                return ReadDeadlineExceeded("dense unpinned baseline")
            assert width == ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
            assert call.timeout_ms == ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS
            return [candidate]
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    candidate["id"],
                    trace_id=candidate["trace_id"],
                    start_time=candidate["start_time"],
                    latest_version=candidate["candidate_version"],
                )
            ]
        assert "attributes_extra" not in call.sql
        return [
            _target_row(
                PROJECT_A,
                candidate["id"],
                trace_id=candidate["trace_id"],
                start_time=candidate["start_time"],
                string="verified-value",
            )
        ]

    read = AttributeReadSelector(
        RecordingExecutor(respond),
        now=NOW,
        json_attribute_mode="arrays",
    ).read_value_cursor_page(
        [PROJECT_A],
        "whatfix.ent_id",
        page_size=1,
        attribute_type=None,
        window_start=NOW - ATTRIBUTE_READ_EXPLICIT_SEGMENT,
        window_end=NOW,
        segment_start=NOW - ATTRIBUTE_READ_EXPLICIT_SEGMENT,
    )

    assert [call.timeout_ms for call in candidate_calls] == [
        ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS,
        ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS,
    ]
    assert [call.params["segment_end"] for call in candidate_calls] == [NOW, NOW]
    assert read.rows == (AttributeValueRow("verified-value", "string", 1),)


def test_filter_value_cursor_unpinned_splits_mixed_typed_and_json_latest_state():
    candidates = [
        _candidate(
            PROJECT_A,
            span_id,
            trace_id=f"trace-{span_id}",
            start_time=NOW - timedelta(minutes=index + 1),
            candidate_version=version,
        )
        for index, (span_id, version) in enumerate(
            (
                ("typed-string", 7),
                ("json-array", 8),
                ("typed-priority", 9),
                ("cleared-later", 3),
                ("tombstoned", 4),
            )
        )
    ]
    by_id = {str(row["id"]): row for row in candidates}

    def row_for(span_id, **kwargs):
        candidate = by_id[span_id]
        return _target_row(
            PROJECT_A,
            span_id,
            trace_id=candidate["trace_id"],
            start_time=candidate["start_time"],
            **kwargs,
        )

    def respond(call, _):
        if "segment_start" in call.params:
            in_segment = [
                candidate
                for candidate in candidates
                if call.params["segment_start"]
                <= candidate["start_time"]
                < call.params["segment_end"]
            ]
            return _keyset_candidate_page(in_segment, call)
        requested_ids = call.params["candidate_ids_0"]
        if "max(_version) AS latest_version" in call.sql:
            latest = {
                "typed-string": (7, 0),
                "json-array": (8, 0),
                "typed-priority": (9, 0),
                "cleared-later": (4, 0),
                "tombstoned": (4, 1),
            }
            return [
                row_for(
                    span_id,
                    latest_version=latest[span_id][0],
                    is_deleted=latest[span_id][1],
                )
                # GROUP BY result order is unspecified. Reverse it to prove
                # candidate/cursor order is restored before dedupe.
                for span_id in reversed(requested_ids)
            ]
        if "legacy_value_raw" in call.sql:
            return [
                row_for(
                    "json-array",
                    legacy_raw=json.dumps(["assistant", "user"]),
                )
                for span_id in requested_ids
                if span_id == "json-array"
            ]
        rows = {
            # A typed key has priority over a duplicate JSON representation,
            # so this identity must not enter the JSON hydration lane.
            "typed-priority": row_for("typed-priority", number=42),
            "json-array": row_for("json-array"),
            "typed-string": row_for("typed-string", string="completed"),
        }
        return [rows[span_id] for span_id in requested_ids if span_id in rows]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "mixed.attribute",
        page_size=10,
        attribute_type=None,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
    )

    assert read.rows == (
        AttributeValueRow("completed", "string", 1),
        AttributeValueRow("assistant", "array", 1),
        AttributeValueRow("user", "array", 1),
        AttributeValueRow(42.0, "number", 1),
    )
    assert read.metadata.query_complete is True
    assert read.browse_status == "exhausted"
    assert len(executor.calls) > 4
    json_hydration_call = next(
        call
        for call in executor.calls
        if "tupleElement(latest_state, 3) AS legacy_value_raw" in call.sql
    )
    assert "AS legacy_value_fingerprint" in json_hydration_call.sql
    assert all(
        "tupleElement(latest_state, 13) AS legacy_value_raw" not in call.sql
        for call in executor.calls
    )


def test_filter_value_cursor_crosses_many_empty_slices_to_reach_old_value():
    old_time = NOW - timedelta(days=60)
    candidate = _candidate(
        PROJECT_A,
        "old-value",
        trace_id="trace-old-value",
        start_time=old_time,
    )

    def respond(call, _):
        if "segment_start" in call.params:
            rows = (
                [candidate]
                if call.params["segment_start"] <= old_time < call.params["segment_end"]
                else []
            )
            return _keyset_candidate_page(rows, call)
        return [
            _target_row(
                PROJECT_A,
                "old-value",
                trace_id="trace-old-value",
                start_time=old_time,
                string="retained-value",
            )
        ]

    executor = RecordingExecutor(respond)
    cursor_args = {}
    pages = []
    for _ in range(10):
        read = AttributeReadSelector(
            executor, now=NOW, json_attribute_mode="arrays"
        ).read_value_cursor_page(
            [PROJECT_A],
            "mastra.span.type",
            page_size=10,
            window_start=NOW - timedelta(days=365),
            window_end=NOW,
            **cursor_args,
        )
        pages.append(read)
        if not read.has_more:
            break
        cursor_args = {
            "segment_end": read.next_segment_end,
            "segment_start": read.next_segment_start,
            "before_identity": read.next_before_identity,
            "resume_identity": read.next_resume_identity,
            "resume_member_offset": read.next_resume_member_offset,
            "seen_value_digests": read.seen_value_digests,
            "seen_value_count": read.seen_value_count,
        }
    else:
        pytest.fail("sparse value cursor did not exhaust its retained window")

    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    widths = [
        call.params["segment_end"] - call.params["segment_start"]
        for call in candidate_calls
    ]
    assert tuple(row for page in pages for row in page.rows) == (
        AttributeValueRow("retained-value", "string", 1),
    )
    assert read.has_more is False
    assert read.browse_status == "exhausted"
    assert max(widths) >= timedelta(days=32)
    assert any(
        call.timeout_ms == ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS
        for call in candidate_calls
    )
    # Sparse history grows geometrically inside each bounded request. Even when
    # one request reaches the old value before it can exhaust the remaining
    # year, its exact continuation keeps every retained value reachable.
    assert len(pages) < 10
    assert len(candidate_calls) < ATTRIBUTE_READ_MAX_QUERY_COUNT * len(pages)


def test_filter_value_cursor_empty_retained_window_terminates_without_loop():
    executor = RecordingExecutor()
    retained_start = NOW - timedelta(days=365)

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "missing.attribute",
        page_size=10,
        window_start=retained_start,
        window_end=NOW,
    )

    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert read.rows == ()
    assert read.has_more is False
    assert read.browse_status == "exhausted"
    assert read.next_segment_end == retained_start
    assert read.next_before_identity is None
    assert read.next_resume_identity is None
    assert len(candidate_calls) < ATTRIBUTE_READ_MAX_QUERY_COUNT
    assert all(
        newer.params["segment_start"] == older.params["segment_end"]
        for newer, older in zip(candidate_calls, candidate_calls[1:], strict=False)
    )


@pytest.mark.parametrize("horizon_days", (7, 30, 365))
def test_filter_value_cursor_searched_absence_returns_advancing_continuation(
    horizon_days,
):
    horizon = timedelta(days=horizon_days)
    executor = RecordingExecutor(distinct_responder=lambda *_args: [])

    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="value-that-does-not-exist",
        attribute_type="string",
        window_start=NOW - horizon,
        window_end=NOW,
    )

    proof_calls = [call for call in executor.calls if "distinct_limit" in call.params]
    widths = tuple(
        timedelta(
            microseconds=(
                call.params["segment_end_us"] - call.params["segment_start_us"]
            )
        )
        for call in proof_calls
    )
    expected_widths = _geometric_slice_widths(
        horizon,
        initial=ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        maximum=ATTRIBUTE_VALUE_CURSOR_MAX_EMPTY_SEGMENT,
    )[:6]
    expected_segment_end = NOW - sum(expected_widths, timedelta())
    assert widths == expected_widths
    assert read.rows == ()
    assert read.browse_status == "continuation"
    assert read.has_more is True
    assert read.next_segment_end == expected_segment_end
    assert read.next_segment_start == expected_segment_end - expected_widths[-1]
    assert read.metadata.query_count == len(proof_calls) == 6
    assert read.metadata.query_count < ATTRIBUTE_READ_MAX_QUERY_COUNT
    assert all(
        call.timeout_ms == ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS
        for call in proof_calls
    )
    assert not any("segment_start" in call.params for call in executor.calls)


def test_filter_value_cursor_search_proof_cap_stops_before_density_cliff():
    class ManualClock:
        value = 100.0

        def __call__(self):
            return self.value

    clock = ManualClock()
    # Sanitized production boundary: 160 s completed in 438 ms, 320 s in
    # 659 ms, and the immediately adjacent doubled 640 s proof timed out at
    # the former 750 ms ceiling. The selector must learn from the successful
    # 320 s statement instead of issuing that timeout as density control flow.
    observed_query_times_ms = {
        5: 233.245,
        10: 266.528,
        20: 276.246,
        40: 280.907,
        80: 376.221,
        160: 437.890,
        320: 658.726,
    }

    def distinct_respond(call, _call_number):
        width_us = call.params["segment_end_us"] - call.params["segment_start_us"]
        width_seconds = width_us // 1_000_000
        if width_seconds > 320:
            pytest.fail("successful proof telemetry must prevent the 640 s timeout")
        query_time_ms = observed_query_times_ms[width_seconds]
        clock.value += query_time_ms / 1_000
        return AttributeQueryPage(data=[], query_time_ms=query_time_ms)

    executor = RecordingExecutor(distinct_responder=distinct_respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        wall_timeout_ms=6_000,
        clock=clock,
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="value-that-does-not-exist",
        attribute_type="string",
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
    )

    proof_calls = [call for call in executor.calls if "distinct_limit" in call.params]
    widths = [
        timedelta(
            microseconds=(
                call.params["segment_end_us"] - call.params["segment_start_us"]
            )
        )
        for call in proof_calls
    ]
    assert ATTRIBUTE_VALUE_CURSOR_DISTINCT_GROWTH_QUERY_TIME_MS == 500
    expected_widths = [
        timedelta(seconds=5),
        timedelta(seconds=10),
        timedelta(seconds=20),
        timedelta(seconds=40),
        timedelta(seconds=80),
        timedelta(seconds=160),
    ]
    assert widths == expected_widths
    assert len(widths) == ATTRIBUTE_VALUE_CURSOR_MAX_SEARCH_PROOFS
    assert all(
        newer.params["segment_start_us"] == older.params["segment_end_us"]
        for newer, older in zip(proof_calls, proof_calls[1:], strict=False)
    )
    assert all(
        call.timeout_ms == ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS
        for call in proof_calls
    )
    assert read.rows == ()
    assert read.has_more is True
    assert read.next_segment_end == NOW - sum(expected_widths, timedelta())
    assert read.next_segment_start == read.next_segment_end - expected_widths[-1]
    assert read.metadata.query_count == len(proof_calls) == 6
    assert not any("segment_start" in call.params for call in executor.calls)


@pytest.mark.parametrize(
    ("incoming_width", "query_time_ms", "read_rows", "read_bytes"),
    (
        (320, 584.935, 148_494, 595_674_646),
        (160, 503.092, 111_456, 439_934_579),
    ),
)
def test_filter_value_cursor_resource_telemetry_sizes_below_adjacent_byte_cliff(
    incoming_width,
    query_time_ms,
    read_rows,
    read_bytes,
):
    # Exact successful statements immediately before the two production byte
    # cliffs. A continuation can carry either learned width across a deploy;
    # size its next adjacent proof to <=25% projected cap utilization instead
    # of learning the density jump from TOO_MANY_BYTES.
    expected_next_width = 80

    def distinct_respond(call, call_number):
        width_us = call.params["segment_end_us"] - call.params["segment_start_us"]
        if call_number == 1:
            assert width_us == incoming_width * 1_000_000
            return AttributeQueryPage(
                data=[],
                query_time_ms=query_time_ms,
                read_rows=read_rows,
                read_bytes=read_bytes,
            )
        assert call_number == 2
        assert width_us == expected_next_width * 1_000_000
        return AttributeQueryPage(
            data=[],
            query_time_ms=200.0,
            read_rows=20_000,
            read_bytes=80_000_000,
        )

    executor = RecordingExecutor(distinct_responder=distinct_respond)
    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="value-that-does-not-exist",
        attribute_type="string",
        window_start=NOW - timedelta(seconds=incoming_width + expected_next_width),
        window_end=NOW,
        segment_start=NOW - timedelta(seconds=incoming_width),
    )

    proof_calls = [call for call in executor.calls if "distinct_limit" in call.params]
    widths = [
        (call.params["segment_end_us"] - call.params["segment_start_us"]) // 1_000_000
        for call in proof_calls
    ]
    assert ATTRIBUTE_VALUE_CURSOR_DISTINCT_RESOURCE_TARGET_FRACTION == 0.25
    assert widths == [incoming_width, expected_next_width]
    assert (
        proof_calls[0].params["segment_start_us"]
        == proof_calls[1].params["segment_end_us"]
    )
    assert proof_calls[0].settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
    assert "max_rows_to_read" not in proof_calls[0].settings
    assert read.rows == ()
    assert read.browse_status == "exhausted"
    assert read.has_more is False
    assert read.next_segment_end == NOW - timedelta(
        seconds=incoming_width + expected_next_width
    )
    assert read.metadata.query_count == len(proof_calls) == 2
    assert not any("segment_start" in call.params for call in executor.calls)


def test_filter_value_cursor_searched_old_number_survives_bounded_continuations():
    old_time = NOW - timedelta(days=300)
    candidate = _candidate(
        PROJECT_A,
        "old-number",
        trace_id="trace-old-number",
        start_time=old_time,
        candidate_version=7,
    )

    def respond(call, _call_number):
        if "segment_start" in call.params:
            rows = (
                [candidate]
                if call.params["segment_start"] <= old_time < call.params["segment_end"]
                else []
            )
            return _keyset_candidate_page(rows, call)
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    candidate["id"],
                    trace_id=candidate["trace_id"],
                    start_time=old_time,
                    latest_version=7,
                )
            ]
        return [
            _target_row(
                PROJECT_A,
                candidate["id"],
                trace_id=candidate["trace_id"],
                start_time=old_time,
                number=42,
            )
        ]

    def distinct_respond(call, _call_number):
        old_time_us = _unix_microseconds(old_time)
        value = (
            42
            if call.params["segment_start_us"]
            <= old_time_us
            < call.params["segment_end_us"]
            else 7
        )
        return [_distinct_value_group("number", value)]

    executor = RecordingExecutor(respond, distinct_responder=distinct_respond)
    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "numeric.attribute",
        page_size=10,
        search="42",
        attribute_type="number",
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert read.rows == ()
    assert read.browse_status == "continuation"
    assert read.has_more is True
    assert read.metadata.query_count < ATTRIBUTE_READ_MAX_QUERY_COUNT
    assert all("distinct_limit" in call.params for call in executor.calls)
    assert all("attrs_string" not in call.sql for call in executor.calls)

    candidate_calls = []
    for _ in range(20):
        continuation_executor = RecordingExecutor(
            respond,
            distinct_responder=distinct_respond,
        )
        read = AttributeReadSelector(
            continuation_executor,
            now=NOW,
        ).read_value_cursor_page(
            [PROJECT_A],
            "numeric.attribute",
            page_size=10,
            search="42",
            attribute_type="number",
            window_start=NOW - timedelta(days=365),
            window_end=NOW,
            segment_end=read.next_segment_end,
            segment_start=read.next_segment_start,
            seen_value_digests=read.seen_value_digests,
        )
        candidate_calls.extend(
            call
            for call in continuation_executor.calls
            if "segment_start" in call.params
        )
        if read.rows:
            break

    assert read.rows == (AttributeValueRow(42.0, "number", 1),)
    assert read.metadata.query_count < ATTRIBUTE_READ_MAX_QUERY_COUNT
    assert all("attrs_string" not in call.sql for call in candidate_calls)
    assert any(call.params["segment_start"] <= old_time for call in candidate_calls)


def test_filter_value_cursor_hot_failure_does_not_pin_sparse_history_to_floor():
    attempts: list[tuple[datetime, datetime]] = []
    successful: list[tuple[datetime, datetime]] = []
    failed_end = NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT

    def respond(call, _call_number):
        if "segment_start" not in call.params:
            pytest.fail("empty searched slices must not enter latest-state replay")
        segment = (call.params["segment_start"], call.params["segment_end"])
        attempts.append(segment)
        width = segment[1] - segment[0]
        if width == ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT * 2 and segment[1] == failed_end:
            return ReadDeadlineExceeded("hot recent adaptive slice exceeded budget")
        successful.append(segment)
        return []

    read = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="missing",
        attribute_type="string",
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )

    widths = tuple(end - start for start, end in attempts)
    assert widths[:4] == (
        ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT * 2,
        ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT * 2,
    )
    assert max(widths) > timedelta(days=1)
    assert all(
        older_end == newer_start
        for (newer_start, _newer_end), (_older_start, older_end) in zip(
            successful, successful[1:], strict=False
        )
    )
    assert read.rows == ()
    assert read.has_more is False
    assert read.next_segment_end == NOW - timedelta(days=7)
    assert read.metadata.query_count < ATTRIBUTE_READ_MAX_QUERY_COUNT


def test_filter_value_cursor_page_n_reuses_signed_adaptive_width(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads.ATTRIBUTE_READ_MAX_QUERY_COUNT",
        5,
    )
    first_executor = RecordingExecutor()
    first = AttributeReadSelector(first_executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="missing",
        attribute_type="string",
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )

    assert first.rows == ()
    assert first.has_more is True
    assert first.next_segment_start is not None
    persisted_width = first.next_segment_end - first.next_segment_start
    assert persisted_width > ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT

    second_executor = RecordingExecutor()
    second = AttributeReadSelector(second_executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="missing",
        attribute_type="string",
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
        segment_end=first.next_segment_end,
        segment_start=first.next_segment_start,
        seen_value_digests=first.seen_value_digests,
    )

    second_candidate = next(
        call for call in second_executor.calls if "segment_start" in call.params
    )
    assert (
        second_candidate.params["segment_end"]
        - second_candidate.params["segment_start"]
        == persisted_width
    )
    assert second.next_segment_end < first.next_segment_end


def test_filter_value_cursor_wide_probe_failure_returns_prior_exact_progress():
    failed_wide_end = None

    def respond(call, _):
        nonlocal failed_wide_end
        if "segment_start" not in call.params:
            raise AssertionError("empty candidate pages must not hydrate")
        width = call.params["segment_end"] - call.params["segment_start"]
        if failed_wide_end is None and width > ATTRIBUTE_READ_EXPLICIT_SEGMENT:
            failed_wide_end = call.params["segment_end"]
            return ReadDeadlineExceeded("speculative wide probe exceeded budget")
        return []

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "sparse.attribute",
        page_size=10,
        window_start=NOW - timedelta(days=3),
        window_end=NOW,
    )

    failed_call = executor.calls[-1]
    assert failed_call.params["segment_end"] == failed_wide_end
    assert (
        failed_call.params["segment_end"] - failed_call.params["segment_start"]
        > ATTRIBUTE_READ_EXPLICIT_SEGMENT
    )
    assert read.next_segment_end == failed_wide_end
    assert read.has_more is True
    assert read.browse_status == "continuation"


def test_filter_value_cursor_widened_checkpoint_is_monotonic_and_unique():
    old_time = NOW - timedelta(days=40)
    candidates = [
        _candidate(
            PROJECT_A,
            span_id,
            trace_id=f"trace-{span_id}",
            start_time=old_time,
        )
        for span_id in ("z-newest", "y-older")
    ]
    value_by_id = {"z-newest": "newest", "y-older": "older"}

    def respond(call, _):
        if "segment_start" in call.params:
            in_segment = [
                row
                for row in candidates
                if call.params["segment_start"]
                <= row["start_time"]
                < call.params["segment_end"]
            ]
            return _keyset_candidate_page(in_segment, call)
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=f"trace-{span_id}",
                start_time=old_time,
                string=value_by_id[span_id],
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    first = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "sparse.attribute",
        page_size=1,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )
    second = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "sparse.attribute",
        page_size=1,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        segment_end=first.next_segment_end,
        before_identity=first.next_before_identity,
        seen_value_digests=first.seen_value_digests,
    )

    assert [row.value for row in first.rows] == ["newest"]
    assert [row.value for row in second.rows] == ["older"]
    assert first.next_segment_end == old_time + ATTRIBUTE_READ_EXPLICIT_SEGMENT
    assert second.next_segment_end == first.next_segment_end
    assert first.next_before_identity is not None
    assert second.next_before_identity is not None
    assert second.next_before_identity < first.next_before_identity
    assert set(first.seen_value_digests).isdisjoint(
        set(second.seen_value_digests[len(first.seen_value_digests) :])
    )


def test_filter_value_cursor_keeps_microsecond_rows_in_adjacent_segments():
    """A driver-coerced second boundary must not poison the next keyset page."""

    old_second = NOW - timedelta(days=40)
    candidates = [
        _candidate(
            PROJECT_A,
            span_id,
            trace_id=f"trace-{span_id}",
            start_time=old_second.replace(microsecond=microsecond),
        )
        for span_id, microsecond in (
            ("newest", 566_461),
            ("middle", 539_436),
            ("oldest", 500_000),
        )
    ]
    values = {str(row["id"]): str(row["id"]) for row in candidates}
    by_id = {str(row["id"]): row for row in candidates}

    def respond(call, _):
        if "segment_start" in call.params:
            # Model the production failure mode: an untyped datetime bound is
            # coerced to whole seconds, while an integer DateTime64 bound keeps
            # the selector's exact half-open segment.
            exact_bounds = "fromUnixTimestamp64Micro" in call.sql
            if exact_bounds:
                segment_start = datetime.fromtimestamp(
                    call.params["segment_start_us"] / 1_000_000,
                    tz=UTC,
                )
                segment_end = datetime.fromtimestamp(
                    call.params["segment_end_us"] / 1_000_000,
                    tz=UTC,
                )
            else:
                segment_start = call.params["segment_start"].replace(microsecond=0)
                segment_end = call.params["segment_end"].replace(microsecond=0)
            in_segment = [
                row
                for row in candidates
                if segment_start <= row["start_time"] < segment_end
            ]
            return _keyset_candidate_page(in_segment, call)
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=by_id[span_id]["trace_id"],
                start_time=by_id[span_id]["start_time"],
                string=values[span_id],
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    reads = []
    cursor: dict[str, Any] = {}
    for _ in range(3):
        executor = RecordingExecutor(respond)
        read = AttributeReadSelector(
            executor, now=NOW, json_attribute_mode="arrays"
        ).read_value_cursor_page(
            [PROJECT_A],
            "fi.span.kind",
            page_size=1,
            window_start=NOW - timedelta(days=365),
            window_end=NOW,
            **cursor,
        )
        candidate_calls = [
            call for call in executor.calls if "segment_start" in call.params
        ]
        assert candidate_calls
        assert all(
            "fromUnixTimestamp64Micro(%(segment_start_us)s)" in call.sql
            and "fromUnixTimestamp64Micro(%(segment_end_us)s)" in call.sql
            for call in candidate_calls
        )
        reads.append(read)
        cursor = {
            "segment_end": read.next_segment_end,
            "before_identity": read.next_before_identity,
            "resume_identity": read.next_resume_identity,
            "resume_member_offset": read.next_resume_member_offset,
            "seen_value_digests": read.seen_value_digests,
        }

    assert [[row.value for row in read.rows] for read in reads] == [
        ["newest"],
        ["middle"],
        ["oldest"],
    ]
    assert len({read.next_before_identity for read in reads}) == 3


def test_filter_value_cursor_recovers_poisoned_before_boundary_without_skip():
    checkpoint_time = (NOW - timedelta(days=40)).replace(microsecond=566_461)
    checkpoint = _candidate(
        PROJECT_A,
        "z-checkpoint",
        trace_id="trace-checkpoint",
        start_time=checkpoint_time,
    )
    same_time_lower_id = _candidate(
        PROJECT_A,
        "a-same-time",
        trace_id="trace-same-time",
        start_time=checkpoint_time,
    )
    adjacent_older = _candidate(
        PROJECT_A,
        "older-by-one-microsecond",
        trace_id="trace-adjacent-older",
        start_time=checkpoint_time - timedelta(microseconds=1),
    )
    candidates = [checkpoint, same_time_lower_id, adjacent_older]
    values = {str(row["id"]): str(row["id"]) for row in candidates}
    poisoned_segment_end = (
        checkpoint_time + ATTRIBUTE_READ_EXPLICIT_SEGMENT + timedelta(microseconds=27)
    )
    cursor = {
        "segment_end": poisoned_segment_end,
        "before_identity": (
            PROJECT_A,
            str(checkpoint["trace_id"]),
            str(checkpoint["id"]),
            checkpoint_time,
        ),
        "seen_value_digests": (
            attribute_value_cursor_digest("string", values["z-checkpoint"]),
        ),
    }

    reads = []
    for _ in range(2):
        executor = _value_cursor_executor(candidates, values)
        read = AttributeReadSelector(
            executor, now=NOW, json_attribute_mode="arrays"
        ).read_value_cursor_page(
            [PROJECT_A],
            "fi.span.kind",
            page_size=1,
            window_start=NOW - timedelta(days=365),
            window_end=NOW,
            **cursor,
        )
        reads.append(read)
        cursor = {
            "segment_end": read.next_segment_end,
            "before_identity": read.next_before_identity,
            "resume_identity": read.next_resume_identity,
            "resume_member_offset": read.next_resume_member_offset,
            "seen_value_digests": read.seen_value_digests,
        }

    assert [[row.value for row in read.rows] for read in reads] == [
        ["a-same-time"],
        ["older-by-one-microsecond"],
    ]
    assert reads[0].next_segment_end == (
        checkpoint_time + ATTRIBUTE_READ_EXPLICIT_SEGMENT
    )
    assert reads[0].next_before_identity is not None
    assert reads[0].next_before_identity[3] == checkpoint_time
    assert reads[1].next_before_identity is not None
    assert reads[1].next_before_identity[3] == (
        checkpoint_time - timedelta(microseconds=1)
    )


def test_filter_value_cursor_recovers_poisoned_resume_array_boundary():
    checkpoint_time = (NOW - timedelta(days=40)).replace(microsecond=566_461)
    resume_identity = (
        PROJECT_A,
        "trace-resume",
        "z-resume-array",
        checkpoint_time,
    )
    same_time_lower_id = _candidate(
        PROJECT_A,
        "a-same-time-after-array",
        trace_id="trace-same-time-after-array",
        start_time=checkpoint_time,
    )
    by_id = {str(same_time_lower_id["id"]): same_time_lower_id}

    def respond(call, _):
        if "segment_start" in call.params:
            in_segment = (
                [same_time_lower_id]
                if call.params["segment_start"]
                <= same_time_lower_id["start_time"]
                < call.params["segment_end"]
                else []
            )
            return _keyset_candidate_page(in_segment, call)
        rows = []
        for span_id in call.params["candidate_ids_0"]:
            if span_id == resume_identity[2]:
                rows.append(
                    _target_row(
                        PROJECT_A,
                        span_id,
                        trace_id=resume_identity[1],
                        start_time=checkpoint_time,
                        legacy_raw=json.dumps(
                            ("prior", "remaining-first", "remaining-second")
                        ),
                    )
                )
            else:
                candidate = by_id[span_id]
                rows.append(
                    _target_row(
                        PROJECT_A,
                        span_id,
                        trace_id=str(candidate["trace_id"]),
                        start_time=candidate["start_time"],
                        string=span_id,
                    )
                )
        return rows

    poisoned_segment_end = (
        checkpoint_time + ATTRIBUTE_READ_EXPLICIT_SEGMENT + timedelta(microseconds=27)
    )
    first = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "fi.span.kind",
        page_size=1,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        segment_end=poisoned_segment_end,
        resume_identity=resume_identity,
        resume_member_offset=1,
        seen_value_digests=(attribute_value_cursor_digest("array", "prior"),),
    )
    second = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "fi.span.kind",
        page_size=1,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        segment_end=first.next_segment_end,
        before_identity=first.next_before_identity,
        resume_identity=first.next_resume_identity,
        resume_member_offset=first.next_resume_member_offset,
        seen_value_digests=first.seen_value_digests,
    )
    third = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "fi.span.kind",
        page_size=1,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        segment_end=second.next_segment_end,
        before_identity=second.next_before_identity,
        resume_identity=second.next_resume_identity,
        resume_member_offset=second.next_resume_member_offset,
        seen_value_digests=second.seen_value_digests,
    )

    assert [row.value for row in first.rows] == ["remaining-first"]
    assert first.next_segment_end == checkpoint_time + ATTRIBUTE_READ_EXPLICIT_SEGMENT
    assert first.next_resume_identity == resume_identity
    assert first.next_resume_member_offset > ATTRIBUTE_READ_MAX_VALUES + 1
    assert first.next_before_identity is None
    assert [row.value for row in second.rows] == ["remaining-second"]
    assert second.next_resume_identity is None
    assert second.next_before_identity == resume_identity
    assert [row.value for row in third.rows] == ["a-same-time-after-array"]
    assert third.next_before_identity is not None
    assert third.next_before_identity[3] == checkpoint_time


@pytest.mark.parametrize("failure_stage", ["candidate", "replay"])
def test_filter_value_cursor_expanded_failure_keeps_widened_checkpoint_compressed(
    failure_stage,
):
    old_time = NOW - timedelta(days=40)
    duplicate_value = "completed"
    candidates = [
        _candidate(
            PROJECT_A,
            f"historical-{index:03d}",
            start_time=old_time,
        )
        for index in range(200)
    ]
    executor = _value_cursor_executor(
        candidates,
        {str(row["id"]): duplicate_value for row in candidates},
        **{f"fail_{failure_stage}_limit": 2 * ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT},
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "sparse.attribute",
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", duplicate_value),),
    )

    ordered = sorted(candidates, key=_candidate_key, reverse=True)
    checkpoint_row = ordered[ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT - 1]
    checkpoint = (
        PROJECT_A,
        str(checkpoint_row["trace_id"]),
        str(checkpoint_row["id"]),
        old_time,
    )
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    successful_wide, failed_expansion = candidate_calls[-2:]
    assert (
        successful_wide.params["segment_end"] - successful_wide.params["segment_start"]
        > ATTRIBUTE_READ_EXPLICIT_SEGMENT
    )
    assert failed_expansion.params["candidate_limit"] - 1 == 128
    assert failed_expansion.params["candidate_before_id"] == checkpoint[2]
    assert failed_expansion.params["segment_start"] == old_time
    assert failed_expansion.params["segment_end"] == old_time + (
        ATTRIBUTE_READ_EXPLICIT_SEGMENT
    )
    assert failed_expansion.timeout_ms == ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS
    replay_sizes = [
        len(call.params["candidate_ids_0"])
        for call in executor.calls
        if "candidate_ids_0" in call.params
    ]
    assert replay_sizes == ([64, 64] if failure_stage == "candidate" else [64, 64, 128])
    assert (
        executor.calls[-1].timeout_ms == ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS
    )
    assert read.rows == ()
    assert read.has_more is True
    assert read.next_before_identity == checkpoint
    assert read.next_segment_end == old_time + ATTRIBUTE_READ_EXPLICIT_SEGMENT
    assert (
        read.next_segment_end - read.next_before_identity[3]
        == ATTRIBUTE_READ_EXPLICIT_SEGMENT
    )


def test_filter_value_cursor_page_caps_each_request_and_publishes_continuation():
    candidates = [
        _candidate(
            PROJECT_A,
            f"span-{index:03d}",
            start_time=NOW - timedelta(seconds=index + 1),
        )
        for index in range(
            ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
            * ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_PAGES
            + 1
        )
    ]
    by_id = {str(row["id"]): row for row in candidates}

    def respond(call, _):
        if "segment_start" in call.params:
            in_segment = [
                row
                for row in candidates
                if call.params["segment_start"]
                <= row["start_time"]
                < call.params["segment_end"]
            ]
            return _keyset_candidate_page(in_segment, call)
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=by_id[span_id]["trace_id"],
                start_time=by_id[span_id]["start_time"],
                string="completed",
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    # The new density-safe 5/10/20/40-second slices consume 75 physical rows in
    # four exact batches. A later page resumes below that checkpoint; no
    # duplicate-heavy request needs the former six-hour first statement.
    consumed_count = 75
    assert read.rows == (AttributeValueRow("completed", "string", consumed_count),)
    assert read.metadata.query_complete is True
    assert read.metadata.query_status == "complete"
    assert read.metadata.query_error_code is None
    assert read.metadata.query_count == (3 * ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_PAGES)
    assert len(executor.calls) == 3 * ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_PAGES
    assert read.has_more is True
    assert read.next_before_identity is None
    assert read.next_segment_end == candidates[consumed_count - 1]["start_time"]
    assert read.next_segment_start < read.next_segment_end
    assert read.seen_value_digests == (
        attribute_value_cursor_digest("string", "completed"),
    )


def test_filter_value_cursor_oversamples_typed_dense_values_on_first_batch():
    candidates = [
        _candidate(
            PROJECT_A,
            f"dense-{index:04d}",
            start_time=NOW - timedelta(microseconds=index + 1),
        )
        for index in range(600)
    ]
    # Model the production voice-call distribution: many adjacent spans share
    # one call id, so the old 64-identity prefix yielded only a handful of
    # values even though acquiring it scanned the whole six-hour segment.
    values = {
        str(row["id"]): f"call-{index // 40:02d}"
        for index, row in enumerate(candidates)
    }
    executor = _value_cursor_executor(candidates, values)

    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "call_id",
        page_size=10,
        attribute_type="string",
        window_start=NOW - timedelta(hours=6),
        window_end=NOW,
    )

    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert (
        candidate_call.params["candidate_limit"] - 1
        == ATTRIBUTE_VALUE_CURSOR_DENSE_CANDIDATE_LIMIT
    )
    assert candidate_call.timeout_ms > ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS
    assert len(read.rows) == 10
    assert len({row.value for row in read.rows}) == 10
    assert read.has_more is True


def test_filter_value_cursor_sparse_typed_key_starts_with_conservative_batch():
    candidates = [
        _candidate(
            PROJECT_A,
            f"sparse-{index:04d}",
            start_time=NOW - timedelta(microseconds=index + 1),
        )
        for index in range(100)
    ]
    executor = _value_cursor_executor(
        candidates,
        {str(row["id"]): "collector-ended-call" for row in candidates},
    )

    AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "ended_reason",
        page_size=10,
        attribute_type="string",
        window_start=NOW - timedelta(hours=6),
        window_end=NOW,
    )

    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert (
        candidate_call.params["candidate_limit"] - 1
        == ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
    )


def test_filter_value_cursor_duplicate_only_page_reaches_older_unique_value():
    duplicate_value = "completed"
    unique_value = "older-unique"
    candidates = [
        _candidate(
            PROJECT_A,
            f"duplicate-{index:04d}",
            start_time=NOW - timedelta(seconds=index + 1),
        )
        for index in range(1_000)
    ]
    candidates.append(
        _candidate(
            PROJECT_A,
            "older-unique",
            start_time=NOW - timedelta(seconds=1_001),
        )
    )
    value_by_id = {
        str(row["id"]): (
            unique_value if row["id"] == "older-unique" else duplicate_value
        )
        for row in candidates
    }
    executor = _value_cursor_executor(candidates, value_by_id)
    duplicate_digest = attribute_value_cursor_digest("string", duplicate_value)

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        seen_value_digests=(duplicate_digest,),
    )

    candidate_limits = [
        call.params["candidate_limit"] - 1
        for call in executor.calls
        if "segment_start" in call.params
    ]
    assert read.rows == (AttributeValueRow(unique_value, "string", 1),)
    assert read.seen_value_digests == (
        duplicate_digest,
        attribute_value_cursor_digest("string", unique_value),
    )
    assert read.metadata.query_count == 16
    assert candidate_limits == [64, 128, 256, 512, 512]
    assert read.has_more is True
    assert read.browse_status == "continuation"


def test_filter_value_cursor_duplicate_only_page_reaches_terminal_exhaustion():
    duplicate_value = "completed"
    candidates = [
        _candidate(
            PROJECT_A,
            f"duplicate-{index:04d}",
            start_time=NOW - timedelta(seconds=index + 1),
        )
        for index in range(1_000)
    ]
    executor = _value_cursor_executor(
        candidates,
        {str(row["id"]): duplicate_value for row in candidates},
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - ATTRIBUTE_READ_EXPLICIT_SEGMENT,
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", duplicate_value),),
    )

    candidate_limits = [
        call.params["candidate_limit"] - 1
        for call in executor.calls
        if "segment_start" in call.params
    ]
    assert read.rows == ()
    assert read.has_more is False
    assert read.browse_status == "exhausted"
    assert read.next_segment_end == NOW - ATTRIBUTE_READ_EXPLICIT_SEGMENT
    assert read.next_before_identity is None
    assert read.metadata.query_count == 16
    assert candidate_limits == [64, 128, 256, 512, 512]


def test_filter_value_cursor_first_page_does_not_use_temporal_distinct_proof():
    executor = RecordingExecutor(
        distinct_responder=lambda *_args: pytest.fail(
            "page one must not issue a temporal distinct proof"
        )
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
    )

    assert read.browse_status == "exhausted"
    assert all("distinct_limit" not in call.params for call in executor.calls)


def test_filter_value_cursor_seen_temporal_distinct_exhausts_finite_dense_window():
    seen_digest = attribute_value_cursor_digest("string", "Rechazado")
    window = timedelta(minutes=30)
    # Every successful request certifies at least one minimum-width slice.
    # Derive the terminal-proof bound from that public progress contract so
    # this remains valid if the internal per-request query budget changes.
    max_pages = int(window / ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT) + 1
    segment_end = NOW
    checkpoints = [segment_end]
    pages: list[AttributeValueCursorPageRead] = []
    all_calls: list[QueryCall] = []

    for _ in range(max_pages):
        executor = RecordingExecutor(
            lambda *_args: pytest.fail(
                "a complete seen-only proof must not re-enter the physical walk"
            ),
            distinct_responder=lambda *_args: [
                _distinct_value_group("string", "Rechazado", count=100_000)
            ],
        )
        read = AttributeReadSelector(
            executor, now=NOW, json_attribute_mode="arrays"
        ).read_value_cursor_page(
            [PROJECT_A],
            "final_status",
            page_size=10,
            window_start=NOW - window,
            window_end=NOW,
            segment_end=segment_end,
            seen_value_digests=(seen_digest,),
        )
        pages.append(read)
        all_calls.extend(executor.calls)
        checkpoints.append(read.next_segment_end)
        if not read.has_more:
            break
        segment_end = read.next_segment_end

    assert len(pages) <= max_pages
    assert all(page.has_more for page in pages[:-1])
    assert pages[-1].browse_status == "exhausted"
    assert pages[-1].has_more is False
    assert pages[-1].next_segment_end == NOW - window
    assert all(page.rows == () for page in pages)
    assert all(
        newer > older
        for newer, older in zip(checkpoints, checkpoints[1:], strict=False)
    )
    assert all("distinct_limit" in call.params for call in all_calls)
    assert all(
        call.params["segment_end_us"] - call.params["segment_start_us"]
        <= int(ATTRIBUTE_VALUE_CURSOR_DISTINCT_MAX_SEGMENT.total_seconds() * 1_000_000)
        for call in all_calls
    )
    assert all(
        call.timeout_ms == ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS
        for call in all_calls
    )
    assert all(
        page.metadata.query_count <= ATTRIBUTE_READ_MAX_QUERY_COUNT for page in pages
    )

    sql = all_calls[0].sql
    assert "SELECT DISTINCT" in sql
    assert sql.count("FROM spans AS raw_source") == 1
    assert "ARRAY JOIN" in sql
    assert "argMax(" not in sql
    assert "WHERE is_deleted = 0" not in sql
    assert "indexHint(has(mapKeys(attrs_string)" in sql
    assert "indexHint(has(mapKeys(attrs_number)" in sql
    assert "indexHint(has(mapKeys(attrs_bool)" in sql
    assert "JSONExtractRaw(attributes_extra" in sql
    assert "AND (" in sql  # direct source predicate keeps CH skip indexes usable


@pytest.mark.parametrize("page_size", (1, 2, 25))
def test_filter_value_cursor_seen_vocabulary_larger_than_page_exhausts(page_size):
    values = (
        "AGENT",
        "CHAIN",
        "CONVERSATION",
        "LLM",
        "RETRIEVER",
        "TOOL",
        "UNKNOWN",
    )
    seen_digests = tuple(
        attribute_value_cursor_digest("string", value) for value in values
    )
    executor = RecordingExecutor(
        lambda *_args: pytest.fail(
            "a complete seen-only vocabulary must not enter the physical walk"
        ),
        distinct_responder=lambda *_args: [
            _distinct_value_group("string", value) for value in values
        ],
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "fi.span.kind",
        page_size=page_size,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT,
        window_end=NOW,
        seen_value_digests=seen_digests,
    )

    assert len(executor.calls) == 1
    distinct_call = executor.calls[0]
    assert distinct_call.params["distinct_limit"] == len(values) + page_size + 1
    assert "max_rows_in_distinct" not in distinct_call.settings
    assert "distinct_overflow_mode" not in distinct_call.settings
    assert read.rows == ()
    assert read.seen_value_digests == seen_digests
    assert read.has_more is False
    assert read.browse_status == "exhausted"
    assert read.next_segment_end == NOW - ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT


def test_filter_value_cursor_proof_result_sentinel_has_a_hard_maximum():
    seen_digests = tuple(
        attribute_value_cursor_digest("string", f"seen-{index}")
        for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS)
    )
    executor = RecordingExecutor(
        lambda *_args: pytest.fail("an empty complete proof must not fallback"),
        distinct_responder=lambda *_args: [],
    )

    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "fi.span.kind",
        page_size=ATTRIBUTE_VALUE_CURSOR_MAX_PAGE_SIZE,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT,
        window_end=NOW,
        seen_value_digests=seen_digests,
    )

    assert len(executor.calls) == 1
    distinct_call = executor.calls[0]
    assert ATTRIBUTE_VALUE_CURSOR_PROOF_MAX_RESULT_ROWS == 4_147
    assert (
        distinct_call.params["distinct_limit"]
        == ATTRIBUTE_VALUE_CURSOR_PROOF_MAX_RESULT_ROWS
    )
    assert (
        distinct_call.settings["max_result_rows"]
        == ATTRIBUTE_VALUE_CURSOR_PROOF_MAX_RESULT_ROWS
    )
    assert read.rows == ()
    assert read.has_more is False
    assert read.browse_status == "exhausted"


def test_filter_value_cursor_radix_count_only_state_still_uses_exact_proof():
    seen_value = "Rechazado"
    seen_digest = attribute_value_cursor_digest("string", seen_value)
    executor = RecordingExecutor(
        lambda *_args: pytest.fail(
            "a complete radix-backed seen proof must not enter the physical walk"
        ),
        distinct_responder=lambda *_args: [
            _distinct_value_group("string", seen_value, count=100_000)
        ],
    )

    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT,
        window_end=NOW,
        seen_value_digests=(),
        seen_value_contains=lambda digest: digest == seen_digest,
        seen_value_count=ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1,
    )

    assert len(executor.calls) == 1
    assert executor.calls[0].params["distinct_limit"] == 4_108
    assert read.rows == ()
    assert read.has_more is False
    assert read.browse_status == "exhausted"
    assert read.seen_value_digests == ()
    assert read.seen_value_count == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1


def test_filter_value_cursor_dynamic_proof_rejects_one_unseen_value():
    seen_values = (
        "AGENT",
        "CHAIN",
        "CONVERSATION",
        "LLM",
        "RETRIEVER",
        "TOOL",
        "UNKNOWN",
    )
    unseen_value = "WORKFLOW"
    seen_digests = tuple(
        attribute_value_cursor_digest("string", value) for value in seen_values
    )
    candidate = _candidate(
        PROJECT_A,
        "unseen-after-proof",
        start_time=NOW - timedelta(seconds=1),
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): unseen_value},
    )
    executor.distinct_responder = lambda *_args: [
        *(_distinct_value_group("string", value) for value in seen_values),
        _distinct_value_group("string", unseen_value),
    ]

    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "fi.span.kind",
        page_size=2,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT,
        window_end=NOW,
        seen_value_digests=seen_digests,
    )

    proof_call = next(
        call for call in executor.calls if "distinct_limit" in call.params
    )
    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert proof_call.params["distinct_limit"] == len(seen_values) + 2 + 1
    assert proof_call.params["segment_end_us"] == _unix_microseconds(NOW)
    assert candidate_call.params["segment_end"] == NOW
    assert read.rows == (AttributeValueRow(unseen_value, "string", 1),)


@pytest.mark.parametrize("horizon_days", (7, 30, 365))
def test_filter_value_cursor_duplicate_only_search_returns_bounded_continuation(
    horizon_days,
):
    horizon = timedelta(days=horizon_days)
    seen_value = "completed"
    executor = RecordingExecutor(
        lambda *_args: pytest.fail(
            "a complete seen-only raw proof must not enter physical replay"
        ),
        distinct_responder=lambda *_args: [
            _distinct_value_group("string", seen_value, count=100_000)
        ],
    )

    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="complete",
        attribute_type="string",
        window_start=NOW - horizon,
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", seen_value),),
    )

    widths = tuple(
        timedelta(
            microseconds=(
                call.params["segment_end_us"] - call.params["segment_start_us"]
            )
        )
        for call in executor.calls
    )
    expected_widths = _geometric_slice_widths(
        horizon,
        initial=ATTRIBUTE_VALUE_CURSOR_DISTINCT_INITIAL_SEGMENT,
        maximum=ATTRIBUTE_VALUE_CURSOR_DISTINCT_MAX_SEGMENT,
    )[:ATTRIBUTE_VALUE_CURSOR_MAX_SEARCH_PROOFS]
    assert widths == expected_widths
    assert read.rows == ()
    assert read.browse_status == "continuation"
    assert read.has_more is True
    assert read.next_segment_end == NOW - sum(expected_widths, timedelta())
    assert read.metadata.query_count == len(executor.calls) == len(expected_widths)
    assert all(
        call.timeout_ms == ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS
        for call in executor.calls
    )


def test_filter_value_cursor_typed_search_distinct_exhausts_seen_matches():
    seen_value = "Rechazado"
    seen_digest = attribute_value_cursor_digest("string", seen_value)
    executor = RecordingExecutor(
        lambda *_args: pytest.fail(
            "a complete typed searched proof must not re-enter the physical walk"
        ),
        distinct_responder=lambda *_args: [
            _distinct_value_group("string", seen_value, count=50_000)
        ],
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - timedelta(minutes=2),
        window_end=NOW,
        seen_value_digests=(seen_digest,),
        search="rEcHaZ",
        attribute_type="string",
    )

    distinct_calls = [
        call for call in executor.calls if "distinct_limit" in call.params
    ]
    assert read.rows == ()
    assert read.has_more is False
    assert read.browse_status == "exhausted"
    assert read.next_segment_end == NOW - timedelta(minutes=2)
    assert distinct_calls
    assert all(
        call.params["distinct_attribute_type"] == "string"
        and call.params["distinct_attribute_search"] == "rEcHaZ"
        for call in distinct_calls
    )
    sql = distinct_calls[0].sql
    assert "SELECT DISTINCT" in sql
    assert sql.count("FROM spans AS raw_source") == 1
    assert "ARRAY JOIN" not in sql
    assert "positionCaseInsensitiveUTF8(" in sql
    assert "length(attrs_string[%(attribute_key)s]) !=" in sql
    assert "argMax(" not in sql
    assert sql.count("indexHint(has(mapKeys(attrs_string)") == 1


def test_filter_value_cursor_unpinned_search_distinct_exhausts_seen_typed_match():
    seen_value = "Rechazado"
    executor = RecordingExecutor(
        lambda *_args: pytest.fail(
            "a complete unpinned searched proof must not re-enter the physical walk"
        ),
        distinct_responder=lambda *_args: [
            _distinct_value_group("string", seen_value, count=50_000)
        ],
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - timedelta(minutes=2),
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", seen_value),),
        search="rechazado",
    )

    assert read.rows == ()
    assert read.browse_status == "exhausted"
    assert all(
        call.params["distinct_attribute_type"] == ""
        and call.params["distinct_attribute_search"] == "rechazado"
        for call in executor.calls
    )
    sql = executor.calls[0].sql
    assert "'string'" in sql
    assert "'number'" in sql
    assert "'boolean'" in sql
    assert "'json'" in sql


def test_filter_value_cursor_searched_empty_proof_returns_bounded_continuation():
    executor = RecordingExecutor(
        distinct_responder=lambda *_args: [],
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", "Rechazado"),),
        search="rechazado",
    )

    distinct_calls = [
        call for call in executor.calls if "distinct_limit" in call.params
    ]
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert distinct_calls
    assert candidate_calls == []
    assert all(
        call.params["segment_end_us"] - call.params["segment_start_us"]
        <= int(ATTRIBUTE_VALUE_CURSOR_DISTINCT_MAX_SEGMENT.total_seconds() * 1_000_000)
        for call in distinct_calls
    )
    assert read.rows == ()
    assert read.browse_status == "continuation"
    assert read.has_more is True
    assert len(distinct_calls) == ATTRIBUTE_VALUE_CURSOR_MAX_SEARCH_PROOFS
    assert read.next_segment_end == NOW - sum(
        (
            ATTRIBUTE_VALUE_CURSOR_DISTINCT_INITIAL_SEGMENT * (2**index)
            for index in range(ATTRIBUTE_VALUE_CURSOR_MAX_SEARCH_PROOFS)
        ),
        timedelta(),
    )
    assert read.metadata.query_count == len(distinct_calls)
    assert read.metadata.query_count < ATTRIBUTE_READ_MAX_QUERY_COUNT


def test_filter_value_cursor_unpinned_search_proof_uses_array_member_semantics():
    raw_array = json.dumps(["Rechazado", "Approved"])
    executor = RecordingExecutor(
        lambda *_args: pytest.fail(
            "an unseen nonmatching array member must not force physical fallback"
        ),
        distinct_responder=lambda *_args: [
            _distinct_value_group("json", raw_array, count=10_000)
        ],
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - timedelta(minutes=2),
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("array", "Rechazado"),),
        search="rechazado",
    )

    call = executor.calls[0]
    assert read.rows == ()
    assert read.browse_status == "exhausted"
    assert "JSONExtractRaw(attributes_extra" in call.sql
    assert "JSONHas(attributes_extra" in call.sql


def test_filter_value_cursor_raw_stale_seen_match_is_safe_to_skip():
    """A stale raw value may over-admit, but a known digest remains skippable."""

    seen_value = "Rechazado"
    executor = RecordingExecutor(
        lambda *_args: pytest.fail(
            "a stale raw match already in the vocabulary must not fallback"
        ),
        distinct_responder=lambda *_args: [_distinct_value_group("string", seen_value)],
    )

    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - timedelta(minutes=2),
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", seen_value),),
        search="rechazado",
        attribute_type="string",
    )

    assert read.rows == ()
    assert read.has_more is False
    assert all("distinct_limit" in call.params for call in executor.calls)


def test_filter_value_cursor_raw_unseen_tombstone_forces_exact_replay_without_emit():
    """An unseen stale match is conservative fallback, never a published value."""

    candidate = _candidate(
        PROJECT_A,
        "stale-rechazado",
        start_time=NOW - timedelta(seconds=1),
        candidate_version=1,
    )

    def respond(call, _call_number):
        if "segment_start" in call.params:
            if not (
                call.params["segment_start"]
                <= candidate["start_time"]
                < call.params["segment_end"]
            ):
                return []
            return _keyset_candidate_page([candidate], call)
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    candidate["id"],
                    trace_id=candidate["trace_id"],
                    start_time=candidate["start_time"],
                    is_deleted=1,
                    latest_version=2,
                )
            ]
        pytest.fail("a tombstoned candidate must not reach value hydration")

    executor = RecordingExecutor(
        respond,
        distinct_responder=lambda *_args: [
            _distinct_value_group("string", "Rechazado")
        ],
    )
    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", "completed"),),
        search="rechazado",
        attribute_type="string",
    )

    assert read.rows == ()
    assert read.has_more is False
    assert any("max(_version) AS latest_version" in call.sql for call in executor.calls)


def test_filter_value_cursor_python_casefold_match_is_not_lost_by_sql_prefilter():
    candidate = _candidate(
        PROJECT_A,
        "unicode-casefold",
        start_time=NOW - timedelta(seconds=1),
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "Straße"},
    )
    executor.distinct_responder = lambda *_args: [
        _distinct_value_group("string", "Straße")
    ]

    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "custom.attribute",
        page_size=10,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", "other"),),
        search="STRASSE",
        attribute_type="string",
    )

    proof_call = next(
        call for call in executor.calls if "distinct_limit" in call.params
    )
    assert "length(attrs_string[%(attribute_key)s]) !=" in proof_call.sql
    assert read.rows == (AttributeValueRow("Straße", "string", 1),)


def test_filter_value_cursor_escaped_json_array_search_uses_decoded_members():
    member = 'a"b'
    raw_array = json.dumps([member, r"c\d"])
    executor = RecordingExecutor(
        lambda *_args: pytest.fail("all matching decoded members are already known"),
        distinct_responder=lambda *_args: [_distinct_value_group("json", raw_array)],
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "custom.attribute",
        page_size=10,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("array", member),),
        search=member,
    )

    assert read.rows == ()
    assert read.has_more is False


def test_filter_value_cursor_unpinned_search_unseen_array_match_is_not_skipped():
    checkpoint_time = NOW - timedelta(minutes=1)
    before_identity = (
        PROJECT_A,
        "trace-z",
        "z-checkpoint",
        checkpoint_time,
    )
    candidate = _candidate(
        PROJECT_A,
        "a-lower-same-time",
        trace_id="trace-a",
        start_time=checkpoint_time,
    )
    raw_array = json.dumps(["Rechazado - manual review", "Approved"])

    def respond(call, _call_number):
        if "segment_start" in call.params:
            rows = (
                [candidate]
                if call.params["segment_start"]
                <= checkpoint_time
                < call.params["segment_end"]
                else []
            )
            return _keyset_candidate_page(rows, call)
        return [
            _target_row(
                PROJECT_A,
                str(candidate["id"]),
                trace_id=str(candidate["trace_id"]),
                start_time=checkpoint_time,
                legacy_raw=raw_array,
            )
        ]

    executor = RecordingExecutor(
        respond,
        distinct_responder=lambda *_args: [_distinct_value_group("json", raw_array)],
    )
    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        before_identity=before_identity,
        seen_value_digests=(attribute_value_cursor_digest("array", "Rechazado"),),
        search="rechazado",
    )

    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert candidate_call.params["candidate_before_id"] == before_identity[2]
    assert read.rows == (AttributeValueRow("Rechazado - manual review", "array", 1),)


def test_filter_value_cursor_typed_search_unseen_match_falls_back_without_skip():
    checkpoint_time = NOW - timedelta(minutes=1)
    before_identity = (
        PROJECT_A,
        "trace-z",
        "z-checkpoint",
        checkpoint_time,
    )
    candidate = _candidate(
        PROJECT_A,
        "a-lower-same-time",
        trace_id="trace-a",
        start_time=checkpoint_time,
    )
    matching_unseen_value = "Rechazado - manual review"
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): matching_unseen_value},
    )
    executor.distinct_responder = lambda *_args: [
        _distinct_value_group("string", matching_unseen_value)
    ]

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        before_identity=before_identity,
        seen_value_digests=(attribute_value_cursor_digest("string", "Rechazado"),),
        search="rechazado",
        attribute_type="string",
    )

    distinct_call = executor.calls[0]
    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert distinct_call.params["distinct_attribute_type"] == "string"
    assert distinct_call.params["distinct_attribute_search"] == "rechazado"
    assert distinct_call.params["distinct_before_id"] == before_identity[2]
    assert candidate_call.params["candidate_before_id"] == before_identity[2]
    assert candidate_call.params["segment_start"] == checkpoint_time
    assert (
        candidate_call.params["segment_end"]
        == checkpoint_time + ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
    )
    assert read.rows == (AttributeValueRow(matching_unseen_value, "string", 1),)


@pytest.mark.parametrize(
    ("attribute_type", "search", "seen_value"),
    [
        ("string", "done", "DONE"),
        ("number", "42", 42.0),
        ("boolean", "TRUE", True),
    ],
)
def test_filter_value_cursor_typed_search_distinct_binds_scalar_semantics(
    attribute_type,
    search,
    seen_value,
):
    executor = RecordingExecutor(
        lambda *_args: pytest.fail("the complete searched slice must not fallback"),
        distinct_responder=lambda *_args: [
            _distinct_value_group(attribute_type, seen_value)
        ],
    )

    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "custom.attribute",
        page_size=10,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest(attribute_type, seen_value),),
        search=search,
        attribute_type=attribute_type,
    )

    call = executor.calls[0]
    assert read.browse_status == "exhausted"
    assert call.params["distinct_attribute_type"] == attribute_type
    assert call.params["distinct_attribute_search"] == search
    if attribute_type == "string":
        assert "positionCaseInsensitiveUTF8(" in call.sql
        assert "length(attrs_string[%(attribute_key)s]) !=" in call.sql
    elif attribute_type == "number":
        assert "positionCaseInsensitiveUTF8(" not in call.sql
        assert "toString(value_number)" not in call.sql
    else:
        assert "positionCaseInsensitiveUTF8(" in call.sql
        assert "if(attrs_bool[%(attribute_key)s], 'true', 'false')" in call.sql


def test_filter_value_cursor_temporal_distinct_unseen_value_falls_back_at_same_keyset():
    checkpoint_time = NOW - timedelta(minutes=1)
    before_identity = (
        PROJECT_A,
        "trace-z",
        "z-checkpoint",
        checkpoint_time,
    )
    candidate = _candidate(
        PROJECT_A,
        "a-lower-same-time",
        trace_id="trace-a",
        start_time=checkpoint_time,
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "queued"},
    )
    executor.distinct_responder = lambda *_args: [
        _distinct_value_group("string", "queued")
    ]

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        before_identity=before_identity,
        seen_value_digests=(attribute_value_cursor_digest("string", "Rechazado"),),
    )

    distinct_call = executor.calls[0]
    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert distinct_call.params["distinct_before_id"] == before_identity[2]
    assert candidate_call.params["candidate_before_id"] == before_identity[2]
    assert distinct_call.params["segment_end_us"] == _unix_microseconds(
        checkpoint_time + timedelta(microseconds=1)
    )
    assert candidate_call.params["segment_end"] == NOW
    assert read.rows == (AttributeValueRow("queued", "string", 1),)


def test_filter_value_cursor_temporal_distinct_failure_returns_proven_safe_slice():
    candidate = _candidate(
        PROJECT_A,
        "older-unique",
        start_time=NOW - timedelta(minutes=6),
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "queued"},
    )
    seen_value = "completed"

    def distinct_respond(call, call_number):
        width_us = call.params["segment_end_us"] - call.params["segment_start_us"]
        width = timedelta(microseconds=width_us)
        if call_number > 1:
            return ReadDeadlineExceeded("speculative distinct slice exceeded budget")
        if width == ATTRIBUTE_VALUE_CURSOR_DISTINCT_INITIAL_SEGMENT:
            return [_distinct_value_group("string", seen_value)]
        return [_distinct_value_group("string", "queued")]

    executor.distinct_responder = distinct_respond
    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", seen_value),),
    )

    distinct_calls = [
        call for call in executor.calls if "distinct_limit" in call.params
    ]
    widths = [
        timedelta(
            microseconds=(
                call.params["segment_end_us"] - call.params["segment_start_us"]
            )
        )
        for call in distinct_calls
    ]
    assert widths == [
        ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT,
        ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT * 2,
    ]
    assert all(width <= ATTRIBUTE_VALUE_CURSOR_DISTINCT_MAX_SEGMENT for width in widths)
    assert not any("segment_start" in call.params for call in executor.calls)
    assert read.rows == ()
    assert read.has_more is True
    assert read.next_segment_end == (
        NOW - ATTRIBUTE_VALUE_CURSOR_DISTINCT_INITIAL_SEGMENT
    )


def test_filter_value_cursor_temporal_distinct_minimum_failure_keeps_original_frontier():
    candidate = _candidate(
        PROJECT_A,
        "newest-unique",
        start_time=NOW - timedelta(minutes=1),
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "queued"},
    )
    executor.distinct_responder = lambda *_args: ReadDeadlineExceeded(
        "all speculative widths exceeded budget"
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", "completed"),),
    )

    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert candidate_call.params["segment_end"] == NOW
    assert read.rows == (AttributeValueRow("queued", "string", 1),)


def test_filter_value_cursor_failed_proof_cap_reanchors_checkpoint_to_exact_floor():
    checkpoint_time = NOW - timedelta(minutes=30)
    before_identity = (
        PROJECT_A,
        "trace-proof-cap",
        "span-z-proof-cap",
        checkpoint_time,
    )
    candidate = _candidate(
        PROJECT_A,
        "span-a-proof-cap",
        trace_id=before_identity[1],
        start_time=checkpoint_time,
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "queued"},
    )
    executor.distinct_responder = lambda *_args: ReadDeadlineExceeded(
        "bounded proof exceeded read budget"
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "fi.span.kind",
        page_size=1,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_start=NOW - timedelta(hours=6),
        before_identity=before_identity,
        seen_value_digests=(attribute_value_cursor_digest("string", "completed"),),
    )

    distinct_calls = [
        call for call in executor.calls if "distinct_limit" in call.params
    ]
    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert len(distinct_calls) == 2
    assert candidate_call.params["segment_start"] == checkpoint_time
    assert candidate_call.params["segment_end"] == (
        checkpoint_time + ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
    )
    assert candidate_call.params["candidate_before_id"] == before_identity[2]
    assert read.rows == (AttributeValueRow("queued", "string", 1),)


def test_filter_value_cursor_typed_distinct_sentinel_is_an_exact_fallback():
    candidate = _candidate(
        PROJECT_A,
        "typed-lane-overflow",
        start_time=NOW - timedelta(seconds=1),
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "Rechazado"},
    )
    # SQL LIMIT returns the complete two-row overflow sentinel without a
    # max_rows_in_distinct race. A sentinel is never accepted as a complete
    # proof and must retain the unchanged ordered fallback.
    executor.distinct_responder = lambda *_args: [
        _distinct_value_group("string", "stale-string"),
        _distinct_value_group("number", 1),
    ]

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        search="Rechazado",
        page_size=1,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
    )

    distinct_call = next(
        call for call in executor.calls if "distinct_limit" in call.params
    )
    assert distinct_call.params["distinct_limit"] == 2
    assert "max_rows_in_distinct" not in distinct_call.settings
    assert "distinct_overflow_mode" not in distinct_call.settings
    assert distinct_call.timeout_ms == ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS
    assert distinct_call.settings["max_threads"] == 1
    assert distinct_call.settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
    assert "max_rows_to_read" not in distinct_call.settings
    assert distinct_call.settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
    assert distinct_call.settings["max_result_rows"] == 2
    assert (
        distinct_call.settings["max_result_bytes"]
        == settings.ATTRIBUTE_READ_MAX_RESULT_BYTES
    )
    assert distinct_call.settings["read_overflow_mode"] == "throw"
    assert distinct_call.settings["result_overflow_mode"] == "throw"
    assert distinct_call.settings["timeout_overflow_mode"] == "throw"
    assert read.rows == (AttributeValueRow("Rechazado", "string", 1),)


def test_filter_value_cursor_code_191_fallback_reanchors_page_n_checkpoint():
    checkpoint_time = NOW - timedelta(minutes=30)
    before_identity = (
        PROJECT_A,
        "trace-page-16",
        "span-z-page-16",
        checkpoint_time,
    )
    candidate = _candidate(
        PROJECT_A,
        "span-a-page-17",
        trace_id=before_identity[1],
        start_time=checkpoint_time,
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "queued"},
    )
    executor.distinct_responder = lambda *_args: ServerException(
        "DB::Exception: Limit for rows to read exceeded: max rows: 2, "
        "current rows: 5 (LIMIT_FOR_SET_SIZE_EXCEEDED)",
        code=191,
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "fi.span.kind",
        page_size=1,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_start=NOW - timedelta(hours=6),
        before_identity=before_identity,
        seen_value_digests=(attribute_value_cursor_digest("string", "completed"),),
    )

    distinct_calls = [
        call for call in executor.calls if "distinct_limit" in call.params
    ]
    distinct_widths = [
        timedelta(
            microseconds=(
                call.params["segment_end_us"] - call.params["segment_start_us"]
            )
        )
        for call in distinct_calls
    ]
    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert distinct_widths[0] == timedelta(hours=6)
    assert distinct_widths == [timedelta(hours=6), timedelta(hours=3)]
    assert all(call.params["distinct_limit"] == 3 for call in distinct_calls)
    assert candidate_call.params["segment_start"] == checkpoint_time
    assert candidate_call.params["segment_end"] == (
        checkpoint_time + ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
    )
    assert candidate_call.params["candidate_before_id"] == before_identity[2]
    assert read.rows == (AttributeValueRow("queued", "string", 1),)


def test_filter_value_cursor_distinct_reserve_fallback_reanchors_checkpoint(
    monkeypatch,
):
    checkpoint_time = NOW - timedelta(minutes=15)
    before_identity = (
        PROJECT_A,
        "trace-reserve-page",
        "span-z-reserve-page",
        checkpoint_time,
    )
    candidate = _candidate(
        PROJECT_A,
        "span-a-reserve-page",
        trace_id=before_identity[1],
        start_time=checkpoint_time,
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "queued"},
    )
    executor.distinct_responder = lambda *_args: ServerException(
        "DB::Exception: Limit for rows to read exceeded: max rows: 2, "
        "current rows: 5 (LIMIT_FOR_SET_SIZE_EXCEEDED)",
        code=191,
    )
    # Admit one proof and exactly preserve the four-query unpinned fallback.
    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads.ATTRIBUTE_READ_MAX_QUERY_COUNT",
        5,
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "fi.span.kind",
        page_size=1,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_start=NOW - timedelta(minutes=20),
        before_identity=before_identity,
        seen_value_digests=(attribute_value_cursor_digest("string", "completed"),),
    )

    distinct_calls = [
        call for call in executor.calls if "distinct_limit" in call.params
    ]
    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert len(distinct_calls) == 1
    assert distinct_calls[0].params["distinct_limit"] == 3
    assert candidate_call.params["segment_start"] == checkpoint_time
    assert candidate_call.params["segment_end"] == checkpoint_time + timedelta(
        minutes=10
    )
    assert candidate_call.params["candidate_before_id"] == before_identity[2]
    assert read.rows == (AttributeValueRow("queued", "string", 1),)


def test_filter_value_cursor_temporal_distinct_preserves_physical_fallback_budget():
    class ManualClock:
        value = 100.0

        def __call__(self):
            return self.value

    clock = ManualClock()
    seen_value = "completed"
    seen_digest = attribute_value_cursor_digest("string", seen_value)
    proof_calls = 0

    def first_distinct(call, _call_number):
        nonlocal proof_calls
        proof_calls += 1
        clock.value += 0.47
        value = seen_value if proof_calls <= 5 else "queued"
        return [_distinct_value_group("string", value)]

    first_executor = RecordingExecutor(
        lambda *_args: pytest.fail(
            "an inconclusive proof after prior progress must not enter the "
            "physical fallback"
        ),
        distinct_responder=first_distinct,
    )
    first = AttributeReadSelector(
        first_executor,
        now=NOW,
        wall_timeout_ms=6_000,
        clock=clock,
        json_attribute_mode="arrays",
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        seen_value_digests=(seen_digest,),
    )

    assert proof_calls == ATTRIBUTE_VALUE_CURSOR_MAX_UNSEARCHED_CONTINUATION_PROOFS
    assert first.rows == ()
    assert first.has_more is True
    assert first.next_segment_end < NOW
    assert all("distinct_limit" in call.params for call in first_executor.calls)
    assert 0.93 <= clock.value - 100.0 < 0.95
    assert ATTRIBUTE_VALUE_CURSOR_DISTINCT_GUARD_MARGIN_MS == 100

    candidate = _candidate(
        PROJECT_A,
        "older-unique",
        start_time=first.next_segment_end - timedelta(minutes=1),
    )
    second_executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "queued"},
    )
    second_executor.distinct_responder = lambda *_args: [
        _distinct_value_group("string", "queued")
    ]
    second = AttributeReadSelector(
        second_executor,
        now=NOW,
        wall_timeout_ms=6_000,
        clock=clock,
        json_attribute_mode="arrays",
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_end=first.next_segment_end,
        seen_value_digests=first.seen_value_digests,
    )

    assert second.rows == (AttributeValueRow("queued", "string", 1),)
    assert any("segment_start" in call.params for call in second_executor.calls)


def test_filter_value_cursor_temporal_distinct_array_overflow_falls_back_without_skip():
    array_members = [f"member-{index}" for index in range(501)]
    candidate = _candidate(
        PROJECT_A,
        "typed-after-array-overflow",
        start_time=NOW - timedelta(minutes=1),
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "queued"},
    )
    executor.distinct_responder = lambda *_args: [
        _distinct_value_group("json", json.dumps(array_members))
    ]

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        seen_value_digests=tuple(
            attribute_value_cursor_digest("array", member) for member in array_members
        ),
    )

    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert candidate_call.params["segment_end"] == NOW
    assert read.rows == (AttributeValueRow("queued", "string", 1),)


def test_filter_value_cursor_temporal_distinct_string_budget_falls_back_without_skip():
    array_members = [
        f"{index:02d}" + "x" * (JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES - 2)
        for index in range(17)
    ]
    candidate = _candidate(
        PROJECT_A,
        "typed-after-array-byte-boundary",
        start_time=NOW - timedelta(minutes=1),
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "queued"},
    )
    executor.distinct_responder = lambda *_args: [
        _distinct_value_group("json", json.dumps(array_members))
    ]

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        seen_value_digests=tuple(
            attribute_value_cursor_digest("array", member)
            for member in array_members[:16]
        ),
    )

    candidate_call = next(
        call for call in executor.calls if "segment_start" in call.params
    )
    assert candidate_call.params["segment_end"] == NOW
    assert read.rows == (AttributeValueRow("queued", "string", 1),)


def test_filter_value_cursor_temporal_distinct_huge_array_never_materializes_proof():
    candidate = _candidate(
        PROJECT_A,
        "typed-after-huge-array-proof",
        start_time=NOW - timedelta(minutes=1),
    )
    executor = _value_cursor_executor(
        [candidate],
        {str(candidate["id"]): "queued"},
    )
    huge_raw_array = '["' + ("x" * (300 * 1024)) + '"]'
    executor.distinct_responder = lambda *_args: [
        _distinct_value_group("json", huge_raw_array)
    ]

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", "completed"),),
    )

    assert any("segment_start" in call.params for call in executor.calls)
    assert read.rows == (AttributeValueRow("queued", "string", 1),)


def test_filter_value_cursor_temporal_distinct_programming_error_is_not_degraded():
    executor = RecordingExecutor(
        distinct_responder=lambda *_args: RuntimeError("invalid distinct SQL")
    )

    with pytest.raises(RuntimeError, match="invalid distinct SQL"):
        AttributeReadSelector(
            executor, now=NOW, json_attribute_mode="arrays"
        ).read_value_cursor_page(
            [PROJECT_A],
            "call.status",
            page_size=10,
            window_start=NOW - timedelta(hours=1),
            window_end=NOW,
            seen_value_digests=(attribute_value_cursor_digest("string", "completed"),),
        )

    assert len(executor.calls) == 1


@pytest.mark.parametrize("failure_stage", ["candidate", "replay"])
def test_filter_value_cursor_expanded_failure_continues_without_skip_or_repeat(
    failure_stage,
):
    duplicate_value = "completed"
    unique_value = "next-unique"
    successful_prefix = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT + (
        2 * ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
    )
    failure_limit = 4 * ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
    candidates = [
        _candidate(
            PROJECT_A,
            f"span-{index:03d}",
            start_time=NOW - timedelta(microseconds=index + 1),
        )
        for index in range(successful_prefix)
    ]
    candidates.append(
        _candidate(
            PROJECT_A,
            "next-unique",
            start_time=NOW - timedelta(microseconds=successful_prefix + 1),
        )
    )
    candidates.extend(
        _candidate(
            PROJECT_A,
            f"tail-{index:03d}",
            start_time=NOW - timedelta(microseconds=successful_prefix + index + 2),
        )
        for index in range(failure_limit)
    )
    value_by_id = {
        str(row["id"]): (
            unique_value if row["id"] == "next-unique" else duplicate_value
        )
        for row in candidates
    }
    failure_kwargs = {
        f"fail_{failure_stage}_limit": failure_limit,
    }
    first_executor = _value_cursor_executor(
        candidates,
        value_by_id,
        **failure_kwargs,
    )
    seen_digest = attribute_value_cursor_digest("string", duplicate_value)

    first = AttributeReadSelector(
        first_executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=1,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        seen_value_digests=(seen_digest,),
    )
    verified_row = candidates[successful_prefix - 1]
    verified_checkpoint = (
        PROJECT_A,
        str(verified_row["trace_id"]),
        str(verified_row["id"]),
        verified_row["start_time"],
    )

    second_executor = _value_cursor_executor(candidates, value_by_id)
    second = AttributeReadSelector(
        second_executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=1,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_end=first.next_segment_end,
        before_identity=first.next_before_identity,
        resume_identity=first.next_resume_identity,
        resume_member_offset=first.next_resume_member_offset,
        seen_value_digests=first.seen_value_digests,
    )

    candidate_limits = [
        call.params["candidate_limit"] - 1
        for call in first_executor.calls
        if "segment_start" in call.params
    ]
    replay_sizes = [
        len(call.params["candidate_ids_0"])
        for call in first_executor.calls
        if "candidate_ids_0" in call.params
    ]
    assert first.rows == ()
    assert first.metadata.query_complete is True
    assert first.next_segment_end == NOW
    assert first.next_before_identity == verified_checkpoint
    assert candidate_limits == [64, 128, failure_limit]
    assert replay_sizes == (
        [64, 64, 128, 128]
        if failure_stage == "candidate"
        else [64, 64, 128, 128, failure_limit]
    )
    assert first_executor.calls[-1].timeout_ms == (
        ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS
    )
    assert second.rows == (AttributeValueRow(unique_value, "string", 1),)
    continued_candidate = next(
        call for call in second_executor.calls if "segment_start" in call.params
    )
    assert continued_candidate.params["candidate_before_id"] == verified_row["id"]
    continued_ids = next(
        call.params["candidate_ids_0"]
        for call in second_executor.calls
        if "candidate_ids_0" in call.params
    )
    assert continued_ids[0] == "next-unique"
    assert set(continued_ids).isdisjoint(
        {str(row["id"]) for row in candidates[:successful_prefix]}
    )
    assert [row.value for page in (first, second) for row in page.rows] == [
        unique_value
    ]


def test_filter_value_cursor_unpinned_duplicate_page_respects_query_ceiling():
    duplicate_value = "completed"
    candidate_page_count = (ATTRIBUTE_READ_MAX_QUERY_COUNT - 1) // 3
    processed_candidates = (
        ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
        + (2 * ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT)
        + (4 * ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT)
        + (candidate_page_count - 3) * ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_LIMIT
    )
    candidates = [
        _candidate(
            PROJECT_A,
            f"span-{index:05d}",
            start_time=NOW - timedelta(microseconds=index + 1),
        )
        for index in range(processed_candidates + 1)
    ]
    executor = _value_cursor_executor(
        candidates,
        {str(row["id"]): duplicate_value for row in candidates},
    )

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", duplicate_value),),
    )

    candidate_limits = [
        call.params["candidate_limit"] - 1
        for call in executor.calls
        if "segment_start" in call.params
    ]
    last_verified = candidates[processed_candidates - 1]
    assert read.rows == ()
    assert read.has_more is True
    assert read.browse_status == "continuation"
    assert read.metadata.query_count == 1 + (3 * candidate_page_count) == 28
    assert len(executor.calls) == 1 + (3 * candidate_page_count)
    assert (
        len(
            [
                call
                for call in executor.calls
                if "max(_version) AS latest_version" in call.sql
            ]
        )
        == candidate_page_count
    )
    assert all(
        "attributes_extra" not in call.sql
        for call in executor.calls
        if "candidate_ids_0" in call.params
    )
    assert candidate_limits == [64, 128, 256] + [
        ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_LIMIT
    ] * (candidate_page_count - 3)
    assert read.next_before_identity == (
        str(last_verified["project_id"]),
        str(last_verified["trace_id"]),
        str(last_verified["id"]),
        last_verified["start_time"],
    )


def test_filter_value_cursor_pinned_type_reserves_three_query_page_ceiling():
    duplicate_value = "completed"
    # One complete temporal proof consumes the first query.  The selector may
    # then start only whole three-statement typed candidate batches.
    candidate_page_count = (ATTRIBUTE_READ_MAX_QUERY_COUNT - 1) // 3
    expected_candidate_limits = [
        ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT,
        2 * ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT,
        4 * ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT,
    ] + [ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_LIMIT] * (candidate_page_count - 3)
    processed_candidates = sum(expected_candidate_limits)
    candidates = [
        _candidate(
            PROJECT_A,
            f"typed-span-{index:05d}",
            start_time=NOW - timedelta(microseconds=index + 1),
        )
        for index in range(processed_candidates + 1)
    ]
    executor = _value_cursor_executor(
        candidates,
        {str(row["id"]): duplicate_value for row in candidates},
    )

    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        attribute_type="string",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        seen_value_digests=(attribute_value_cursor_digest("string", duplicate_value),),
    )

    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    certificate_calls = [
        call for call in executor.calls if "max(_version) AS latest_version" in call.sql
    ]
    hydration_calls = [
        call
        for call in executor.calls
        if "segment_start" not in call.params
        and "distinct_limit" not in call.params
        and "max(_version) AS latest_version" not in call.sql
    ]
    last_verified = candidates[processed_candidates - 1]
    assert read.rows == ()
    assert read.has_more is True
    assert read.browse_status == "continuation"
    expected_query_count = 1 + (candidate_page_count * 3)
    assert read.metadata.query_count == expected_query_count
    assert len(executor.calls) == expected_query_count
    assert (
        len(candidate_calls)
        == len(certificate_calls)
        == len(hydration_calls)
        == candidate_page_count
    )
    assert [
        call.params["candidate_limit"] - 1 for call in candidate_calls
    ] == expected_candidate_limits
    assert read.next_before_identity == (
        str(last_verified["project_id"]),
        str(last_verified["trace_id"]),
        str(last_verified["id"]),
        last_verified["start_time"],
    )


def test_filter_value_cursor_resumed_array_walk_uses_safe_30_query_cap():
    duplicate_value = "completed"
    resume_time = NOW - timedelta(microseconds=1)
    resume_identity = (
        PROJECT_A,
        "trace-resume-array",
        "resume-array",
        resume_time,
    )
    candidate_pages = (ATTRIBUTE_READ_MAX_QUERY_COUNT - 2 - 1) // 4
    processed_candidates = (
        ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
        + (2 * ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT)
        + (4 * ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT)
        + (candidate_pages - 3) * ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_LIMIT
    )
    candidates = [
        _candidate(
            PROJECT_A,
            f"span-{index:05d}",
            start_time=NOW - timedelta(microseconds=index + 2),
        )
        for index in range(processed_candidates + 1)
    ]
    by_id = {str(row["id"]): row for row in candidates}

    def respond(call, _):
        if "segment_start" in call.params:
            return _keyset_candidate_page(candidates, call)
        rows = []
        for span_id in call.params["candidate_ids_0"]:
            if span_id == resume_identity[2]:
                rows.append(
                    _target_row(
                        PROJECT_A,
                        span_id,
                        trace_id=resume_identity[1],
                        start_time=resume_time,
                        legacy_raw=json.dumps(("prior", duplicate_value)),
                    )
                )
                continue
            candidate = by_id[span_id]
            rows.append(
                _target_row(
                    PROJECT_A,
                    span_id,
                    trace_id=candidate["trace_id"],
                    start_time=candidate["start_time"],
                    legacy_raw=json.dumps((duplicate_value,)),
                )
            )
        return rows

    executor = RecordingExecutor(respond)
    seen_digests = (
        attribute_value_cursor_digest("array", "prior"),
        attribute_value_cursor_digest("array", duplicate_value),
    )
    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "call.status",
        page_size=10,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        resume_identity=resume_identity,
        resume_member_offset=1,
        seen_value_digests=seen_digests,
    )

    last_verified = candidates[processed_candidates - 1]
    candidate_limits = [
        call.params["candidate_limit"] - 1
        for call in executor.calls
        if "segment_start" in call.params
    ]
    assert read.rows == ()
    assert read.has_more is True
    assert read.browse_status == "continuation"
    assert read.seen_value_digests == seen_digests
    assert read.metadata.query_count == 2 + 1 + (4 * candidate_pages) == 27
    assert len(executor.calls) == 27
    assert candidate_limits == [64, 128, 256] + [
        ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_LIMIT
    ] * (candidate_pages - 3)
    assert read.next_resume_identity is None
    assert read.next_before_identity == (
        str(last_verified["project_id"]),
        str(last_verified["trace_id"]),
        str(last_verified["id"]),
        last_verified["start_time"],
    )
    assert read.next_before_identity[3] < resume_identity[3]


def test_filter_value_cursor_exhausts_frozen_window_after_tracking_prefix_is_full():
    seen = tuple(
        attribute_value_cursor_digest("string", f"prior-{index}")
        for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS)
    )
    executor = RecordingExecutor()

    read = AttributeReadSelector(
        executor, now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        seen_value_digests=seen,
    )

    assert read.rows == ()
    assert read.has_more is False
    assert read.browse_status == "exhausted"
    assert read.seen_value_digests == seen
    assert read.metadata.query_complete is True
    assert len(executor.calls) == 2


def test_filter_value_cursor_continues_past_legacy_tracking_threshold(monkeypatch):
    identity = (PROJECT_A, "trace-after-cap", "span-after-cap", NOW - timedelta(1))
    selector = AttributeReadSelector(
        RecordingExecutor(), now=NOW, json_attribute_mode="arrays"
    )
    monkeypatch.setattr(
        selector,
        "_candidate_ids",
        lambda *_args, **_kwargs: ((identity,), False, {identity: 1}),
    )
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **_kwargs: [
            _target_row(
                PROJECT_A,
                identity[2],
                trace_id=identity[1],
                start_time=identity[3],
                legacy_raw=json.dumps(["after-4096-a", "after-4096-b"]),
            )
        ],
    )
    seen = tuple(
        attribute_value_cursor_digest("string", f"prior-{index}")
        for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS - 1)
    )

    read = selector.read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=2,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        seen_value_digests=seen,
    )

    assert [row.value for row in read.rows] == ["after-4096-a", "after-4096-b"]
    assert len(read.seen_value_digests) == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1
    assert read.seen_value_digests[-2] == attribute_value_cursor_digest(
        "array", "after-4096-a"
    )
    assert read.seen_value_digests[-1] == attribute_value_cursor_digest(
        "array", "after-4096-b"
    )
    assert read.appended_value_digests == read.seen_value_digests[-2:]
    assert read.seen_value_count == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1
    assert read.has_more is True
    assert read.browse_status == "continuation"


def test_filter_value_cursor_tracks_and_continues_post_threshold_page():
    candidates = [
        _candidate(
            PROJECT_A,
            f"post-cap-{index}",
            trace_id=f"trace-post-cap-{index}",
            start_time=NOW - timedelta(minutes=index + 1),
        )
        for index in range(2)
    ]
    by_id = {str(row["id"]): row for row in candidates}

    def respond(call, _):
        if "segment_start" in call.params:
            in_segment = [
                row
                for row in candidates
                if call.params["segment_start"]
                <= row["start_time"]
                < call.params["segment_end"]
            ]
            return _keyset_candidate_page(in_segment, call)
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=by_id[span_id]["trace_id"],
                start_time=by_id[span_id]["start_time"],
                string="repeated-after-cap",
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    seen = tuple(
        attribute_value_cursor_digest("string", f"prior-{index}")
        for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS)
    )
    first = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=1,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        seen_value_digests=seen,
    )
    assert [row.value for row in first.rows] == ["repeated-after-cap"]
    emitted_digest = attribute_value_cursor_digest("string", "repeated-after-cap")
    assert first.seen_value_digests == (*seen, emitted_digest)
    assert first.appended_value_digests == (emitted_digest,)
    assert first.seen_value_count == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1
    assert first.next_before_identity == (
        PROJECT_A,
        "trace-post-cap-0",
        "post-cap-0",
        NOW - timedelta(minutes=1),
    )
    assert first.has_more is True
    assert first.browse_status == "continuation"


def test_filter_value_cursor_exact_typed_search_tracks_past_legacy_threshold(
    monkeypatch,
):
    identity = (PROJECT_A, "trace-search-cap", "span-search-cap", NOW - timedelta(1))
    selector = AttributeReadSelector(
        RecordingExecutor(), now=NOW, json_attribute_mode="arrays"
    )
    candidate_calls = []

    def candidates(*_args, **kwargs):
        candidate_calls.append(kwargs)
        return ((identity,), False, {identity: 1})

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest_typed_values",
        lambda *_args, **_kwargs: [
            _target_row(
                PROJECT_A,
                identity[2],
                trace_id=identity[1],
                start_time=identity[3],
                string="Rechazado",
            )
        ],
    )
    seen = tuple(
        attribute_value_cursor_digest("string", f"prior-{index}")
        for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS)
    )

    read = selector.read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="rechazado",
        attribute_type="string",
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        seen_value_digests=seen,
    )

    assert [row.value for row in read.rows] == ["Rechazado"]
    rechazada_digest = attribute_value_cursor_digest("string", "Rechazado")
    assert read.seen_value_digests == (*seen, rechazada_digest)
    assert read.appended_value_digests == (rechazada_digest,)
    assert read.seen_value_count == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1
    assert read.has_more is True
    assert read.browse_status == "continuation"
    assert candidate_calls[0]["predicate_params"] == {"attribute_search": "rechazado"}
    assert candidate_calls[0]["include_versions"] is True


def test_filter_value_cursor_candidate_uses_safe_first_attempt():
    candidate = _candidate(
        PROJECT_A,
        "candidate-budget-recovered",
        start_time=NOW - timedelta(seconds=1),
    )
    attempted_widths = []

    def respond(call, _):
        if "segment_start" in call.params:
            attempted_widths.append(
                call.params["segment_end"] - call.params["segment_start"]
            )
            if attempted_widths[-1] > ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT:
                return ReadDeadlineExceeded("candidate deadline")
            return (
                [candidate]
                if call.params["segment_start"]
                <= candidate["start_time"]
                < call.params["segment_end"]
                else []
            )
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    candidate["id"],
                    trace_id=candidate["trace_id"],
                    start_time=candidate["start_time"],
                    latest_version=candidate["candidate_version"],
                )
            ]
        return [
            _target_row(
                PROJECT_A,
                candidate["id"],
                trace_id=candidate["trace_id"],
                start_time=candidate["start_time"],
                string="recovered",
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="recovered",
        attribute_type="string",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    assert read.rows == (AttributeValueRow("recovered", "string", 1),)
    assert read.metadata.query_complete is True
    assert attempted_widths[:3] == [
        ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT * 2,
        ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
    ]
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert len(candidate_calls) == len(attempted_widths)
    assert all("candidate_before_id" not in call.params for call in candidate_calls)


def test_filter_value_cursor_replay_uses_safe_first_attempt():
    candidate = _candidate(
        PROJECT_A,
        "replay-budget-recovered",
        start_time=NOW - timedelta(seconds=1),
    )
    attempted_widths = []
    current_width = ATTRIBUTE_READ_EXPLICIT_SEGMENT

    def respond(call, _):
        nonlocal current_width
        if "segment_start" in call.params:
            current_width = call.params["segment_end"] - call.params["segment_start"]
            attempted_widths.append(current_width)
            return (
                [candidate]
                if call.params["segment_start"]
                <= candidate["start_time"]
                < call.params["segment_end"]
                else []
            )
        if current_width > ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT:
            return ReadDeadlineExceeded("verify deadline")
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    candidate["id"],
                    trace_id=candidate["trace_id"],
                    start_time=candidate["start_time"],
                    latest_version=candidate["candidate_version"],
                )
            ]
        return [
            _target_row(
                PROJECT_A,
                candidate["id"],
                trace_id=candidate["trace_id"],
                start_time=candidate["start_time"],
                string="recovered",
            )
        ]

    read = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="recovered",
        attribute_type="string",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    assert read.rows == (AttributeValueRow("recovered", "string", 1),)
    assert read.metadata.query_complete is True
    assert attempted_widths[0] == ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
    assert max(attempted_widths) > timedelta(hours=1)


def test_filter_value_cursor_grows_to_reach_next_adaptive_slice():
    minimum_width = ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
    candidate = _candidate(
        PROJECT_A,
        "adaptive-search-result",
        start_time=NOW - minimum_width - timedelta(seconds=1),
    )
    attempted_widths = []

    def respond(call, _):
        if "segment_start" in call.params:
            width = call.params["segment_end"] - call.params["segment_start"]
            attempted_widths.append(width)
            if (
                call.params["segment_start"]
                <= candidate["start_time"]
                < call.params["segment_end"]
            ):
                return [candidate]
            return []
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    candidate["id"],
                    trace_id=candidate["trace_id"],
                    start_time=candidate["start_time"],
                    latest_version=candidate["candidate_version"],
                )
            ]
        return [
            _target_row(
                PROJECT_A,
                candidate["id"],
                trace_id=candidate["trace_id"],
                start_time=candidate["start_time"],
                string="Rechazado",
            )
        ]

    first = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="Rechazado",
        attribute_type="string",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    assert first.rows == (AttributeValueRow("Rechazado", "string", 1),)
    assert first.has_more is False
    assert first.next_before_identity is None
    assert first.next_segment_end == NOW - timedelta(days=1)
    assert first.next_segment_start is None
    assert attempted_widths[:2] == [minimum_width, minimum_width * 2]
    assert max(attempted_widths) > timedelta(hours=1)


def test_filter_value_cursor_retries_later_replay_failure_at_safe_width():
    minimum_width = ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
    candidate = _candidate(
        PROJECT_A,
        "safe-replay-search-result",
        start_time=NOW - minimum_width - timedelta(seconds=1),
    )
    attempted_widths = []
    current_width = ATTRIBUTE_READ_EXPLICIT_SEGMENT
    replay_failures = 0

    def respond(call, _):
        nonlocal current_width
        nonlocal replay_failures
        if "segment_start" in call.params:
            current_width = call.params["segment_end"] - call.params["segment_start"]
            attempted_widths.append(current_width)
            if (
                call.params["segment_start"]
                <= candidate["start_time"]
                < call.params["segment_end"]
            ):
                return [candidate]
            return []
        if replay_failures == 0:
            replay_failures += 1
            return ReadDeadlineExceeded("one safe-width replay deadline")
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    candidate["id"],
                    trace_id=candidate["trace_id"],
                    start_time=candidate["start_time"],
                    latest_version=candidate["candidate_version"],
                )
            ]
        return [
            _target_row(
                PROJECT_A,
                candidate["id"],
                trace_id=candidate["trace_id"],
                start_time=candidate["start_time"],
                string="Rechazado",
            )
        ]

    first = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="Rechazado",
        attribute_type="string",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    assert first.rows == ()
    assert first.next_segment_end == NOW - minimum_width
    assert first.next_segment_start == NOW - (minimum_width * 2)
    assert attempted_widths == [minimum_width, minimum_width * 2]

    second_attempt_start = len(attempted_widths)
    second = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        search="Rechazado",
        attribute_type="string",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_end=first.next_segment_end,
        segment_start=first.next_segment_start,
        seen_value_digests=first.seen_value_digests,
    )

    assert second.rows == (AttributeValueRow("Rechazado", "string", 1),)
    assert replay_failures == 1
    assert attempted_widths[second_attempt_start] == minimum_width
    assert max(attempted_widths[second_attempt_start:]) > timedelta(hours=1)


@pytest.mark.parametrize(
    "segment_start",
    (
        NOW,
        NOW - timedelta(days=1, microseconds=1),
        NOW - ATTRIBUTE_VALUE_CURSOR_MAX_EMPTY_SEGMENT - timedelta(microseconds=1),
    ),
)
def test_filter_value_cursor_rejects_invalid_adaptive_segment(segment_start):
    with pytest.raises(ValueError, match="invalid filter-value segment cursor"):
        AttributeReadSelector(RecordingExecutor(), now=NOW).read_value_cursor_page(
            [PROJECT_A],
            "final_status",
            page_size=10,
            window_start=NOW - timedelta(days=1),
            window_end=NOW,
            segment_start=segment_start,
        )


def test_filter_value_cursor_adaptive_segment_must_contain_checkpoint():
    checkpoint = (
        PROJECT_A,
        "trace-before-adaptive-segment",
        "span-before-adaptive-segment",
        NOW - timedelta(minutes=10),
    )

    with pytest.raises(ValueError, match="invalid filter-value physical cursor"):
        AttributeReadSelector(RecordingExecutor(), now=NOW).read_value_cursor_page(
            [PROJECT_A],
            "final_status",
            page_size=10,
            window_start=NOW - timedelta(days=1),
            window_end=NOW,
            segment_start=NOW - timedelta(minutes=5),
            before_identity=checkpoint,
        )


@pytest.mark.parametrize("failure_stage", ["candidate", "replay"])
def test_filter_value_cursor_minimum_floor_failure_remains_fail_closed(
    failure_stage,
):
    candidate = _candidate(
        PROJECT_A,
        f"{failure_stage}-floor-failure",
        start_time=NOW - timedelta(minutes=1),
    )
    attempted_widths = []

    def respond(call, _):
        if "segment_start" in call.params:
            attempted_widths.append(
                call.params["segment_end"] - call.params["segment_start"]
            )
            if failure_stage == "candidate":
                return ReadDeadlineExceeded("candidate deadline")
            return [candidate]
        return ReadDeadlineExceeded("verify deadline")

    message = (
        "candidate deadline" if failure_stage == "candidate" else "verify deadline"
    )
    with pytest.raises(ReadDeadlineExceeded, match=message):
        AttributeReadSelector(
            RecordingExecutor(respond), now=NOW
        ).read_value_cursor_page(
            [PROJECT_A],
            "final_status",
            page_size=10,
            attribute_type="string",
            window_start=NOW - timedelta(days=1),
            window_end=NOW,
        )

    assert attempted_widths
    assert set(attempted_widths) == {ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT}


def test_filter_value_cursor_four_second_wall_returns_only_proven_progress():
    class ManualClock:
        value = 100.0

        def __call__(self):
            return self.value

    clock = ManualClock()
    attempted_segments = []

    def respond(call, _):
        assert "segment_start" in call.params
        attempted_segments.append(
            (call.params["segment_start"], call.params["segment_end"])
        )
        clock.value += 0.7
        return []

    read = AttributeReadSelector(
        RecordingExecutor(respond),
        now=NOW,
        wall_timeout_ms=4_000,
        clock=clock,
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        attribute_type="string",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    assert read.rows == ()
    assert read.has_more is True
    assert read.browse_status == "continuation"
    assert read.next_segment_end == attempted_segments[-1][0]
    assert read.next_segment_end < NOW
    assert all(
        newer_start == older_end
        for (newer_start, _), (_, older_end) in zip(
            attempted_segments,
            attempted_segments[1:],
            strict=False,
        )
    )
    assert clock.value - 100.0 < 4.0


def test_filter_value_cursor_four_second_wall_never_skips_unproven_frontier():
    class ManualClock:
        value = 100.0

        def __call__(self):
            return self.value

    clock = ManualClock()

    def respond(_call, _):
        clock.value += 4.1
        return ReadDeadlineExceeded("minimum slice exceeded the picker wall")

    with pytest.raises(
        ReadDeadlineExceeded,
        match="minimum slice exceeded the picker wall",
    ):
        AttributeReadSelector(
            RecordingExecutor(respond),
            now=NOW,
            wall_timeout_ms=4_000,
            clock=clock,
        ).read_value_cursor_page(
            [PROJECT_A],
            "final_status",
            page_size=10,
            attribute_type="string",
            window_start=NOW - timedelta(days=1),
            window_end=NOW,
        )


def test_filter_value_cursor_empty_legacy_frontier_adopts_lossless_five_second_seed():
    legacy_segment_end = NOW - timedelta(hours=12)
    candidate_segments = []

    def respond(call, _):
        assert "segment_start" in call.params
        candidate_segments.append(
            (call.params["segment_start"], call.params["segment_end"])
        )
        return []

    read = AttributeReadSelector(
        RecordingExecutor(respond),
        now=NOW,
        wall_timeout_ms=ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS,
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        attribute_type="string",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_end=legacy_segment_end,
    )

    first_start, first_end = candidate_segments[0]
    assert first_end == legacy_segment_end
    assert first_start == legacy_segment_end - ATTRIBUTE_VALUE_CURSOR_INITIAL_SEGMENT
    assert all(
        newer_start == older_end
        for (newer_start, _), (_, older_end) in zip(
            candidate_segments,
            candidate_segments[1:],
            strict=False,
        )
    )
    assert read.next_segment_end == NOW - timedelta(days=1)
    assert read.has_more is False
    assert read.browse_status == "exhausted"


def test_filter_value_cursor_persists_narrower_retry_when_request_budget_ends(
    monkeypatch,
):
    """A changed retry width is finite progress even before rows are consumed."""

    attempted_widths = []

    def respond(call, _):
        assert "segment_start" in call.params
        attempted_widths.append(
            call.params["segment_end"] - call.params["segment_start"]
        )
        return ReadDeadlineExceeded("wide candidate deadline")

    # Admit the first typed candidate statement, but leave no query budget for
    # its five-second retry.  The response must persist that narrower strategy
    # instead of returning a repeated cursor or a 503.  A later request that
    # also fails at the persisted floor is covered by the fail-closed test
    # above.
    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads.ATTRIBUTE_READ_MAX_QUERY_COUNT",
        3,
    )
    read = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=10,
        attribute_type="string",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_start=NOW - ATTRIBUTE_READ_EXPLICIT_SEGMENT,
    )

    assert read.rows == ()
    assert read.has_more is True
    assert read.browse_status == "continuation"
    assert read.next_segment_end == NOW
    assert read.next_segment_start == NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
    assert attempted_widths == [ATTRIBUTE_READ_EXPLICIT_SEGMENT]


def test_filter_value_cursor_resume_budget_failure_never_advances_cursor():
    resume_identity = (
        PROJECT_A,
        "trace-resume-budget",
        "resume-budget",
        NOW - timedelta(minutes=1),
    )
    executor = RecordingExecutor(lambda *_: ReadDeadlineExceeded("resume deadline"))

    with pytest.raises(ReadDeadlineExceeded, match="resume deadline"):
        AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
            [PROJECT_A],
            "final_status",
            page_size=10,
            window_start=NOW - timedelta(days=1),
            window_end=NOW,
            resume_identity=resume_identity,
            resume_member_offset=1,
        )

    assert len(executor.calls) == 1
    assert "segment_start" not in executor.calls[0].params


def test_filter_value_cursor_full_url_stays_below_common_request_line_limit():
    search = "x" * 512
    project_id = "00000000-0000-4000-8000-000000000005"
    seen_reference = ("state", "a" * 64)
    scope = {
        "principal_id": "00000000-0000-4000-8000-000000000001",
        "auth_type": "TokenAuthentication",
        "auth_id": "00000000-0000-4000-8000-000000000002",
        "organization_id": "00000000-0000-4000-8000-000000000003",
        "workspace_id": "00000000-0000-4000-8000-000000000004",
        "project_ids": [project_id],
    }
    cursor_query = {
        "metric_name": "final_status",
        "metric_type": "custom_attribute",
        "source": "traces",
        "project_ids": [project_id],
        "search": search,
    }
    cursor = encode_list_cursor(
        resource="dashboard_filter_values",
        scope=scope,
        query=cursor_query,
        page_size=50,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        order=(NOW, (), (), 0, seen_reference),
        seen_rows=1_000_000,
        scan_slice_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        scan_slice_end=NOW,
    )
    full_url = "https://api.futureagi.com/tracer/dashboard/filter_values/?" + urlencode(
        {
            "metric_name": "final_status",
            "metric_type": "custom_attribute",
            "project_ids": project_id,
            "source": "traces",
            "page_size": 50,
            "search": search,
            "cursor": cursor,
        }
    )

    assert len(full_url.encode("utf-8")) < 8 * 1024


def test_filter_value_cursor_resumes_mid_array_without_skipping_members():
    candidate = _candidate(
        PROJECT_A,
        "array-row",
        trace_id="trace-array-row",
        start_time=NOW - timedelta(minutes=1),
    )
    members = ("one", "two", "three", "four", "five")

    def respond(call, _):
        if "segment_start" in call.params:
            rows = (
                [candidate]
                if call.params["segment_start"]
                <= candidate["start_time"]
                < call.params["segment_end"]
                else []
            )
            return _keyset_candidate_page(rows, call)
        return [
            _target_row(
                PROJECT_A,
                "array-row",
                trace_id="trace-array-row",
                start_time=candidate["start_time"],
                legacy_raw=json.dumps(members),
            )
        ]

    first = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=2,
        window_start=NOW - timedelta(hours=6),
        window_end=NOW,
    )
    assert [row.value for row in first.rows] == ["one", "two"]
    assert first.next_resume_identity == (
        PROJECT_A,
        "trace-array-row",
        "array-row",
        candidate["start_time"],
    )
    assert first.next_resume_member_offset > ATTRIBUTE_READ_MAX_VALUES + 1

    second = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=2,
        window_start=NOW - timedelta(hours=6),
        window_end=NOW,
        segment_end=first.next_segment_end,
        before_identity=first.next_before_identity,
        resume_identity=first.next_resume_identity,
        resume_member_offset=first.next_resume_member_offset,
        seen_value_digests=first.seen_value_digests,
    )
    assert [row.value for row in second.rows] == ["three", "four"]
    assert second.next_resume_member_offset > ATTRIBUTE_READ_MAX_VALUES + 1
    assert second.next_resume_member_offset != first.next_resume_member_offset

    third = AttributeReadSelector(
        RecordingExecutor(respond), now=NOW, json_attribute_mode="arrays"
    ).read_value_cursor_page(
        [PROJECT_A],
        "final_status",
        page_size=2,
        window_start=NOW - timedelta(hours=6),
        window_end=NOW,
        segment_end=second.next_segment_end,
        before_identity=second.next_before_identity,
        resume_identity=second.next_resume_identity,
        resume_member_offset=second.next_resume_member_offset,
        seen_value_digests=second.seen_value_digests,
    )
    assert [row.value for row in third.rows] == ["five"]
    assert third.next_resume_identity is None
    assert third.has_more is False
    assert [row.value for page in (first, second, third) for row in page.rows] == list(
        members
    )


def _json_array_cursor_executor(candidate, raw_value):
    def respond(call, _call_number):
        if "segment_start" in call.params:
            rows = (
                [candidate]
                if call.params["segment_start"]
                <= candidate["start_time"]
                < call.params["segment_end"]
                else []
            )
            return _keyset_candidate_page(rows, call)
        resolved_raw = raw_value() if callable(raw_value) else raw_value
        return [
            _target_row(
                PROJECT_A,
                str(candidate["id"]),
                trace_id=str(candidate["trace_id"]),
                start_time=candidate["start_time"],
                legacy_raw=resolved_raw,
            )
        ]

    return RecordingExecutor(respond)


def _read_json_array_cursor_pages(
    executor,
    *,
    page_size,
    search=None,
    max_pages=100,
):
    cursor = {}
    reads = []
    for _ in range(max_pages):
        read = AttributeReadSelector(
            executor,
            now=NOW,
            json_attribute_mode="arrays",
        ).read_value_cursor_page(
            [PROJECT_A],
            "json.array",
            page_size=page_size,
            window_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
            window_end=NOW,
            search=search,
            attribute_type="array",
            **cursor,
        )
        reads.append(read)
        if not read.has_more:
            break
        cursor = {
            "segment_end": read.next_segment_end,
            "before_identity": read.next_before_identity,
            "resume_identity": read.next_resume_identity,
            "resume_member_offset": read.next_resume_member_offset,
            "seen_value_digests": read.seen_value_digests,
        }
    else:
        pytest.fail("JSON array cursor did not exhaust inside the test page bound")
    return reads


def test_filter_value_cursor_reaches_every_member_beyond_legacy_500_cap():
    candidate = _candidate(
        PROJECT_A,
        "large-array",
        start_time=NOW - timedelta(seconds=1),
    )
    members = [f"member-{index:04d}" for index in range(623)]
    executor = _json_array_cursor_executor(candidate, json.dumps(members))
    reads = _read_json_array_cursor_pages(
        executor,
        page_size=50,
    )

    assert [row.value for read in reads for row in read.rows] == members
    assert len(reads) == 13
    assert all(read.metadata.query_complete for read in reads)
    assert reads[-1].has_more is False
    assert reads[0].next_resume_member_offset > ATTRIBUTE_READ_MAX_VALUES + 1
    assert any(
        "tupleElement(latest_state, 13) AS legacy_value_raw" in call.sql
        and "AS legacy_value_fingerprint" in call.sql
        for call in executor.calls
    )
    # The packed fingerprint/checkpoint stays tiny inside the signed HTTP
    # cursor even after replacing the old one-to-three digit member offset.
    token = encode_list_cursor(
        resource="dashboard_filter_values",
        scope={"project_ids": [PROJECT_A]},
        query={"metric_name": "json.array"},
        page_size=50,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        window_end=NOW,
        order=(
            reads[0].next_segment_end,
            (),
            reads[0].next_resume_identity or (),
            reads[0].next_resume_member_offset,
            (),
        ),
        seen_rows=50,
    )
    assert len(token.encode("utf-8")) < 16 * 1024


def test_filter_value_cursor_pages_total_string_bytes_and_skips_only_oversized_member():
    candidate = _candidate(
        PROJECT_A,
        "byte-bounded-array",
        start_time=NOW - timedelta(seconds=1),
    )
    valid_members = [
        f"{index:02d}" + "x" * (JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES - 2)
        for index in range(19)
    ]
    oversized = "z" * (JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES + 1)
    members = [*valid_members[:8], oversized, *valid_members[8:], "tail"]
    reads = _read_json_array_cursor_pages(
        _json_array_cursor_executor(candidate, json.dumps(members)),
        page_size=50,
    )

    assert JSON_ARRAY_FILTER_MAX_TOTAL_STRING_UTF8_BYTES == (
        16 * JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES
    )
    assert [row.value for read in reads for row in read.rows] == [
        *valid_members,
        "tail",
    ]
    assert len(reads) >= 2
    assert oversized not in [row.value for read in reads for row in read.rows]


def test_filter_value_cursor_search_reaches_and_deduplicates_matches_after_500():
    candidate = _candidate(
        PROJECT_A,
        "searched-large-array",
        start_time=NOW - timedelta(seconds=1),
    )
    members = [f"noise-{index:04d}" for index in range(550)]
    members.extend(["needle-first", "needle-first", "other", "needle-second"])
    reads = _read_json_array_cursor_pages(
        _json_array_cursor_executor(candidate, json.dumps(members)),
        page_size=1,
        search="NeEdLe",
    )

    assert [row.value for read in reads for row in read.rows] == [
        "needle-first",
        "needle-second",
    ]


def test_filter_value_cursor_search_checkpoints_deep_duplicate_free_prefix():
    candidate = _candidate(
        PROJECT_A,
        "deep-searched-array",
        start_time=NOW - timedelta(seconds=1),
    )
    members = [f"noise-{index:05d}" for index in range(5_000)]
    members.append("deep-needle")
    reads = _read_json_array_cursor_pages(
        _json_array_cursor_executor(candidate, json.dumps(members)),
        page_size=1,
        search="needle",
    )

    assert [row.value for read in reads for row in read.rows] == ["deep-needle"]
    assert len(reads) >= 3
    assert all(
        read.next_resume_member_offset > ATTRIBUTE_READ_MAX_VALUES + 1
        for read in reads
        if read.next_resume_identity is not None
    )


def test_filter_value_cursor_skips_huge_unfilterable_string_incrementally():
    candidate = _candidate(
        PROJECT_A,
        "huge-string-array",
        start_time=NOW - timedelta(seconds=1),
    )
    huge_unfilterable = "x" * (600 * 1024)
    reads = _read_json_array_cursor_pages(
        _json_array_cursor_executor(
            candidate,
            json.dumps([huge_unfilterable, "reachable-tail"]),
        ),
        page_size=1,
    )

    assert [row.value for read in reads for row in read.rows] == ["reachable-tail"]
    assert len(reads) >= 4
    assert all(
        read.next_resume_member_offset > ATTRIBUTE_READ_MAX_VALUES + 1
        for read in reads
        if read.next_resume_identity is not None
    )


def test_filter_value_cursor_array_mutation_restarts_without_skip_or_repeat():
    candidate = _candidate(
        PROJECT_A,
        "mutating-array",
        start_time=NOW - timedelta(seconds=1),
    )
    raw = {"value": json.dumps(["one", "two", "three"])}
    executor = _json_array_cursor_executor(candidate, lambda: raw["value"])
    first = AttributeReadSelector(
        executor,
        now=NOW,
        json_attribute_mode="arrays",
    ).read_value_cursor_page(
        [PROJECT_A],
        "json.array",
        page_size=1,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        window_end=NOW,
        attribute_type="array",
    )
    raw["value"] = json.dumps(["new-head", "one", "two", "three"])
    cursor = {
        "segment_end": first.next_segment_end,
        "before_identity": first.next_before_identity,
        "resume_identity": first.next_resume_identity,
        "resume_member_offset": first.next_resume_member_offset,
        "seen_value_digests": first.seen_value_digests,
    }
    following = []
    for _ in range(4):
        read = AttributeReadSelector(
            executor,
            now=NOW,
            json_attribute_mode="arrays",
        ).read_value_cursor_page(
            [PROJECT_A],
            "json.array",
            page_size=1,
            window_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
            window_end=NOW,
            attribute_type="array",
            **cursor,
        )
        following.append(read)
        if not read.has_more:
            break
        cursor = {
            "segment_end": read.next_segment_end,
            "before_identity": read.next_before_identity,
            "resume_identity": read.next_resume_identity,
            "resume_member_offset": read.next_resume_member_offset,
            "seen_value_digests": read.seen_value_digests,
        }

    assert [row.value for row in first.rows] == ["one"]
    assert [row.value for read in following for row in read.rows] == [
        "new-head",
        "two",
        "three",
    ]
    assert following[-1].has_more is False


def test_filter_value_cursor_shorter_seen_array_mutation_exhausts_without_loop():
    candidate = _candidate(
        PROJECT_A,
        "shortened-array",
        start_time=NOW - timedelta(seconds=1),
    )
    raw = {"value": json.dumps(["one", "two", "three"])}
    executor = _json_array_cursor_executor(candidate, lambda: raw["value"])
    first = AttributeReadSelector(
        executor,
        now=NOW,
        json_attribute_mode="arrays",
    ).read_value_cursor_page(
        [PROJECT_A],
        "json.array",
        page_size=1,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        window_end=NOW,
        attribute_type="array",
    )
    raw["value"] = json.dumps(["one"])

    second = AttributeReadSelector(
        executor,
        now=NOW,
        json_attribute_mode="arrays",
    ).read_value_cursor_page(
        [PROJECT_A],
        "json.array",
        page_size=1,
        window_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
        window_end=NOW,
        segment_end=first.next_segment_end,
        before_identity=first.next_before_identity,
        resume_identity=first.next_resume_identity,
        resume_member_offset=first.next_resume_member_offset,
        seen_value_digests=first.seen_value_digests,
        attribute_type="array",
    )

    assert [row.value for row in first.rows] == ["one"]
    assert second.rows == ()
    assert second.has_more is False
    assert second.browse_status == "exhausted"


def test_filter_value_cursor_rejects_malformed_packed_array_offset():
    candidate = _candidate(
        PROJECT_A,
        "malformed-array-offset",
        start_time=NOW - timedelta(seconds=1),
    )
    identity = (
        PROJECT_A,
        str(candidate["trace_id"]),
        str(candidate["id"]),
        candidate["start_time"],
    )

    with pytest.raises(ValueError, match="invalid filter-value JSON member cursor"):
        AttributeReadSelector(
            _json_array_cursor_executor(candidate, json.dumps(["one", "two"])),
            now=NOW,
            json_attribute_mode="arrays",
        ).read_value_cursor_page(
            [PROJECT_A],
            "json.array",
            page_size=1,
            window_start=NOW - ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
            window_end=NOW,
            resume_identity=identity,
            resume_member_offset=10**200,
            attribute_type="array",
        )


def test_typed_value_version_certificate_narrows_hydration_to_current_live_rows():
    candidates = [
        _candidate(
            PROJECT_A,
            span_id,
            trace_id=f"trace-{span_id}",
            start_time=NOW - timedelta(hours=1, seconds=index),
        )
        for index, span_id in enumerate(
            (
                "active-string",
                "active-number",
                "active-boolean",
                "cleared-one",
                "cleared-two",
                "tombstoned-one",
                "tombstoned-two",
                "cleared-three",
                "truncation-sentinel",
            )
        )
    ]
    by_id = {str(row["id"]): row for row in candidates}
    active_ids = {"active-string", "active-number", "active-boolean"}
    tombstoned_ids = {"tombstoned-one", "tombstoned-two"}

    def certificate_row(span_id: str) -> dict[str, Any]:
        candidate = by_id[span_id]
        return {
            "project_id": PROJECT_A,
            "trace_id": candidate["trace_id"],
            "id": span_id,
            "start_time": candidate["start_time"],
            "is_deleted": int(span_id in tombstoned_ids),
            "latest_version": 1 if span_id in active_ids else 2,
        }

    def respond(call, _):
        if "segment_start" in call.params:
            return candidates
        requested_ids = call.params["candidate_ids_0"]
        if "max(_version) AS latest_version" in call.sql:
            return [certificate_row(span_id) for span_id in requested_ids]
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=by_id[span_id]["trace_id"],
                start_time=by_id[span_id]["start_time"],
                string="Rejected" if span_id == "active-string" else None,
                number=42 if span_id == "active-number" else None,
                boolean=True if span_id == "active-boolean" else None,
            )
            for span_id in requested_ids
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).read_values(
        [PROJECT_A],
        "final_status",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    assert {(row.value, row.type, row.count) for row in read.rows} == {
        ("Rejected", "string", 1),
        (42.0, "number", 1),
        (True, "boolean", 1),
    }
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_count == 3

    candidate_call = executor.calls[0]
    certificate_call = executor.calls[1]
    hydration_call = executor.calls[2]
    assert "toUInt64(_version) AS candidate_version" in candidate_call.sql
    for family in ("string", "number", "bool"):
        assert f"attrs_{family}[%(attribute_key)s]" not in candidate_call.sql
        assert f"attrs_{family}" not in certificate_call.sql
        assert f"attrs_{family}[%(attribute_key)s]" in hydration_call.sql
    assert "attributes_extra" not in candidate_call.sql
    assert "attributes_extra" not in certificate_call.sql
    assert "attribute_key" not in certificate_call.params

    expected_active_identities = {
        (
            by_id[span_id]["trace_id"],
            span_id,
            _unix_microseconds(by_id[span_id]["start_time"]),
        )
        for span_id in active_ids
    }
    assert (
        set(hydration_call.params["candidate_physical_identities_0"])
        == expected_active_identities
    )
    assert set(hydration_call.params["candidate_ids_0"]) == active_ids


def test_typed_value_candidate_deduplicates_to_highest_raw_version():
    older = _candidate(
        PROJECT_A,
        "duplicate-version",
        trace_id="trace-duplicate-version",
        start_time=NOW - timedelta(hours=1),
        candidate_version=2,
    )
    newer = {**older, "candidate_version": 7}

    def respond(call, _):
        if "segment_start" in call.params:
            return [older, newer]
        if "max(_version) AS latest_version" in call.sql:
            assert call.params["candidate_ids_0"] == ("duplicate-version",)
            return [
                _target_row(
                    PROJECT_A,
                    "duplicate-version",
                    trace_id="trace-duplicate-version",
                    start_time=older["start_time"],
                    latest_version=7,
                )
            ]
        return [
            _target_row(
                PROJECT_A,
                "duplicate-version",
                trace_id="trace-duplicate-version",
                start_time=older["start_time"],
                string="Rejected",
                latest_version=7,
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).read_values(
        [PROJECT_A],
        "final_status",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    assert read.rows == (AttributeValueRow("Rejected", "string", 1),)
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_count == 3
    assert len(executor.calls) == 3


def test_typed_value_all_stale_versions_skip_value_hydration():
    candidates = [
        _candidate(
            PROJECT_A,
            span_id,
            trace_id=f"trace-{span_id}",
            start_time=NOW - timedelta(hours=1, seconds=index),
        )
        for index, span_id in enumerate(("cleared", "tombstoned"))
    ]
    by_id = {str(row["id"]): row for row in candidates}

    def respond(call, _):
        if "segment_start" in call.params:
            if not (
                call.params["segment_start"]
                <= candidates[0]["start_time"]
                < call.params["segment_end"]
            ):
                return []
            return candidates
        return [
            {
                "project_id": PROJECT_A,
                "trace_id": by_id[span_id]["trace_id"],
                "id": span_id,
                "start_time": by_id[span_id]["start_time"],
                "is_deleted": int(span_id == "tombstoned"),
                "latest_version": 2,
            }
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).read_values(
        [PROJECT_A],
        "final_status",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    assert read.rows == ()
    assert read.metadata.query_status == "complete"
    assert read.metadata.query_count == 5
    assert len(executor.calls) == 5
    certificate_call = executor.calls[1]
    assert "max(_version) AS latest_version" in certificate_call.sql
    assert "attrs_" not in certificate_call.sql
    assert "attributes_extra" not in certificate_call.sql


def test_explicit_window_json_value_runs_after_all_typed_bands_are_empty():
    json_emitted = False

    def respond(call, _):
        nonlocal json_emitted
        if "segment_start" in call.params:
            if "candidate_version" in call.sql or json_emitted:
                return []
            json_emitted = True
            return [
                _candidate(
                    PROJECT_A,
                    "json-array",
                    start_time=NOW - timedelta(hours=1),
                )
            ]
        return [
            _target_row(
                PROJECT_A,
                "json-array",
                start_time=NOW - timedelta(hours=1),
                legacy_raw='["accepted"]',
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="arrays",
    ).read_values(
        [PROJECT_A],
        "json_array",
        search="CEPT",
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )

    assert read.rows == (AttributeValueRow("accepted", "array", 1),)
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert len(candidate_calls) == ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT - 1
    assert all("candidate_version" in call.sql for call in candidate_calls[:-1])
    assert "candidate_version" not in candidate_calls[-1].sql
    assert all("attributes_extra" not in call.sql for call in candidate_calls)
    assert all("JSONHas(attributes_extra" not in call.sql for call in candidate_calls)
    assert all("attribute_search" not in call.params for call in candidate_calls)
    first_json_call = candidate_calls[-1]
    assert first_json_call.settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
    assert "max_rows_to_read" not in first_json_call.settings
    assert first_json_call.settings["max_block_size"] == 2_048
    json_hydration = next(
        call
        for call in executor.calls
        if "segment_start" not in call.params and "JSONHas(attributes_extra" in call.sql
    )
    assert len(json_hydration.params["candidate_ids_0"]) == 1


def test_stale_typed_page_reaches_live_typed_continuation_with_json_enabled():
    stale_candidates = [
        _candidate(
            PROJECT_A,
            f"stale-before-live-{index:02d}",
            start_time=NOW - timedelta(hours=1, seconds=index),
        )
        for index in range(ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1)
    ]
    live_candidate = _candidate(
        PROJECT_A,
        "live-typed-continuation",
        start_time=NOW - timedelta(hours=2),
    )
    rows_by_id = {str(row["id"]): row for row in [*stale_candidates, live_candidate]}
    unordered_typed_calls = 0

    def respond(call, _):
        nonlocal unordered_typed_calls
        if "segment_start" in call.params:
            if "candidate_version" not in call.sql:
                pytest.fail("JSON sampling ran after a live typed continuation")
            if "LIMIT 1 BY" in call.sql:
                return [live_candidate]
            unordered_typed_calls += 1
            return stale_candidates if unordered_typed_calls == 1 else []
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    span_id,
                    trace_id=rows_by_id[span_id]["trace_id"],
                    start_time=rows_by_id[span_id]["start_time"],
                    latest_version=(1 if span_id == "live-typed-continuation" else 2),
                )
                for span_id in call.params["candidate_ids_0"]
            ]
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=rows_by_id[span_id]["trace_id"],
                start_time=rows_by_id[span_id]["start_time"],
                string="Rechazado",
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        json_attribute_mode="arrays",
    ).read_values(
        [PROJECT_A],
        "final_status",
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )

    assert read.rows == (AttributeValueRow("Rechazado", "string", 1),)
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert sum("LIMIT 1 BY" in call.sql for call in candidate_calls) == 1
    assert all("candidate_version" in call.sql for call in candidate_calls)
    assert len(candidate_calls) == ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT - 1


def test_stale_typed_continuation_cannot_starve_live_json_value():
    stale_candidates = [
        _candidate(
            PROJECT_A,
            f"stale-typed-{index:02d}",
            start_time=NOW - timedelta(hours=1, seconds=index),
        )
        for index in range(ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1)
    ]
    stale_by_id = {str(row["id"]): row for row in stale_candidates}
    typed_candidate_calls = 0

    def respond(call, _):
        nonlocal typed_candidate_calls
        if "segment_start" in call.params:
            if "candidate_version" in call.sql:
                typed_candidate_calls += 1
                return stale_candidates if typed_candidate_calls == 1 else []
            return [
                _candidate(
                    PROJECT_A,
                    "live-json-array",
                    start_time=NOW - timedelta(hours=1),
                )
            ]
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    span_id,
                    trace_id=stale_by_id[span_id]["trace_id"],
                    start_time=stale_by_id[span_id]["start_time"],
                    latest_version=2,
                )
                for span_id in call.params["candidate_ids_0"]
            ]
        return [
            _target_row(
                PROJECT_A,
                "live-json-array",
                start_time=NOW - timedelta(hours=1),
                legacy_raw='["accepted"]',
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="arrays",
    ).read_values(
        [PROJECT_A],
        "json_array",
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )

    assert read.rows == (AttributeValueRow("accepted", "array", 1),)
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    continuation_index = next(
        index
        for index, call in enumerate(candidate_calls)
        if "candidate_version" in call.sql and "LIMIT 1 BY" in call.sql
    )
    json_candidate_indexes = [
        index
        for index, call in enumerate(candidate_calls)
        if "candidate_version" not in call.sql
    ]
    assert json_candidate_indexes == [len(candidate_calls) - 1]
    assert continuation_index < json_candidate_indexes[0]
    assert len(candidate_calls) == ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT
    assert all("attributes_extra" not in call.sql for call in candidate_calls)


def test_older_typed_value_is_verified_before_json_overflow_lane():
    windows = adaptive_attribute_windows(NOW)
    recent = _candidate(
        PROJECT_A,
        "recent-cleared",
        start_time=NOW - timedelta(days=1),
    )
    older = _candidate(
        PROJECT_A,
        "older-live",
        start_time=NOW - timedelta(days=10),
    )
    candidates = {str(row["id"]): row for row in (recent, older)}

    def respond(call, _):
        if "segment_start" in call.params:
            if "JSONHas(attributes_extra" in call.sql:
                pytest.fail("JSON overflow ran before a verified typed sample")
            if call.params["segment_start"] == windows[0][0]:
                return [recent]
            if call.params["segment_start"] == windows[1][0]:
                return [older]
            return []
        requested_ids = call.params["candidate_ids_0"]
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    span_id,
                    start_time=candidates[span_id]["start_time"],
                    latest_version=2 if span_id == "recent-cleared" else 1,
                )
                for span_id in requested_ids
            ]
        return [
            _target_row(
                PROJECT_A,
                span_id,
                start_time=candidates[span_id]["start_time"],
                string="Rejected",
            )
            for span_id in requested_ids
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).read_values(
        [PROJECT_A], "final_status"
    )

    assert read.rows == (AttributeValueRow("Rejected", "string", 1),)
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert [call.params["segment_start"] for call in candidate_calls] == [
        segment[0] for segment in windows
    ]
    assert all("JSONHas(attributes_extra" not in call.sql for call in candidate_calls)


def test_value_search_pages_past_seed_stale_matches_to_live_value():
    stale = [
        _candidate(
            PROJECT_A,
            f"stale-value-{index:04d}",
            trace_id=f"trace-stale-value-{index:04d}",
            start_time=NOW - timedelta(seconds=index + 1),
        )
        for index in range(ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT)
    ]
    live = _candidate(
        PROJECT_A,
        "live-value",
        trace_id="trace-live-value",
        start_time=NOW - timedelta(seconds=ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1),
    )
    rows_by_id = {str(row["id"]): row for row in [*stale, live]}
    recent_start = adaptive_attribute_windows(NOW)[0][0]

    def respond(call, _):
        if "segment_start" in call.params:
            if call.params["segment_start"] != recent_start:
                return []
            return _keyset_candidate_page([*stale, live], call)
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=rows_by_id[span_id]["trace_id"],
                start_time=rows_by_id[span_id]["start_time"],
                string="Rejected" if span_id == "live-value" else None,
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).read_values([PROJECT_A], "final_status", search="Rejected")

    assert read.rows == (AttributeValueRow("Rejected", "string", 1),)
    assert read.metadata.query_complete is True
    candidates = [call for call in executor.calls if "segment_start" in call.params]
    assert all("attribute_search" not in call.params for call in candidates)
    assert all(
        call.params["candidate_limit"] == ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1
        for call in candidates
    )
    assert all("positionCaseInsensitiveUTF8" not in call.sql for call in candidates)
    assert all(
        "indexHint(has(mapKeys(attrs_string), %(attribute_key)s))" in call.sql
        and "has(attrs_string.keys, %(attribute_key)s)" in call.sql
        for call in candidates
    )
    continuation = next(
        call for call in candidates if "candidate_before_start_us" in call.params
    )
    assert continuation.params["candidate_before_id"] == stale[-1]["id"]
    assert all(
        "toUInt64(_version) AS candidate_version" in call.sql for call in candidates
    )
    ordered_call = next(call for call in candidates if "LIMIT 1 BY" in call.sql)
    assert ordered_call.sql.index("_version DESC") < ordered_call.sql.index(
        "LIMIT 1 BY"
    )


def test_incomplete_global_latest_replay_fails_closed_without_retry():
    def respond(call, _):
        if "segment_start" in call.params:
            return [
                _candidate(PROJECT_A, "one"),
                _candidate(PROJECT_A, "two"),
            ]
        return [_target_row(PROJECT_A, "one", string="partial-must-be-discarded")]

    executor = RecordingExecutor(respond)
    selector = AttributeReadSelector(executor, now=NOW)

    with pytest.raises(IncompleteLatestStateReplay):
        selector.read_values([PROJECT_A], "final_status")

    assert len(executor.calls) == 2


def test_global_replay_resource_failure_discards_partial_and_does_not_retry():
    def respond(call, _):
        if "segment_start" in call.params:
            return [_candidate(PROJECT_A, "one")]
        return ServerException("private SQL fragment", 307)

    executor = RecordingExecutor(respond)
    selector = AttributeReadSelector(executor, now=NOW)

    with pytest.raises(ServerException) as raised:
        selector.read_values([PROJECT_A], "final_status")

    assert raised.value.code == 307
    assert len(executor.calls) == 2
    assert selector.query_count == 2


@pytest.mark.parametrize("code", [241, 307])
def test_json_budget_failure_keeps_verified_typed_key_inventory_usable(code: int):
    recent_start = adaptive_attribute_windows(NOW)[0][0]

    def respond(call, _):
        if "segment_start" in call.params:
            if "attributes_extra NOT IN" in call.sql:
                return ServerException("private JSON lane failure", code)
            if call.params["segment_start"] == recent_start:
                return [_candidate(PROJECT_A, "typed-final-status")]
            return []
        return [
            {
                "project_id": PROJECT_A,
                "trace_id": f"trace-{PROJECT_A}-typed-final-status",
                "id": "typed-final-status",
                "start_time": NOW - timedelta(days=1),
                "is_deleted": 0,
                "string_keys": ["final_status"],
                "number_keys": [],
                "boolean_keys": [],
            }
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).discover_keys([PROJECT_A])

    assert read.rows == (AttributeKeyRow("final_status", "string", 1),)
    assert read.metadata.query_complete is False
    assert read.metadata.query_error_code == "sample_limit"
    json_call = next(
        call for call in executor.calls if "attributes_extra NOT IN" in call.sql
    )
    assert 0 < json_call.timeout_ms <= ATTRIBUTE_READ_JSON_QUERY_TIMEOUT_MS
    typed_call = next(
        call for call in executor.calls if "length(attrs_string.keys)" in call.sql
    )
    assert "attributes_extra" not in typed_call.sql


def test_verified_typed_searched_value_skips_json_budget_risk():
    recent_start = adaptive_attribute_windows(NOW)[0][0]

    def respond(call, _):
        if "segment_start" in call.params:
            if "JSONHas(attributes_extra" in call.sql:
                pytest.fail("JSON overflow ran after a verified typed sample")
            if call.params["segment_start"] == recent_start:
                return [_candidate(PROJECT_A, "typed-rejected")]
            return []
        return [_target_row(PROJECT_A, "typed-rejected", string="Rejected")]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).read_values(
        [PROJECT_A],
        "final_status",
        search="Rejected",
    )

    assert read.rows == (AttributeValueRow("Rejected", "string", 1),)
    assert read.metadata.query_complete is False
    assert read.metadata.query_error_code == "sample_limit"
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    assert all(
        not (
            "mapContains(attrs_string" in call.sql
            and "JSONHas(attributes_extra" in call.sql
        )
        for call in candidate_calls
    )
    assert all("JSONHas(attributes_extra" not in call.sql for call in candidate_calls)


def test_absent_heavy_json_key_uses_only_bounded_identity_seeds():
    starts_by_id: dict[str, datetime] = {}
    json_candidate_page = 0

    def respond(call, _):
        nonlocal json_candidate_page
        if "segment_start" in call.params:
            if "candidate_version" in call.sql:
                return []
            page = json_candidate_page
            json_candidate_page += 1
            rows = [
                _candidate(
                    PROJECT_A,
                    f"raw-json-{page:02d}-{index:02d}",
                    start_time=call.params["segment_start"]
                    + timedelta(seconds=index + 1),
                )
                for index in range(ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1)
            ]
            starts_by_id.update((str(row["id"]), row["start_time"]) for row in rows)
            return rows

        return [
            _target_row(
                PROJECT_A,
                span_id,
                start_time=starts_by_id[span_id],
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).read_values(
        [PROJECT_A], "absent_heavy_key"
    )

    assert read.rows == ()
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "degraded"
    assert read.metadata.query_error_code == "sample_limit"
    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    windows = adaptive_attribute_windows(NOW)
    assert len(candidate_calls) == 2 * len(windows)
    assert all("attributes_extra" not in call.sql for call in candidate_calls)
    assert all("JSONHas(attributes_extra" not in call.sql for call in candidate_calls)
    json_calls = [
        call for call in candidate_calls if "candidate_version" not in call.sql
    ]
    assert len(json_calls) == len(windows)
    assert all(
        0 < call.timeout_ms <= ATTRIBUTE_READ_JSON_QUERY_TIMEOUT_MS
        for call in json_calls
    )
    assert all(
        call.settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
        and "max_rows_to_read" not in call.settings
        and call.settings["max_block_size"] == 2_048
        for call in json_calls
    )
    hydration_calls = [
        call
        for call in executor.calls
        if "segment_start" not in call.params and "JSONHas(attributes_extra" in call.sql
    ]
    assert len(hydration_calls) == len(windows)
    assert all(
        len(call.params["candidate_ids_0"]) <= ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT
        for call in hydration_calls
    )
    # Five typed seeds plus one JSON identity seed and exact hydration for
    # each of the five adaptive bands. No JSON continuation can consume the
    # 30-query safety ceiling while chasing an absent key.
    assert read.metadata.query_count == 3 * len(windows)
    assert len(executor.calls) == read.metadata.query_count


def test_explicit_seven_day_json_miss_reserves_one_bounded_json_sample():
    starts_by_id: dict[str, datetime] = {}
    json_candidate_page = 0

    def respond(call, _):
        nonlocal json_candidate_page
        if "segment_start" in call.params:
            if "candidate_version" in call.sql:
                return []
            page = json_candidate_page
            json_candidate_page += 1
            rows = [
                _candidate(
                    PROJECT_A,
                    f"daily-json-{page:02d}-{index:02d}",
                    start_time=call.params["segment_start"]
                    + timedelta(seconds=index + 1),
                )
                for index in range(ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1)
            ]
            starts_by_id.update((str(row["id"]), row["start_time"]) for row in rows)
            return rows
        return [
            _target_row(
                PROJECT_A,
                span_id,
                start_time=starts_by_id[span_id],
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="arrays",
    ).read_values(
        [PROJECT_A],
        "absent_json_array",
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
    )

    candidate_calls = [
        call for call in executor.calls if "segment_start" in call.params
    ]
    json_calls = [
        call for call in candidate_calls if "candidate_version" not in call.sql
    ]
    hydration_calls = [
        call
        for call in executor.calls
        if "segment_start" not in call.params and "JSONHas(attributes_extra" in call.sql
    ]
    assert read.rows == ()
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "degraded"
    assert read.metadata.query_error_code == "sample_limit"
    assert len(candidate_calls) == ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT - 1
    assert len(json_calls) == 1
    assert len(hydration_calls) == 1
    assert read.metadata.query_count == 15
    assert len(executor.calls) == 15


def test_timeout_on_first_segment_has_no_retry():
    executor = RecordingExecutor(
        lambda *_: ReadDeadlineExceeded("private deadline detail")
    )
    selector = AttributeReadSelector(executor, now=NOW)

    with pytest.raises(ReadDeadlineExceeded):
        selector.discover_keys([PROJECT_A])

    assert len(executor.calls) == 1


def test_later_budget_timeout_keeps_replayed_inventory_and_marks_it_degraded(
    monkeypatch,
):
    warning = MagicMock()
    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads.logger",
        SimpleNamespace(warning=warning),
    )

    def respond(call, call_number):
        if call_number == 1:
            return [_candidate(PROJECT_A, "recent")]
        if call_number == 2:
            return [
                {
                    "project_id": PROJECT_A,
                    "id": "recent",
                    "start_time": NOW - timedelta(days=1),
                    "is_deleted": 0,
                    "trace_id": f"trace-{PROJECT_A}-recent",
                    "trace_session_id": "",
                    "parent_span_id": "",
                    "string_keys": ["final_status"],
                    "number_keys": [],
                    "boolean_keys": [],
                    "attributes_extra": "{}",
                }
            ]
        return ReadDeadlineExceeded("private deadline detail")

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).discover_keys([PROJECT_A])

    assert read.rows == (AttributeKeyRow("final_status", "string", 1),)
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "degraded"
    assert read.metadata.query_error_code == "read_budget_exceeded"
    assert read.metadata.query_window_start == NOW - timedelta(days=7)
    assert read.metadata.query_count == 3
    warning.assert_called_once_with(
        "attribute_read_partial_budget_exceeded",
        operation="discover_keys",
        query_count=3,
    )


def test_each_public_operation_starts_fresh_wall_budget_at_call_boundary():
    class ManualClock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = ManualClock()
    executor = RecordingExecutor()
    selector = AttributeReadSelector(
        executor,
        now=NOW,
        clock=clock,
        typed_only=True,
    )

    # Object construction can precede request dispatch without consuming the
    # operation's four-second wall budget.
    clock.value = 100.0
    selector.discover_keys([PROJECT_A], exact_key="first")
    assert selector.query_count == 5

    # A second public operation on the same selector gets a fresh budget and
    # query counter; its own adaptive queries still share that one deadline.
    clock.value = 200.0
    selector.read_values([PROJECT_A], "second")
    assert selector.query_count == 5
    assert len(executor.calls) == 10


def test_candidate_sample_cap_is_explicitly_degraded_and_query_count_bounded():
    starts_by_id: dict[str, datetime] = {}
    recent_start = adaptive_attribute_windows(NOW)[0][0]
    recent_page = 0

    def respond(call, _):
        nonlocal recent_page
        if "segment_start" in call.params:
            if call.params["segment_start"] != recent_start:
                return []
            page = recent_page
            recent_page += 1
            rows = [
                _candidate(
                    PROJECT_A,
                    f"span-{page:02d}-{index:04d}",
                    trace_id=f"trace-span-{page:02d}-{index:04d}",
                    start_time=NOW - timedelta(seconds=page * 1_000 + index + 1),
                )
                for index in range(ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1)
            ]
            starts_by_id.update((str(row["id"]), row["start_time"]) for row in rows)
            return rows
        return [
            _target_row(
                PROJECT_A,
                span_id,
                trace_id=f"trace-{span_id}",
                start_time=starts_by_id[span_id],
                string="same",
            )
            for span_id in call.params["candidate_ids_0"]
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
    ).read_values([PROJECT_A], "sampled")

    assert read.rows == (
        AttributeValueRow(
            "same",
            "string",
            ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT,
        ),
    )
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_count <= (
        2 * ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT
    )


def test_value_search_miss_never_exceeds_hard_query_ceiling():
    starts_by_id: dict[str, datetime] = {}
    typed_candidate_page = 0

    def respond(call, _):
        nonlocal typed_candidate_page
        if "segment_start" in call.params:
            if "JSONHas(attributes_extra" in call.sql:
                return []
            page = typed_candidate_page
            typed_candidate_page += 1
            rows = [
                _candidate(
                    PROJECT_A,
                    f"miss-{page:02d}-{index:02d}",
                    start_time=NOW - timedelta(days=1, seconds=page * 100 + index + 1),
                )
                for index in range(ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT + 1)
            ]
            starts_by_id.update((str(row["id"]), row["start_time"]) for row in rows)
            return rows

        requested_ids = call.params["candidate_ids_0"]
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    span_id,
                    start_time=starts_by_id[span_id],
                    latest_version=1,
                )
                for span_id in requested_ids
            ]
        return [
            _target_row(
                PROJECT_A,
                span_id,
                start_time=starts_by_id[span_id],
                string="does-not-match",
            )
            for span_id in requested_ids
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(
        executor,
        now=NOW,
        wall_timeout_ms=600_000,
    ).read_values([PROJECT_A], "final_status", search="needle")

    assert read.rows == ()
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "degraded"
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_count == ATTRIBUTE_READ_MAX_QUERY_COUNT
    assert len(executor.calls) == ATTRIBUTE_READ_MAX_QUERY_COUNT


def test_query_ceiling_retains_values_but_never_claims_complete(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads.ATTRIBUTE_READ_MAX_QUERY_COUNT",
        4,
    )
    candidate = _candidate(PROJECT_A, "retained-before-cap")

    def respond(call, call_number):
        if "segment_start" in call.params:
            return [candidate] if call_number == 1 else []
        if "max(_version) AS latest_version" in call.sql:
            return [
                _target_row(
                    PROJECT_A,
                    "retained-before-cap",
                    start_time=candidate["start_time"],
                    latest_version=1,
                )
            ]
        return [
            _target_row(
                PROJECT_A,
                "retained-before-cap",
                start_time=candidate["start_time"],
                string="Rejected",
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).read_values(
        [PROJECT_A], "final_status"
    )

    assert read.rows == (AttributeValueRow("Rejected", "string", 1),)
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "sampled"
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_count == 4
    assert len(executor.calls) == 4


def test_query_ceiling_without_decoded_values_returns_degraded(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads.ATTRIBUTE_READ_MAX_QUERY_COUNT",
        4,
    )
    candidate = _candidate(PROJECT_A, "empty-before-cap")

    def respond(call, call_number):
        if "segment_start" in call.params:
            return [candidate] if call_number == 1 else []
        return [
            _target_row(
                PROJECT_A,
                "empty-before-cap",
                start_time=candidate["start_time"],
                latest_version=1,
            )
        ]

    executor = RecordingExecutor(respond)
    read = AttributeReadSelector(executor, now=NOW).read_values(
        [PROJECT_A], "final_status"
    )

    assert read.rows == ()
    assert read.metadata.query_complete is False
    assert read.metadata.query_status == "degraded"
    assert read.metadata.query_error_code == "sample_limit"
    assert read.metadata.query_count == 4
    assert len(executor.calls) == 4


def test_malformed_keys_and_oversized_project_scopes_fail_before_ch():
    executor = RecordingExecutor()
    selector = AttributeReadSelector(executor, now=NOW)

    for key in ("", "contains\x00control", "é" * 257):
        with pytest.raises(InvalidAttributeKey):
            selector.read_values([PROJECT_A], key)
    assert validate_attribute_key("customer.%_status\\路径'quote") == (
        "customer.%_status\\路径'quote"
    )
    too_many_projects = [
        str(uuid.uuid4()) for _ in range(ATTRIBUTE_READ_MAX_PROJECTS + 1)
    ]
    with pytest.raises(IncompleteLatestStateReplay):
        selector.discover_keys(too_many_projects)
    assert executor.calls == []


def test_span_attribute_keys_contract_accepts_exact_probe_and_read_state():
    project_id = uuid.uuid4()
    query = SpanAttributeProjectQuerySerializer(
        data={"project_id": project_id, "q": "final_status", "page_size": 10}
    )

    assert query.is_valid(), query.errors
    assert query.validated_data["q"] == "final_status"
    assert query.validated_data["page_size"] == 10
    assert query.validated_data["discovery_mode"] == "filter"
    eval_query = SpanAttributeProjectQuerySerializer(
        data={
            "project_id": project_id,
            "page_size": 10,
            "discovery_mode": "eval_mapping",
        }
    )
    assert eval_query.is_valid(), eval_query.errors
    assert eval_query.validated_data["discovery_mode"] == "eval_mapping"
    workspace_query = SpanAttributeProjectQuerySerializer(
        data={"workspace_scope": True, "page_size": 50}
    )
    assert workspace_query.is_valid(), workspace_query.errors
    assert workspace_query.validated_data["workspace_scope"] is True
    for invalid_scope in (
        {},
        {"workspace_scope": True},
        {
            "project_id": project_id,
            "workspace_scope": True,
            "page_size": 50,
        },
    ):
        invalid_query = SpanAttributeProjectQuerySerializer(data=invalid_scope)
        assert not invalid_query.is_valid()
    assert {
        "query_complete",
        "query_status",
        "query_error_code",
        "query_window_start",
        "query_window_end",
        "total_count",
        "has_more",
        "next_cursor",
        "browse_mode",
        "browse_status",
        "browse_limit",
        "lookup_mode",
        "exact_match",
    } <= set(SpanAttributeKeysResponseSerializer().fields)


def test_span_attribute_key_cursor_pages_resume_wide_rows_without_duplicates(
    monkeypatch,
):
    identity = (PROJECT_A, "trace-1", "span-1", NOW - timedelta(hours=1))
    row = {
        "project_id": PROJECT_A,
        "trace_id": "trace-1",
        "id": "span-1",
        "start_time": identity[3],
        "is_deleted": 0,
        "string_keys": ["alpha", "beta"],
        "number_keys": ["alpha"],
        "boolean_keys": [],
        "attributes_extra": "{}",
    }
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    monkeypatch.setattr(
        selector,
        "_candidate_ids",
        lambda *_args, **_kwargs: ((identity,), False, {}),
    )
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **_kwargs: [row],
    )

    first = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=1,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )
    second = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=1,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        segment_end=first.next_segment_end,
        before_identity=first.next_before_identity,
        resume_identity=first.next_resume_identity,
        resume_key_offset=first.next_resume_key_offset,
        seen_key_digests=first.seen_key_digests,
    )

    assert [(item.key, item.type) for item in first.rows] == [("alpha", "string")]
    assert [(item.key, item.type) for item in second.rows] == [("beta", "string")]
    assert second.seen_key_digests == (
        attribute_key_cursor_digest("alpha"),
        attribute_key_cursor_digest("beta"),
    )
    assert first.next_before_identity is None
    assert first.next_resume_identity == identity
    assert first.browse_status == "continuation"
    assert first.metadata.query_status == "complete"
    assert first.metadata.query_error_code is None
    assert len(second.seen_key_digests) == 2


def test_workspace_key_cursor_merges_new_types_and_dedupes_repeated_type_rows(
    monkeypatch,
):
    identities = tuple(
        (
            PROJECT_A,
            f"trace-workspace-type-{index}",
            f"span-workspace-type-{index}",
            NOW - timedelta(minutes=index + 1),
        )
        for index in range(3)
    )
    rows = {
        identities[0]: {
            "project_id": PROJECT_A,
            "trace_id": identities[0][1],
            "id": identities[0][2],
            "start_time": identities[0][3],
            "is_deleted": 0,
            "string_keys": ["migrated.attribute"],
            "number_keys": [],
            "boolean_keys": [],
            "attributes_extra": "{}",
        },
        identities[1]: {
            "project_id": PROJECT_A,
            "trace_id": identities[1][1],
            "id": identities[1][2],
            "start_time": identities[1][3],
            "is_deleted": 0,
            "string_keys": ["migrated.attribute"],
            "number_keys": [],
            "boolean_keys": [],
            "attributes_extra": "{}",
        },
        identities[2]: {
            "project_id": PROJECT_A,
            "trace_id": identities[2][1],
            "id": identities[2][2],
            "start_time": identities[2][3],
            "is_deleted": 0,
            "string_keys": [],
            "number_keys": ["migrated.attribute"],
            "boolean_keys": [],
            "attributes_extra": "{}",
        },
    }
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    monkeypatch.setattr(
        selector,
        "_candidate_ids",
        lambda *_args, **_kwargs: (identities, False, {}),
    )
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **kwargs: [
            rows[identity] for identity in kwargs["candidate_ids"]
        ],
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(minutes=5),
        window_end=NOW,
        dedupe_by_type=True,
    )

    assert [(row.key, row.types) for row in page.rows] == [
        ("migrated.attribute", ("string", "number"))
    ]
    assert page.appended_key_digests == (
        attribute_key_type_cursor_digest("migrated.attribute", "string"),
        attribute_key_type_cursor_digest("migrated.attribute", "number"),
    )
    assert len(page.appended_key_digests) == len(set(page.appended_key_digests))
    assert page.seen_key_count == 2
    assert page.has_more is False


def test_workspace_exact_key_cursor_exhausts_same_batch_type_families(monkeypatch):
    identities = tuple(
        (
            PROJECT_A,
            f"trace-workspace-exact-{index}",
            f"span-workspace-exact-{index}",
            NOW - timedelta(minutes=index + 1),
        )
        for index in range(3)
    )
    rows = {
        identities[0]: _target_row(
            PROJECT_A,
            identities[0][2],
            trace_id=identities[0][1],
            start_time=identities[0][3],
            string="first",
        ),
        identities[1]: _target_row(
            PROJECT_A,
            identities[1][2],
            trace_id=identities[1][1],
            start_time=identities[1][3],
            string="second",
        ),
        identities[2]: _target_row(
            PROJECT_A,
            identities[2][2],
            trace_id=identities[2][1],
            start_time=identities[2][3],
            number=7,
        ),
    }
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    monkeypatch.setattr(
        selector,
        "_candidate_ids",
        lambda *_args, **_kwargs: (identities, False, {}),
    )
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **kwargs: [
            rows[identity] for identity in kwargs["candidate_ids"]
        ],
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(minutes=5),
        window_end=NOW,
        exact_key="migrated.attribute",
        dedupe_by_type=True,
        exhaustive_exact_types=True,
    )

    assert [(row.key, row.types) for row in page.rows] == [
        ("migrated.attribute", ("string", "number"))
    ]
    assert page.appended_key_digests == (
        attribute_key_type_cursor_digest("migrated.attribute", "string"),
        attribute_key_type_cursor_digest("migrated.attribute", "number"),
    )
    assert len(page.appended_key_digests) == len(set(page.appended_key_digests))
    assert page.seen_key_count == 2
    assert page.has_more is False
    assert page.browse_status == "exhausted"


def test_span_attribute_key_cursor_remains_pageable_below_state_capacity(monkeypatch):
    identity = (PROJECT_A, "trace-cap", "span-cap", NOW - timedelta(hours=1))
    row = {
        "project_id": PROJECT_A,
        "trace_id": identity[1],
        "id": identity[2],
        "start_time": identity[3],
        "is_deleted": 0,
        "string_keys": ["last_recent_key"],
        "number_keys": [],
        "boolean_keys": [],
        "attributes_extra": "{}",
    }
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    monkeypatch.setattr(
        selector,
        "_candidate_ids",
        lambda *_args, **_kwargs: ((identity,), False, {}),
    )
    monkeypatch.setattr(selector, "_verify_latest", lambda *_args, **_kwargs: [row])
    seen = tuple(attribute_key_cursor_digest(f"prior-{index}") for index in range(300))

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        seen_key_digests=seen,
    )

    assert [item.key for item in page.rows] == ["last_recent_key"]
    assert len(page.seen_key_digests) == 301
    assert page.has_more is True
    assert page.browse_status == "continuation"
    assert page.metadata.query_complete is True
    assert page.metadata.query_status == "complete"
    assert page.metadata.query_error_code is None


def test_span_attribute_key_cursor_reaches_and_continues_at_legacy_threshold(
    monkeypatch,
):
    identity = (PROJECT_A, "trace-cap", "span-cap", NOW - timedelta(hours=1))
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    monkeypatch.setattr(
        selector,
        "_candidate_ids",
        lambda *_args, **_kwargs: ((identity,), False, {}),
    )
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **_kwargs: [
            {
                "project_id": PROJECT_A,
                "trace_id": identity[1],
                "id": identity[2],
                "start_time": identity[3],
                "is_deleted": 0,
                "string_keys": ["last_recent_key"],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": "{}",
            }
        ],
    )
    seen = tuple(
        attribute_key_cursor_digest(f"prior-{index}")
        for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS - 1)
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        seen_key_digests=seen,
    )

    assert [item.key for item in page.rows] == ["last_recent_key"]
    assert len(page.seen_key_digests) == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS
    assert page.appended_key_digests == (page.seen_key_digests[-1],)
    assert page.seen_key_count == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS
    assert page.has_more is True
    assert page.browse_status == "continuation"


def test_span_attribute_key_cursor_tracks_continuation_past_legacy_threshold(
    monkeypatch,
):
    identity = (PROJECT_A, "trace-cap", "span-cap", NOW - timedelta(hours=1))
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    monkeypatch.setattr(
        selector,
        "_candidate_ids",
        lambda *_args, **_kwargs: ((identity,), False, {}),
    )
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **_kwargs: [
            {
                "project_id": PROJECT_A,
                "trace_id": identity[1],
                "id": identity[2],
                "start_time": identity[3],
                "is_deleted": 0,
                "string_keys": ["key_after_tracked_prefix"],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": "{}",
            }
        ],
    )
    seen = tuple(
        attribute_key_cursor_digest(f"prior-{index}")
        for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS)
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        seen_key_digests=seen,
    )

    assert [item.key for item in page.rows] == ["key_after_tracked_prefix"]
    emitted_digest = attribute_key_cursor_digest("key_after_tracked_prefix")
    assert page.seen_key_digests == (*seen, emitted_digest)
    assert page.appended_key_digests == (emitted_digest,)
    assert page.seen_key_count == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1
    assert page.has_more is True
    assert page.browse_status == "continuation"


def test_span_attribute_key_cursor_dedupes_across_slices_past_legacy_threshold(
    monkeypatch,
):
    duplicate_key = "already_seen_key"
    unique_key = "older_unique_key"
    duplicate_identity = (
        PROJECT_A,
        "trace-duplicate-slice",
        "span-duplicate-slice",
        NOW - timedelta(hours=1),
    )
    unique_identity = (
        PROJECT_A,
        "trace-unique-slice",
        "span-unique-slice",
        NOW - timedelta(hours=7),
    )
    identities = (duplicate_identity, unique_identity)
    rows = {
        duplicate_identity: {
            "project_id": PROJECT_A,
            "trace_id": duplicate_identity[1],
            "id": duplicate_identity[2],
            "start_time": duplicate_identity[3],
            "is_deleted": 0,
            "string_keys": [duplicate_key],
            "number_keys": [],
            "boolean_keys": [],
            "attributes_extra": "{}",
        },
        unique_identity: {
            "project_id": PROJECT_A,
            "trace_id": unique_identity[1],
            "id": unique_identity[2],
            "start_time": unique_identity[3],
            "is_deleted": 0,
            "string_keys": [unique_key],
            "number_keys": [],
            "boolean_keys": [],
            "attributes_extra": "{}",
        },
    }
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    scanned_segments: list[tuple[datetime, datetime]] = []

    def candidates(_projects, segment, **_kwargs):
        scanned_segments.append(segment)
        return (
            tuple(
                identity
                for identity in identities
                if segment[0] <= identity[3] < segment[1]
            ),
            False,
            {},
        )

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **kwargs: [
            rows[identity] for identity in kwargs.get("candidate_ids", ())
        ],
    )
    seen = (
        *(
            attribute_key_cursor_digest(f"prior-{index}")
            for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS - 1)
        ),
        attribute_key_cursor_digest(duplicate_key),
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=1,
        window_start=NOW - timedelta(hours=18),
        window_end=NOW,
        seen_key_digests=seen,
    )

    assert [item.key for item in page.rows] == [unique_key]
    unique_digest = attribute_key_cursor_digest(unique_key)
    assert page.seen_key_digests == (*seen, unique_digest)
    assert page.appended_key_digests == (unique_digest,)
    assert page.seen_key_count == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1
    assert page.has_more is True
    assert page.browse_status == "continuation"
    assert len(scanned_segments) >= 2
    assert scanned_segments[0] != scanned_segments[1]


def test_span_attribute_key_cursor_skips_duplicate_only_page_after_first_state_block(
    monkeypatch,
):
    duplicate_key = "repeated_after_first_block"
    unique_key = "older_unique_key"
    duplicate_identities = tuple(
        (
            PROJECT_A,
            f"trace-duplicate-{index}",
            f"span-duplicate-{index}",
            NOW - timedelta(seconds=index + 1),
        )
        for index in range(1_000)
    )
    unique_identity = (
        PROJECT_A,
        "trace-unique",
        "span-unique",
        NOW - timedelta(seconds=len(duplicate_identities) + 1),
    )
    ordered_identities = (*duplicate_identities, unique_identity)
    identity_indexes = {
        identity: index for index, identity in enumerate(ordered_identities)
    }
    rows = {
        identity: {
            "project_id": PROJECT_A,
            "trace_id": identity[1],
            "id": identity[2],
            "start_time": identity[3],
            "is_deleted": 0,
            "string_keys": [
                unique_key if identity == unique_identity else duplicate_key
            ],
            "number_keys": [],
            "boolean_keys": [],
            "attributes_extra": "{}",
        }
        for identity in ordered_identities
    }
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    candidate_limits: list[int] = []

    def candidates(_projects, segment, **kwargs):
        before_identity = kwargs.get("before_identity")
        candidate_limits.append(kwargs["candidate_limit"])
        matches = [
            identity
            for identity in ordered_identities
            if segment[0] <= identity[3] < segment[1]
            and (
                before_identity is None
                or identity_indexes[identity] > identity_indexes[before_identity]
            )
        ]
        if not matches:
            return (), False, {}
        limit = kwargs["candidate_limit"]
        return (
            tuple(matches[:limit]),
            len(matches) > limit,
            {},
        )

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **kwargs: [
            rows[identity] for identity in kwargs.get("candidate_ids", ())
        ],
    )
    seen = (
        *(
            attribute_key_cursor_digest(f"prior-{index}")
            for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS - 1)
        ),
        attribute_key_cursor_digest(duplicate_key),
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        seen_key_digests=seen,
    )

    assert [item.key for item in page.rows] == [unique_key]
    unique_digest = attribute_key_cursor_digest(unique_key)
    assert page.seen_key_digests == (*seen, unique_digest)
    assert page.appended_key_digests == (unique_digest,)
    assert page.seen_key_count == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1
    assert page.has_more is False
    assert page.browse_status == "exhausted"
    assert candidate_limits[:5] == [
        64,
        128,
        256,
        ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_LIMIT,
        ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_LIMIT,
    ]


def test_span_attribute_key_cursor_collapses_empty_historical_suffix(monkeypatch):
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    widths: list[timedelta] = []
    timeouts: list[int | None] = []

    def candidates(_projects, segment, **kwargs):
        widths.append(segment[1] - segment[0])
        timeouts.append(kwargs.get("query_timeout_ms"))
        return (), False, {}

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", lambda *_args, **_kwargs: [])

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=3650),
        window_end=NOW,
    )

    assert page.rows == ()
    assert page.has_more is False
    assert page.browse_status == "exhausted"
    assert widths[0] == ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
    assert all(
        later == earlier * 2
        for earlier, later in zip(widths, widths[1:-1], strict=False)
    )
    assert max(widths) > timedelta(days=60)
    assert ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS in timeouts
    assert len(widths) < ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_PAGES


def test_span_attribute_key_cursor_sizes_initial_dense_read_to_public_page(
    monkeypatch,
):
    identities = tuple(
        (
            PROJECT_A,
            f"trace-page-{index}",
            f"span-page-{index}",
            NOW - timedelta(seconds=index + 1),
        )
        for index in range(10)
    )
    candidate_calls: list[tuple[timedelta, int]] = []
    replay_sizes: list[int] = []
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )

    def candidates(_projects, segment, **kwargs):
        candidate_calls.append((segment[1] - segment[0], kwargs["candidate_limit"]))
        return identities, True, {}

    def verify(*_args, **kwargs):
        candidate_ids = kwargs["candidate_ids"]
        replay_sizes.append(len(candidate_ids))
        return [
            {
                "project_id": identity[0],
                "trace_id": identity[1],
                "id": identity[2],
                "start_time": identity[3],
                "is_deleted": 0,
                "string_keys": [f"key-{index}"],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": "{}",
            }
            for index, identity in enumerate(candidate_ids)
        ]

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", verify)

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert candidate_calls == [(ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT, 10)]
    assert replay_sizes == [10]
    assert [row.key for row in page.rows] == [f"key-{index}" for index in range(10)]
    assert page.has_more is True
    assert page.next_before_identity == identities[-1]


@pytest.mark.parametrize("failure_stage", ["candidate", "replay"])
def test_span_attribute_key_cursor_retries_expanded_batch_without_moving_checkpoint(
    monkeypatch, failure_stage
):
    duplicate_key = "already_seen"
    unique_key = "reachable_after_expanded_retry"
    identities = tuple(
        (
            PROJECT_A,
            f"trace-retry-{index}",
            f"span-retry-{index}",
            NOW - timedelta(seconds=index + 1),
        )
        for index in range(100)
    )
    unique_identity = (
        PROJECT_A,
        "trace-retry-unique",
        "span-retry-unique",
        NOW - timedelta(seconds=len(identities) + 1),
    )
    ordered_identities = (*identities, unique_identity)
    identity_indexes = {
        identity: index for index, identity in enumerate(ordered_identities)
    }
    rows = {
        identity: {
            "project_id": PROJECT_A,
            "trace_id": identity[1],
            "id": identity[2],
            "start_time": identity[3],
            "is_deleted": 0,
            "string_keys": [
                unique_key if identity == unique_identity else duplicate_key
            ],
            "number_keys": [],
            "boolean_keys": [],
            "attributes_extra": "{}",
        }
        for identity in ordered_identities
    }
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    calls: list[tuple[int, tuple[str, str, str, datetime] | None, int | None]] = []
    replay_calls: list[tuple[int, int | None]] = []

    def candidates(_projects, segment, **kwargs):
        limit = kwargs["candidate_limit"]
        before_identity = kwargs.get("before_identity")
        calls.append((limit, before_identity, kwargs.get("query_timeout_ms")))
        if limit > 64 and failure_stage == "candidate":
            raise ReadDeadlineExceeded("expanded replay exceeded its short budget")
        matches = [
            identity
            for identity in ordered_identities
            if segment[0] <= identity[3] < segment[1]
            and (
                before_identity is None
                or identity_indexes[identity] > identity_indexes[before_identity]
            )
        ]
        return tuple(matches[:limit]), len(matches) > limit, {}

    monkeypatch.setattr(selector, "_candidate_ids", candidates)

    def verify(*_args, **kwargs):
        candidate_ids = kwargs.get("candidate_ids", ())
        timeout_ms = kwargs.get("query_timeout_ms")
        replay_calls.append((len(candidate_ids), timeout_ms))
        if (
            failure_stage == "replay"
            and timeout_ms == ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
        ):
            raise ReadDeadlineExceeded("expanded replay exceeded its short budget")
        return [rows[identity] for identity in candidate_ids]

    monkeypatch.setattr(selector, "_verify_latest", verify)

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        seen_key_digests=(attribute_key_cursor_digest(duplicate_key),),
    )

    assert [row.key for row in page.rows] == [unique_key]
    assert calls[0] == (
        64,
        None,
        ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS,
    )
    if failure_stage == "candidate":
        assert calls[1] == (
            128,
            identities[63],
            ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS,
        )
        assert calls[2] == (
            64,
            identities[63],
            ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS,
        )
    else:
        assert len(calls) > 2
        assert all(
            limit <= ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
            and timeout == ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
            for limit, _checkpoint, timeout in calls
        )
        checkpoints = [checkpoint for _limit, checkpoint, _timeout in calls[1:]]
        physical_checkpoints = [
            checkpoint for checkpoint in checkpoints if checkpoint is not None
        ]
        assert len(physical_checkpoints) == len(set(physical_checkpoints))
        assert [
            identity_indexes[checkpoint] for checkpoint in physical_checkpoints
        ] == (
            sorted(identity_indexes[checkpoint] for checkpoint in physical_checkpoints)
        )
        speculative_indexes = [
            index
            for index, (_size, timeout) in enumerate(replay_calls)
            if timeout == ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
        ]
        assert speculative_indexes
        assert all(
            index + 1 < len(replay_calls)
            and replay_calls[index + 1][1] is None
            and replay_calls[index + 1][0] <= replay_calls[index][0]
            for index in speculative_indexes
        )


@pytest.mark.parametrize(
    "failure_code",
    [
        pytest.param(None, id="timeout"),
        pytest.param(396, id="max-result-bytes"),
    ],
)
def test_span_attribute_key_cursor_recuts_base_replay_without_skipping_keys(
    monkeypatch, failure_code
):
    identities = tuple(
        (
            PROJECT_A,
            f"trace-wide-{index}",
            f"span-wide-{index}",
            NOW - timedelta(seconds=index + 1),
        )
        for index in range(70)
    )
    identity_indexes = {identity: index for index, identity in enumerate(identities)}
    rows = {
        identity: {
            "project_id": PROJECT_A,
            "trace_id": identity[1],
            "id": identity[2],
            "start_time": identity[3],
            "is_deleted": 0,
            "string_keys": [f"wide_key_{index:02d}"],
            "number_keys": [],
            "boolean_keys": [],
            "attributes_extra": "{}",
        }
        for index, identity in enumerate(identities)
    }
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    candidate_calls = []

    def candidates(_projects, segment, **kwargs):
        before_identity = kwargs.get("before_identity")
        limit = kwargs["candidate_limit"]
        candidate_calls.append((limit, before_identity))
        matches = [
            identity
            for identity in identities
            if segment[0] <= identity[3] < segment[1]
            and (
                before_identity is None
                or identity_indexes[identity] > identity_indexes[before_identity]
            )
        ]
        return tuple(matches[:limit]), len(matches) > limit, {}

    def verify(*_args, **kwargs):
        candidate_ids = kwargs.get("candidate_ids", ())
        if len(candidate_ids) > 32:
            if failure_code is None:
                raise ReadDeadlineExceeded("wide replay exceeded its deadline")
            raise ServerException("wide replay exceeded max_result_bytes", failure_code)
        return [rows[identity] for identity in candidate_ids]

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", verify)

    first = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=2,
        window_start=NOW - timedelta(hours=6),
        window_end=NOW,
    )
    second = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=2,
        window_start=NOW - timedelta(hours=6),
        window_end=NOW,
        segment_end=first.next_segment_end,
        segment_start=first.next_segment_start,
        before_identity=first.next_before_identity,
        resume_identity=first.next_resume_identity,
        resume_key_offset=first.next_resume_key_offset,
        seen_key_digests=first.seen_key_digests,
    )

    assert candidate_calls[0] == (2, None)
    assert candidate_calls[1:] == [(64, identities[1])]
    assert [row.key for row in first.rows] == ["wide_key_00", "wide_key_01"]
    assert [row.key for row in second.rows] == ["wide_key_02", "wide_key_03"]
    assert set(first.seen_key_digests).isdisjoint(second.appended_key_digests)


def test_span_attribute_key_cursor_exact_search_continues_until_verified_match(
    monkeypatch,
):
    identity = (PROJECT_A, "trace-search", "span-search", NOW - timedelta(hours=1))
    phase = {"match": False}
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    monkeypatch.setattr(
        selector,
        "_candidate_ids",
        lambda *_args, **_kwargs: ((identity,), True, {}),
    )

    def verify(*_args, **_kwargs):
        return [
            {
                "project_id": PROJECT_A,
                "trace_id": identity[1],
                "id": identity[2],
                "start_time": identity[3],
                "is_deleted": 0,
                "string_keys": [
                    "final_status" if phase["match"] else "another_attribute"
                ],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": "{}",
            }
        ]

    monkeypatch.setattr(selector, "_verify_latest", verify)

    first = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        exact_key="final_status",
    )
    assert first.rows == ()
    assert first.has_more is True
    assert first.browse_status == "continuation"
    assert first.next_before_identity == identity

    phase["match"] = True
    second = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        segment_end=first.next_segment_end,
        before_identity=first.next_before_identity,
        seen_key_digests=first.seen_key_digests,
        exact_key="final_status",
    )
    assert [row.key for row in second.rows] == ["final_status"]
    assert second.has_more is False
    assert second.browse_status == "exhausted"


def test_span_attribute_key_cursor_exact_json_continuation_is_bounded_and_unique(
    monkeypatch,
):
    stale_identity = (
        PROJECT_A,
        "trace-json-stale",
        "span-z-json-stale",
        NOW - timedelta(minutes=1),
    )
    match_identity = (
        PROJECT_A,
        "trace-json-match",
        "span-a-json-match",
        stale_identity[3],
    )
    ordered_candidates = []
    ordered_calls = []
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )

    def candidates(_projects, segment, **kwargs):
        if not kwargs["ordered"]:
            return (), False, {}
        ordered_calls.append((segment, kwargs))
        before_identity = kwargs.get("before_identity")
        identity = stale_identity if before_identity is None else match_identity
        ordered_candidates.append(identity)
        return (identity,), before_identity is None, {}

    def verify(*_args, **kwargs):
        rows = []
        for identity in kwargs.get("candidate_ids", ()):
            rows.append(
                {
                    "project_id": PROJECT_A,
                    "trace_id": identity[1],
                    "id": identity[2],
                    "start_time": identity[3],
                    "is_deleted": 0,
                    "string_keys": [],
                    "number_keys": [],
                    "boolean_keys": [],
                    # The first physical candidate represents a stale version:
                    # exact candidate discovery may admit it, but latest-state
                    # replay must not publish it or lose the following match.
                    "attributes_extra": (
                        "{}"
                        if identity == stale_identity
                        else '{"json_only":{"nested":"value"}}'
                    ),
                }
            )
        return rows

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", verify)
    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads."
        "ATTRIBUTE_KEY_CURSOR_EXACT_MAX_CANDIDATE_PAGES",
        2,
    )

    first = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        exact_key="json_only",
    )

    assert first.rows == ()
    assert first.has_more is True
    assert first.next_before_identity == stale_identity

    second = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        segment_end=first.next_segment_end,
        segment_start=first.next_segment_start,
        before_identity=first.next_before_identity,
        resume_identity=first.next_resume_identity,
        resume_key_offset=first.next_resume_key_offset,
        seen_key_digests=first.seen_key_digests,
        exact_key="json_only",
    )

    assert [row.key for row in second.rows] == ["json_only"]
    assert second.has_more is False
    assert second.browse_status == "exhausted"
    assert ordered_candidates == [stale_identity, match_identity]
    assert len(set(ordered_candidates)) == len(ordered_candidates)
    assert all(
        end - start <= ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
        for (start, end), _kwargs in ordered_calls
    )
    assert all(
        kwargs["attribute_key"] == "json_only"
        and "JSONHas(attributes_extra, %(attribute_key)s)" in kwargs["predicate"]
        and "attributes_extra NOT IN" not in kwargs["predicate"]
        for _segment, kwargs in ordered_calls
    )


def test_span_attribute_key_cursor_exact_search_uses_indexed_typed_probe(
    monkeypatch,
):
    identity = (
        PROJECT_A,
        "trace-indexed",
        "span-indexed",
        NOW - timedelta(minutes=1),
    )
    candidate_calls = []
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )

    def candidates(_projects, segment, **kwargs):
        candidate_calls.append((segment, kwargs))
        return (identity,), False, {}

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **_kwargs: [
            {
                "project_id": PROJECT_A,
                "trace_id": identity[1],
                "id": identity[2],
                "start_time": identity[3],
                "is_deleted": 0,
                "string_keys": ["final_status"],
                "number_keys": [],
                "boolean_keys": [],
            }
        ],
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        exact_key="final_status",
    )

    assert [row.key for row in page.rows] == ["final_status"]
    assert page.has_more is False
    assert len(candidate_calls) == 1
    probe_segment, probe_kwargs = candidate_calls[0]
    assert probe_segment[1] - probe_segment[0] <= ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
    assert probe_kwargs["attribute_key"] == "final_status"
    assert probe_kwargs["ordered"] is False
    assert "indexHint(has(mapKeys(attrs_string)" in probe_kwargs["predicate"]
    assert "candidate_query_settings" not in probe_kwargs


def test_span_attribute_key_cursor_exact_json_binds_key_in_ordered_sql():
    executor = RecordingExecutor()
    selector = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(minutes=1),
        window_end=NOW,
        exact_key="llm.invocation_parameters",
    )

    assert page.rows == ()
    assert page.has_more is False
    assert len(executor.calls) == 2
    typed_probe, ordered_fallback = executor.calls
    assert "indexHint(has(mapKeys(attrs_string)" in typed_probe.sql
    assert "ORDER BY\n        start_time DESC" in ordered_fallback.sql
    assert "mapContains(attrs_string, %(attribute_key)s)" in ordered_fallback.sql
    assert "JSONHas(attributes_extra, %(attribute_key)s)" in ordered_fallback.sql
    assert "attributes_extra NOT IN" not in ordered_fallback.sql
    assert ordered_fallback.params["attribute_key"] == "llm.invocation_parameters"
    assert ordered_fallback.settings["use_skip_indexes"] == 0


@pytest.mark.parametrize("horizon_days", (7, 30, 365))
def test_span_attribute_key_cursor_exact_absence_returns_bounded_continuation(
    horizon_days,
):
    horizon = timedelta(days=horizon_days)
    executor = RecordingExecutor()
    page = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    ).read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - horizon,
        window_end=NOW,
        exact_key="missing.attribute",
    )

    ordered_calls = [
        call
        for call in executor.calls
        if "ORDER BY\n        start_time DESC" in call.sql
    ]
    widths = tuple(
        call.params["segment_end"] - call.params["segment_start"]
        for call in ordered_calls
    )
    expected_widths = _geometric_slice_widths(
        horizon,
        initial=ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
        maximum=ATTRIBUTE_KEY_CURSOR_EXACT_MAX_EMPTY_SEGMENT,
    )[: ATTRIBUTE_KEY_CURSOR_EXACT_MAX_CANDIDATE_PAGES - 1]
    assert widths == expected_widths
    assert page.rows == ()
    assert page.browse_status == "continuation"
    assert page.has_more is True
    assert page.next_segment_end == NOW - sum(expected_widths, timedelta())
    assert page.next_segment_start == page.next_segment_end - min(
        expected_widths[-1] * 2,
        ATTRIBUTE_KEY_CURSOR_EXACT_MAX_EMPTY_SEGMENT,
    )
    assert (
        page.metadata.query_count
        == len(executor.calls)
        == ATTRIBUTE_KEY_CURSOR_EXACT_MAX_CANDIDATE_PAGES
    )
    assert page.metadata.query_count < ATTRIBUTE_READ_MAX_QUERY_COUNT
    assert all(
        call.timeout_ms == ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
        for call in ordered_calls[1:]
    )


def test_span_attribute_key_cursor_exact_page_n_reuses_adaptive_width(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads.ATTRIBUTE_READ_MAX_QUERY_COUNT",
        5,
    )
    first_executor = RecordingExecutor()
    first = AttributeReadSelector(
        first_executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    ).read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        exact_key="missing.attribute",
    )

    assert first.rows == ()
    assert first.has_more is True
    assert first.next_segment_start is not None
    persisted_width = first.next_segment_end - first.next_segment_start
    assert persisted_width > ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT

    second_executor = RecordingExecutor()
    second = AttributeReadSelector(
        second_executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    ).read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        segment_end=first.next_segment_end,
        segment_start=first.next_segment_start,
        seen_key_digests=first.seen_key_digests,
        exact_key="missing.attribute",
    )

    first_ordered_call = next(
        call
        for call in second_executor.calls
        if "ORDER BY\n        start_time DESC" in call.sql
    )
    assert (
        first_ordered_call.params["segment_end"]
        - first_ordered_call.params["segment_start"]
        == persisted_width
    )
    assert first_ordered_call.timeout_ms == ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
    assert second.next_segment_end < first.next_segment_end


def test_span_attribute_key_cursor_exact_sparse_year_returns_bounded_continuation():
    ordered_segments = []
    boundary_identity = (
        PROJECT_A,
        "trace-boundary",
        "span-boundary",
        NOW - ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
    )
    returned_boundaries = []
    typed_probe_count = 0

    def respond(call, _call_number):
        nonlocal typed_probe_count
        if "segment_start_us" not in call.params:
            assert returned_boundaries == [boundary_identity]
            return [
                {
                    "project_id": boundary_identity[0],
                    "trace_id": boundary_identity[1],
                    "id": boundary_identity[2],
                    "start_time": boundary_identity[3],
                    "is_deleted": 0,
                    "string_present": 0,
                    "string_value": "",
                    "number_present": 0,
                    "number_value": 0,
                    "boolean_present": 0,
                    "boolean_value": 0,
                    "legacy_present": 0,
                    "legacy_value_raw": "",
                }
            ]
        if "ORDER BY\n        start_time DESC" not in call.sql:
            typed_probe_count += 1
            return []
        segment = (call.params["segment_start"], call.params["segment_end"])
        ordered_segments.append(segment)
        assert call.params["attribute_key"] == "missing_json_key"
        if segment[0] <= boundary_identity[3] < segment[1] and not returned_boundaries:
            returned_boundaries.append(boundary_identity)
            return [
                {
                    "project_id": boundary_identity[0],
                    "trace_id": boundary_identity[1],
                    "id": boundary_identity[2],
                    "start_time": boundary_identity[3],
                }
            ]
        return []

    executor = RecordingExecutor(respond)
    selector = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )

    window_start = NOW - timedelta(days=365)
    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=window_start,
        window_end=NOW,
        exact_key="missing_json_key",
    )

    widths = [end - start for start, end in ordered_segments]
    assert typed_probe_count == 1
    assert returned_boundaries == [boundary_identity]
    assert page.rows == ()
    assert page.has_more is True
    assert page.browse_status == "continuation"
    assert page.next_segment_end == ordered_segments[-1][0]
    assert page.next_segment_start == page.next_segment_end - widths[-1] * 2
    assert page.metadata.query_count == len(executor.calls)
    assert page.metadata.query_count == 9
    assert page.metadata.query_count < ATTRIBUTE_READ_MAX_QUERY_COUNT
    assert widths[0] == ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
    # A completely replayed stale/unsupported candidate proves the first
    # half-open slice empty just like an empty candidate page, so the adjacent
    # older slice widens and the boundary identity is never revisited.
    assert widths[1] == ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT * 2
    assert len(widths) == ATTRIBUTE_KEY_CURSOR_EXACT_MAX_CANDIDATE_PAGES - 1
    assert all(
        later_end == earlier_start
        for (earlier_start, _earlier_end), (_later_start, later_end) in zip(
            ordered_segments,
            ordered_segments[1:],
            strict=False,
        )
    )
    assert all(
        later
        >= min(
            earlier * 2,
            ATTRIBUTE_KEY_CURSOR_EXACT_MAX_EMPTY_SEGMENT,
        )
        or later == widths[-1]
        for earlier, later in zip(widths, widths[1:], strict=False)
    )


def test_span_attribute_key_cursor_exact_missing_key_returns_advancing_page():
    window_start = NOW - timedelta(days=365)
    executor = RecordingExecutor()

    page = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    ).read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=window_start,
        window_end=NOW,
        exact_key="ended_reason",
    )

    assert page.rows == ()
    assert page.has_more is True
    assert page.next_segment_end < NOW
    assert page.next_segment_start is not None
    assert page.metadata.query_count <= 1 + (
        2 * ATTRIBUTE_KEY_CURSOR_EXACT_MAX_CANDIDATE_PAGES
    )


def test_span_attribute_key_cursor_exact_dense_slice_halves_below_five_minutes():
    identity = (
        PROJECT_A,
        "trace-dense-json",
        "span-dense-json",
        NOW - timedelta(seconds=10),
    )
    attempted_widths = []

    def respond(call, _call_number):
        if "segment_start_us" in call.params:
            if "ORDER BY\n        start_time DESC" not in call.sql:
                return []
            width = call.params["segment_end"] - call.params["segment_start"]
            attempted_widths.append(width)
            if width > ATTRIBUTE_KEY_CURSOR_EXACT_MIN_SEGMENT:
                return ReadDeadlineExceeded("dense exact slice exceeded read budget")
            return [
                {
                    "project_id": PROJECT_A,
                    "trace_id": identity[1],
                    "id": identity[2],
                    "start_time": identity[3],
                }
            ]
        return [
            {
                "project_id": PROJECT_A,
                "trace_id": identity[1],
                "id": identity[2],
                "start_time": identity[3],
                "is_deleted": 0,
                "string_present": 0,
                "string_value": "",
                "number_present": 0,
                "number_value": 0,
                "boolean_present": 0,
                "boolean_value": 0,
                "legacy_present": 1,
                "legacy_value_raw": '{"nested":"present"}',
            }
        ]

    executor = RecordingExecutor(respond)
    page = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    ).read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        exact_key="dense_json_key",
    )

    assert [row.key for row in page.rows] == ["dense_json_key"]
    assert page.has_more is False
    assert attempted_widths[0] == ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
    assert attempted_widths[-1] == ATTRIBUTE_KEY_CURSOR_EXACT_MIN_SEGMENT
    assert all(
        later < earlier
        for earlier, later in zip(attempted_widths, attempted_widths[1:], strict=False)
    )
    assert len(executor.calls) <= ATTRIBUTE_READ_MAX_QUERY_COUNT


def test_span_attribute_key_cursor_exact_floor_failure_never_loops_with_200():
    attempted_widths = []

    def respond(call, _call_number):
        if "segment_start_us" not in call.params:
            raise AssertionError("candidate failure must not start latest-state replay")
        if "ORDER BY\n        start_time DESC" not in call.sql:
            # The independent typed accelerator misses and does not own cursor
            # progress. Only the key-bound ordered fallback is forced to fail.
            return []
        attempted_widths.append(
            call.params["segment_end"] - call.params["segment_start"]
        )
        return ReadDeadlineExceeded("dense exact candidate exceeded read budget")

    selector = AttributeReadSelector(
        RecordingExecutor(respond),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )

    with pytest.raises(ReadDeadlineExceeded, match="no physical progress"):
        selector.read_key_cursor_page(
            [PROJECT_A],
            page_size=10,
            window_start=NOW - timedelta(hours=1),
            window_end=NOW,
            exact_key="dense_json_key",
        )

    assert attempted_widths == [
        timedelta(minutes=5),
        timedelta(minutes=2, seconds=30),
        timedelta(minutes=1, seconds=15),
        timedelta(seconds=37, microseconds=500_000),
        ATTRIBUTE_KEY_CURSOR_EXACT_MIN_SEGMENT,
    ]


def test_span_attribute_key_cursor_exact_replay_budget_halves_without_progress():
    """A singleton replay failure shrinks time, not an irrelevant batch cap."""

    identity = (
        PROJECT_A,
        "trace-replay-dense",
        "span-replay-dense",
        NOW - timedelta(seconds=10),
    )
    candidate_widths = []
    replay_widths = []
    candidate_calls = []

    def respond(call, _call_number):
        if "segment_start_us" in call.params:
            if "ORDER BY\n        start_time DESC" not in call.sql:
                return []
            width = call.params["segment_end"] - call.params["segment_start"]
            candidate_widths.append(width)
            candidate_calls.append(call)
            return [
                {
                    "project_id": identity[0],
                    "trace_id": identity[1],
                    "id": identity[2],
                    "start_time": identity[3],
                }
            ]
        assert candidate_widths
        replay_widths.append(candidate_widths[-1])
        assert "JSONExtractRaw(attributes_extra, %(attribute_key)s)" in call.sql
        assert "string_keys" not in call.sql
        if candidate_widths[-1] > ATTRIBUTE_KEY_CURSOR_EXACT_MIN_SEGMENT:
            return ReadDeadlineExceeded("target replay exceeded read budget")
        return [
            {
                "project_id": identity[0],
                "trace_id": identity[1],
                "id": identity[2],
                "start_time": identity[3],
                "is_deleted": 0,
                "string_present": 0,
                "string_value": "",
                "number_present": 0,
                "number_value": 0,
                "boolean_present": 0,
                "boolean_value": 0,
                "legacy_present": 1,
                "legacy_value_raw": '{"nested":"present"}',
            }
        ]

    executor = RecordingExecutor(respond)
    page = AttributeReadSelector(
        executor,
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    ).read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        exact_key="dense_json_key",
    )

    assert [row.key for row in page.rows] == ["dense_json_key"]
    assert page.has_more is False
    assert list(dict.fromkeys(candidate_widths)) == [
        timedelta(minutes=5),
        timedelta(minutes=2, seconds=30),
        timedelta(minutes=1, seconds=15),
        timedelta(seconds=37, microseconds=500_000),
        ATTRIBUTE_KEY_CURSOR_EXACT_MIN_SEGMENT,
    ]
    assert replay_widths[-1] == ATTRIBUTE_KEY_CURSOR_EXACT_MIN_SEGMENT
    assert all(
        "candidate_before_start_us" not in call.params for call in candidate_calls
    )
    assert len(executor.calls) <= ATTRIBUTE_READ_MAX_QUERY_COUNT


@pytest.mark.parametrize(
    ("segment_end", "segment_start"),
    [
        (
            NOW - timedelta(days=90) + ATTRIBUTE_READ_EXPLICIT_SEGMENT,
            None,
        ),
        (NOW, NOW - timedelta(days=120)),
    ],
    ids=("legacy-five-field", "wide-six-field"),
)
def test_span_attribute_key_cursor_exact_reanchors_old_cursor_without_skipping(
    monkeypatch,
    segment_end,
    segment_start,
):
    checkpoint = (
        PROJECT_A,
        "trace-old-cursor",
        "span-z-old-cursor",
        NOW - timedelta(days=90),
    )
    older_tie = (
        PROJECT_A,
        checkpoint[1],
        "span-a-old-cursor",
        checkpoint[3],
    )
    candidate_calls = []
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )

    def candidates(_projects, segment, **kwargs):
        candidate_calls.append((segment, kwargs.get("before_identity")))
        assert segment[0] <= checkpoint[3] < segment[1]
        return (older_tie,), False, {}

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **_kwargs: [
            {
                "project_id": older_tie[0],
                "trace_id": older_tie[1],
                "id": older_tie[2],
                "start_time": older_tie[3],
                "is_deleted": 0,
                "string_present": 0,
                "string_value": "",
                "number_present": 0,
                "number_value": 0,
                "boolean_present": 0,
                "boolean_value": 0,
                "legacy_present": 1,
                "legacy_value_raw": '{"nested":"present"}',
            }
        ],
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=180),
        window_end=NOW,
        segment_end=segment_end,
        segment_start=segment_start,
        before_identity=checkpoint,
        exact_key="old_json_key",
    )

    assert [row.key for row in page.rows] == ["old_json_key"]
    assert page.has_more is False
    assert candidate_calls == [
        (
            (
                checkpoint[3],
                checkpoint[3] + ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
            ),
            checkpoint,
        )
    ]


def test_span_attribute_key_cursor_exact_typed_budget_falls_back_to_json(
    monkeypatch,
):
    identity = (PROJECT_A, "trace-json", "span-json", NOW - timedelta(hours=1))
    candidate_calls = []
    generic_widths = []
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )

    def candidates(_projects, _segment, **kwargs):
        candidate_calls.append(kwargs)
        if not kwargs["ordered"]:
            raise ReadDeadlineExceeded("typed index probe exceeded its budget")
        width = _segment[1] - _segment[0]
        generic_widths.append(width)
        if width > timedelta(minutes=5):
            raise ReadDeadlineExceeded("generic JSON probe exceeded its budget")
        return (identity,), False, {}

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **_kwargs: [
            {
                "project_id": PROJECT_A,
                "trace_id": identity[1],
                "id": identity[2],
                "start_time": identity[3],
                "is_deleted": 0,
                "string_keys": [],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": '{"json_only":{"nested":"value"}}',
            }
        ],
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        exact_key="json_only",
    )

    assert [row.key for row in page.rows] == ["json_only"]
    assert page.rows[0].type == "map"
    assert page.has_more is False
    assert len(candidate_calls) == 2
    assert all(call["attribute_key"] == "json_only" for call in candidate_calls)
    assert generic_widths == [timedelta(minutes=5)]
    assert (
        "JSONHas(attributes_extra, %(attribute_key)s)"
        in candidate_calls[-1]["predicate"]
    )
    assert "attributes_extra NOT IN" not in candidate_calls[-1]["predicate"]
    assert candidate_calls[-1]["candidate_query_settings"] == {"use_skip_indexes": 0}


def test_span_attribute_key_cursor_rejects_dual_physical_checkpoints():
    identity = (PROJECT_A, "trace-a", "span-a", NOW - timedelta(hours=1))

    with pytest.raises(ValueError, match="mutually exclusive"):
        AttributeReadSelector(RecordingExecutor(), now=NOW).read_key_cursor_page(
            [PROJECT_A],
            page_size=10,
            window_start=NOW - timedelta(days=365),
            window_end=NOW,
            before_identity=identity,
            resume_identity=identity,
        )


def test_attribute_retained_window_start_uses_exact_global_part_metadata():
    retained_start = NOW - timedelta(days=400)

    def respond(call, _call_number):
        assert "FROM system.parts" in call.sql
        assert "minOrNull(min_time) AS retained_start" in call.sql
        assert call.params == {
            "window_end_us": _unix_microseconds(NOW),
        }
        return [{"retained_start": retained_start}]

    executor = RecordingExecutor(respond)

    result = AttributeReadSelector(executor, now=NOW).retained_window_start(
        [PROJECT_A, PROJECT_B],
        window_end=NOW,
    )

    assert result == retained_start
    assert len(executor.calls) == 1


def test_attribute_retained_window_start_returns_none_for_empty_scope():
    executor = RecordingExecutor(lambda _call, _call_number: [{"retained_start": None}])

    result = AttributeReadSelector(executor, now=NOW).retained_window_start(
        [PROJECT_A],
        window_end=NOW,
    )

    assert result is None
    assert len(executor.calls) == 1


def test_attribute_cursor_continues_retained_bound_operation_budget():
    retained_start = NOW - timedelta(microseconds=1)

    def respond(call, _call_number):
        if "FROM system.parts" in call.sql:
            return [{"retained_start": retained_start}]
        return []

    executor = RecordingExecutor(respond)
    selector = AttributeReadSelector(executor, now=NOW)

    assert selector.retained_window_start([PROJECT_A], window_end=NOW) == retained_start
    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=retained_start,
        window_end=NOW,
        continue_operation=True,
    )

    assert page.has_more is False
    assert page.metadata.query_count == len(executor.calls)
    assert page.metadata.query_count >= 2


def test_value_cursor_continue_without_prior_metadata_starts_operation_budget():
    executor = RecordingExecutor()

    page = AttributeReadSelector(executor, now=NOW).read_value_cursor_page(
        [PROJECT_A],
        "model",
        page_size=10,
        window_start=NOW - timedelta(microseconds=1),
        window_end=NOW,
        continue_operation=True,
    )

    assert page.has_more is False
    assert page.metadata.query_complete is True
    assert page.metadata.query_count == len(executor.calls)


def test_attribute_retained_bound_budget_falls_back_without_starving_cursor(
    monkeypatch,
):
    class ManualClock:
        value = 100.0

        def __call__(self):
            return self.value

    clock = ManualClock()

    class Capacity:
        def __init__(self):
            self.timeouts: list[float] = []

        def acquire(self, *, timeout):
            self.timeouts.append(timeout)
            if len(self.timeouts) == 1:
                clock.value += timeout
                return False
            return True

        def release(self):
            return None

    capacity = Capacity()
    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads._ATTRIBUTE_READ_CAPACITY",
        capacity,
    )
    executor = RecordingExecutor()
    selector = AttributeReadSelector(executor, now=NOW, clock=clock)

    retained_start = selector.retained_window_start([PROJECT_A], window_end=NOW)
    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=retained_start,
        window_end=NOW,
        continue_operation=True,
    )

    assert retained_start == datetime(1970, 1, 1, tzinfo=UTC)
    assert capacity.timeouts[0] == ATTRIBUTE_READ_METADATA_TIMEOUT_MS / 1000
    assert capacity.timeouts[1] > capacity.timeouts[0]
    assert any("segment_start" in call.params for call in executor.calls)
    assert all(
        0 < call.timeout_ms <= ATTRIBUTE_READ_WALL_TIMEOUT_MS for call in executor.calls
    )
    assert clock.value < 101.0
    assert page.metadata.query_count == len(executor.calls)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"retained_start": "not-a-datetime"}],
        [{"retained_start": NOW}],
    ],
)
def test_attribute_retained_window_start_rejects_invalid_exact_envelope(rows):
    executor = RecordingExecutor(lambda _call, _call_number: rows)

    with pytest.raises(IncompleteLatestStateReplay):
        AttributeReadSelector(executor, now=NOW).retained_window_start(
            [PROJECT_A],
            window_end=NOW,
        )


def test_span_attribute_key_cursor_reaches_retained_key_older_than_one_year(
    monkeypatch,
):
    retained_start = datetime(1970, 1, 1, tzinfo=UTC)
    old_start = NOW - timedelta(days=400)
    identity = (PROJECT_A, "trace-old", "span-old", old_start)
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    visited_segments = []

    def candidates(_projects, segment, **_kwargs):
        visited_segments.append(segment)
        if segment[0] <= old_start < segment[1]:
            return (identity,), False, {}
        return (), False, {}

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **_kwargs: [
            {
                "project_id": PROJECT_A,
                "trace_id": identity[1],
                "id": identity[2],
                "start_time": old_start,
                "is_deleted": 0,
                "string_keys": ["retained_legacy_attribute"],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": "{}",
            }
        ],
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=retained_start,
        window_end=NOW,
    )

    assert [row.key for row in page.rows] == ["retained_legacy_attribute"]
    assert page.metadata.query_window_start == retained_start
    assert page.metadata.query_window_end == NOW
    assert any(start <= old_start < end for start, end in visited_segments)
    assert all(
        later_end == earlier_start
        for (earlier_start, _), (_, later_end) in zip(
            visited_segments, visited_segments[1:], strict=False
        )
    )


def test_span_attribute_key_cursor_compresses_widened_checkpoint_and_continues(
    monkeypatch,
):
    identities = tuple(
        (
            PROJECT_A,
            f"trace-wide-{index}",
            f"span-wide-{index}",
            NOW - timedelta(days=130 + index * 10),
        )
        for index in range(5)
    )
    rows = {
        identity: {
            "project_id": PROJECT_A,
            "trace_id": identity[1],
            "id": identity[2],
            "start_time": identity[3],
            "is_deleted": 0,
            "string_keys": [f"wide_key_{index}"],
            "number_keys": [],
            "boolean_keys": [],
            "attributes_extra": "{}",
        }
        for index, identity in enumerate(identities)
    }
    attempted_segments: list[
        tuple[tuple[datetime, datetime], tuple[str, str, str, datetime] | None]
    ] = []

    def candidates(_projects, segment, **kwargs):
        before_identity = kwargs.get("before_identity")
        attempted_segments.append((segment, before_identity))
        if before_identity is not None:
            assert segment[0] <= before_identity[3] < segment[1]
        matches = [
            identity
            for identity in identities
            if segment[0] <= identity[3] < segment[1]
            and (before_identity is None or identity[3] < before_identity[3])
        ]
        matches.sort(key=lambda identity: identity[3], reverse=True)
        limit = kwargs["candidate_limit"]
        return tuple(matches[:limit]), len(matches) > limit, {}

    def verify(*_args, **kwargs):
        return [rows[identity] for identity in kwargs["candidate_ids"]]

    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", verify)

    first = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=3,
        window_start=NOW - timedelta(days=400),
        window_end=NOW,
    )

    assert [row.key for row in first.rows] == [
        "wide_key_0",
        "wide_key_1",
        "wide_key_2",
    ]
    assert any(
        segment_end - segment_start > timedelta(days=60)
        for (segment_start, segment_end), _checkpoint in attempted_segments
    )
    assert first.next_segment_end == identities[2][3] + timedelta(hours=6)
    assert first.next_segment_start is None
    assert first.next_before_identity == identities[2]

    attempted_segments.clear()
    second = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=3,
        window_start=NOW - timedelta(days=400),
        window_end=NOW,
        segment_end=first.next_segment_end,
        before_identity=first.next_before_identity,
        resume_identity=first.next_resume_identity,
        resume_key_offset=first.next_resume_key_offset,
        seen_key_digests=first.seen_key_digests,
    )

    assert attempted_segments[0] == (
        (
            first.next_before_identity[3]
            + timedelta(microseconds=1)
            - ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
            first.next_before_identity[3] + timedelta(microseconds=1),
        ),
        first.next_before_identity,
    )
    assert [row.key for row in second.rows] == ["wide_key_3", "wide_key_4"]
    assert len({row.key for row in (*first.rows, *second.rows)}) == 5


def test_span_attribute_key_cursor_compresses_widened_truncated_segment(
    monkeypatch,
):
    recent_identities = [
        (
            PROJECT_A,
            f"trace-truncated-{index}",
            f"span-truncated-{index}",
            NOW - timedelta(hours=6, minutes=1 + index * 8),
        )
        for index in range(64)
    ]
    older_identities = [
        (
            PROJECT_A,
            f"trace-truncated-{index + 64}",
            f"span-truncated-{index + 64}",
            NOW
            - timedelta(
                hours=14,
                minutes=26,
                seconds=index * 145,
            ),
        )
        for index in range(86)
    ]
    identities = tuple(
        sorted(
            (*recent_identities, *older_identities),
            key=lambda identity: identity[3],
            reverse=True,
        )
    )
    rows = {
        identity: {
            "project_id": PROJECT_A,
            "trace_id": identity[1],
            "id": identity[2],
            "start_time": identity[3],
            "is_deleted": 0,
            "string_keys": ["shared_key"],
            "number_keys": [],
            "boolean_keys": [],
            "attributes_extra": "{}",
        }
        for identity in identities
    }
    returned_identities: list[tuple[str, str, str, datetime]] = []

    def candidates(_projects, segment, **kwargs):
        before_identity = kwargs.get("before_identity")
        if before_identity is not None:
            # This assertion catches both the old same-request reset after a
            # truncated widened page and the old cross-request six-hour reset.
            assert segment[0] <= before_identity[3] < segment[1]
        matches = [
            identity
            for identity in identities
            if segment[0] <= identity[3] < segment[1]
            and (before_identity is None or identity[3] < before_identity[3])
        ]
        limit = kwargs["candidate_limit"]
        selected = tuple(matches[:limit])
        returned_identities.extend(selected)
        return selected, len(matches) > limit, {}

    def verify(*_args, **kwargs):
        return [rows[identity] for identity in kwargs["candidate_ids"]]

    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", verify)
    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads."
        "ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_PAGES",
        12,
    )

    first = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(hours=48),
        window_end=NOW,
    )

    assert [row.key for row in first.rows] == ["shared_key"]
    assert first.next_segment_start is None

    page = first
    for _attempt in range(10):
        if not page.has_more:
            break
        page = selector.read_key_cursor_page(
            [PROJECT_A],
            page_size=10,
            window_start=NOW - timedelta(hours=48),
            window_end=NOW,
            segment_end=page.next_segment_end,
            segment_start=page.next_segment_start,
            before_identity=page.next_before_identity,
            resume_identity=page.next_resume_identity,
            resume_key_offset=page.next_resume_key_offset,
            seen_key_digests=page.seen_key_digests,
        )
        assert page.metadata.query_complete is True
        assert page.next_segment_start is None

    assert page.has_more is False
    assert set(returned_identities) == set(identities)


def test_span_attribute_key_cursor_recovers_old_wide_checkpoint_after_budget_cascade(
    monkeypatch,
):
    checkpoint = (
        PROJECT_A,
        "trace-wide-checkpoint",
        "span-z-wide-checkpoint",
        NOW - timedelta(days=90),
    )
    older = (
        PROJECT_A,
        checkpoint[1],
        "span-a-after-wide-budget",
        checkpoint[3],
    )
    row = {
        "project_id": PROJECT_A,
        "trace_id": older[1],
        "id": older[2],
        "start_time": older[3],
        "is_deleted": 0,
        "string_keys": ["reachable_after_wide_budget"],
        "number_keys": [],
        "boolean_keys": [],
        "attributes_extra": "{}",
    }
    attempted_widths: list[timedelta] = []

    def candidates(_projects, segment, **kwargs):
        attempted_widths.append(segment[1] - segment[0])
        before_identity = kwargs.get("before_identity")
        if before_identity is not None and not (
            segment[0] <= before_identity[3] < segment[1]
        ):
            raise ValueError("candidate keyset must stay inside its segment")
        if segment[1] - segment[0] > timedelta(days=60):
            raise ReadDeadlineExceeded("old widened cursor exceeded read budget")
        if segment[1] - segment[0] == ATTRIBUTE_READ_EXPLICIT_SEGMENT:
            raise ReadDeadlineExceeded("legacy six-hour retry exceeded read budget")
        matches = (
            (older,)
            if segment[0] <= older[3] < segment[1]
            and (
                before_identity is None
                or (older[3], older[2], older[1], older[0])
                < (
                    before_identity[3],
                    before_identity[2],
                    before_identity[1],
                    before_identity[0],
                )
            )
            else ()
        )
        return matches, False, {}

    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **kwargs: [row] if kwargs.get("candidate_ids") else [],
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=1,
        window_start=NOW - timedelta(days=180),
        window_end=NOW,
        segment_end=NOW,
        segment_start=NOW - timedelta(days=120),
        before_identity=checkpoint,
    )

    assert attempted_widths[0] == timedelta(days=120)
    assert attempted_widths[1] == ATTRIBUTE_READ_EXPLICIT_SEGMENT
    assert attempted_widths[2] == ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
    assert [item.key for item in page.rows] == ["reachable_after_wide_budget"]
    assert page.next_segment_start is None


def test_span_attribute_key_cursor_recuts_dense_checkpoint_at_same_keyset_frontier(
    monkeypatch,
):
    checkpoint = (
        PROJECT_A,
        "trace-dense-checkpoint",
        "span-z-dense-checkpoint",
        NOW - timedelta(hours=1),
    )
    older = (
        PROJECT_A,
        checkpoint[1],
        "span-a-after-dense-checkpoint",
        checkpoint[3],
    )
    calls = []
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )

    def candidates(_projects, segment, **kwargs):
        calls.append(
            (
                segment,
                kwargs.get("before_identity"),
                kwargs.get("query_timeout_ms"),
            )
        )
        if segment[1] - segment[0] == ATTRIBUTE_READ_EXPLICIT_SEGMENT:
            raise ReadDeadlineExceeded("dense checkpoint candidate timed out")
        assert segment[1] - segment[0] == ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
        return (older,), False, {}

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **_kwargs: [
            {
                "project_id": PROJECT_A,
                "trace_id": older[1],
                "id": older[2],
                "start_time": older[3],
                "is_deleted": 0,
                "string_keys": ["reachable_after_candidate_recut"],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": "{}",
            }
        ],
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=1,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_end=NOW,
        segment_start=NOW - ATTRIBUTE_READ_EXPLICIT_SEGMENT,
        before_identity=checkpoint,
    )

    assert calls[0] == (
        (NOW - ATTRIBUTE_READ_EXPLICIT_SEGMENT, NOW),
        checkpoint,
        ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS,
    )
    assert calls[1][0][1] == checkpoint[3] + ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
    assert calls[1][0][1] - calls[1][0][0] == ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
    assert calls[1][1] == checkpoint
    assert calls[1][2] == ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
    assert [row.key for row in page.rows] == ["reachable_after_candidate_recut"]


@pytest.mark.parametrize(
    ("segment_start", "expected_widths"),
    [
        (
            None,
            (
                ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
                ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT,
                ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
                ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT,
            ),
        ),
        (
            NOW - timedelta(days=1),
            (
                timedelta(days=1),
                ATTRIBUTE_READ_EXPLICIT_SEGMENT,
                ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
                ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT,
                ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT,
            ),
        ),
    ],
    ids=("legacy-five-field", "wide-six-field"),
)
def test_span_attribute_key_cursor_dense_retry_keeps_late_pages_exact(
    monkeypatch,
    segment_start,
    expected_widths,
):
    checkpoint = (
        PROJECT_A,
        "trace-dense-history",
        "span-z-dense-history",
        NOW - timedelta(hours=1),
    )
    identities = (
        (
            PROJECT_A,
            "trace-dense-history",
            "span-y-dense-history",
            checkpoint[3] - timedelta(seconds=1),
        ),
        (
            PROJECT_A,
            "trace-dense-history",
            "span-x-dense-history",
            checkpoint[3] - timedelta(seconds=2),
        ),
    )
    rows = {
        identity: {
            "project_id": identity[0],
            "trace_id": identity[1],
            "id": identity[2],
            "start_time": identity[3],
            "is_deleted": 0,
            "string_keys": [f"late_unique_{index}"],
            "number_keys": [],
            "boolean_keys": [],
            "attributes_extra": "{}",
        }
        for index, identity in enumerate(identities)
    }
    candidate_calls: list[
        tuple[
            timedelta,
            tuple[str, str, str, datetime] | None,
            int | None,
        ]
    ] = []
    replay_calls: list[tuple[int, int | None]] = []
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        wall_timeout_ms=ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS,
        typed_only=True,
        json_attribute_mode="structured",
    )

    def order_key(identity):
        return identity[3], identity[2], identity[1], identity[0]

    def candidates(_projects, segment, **kwargs):
        width = segment[1] - segment[0]
        before_identity = kwargs.get("before_identity")
        candidate_calls.append((width, before_identity, kwargs.get("query_timeout_ms")))
        if width > ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT:
            raise ReadDeadlineExceeded("dense historical candidate timed out")
        matches = tuple(
            identity
            for identity in identities
            if segment[0] <= identity[3] < segment[1]
            and (
                before_identity is None
                or order_key(identity) < order_key(before_identity)
            )
        )
        limit = kwargs["candidate_limit"]
        return matches[:limit], len(matches) > limit, {}

    def verify(*_args, **kwargs):
        candidate_ids = kwargs.get("candidate_ids", ())
        replay_calls.append((len(candidate_ids), kwargs.get("query_timeout_ms")))
        return [rows[identity] for identity in candidate_ids]

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", verify)

    first = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=1,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_end=NOW,
        segment_start=segment_start,
        before_identity=checkpoint,
    )
    second = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=1,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_end=first.next_segment_end,
        segment_start=first.next_segment_start,
        before_identity=first.next_before_identity,
        resume_identity=first.next_resume_identity,
        resume_key_offset=first.next_resume_key_offset,
        seen_key_digests=first.seen_key_digests,
    )

    assert [row.key for row in first.rows] == ["late_unique_0"]
    assert [row.key for row in second.rows] == ["late_unique_1"]
    assert set(first.seen_key_digests).isdisjoint(second.appended_key_digests)
    assert first.metadata.query_complete is True
    assert second.metadata.query_complete is True
    assert tuple(call[0] for call in candidate_calls[: len(expected_widths)]) == (
        expected_widths
    )
    assert tuple(call[2] for call in candidate_calls[: len(expected_widths)]) == (
        tuple(
            (
                ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
                if width > ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT
                else None
            )
            for width in expected_widths
        )
    )
    assert replay_calls == (
        [
            (2, ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS),
            (1, None),
        ]
        if segment_start is None
        else [
            (0, None),
            (2, ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS),
            (1, None),
        ]
    )


def test_span_attribute_key_cursor_dense_floor_failure_is_fail_closed(monkeypatch):
    checkpoint = (
        PROJECT_A,
        "trace-unreadable-history",
        "span-unreadable-history",
        NOW - timedelta(hours=1),
    )
    calls: list[tuple[timedelta, int | None]] = []
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        wall_timeout_ms=ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS,
        typed_only=True,
        json_attribute_mode="structured",
    )

    def candidates(_projects, segment, **kwargs):
        calls.append((segment[1] - segment[0], kwargs.get("query_timeout_ms")))
        raise ReadDeadlineExceeded("dense floor still unreadable")

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **_kwargs: pytest.fail(
            "failed candidates must not reach latest-state replay"
        ),
    )

    for _attempt in range(2):
        with pytest.raises(ReadDeadlineExceeded, match="dense floor still unreadable"):
            selector.read_key_cursor_page(
                [PROJECT_A],
                page_size=1,
                window_start=NOW - timedelta(days=1),
                window_end=NOW,
                segment_end=NOW,
                before_identity=checkpoint,
            )

    assert calls[:2] == [
        (
            ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
            ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS,
        ),
        (ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT, None),
    ]
    assert calls[2:] == calls[:2]


def test_span_attribute_key_cursor_reuses_candidate_proof_while_recutting_replay(
    monkeypatch,
):
    checkpoint = (
        PROJECT_A,
        "trace-replay-prefix",
        "span-replay-checkpoint",
        NOW - timedelta(minutes=10),
    )
    identities = tuple(
        (
            PROJECT_A,
            f"trace-replay-prefix-{index}",
            f"span-replay-prefix-{index}",
            checkpoint[3] - timedelta(microseconds=index + 1),
        )
        for index in range(64)
    )
    candidate_calls = 0
    replay_calls: list[tuple[int, int | None]] = []
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        wall_timeout_ms=ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS,
        typed_only=True,
        json_attribute_mode="structured",
    )

    def candidates(_projects, _segment, **_kwargs):
        nonlocal candidate_calls
        candidate_calls += 1
        return identities, True, {}

    def verify(*_args, **kwargs):
        candidate_ids = kwargs["candidate_ids"]
        timeout_ms = kwargs.get("query_timeout_ms")
        replay_calls.append((len(candidate_ids), timeout_ms))
        if (
            len(candidate_ids) > 1
            and timeout_ms == ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
        ):
            raise ReadDeadlineExceeded("wide latest-state replay timed out")
        identity = candidate_ids[0]
        return [
            {
                "project_id": identity[0],
                "trace_id": identity[1],
                "id": identity[2],
                "start_time": identity[3],
                "is_deleted": 0,
                "string_keys": ["verified_after_replay_recut"],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": "{}",
            }
        ]

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", verify)

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=1,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        segment_end=checkpoint[3] + ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
        before_identity=checkpoint,
    )

    assert [row.key for row in page.rows] == ["verified_after_replay_recut"]
    assert candidate_calls == 1
    assert replay_calls == [
        (64, ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS),
        (32, None),
    ]
    assert page.next_before_identity == identities[0]


def test_span_attribute_key_cursor_single_identity_replay_failure_does_not_skip(
    monkeypatch,
):
    checkpoint = (
        PROJECT_A,
        "trace-huge-replay",
        "span-z-huge-replay",
        NOW - timedelta(minutes=1),
    )
    huge_identity = (
        PROJECT_A,
        "trace-huge-replay",
        "span-a-huge-replay",
        checkpoint[3],
    )
    candidate_calls = 0
    replay_calls = 0
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        wall_timeout_ms=ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS,
        typed_only=True,
        json_attribute_mode="structured",
    )

    def candidates(*_args, **_kwargs):
        nonlocal candidate_calls
        candidate_calls += 1
        return (huge_identity,), False, {}

    def verify(*_args, **kwargs):
        nonlocal replay_calls
        replay_calls += 1
        assert kwargs["candidate_ids"] == (huge_identity,)
        assert kwargs.get("query_timeout_ms") is None
        raise ReadDeadlineExceeded("single identity cannot be hydrated")

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", verify)

    for _attempt in range(2):
        with pytest.raises(
            ReadDeadlineExceeded,
            match="single identity cannot be hydrated",
        ):
            selector.read_key_cursor_page(
                [PROJECT_A],
                page_size=1,
                window_start=NOW - timedelta(days=1),
                window_end=NOW,
                segment_end=checkpoint[3]
                + ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT,
                segment_start=checkpoint[3],
                before_identity=checkpoint,
            )

    assert candidate_calls == 2
    assert replay_calls == 2


def test_span_attribute_key_cursor_empty_retained_window_terminates_in_one_request():
    retained_start = datetime(1970, 1, 1, tzinfo=UTC)
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=retained_start,
        window_end=NOW,
    )

    assert page.rows == ()
    assert page.has_more is False
    assert page.browse_status == "exhausted"
    assert page.next_segment_end == retained_start
    assert 1 < page.metadata.query_count < ATTRIBUTE_READ_MAX_QUERY_COUNT


def test_span_attribute_key_cursor_shrinks_widened_window_on_read_budget(
    monkeypatch,
):
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    attempted_widths: list[timedelta] = []

    def candidates(_projects, segment, **_kwargs):
        width = segment[1] - segment[0]
        attempted_widths.append(width)
        if width > timedelta(hours=6):
            raise ReadDeadlineExceeded("widened slice exceeded the read envelope")
        return (), False, {}

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", lambda *_args, **_kwargs: [])

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=30),
        window_end=NOW,
    )

    assert (
        sum(width > ATTRIBUTE_READ_EXPLICIT_SEGMENT for width in attempted_widths) == 1
    )
    assert page.has_more is True
    assert page.browse_status == "continuation"
    assert page.next_segment_end < NOW
    assert page.metadata.query_complete is True


def test_span_attribute_key_cursor_four_second_wall_publishes_prior_proof(
    monkeypatch,
):
    clock_value = [100.0]

    def clock():
        return clock_value[0]

    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        wall_timeout_ms=4_000,
        clock=clock,
        typed_only=True,
        json_attribute_mode="structured",
    )
    attempted_segments = []

    def candidates(_projects, segment, **_kwargs):
        attempted_segments.append(segment)
        if len(attempted_segments) == 1:
            clock_value[0] += 0.2
            return (), False, {}
        clock_value[0] += 3.9
        raise ReadDeadlineExceeded("later key slice exceeded the picker wall")

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", lambda *_args, **_kwargs: [])

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
    )

    assert page.rows == ()
    assert page.has_more is True
    assert page.browse_status == "continuation"
    assert page.next_segment_end == attempted_segments[0][0]
    assert page.next_segment_end < NOW
    assert page.metadata.query_complete is True


def test_span_attribute_key_cursor_remembers_safe_width_after_slow_budget_failure(
    monkeypatch,
):
    clock_value = [100.0]

    def clock():
        return clock_value[0]

    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        wall_timeout_ms=6_000,
        clock=clock,
        typed_only=True,
        json_attribute_mode="structured",
    )
    attempted_widths: list[timedelta] = []

    def candidates(_projects, segment, **_kwargs):
        width = segment[1] - segment[0]
        attempted_widths.append(width)
        if selector._deadline is not None and clock() >= selector._deadline:
            raise ReadDeadlineExceeded("attribute read deadline exceeded")
        if width > timedelta(hours=6):
            # Model the documented production shape: the widened query consumes
            # almost its full statement timeout before ClickHouse rejects it.
            clock_value[0] += 1.45
            raise ReadDeadlineExceeded("widened slice exceeded the read envelope")
        clock_value[0] += 0.01
        return (), False, {}

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", lambda *_args, **_kwargs: [])

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=30),
        window_end=NOW,
    )

    failed_index = next(
        index
        for index, width in enumerate(attempted_widths)
        if width > ATTRIBUTE_READ_EXPLICIT_SEGMENT
    )
    assert all(
        width <= ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
        for width in attempted_widths[failed_index + 1 :]
    )
    assert clock() < 102.0
    assert page.has_more is True
    assert page.browse_status == "continuation"


def test_span_attribute_key_cursor_caps_generic_growth_before_dense_retry(
    monkeypatch,
):
    clock_value = [100.0]

    def clock():
        return clock_value[0]

    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        wall_timeout_ms=8_000,
        clock=clock,
        typed_only=True,
        json_attribute_mode="structured",
    )
    attempted: list[tuple[timedelta, int | None]] = []

    def candidates(_projects, segment, **kwargs):
        width = segment[1] - segment[0]
        timeout_ms = kwargs.get("query_timeout_ms")
        attempted.append((width, timeout_ms))
        if width <= ATTRIBUTE_READ_EXPLICIT_SEGMENT:
            clock_value[0] += 0.02
            return (), False, {}
        assert timeout_ms == ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
        clock_value[0] += ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS / 1000
        raise ReadDeadlineExceeded("speculative generic growth timed out")

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", lambda *_args, **_kwargs: [])

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=50,
        window_start=NOW - timedelta(days=30),
        window_end=NOW,
    )

    assert attempted[0] == (ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT, None)
    first_failed_growth_index = next(
        index
        for index, (width, _timeout) in enumerate(attempted)
        if width > ATTRIBUTE_READ_EXPLICIT_SEGMENT
    )
    assert all(
        timeout == ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
        for _width, timeout in attempted[1:]
    )
    assert attempted[first_failed_growth_index][1] == (
        ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
    )
    assert attempted[first_failed_growth_index + 1] == (
        ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
        ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS,
    )
    assert clock() < 101.0
    assert selector._deadline is not None
    assert selector._deadline - clock() > 7.0
    assert page.metadata.query_complete is True
    assert page.has_more is True
    assert page.browse_status == "continuation"
    assert page.next_segment_end < NOW


def test_span_attribute_key_cursor_jumps_from_slow_base_window_to_five_minutes(
    monkeypatch,
):
    clock_value = [100.0]

    def clock():
        return clock_value[0]

    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        wall_timeout_ms=6_000,
        clock=clock,
        typed_only=True,
        json_attribute_mode="structured",
    )
    attempted_widths = []

    def candidates(_projects, segment, **_kwargs):
        width = segment[1] - segment[0]
        attempted_widths.append(width)
        if width > timedelta(minutes=5):
            clock_value[0] += 1.45
            raise ReadDeadlineExceeded("dense base slice exceeded its budget")
        clock_value[0] += 0.01
        return (), False, {}

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(selector, "_verify_latest", lambda *_args, **_kwargs: [])

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=30),
        window_end=NOW,
    )

    assert attempted_widths[:2] == [
        ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
        ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT * 2,
    ]
    assert attempted_widths[2:] == [ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT] * (
        ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_PAGES - 1
    )
    assert clock() < 102.0
    assert page.has_more is True
    assert page.metadata.query_complete is True


def test_span_attribute_key_cursor_keeps_safe_width_after_stale_candidate(
    monkeypatch,
):
    identity = (PROJECT_A, "trace-stale", "span-stale", NOW - timedelta(minutes=1))
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    attempted_widths = []
    five_minute_calls = 0

    def candidates(_projects, segment, **_kwargs):
        nonlocal five_minute_calls
        width = segment[1] - segment[0]
        attempted_widths.append(width)
        if width > timedelta(minutes=5):
            raise ReadDeadlineExceeded("dense base slice exceeded its budget")
        five_minute_calls += 1
        return ((identity,), False, {}) if five_minute_calls == 1 else ((), False, {})

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **kwargs: (
            [
                {
                    "project_id": PROJECT_A,
                    "trace_id": identity[1],
                    "id": identity[2],
                    "start_time": identity[3],
                    "is_deleted": 1,
                    "string_keys": [],
                    "number_keys": [],
                    "boolean_keys": [],
                    "attributes_extra": "{}",
                }
            ]
            if kwargs.get("candidate_ids")
            else []
        ),
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=30),
        window_end=NOW,
    )

    assert attempted_widths[:2] == [
        ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
        ATTRIBUTE_READ_EXPLICIT_SEGMENT,
    ]
    assert all(
        width == ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT for width in attempted_widths[2:]
    )
    assert page.has_more is True


def test_span_attribute_key_cursor_exact_json_fallback_shrinks_without_reprobe(
    monkeypatch,
):
    identity = (
        PROJECT_A,
        "trace-json-old",
        "span-json-old",
        NOW - timedelta(minutes=1),
    )
    selector = AttributeReadSelector(
        RecordingExecutor(),
        now=NOW,
        typed_only=True,
        json_attribute_mode="structured",
    )
    typed_probe_count = 0
    generic_widths = []

    def candidates(_projects, segment, **kwargs):
        nonlocal typed_probe_count
        if not kwargs["ordered"]:
            typed_probe_count += 1
            return (), False, {}
        width = segment[1] - segment[0]
        generic_widths.append(width)
        if width > timedelta(minutes=5):
            raise ReadDeadlineExceeded("dense JSON slice exceeded its budget")
        return (identity,), False, {}

    monkeypatch.setattr(selector, "_candidate_ids", candidates)
    monkeypatch.setattr(
        selector,
        "_verify_latest",
        lambda *_args, **_kwargs: [
            {
                "project_id": PROJECT_A,
                "trace_id": identity[1],
                "id": identity[2],
                "start_time": identity[3],
                "is_deleted": 0,
                "string_keys": [],
                "number_keys": [],
                "boolean_keys": [],
                "attributes_extra": '{"json_only":{"nested":"value"}}',
            }
        ],
    )

    page = selector.read_key_cursor_page(
        [PROJECT_A],
        page_size=10,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        exact_key="json_only",
    )

    assert typed_probe_count == 1
    assert generic_widths == [timedelta(minutes=5)]
    assert [row.key for row in page.rows] == ["json_only"]
    assert page.rows[0].type == "map"
    assert page.has_more is False


def test_span_attribute_key_api_cursor_is_scoped_and_restores_progress(monkeypatch):
    from tracer.views.span_attributes import (
        SPAN_ATTRIBUTE_RETAINED_DATA_START,
        SpanAttributeKeysView,
    )

    identity = (PROJECT_A, "trace-1", "span-1", NOW - timedelta(hours=1))
    retained_start = SPAN_ATTRIBUTE_RETAINED_DATA_START
    seen = (attribute_key_cursor_digest("alpha"),)
    calls = []

    def read_page(self, project_ids, **kwargs):
        assert (
            0
            < self._wall_timeout_seconds
            <= (ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS / 1000)
        )
        calls.append((project_ids, kwargs))
        if len(calls) == 1:
            return AttributeKeyCursorPageRead(
                (AttributeKeyRow("alpha", "string", 1),),
                _metadata(),
                True,
                "continuation",
                kwargs["window_end"],
                identity,
                None,
                0,
                seen,
                kwargs["window_end"] - timedelta(hours=12),
            )
        return AttributeKeyCursorPageRead(
            (AttributeKeyRow("beta", "number", 1),),
            _metadata(),
            False,
            "exhausted",
            kwargs["window_start"],
            None,
            None,
            0,
            (*seen, attribute_key_cursor_digest("beta")),
        )

    monkeypatch.setattr(AttributeReadSelector, "read_key_cursor_page", read_page)
    monkeypatch.setattr(
        AttributeReadSelector,
        "retained_window_start",
        lambda *_args, **_kwargs: pytest.fail(
            "page one must not spend a ClickHouse read on retained metadata"
        ),
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_A, "page_size": 10},
    )
    first_response = SpanAttributeKeysView.as_view()(first_request)

    assert first_response.status_code == 200
    assert first_response.data["result"][0]["key"] == "alpha"
    assert first_response.data["has_more"] is True
    assert first_response.data["query_status"] == "complete"
    assert "query_error_code" not in first_response.data
    assert first_response.data["browse_mode"] == "recent_suggestions"
    assert first_response.data["browse_status"] == "continuation"
    assert "browse_limit" not in first_response.data
    assert calls[0][1]["window_start"] == retained_start
    assert "continue_operation" not in calls[0][1]
    contract = SpanAttributeKeysResponseSerializer(data=first_response.data)
    assert contract.is_valid(), contract.errors
    cursor = first_response.data["next_cursor"]
    assert cursor
    legacy_scope_request = SimpleNamespace(
        user=SimpleNamespace(),
        auth=None,
        organization=None,
        workspace=None,
    )
    legacy_state = decode_list_cursor(
        cursor,
        resource="span_attribute_keys",
        scope=cursor_scope_for_request(
            legacy_scope_request,
            project_ids=[PROJECT_A],
        ),
        query={"project_id": PROJECT_A, "mode": "recent_attribute_keys"},
        page_size=10,
    )
    assert len(legacy_state.order) == 6
    assert legacy_state.order[0] == calls[0][1]["window_end"]
    assert legacy_state.order[1] == identity

    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_A, "page_size": 10, "cursor": cursor},
    )
    second_response = SpanAttributeKeysView.as_view()(second_request)

    assert second_response.status_code == 200
    assert second_response.data["result"][0]["key"] == "beta"
    assert calls[1][1]["window_start"] == retained_start
    assert "continue_operation" not in calls[1][1]
    assert calls[1][1]["window_end"] == calls[0][1]["window_end"]
    assert calls[1][1]["segment_start"] == (
        calls[0][1]["window_end"] - timedelta(hours=12)
    )
    assert calls[1][1]["before_identity"] == identity
    assert calls[1][1]["seen_key_digests"] == seen

    mismatched_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_B, "page_size": 10, "cursor": cursor},
    )
    mismatched_response = SpanAttributeKeysView.as_view()(mismatched_request)
    assert mismatched_response.status_code == 400
    assert len(calls) == 2


def test_span_attribute_key_workspace_cursor_advances_one_bounded_project_batch(
    monkeypatch,
):
    from tracer.views.span_attributes import SpanAttributeKeysView

    batch_calls = []
    read_calls = []

    def project_batch(_request, *, after_project_id=None):
        batch_calls.append(after_project_id)
        if after_project_id is None:
            return (PROJECT_A,), True
        assert after_project_id == PROJECT_A
        return (PROJECT_B,), False

    def read_page(_self, project_ids, **kwargs):
        read_calls.append((tuple(project_ids), kwargs))
        assert kwargs["dedupe_by_type"] is True
        assert kwargs["exhaustive_exact_types"] is False
        if tuple(project_ids) == (PROJECT_A,):
            return AttributeKeyCursorPageRead(
                (AttributeKeyRow("workspace.alpha", "string", 1),),
                _metadata(),
                False,
                "exhausted",
                kwargs["window_start"],
                None,
                None,
                0,
                (attribute_key_type_cursor_digest("workspace.alpha", "string"),),
            )
        assert tuple(project_ids) == (PROJECT_B,)
        assert kwargs["seen_key_contains"](
            attribute_key_type_cursor_digest("workspace.alpha", "string")
        )
        return AttributeKeyCursorPageRead(
            (AttributeKeyRow("workspace.beta", "number", 1),),
            _metadata(),
            False,
            "exhausted",
            kwargs["window_start"],
            None,
            None,
            0,
            (
                attribute_key_type_cursor_digest("workspace.alpha", "string"),
                attribute_key_type_cursor_digest("workspace.beta", "number"),
            ),
        )

    monkeypatch.setattr(
        "tracer.views.span_attributes._workspace_project_batch", project_batch
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._run_span_attribute_pg_read",
        _direct_pg_read,
    )
    monkeypatch.setattr(AttributeReadSelector, "read_key_cursor_page", read_page)

    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"workspace_scope": True, "page_size": 10},
    )
    first_response = SpanAttributeKeysView.as_view()(first_request)

    assert first_response.status_code == 200
    assert [row["key"] for row in first_response.data["result"]] == ["workspace.alpha"]
    assert first_response.data["has_more"] is True
    assert first_response.data["browse_status"] == "continuation"
    assert len(read_calls) == 1
    workspace_scope_request = SimpleNamespace(
        user=SimpleNamespace(),
        auth=None,
        organization=None,
        workspace=None,
    )
    workspace_state = decode_list_cursor(
        first_response.data["next_cursor"],
        resource="span_attribute_keys",
        scope=cursor_scope_for_request(workspace_scope_request, project_ids=[]),
        query={"workspace_scope": True, "mode": "recent_attribute_keys"},
        page_size=10,
    )
    assert workspace_state.order[0] == PROJECT_A
    assert isinstance(workspace_state.order[1], tuple)
    assert workspace_state.order[1] == ()
    assert workspace_state.order[2] is True

    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "workspace_scope": True,
            "page_size": 10,
            "cursor": first_response.data["next_cursor"],
        },
    )
    second_response = SpanAttributeKeysView.as_view()(second_request)

    assert second_response.status_code == 200
    assert [row["key"] for row in second_response.data["result"]] == ["workspace.beta"]
    assert second_response.data["has_more"] is False
    assert batch_calls == [None, PROJECT_A]
    assert len(read_calls) == 2
    assert read_calls[1][1]["seen_key_digests"] == (
        attribute_key_type_cursor_digest("workspace.alpha", "string"),
    )


def test_workspace_key_cursor_reaches_new_type_for_same_key_in_later_batch(
    monkeypatch,
):
    from tracer.views.span_attributes import SpanAttributeKeysView

    def project_batch(_request, *, after_project_id=None):
        return (
            ((PROJECT_A,), True) if after_project_id is None else ((PROJECT_B,), False)
        )

    def read_page(_self, project_ids, **kwargs):
        assert kwargs["dedupe_by_type"] is True
        assert kwargs["exhaustive_exact_types"] is False
        attr_type = "string" if tuple(project_ids) == (PROJECT_A,) else "number"
        digest = attribute_key_type_cursor_digest(
            "migrated.workspace.attribute", attr_type
        )
        if attr_type == "number":
            assert kwargs["seen_key_contains"](
                attribute_key_type_cursor_digest(
                    "migrated.workspace.attribute", "string"
                )
            )
            assert not kwargs["seen_key_contains"](digest)
        seen_after = (*kwargs["seen_key_digests"], digest)
        return AttributeKeyCursorPageRead(
            (
                AttributeKeyRow(
                    "migrated.workspace.attribute",
                    attr_type,
                    1,
                    (attr_type,),
                ),
            ),
            _metadata(),
            False,
            "exhausted",
            kwargs["window_start"],
            None,
            None,
            0,
            seen_after,
            appended_key_digests=(digest,),
            seen_key_count=kwargs["seen_key_count"] + 1,
        )

    monkeypatch.setattr(
        "tracer.views.span_attributes._workspace_project_batch", project_batch
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._run_span_attribute_pg_read",
        _direct_pg_read,
    )
    monkeypatch.setattr(AttributeReadSelector, "read_key_cursor_page", read_page)

    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"workspace_scope": True, "page_size": 10},
    )
    first_response = SpanAttributeKeysView.as_view()(first_request)
    assert first_response.status_code == 200
    assert first_response.data["result"][0]["key"] == ("migrated.workspace.attribute")
    assert first_response.data["result"][0]["type"] == "string"
    assert tuple(first_response.data["result"][0]["types"]) == ("string",)
    assert first_response.data["has_more"] is True

    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "workspace_scope": True,
            "page_size": 10,
            "cursor": first_response.data["next_cursor"],
        },
    )
    second_response = SpanAttributeKeysView.as_view()(second_request)
    assert second_response.status_code == 200
    assert second_response.data["result"][0]["key"] == ("migrated.workspace.attribute")
    assert second_response.data["result"][0]["type"] == "number"
    assert tuple(second_response.data["result"][0]["types"]) == ("number",)
    assert second_response.data["has_more"] is False


def test_workspace_exact_key_cursor_continues_after_match_for_later_batch_type(
    monkeypatch,
):
    from tracer.views.span_attributes import SpanAttributeKeysView

    def project_batch(_request, *, after_project_id=None):
        return (
            ((PROJECT_A,), True) if after_project_id is None else ((PROJECT_B,), False)
        )

    read_calls = []

    def read_page(_self, project_ids, **kwargs):
        read_calls.append((tuple(project_ids), kwargs))
        assert kwargs["dedupe_by_type"] is True
        assert kwargs["exhaustive_exact_types"] is True
        assert kwargs["exact_key"] == "migrated.workspace.attribute"
        attr_type = "string" if tuple(project_ids) == (PROJECT_A,) else "number"
        digest = attribute_key_type_cursor_digest(
            "migrated.workspace.attribute", attr_type
        )
        seen_after = (*kwargs["seen_key_digests"], digest)
        return AttributeKeyCursorPageRead(
            (
                AttributeKeyRow(
                    "migrated.workspace.attribute",
                    attr_type,
                    1,
                    (attr_type,),
                ),
            ),
            _metadata(),
            False,
            "exhausted",
            kwargs["window_start"],
            None,
            None,
            0,
            seen_after,
            appended_key_digests=(digest,),
            seen_key_count=kwargs["seen_key_count"] + 1,
        )

    monkeypatch.setattr(
        "tracer.views.span_attributes._workspace_project_batch", project_batch
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._run_span_attribute_pg_read",
        _direct_pg_read,
    )
    monkeypatch.setattr(AttributeReadSelector, "read_key_cursor_page", read_page)

    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "workspace_scope": True,
            "page_size": 10,
            "q": "migrated.workspace.attribute",
        },
    )
    first_response = SpanAttributeKeysView.as_view()(first_request)
    assert first_response.status_code == 200
    assert first_response.data["exact_match"] is True
    assert tuple(first_response.data["result"][0]["types"]) == ("string",)
    assert first_response.data["has_more"] is True

    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "workspace_scope": True,
            "page_size": 10,
            "q": "migrated.workspace.attribute",
            "cursor": first_response.data["next_cursor"],
        },
    )
    second_response = SpanAttributeKeysView.as_view()(second_request)
    assert second_response.status_code == 200
    assert second_response.data["exact_match"] is True
    assert tuple(second_response.data["result"][0]["types"]) == ("number",)
    assert second_response.data["has_more"] is False
    assert read_calls[1][1]["seen_key_contains"](
        attribute_key_type_cursor_digest("migrated.workspace.attribute", "string")
    )


def test_span_attribute_key_workspace_cursor_reauthorizes_active_project_batch(
    monkeypatch,
):
    from tracer.views.span_attributes import SpanAttributeKeysView

    calls = []

    def read_page(_self, project_ids, **kwargs):
        calls.append(tuple(project_ids))
        return AttributeKeyCursorPageRead(
            (),
            _metadata(),
            True,
            "continuation",
            kwargs["window_end"] - timedelta(hours=6),
            None,
            None,
            0,
            (),
        )

    monkeypatch.setattr(
        "tracer.views.span_attributes._workspace_project_batch",
        lambda _request, *, after_project_id=None: ((PROJECT_A,), False),
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._run_span_attribute_pg_read",
        _direct_pg_read,
    )
    monkeypatch.setattr(AttributeReadSelector, "read_key_cursor_page", read_page)
    monkeypatch.setattr(
        "tracer.views.span_attributes._workspace_projects_are_in_request_scope",
        lambda _request, _project_ids: False,
    )
    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"workspace_scope": True, "page_size": 10},
    )
    first_response = SpanAttributeKeysView.as_view()(first_request)
    assert first_response.status_code == 200

    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "workspace_scope": True,
            "page_size": 10,
            "cursor": first_response.data["next_cursor"],
        },
    )
    second_response = SpanAttributeKeysView.as_view()(second_request)

    assert second_response.status_code == 400
    assert second_response.data["code"] == "cursor_mismatch"
    assert calls == [(PROJECT_A,)]


def test_span_attribute_key_workspace_exact_search_continues_across_batches(
    monkeypatch,
):
    from tracer.views.span_attributes import SpanAttributeKeysView

    batch_calls = []
    read_calls = []

    def project_batch(_request, *, after_project_id=None):
        batch_calls.append(after_project_id)
        return (
            ((PROJECT_A,), True) if after_project_id is None else ((PROJECT_B,), False)
        )

    def read_page(_self, project_ids, **kwargs):
        read_calls.append((tuple(project_ids), kwargs["exact_key"]))
        rows = (
            ()
            if tuple(project_ids) == (PROJECT_A,)
            else (AttributeKeyRow("historical.exact", "string", 1),)
        )
        return AttributeKeyCursorPageRead(
            rows,
            _metadata(),
            False,
            "exhausted",
            kwargs["window_start"],
            None,
            None,
            0,
            tuple(attribute_key_cursor_digest(row.key) for row in rows),
        )

    monkeypatch.setattr(
        "tracer.views.span_attributes._workspace_project_batch", project_batch
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._run_span_attribute_pg_read",
        _direct_pg_read,
    )
    monkeypatch.setattr(AttributeReadSelector, "read_key_cursor_page", read_page)
    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "workspace_scope": True,
            "page_size": 10,
            "q": "historical.exact",
        },
    )
    first_response = SpanAttributeKeysView.as_view()(first_request)

    assert first_response.status_code == 200
    assert first_response.data["exact_match"] is False
    assert first_response.data["has_more"] is True
    assert len(read_calls) == 1

    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "workspace_scope": True,
            "page_size": 10,
            "q": "historical.exact",
            "cursor": first_response.data["next_cursor"],
        },
    )
    second_response = SpanAttributeKeysView.as_view()(second_request)

    assert second_response.status_code == 200
    assert second_response.data["exact_match"] is True
    assert second_response.data["has_more"] is False
    assert batch_calls == [None, PROJECT_A]
    assert read_calls == [
        ((PROJECT_A,), "historical.exact"),
        ((PROJECT_B,), "historical.exact"),
    ]


def test_span_attribute_key_workspace_cursor_handles_an_empty_workspace(monkeypatch):
    from tracer.views.span_attributes import SpanAttributeKeysView

    monkeypatch.setattr(
        "tracer.views.span_attributes._workspace_project_batch",
        lambda _request, *, after_project_id=None: ((), False),
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._run_span_attribute_pg_read",
        _direct_pg_read,
    )
    monkeypatch.setattr(
        AttributeReadSelector,
        "read_key_cursor_page",
        lambda *_args, **_kwargs: pytest.fail(
            "an empty workspace must not issue a ClickHouse attribute read"
        ),
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"workspace_scope": True, "page_size": 10},
    )

    response = SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert response.data["result"] == []
    assert response.data["has_more"] is False
    assert response.data["next_cursor"] is None


def test_span_attribute_key_workspace_cursor_terminates_if_next_batch_was_deleted(
    monkeypatch,
):
    from tracer.views.span_attributes import SpanAttributeKeysView

    batch_calls = []
    read_calls = []

    def project_batch(_request, *, after_project_id=None):
        batch_calls.append(after_project_id)
        return ((PROJECT_A,), True) if after_project_id is None else ((), False)

    def read_page(_self, project_ids, **kwargs):
        read_calls.append(tuple(project_ids))
        return AttributeKeyCursorPageRead(
            (AttributeKeyRow("surviving.attribute", "string", 1),),
            _metadata(),
            False,
            "exhausted",
            kwargs["window_start"],
            None,
            None,
            0,
            (attribute_key_cursor_digest("surviving.attribute"),),
        )

    monkeypatch.setattr(
        "tracer.views.span_attributes._workspace_project_batch", project_batch
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._run_span_attribute_pg_read",
        _direct_pg_read,
    )
    monkeypatch.setattr(AttributeReadSelector, "read_key_cursor_page", read_page)
    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"workspace_scope": True, "page_size": 10},
    )
    first_response = SpanAttributeKeysView.as_view()(first_request)
    assert first_response.status_code == 200
    assert first_response.data["has_more"] is True

    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "workspace_scope": True,
            "page_size": 10,
            "cursor": first_response.data["next_cursor"],
        },
    )
    second_response = SpanAttributeKeysView.as_view()(second_request)

    assert second_response.status_code == 200
    assert second_response.data["result"] == []
    assert second_response.data["has_more"] is False
    assert second_response.data["next_cursor"] is None
    assert batch_calls == [None, PROJECT_A]
    assert read_calls == [(PROJECT_A,)]


def test_span_attribute_key_workspace_cursor_is_signed_to_workspace(monkeypatch):
    from tracer.views.span_attributes import SpanAttributeKeysView

    calls = []
    monkeypatch.setattr(
        "tracer.views.span_attributes._workspace_project_batch",
        lambda _request, *, after_project_id=None: ((PROJECT_A,), False),
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._run_span_attribute_pg_read",
        _direct_pg_read,
    )

    def read_page(_self, project_ids, **kwargs):
        calls.append(tuple(project_ids))
        return AttributeKeyCursorPageRead(
            (),
            _metadata(),
            True,
            "continuation",
            kwargs["window_end"] - timedelta(hours=6),
            None,
            None,
            0,
            (),
        )

    monkeypatch.setattr(AttributeReadSelector, "read_key_cursor_page", read_page)
    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"workspace_scope": True, "page_size": 10},
    )
    first_request.workspace = SimpleNamespace(pk="workspace-a")
    first_response = SpanAttributeKeysView.as_view()(first_request)
    assert first_response.status_code == 200

    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "workspace_scope": True,
            "page_size": 10,
            "cursor": first_response.data["next_cursor"],
        },
    )
    second_request.workspace = SimpleNamespace(pk="workspace-b")
    second_response = SpanAttributeKeysView.as_view()(second_request)

    assert second_response.status_code == 400
    assert second_response.data["code"] == "cursor_mismatch"
    assert calls == [(PROJECT_A,)]


def test_span_attribute_workspace_project_read_sets_remaining_pg_timeout(
    monkeypatch,
):
    from tracer.views import span_attributes as span_attribute_view

    cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    cursor_context.__exit__.return_value = False
    connection = SimpleNamespace(
        vendor="postgresql",
        in_atomic_block=False,
        cursor=lambda: cursor_context,
    )
    deadline = MagicMock()
    deadline.remaining_ms.side_effect = [1_234, 900]
    monkeypatch.setattr(span_attribute_view, "connection", connection)
    monkeypatch.setattr(
        span_attribute_view,
        "transaction",
        SimpleNamespace(atomic=lambda: nullcontext()),
    )

    result = span_attribute_view._run_span_attribute_pg_read(
        deadline,
        lambda: (PROJECT_A,),
    )

    assert result == (PROJECT_A,)
    assert deadline.remaining_ms.call_args_list == [
        mock_call(ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS),
        mock_call(floor_ms=1),
    ]
    assert cursor.execute.call_args_list == [
        mock_call("SET TRANSACTION READ ONLY"),
        mock_call("SELECT set_config('statement_timeout', %s, true)", ["1234"]),
    ]


def test_span_attribute_key_api_tracking_limit_is_terminal(monkeypatch):
    from tracer.views.span_attributes import SpanAttributeKeysView

    seen = tuple(
        attribute_key_cursor_digest(f"prior-{index}")
        for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS)
    )
    monkeypatch.setattr(
        AttributeReadSelector,
        "read_key_cursor_page",
        lambda _self, _project_ids, **kwargs: AttributeKeyCursorPageRead(
            (AttributeKeyRow("final_verified_key", "string", 1),),
            _metadata(),
            False,
            "limit_reached",
            kwargs["window_start"],
            None,
            None,
            0,
            seen,
        ),
    )
    monkeypatch.setattr(
        AttributeReadSelector,
        "retained_window_start",
        lambda _self, _projects, *, window_end: window_end - timedelta(days=400),
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_A, "page_size": 10},
    )

    response = SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert response.data["result"][0]["key"] == "final_verified_key"
    assert response.data["result"][0]["type"] == "string"
    assert response.data["result"][0]["count"] == 1
    assert response.data["result"][0]["count_exact"] is False
    assert response.data["has_more"] is False
    assert response.data["next_cursor"] is None
    assert response.data["browse_status"] == "limit_reached"
    contract = SpanAttributeKeysResponseSerializer(data=response.data)
    assert contract.is_valid(), contract.errors


def test_span_attribute_key_api_cursor_binds_and_continues_exact_search(monkeypatch):
    from tracer.views.span_attributes import SpanAttributeKeysView

    calls = []

    def read_page(self, project_ids, **kwargs):
        calls.append((project_ids, kwargs))
        if len(calls) == 1:
            return AttributeKeyCursorPageRead(
                (),
                _metadata(),
                True,
                "continuation",
                kwargs["window_end"] - timedelta(hours=6),
                None,
                None,
                0,
                (),
                kwargs["window_end"] - timedelta(hours=12),
            )
        return AttributeKeyCursorPageRead(
            (AttributeKeyRow("final_status", "string", 1),),
            _metadata(),
            False,
            "exhausted",
            kwargs["window_start"],
            None,
            None,
            0,
            (attribute_key_cursor_digest("final_status"),),
        )

    monkeypatch.setattr(AttributeReadSelector, "read_key_cursor_page", read_page)
    monkeypatch.setattr(
        AttributeReadSelector,
        "retained_window_start",
        lambda _self, _projects, *, window_end: NOW - timedelta(days=400),
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_A, "page_size": 10, "q": "final_status"},
    )
    first_response = SpanAttributeKeysView.as_view()(first_request)

    assert first_response.status_code == 200
    assert first_response.data["result"] == []
    assert first_response.data["lookup_mode"] == "exact"
    assert first_response.data["exact_match"] is False
    assert first_response.data["has_more"] is True
    assert calls[0][1]["exact_key"] == "final_status"
    cursor = first_response.data["next_cursor"]
    assert cursor

    replay_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "project_id": PROJECT_A,
            "page_size": 10,
            "q": "another_key",
            "cursor": cursor,
        },
    )
    replay_response = SpanAttributeKeysView.as_view()(replay_request)
    assert replay_response.status_code == 400
    assert len(calls) == 1

    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "project_id": PROJECT_A,
            "page_size": 10,
            "q": "final_status",
            "cursor": cursor,
        },
    )
    second_response = SpanAttributeKeysView.as_view()(second_request)

    assert second_response.status_code == 200
    assert second_response.data["result"][0]["key"] == "final_status"
    assert second_response.data["lookup_mode"] == "exact"
    assert second_response.data["exact_match"] is True
    assert second_response.data["has_more"] is False
    assert calls[1][1]["exact_key"] == "final_status"
    assert calls[1][1]["segment_start"] == (
        calls[0][1]["window_end"] - timedelta(hours=12)
    )


def test_span_attribute_key_api_eval_mapping_mode_is_signed_and_reads_all_json(
    monkeypatch,
):
    from tracer.views.span_attributes import SpanAttributeKeysView

    calls = []

    def read_page(self, project_ids, **kwargs):
        calls.append((self._json_attribute_mode, project_ids, kwargs))
        if len(calls) == 1:
            return AttributeKeyCursorPageRead(
                (AttributeKeyRow("json_only", "json", 1),),
                _metadata(),
                True,
                "continuation",
                kwargs["window_end"] - timedelta(hours=6),
                None,
                None,
                0,
                (attribute_key_cursor_digest("json_only"),),
            )
        return AttributeKeyCursorPageRead(
            (),
            _metadata(),
            False,
            "exhausted",
            kwargs["window_start"],
            None,
            None,
            0,
            (attribute_key_cursor_digest("json_only"),),
        )

    monkeypatch.setattr(AttributeReadSelector, "read_key_cursor_page", read_page)
    monkeypatch.setattr(
        AttributeReadSelector,
        "retained_window_start",
        lambda _self, _projects, *, window_end: NOW - timedelta(days=400),
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "project_id": PROJECT_A,
            "page_size": 10,
            "discovery_mode": "eval_mapping",
        },
    )
    first_response = SpanAttributeKeysView.as_view()(first_request)

    assert first_response.status_code == 200
    assert first_response.data["result"][0]["key"] == "json_only"
    assert first_response.data["result"][0]["type"] == "json"
    assert calls[0][0] == "all"
    cursor = first_response.data["next_cursor"]
    assert cursor

    replay_as_filter = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_A, "page_size": 10, "cursor": cursor},
    )
    replay_response = SpanAttributeKeysView.as_view()(replay_as_filter)
    assert replay_response.status_code == 400
    assert len(calls) == 1

    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "project_id": PROJECT_A,
            "page_size": 10,
            "discovery_mode": "eval_mapping",
            "cursor": cursor,
        },
    )
    second_response = SpanAttributeKeysView.as_view()(second_request)
    assert second_response.status_code == 200
    assert second_response.data["has_more"] is False
    assert calls[1][0] == "all"


@pytest.mark.parametrize(
    "failure_code",
    [
        pytest.param(None, id="request-wall-deadline"),
        pytest.param(159, id="clickhouse-timeout"),
        pytest.param(241, id="clickhouse-memory-budget"),
        pytest.param(497, id="clickhouse-read-permission"),
    ],
)
def test_span_attribute_key_cursor_operational_failures_are_sanitized_503(
    monkeypatch, failure_code
):
    from tracer.views.span_attributes import SpanAttributeKeysView

    def fail(*_args, **_kwargs):
        if failure_code is None:
            raise ReadDeadlineExceeded("private key cursor deadline")
        raise ServerException("private key cursor ClickHouse detail", failure_code)

    monkeypatch.setattr(AttributeReadSelector, "read_key_cursor_page", fail)
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_A, "page_size": 10},
    )

    response = SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    payload = json.dumps(response.data)
    assert "temporarily unavailable" in payload
    assert "private key cursor" not in payload
    assert "next_cursor" not in payload
    assert "has_more" not in payload
    assert "query_complete" not in payload


def test_span_attribute_key_cursor_incomplete_metadata_is_a_sanitized_503(
    monkeypatch,
):
    from tracer.views.span_attributes import SpanAttributeKeysView

    monkeypatch.setattr(
        AttributeReadSelector,
        "retained_window_start",
        lambda _self, _projects, *, window_end: NOW - timedelta(days=400),
    )
    monkeypatch.setattr(
        AttributeReadSelector,
        "read_key_cursor_page",
        lambda _self, _project_ids, **kwargs: AttributeKeyCursorPageRead(
            (AttributeKeyRow("final_status", "string", 1),),
            _metadata(complete=False, error_code="read_budget_exceeded"),
            True,
            "continuation",
            kwargs["window_end"],
            None,
            None,
            0,
            (attribute_key_cursor_digest("final_status"),),
        ),
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_A, "page_size": 10},
    )

    response = SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 503
    payload = json.dumps(response.data)
    assert "temporarily unavailable" in payload
    assert "final_status" not in payload
    assert "read_budget_exceeded" not in payload
    assert "next_cursor" not in payload


def _max_entropy_physical_id(label: str, length: int = 255) -> str:
    encoded = ""
    index = 0
    while len(encoded) < length:
        encoded += base64.urlsafe_b64encode(
            hashlib.sha512(f"{label}-{index}".encode()).digest()
        ).decode()
        index += 1
    return encoded[:length]


@pytest.mark.parametrize("checkpoint", ["before", "resume"])
def test_span_attribute_key_cursor_reachable_full_url_stays_below_limit(checkpoint):
    seen_reference = ("state", "a" * 64)
    scope = {
        "principal_id": "00000000-0000-4000-8000-000000000001",
        "auth_type": "TokenAuthentication",
        "auth_id": "00000000-0000-4000-8000-000000000002",
        "organization_id": "00000000-0000-4000-8000-000000000003",
        "workspace_id": "00000000-0000-4000-8000-000000000004",
        "project_ids": [PROJECT_A],
    }
    identity = (
        PROJECT_A,
        _max_entropy_physical_id(f"{checkpoint}-trace"),
        _max_entropy_physical_id(f"{checkpoint}-span"),
        NOW - timedelta(microseconds=1),
    )
    before_identity = identity if checkpoint == "before" else ()
    resume_identity = identity if checkpoint == "resume" else ()
    cursor = encode_list_cursor(
        resource="span_attribute_keys",
        scope=scope,
        query={"project_id": PROJECT_A, "mode": "recent_attribute_keys"},
        page_size=50,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        order=(
            NOW,
            before_identity,
            resume_identity,
            9_223_372_036_854_775_807 if checkpoint == "resume" else 0,
            seen_reference,
        ),
        seen_rows=1_000_000,
    )
    full_url = "https://api.futureagi.com/api/traces/span-attribute-keys/?" + urlencode(
        {"project_id": PROJECT_A, "page_size": 50, "cursor": cursor}
    )

    assert len(cursor.encode("utf-8")) <= ATTRIBUTE_KEY_CURSOR_MAX_TOKEN_BYTES
    assert len(full_url.encode("utf-8")) < 8 * 1024


def test_span_attribute_key_api_does_not_turn_cursor_size_into_vocabulary_cap(
    monkeypatch,
):
    from tracer.views.span_attributes import SpanAttributeKeysView

    oversized_identity = (
        PROJECT_A,
        _max_entropy_physical_id("oversized-trace", 8_000),
        _max_entropy_physical_id("oversized-span", 8_000),
        NOW - timedelta(microseconds=1),
    )

    monkeypatch.setattr(
        AttributeReadSelector,
        "read_key_cursor_page",
        lambda _self, _project_ids, **kwargs: AttributeKeyCursorPageRead(
            (AttributeKeyRow("recent_key", "string", 1),),
            _metadata(),
            True,
            "continuation",
            kwargs["window_end"],
            oversized_identity,
            None,
            0,
            (attribute_key_cursor_digest("recent_key"),),
        ),
    )
    monkeypatch.setattr(
        AttributeReadSelector,
        "retained_window_start",
        lambda _self, _projects, *, window_end: NOW - timedelta(days=400),
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_A, "page_size": 10},
    )

    response = SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert response.data["result"][0]["key"] == "recent_key"
    assert response.data["has_more"] is True
    assert response.data["next_cursor"]
    assert response.data["browse_status"] == "continuation"
    assert response.data["query_status"] == "complete"
    assert "query_error_code" not in response.data


def test_span_attribute_detail_contract_validates_key_and_exposes_read_state():
    query = SpanAttributeDetailQuerySerializer(
        data={"project_id": uuid.uuid4(), "key": "customer.%_status\\path"}
    )

    assert query.is_valid(), query.errors
    assert query.validated_data["key"] == "customer.%_status\\path"
    assert query.validated_data["refresh"] is False
    for invalid_key in ("", "contains\x00control", "é" * 257):
        invalid = SpanAttributeDetailQuerySerializer(
            data={"project_id": uuid.uuid4(), "key": invalid_key}
        )
        assert not invalid.is_valid()
        assert "key" in invalid.errors
    assert {
        "query_complete",
        "query_status",
        "query_sampled",
        "query_error_code",
        "query_window_start",
        "query_window_end",
        "query_refreshing",
        "query_refresh_failed",
    } <= set(SpanAttributeDetailResponseSerializer().fields)


def test_eval_attribute_picker_contract_accepts_general_exact_key_probe():
    project_id = uuid.uuid4()
    query = ObservationAttributeListQuerySerializer(
        data={
            "filters": {"project_id": str(project_id)},
            "row_type": "spans",
            "q": "customer.%_status\\path",
        }
    )

    assert query.is_valid(), query.errors
    assert query.validated_data["q"] == "customer.%_status\\path"
    assert {
        "query_complete",
        "query_status",
        "query_error_code",
        "query_window_start",
        "query_window_end",
    } <= set(ObservationAttributeListResponseSerializer().fields)


def _bypass_dashboard_filter_value_pg_read(monkeypatch):
    """Keep selector/view contract tests isolated from PostgreSQL plumbing."""

    monkeypatch.setattr(
        "tracer.views.dashboard._run_filter_value_pg_read",
        lambda _deadline, read: read(),
    )
    monkeypatch.setattr(
        "tracer.views.dashboard._bounded_authorized_filter_value_projects",
        lambda _request, project_ids, *, deadline: tuple(project_ids),
    )


def test_dashboard_final_status_picker_returns_rejected_from_selector(
    monkeypatch,
):
    from tracer.views.dashboard import DashboardViewSet

    _bypass_dashboard_filter_value_pg_read(monkeypatch)
    captured: dict[str, Any] = {}

    def read_values(self, project_ids, key, **kwargs):
        captured.update(
            project_ids=project_ids,
            key=key,
            typed_only=self._typed_only,
            json_attribute_mode=self._json_attribute_mode,
            kwargs=kwargs,
        )
        return AttributeValueRead(
            (AttributeValueRow("Rejected", "string", 1),),
            _metadata(),
        )

    monkeypatch.setattr(AttributeReadSelector, "read_values", read_values)
    monkeypatch.setattr("tracer.views.dashboard.is_clickhouse_enabled", lambda: False)
    monkeypatch.setattr(
        "tracer.views.dashboard.project_queryset_for_request",
        lambda _request: _ProjectScope([PROJECT_A]),
    )
    monkeypatch.setattr(
        "tracer.views.dashboard.AnalyticsQueryService.execute_ch_query",
        lambda *_args, **_kwargs: pytest.fail("legacy ClickHouse must not be queried"),
    )

    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "final_status",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_A,
            "source": "traces",
        },
    )
    response = DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 200
    payload = response.data["result"]
    assert payload["values"] == [
        {"value": "Rejected", "type": "string", "label": "Rejected"}
    ]
    assert payload["query_complete"] is True
    assert captured["project_ids"] == [PROJECT_A]
    assert captured["key"] == "final_status"
    assert captured["typed_only"] is True
    assert captured["json_attribute_mode"] == "arrays"


def test_dashboard_json_array_value_picker_preserves_scalar_json_types(monkeypatch):
    from tracer.views.dashboard import DashboardViewSet

    _bypass_dashboard_filter_value_pg_read(monkeypatch)

    def read_values(self, project_ids, key, **kwargs):
        assert self._json_attribute_mode == "arrays"
        return AttributeValueRead(
            (
                AttributeValueRow(True, "array", 2),
                AttributeValueRow(7, "array", 1),
                AttributeValueRow("seven", "array", 1),
            ),
            _metadata(),
        )

    monkeypatch.setattr(AttributeReadSelector, "read_values", read_values)
    monkeypatch.setattr("tracer.views.dashboard.is_clickhouse_enabled", lambda: False)
    monkeypatch.setattr(
        "tracer.views.dashboard.project_queryset_for_request",
        lambda _request: _ProjectScope([PROJECT_A]),
    )

    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "json_choices",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_A,
            "source": "traces",
        },
    )
    response = DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 200
    assert response.data["result"]["values"] == [
        {"value": True, "type": "array", "label": "true"},
        {"value": 7, "type": "array", "label": "7"},
        {"value": "seven", "type": "array", "label": "seven"},
    ]


def test_dashboard_empty_sample_limit_is_an_explicit_200_sample(monkeypatch):
    from tracer.views.dashboard import DashboardViewSet

    _bypass_dashboard_filter_value_pg_read(monkeypatch)

    monkeypatch.setattr(
        AttributeReadSelector,
        "read_values",
        lambda *_args, **_kwargs: AttributeValueRead(
            (),
            _metadata(complete=False, error_code="sample_limit"),
        ),
    )
    monkeypatch.setattr("tracer.views.dashboard.is_clickhouse_enabled", lambda: False)
    monkeypatch.setattr(
        "tracer.views.dashboard.project_queryset_for_request",
        lambda _request: _ProjectScope([PROJECT_A]),
    )

    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "absent_heavy_key",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_A,
            "source": "traces",
        },
    )
    response = DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 200
    assert response.data["result"]["values"] == []
    assert response.data["result"]["query_complete"] is False
    assert response.data["result"]["query_status"] == "sampled"
    assert response.data["result"]["query_error_code"] == "sample_limit"


def test_dashboard_empty_read_budget_remains_a_sanitized_503(monkeypatch):
    from tracer.views.dashboard import DashboardViewSet

    _bypass_dashboard_filter_value_pg_read(monkeypatch)

    monkeypatch.setattr(
        AttributeReadSelector,
        "read_values",
        lambda *_args, **_kwargs: AttributeValueRead(
            (),
            _metadata(complete=False, error_code="read_budget_exceeded"),
        ),
    )
    monkeypatch.setattr("tracer.views.dashboard.is_clickhouse_enabled", lambda: False)
    monkeypatch.setattr(
        "tracer.views.dashboard.project_queryset_for_request",
        lambda _request: _ProjectScope([PROJECT_A]),
    )

    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "absent_heavy_key",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_A,
            "source": "traces",
        },
    )
    response = DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 503
    payload = json.dumps(response.data)
    assert "temporarily unavailable" in payload
    assert "read_budget_exceeded" not in payload


@pytest.mark.parametrize("code", [159, 241, 307])
def test_dashboard_budget_errors_return_sanitized_503_and_are_not_retried(
    code, monkeypatch
):
    from tracer.views.dashboard import DashboardViewSet

    _bypass_dashboard_filter_value_pg_read(monkeypatch)
    calls = 0

    def fail(self, query, params, *, timeout_ms, settings):
        nonlocal calls
        calls += 1
        raise ServerException("secret SQL and stack detail", code)

    monkeypatch.setattr(V2AttributeQueryExecutor, "execute", fail)
    monkeypatch.setattr("tracer.views.dashboard.is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(
        "tracer.views.dashboard.project_queryset_for_request",
        lambda _request: _ProjectScope([PROJECT_A]),
    )

    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "final_status",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_A,
            "source": "traces",
        },
    )
    response = DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    serialized = json.dumps(response.data)
    assert "temporarily unavailable" in serialized
    assert "secret" not in serialized
    assert "SELECT" not in serialized
    assert calls == 1


@pytest.mark.parametrize(
    "failure_code",
    [
        pytest.param(None, id="request-wall-deadline"),
        pytest.param(159, id="clickhouse-timeout"),
        pytest.param(241, id="clickhouse-memory-budget"),
        pytest.param(497, id="clickhouse-read-permission"),
    ],
)
def test_dashboard_cursor_operational_failure_returns_503_without_values_or_cursor(
    monkeypatch, failure_code
):
    from tracer.views.dashboard import DashboardViewSet

    _bypass_dashboard_filter_value_pg_read(monkeypatch)
    calls = 0

    def fail(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        assert "version_ceiling" not in kwargs
        if failure_code is None:
            raise ReadDeadlineExceeded("private cursor deadline")
        raise ServerException("private cursor ClickHouse detail", failure_code)

    monkeypatch.setattr(AttributeReadSelector, "read_value_cursor_page", fail)
    monkeypatch.setattr(
        AttributeReadSelector,
        "retained_window_start",
        lambda _self, _projects, *, window_end: NOW - timedelta(days=400),
    )
    monkeypatch.setattr(
        "tracer.views.dashboard.project_queryset_for_request",
        lambda _request: _ProjectScope([PROJECT_A]),
    )

    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "final_status",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_A,
            "source": "traces",
            "page_size": 10,
        },
    )
    response = DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 503
    assert calls == 1
    payload = json.dumps(response.data)
    assert "temporarily unavailable" in payload
    assert "private cursor deadline" not in payload
    assert "private cursor ClickHouse detail" not in payload
    assert "next_cursor" not in payload
    assert '"values"' not in payload


def test_dashboard_cursor_incomplete_metadata_is_a_sanitized_503(monkeypatch):
    from tracer.views.dashboard import DashboardViewSet

    _bypass_dashboard_filter_value_pg_read(monkeypatch)

    monkeypatch.setattr(
        AttributeReadSelector,
        "read_value_cursor_page",
        lambda _self, _project_ids, _key, **kwargs: AttributeValueCursorPageRead(
            (AttributeValueRow("Rechazado", "string", 1),),
            _metadata(complete=False, error_code="read_budget_exceeded"),
            True,
            kwargs["window_end"],
            None,
            None,
            0,
            (attribute_value_cursor_digest("string", "Rechazado"),),
            "continuation",
        ),
    )
    monkeypatch.setattr(
        AttributeReadSelector,
        "retained_window_start",
        lambda _self, _projects, *, window_end: NOW - timedelta(days=400),
    )
    monkeypatch.setattr(
        "tracer.views.dashboard.project_queryset_for_request",
        lambda _request: _ProjectScope([PROJECT_A]),
    )
    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "final_status",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_A,
            "source": "traces",
            "page_size": 10,
        },
    )

    response = DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 503
    payload = json.dumps(response.data)
    assert "temporarily unavailable" in payload
    assert "Rechazado" not in payload
    assert "read_budget_exceeded" not in payload
    assert "next_cursor" not in payload


def test_eval_picker_uses_selector_for_keys_and_cardinality_without_pg_fallback(
    monkeypatch,
):
    from tracer.views.observation_span import ObservationSpanView

    captured: dict[str, Any] = {}

    def discover_keys(self, project_ids, exact_key=None):
        captured.update(
            typed_only=self._typed_only,
            json_attribute_mode=self._json_attribute_mode,
            exact_key=exact_key,
        )
        return AttributeKeyRead(
            (AttributeKeyRow(exact_key or "fallback", "json", 1),),
            _metadata(),
        )

    monkeypatch.setattr(
        AttributeReadSelector,
        "discover_keys",
        discover_keys,
    )
    monkeypatch.setattr(
        AttributeReadSelector,
        "sample_cardinality",
        lambda self, project_ids, **kwargs: AttributeCardinalityRead(1, 1, _metadata()),
    )
    monkeypatch.setattr(
        "tracer.views.observation_span.ObservationSpanView._get_span_attribute_keys",
        lambda *_args, **_kwargs: pytest.fail("PG/legacy inventory fallback used"),
    )
    monkeypatch.setattr(
        "tracer.views.observation_span.ObservationSpanView._max_spans_per_trace",
        lambda *_args, **_kwargs: pytest.fail("PG/legacy cardinality fallback used"),
    )
    monkeypatch.setattr(
        "tracer.views.observation_span.ObservationSpanView._max_traces_per_session",
        lambda *_args, **_kwargs: pytest.fail("PG cardinality fallback used"),
    )
    monkeypatch.setattr(
        ObservationSpanView,
        "_attribute_project_for_request",
        staticmethod(lambda _request, _project_id: True),
    )

    request = _authenticated_get(
        "/tracer/observation-span/get_eval_attributes_list/",
        {
            "filters": json.dumps({"project_id": PROJECT_A}),
            "row_type": "traces",
            "q": "rare.customer.key",
        },
    )
    response = ObservationSpanView.as_view({"get": "get_eval_attributes_list"})(request)

    assert response.status_code == 200
    payload = response.data
    assert "spans.0.rare.customer.key" in payload["result"]
    assert payload["query_complete"] is True
    assert captured == {
        "typed_only": True,
        "json_attribute_mode": "all",
        "exact_key": "rare.customer.key",
    }


def test_eval_picker_generic_inventory_prefers_one_recent_dense_segment(monkeypatch):
    """A dense active project must not begin with the legacy seven-day scan."""

    from tracer.views.observation_span import ObservationSpanView

    calls: list[dict[str, Any]] = []

    def discover_keys(self, project_ids, exact_key=None, **kwargs):
        calls.append(
            {
                "project_ids": project_ids,
                "exact_key": exact_key,
                **kwargs,
            }
        )
        window_start = kwargs["window_start"]
        window_end = kwargs["window_end"]
        return AttributeKeyRead(
            (AttributeKeyRow("final_status", "string", 1),),
            AttributeReadMetadata(
                query_complete=True,
                query_status="complete",
                query_error_code=None,
                query_window_start=window_start,
                query_window_end=window_end,
                query_count=2,
            ),
        )

    monkeypatch.setattr(AttributeReadSelector, "discover_keys", discover_keys)
    monkeypatch.setattr(
        ObservationSpanView,
        "_attribute_project_for_request",
        staticmethod(lambda _request, _project_id: True),
    )
    request = _authenticated_get(
        "/tracer/observation-span/get_eval_attributes_list/",
        {"filters": json.dumps({"project_id": PROJECT_A})},
    )

    response = ObservationSpanView.as_view({"get": "get_eval_attributes_list"})(request)

    assert response.status_code == 200
    assert response.data["result"] == ["final_status"]
    assert response.data["query_complete"] is False
    assert response.data["query_status"] == "sampled"
    assert response.data["query_error_code"] == "sample_limit"
    assert len(calls) == 1
    assert calls[0]["project_ids"] == [PROJECT_A]
    assert calls[0]["exact_key"] is None
    assert (
        calls[0]["window_end"] - calls[0]["window_start"]
        == ATTRIBUTE_READ_EXPLICIT_SEGMENT
    )


def test_eval_picker_empty_recent_segment_preserves_historical_fallback(monkeypatch):
    """Sparse projects still search the existing adaptive historical bands."""

    from tracer.views.observation_span import ObservationSpanView

    calls: list[dict[str, Any]] = []

    def discover_keys(self, project_ids, exact_key=None, **kwargs):
        calls.append(
            {
                "project_ids": project_ids,
                "exact_key": exact_key,
                **kwargs,
            }
        )
        if kwargs:
            return AttributeKeyRead(
                (),
                AttributeReadMetadata(
                    query_complete=True,
                    query_status="complete",
                    query_error_code=None,
                    query_window_start=kwargs["window_start"],
                    query_window_end=kwargs["window_end"],
                    query_count=2,
                ),
            )
        return AttributeKeyRead(
            (AttributeKeyRow("historical_status", "string", 1),),
            _metadata(),
        )

    monkeypatch.setattr(AttributeReadSelector, "discover_keys", discover_keys)
    monkeypatch.setattr(
        ObservationSpanView,
        "_attribute_project_for_request",
        staticmethod(lambda _request, _project_id: True),
    )
    request = _authenticated_get(
        "/tracer/observation-span/get_eval_attributes_list/",
        {"filters": json.dumps({"project_id": PROJECT_A})},
    )

    response = ObservationSpanView.as_view({"get": "get_eval_attributes_list"})(request)

    assert response.status_code == 200
    assert response.data["result"] == ["historical_status"]
    assert response.data["query_complete"] is True
    assert len(calls) == 2
    assert calls[0]["window_end"] - calls[0]["window_start"] == timedelta(hours=6)
    assert calls[1] == {
        "project_ids": [PROJECT_A],
        "exact_key": None,
    }


def test_session_eval_picker_without_verified_sessions_returns_static_sample(
    monkeypatch,
):
    """No recent session rows must not turn valid static fields into a 503."""

    from tracer.views.observation_span import ObservationSpanView

    monkeypatch.setattr(
        AttributeReadSelector,
        "discover_keys",
        lambda *_args, **_kwargs: AttributeKeyRead(
            (AttributeKeyRow("call.participant_phone_number", "string", 1),),
            _metadata(),
        ),
    )

    def sample_cardinality(self, project_ids, **kwargs):
        assert project_ids == [PROJECT_A]
        assert kwargs == {"ensure_session_sample": True}
        return AttributeCardinalityRead(
            max_spans_per_trace=64,
            max_traces_per_session=0,
            metadata=_metadata(
                complete=False,
                error_code="sample_limit",
                sampled=True,
            ),
        )

    monkeypatch.setattr(
        AttributeReadSelector,
        "sample_cardinality",
        sample_cardinality,
    )
    monkeypatch.setattr(
        ObservationSpanView,
        "_attribute_project_for_request",
        staticmethod(lambda _request, _project_id: True),
    )
    request = _authenticated_get(
        "/tracer/observation-span/get_eval_attributes_list/",
        {
            "filters": json.dumps({"project_id": PROJECT_A}),
            "row_type": "sessions",
            "q": "call.participant_phone_number",
        },
    )

    response = ObservationSpanView.as_view({"get": "get_eval_attributes_list"})(request)

    assert response.status_code == 200
    assert response.data["result"] == ["name", "bookmarked"]
    assert response.data["query_complete"] is False
    assert response.data["query_status"] == "sampled"
    assert response.data["query_error_code"] == "sample_limit"
    assert not any(path.startswith("traces.") for path in response.data["result"])


@pytest.mark.parametrize(
    ("action_name", "path"),
    [
        (
            "get_span_attributes_list",
            "/tracer/observation-span/get_span_attributes_list/",
        ),
        (
            "get_eval_attributes_list",
            "/tracer/observation-span/get_eval_attributes_list/",
        ),
    ],
)
def test_observation_attribute_pickers_return_sanitized_503_for_typed_ch_failures(
    monkeypatch, action_name, path
):
    from tracer.views.observation_span import ObservationSpanView

    def fail(*_args, **_kwargs):
        raise ServerException("private ClickHouse query detail", 159)

    monkeypatch.setattr(AttributeReadSelector, "discover_keys", fail)
    monkeypatch.setattr(
        ObservationSpanView,
        "_attribute_project_for_request",
        staticmethod(lambda _request, _project_id: True),
    )
    request = _authenticated_get(
        path,
        {"filters": json.dumps({"project_id": PROJECT_A})},
    )

    response = ObservationSpanView.as_view({"get": action_name})(request)

    assert response.status_code == 503
    assert "temporarily unavailable" in json.dumps(response.data)
    assert "private ClickHouse" not in json.dumps(response.data)


@pytest.mark.parametrize(
    ("action_name", "path"),
    [
        (
            "get_span_attributes_list",
            "/tracer/observation-span/get_span_attributes_list/",
        ),
        (
            "get_eval_attributes_list",
            "/tracer/observation-span/get_eval_attributes_list/",
        ),
    ],
)
def test_observation_attribute_pickers_return_sanitized_500_for_programming_defects(
    monkeypatch, action_name, path
):
    from tracer.views.observation_span import ObservationSpanView

    def fail(*_args, **_kwargs):
        raise RuntimeError("private attribute compiler invariant")

    monkeypatch.setattr(AttributeReadSelector, "discover_keys", fail)
    monkeypatch.setattr(
        ObservationSpanView,
        "_attribute_project_for_request",
        staticmethod(lambda _request, _project_id: True),
    )
    request = _authenticated_get(
        path,
        {"filters": json.dumps({"project_id": PROJECT_A})},
    )

    response = ObservationSpanView.as_view({"get": action_name})(request)

    assert response.status_code == 500
    payload = json.dumps(response.data)
    assert "could not be loaded" in payload
    assert "compiler invariant" not in payload


@pytest.mark.parametrize(
    ("action_name", "path"),
    [
        (
            "get_span_attributes_list",
            "/tracer/observation-span/get_span_attributes_list/",
        ),
        (
            "get_eval_attributes_list",
            "/tracer/observation-span/get_eval_attributes_list/",
        ),
    ],
)
@pytest.mark.parametrize(
    ("error_code", "sampled", "expected_status"),
    [
        ("read_budget_exceeded", False, 503),
        ("sample_limit", False, 503),
        ("sample_limit", True, 200),
    ],
)
def test_observation_attribute_pickers_only_publish_labelled_samples(
    monkeypatch, action_name, path, error_code, sampled, expected_status
):
    from tracer.views.observation_span import ObservationSpanView

    monkeypatch.setattr(
        AttributeReadSelector,
        "discover_keys",
        lambda *_args, **_kwargs: AttributeKeyRead(
            (AttributeKeyRow("final_status", "string", 1),),
            _metadata(complete=False, error_code=error_code, sampled=sampled),
        ),
    )
    monkeypatch.setattr(
        ObservationSpanView,
        "_attribute_project_for_request",
        staticmethod(lambda _request, _project_id: True),
    )
    request = _authenticated_get(
        path,
        {"filters": json.dumps({"project_id": PROJECT_A})},
    )

    response = ObservationSpanView.as_view({"get": action_name})(request)

    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.data["result"] == ["final_status"]
        assert response.data["query_complete"] is False
        assert response.data["query_status"] == "sampled"
        assert response.data["query_error_code"] == "sample_limit"
        assert "query_window_start" in response.data
        assert "query_window_end" in response.data
    else:
        assert "temporarily unavailable" in json.dumps(response.data)


@pytest.mark.parametrize(
    ("action_name", "path"),
    [
        (
            "get_span_attributes_list",
            "/tracer/observation-span/get_span_attributes_list/",
        ),
        (
            "get_eval_attributes_list",
            "/tracer/observation-span/get_eval_attributes_list/",
        ),
    ],
)
def test_observation_attribute_pickers_publish_empty_labelled_exact_sample_results(
    monkeypatch, action_name, path
):
    from tracer.views.observation_span import ObservationSpanView

    monkeypatch.setattr(
        AttributeReadSelector,
        "discover_keys",
        lambda *_args, **_kwargs: AttributeKeyRead(
            (),
            _metadata(complete=False, error_code="sample_limit", sampled=True),
        ),
    )
    monkeypatch.setattr(
        ObservationSpanView,
        "_attribute_project_for_request",
        staticmethod(lambda _request, _project_id: True),
    )
    request = _authenticated_get(
        path,
        {
            "filters": json.dumps({"project_id": PROJECT_A}),
            "q": "rare_json_key",
        },
    )

    response = ObservationSpanView.as_view({"get": action_name})(request)

    assert response.status_code == 200
    assert response.data["result"] == []
    assert response.data["query_complete"] is False
    assert response.data["query_status"] == "sampled"
    assert response.data["query_error_code"] == "sample_limit"


@pytest.mark.parametrize(
    ("action_name", "path"),
    [
        (
            "get_span_attributes_list",
            "/tracer/observation-span/get_span_attributes_list/",
        ),
        (
            "get_eval_attributes_list",
            "/tracer/observation-span/get_eval_attributes_list/",
        ),
    ],
)
def test_observation_attribute_pickers_reject_empty_generic_sample_results(
    monkeypatch, action_name, path
):
    from tracer.views.observation_span import ObservationSpanView

    monkeypatch.setattr(
        AttributeReadSelector,
        "discover_keys",
        lambda *_args, **_kwargs: AttributeKeyRead(
            (),
            _metadata(complete=False, error_code="sample_limit", sampled=True),
        ),
    )
    monkeypatch.setattr(
        ObservationSpanView,
        "_attribute_project_for_request",
        staticmethod(lambda _request, _project_id: True),
    )
    request = _authenticated_get(
        path,
        {"filters": json.dumps({"project_id": PROJECT_A})},
    )

    response = ObservationSpanView.as_view({"get": action_name})(request)

    assert response.status_code == 503
    assert "temporarily unavailable" in json.dumps(response.data)


@pytest.mark.parametrize(
    ("action_name", "path"),
    [
        (
            "get_span_attributes_list",
            "/tracer/observation-span/get_span_attributes_list/",
        ),
        (
            "get_eval_attributes_list",
            "/tracer/observation-span/get_eval_attributes_list/",
        ),
    ],
)
def test_observation_attribute_pickers_reject_empty_read_budget_results(
    monkeypatch, action_name, path
):
    from tracer.views.observation_span import ObservationSpanView

    monkeypatch.setattr(
        AttributeReadSelector,
        "discover_keys",
        lambda *_args, **_kwargs: AttributeKeyRead(
            (),
            _metadata(complete=False, error_code="read_budget_exceeded"),
        ),
    )
    monkeypatch.setattr(
        ObservationSpanView,
        "_attribute_project_for_request",
        staticmethod(lambda _request, _project_id: True),
    )
    request = _authenticated_get(
        path,
        {"filters": json.dumps({"project_id": PROJECT_A})},
    )

    response = ObservationSpanView.as_view({"get": action_name})(request)

    assert response.status_code == 503
    payload = json.dumps(response.data)
    assert "temporarily unavailable" in payload
    assert "read_budget_exceeded" not in payload


def test_span_attribute_ownership_gate_precedes_any_ch_read(monkeypatch):
    from tracer.views.span_attributes import SpanAttributeKeysView

    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        pytest.fail("ClickHouse read crossed the project ownership gate")

    monkeypatch.setattr(V2AttributeQueryExecutor, "execute", fail)
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: False,
    )
    unknown_project = uuid.uuid4()

    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": str(unknown_project), "q": "final_status"},
    )
    response = SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 404
    assert calls == 0


def test_span_attribute_detail_ownership_gate_precedes_any_ch_read(monkeypatch):
    from tracer.views.span_attributes import SpanAttributeDetailView

    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        pytest.fail("ClickHouse read crossed the project ownership gate")

    monkeypatch.setattr(V2AttributeQueryExecutor, "execute", fail)
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: False,
    )

    request = _authenticated_get(
        "/api/traces/span-attribute-detail/",
        {"project_id": PROJECT_A, "key": "final_status"},
    )
    response = SpanAttributeDetailView.as_view()(request)

    assert response.status_code == 404
    assert calls == 0


def test_span_attribute_detail_missing_tenant_context_fails_closed(monkeypatch):
    from tracer.views.span_attributes import SpanAttributeDetailView

    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda *_args, **_kwargs: pytest.fail(
            "tenant-less request queried project scope"
        ),
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes.read_or_schedule_exact_snapshot",
        lambda *_args, **_kwargs: pytest.fail("tenant-less request scheduled a worker"),
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-detail/",
        {"project_id": PROJECT_A, "key": "final_status"},
    )
    request.workspace = SimpleNamespace(id="workspace-a")

    response = SpanAttributeDetailView.as_view()(request)

    assert response.status_code == 404
    assert response.data["result"] == "Project not found"


def test_span_attribute_detail_serves_or_schedules_exact_snapshot(monkeypatch):
    from tracer.views.span_attributes import SpanAttributeDetailView

    captured: dict[str, Any] = {}

    def read_or_schedule(namespace, identity, **kwargs):
        captured.update(namespace=namespace, identity=identity, kwargs=kwargs)
        return {
            "key": "final_status",
            "type": "string",
            "count": 3,
            "unique_values": 2,
            "top_values": [
                {"value": "Rejected", "count": 2, "percentage": 66.7},
                {"value": "Accepted", "count": 1, "percentage": 33.3},
            ],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
            "query_refreshing": False,
            "query_refresh_failed": False,
        }

    monkeypatch.setattr(
        "tracer.views.span_attributes.read_or_schedule_exact_snapshot",
        read_or_schedule,
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-detail/",
        {"project_id": PROJECT_A, "key": "final_status", "refresh": "true"},
    )
    request.organization = SimpleNamespace(id="organization-a")
    request.workspace = SimpleNamespace(id="workspace-a")

    response = SpanAttributeDetailView.as_view()(request)

    assert response.status_code == 200
    assert response.data["key"] == "final_status"
    assert response.data["type"] == "string"
    assert response.data["count"] == 3
    assert response.data["unique_values"] == 2
    assert response.data["top_values"] == [
        {"value": "Rejected", "count": 2, "percentage": 66.7},
        {"value": "Accepted", "count": 1, "percentage": 33.3},
    ]
    assert response.data["query_complete"] is True
    assert response.data["query_sampled"] is False
    contract = SpanAttributeDetailResponseSerializer(data=response.data)
    assert contract.is_valid(), contract.errors
    assert captured == {
        "namespace": "attribute-detail",
        "identity": {
            "organization_id": "organization-a",
            "workspace_id": "workspace-a",
            "project_id": PROJECT_A,
            "attribute_key": "final_status",
            "horizon_days": 365,
        },
        "kwargs": {
            "refresh": True,
            "pending_payload": {
                "key": "final_status",
                "type": None,
                "count": 0,
                "unique_values": 0,
                "top_values": [],
                "query_complete": False,
                "query_status": "pending",
                "query_sampled": False,
            },
        },
    }


def test_span_attribute_numeric_detail_contract_accepts_exact_statistics():
    payload = {
        "key": "latency.score",
        "type": "number",
        "count": 4,
        "unique_values": 3,
        "top_values": [
            {"value": 10.0, "count": 2, "percentage": 50.0},
        ],
        "min": 1.0,
        "max": 100.0,
        "avg": 30.25,
        "p50": 10.0,
        "p95": 100.0,
        "stats": {
            "min": 1.0,
            "max": 100.0,
            "avg": 30.25,
            "p50": 10.0,
            "p95": 100.0,
        },
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }
    contract = SpanAttributeDetailResponseSerializer(data=payload)
    assert contract.is_valid(), contract.errors


def test_span_attribute_detail_schedule_failure_is_sanitized(monkeypatch):
    from tracer.views.span_attributes import SpanAttributeDetailView

    def fail(*_args, **_kwargs):
        raise ServerException("secret SQL and internal stack", 159)

    monkeypatch.setattr(
        "tracer.views.span_attributes.read_or_schedule_exact_snapshot",
        fail,
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-detail/",
        {"project_id": PROJECT_A, "key": "final_status"},
    )
    request.organization = SimpleNamespace(id="organization-a")
    request.workspace = SimpleNamespace(id="workspace-a")

    response = SpanAttributeDetailView.as_view()(request)

    assert response.status_code == 500
    serialized = json.dumps(response.data)
    assert "secret" not in serialized
    assert "SELECT" not in serialized


@pytest.mark.parametrize(
    ("view_name", "selector_method", "path", "params"),
    [
        (
            "SpanAttributeKeysView",
            "discover_keys",
            "/api/traces/span-attribute-keys/",
            {"project_id": PROJECT_A},
        ),
        (
            "SpanAttributeValuesView",
            "read_values",
            "/api/traces/span-attribute-values/",
            {"project_id": PROJECT_A, "key": "final_status"},
        ),
    ],
)
@pytest.mark.parametrize(
    "failure_code",
    [
        pytest.param(159, id="clickhouse-timeout"),
        pytest.param(210, id="clickhouse-network"),
        pytest.param(241, id="clickhouse-memory-budget"),
        pytest.param(497, id="clickhouse-read-permission"),
    ],
)
def test_span_attribute_views_return_sanitized_503_for_operational_failures(
    monkeypatch,
    view_name,
    selector_method,
    path,
    params,
    failure_code,
):
    from tracer.views import span_attributes

    def fail(*_args, **_kwargs):
        raise ServerException("secret ClickHouse detail", failure_code)

    monkeypatch.setattr(AttributeReadSelector, selector_method, fail)
    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(path, params)

    response = getattr(span_attributes, view_name).as_view()(request)

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    payload = json.dumps(response.data)
    assert "temporarily unavailable" in payload
    assert "secret ClickHouse detail" not in payload
    assert "query_complete" not in payload
    assert "query_status" not in payload
    assert "query_error_code" not in payload


@pytest.mark.parametrize(
    ("view_name", "selector_method", "read_result", "path", "params"),
    [
        (
            "SpanAttributeKeysView",
            "discover_keys",
            AttributeKeyRead(
                (AttributeKeyRow("final_status", "string", 1),),
                _metadata(complete=False, error_code="read_budget_exceeded"),
            ),
            "/api/traces/span-attribute-keys/",
            {"project_id": PROJECT_A},
        ),
        (
            "SpanAttributeValuesView",
            "read_values",
            AttributeValueRead(
                (AttributeValueRow("Rechazado", "string", 1),),
                _metadata(complete=False, error_code="read_budget_exceeded"),
            ),
            "/api/traces/span-attribute-values/",
            {"project_id": PROJECT_A, "key": "final_status"},
        ),
    ],
)
def test_span_attribute_views_reject_incomplete_selector_metadata(
    monkeypatch,
    view_name,
    selector_method,
    read_result,
    path,
    params,
):
    from tracer.views import span_attributes

    monkeypatch.setattr(
        AttributeReadSelector,
        selector_method,
        lambda *_args, **_kwargs: read_result,
    )
    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(path, params)

    response = getattr(span_attributes, view_name).as_view()(request)

    assert response.status_code == 503
    payload = json.dumps(response.data)
    assert "temporarily unavailable" in payload
    assert "read_budget_exceeded" not in payload
    assert "final_status" not in payload
    assert "Rechazado" not in payload


def test_legacy_span_attribute_values_publish_honest_empty_bounded_sample(
    monkeypatch,
):
    from tracer.views.span_attributes import SpanAttributeValuesView

    monkeypatch.setattr(
        AttributeReadSelector,
        "read_values",
        lambda *_args, **_kwargs: AttributeValueRead(
            (),
            _metadata(complete=False, error_code="sample_limit"),
        ),
    )
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-values/",
        {"project_id": PROJECT_A, "key": "ended_reason"},
    )

    response = SpanAttributeValuesView.as_view()(request)

    assert response.status_code == 200
    assert response.data["result"] == []
    assert response.data["query_complete"] is False
    assert response.data["query_status"] == "sampled"
    assert response.data["query_error_code"] == "sample_limit"


def test_legacy_span_attribute_values_use_one_bounded_compatibility_slice(
    monkeypatch,
):
    from tracer.views.span_attributes import SpanAttributeValuesView

    captured: dict[str, Any] = {}

    def read_values(self, project_ids, key, **kwargs):
        captured.update(project_ids=project_ids, key=key, **kwargs)
        return AttributeValueRead(
            (AttributeValueRow("assistant-ended-call", "string", 1),),
            AttributeReadMetadata(
                query_complete=True,
                query_status="complete",
                query_error_code=None,
                query_window_start=kwargs["window_start"],
                query_window_end=kwargs["window_end"],
                query_count=2,
            ),
        )

    monkeypatch.setattr(AttributeReadSelector, "read_values", read_values)
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-values/",
        {"project_id": PROJECT_A, "key": "ended_reason", "limit": 50},
    )

    response = SpanAttributeValuesView.as_view()(request)

    assert response.status_code == 200
    assert response.data["result"][0]["value"] == "assistant-ended-call"
    assert response.data["query_complete"] is False
    assert response.data["query_status"] == "sampled"
    assert response.data["query_error_code"] == "sample_limit"
    assert captured["project_ids"] == [PROJECT_A]
    assert captured["key"] == "ended_reason"
    assert captured["window_end"] - captured["window_start"] == timedelta(hours=6)


@pytest.mark.parametrize(
    ("view_name", "selector_method", "path", "params"),
    [
        (
            "SpanAttributeKeysView",
            "discover_keys",
            "/api/traces/span-attribute-keys/",
            {"project_id": PROJECT_A},
        ),
        (
            "SpanAttributeValuesView",
            "read_values",
            "/api/traces/span-attribute-values/",
            {"project_id": PROJECT_A, "key": "final_status"},
        ),
    ],
)
def test_span_attribute_views_return_sanitized_500_for_programming_defects(
    monkeypatch,
    view_name,
    selector_method,
    path,
    params,
):
    from tracer.views import span_attributes

    def fail(*_args, **_kwargs):
        raise RuntimeError("attribute compiler invariant failed")

    monkeypatch.setattr(AttributeReadSelector, selector_method, fail)
    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(path, params)

    response = getattr(span_attributes, view_name).as_view()(request)

    assert response.status_code == 500
    serialized = json.dumps(response.data)
    assert "could not be loaded" in serialized
    assert "compiler invariant" not in serialized


@pytest.mark.parametrize(
    ("view_name", "path", "params"),
    [
        (
            "SpanAttributeKeysView",
            "/api/traces/span-attribute-keys/",
            {"project_id": PROJECT_A},
        ),
        (
            "SpanAttributeValuesView",
            "/api/traces/span-attribute-values/",
            {"project_id": PROJECT_A, "key": "final_status"},
        ),
    ],
)
def test_span_attribute_views_sanitize_unexpected_scope_failures(
    monkeypatch,
    view_name,
    path,
    params,
):
    from tracer.views import span_attributes

    def fail_scope(*_args, **_kwargs):
        raise RuntimeError("private ownership database detail")

    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        fail_scope,
    )
    request = _authenticated_get(path, params)

    response = getattr(span_attributes, view_name).as_view()(request)

    assert response.status_code == 500
    serialized = json.dumps(response.data)
    assert "could not be loaded" in serialized
    assert "ownership database detail" not in serialized


@pytest.mark.parametrize(
    ("view_name", "path", "params"),
    [
        (
            "SpanAttributeKeysView",
            "/api/traces/span-attribute-keys/",
            {"project_id": PROJECT_A},
        ),
        (
            "SpanAttributeValuesView",
            "/api/traces/span-attribute-values/",
            {"project_id": PROJECT_A, "key": "final_status"},
        ),
    ],
)
@pytest.mark.parametrize(
    ("failure", "expected_status", "public_message"),
    [
        (RuntimeError("private selector configuration"), 500, "could not be loaded"),
        (
            ReadDeadlineExceeded("private selector connection timeout"),
            503,
            "temporarily unavailable",
        ),
    ],
)
def test_span_attribute_views_sanitize_selector_construction_failures(
    monkeypatch,
    view_name,
    path,
    params,
    failure,
    expected_status,
    public_message,
):
    from tracer.views import span_attributes

    def fail_selector(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(span_attributes, "AttributeReadSelector", fail_selector)
    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(path, params)

    response = getattr(span_attributes, view_name).as_view()(request)

    assert response.status_code == expected_status
    serialized = json.dumps(response.data)
    assert public_message in serialized
    assert "private selector" not in serialized


@pytest.mark.parametrize(
    ("view_name", "selector_method", "path", "params"),
    [
        (
            "SpanAttributeKeysView",
            "discover_keys",
            "/api/traces/span-attribute-keys/",
            {"project_id": PROJECT_A},
        ),
        (
            "SpanAttributeValuesView",
            "read_values",
            "/api/traces/span-attribute-values/",
            {"project_id": PROJECT_A, "key": "final_status"},
        ),
    ],
)
def test_span_attribute_views_return_sanitized_500_for_driver_query_defects(
    monkeypatch,
    view_name,
    selector_method,
    path,
    params,
):
    from tracer.views import span_attributes

    def fail(*_args, **_kwargs):
        raise ServerException("secret missing-column SQL", 47)

    monkeypatch.setattr(AttributeReadSelector, selector_method, fail)
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    request = _authenticated_get(path, params)

    response = getattr(span_attributes, view_name).as_view()(request)

    assert response.status_code == 500
    serialized = json.dumps(response.data)
    assert "could not be loaded" in serialized
    assert "secret missing-column SQL" not in serialized


def test_span_attribute_keys_use_v2_when_legacy_clickhouse_is_disabled(monkeypatch):
    from tracer.views.span_attributes import SpanAttributeKeysView

    captured: dict[str, Any] = {}

    def discover_keys(self, project_ids, exact_key=None, **kwargs):
        captured.update(
            project_ids=project_ids,
            exact_key=exact_key,
            typed_only=self._typed_only,
            json_attribute_mode=self._json_attribute_mode,
            **kwargs,
        )
        return AttributeKeyRead(
            (AttributeKeyRow("json_choices", "array", 1),),
            _metadata(),
        )

    monkeypatch.setattr(AttributeReadSelector, "discover_keys", discover_keys)
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )

    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_A, "q": "json_choices"},
    )
    response = SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert response.data["result"] == [
        {
            "key": "json_choices",
            "type": "array",
            "count": 1,
            "count_exact": False,
        }
    ]
    assert response.data["lookup_mode"] == "exact"
    assert response.data["exact_match"] is True
    assert "browse_status" not in response.data
    contract = SpanAttributeKeysResponseSerializer(data=response.data)
    assert contract.is_valid(), contract.errors
    assert captured == {
        "project_ids": [PROJECT_A],
        "exact_key": "json_choices",
        "typed_only": True,
        "json_attribute_mode": "structured",
    }
