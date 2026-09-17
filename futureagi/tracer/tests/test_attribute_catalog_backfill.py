from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tracer.services.clickhouse.v2 import attribute_catalog_backfill as backfill
from tracer.services.clickhouse.v2.attribute_catalog_backfill import (
    CATALOG_BACKFILL_ACK,
    CATALOG_BACKFILL_CLOUD_DEPLOYMENT,
    CATALOG_BACKFILL_ENVIRONMENT,
    CATALOG_DATABASE_PREFIX,
    CATALOG_INSERT_COLUMNS,
    CHECKPOINT_TABLE,
    GAP_INVALID_ATTRIBUTES_EXTRA,
    GAP_INVALID_SOURCE_MAPS,
    GAP_SELECTABLE_VALUE_PROJECTION,
    GAP_SOURCE_ATTRIBUTE_BYTES,
    GAP_SOURCE_ATTRIBUTE_ENTRIES,
    KEY_TABLE,
    MAX_CLICKHOUSE_CALL_SECONDS,
    MAX_PAGE_ROWS,
    MAX_RUNTIME_SECONDS,
    MAX_SOURCE_ATTRIBUTE_BYTES,
    MAX_SOURCE_ATTRIBUTE_ENTRIES,
    MAX_WINDOWS,
    READ_SETTINGS,
    VALUE_TABLE,
    WRITE_SETTINGS,
    CatalogAttributeBackfillRunner,
    CatalogBackfillCallDeadlineExceeded,
    CatalogBackfillConfig,
    CatalogBackfillError,
    TimedCatalogBackfillIO,
    iter_hour_windows,
    parse_utc_hour,
)
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    CATALOG_MAX_VALUE_SEARCH_TEXT_BYTES,
)
from tracer.utils.attribute_suggestion_contract import (
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)
from tracer.utils.filter_operators import JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
SINCE = datetime(2026, 1, 1, tzinfo=UTC)
UNTIL = SINCE + timedelta(hours=1)
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
TARGET_DATABASE = f"{CATALOG_DATABASE_PREFIX}unit"


def _config(**overrides: Any) -> CatalogBackfillConfig:
    values: dict[str, Any] = {
        "environment": CATALOG_BACKFILL_ENVIRONMENT,
        "cloud_deployment": CATALOG_BACKFILL_CLOUD_DEPLOYMENT,
        "dev_identity": "dev:unit-test",
        "acknowledgement": CATALOG_BACKFILL_ACK,
        "project_id": PROJECT_ID,
        "since": SINCE,
        "until": UNTIL,
        "catalog_epoch": 101,
        "source_database": "source_dev",
        "target_database": TARGET_DATABASE,
        "page_rows": 2,
        "max_windows": 24,
        "max_runtime_seconds": 600,
        "max_source_attribute_entries": 10,
        "max_source_attribute_bytes": 10_000,
        "worker_id": "unit-test",
    }
    values.update(overrides)
    return CatalogBackfillConfig(**values)


def _source_row(
    span_id: str,
    *,
    observation_type: str = "span",
    service_name: str = "svc",
    trace_id: str = "trace",
    entries: int = 1,
    source_bytes: int = 16,
    attrs_string: Any = None,
    attrs_number: Any = None,
    attrs_bool: Any = None,
    attributes_extra: Any = "{}",
) -> dict[str, Any]:
    return {
        "observation_type": observation_type,
        "service_name": service_name,
        "trace_id": trace_id,
        "span_id": span_id,
        "seen_at": SINCE + timedelta(minutes=1),
        "source_attribute_entries": entries,
        "source_attribute_bytes": source_bytes,
        "attrs_string": {"region": "us"} if attrs_string is None else attrs_string,
        "attrs_number": {} if attrs_number is None else attrs_number,
        "attrs_bool": {} if attrs_bool is None else attrs_bool,
        "attributes_extra": attributes_extra,
    }


def _projected_source_row(span_id: str) -> dict[str, Any]:
    row = _source_row(span_id)
    row.pop("attrs_string")
    row.pop("attributes_extra")
    row.update(
        {
            "source_attribute_entries": 8,
            "source_attribute_bytes": 512,
            "attrs_string_projection": [
                ("region", 0, "us"),
                ("empty", 1, ""),
            ],
            "attrs_number": {"latency": 1.5, "not_finite": float("inf")},
            "attrs_bool": {"cached": 1},
            "attributes_extra_projection": [
                (
                    "tags",
                    "Array",
                    0,
                    [
                        '"blue"',
                        "7",
                        "true",
                        json.dumps("é" * 2_048),
                    ],
                ),
                ("nested", "Object", 1, []),
                ("scalar_extra", "String", 1, []),
            ],
            "attributes_extra_valid": 1,
            "selectable_projection_complete": 1,
        }
    )
    return row


def _checkpoint_row(
    *,
    status: str = "running",
    cursor_span_id: str = "span-1",
    source_rows: int = 1,
    processed_rows: int = 1,
    key_rows: int = 1,
    value_rows: int = 1,
    gap_count: int = 0,
    gap_reasons: Sequence[str] = (),
    fence: int = 77,
    state_version: int = 99,
    state_variants: int = 1,
    projection_version: int = 2,
) -> dict[str, Any]:
    return {
        "window_start": SINCE,
        "window_end": UNTIL,
        "source_version_fence": fence,
        "cursor_observation_type": "span" if source_rows else "",
        "cursor_service_name": "svc" if source_rows else "",
        "cursor_trace_id": "trace" if source_rows else "",
        "cursor_span_id": cursor_span_id if source_rows else "",
        "status": status,
        "source_rows": source_rows,
        "processed_rows": processed_rows,
        "key_rows": key_rows,
        "value_rows": value_rows,
        "gap_count": gap_count,
        "gap_reasons": list(gap_reasons),
        "started_at": SINCE,
        "state_version": state_version,
        "state_variants": state_variants,
        "projection_version": projection_version,
    }


class FakeIO:
    def __init__(
        self,
        *,
        checkpoints: Sequence[Mapping[str, Any]] = (),
        fence: int = 77,
        future_version_rows: int = 0,
        pages: Sequence[Sequence[Mapping[str, Any]]] = ((),),
        on_insert: Callable[[str, Sequence[Sequence[Any]]], None] | None = None,
    ) -> None:
        self.checkpoints = [dict(row) for row in checkpoints]
        self.fence = fence
        self.future_version_rows = future_version_rows
        self.pages = [[dict(row) for row in page] for page in pages]
        self.pending_payload: list[dict[str, Any]] | None = None
        self.on_insert = on_insert
        self.select_calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.insert_calls: list[
            tuple[str, list[Sequence[Any]], tuple[str, ...], dict[str, Any]]
        ] = []

    def select(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        role: str,
        settings: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        self.select_calls.append((sql, dict(params), {**dict(settings), "_role": role}))
        if "span_attribute_catalog_checkpoints" in sql:
            return list(self.checkpoints)
        if "AS occupied_hours" in sql:
            return [
                {
                    "source_version_fence": self.fence,
                    "future_version_rows": self.future_version_rows,
                    "occupied_hours": sorted(
                        {
                            row["seen_at"].replace(minute=0, second=0, microsecond=0)
                            for page in self.pages
                            for row in page
                        }
                    ),
                }
            ]
        if "AS source_version_fence" in sql:
            return [{"source_version_fence": self.fence}]
        if "measured_rows" in sql:
            if self.pending_payload is None:
                raise AssertionError("payload SELECT without an identity page")
            identities = set(params["catalog_source_identities"])
            rows = [
                row
                for row in self.pending_payload
                if (
                    row["observation_type"],
                    row["service_name"],
                    row["trace_id"],
                    row["span_id"],
                )
                in identities
            ]
            self.pending_payload = None
            return rows
        if not self.pages:
            raise AssertionError("unexpected source identity SELECT")
        self.pending_payload = self.pages.pop(0)
        return [
            {
                "observation_type": row["observation_type"],
                "service_name": row["service_name"],
                "trace_id": row["trace_id"],
                "span_id": row["span_id"],
            }
            for row in self.pending_payload
        ]

    def insert(
        self,
        table: str,
        rows: Sequence[Sequence[Any]],
        columns: Sequence[str],
        *,
        settings: Mapping[str, Any],
    ) -> None:
        copied = list(rows)
        self.insert_calls.append((table, copied, tuple(columns), dict(settings)))
        if self.on_insert is not None:
            self.on_insert(table, copied)


def _run(io: FakeIO, config: CatalogBackfillConfig | None = None, **kwargs: Any):
    return CatalogAttributeBackfillRunner(
        io,
        config or _config(),
        monotonic=lambda: 0.0,
        now=lambda: NOW,
        version_ns=lambda: 1_000,
        run_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        **kwargs,
    ).run()


def _nonempty_targets(io: FakeIO) -> list[str]:
    return [
        table.strip("`").split("`.`")[-1]
        for table, rows, _, _ in io.insert_calls
        if rows
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"environment": "production"}, "development-only"),
        ({"cloud_deployment": "PROD"}, "CLOUD_DEPLOYMENT=DEV"),
        ({"dev_identity": "prod:unit-test"}, "dev:<identity>"),
        ({"acknowledgement": "yes"}, "acknowledgement"),
        ({"project_id": "not-a-uuid"}, "UUID"),
        ({"catalog_epoch": 0}, "UInt16"),
        ({"catalog_epoch": 65_536}, "UInt16"),
        ({"source_database": "source; DROP TABLE spans"}, "identifier"),
        ({"target_database": "system"}, "system database"),
        ({"target_database": "production_catalog"}, "must start"),
        ({"target_database": CATALOG_DATABASE_PREFIX}, "must start"),
        ({"target_database": f"{CATALOG_DATABASE_PREFIX}Upper"}, "must start"),
        ({"target_database": "source_dev"}, "must be distinct"),
        ({"page_rows": MAX_PAGE_ROWS + 1}, "page_rows"),
        ({"max_windows": MAX_WINDOWS + 1}, "max_windows"),
        ({"max_runtime_seconds": MAX_RUNTIME_SECONDS + 1}, "max_runtime"),
        (
            {"max_source_attribute_entries": MAX_SOURCE_ATTRIBUTE_ENTRIES + 1},
            "attribute_entries",
        ),
        (
            {"max_source_attribute_bytes": MAX_SOURCE_ATTRIBUTE_BYTES + 1},
            "attribute_bytes",
        ),
    ],
)
def test_config_fails_closed_on_scope_and_hard_bound_violations(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(CatalogBackfillError, match=message):
        _config(**overrides).validated()


def test_config_requires_half_open_hour_aligned_utc_and_explicit_window_budget() -> (
    None
):
    with pytest.raises(CatalogBackfillError, match="timezone-aware"):
        _config(since=datetime(2026, 1, 1)).validated()
    with pytest.raises(CatalogBackfillError, match="non-zero offset"):
        _config(
            since=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))
        ).validated()
    with pytest.raises(CatalogBackfillError, match="exact UTC hour"):
        _config(since=SINCE + timedelta(microseconds=1)).validated()
    with pytest.raises(CatalogBackfillError, match="before"):
        _config(until=SINCE).validated()
    with pytest.raises(CatalogBackfillError, match="max_windows"):
        _config(until=SINCE + timedelta(hours=25), max_windows=24).validated()


def test_twelve_month_ceiling_and_hour_window_generation() -> None:
    until = SINCE + timedelta(hours=MAX_WINDOWS)
    config = _config(until=until, max_windows=MAX_WINDOWS).validated()
    windows = iter_hour_windows(config.since, config.until)
    assert len(windows) == MAX_WINDOWS
    assert windows[0].start == SINCE
    assert windows[-1].end == until
    with pytest.raises(CatalogBackfillError, match="12-month"):
        _config(until=until + timedelta(hours=1), max_windows=MAX_WINDOWS).validated()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-01T00:00:00Z", SINCE),
        ("2026-01-01T00:00:00+00:00", SINCE),
    ],
)
def test_parse_utc_hour(raw: str, expected: datetime) -> None:
    assert parse_utc_hour(raw, "since") == expected


def test_source_and_checkpoint_sql_pin_select_only_keyset_and_bounds() -> None:
    identity_sql = backfill._SOURCE_IDENTITY_PAGE_SQL_TEMPLATE.lower()
    payload_sql = backfill._SOURCE_PAYLOAD_SQL_TEMPLATE.lower()
    occupied_sql = backfill._SOURCE_OCCUPIED_HOURS_SQL_TEMPLATE.lower()
    checkpoint_sql = backfill._CHECKPOINT_READ_SQL_TEMPLATE.lower()
    for sql in (
        identity_sql,
        payload_sql,
        occupied_sql,
        checkpoint_sql,
    ):
        assert " final" not in sql
        assert (
            re.search(r"\b(insert|alter|create|drop|truncate|optimize)\b", sql) is None
        )
        assert "select" in sql

    assert "project_id = touuid(%(catalog_project_id)s)" in identity_sql
    assert "start_time >= %(catalog_window_start)s" in identity_sql
    assert "start_time < %(catalog_window_end)s" in identity_sql
    assert "_version <= %(catalog_source_version_fence)s" in identity_sql
    assert "argmax(is_deleted, _version) = 0" in identity_sql
    assert "tostartofhour(start_time)" in identity_sql
    assert "catalog_after_observation_type" in identity_sql
    assert "catalog_after_service_name" in identity_sql
    assert "catalog_after_trace_id" in identity_sql
    assert "catalog_after_span_id" in identity_sql
    assert "catalog_source_limit" in identity_sql
    assert "attrs_string" not in identity_sql
    assert "attrs_number" not in identity_sql
    assert "attrs_bool" not in identity_sql
    assert "attributes_extra" not in identity_sql

    assert "jsonextractkeysandvaluesraw(sp.attributes_extra)" in payload_sql
    assert "jsonextractarrayraw" in payload_sql
    assert "attrs_string_projection" in payload_sql
    assert "attributes_extra_projection" in payload_sql
    assert "projected_array_values_fit" in payload_sql
    assert "selectable_projection_complete" in payload_sql
    assert "jsonextractstring(member)" in payload_sql
    assert "jsontype(member)) in ('float64', 'double')" in payload_sql
    assert "isfinite(jsonextractfloat(member))" in payload_sql
    assert "argmax(" in payload_sql
    assert "catalog_source_identities" in payload_sql
    assert "catalog_after_" not in payload_sql
    assert "catalog_source_limit" not in payload_sql
    assert "catalog_projected_typed_string_value_bytes" in payload_sql
    assert "catalog_projected_array_string_value_bytes" in payload_sql
    assert "catalog_projected_value_budget_bytes" in payload_sql
    assert "catalog_projected_array_members" in payload_sql
    assert "latest_attributes_extra" not in payload_sql
    assert "select\n    observation_type" in payload_sql
    measured_sql = payload_sql.split("), measured_rows as", 1)[1].split(
        "\nselect\n", 1
    )[0]
    encoded_tuple = re.search(
        r"tojsonstring\(\s*tuple\((.*?)\)\s*\)", measured_sql, re.DOTALL
    )
    assert encoded_tuple is not None
    assert [item.strip() for item in encoded_tuple.group(1).split(",")] == [
        "attrs_string_projection",
        "attrs_number",
        "attrs_bool",
        "attributes_extra_projection",
    ]

    assert "source_version_fence" in occupied_sql
    assert "countif(_version > source_version_fence)" in occupied_sql
    assert "groupuniqarrayif(8785)" in occupied_sql
    assert "_version <= source_version_fence" in occupied_sql
    assert "as occupied_hours" in occupied_sql
    assert "attrs_string" not in occupied_sql

    assert "argmax(" in checkpoint_sql
    assert "state_variants" in checkpoint_sql
    assert "window_start < %(catalog_until)s" in checkpoint_sql
    assert "window_end > %(catalog_since)s" in checkpoint_sql

    assert READ_SETTINGS == {
        "readonly": 2,
        "max_execution_time": 8,
        "timeout_overflow_mode": "throw",
        "max_threads": 1,
        "max_block_size": 1,
        "preferred_block_size_bytes": 1 * 1024 * 1024,
        "max_memory_usage": 768 * 1024 * 1024,
        "max_bytes_to_read": 512 * 1024 * 1024,
        "read_overflow_mode": "throw",
        "max_rows_to_read": 1_000_000,
        "max_result_bytes": 128 * 1024 * 1024,
        "result_overflow_mode": "throw",
    }
    assert WRITE_SETTINGS["async_insert"] == 0
    assert WRITE_SETTINGS["wait_for_async_insert"] == 1
    assert WRITE_SETTINGS["max_execution_time"] < MAX_CLICKHOUSE_CALL_SECONDS
    assert WRITE_SETTINGS["max_threads"] == 1


def test_projection_string_limits_keep_typed_and_array_contracts_separate() -> None:
    assert (
        backfill.PROJECTED_ARRAY_STRING_VALUE_BYTES
        == JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES
        == 4_096
    )
    assert backfill.PROJECTED_TYPED_STRING_VALUE_BYTES == 16_384
    assert (
        backfill.PROJECTED_TYPED_STRING_VALUE_BYTES
        == TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
    )
    assert CATALOG_MAX_VALUE_SEARCH_TEXT_BYTES <= (
        backfill.PROJECTED_TYPED_STRING_VALUE_BYTES
    )


def test_single_page_inserts_keys_then_values_then_checkpoint() -> None:
    io = FakeIO(pages=[[_source_row("span-1")]])
    summary = _run(io)

    assert summary.windows_completed == 1
    assert summary.source_rows == 1
    assert summary.key_rows == 1
    assert summary.value_rows == 1
    assert summary.gap_rows == 0
    assert _nonempty_targets(io) == [
        CHECKPOINT_TABLE,
        KEY_TABLE,
        VALUE_TABLE,
        CHECKPOINT_TABLE,
    ]
    initial_checkpoint, key_insert, value_insert, checkpoint_insert = io.insert_calls
    initial_state = dict(
        zip(initial_checkpoint[2], initial_checkpoint[1][0], strict=True)
    )
    assert initial_state["status"] == "running"
    assert initial_state["source_rows"] == 0
    assert initial_state["source_version_fence"] == 77
    assert key_insert[1][0][0] == PROJECT_ID
    assert key_insert[1][0][1:4] == ("region", "region", "string")
    assert value_insert[1][0][1] == "region"
    checkpoint = checkpoint_insert[1][0]
    checkpoint_by_name = dict(zip(checkpoint_insert[2], checkpoint, strict=True))
    assert checkpoint_by_name["status"] == "complete"
    assert checkpoint_by_name["source_rows"] == 1
    assert checkpoint_by_name["processed_rows"] == 1
    assert checkpoint_by_name["cursor_span_id"] == "span-1"
    assert checkpoint_by_name["finished_at"] == NOW
    assert checkpoint_by_name["source_version_fence"] == 77
    assert set(_nonempty_targets(io)) <= {KEY_TABLE, VALUE_TABLE, CHECKPOINT_TABLE}


def test_page_plus_one_paginates_and_checkpoints_each_acknowledged_page() -> None:
    io = FakeIO(
        pages=[
            [_source_row("span-1"), _source_row("span-2")],
            [_source_row("span-2")],
        ]
    )
    summary = _run(io, _config(page_rows=1))
    assert summary.source_rows == 2
    checkpoint_calls = [
        call for call in io.insert_calls if call[0].endswith(f"`{CHECKPOINT_TABLE}`")
    ]
    assert len(checkpoint_calls) == 3
    states = []
    for _, rows, columns, _ in checkpoint_calls:
        states.append(dict(zip(columns, rows[0], strict=True)))
    assert [state["status"] for state in states] == [
        "running",
        "running",
        "complete",
    ]
    assert [state["cursor_span_id"] for state in states] == [
        "",
        "span-1",
        "span-2",
    ]
    identity_calls = [
        call for call in io.select_calls if "catalog_after_observation_type" in call[0]
    ]
    assert identity_calls[1][1]["catalog_after_span_id"] == "span-1"
    assert all(call[1]["catalog_source_limit"] == 2 for call in identity_calls)


def test_resume_reuses_fence_cursor_and_cumulative_counts_without_fence_query() -> None:
    io = FakeIO(
        checkpoints=[_checkpoint_row()],
        pages=[[_source_row("span-2")]],
    )
    summary = _run(io)
    assert summary.source_rows == 2
    assert summary.key_rows == 2
    assert summary.value_rows == 2
    assert not any(
        "AS source_version_fence\nFROM" in sql for sql, _, _ in io.select_calls
    )
    page_call = next(
        call for call in io.select_calls if "catalog_after_observation_type" in call[0]
    )
    assert page_call[1]["catalog_source_version_fence"] == 77
    assert page_call[1]["catalog_after_span_id"] == "span-1"


def test_clickhouse_naive_utc_datetimes_are_normalized_for_resume() -> None:
    checkpoint = _checkpoint_row()
    checkpoint["window_start"] = SINCE.replace(tzinfo=None)
    checkpoint["window_end"] = UNTIL.replace(tzinfo=None)
    checkpoint["started_at"] = SINCE.replace(tzinfo=None)
    row = _source_row("span-2")
    row["seen_at"] = (SINCE + timedelta(minutes=1)).replace(tzinfo=None)
    io = FakeIO(checkpoints=[checkpoint], pages=[[row]])
    summary = _run(io)
    assert summary.windows_completed == 1
    key_call = next(
        call
        for call in io.insert_calls
        if call[1] and call[0].endswith(f"`{KEY_TABLE}`")
    )
    assert key_call[1][0][4].tzinfo is UTC


def test_resume_requires_the_current_projection_version() -> None:
    missing = _checkpoint_row()
    missing.pop("projection_version")
    cases = (
        (missing, "projection_version must be a non-negative integer"),
        (_checkpoint_row(projection_version=1), "incompatible catalog projection"),
    )
    for checkpoint, match in cases:
        io = FakeIO(checkpoints=[checkpoint])
        with pytest.raises(CatalogBackfillError, match=match):
            _run(io)
        assert io.insert_calls == []


@pytest.mark.parametrize("status", ["complete", "gap"])
def test_terminal_checkpoint_skips_window_without_source_or_writes(status: str) -> None:
    checkpoint = _checkpoint_row(
        status=status,
        gap_count=1 if status == "gap" else 0,
        gap_reasons=[GAP_SOURCE_ATTRIBUTE_BYTES] if status == "gap" else [],
    )
    io = FakeIO(checkpoints=[checkpoint], pages=[])
    summary = _run(io)
    assert summary.windows_skipped == 1
    assert summary.windows_completed == 0
    assert summary.windows_gap == (1 if status == "gap" else 0)
    assert summary.source_rows == 1
    assert summary.key_rows == 1
    assert summary.value_rows == 1
    assert summary.gap_rows == (1 if status == "gap" else 0)
    assert summary.gap_reasons == (
        (GAP_SOURCE_ATTRIBUTE_BYTES,) if status == "gap" else ()
    )
    assert len(io.select_calls) == 1
    assert io.insert_calls == []


def test_source_caps_are_declared_as_gaps_and_never_build_partial_rows() -> None:
    io = FakeIO(
        pages=[
            [
                _source_row(
                    "span-1",
                    entries=11,
                    source_bytes=10_001,
                    attrs_string={},
                )
            ]
        ]
    )
    summary = _run(io)
    assert summary.windows_gap == 1
    assert summary.gap_rows == 1
    assert summary.key_rows == 0
    assert summary.value_rows == 0
    assert summary.gap_reasons == (
        GAP_SOURCE_ATTRIBUTE_ENTRIES,
        GAP_SOURCE_ATTRIBUTE_BYTES,
    )
    assert _nonempty_targets(io) == [CHECKPOINT_TABLE, CHECKPOINT_TABLE]
    checkpoint_call = io.insert_calls[-1]
    state = dict(zip(checkpoint_call[2], checkpoint_call[1][0], strict=True))
    assert state["status"] == "gap"
    assert state["gap_reasons"] == [
        GAP_SOURCE_ATTRIBUTE_ENTRIES,
        GAP_SOURCE_ATTRIBUTE_BYTES,
    ]


@pytest.mark.parametrize(
    ("row_overrides", "reason"),
    [
        (
            {"attrs_string": [], "attrs_number": {}, "attrs_bool": {}},
            GAP_INVALID_SOURCE_MAPS,
        ),
        ({"attributes_extra": "[1,2]"}, GAP_INVALID_ATTRIBUTES_EXTRA),
        ({"attributes_extra": "not-json"}, GAP_INVALID_ATTRIBUTES_EXTRA),
    ],
)
def test_malformed_source_shapes_are_explicit_gap_rows(
    row_overrides: dict[str, Any], reason: str
) -> None:
    io = FakeIO(pages=[[_source_row("span-1", **row_overrides)]])
    summary = _run(io)
    assert summary.gap_rows == 1
    assert reason in summary.gap_reasons
    assert _nonempty_targets(io) == [CHECKPOINT_TABLE, CHECKPOINT_TABLE]


def test_projected_key_only_attributes_are_complete_without_value_rows() -> None:
    io = FakeIO(pages=[[_projected_source_row("span-1")]])
    summary = _run(io)

    assert summary.windows_completed == 1
    assert summary.windows_gap == 0
    assert summary.gap_rows == 0
    assert summary.key_rows == 8
    assert summary.value_rows == 7
    value_call = next(
        call
        for call in io.insert_calls
        if call[1] and call[0].endswith(f"`{VALUE_TABLE}`")
    )
    value_keys = [row[1] for row in value_call[1]]
    assert value_keys == [
        "cached",
        "latency",
        "region",
        "tags",
        "tags",
        "tags",
        "tags",
    ]
    assert not {
        "empty",
        "nested",
        "not_finite",
        "scalar_extra",
    } & set(value_keys)


@pytest.mark.parametrize("json_type", ["Float64", "Double"])
def test_clickhouse_float_json_type_aliases_are_complete_key_only_metadata(
    json_type: str,
) -> None:
    """CH25 reports JSON floating scalars as ``Double`` on real DEV rows."""

    row = _projected_source_row("span-1")
    row.update(
        {
            "source_attribute_entries": 1,
            "source_attribute_bytes": 64,
            "attrs_string_projection": [],
            "attrs_number": {},
            "attrs_bool": {},
            "attributes_extra_projection": [
                ("floating_scalar", json_type, 1, []),
            ],
        }
    )
    io = FakeIO(pages=[[row]])

    summary = _run(io)

    assert summary.windows_completed == 1
    assert summary.windows_gap == 0
    assert summary.gap_rows == 0
    assert summary.key_rows == 1
    assert summary.value_rows == 0


def test_oversized_typed_string_is_complete_key_only_picker_metadata() -> None:
    at_limit = "é" * (TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES // 2)
    row = _projected_source_row("span-1")
    row.update(
        {
            "source_attribute_entries": 2,
            "source_attribute_bytes": 512,
            "attrs_string_projection": [
                ("at_limit", 0, at_limit),
                ("oversized", 1, ""),
            ],
            "attrs_number": {},
            "attrs_bool": {},
            "attributes_extra_projection": [],
            "selectable_projection_complete": 1,
        }
    )
    io = FakeIO(pages=[[row]])

    summary = _run(io)

    assert summary.windows_gap == 0
    assert summary.gap_rows == 0
    assert summary.key_rows == 2
    assert summary.value_rows == 1
    key_call = next(
        call
        for call in io.insert_calls
        if call[1] and call[0].endswith(f"`{KEY_TABLE}`")
    )
    value_call = next(
        call
        for call in io.insert_calls
        if call[1] and call[0].endswith(f"`{VALUE_TABLE}`")
    )
    assert [inserted[1] for inserted in key_call[1]] == ["at_limit", "oversized"]
    assert [inserted[1] for inserted in value_call[1]] == ["at_limit"]


def test_selectable_projection_omission_is_a_durable_fallback_gap() -> None:
    row = _projected_source_row("span-1")
    row.update(
        {
            "attrs_string_projection": [("oversize", 1, "")],
            "attributes_extra_projection": [("too_many", "Array", 1, [])],
            "selectable_projection_complete": 0,
            "source_attribute_entries": 2,
        }
    )
    io = FakeIO(pages=[[row]])
    summary = _run(io)

    assert summary.windows_gap == 1
    assert summary.gap_rows == 1
    assert summary.gap_reasons == (GAP_SELECTABLE_VALUE_PROJECTION,)
    assert summary.key_rows == 0
    assert summary.value_rows == 0
    assert _nonempty_targets(io) == [CHECKPOINT_TABLE, CHECKPOINT_TABLE]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(attrs_string_projection=[("oversize", 1, "leaked")]),
        lambda row: row.update(
            attributes_extra_projection=[("nested", "Object", 0, [])]
        ),
        lambda row: row.update(attributes_extra_valid=0),
    ],
)
def test_inconsistent_projected_shapes_are_explicit_gaps(mutation) -> None:
    row = _projected_source_row("span-1")
    mutation(row)
    io = FakeIO(pages=[[row]])
    summary = _run(io)

    assert summary.windows_gap == 1
    assert summary.gap_rows == 1
    assert summary.key_rows == 0
    assert summary.value_rows == 0
    assert _nonempty_targets(io) == [CHECKPOINT_TABLE, CHECKPOINT_TABLE]


def test_builder_limit_gap_is_propagated_to_terminal_checkpoint() -> None:
    attrs = {f"key-{index:04}": "value" for index in range(1_025)}
    io = FakeIO(
        pages=[
            [
                _source_row(
                    "span-1",
                    entries=1_025,
                    source_bytes=50_000,
                    attrs_string=attrs,
                )
            ]
        ]
    )
    summary = _run(
        io,
        _config(
            max_source_attribute_entries=2_048,
            max_source_attribute_bytes=100_000,
        ),
    )
    assert summary.key_rows == 1_024
    assert summary.value_rows == 1_024
    assert summary.gap_rows == 1
    assert "max_keys" in summary.gap_reasons


def test_dry_run_reads_and_builds_all_pages_but_performs_zero_writes() -> None:
    io = FakeIO(
        pages=[
            [_source_row("span-1"), _source_row("span-2")],
            [_source_row("span-2")],
        ]
    )
    summary = _run(io, _config(page_rows=1, dry_run=True))
    assert summary.dry_run
    assert summary.source_rows == 2
    assert summary.key_rows == 2
    assert summary.value_rows == 2
    assert io.insert_calls == []


def test_range_discovery_batches_proven_empty_hour_checkpoints() -> None:
    middle = _source_row("span-1")
    middle["seen_at"] = SINCE + timedelta(hours=1, minutes=1)
    io = FakeIO(pages=[[middle]])
    summary = _run(
        io,
        _config(until=SINCE + timedelta(hours=3), max_windows=3),
    )
    assert summary.windows_total == 3
    assert summary.windows_completed == 3
    assert summary.source_rows == 1
    occupied_calls = [
        call for call in io.select_calls if "AS occupied_hours" in call[0]
    ]
    identity_calls = [
        call for call in io.select_calls if "catalog_after_observation_type" in call[0]
    ]
    assert len(occupied_calls) == 1
    assert len(identity_calls) == 1
    checkpoint_calls = [
        call for call in io.insert_calls if call[0].endswith(f"`{CHECKPOINT_TABLE}`")
    ]
    # One INSERT contains both proven-empty hours; occupied hour uses its own
    # initial and terminal checkpoint rows.
    assert len(checkpoint_calls[0][1]) == 2
    empty_states = [
        dict(zip(checkpoint_calls[0][2], row, strict=True))
        for row in checkpoint_calls[0][1]
    ]
    assert [state["window_start"] for state in empty_states] == [
        SINCE,
        SINCE + timedelta(hours=2),
    ]
    assert all(state["status"] == "complete" for state in empty_states)
    assert all(state["source_rows"] == 0 for state in empty_states)


def test_all_empty_dry_run_is_one_bounded_source_discovery_and_zero_writes() -> None:
    io = FakeIO(pages=[[]])
    summary = _run(
        io,
        _config(
            until=SINCE + timedelta(hours=24),
            max_windows=24,
            dry_run=True,
        ),
    )
    assert summary.windows_completed == 24
    assert summary.source_rows == 0
    assert len(io.select_calls) == 2  # checkpoint inventory + occupied hours
    assert io.insert_calls == []


def test_range_discovery_fails_closed_on_future_source_versions() -> None:
    io = FakeIO(pages=[[]], future_version_rows=1)
    with pytest.raises(CatalogBackfillError, match="beyond the frozen"):
        _run(io)
    assert io.insert_calls == []


def test_signal_during_page_finishes_page_checkpoint_then_stops() -> None:
    stop = False

    def on_insert(table: str, rows: Sequence[Sequence[Any]]) -> None:
        nonlocal stop
        if rows and table.endswith(f"`{KEY_TABLE}`"):
            stop = True

    io = FakeIO(
        pages=[[_source_row("span-1"), _source_row("span-2")]],
        on_insert=on_insert,
    )
    summary = _run(
        io,
        _config(page_rows=1),
        stop_requested=lambda: stop,
    )
    assert summary.stopped
    assert summary.windows_pending == 1
    checkpoint_call = io.insert_calls[-1]
    state = dict(zip(checkpoint_call[2], checkpoint_call[1][0], strict=True))
    assert state["status"] == "running"
    assert state["cursor_span_id"] == "span-1"
    assert state["processed_rows"] == 1


def test_runtime_guard_stops_before_starting_a_page() -> None:
    io = FakeIO(pages=[])
    summary = _run(io, _config(max_runtime_seconds=1))
    assert summary.stopped
    assert summary.windows_pending == 1
    assert len(io.select_calls) == 1  # bounded checkpoint inventory only
    assert io.insert_calls == []


def test_failed_value_insert_never_advances_checkpoint() -> None:
    def fail_value(table: str, rows: Sequence[Sequence[Any]]) -> None:
        if rows and table.endswith(f"`{VALUE_TABLE}`"):
            raise CatalogBackfillError("value insert failed")

    io = FakeIO(pages=[[_source_row("span-1")]], on_insert=fail_value)
    with pytest.raises(CatalogBackfillError, match="value insert failed"):
        _run(io)
    assert _nonempty_targets(io) == [CHECKPOINT_TABLE, KEY_TABLE, VALUE_TABLE]
    checkpoints = [
        call for call in io.insert_calls if call[0].endswith(f"`{CHECKPOINT_TABLE}`")
    ]
    assert len(checkpoints) == 1
    state = dict(zip(checkpoints[0][2], checkpoints[0][1][0], strict=True))
    assert state["status"] == "running"
    assert state["processed_rows"] == 0


@pytest.mark.parametrize(
    "rows",
    [
        [_source_row("span-1"), _source_row("span-1")],
        [_source_row("span-2"), _source_row("span-1")],
    ],
)
def test_unordered_or_duplicate_source_keysets_fail_before_writes(rows) -> None:
    io = FakeIO(pages=[rows])
    with pytest.raises(CatalogBackfillError, match="strictly keyset ordered"):
        _run(io)
    assert _nonempty_targets(io) == [CHECKPOINT_TABLE]
    state = dict(zip(io.insert_calls[0][2], io.insert_calls[0][1][0], strict=True))
    assert state["processed_rows"] == 0


@pytest.mark.parametrize(
    "checkpoint",
    [
        _checkpoint_row(state_variants=2),
        _checkpoint_row(source_rows=2, processed_rows=1),
        _checkpoint_row(status="complete", gap_count=1, gap_reasons=["x"]),
        _checkpoint_row(status="gap", gap_count=0, gap_reasons=[]),
        _checkpoint_row(fence=0),
    ],
)
def test_ambiguous_or_inconsistent_checkpoint_fails_closed(checkpoint) -> None:
    io = FakeIO(checkpoints=[checkpoint], pages=[])
    with pytest.raises(CatalogBackfillError):
        _run(io)
    assert io.insert_calls == []


def test_overlapping_nonhourly_checkpoint_shape_fails_closed() -> None:
    checkpoint = _checkpoint_row()
    checkpoint["window_end"] = UNTIL - timedelta(minutes=1)
    io = FakeIO(checkpoints=[checkpoint], pages=[])
    with pytest.raises(CatalogBackfillError, match="window shape"):
        _run(io)


class _NamedResult:
    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.rows = rows

    def named_results(self):
        yield from self.rows


class _Client:
    def __init__(self) -> None:
        self.query_calls: list[tuple] = []
        self.insert_calls: list[tuple] = []

    def query(self, *args, **kwargs):
        self.query_calls.append((args, kwargs))
        return _NamedResult([{"ok": 1}])

    def insert(self, *args, **kwargs):
        self.insert_calls.append((args, kwargs))

    def command(self, *args, **kwargs):
        return ""


def _timed_io(source: _Client | None = None, catalog: _Client | None = None, **kwargs):
    return TimedCatalogBackfillIO(
        source or _Client(),
        catalog or _Client(),
        _Client(),
        _Client(),
        target_database=TARGET_DATABASE,
        **kwargs,
    )


def test_timed_io_rejects_nonselect_sql_and_non_catalog_write_targets() -> None:
    io = _timed_io()
    with pytest.raises(CatalogBackfillError, match="one SELECT"):
        io.select("INSERT INTO x VALUES (1)", {}, role="source", settings={})
    with pytest.raises(CatalogBackfillError, match="not allowed"):
        io.insert(f"`{TARGET_DATABASE}`.`spans`", [[1]], ["x"], settings={})

    with pytest.raises(CatalogBackfillError, match="fully-qualified"):
        io.insert(
            "`production_catalog`.`span_attribute_key_catalog`",
            [[1]],
            CATALOG_INSERT_COLUMNS[KEY_TABLE],
            settings={},
        )


def test_timed_io_enforces_absolute_ten_second_select_deadline() -> None:
    release = threading.Event()

    class BlockingClient(_Client):
        def query(self, *args, **kwargs):
            self.query_calls.append((args, kwargs))
            release.wait(1)
            return _NamedResult([{"ok": 1}])

    class CancelClient(_Client):
        def command(self, *args, **kwargs):
            self.command_call = (args, kwargs)
            release.set()
            return ""

    source = BlockingClient()
    cancel = CancelClient()
    tokens = iter(("worktoken", "canceltoken"))
    io = TimedCatalogBackfillIO(
        source,
        _Client(),
        cancel,
        _Client(),
        target_database=TARGET_DATABASE,
        max_call_seconds=0.05,
        query_id_factory=lambda: next(tokens),
    )
    started = time.monotonic()
    with pytest.raises(CatalogBackfillCallDeadlineExceeded, match="SELECT"):
        io.select("SELECT 1", {}, role="source", settings={})
    assert time.monotonic() - started < 0.2
    assert source.query_calls[0][1]["transport_settings"] == {
        "X-ClickHouse-Query-Id": "property_catalog_backfill_work_worktoken"
    }
    assert cancel.command_call[1]["parameters"] == {
        "query_id": "property_catalog_backfill_work_worktoken"
    }
    assert "KILL QUERY" in cancel.command_call[0][0]


def test_timed_io_enforces_absolute_ten_second_insert_deadline_after_safe_target() -> (
    None
):
    release = threading.Event()

    class BlockingClient(_Client):
        def insert(self, *args, **kwargs):
            self.insert_calls.append((args, kwargs))
            release.wait(1)

    class CancelClient(_Client):
        def command(self, *args, **kwargs):
            release.set()
            return ""

    client = BlockingClient()
    io = TimedCatalogBackfillIO(
        _Client(),
        client,
        _Client(),
        CancelClient(),
        target_database=TARGET_DATABASE,
        max_call_seconds=0.05,
    )
    columns = CATALOG_INSERT_COLUMNS[KEY_TABLE]
    with pytest.raises(CatalogBackfillCallDeadlineExceeded, match="INSERT"):
        io.insert(
            f"`{TARGET_DATABASE}`.`span_attribute_key_catalog`",
            [[None] * len(columns)],
            columns,
            settings={},
        )
    assert len(client.insert_calls) == 1
    assert "X-ClickHouse-Query-Id" in client.insert_calls[0][1]["transport_settings"]


def test_timed_io_routes_source_and_catalog_reads_to_distinct_clients() -> None:
    source = _Client()
    catalog = _Client()
    io = TimedCatalogBackfillIO(
        source,
        catalog,
        _Client(),
        _Client(),
        target_database=TARGET_DATABASE,
    )
    io.select("SELECT 1", {}, role="source", settings={})
    io.select("SELECT 1", {}, role="catalog", settings={})
    assert len(source.query_calls) == 1
    assert len(catalog.query_calls) == 1
    assert not source.insert_calls


def test_timed_io_rejects_catalog_column_or_row_drift_without_network_call() -> None:
    catalog = _Client()
    io = _timed_io(catalog=catalog)
    with pytest.raises(CatalogBackfillError, match="columns"):
        io.insert(
            f"`{TARGET_DATABASE}`.`span_attribute_key_catalog`",
            [[1]],
            ["x"],
            settings={},
        )
    with pytest.raises(CatalogBackfillError, match="row"):
        io.insert(
            f"`{TARGET_DATABASE}`.`span_attribute_key_catalog`",
            [[PROJECT_ID]],
            CATALOG_INSERT_COLUMNS[KEY_TABLE],
            settings={},
        )
    assert catalog.insert_calls == []


def test_timed_io_refuses_unsafe_query_id_factory_output() -> None:
    io = _timed_io(query_id_factory=lambda: "unsafe query id")
    with pytest.raises(CatalogBackfillError, match="unsafe query id"):
        io.select("SELECT 1", {}, role="source", settings={})


def test_only_catalog_tables_are_present_in_runner_insert_targets() -> None:
    implementation = backfill.__file__
    assert implementation is not None
    assert backfill.CATALOG_BACKFILL_WRITE_TABLES == {
        KEY_TABLE,
        VALUE_TABLE,
        CHECKPOINT_TABLE,
    }
    assert (
        "span_attribute_catalog_activations"
        not in backfill.CATALOG_BACKFILL_WRITE_TABLES
    )
    assert (
        "span_attribute_catalog_source_streams"
        not in backfill.CATALOG_BACKFILL_WRITE_TABLES
    )


def test_legacy_management_command_is_a_zero_io_retirement_stub() -> None:
    command_path = (
        Path(__file__).resolve().parents[1]
        / "management/commands/ch25_backfill_attribute_catalog.py"
    )
    source = command_path.read_text()

    assert "ch25_property_catalog_dev_rollout" in source
    assert "performs zero I/O" in source
    assert "clickhouse_connect" not in source
    assert "get_v2_config" not in source
    assert "CatalogAttributeBackfillRunner" not in source
