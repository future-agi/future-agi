"""Contracts for the exact graph typed-Map partition discovery lane.

These tests intentionally exercise the V2 builder boundary.  The lane is
authoritative only when every physical ReplacingMergeTree identity is reduced
to its latest version before the mutable tombstone/attribute predicate is
evaluated.
"""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from typing import Any

import pytest
from clickhouse_driver.errors import ServerException

from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
WINDOW_START = datetime(2026, 7, 24, 2, 43, 12)
WINDOW_END = datetime(2026, 7, 31, 6, 59, 59)


def _time_filter() -> dict[str, Any]:
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [WINDOW_START, WINDOW_END],
        },
    }


def _attribute_filter(
    *,
    key: str = "final_status",
    filter_type: str = "text",
    operation: str = "equals",
    value: Any = "Rechazado",
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
    *,
    column_id: str = "model",
    operation: str = "equals",
    value: Any = "gpt-4o-2024-11-20",
) -> dict[str, Any]:
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _builder(
    *non_time_filters: dict[str, Any],
    sampling_rate: float | None = None,
) -> TraceListQueryBuilderV2:
    return TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), *non_time_filters],
        page_number=0,
        page_size=200,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
        bounded_sampling_salt=(
            "sampling-is-not-authoritative" if sampling_rate is not None else None
        ),
        bounded_sampling_rate=sampling_rate,
    )


def _compact(sql: str) -> str:
    return " ".join(sql.split())


def _unix_microseconds(value: datetime) -> int:
    utc_value = value.replace(tzinfo=UTC)
    delta = utc_value - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000


@pytest.fixture(autouse=True)
def _allow_short_windows_in_direct_anchor_orchestrator_tests(monkeypatch):
    """Keep partition mechanics isolated from the production routing policy."""

    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(
        exact_module,
        "EXACT_GRAPH_TRACE_ANCHOR_MIN_REQUEST_WIDTH",
        timedelta(0),
    )
    monkeypatch.setattr(
        exact_module,
        "EXACT_GRAPH_TRACE_ANCHOR_MIN_RETENTION_FRACTION",
        0.0,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filter_type", "operation", "value"),
    [
        ("text", "equals", "Rechazado"),
        ("text", "in", ["Rechazado", "Aprobado"]),
        ("number", "equals", 7),
        ("number", "in", [7, 9]),
        ("boolean", "equals", True),
        ("boolean", "in", [True, False]),
    ],
)
def test_exact_graph_anchor_partition_accepts_one_positive_typed_map_leaf(
    filter_type: str,
    operation: str,
    value: Any,
):
    builder = _builder(
        _attribute_filter(
            filter_type=filter_type,
            operation=operation,
            value=value,
        )
    )

    assert builder.exact_graph_supports_authoritative_anchor_partition() is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("column_id", "operation", "value"),
    [
        ("model", "equals", "gpt-4o-2024-11-20"),
        ("model", "in", ["gpt-4o-2024-11-20", "gpt-5-mini"]),
        ("status", "equals", "ERROR"),
        ("provider", "equals", "openai"),
    ],
)
def test_exact_graph_anchor_partition_accepts_one_positive_direct_scalar_leaf(
    column_id: str,
    operation: str,
    value: Any,
):
    builder = _builder(
        _system_filter(
            column_id=column_id,
            operation=operation,
            value=value,
        )
    )

    assert builder.exact_graph_supports_authoritative_anchor_partition() is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "filter_item",
    [
        _attribute_filter(operation="not_equals"),
        _attribute_filter(operation="contains"),
        _attribute_filter(operation="starts_with"),
        _attribute_filter(
            filter_type="number",
            operation="greater_than",
            value=7,
        ),
        _attribute_filter(filter_type="array", operation="contains", value=["vip"]),
        _attribute_filter(
            filter_type="map",
            operation="contains",
            value={"tier": "gold"},
        ),
        _system_filter(column_id="trace_name", value="root-only"),
    ],
)
def test_exact_graph_anchor_partition_rejects_non_authoritative_leaf_shapes(
    filter_item: dict[str, Any],
):
    assert (
        _builder(filter_item).exact_graph_supports_authoritative_anchor_partition()
        is False
    )


@pytest.mark.unit
def test_exact_graph_anchor_partition_rejects_multiple_leaves_and_sampling():
    first = _attribute_filter()
    second = _attribute_filter(
        key="customer_tier",
        operation="in",
        value=["gold", "silver"],
    )

    assert (
        _builder(
            first,
            second,
        ).exact_graph_supports_authoritative_anchor_partition()
        is False
    )
    assert (
        _builder(
            first,
            sampling_rate=50,
        ).exact_graph_supports_authoritative_anchor_partition()
        is False
    )


@pytest.mark.unit
def test_exact_graph_anchor_partition_reduces_full_v2_identity_before_filtering():
    builder = _builder(_attribute_filter())
    partition_start = datetime(2026, 7, 27, 6, 0, 0)
    partition_end = datetime(2026, 7, 27, 12, 0, 0)

    sql, params = builder.build_exact_graph_latest_anchor_partition(
        partition_start=partition_start,
        partition_end=partition_end,
        limit=10_001,
    )
    compact = _compact(sql)
    physical_identity = (
        "GROUP BY project_id, observation_type, service_name, "
        "toStartOfHour(start_time), trace_id, id"
    )

    assert physical_identity in compact
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in compact
    assert (
        "argMax(mapContains(attrs_string, %(latest_filter_key_0)s), _version) "
        "AS latest_attr_exists_0"
    ) in compact
    assert (
        "argMax(attrs_string[%(latest_filter_key_0)s], _version) AS latest_attr_value_0"
    ) in compact

    # The raw CTE is only a necessary-superset identity accelerator.  The
    # authoritative replay must not apply either mutable predicate before it
    # has reduced every selected physical identity to its latest version.
    replay_start = compact.index("SELECT grouped_trace_id AS trace_id FROM (")
    identity_start = compact.index(physical_identity, replay_start)
    identity_end = identity_start + len(physical_identity)
    assert "latest_filter_param_0" in compact[:replay_start]
    assert "is_deleted = 0" in compact[:replay_start]
    assert "latest_filter_param_0" not in compact[replay_start:identity_end]
    assert "is_deleted = 0" not in compact[replay_start:identity_end]
    assert "latest_is_deleted = 0" in compact[identity_end:]
    assert "latest_attr_exists_0" in compact[identity_end:]
    assert "latest_attr_value_0" in compact[identity_end:]
    assert "latest_filter_param_0" in compact[identity_end:]

    assert params["latest_filter_key_0"] == "final_status"
    assert params["latest_filter_param_0"] == "rechazado"
    assert params["exact_graph_anchor_start_us"] == _unix_microseconds(partition_start)
    assert params["exact_graph_anchor_end_us"] == _unix_microseconds(partition_end)
    assert params["exact_graph_anchor_limit"] == 10_001


@pytest.mark.unit
def test_exact_graph_anchor_partition_reduces_direct_model_before_filtering():
    builder = _builder(_system_filter())

    sql, params = builder.build_exact_graph_latest_anchor_partition(
        partition_start=datetime(2026, 7, 27, 6, 0, 0),
        partition_end=datetime(2026, 7, 27, 12, 0, 0),
        limit=10_001,
    )
    compact = _compact(sql)
    replay_start = compact.index("SELECT grouped_trace_id AS trace_id FROM (")
    identity_end = compact.index(
        "GROUP BY project_id, observation_type, service_name, "
        "toStartOfHour(start_time), trace_id, id",
        replay_start,
    )

    assert (
        "lowerUTF8(toString(model)) = %(latest_filter_param_0)s"
        in compact[:replay_start]
    )
    assert "argMax(tuple(model), _version).1 AS latest_column_value_0" in compact
    assert (
        "lowerUTF8(toString(latest_column_value_0))"
        not in compact[replay_start:identity_end]
    )
    assert (
        "lowerUTF8(toString(latest_column_value_0)) = %(latest_filter_param_0)s"
        in compact[identity_end:]
    )
    assert params["latest_filter_param_0"] == "gpt-4o-2024-11-20"


@pytest.mark.unit
def test_exact_graph_anchor_partition_pushes_only_immutable_trace_keyset():
    builder = _builder(
        _attribute_filter(operation="in", value=["Rechazado", "Aprobado"])
    )

    sql, params = builder.build_exact_graph_latest_anchor_partition(
        partition_start=datetime(2026, 7, 27, 0, 0, 0),
        partition_end=datetime(2026, 7, 27, 6, 0, 0),
        before_trace_id="trace-keyset-0200",
        limit=201,
    )
    compact = _compact(sql)
    identity_end = compact.index(
        "GROUP BY project_id, observation_type, service_name, "
        "toStartOfHour(start_time), trace_id, id"
    )

    assert "trace_id > %(exact_graph_anchor_after_trace_id)s" in compact[:identity_end]
    assert (
        "grouped_trace_id > %(exact_graph_anchor_after_trace_id)s"
        in compact[identity_end:]
    )
    assert params["exact_graph_anchor_after_trace_id"] == "trace-keyset-0200"
    assert params["exact_graph_anchor_limit"] == 201


@pytest.mark.unit
def test_exact_graph_anchor_partition_defaults_to_50k_plus_one_sentinel():
    builder = _builder(_attribute_filter())

    sql, params = builder.build_exact_graph_latest_anchor_partition(
        partition_start=datetime(2026, 7, 27, 0, 0, 0),
        partition_end=datetime(2026, 7, 27, 1, 0, 0),
    )

    assert "LIMIT %(exact_graph_anchor_limit)s" in _compact(sql)
    assert params["exact_graph_anchor_limit"] == 50_001


@pytest.mark.unit
@pytest.mark.parametrize(
    ("partition_start", "partition_end"),
    [
        (datetime(2026, 7, 27, 0, 1), datetime(2026, 7, 27, 6, 0)),
        (datetime(2026, 7, 27, 0, 0), datetime(2026, 7, 27, 6, 0, 1)),
        (datetime(2026, 7, 27, 6, 0), datetime(2026, 7, 27, 6, 0)),
        (datetime(2026, 7, 27, 12, 0), datetime(2026, 7, 27, 6, 0)),
    ],
)
def test_exact_graph_anchor_partition_requires_ordered_whole_hour_bounds(
    partition_start: datetime,
    partition_end: datetime,
):
    builder = _builder(_attribute_filter())

    with pytest.raises(ValueError, match="hour|partition"):
        builder.build_exact_graph_latest_anchor_partition(
            partition_start=partition_start,
            partition_end=partition_end,
            limit=10_001,
        )


@pytest.mark.unit
def test_exact_graph_anchor_scan_bounds_use_active_part_metadata():
    builder = _builder(_attribute_filter())

    sql, params = builder.build_exact_graph_anchor_scan_bounds()
    compact = _compact(sql)

    assert "minOrNull(min_time) AS min_start_time" in compact
    assert "maxOrNull(max_time) AS max_start_time" in compact
    assert "FROM system.parts" in compact
    assert "WHERE active" in compact
    assert "database = currentDatabase()" in compact
    assert "table = 'spans'" in compact
    assert "project_id" not in compact
    assert params == {}
    assert "attrs_string" not in compact
    assert "latest_filter_key_0" not in compact
    assert "latest_filter_param_0" not in compact
    assert "mapContains" not in compact
    assert "GROUP BY" not in compact
    assert "ORDER BY" not in compact


@pytest.mark.unit
def test_exact_graph_root_partition_reduces_versions_before_live_window_filter():
    builder = _builder(_attribute_filter())
    partition_start = datetime(2026, 7, 27, 0, 0)
    partition_end = datetime(2026, 7, 27, 2, 0)
    request_start = datetime(2026, 7, 27, 0, 15)
    request_end = datetime(2026, 7, 27, 1, 45)

    sql, params = builder.build_exact_graph_latest_root_partition(
        partition_start=partition_start,
        partition_end=partition_end,
        request_start=request_start,
        request_end=request_end,
        before_trace_id="trace-cursor",
        limit=50_001,
    )
    compact = _compact(sql)
    physical_identity = (
        "GROUP BY project_id, observation_type, service_name, "
        "toStartOfHour(start_time), trace_id, id"
    )
    identity_end = compact.index(physical_identity) + len(physical_identity)

    assert "argMax(is_deleted, _version) AS latest_is_deleted" in compact
    assert (
        "argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id" in compact
    )
    assert "latest_is_deleted = 0" not in compact[:identity_end]
    assert "latest_parent_span_id IS NULL" not in compact[:identity_end]
    assert "latest_is_deleted = 0" in compact[identity_end:]
    assert "latest_parent_span_id IS NULL" in compact[identity_end:]
    assert "trace_id > %(exact_graph_root_after_trace_id)s" in compact[:identity_end]
    assert (
        "grouped_trace_id > %(exact_graph_root_after_trace_id)s"
        in compact[identity_end:]
    )
    assert "attrs_string" not in compact
    assert "attrs_number" not in compact
    assert "attrs_bool" not in compact
    assert params["exact_graph_root_partition_start_us"] == _unix_microseconds(
        partition_start
    )
    assert params["exact_graph_root_partition_end_us"] == _unix_microseconds(
        partition_end
    )
    assert params["exact_graph_root_start_us"] == _unix_microseconds(request_start)
    assert params["exact_graph_root_end_us"] == _unix_microseconds(request_end)
    assert params["exact_graph_root_after_trace_id"] == "trace-cursor"
    assert params["exact_graph_root_partition_limit"] == 50_001


@pytest.mark.unit
def test_exact_graph_raw_candidate_page_stays_finite_and_keyset_ordered():
    builder = _builder(_attribute_filter())

    assert builder.exact_graph_candidate_witness_replays_global_membership() is True
    assert builder.exact_graph_candidate_witness_has_deployed_value_index() is True
    sql, params = builder.build_exact_graph_candidate_witness_probe(limit=1_001)
    compact = _compact(sql)

    assert "LIMIT 1 BY trace_id" in compact
    assert "GROUP BY" not in compact
    assert "ORDER BY trace_id ASC" in compact
    assert re.search(r"LIMIT %\(exact_graph_candidate_limit\)s", compact)
    assert params["exact_graph_candidate_limit"] == 1_001

    next_sql, next_params = builder.build_exact_graph_candidate_witness_probe(
        limit=1_001,
        after_trace_id="11111111-1111-4111-8111-111111111111",
    )
    assert "trace_id > %(exact_graph_candidate_after_trace_id)s" in _compact(next_sql)
    assert next_params["exact_graph_candidate_after_trace_id"] == (
        "11111111-1111-4111-8111-111111111111"
    )

    assert (
        _builder(_system_filter()).exact_graph_candidate_witness_replays_global_membership()
        is False
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filter_type", "operation", "value", "expected"),
    [
        ("text", "equals", "customer-45142993", True),
        ("text", "in", ["customer-45142993", "customer-7"], True),
        ("text", "equals", "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}stanbul", False),
        ("text", "contains", "4514", False),
        ("number", "equals", 7, True),
        ("number", "in", [7, 9], True),
        ("number", "greater_than", 7, False),
        ("boolean", "equals", True, False),
    ],
)
def test_exact_graph_candidate_reports_only_deployed_value_indexes(
    filter_type: str,
    operation: str,
    value: Any,
    expected: bool,
):
    builder = _builder(
        _attribute_filter(
            filter_type=filter_type,
            operation=operation,
            value=value,
        )
    )

    assert (
        builder.exact_graph_candidate_witness_has_deployed_value_index() is expected
    )


@pytest.mark.unit
def test_exact_graph_global_classifier_accepts_5k_and_rejects_larger_batch():
    builder = _builder(_attribute_filter())
    rows = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:04d}",
            "start_time": WINDOW_END - timedelta(minutes=1),
        }
        for index in range(5_001)
    ]

    sql, params = builder.build_filter_identity_match_query_from_seed_rows(rows[:5_000])

    assert sql
    assert len(params["candidate_trace_ids"]) == 5_000
    with pytest.raises(ValueError, match="candidate trace batch exceeds bounded limit"):
        builder.build_filter_identity_match_query_from_seed_rows(rows)


class _AuthoritativeAnchorBuilderFake:
    def __init__(self) -> None:
        self.partition_calls: list[tuple[datetime, datetime, str | None, int]] = []
        self.root_calls: list[tuple[list[str], datetime, datetime]] = []

    @staticmethod
    def exact_graph_supports_authoritative_anchor_partition() -> bool:
        return True

    @staticmethod
    def build_exact_graph_anchor_scan_bounds() -> tuple[str, dict[str, Any]]:
        return "bounds", {}

    def build_exact_graph_latest_anchor_partition(
        self,
        *,
        partition_start: datetime,
        partition_end: datetime,
        before_trace_id: str | None = None,
        limit: int = 50_001,
    ) -> tuple[str, dict[str, Any]]:
        self.partition_calls.append(
            (partition_start, partition_end, before_trace_id, limit)
        )
        return f"partition:{before_trace_id or 'first'}", {}

    def build_exact_graph_root_membership_query(
        self,
        *,
        candidate_trace_ids: list[str],
        request_start: datetime,
        request_end: datetime,
    ) -> tuple[str, dict[str, Any]]:
        self.root_calls.append((list(candidate_trace_ids), request_start, request_end))
        return "roots", {}


class _PartitionedRootBuilderFake(_AuthoritativeAnchorBuilderFake):
    def __init__(self) -> None:
        super().__init__()
        self.root_partition_calls: list[
            tuple[datetime, datetime, datetime, datetime, str | None, int]
        ] = []

    def build_exact_graph_latest_root_partition(
        self,
        *,
        partition_start: datetime,
        partition_end: datetime,
        request_start: datetime,
        request_end: datetime,
        before_trace_id: str | None = None,
        limit: int = 50_001,
    ) -> tuple[str, dict[str, Any]]:
        self.root_partition_calls.append(
            (
                partition_start,
                partition_end,
                request_start,
                request_end,
                before_trace_id,
                limit,
            )
        )
        return f"root-partition:{partition_start.hour:02d}", {}


@pytest.mark.unit
def test_authoritative_anchor_route_skips_request_under_30_days(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(
        exact_module,
        "EXACT_GRAPH_TRACE_ANCHOR_MIN_REQUEST_WIDTH",
        timedelta(days=30),
    )
    monkeypatch.setattr(
        exact_module,
        "EXACT_GRAPH_TRACE_ANCHOR_MIN_RETENTION_FRACTION",
        0.25,
    )
    builder = _AuthoritativeAnchorBuilderFake()

    class Analytics:
        @staticmethod
        def execute_ch_query(*args: Any, **kwargs: Any):
            del args, kwargs
            raise AssertionError("short windows must not scan complete retention")

    result = exact_module._enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=WINDOW_START,
        request_end=WINDOW_END,
        started=monotonic(),
    )

    assert result is None
    assert builder.partition_calls == []
    assert builder.root_calls == []


@pytest.mark.unit
def test_short_window_skips_global_candidate_before_root_cursor(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1, 0, 0)
    request_end = request_start + timedelta(minutes=5)

    class Builder:
        def __init__(self, **kwargs: Any):
            del kwargs

        @staticmethod
        def supports_bounded_filter_scan() -> bool:
            return True

        @staticmethod
        def parse_time_range(_filters: list[dict[str, Any]]):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def exact_graph_supports_authoritative_anchor_partition() -> bool:
            return True

        @staticmethod
        def exact_graph_candidate_witness_replays_global_membership() -> bool:
            return True

        @staticmethod
        def exact_graph_candidate_witness_has_deployed_value_index() -> bool:
            return False

        @staticmethod
        def build_exact_graph_candidate_witness_probe(**_kwargs: Any):
            raise AssertionError("short windows must not scan retained history")

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs: Any):
            return "request-window-roots", {}

    class Analytics:
        calls: list[str] = []

        @classmethod
        def execute_ch_query(cls, query: str, _params: dict[str, Any], **_kwargs: Any):
            cls.calls.append(query)
            return SimpleNamespace(data=[], query_time_ms=1.0)

    monkeypatch.setattr(
        exact_module,
        "EXACT_GRAPH_TRACE_ANCHOR_MIN_REQUEST_WIDTH",
        timedelta(days=30),
    )
    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    result = exact_module._enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter(key="prompt_slug")],
        annotation_label_ids=None,
        started=monotonic(),
    )

    assert result == ([], 1, 0)
    assert Analytics.calls == ["request-window-roots"]


@pytest.mark.unit
def test_short_window_uses_proven_indexed_global_candidate(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1, 0, 0)
    request_end = request_start + timedelta(minutes=5)

    class Builder:
        def __init__(self, **kwargs: Any):
            del kwargs

        @staticmethod
        def supports_bounded_filter_scan() -> bool:
            return True

        @staticmethod
        def parse_time_range(_filters: list[dict[str, Any]]):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def exact_graph_supports_authoritative_anchor_partition() -> bool:
            return True

        @staticmethod
        def exact_graph_candidate_witness_replays_global_membership() -> bool:
            return True

        @staticmethod
        def exact_graph_candidate_witness_has_deployed_value_index() -> bool:
            return True

        @staticmethod
        def build_exact_graph_candidate_witness_probe(**_kwargs: Any):
            return "indexed-global-candidate", {}

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs: Any):
            raise AssertionError("indexed candidate must precede the root cursor")

    class Analytics:
        calls: list[str] = []

        @classmethod
        def execute_ch_query(cls, query: str, _params: dict[str, Any], **_kwargs: Any):
            cls.calls.append(query)
            return SimpleNamespace(data=[], query_time_ms=1.0)

    monkeypatch.setattr(
        exact_module,
        "EXACT_GRAPH_TRACE_ANCHOR_MIN_REQUEST_WIDTH",
        timedelta(days=30),
    )
    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    result = exact_module._enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id=PROJECT_ID,
        filters=[_time_filter(), _attribute_filter(key="customer_id")],
        annotation_label_ids=None,
        started=monotonic(),
    )

    assert result == ([], 1, 0)
    assert Analytics.calls == ["indexed-global-candidate"]


@pytest.mark.unit
def test_authoritative_anchor_route_skips_narrow_retention_fraction(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(
        exact_module,
        "EXACT_GRAPH_TRACE_ANCHOR_MIN_REQUEST_WIDTH",
        timedelta(days=30),
    )
    monkeypatch.setattr(
        exact_module,
        "EXACT_GRAPH_TRACE_ANCHOR_MIN_RETENTION_FRACTION",
        0.25,
    )
    builder = _AuthoritativeAnchorBuilderFake()
    request_start = datetime(2026, 6, 1)
    request_end = datetime(2026, 8, 1)

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            assert query == "bounds"
            return SimpleNamespace(
                data=[
                    {
                        "min_start_time": datetime(2025, 1, 1),
                        "max_start_time": datetime(2026, 8, 1),
                    }
                ],
                columns=["min_start_time", "max_start_time"],
            )

    result = exact_module._enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=request_start,
        request_end=request_end,
        started=monotonic(),
    )

    assert result is None
    assert builder.partition_calls == []
    assert builder.root_calls == []


@pytest.mark.unit
def test_authoritative_anchor_orchestrator_rounds_bounds_and_verifies_roots():
    from tracer.services.clickhouse.exact_graph_reads import (
        _enumerate_authoritative_anchor_trace_ids,
    )

    builder = _AuthoritativeAnchorBuilderFake()

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": datetime(2026, 7, 27, 0, 15),
                            "max_start_time": datetime(2026, 7, 27, 0, 45),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query == "partition:first":
                return SimpleNamespace(
                    data=[{"trace_id": "trace-a"}, {"trace_id": "trace-b"}],
                    columns=["trace_id"],
                )
            if query == "roots":
                return SimpleNamespace(
                    data=[{"trace_id": "trace-b"}],
                    columns=["trace_id"],
                )
            raise AssertionError(f"unexpected fake query: {query}")

    result = _enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=WINDOW_START,
        request_end=WINDOW_END,
        started=monotonic(),
    )

    assert result == (["trace-b"], 3, 4)
    assert builder.partition_calls == [
        (
            datetime(2026, 7, 27, 0, 0),
            datetime(2026, 7, 27, 1, 0),
            None,
            50_001,
        )
    ]
    assert builder.root_calls == [(["trace-a", "trace-b"], WINDOW_START, WINDOW_END)]


@pytest.mark.unit
def test_authoritative_anchor_compatibility_root_verifier_chunks_at_512():
    from tracer.services.clickhouse.exact_graph_reads import (
        _enumerate_authoritative_anchor_trace_ids,
    )

    builder = _AuthoritativeAnchorBuilderFake()
    trace_ids = [f"trace-{index:04d}" for index in range(513)]

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": datetime(2026, 7, 27, 0, 15),
                            "max_start_time": datetime(2026, 7, 27, 0, 45),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query == "partition:first":
                return SimpleNamespace(
                    data=[{"trace_id": trace_id} for trace_id in trace_ids],
                    columns=["trace_id"],
                )
            if query == "roots":
                return SimpleNamespace(
                    data=[
                        {"trace_id": trace_id} for trace_id in builder.root_calls[-1][0]
                    ],
                    columns=["trace_id"],
                )
            raise AssertionError(f"unexpected fake query: {query}")

    result = _enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=WINDOW_START,
        request_end=WINDOW_END,
        started=monotonic(),
    )

    assert result == (trace_ids, 4, 1_027)
    assert [len(call[0]) for call in builder.root_calls] == [512, 1]


@pytest.mark.unit
def test_authoritative_anchor_orchestrator_intersects_partitioned_latest_roots(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(
        exact_module,
        "EXACT_GRAPH_TRACE_ROOT_VERIFY_BATCH_SIZE",
        1,
    )

    builder = _PartitionedRootBuilderFake()
    request_start = datetime(2026, 7, 27, 0, 15)
    request_end = datetime(2026, 7, 27, 3, 45)

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": datetime(2026, 7, 27, 0, 20),
                            "max_start_time": datetime(2026, 7, 27, 1, 40),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query == "partition:first":
                return SimpleNamespace(
                    data=[
                        {"trace_id": "match-has-live-root"},
                        {"trace_id": "match-without-live-root"},
                    ],
                    columns=["trace_id"],
                )
            if query == "root-partition:00":
                return SimpleNamespace(
                    data=[
                        {"trace_id": "match-has-live-root"},
                        {"trace_id": "unmatched-live-root"},
                    ],
                    columns=["trace_id"],
                )
            if query == "root-partition:02":
                return SimpleNamespace(
                    data=[{"trace_id": "another-unmatched-live-root"}],
                    columns=["trace_id"],
                )
            raise AssertionError(f"unexpected fake query: {query}")

    result = exact_module._enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=request_start,
        request_end=request_end,
        started=monotonic(),
    )

    assert result == (["match-has-live-root"], 4, 6)
    assert builder.root_calls == []
    assert {(call[0], call[1]) for call in builder.root_partition_calls} == {
        (datetime(2026, 7, 27, 0, 0), datetime(2026, 7, 27, 2, 0)),
        (datetime(2026, 7, 27, 2, 0), datetime(2026, 7, 27, 4, 0)),
    }
    assert all(
        (call[2], call[3]) == (request_start, request_end)
        for call in builder.root_partition_calls
    )


@pytest.mark.unit
def test_authoritative_anchor_orchestrator_exhausts_partitioned_root_cursor(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_ANCHOR_RESULT_SENTINEL", 3)
    monkeypatch.setattr(
        exact_module,
        "EXACT_GRAPH_TRACE_ROOT_VERIFY_BATCH_SIZE",
        1,
    )

    class RootCursorBuilder(_PartitionedRootBuilderFake):
        def build_exact_graph_latest_root_partition(
            self,
            *,
            partition_start: datetime,
            partition_end: datetime,
            request_start: datetime,
            request_end: datetime,
            before_trace_id: str | None = None,
            limit: int = 50_001,
        ) -> tuple[str, dict[str, Any]]:
            self.root_partition_calls.append(
                (
                    partition_start,
                    partition_end,
                    request_start,
                    request_end,
                    before_trace_id,
                    limit,
                )
            )
            return f"root:{before_trace_id or 'first'}", {}

    builder = RootCursorBuilder()
    request_start = datetime(2026, 7, 27, 0, 15)
    request_end = datetime(2026, 7, 27, 0, 45)

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": datetime(2026, 7, 27, 0, 20),
                            "max_start_time": datetime(2026, 7, 27, 0, 40),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query == "partition:first":
                return SimpleNamespace(
                    data=[{"trace_id": "trace-a"}, {"trace_id": "trace-d"}],
                    columns=["trace_id"],
                )
            if query == "root:first":
                return SimpleNamespace(
                    data=[
                        {"trace_id": "trace-a"},
                        {"trace_id": "trace-b"},
                        {"trace_id": "trace-c"},
                    ],
                    columns=["trace_id"],
                )
            if query == "root:trace-c":
                return SimpleNamespace(
                    data=[{"trace_id": "trace-d"}],
                    columns=["trace_id"],
                )
            raise AssertionError(f"unexpected fake query: {query}")

    result = exact_module._enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=request_start,
        request_end=request_end,
        started=monotonic(),
    )

    assert result == (["trace-a", "trace-d"], 4, 7)
    assert [call[4] for call in builder.root_partition_calls] == [None, "trace-c"]
    assert [call[5] for call in builder.root_partition_calls] == [3, 3]
    assert builder.root_calls == []


@pytest.mark.unit
def test_authoritative_anchor_uses_finite_root_verifier_for_small_candidates():
    from tracer.services.clickhouse.exact_graph_reads import (
        _enumerate_authoritative_anchor_trace_ids,
    )

    builder = _PartitionedRootBuilderFake()

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": datetime(2026, 7, 27, 0, 15),
                            "max_start_time": datetime(2026, 7, 27, 0, 45),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query == "partition:first":
                return SimpleNamespace(
                    data=[{"trace_id": "trace-a"}, {"trace_id": "trace-b"}],
                    columns=["trace_id"],
                )
            if query == "roots":
                return SimpleNamespace(
                    data=[{"trace_id": "trace-b"}],
                    columns=["trace_id"],
                )
            raise AssertionError(f"unexpected fake query: {query}")

    result = _enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=WINDOW_START,
        request_end=WINDOW_END,
        started=monotonic(),
    )

    assert result == (["trace-b"], 3, 4)
    assert builder.root_calls == [(["trace-a", "trace-b"], WINDOW_START, WINDOW_END)]
    assert builder.root_partition_calls == []


@pytest.mark.unit
def test_authoritative_anchor_orchestrator_exhausts_trace_id_cursor(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_ANCHOR_RESULT_SENTINEL", 3)
    builder = _AuthoritativeAnchorBuilderFake()

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": datetime(2026, 7, 27, 0, 15),
                            "max_start_time": datetime(2026, 7, 27, 0, 45),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query == "partition:first":
                return SimpleNamespace(
                    data=[
                        {"trace_id": "trace-a"},
                        {"trace_id": "trace-b"},
                        {"trace_id": "trace-c"},
                    ],
                    columns=["trace_id"],
                )
            if query == "partition:trace-c":
                return SimpleNamespace(
                    data=[{"trace_id": "trace-d"}],
                    columns=["trace_id"],
                )
            if query == "roots":
                return SimpleNamespace(
                    data=[
                        {"trace_id": "trace-a"},
                        {"trace_id": "trace-b"},
                        {"trace_id": "trace-c"},
                        {"trace_id": "trace-d"},
                    ],
                    columns=["trace_id"],
                )
            raise AssertionError(f"unexpected fake query: {query}")

    result = exact_module._enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=WINDOW_START,
        request_end=WINDOW_END,
        started=monotonic(),
    )

    assert result == (
        ["trace-a", "trace-b", "trace-c", "trace-d"],
        4,
        9,
    )
    assert [call[2] for call in builder.partition_calls] == [None, "trace-c"]
    assert [call[3] for call in builder.partition_calls] == [3, 3]


@pytest.mark.unit
def test_authoritative_anchor_orchestrator_covers_contiguous_full_partitions(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_ANCHOR_MAX_WORKERS", 1)

    builder = _AuthoritativeAnchorBuilderFake()

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": datetime(2026, 7, 27, 0, 15),
                            "max_start_time": datetime(2026, 7, 27, 6, 45),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query == "partition:first":
                partition_start, partition_end, _, _ = builder.partition_calls[-1]
                return SimpleNamespace(
                    data=[
                        {
                            "trace_id": (
                                f"trace-{partition_start.hour:02d}-"
                                f"{partition_end.hour:02d}"
                            )
                        }
                    ],
                    columns=["trace_id"],
                )
            if query == "roots":
                return SimpleNamespace(
                    data=[
                        {"trace_id": trace_id} for trace_id in builder.root_calls[-1][0]
                    ],
                    columns=["trace_id"],
                )
            raise AssertionError(f"unexpected fake query: {query}")

    result = exact_module._enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=WINDOW_START,
        request_end=WINDOW_END,
        started=monotonic(),
    )

    expected_partitions = [
        (datetime(2026, 7, 27, 0, 0), datetime(2026, 7, 27, 2, 0)),
        (datetime(2026, 7, 27, 2, 0), datetime(2026, 7, 27, 6, 0)),
        (datetime(2026, 7, 27, 6, 0), datetime(2026, 7, 27, 7, 0)),
    ]
    assert [(call[0], call[1]) for call in builder.partition_calls] == (
        expected_partitions
    )
    assert result == (
        ["trace-00-02", "trace-02-06", "trace-06-07"],
        5,
        7,
    )


@pytest.mark.unit
def test_authoritative_anchor_orchestrator_caps_width_after_budget_split(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_ANCHOR_MAX_WORKERS", 1)

    builder = _AuthoritativeAnchorBuilderFake()
    safe_width = timedelta(hours=2)

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": datetime(2026, 7, 27, 0, 15),
                            "max_start_time": datetime(2026, 7, 27, 7, 45),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query == "partition:first":
                partition_start, partition_end, _, _ = builder.partition_calls[-1]
                if partition_end - partition_start > safe_width:
                    raise ServerException("bounded read exceeded", code=159)
                return SimpleNamespace(
                    data=[
                        {
                            "trace_id": (
                                f"trace-{partition_start.hour:02d}-"
                                f"{partition_end.hour:02d}"
                            )
                        }
                    ],
                    columns=["trace_id"],
                )
            if query == "roots":
                return SimpleNamespace(
                    data=[
                        {"trace_id": trace_id} for trace_id in builder.root_calls[-1][0]
                    ],
                    columns=["trace_id"],
                )
            raise AssertionError(f"unexpected fake query: {query}")

    result = exact_module._enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=WINDOW_START,
        request_end=WINDOW_END,
        started=monotonic(),
    )

    attempted_widths = [
        partition_end - partition_start
        for partition_start, partition_end, _, _ in builder.partition_calls
    ]
    assert attempted_widths == [
        timedelta(hours=2),
        timedelta(hours=4),
        timedelta(hours=2),
        timedelta(hours=2),
        timedelta(hours=2),
    ]
    assert result == (
        ["trace-00-02", "trace-02-04", "trace-04-06", "trace-06-08"],
        7,
        9,
    )


@pytest.mark.unit
def test_authoritative_anchor_orchestrator_grows_across_sparse_year(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_ANCHOR_MAX_WORKERS", 1)

    builder = _AuthoritativeAnchorBuilderFake()
    retention_start = datetime(2025, 1, 1, 0, 0)
    retention_end = datetime(2026, 1, 1, 0, 0)

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": retention_start,
                            "max_start_time": retention_end - timedelta(minutes=1),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query == "partition:first":
                return SimpleNamespace(data=[], columns=["trace_id"])
            raise AssertionError(f"unexpected fake query: {query}")

    result = exact_module._enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=retention_start,
        request_end=retention_end,
        started=monotonic(),
    )

    partitions = [(call[0], call[1]) for call in builder.partition_calls]
    assert len(partitions) == 13
    assert [end - start for start, end in partitions[:4]] == [
        timedelta(hours=2),
        timedelta(hours=4),
        timedelta(hours=8),
        timedelta(hours=16),
    ]
    assert partitions[0][0] == retention_start
    assert partitions[-1][1] == retention_end
    assert all(
        current_end == next_start
        for (_, current_end), (next_start, _) in zip(
            partitions, partitions[1:], strict=False
        )
    )
    assert result == ([], 14, 1)


@pytest.mark.unit
def test_authoritative_anchor_orchestrator_runs_two_disjoint_partitions_in_parallel(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_ANCHOR_MAX_WORKERS", 2)

    class ConcurrentBuilder(_AuthoritativeAnchorBuilderFake):
        def build_exact_graph_latest_anchor_partition(
            self,
            *,
            partition_start: datetime,
            partition_end: datetime,
            before_trace_id: str | None = None,
            limit: int = 50_001,
        ) -> tuple[str, dict[str, Any]]:
            self.partition_calls.append(
                (partition_start, partition_end, before_trace_id, limit)
            )
            return f"partition:{partition_start.hour:02d}", {}

    builder = ConcurrentBuilder()
    partition_barrier = threading.Barrier(2, timeout=2)

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": datetime(2026, 7, 27, 0, 15),
                            "max_start_time": datetime(2026, 7, 27, 3, 45),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query.startswith("partition:"):
                partition_hour = int(query.rsplit(":", 1)[1])
                partition_barrier.wait()
                return SimpleNamespace(
                    data=[{"trace_id": f"trace-{partition_hour:02d}"}],
                    columns=["trace_id"],
                )
            if query == "roots":
                return SimpleNamespace(
                    data=[
                        {"trace_id": trace_id} for trace_id in builder.root_calls[-1][0]
                    ],
                    columns=["trace_id"],
                )
            raise AssertionError(f"unexpected fake query: {query}")

    result = exact_module._enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=WINDOW_START,
        request_end=WINDOW_END,
        started=monotonic(),
    )

    assert result == (["trace-00", "trace-02"], 4, 5)
    assert {(call[0], call[1]) for call in builder.partition_calls} == {
        (datetime(2026, 7, 27, 0, 0), datetime(2026, 7, 27, 2, 0)),
        (datetime(2026, 7, 27, 2, 0), datetime(2026, 7, 27, 4, 0)),
    }


@pytest.mark.unit
def test_authoritative_anchor_orchestrator_splits_budget_failure_on_hour_boundary():
    from tracer.services.clickhouse.exact_graph_reads import (
        _enumerate_authoritative_anchor_trace_ids,
    )

    builder = _AuthoritativeAnchorBuilderFake()

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": datetime(2026, 7, 27, 0, 15),
                            "max_start_time": datetime(2026, 7, 27, 1, 45),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query == "partition:first":
                partition_start, partition_end, _, _ = builder.partition_calls[-1]
                if partition_end - partition_start > exact_hour:
                    raise ServerException("bounded read exceeded", code=159)
                return SimpleNamespace(
                    data=[{"trace_id": f"trace-{partition_start.hour:02d}"}],
                    columns=["trace_id"],
                )
            if query == "roots":
                return SimpleNamespace(
                    data=[
                        {"trace_id": trace_id} for trace_id in builder.root_calls[-1][0]
                    ],
                    columns=["trace_id"],
                )
            raise AssertionError(f"unexpected fake query: {query}")

    exact_hour = timedelta(hours=1)
    result = _enumerate_authoritative_anchor_trace_ids(
        analytics=Analytics(),
        builder=builder,
        request_start=WINDOW_START,
        request_end=WINDOW_END,
        started=monotonic(),
    )

    assert [(call[0], call[1]) for call in builder.partition_calls] == [
        (datetime(2026, 7, 27, 0, 0), datetime(2026, 7, 27, 2, 0)),
        (datetime(2026, 7, 27, 0, 0), datetime(2026, 7, 27, 1, 0)),
        (datetime(2026, 7, 27, 1, 0), datetime(2026, 7, 27, 2, 0)),
    ]
    assert result == (["trace-00", "trace-01"], 5, 5)


@pytest.mark.unit
def test_authoritative_anchor_orchestrator_one_hour_budget_failure_is_fatal():
    from tracer.services.clickhouse.exact_graph_reads import (
        _enumerate_authoritative_anchor_trace_ids,
    )

    builder = _AuthoritativeAnchorBuilderFake()

    class Analytics:
        @staticmethod
        def execute_ch_query(query: str, params: dict[str, Any], **kwargs: Any):
            del params, kwargs
            if query == "bounds":
                return SimpleNamespace(
                    data=[
                        {
                            "min_start_time": datetime(2026, 7, 27, 0, 15),
                            "max_start_time": datetime(2026, 7, 27, 0, 45),
                        }
                    ],
                    columns=["min_start_time", "max_start_time"],
                )
            if query == "partition:first":
                raise ServerException("bounded read exceeded", code=159)
            raise AssertionError(f"unexpected fake query: {query}")

    with pytest.raises(ServerException) as exc_info:
        _enumerate_authoritative_anchor_trace_ids(
            analytics=Analytics(),
            builder=builder,
            request_start=WINDOW_START,
            request_end=WINDOW_END,
            started=monotonic(),
        )

    assert exc_info.value.code == 159
    assert [(call[0], call[1]) for call in builder.partition_calls] == [
        (datetime(2026, 7, 27, 0, 0), datetime(2026, 7, 27, 1, 0))
    ]


@pytest.mark.unit
def test_authoritative_anchor_orchestrator_returns_none_for_unsupported_builder():
    from tracer.services.clickhouse.exact_graph_reads import (
        _enumerate_authoritative_anchor_trace_ids,
    )

    class UnsupportedBuilder:
        @staticmethod
        def exact_graph_supports_authoritative_anchor_partition() -> bool:
            return False

    class Analytics:
        @staticmethod
        def execute_ch_query(*args: Any, **kwargs: Any):
            raise AssertionError("unsupported lane must not execute ClickHouse")

    assert (
        _enumerate_authoritative_anchor_trace_ids(
            analytics=Analytics(),
            builder=UnsupportedBuilder(),
            request_start=WINDOW_START,
            request_end=WINDOW_END,
            started=monotonic(),
        )
        is None
    )
