from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.views.trace import (
    _append_trace_attribute_value,
    _decode_projected_trace_attribute_value,
    _iter_merged_trace_attribute_rows,
    _trace_attribute_value_token,
)

pytestmark = pytest.mark.unit


def _builder():
    builder = TraceListQueryBuilderV2(
        project_id="00000000-0000-4000-8000-000000000001",
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        datetime(2026, 7, 1, tzinfo=UTC),
                        datetime(2026, 8, 1, tzinfo=UTC),
                    ],
                },
            }
        ],
    )
    builder.build()
    return builder


def test_trace_attribute_hydration_is_skipped_without_requested_keys():
    sql, params = _builder().build_span_attributes_query(["trace-1"])

    assert sql == ""
    assert params == {}


def test_trace_attribute_hydration_projects_latest_requested_key_value():
    builder = _builder()

    sql, params = builder.build_span_attributes_query(
        ["trace-1"],
        attribute_keys=["final_status", "final_status", "nested.flag"],
    )

    assert "argMax(attrs_string, _version)" in sql
    assert "argMax(attrs_number, _version)" in sql
    assert "argMax(attrs_bool, _version)" in sql
    assert "argMax(tuple(attributes_extra), _version).1" in sql
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in sql
    assert "WHERE latest_is_deleted = 0" in sql
    assert "GROUP BY project_id, trace_id, id, start_time" in sql
    assert "GROUP BY project_id, trace_id, attribute_key" in sql
    assert "groupArray" not in sql
    assert "ARRAY JOIN %(requested_attribute_keys)s AS attribute_key" in sql
    assert "SELECT DISTINCT" not in sql
    assert "argMax(candidate_attribute_value_json, tuple(start_time, id))" in sql
    assert "attribute_value_json" in sql
    assert "JSONExtractRaw(latest_attributes_extra, attribute_key)" in sql
    assert "mapContains(latest_attrs_bool, attribute_key)" in sql
    assert "mapContains(latest_attrs_number, attribute_key)" in sql
    assert "mapContains(latest_attrs_string, attribute_key)" in sql
    assert "LIMIT" not in sql
    assert "INTERVAL 1 DAY" not in sql
    assert "start_time >= %(start_date)s" not in sql
    assert params["attr_trace_identities"] == (
        ("00000000-0000-4000-8000-000000000001", "trace-1"),
    )
    assert params["requested_attribute_keys"] == ["final_status", "nested.flag"]
    assert "attribute_value_limit" not in params


def test_more_than_5000_historical_values_collapse_to_one_latest_value_per_key():
    trace_ids = ["high-fanout-trace"]
    sql, params = _builder().build_span_attributes_query(
        trace_ids,
        attribute_keys=["final_status"],
    )

    # Even if this trace has 5,002 (or millions of) historical distinct values,
    # the outer aggregation emits one deterministic latest live value for its
    # one requested key. There is no fixed value-count failure ceiling.
    assert "groupArray" not in sql
    assert "arrayJoin(mapKeys" not in sql
    assert "argMax(candidate_attribute_value_json, tuple(start_time, id))" in sql
    assert "GROUP BY project_id, trace_id, attribute_key" in sql
    assert "LIMIT" not in sql
    # ARRAY JOIN must receive an Array/list. clickhouse-driver formats a
    # one-element tuple as a scalar String, which ClickHouse 25 rejects.
    assert params["requested_attribute_keys"] == ["final_status"]
    assert len(params["attr_trace_identities"]) == len(trace_ids)


def test_attribute_hydration_does_not_drop_children_after_one_day():
    sql, _ = _builder().build_span_attributes_query(
        ["long-running-trace"], attribute_keys=["final_status"]
    )

    # Membership is project + page trace id. A date predicate here would
    # silently omit a child created >24h after the in-window root.
    assert "start_time >=" not in sql
    assert "start_time <" not in sql


def test_org_attribute_hydration_keeps_project_in_exact_identity():
    project_a = "00000000-0000-4000-8000-000000000001"
    project_b = "00000000-0000-4000-8000-000000000002"
    builder = TraceListQueryBuilderV2(project_ids=[project_a, project_b], filters=[])

    sql, params = builder.build_span_attributes_query(
        ["customer-controlled-shared-trace"],
        attribute_keys=["final_status"],
        trace_identities=[
            (project_a, "customer-controlled-shared-trace"),
            (project_b, "customer-controlled-shared-trace"),
        ],
    )

    assert "toString(project_id) AS project_id" in sql
    assert "project_id IN %(project_ids)s" in sql
    assert "GROUP BY project_id, trace_id, id, start_time" in sql
    assert params["attr_trace_identities"] == (
        (project_a, "customer-controlled-shared-trace"),
        (project_b, "customer-controlled-shared-trace"),
    )
    assert params["project_ids"] == (project_a, project_b)


def test_org_attribute_hydration_rejects_cross_tenant_identity():
    builder = TraceListQueryBuilderV2(
        project_ids=["00000000-0000-4000-8000-000000000001"], filters=[]
    )

    with pytest.raises(ValueError, match="escaped request scope"):
        builder.build_span_attributes_query(
            ["trace-1"],
            attribute_keys=["final_status"],
            trace_identities=[("00000000-0000-4000-8000-000000000002", "trace-1")],
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"Rechazado"', "Rechazado"),
        ("42.5", 42.5),
        ("true", True),
        ('{"attempt":2}', {"attempt": 2}),
        ("null", None),
    ],
)
def test_projected_trace_attribute_json_preserves_exact_type(raw, expected):
    assert _decode_projected_trace_attribute_value(raw) == expected


@pytest.mark.parametrize("raw", ["", "not-json"])
def test_projected_trace_attribute_json_rejects_incomplete_values(raw):
    with pytest.raises(ValueError):
        _decode_projected_trace_attribute_value(raw)


def test_trace_attribute_replay_keeps_all_5002_physical_rows_exactly():
    packed = [
        (
            f'{{"shared":"extra-{index}","physical":{index}}}',
            {"shared": "typed", "string_value": f"value-{index}"},
            {"number_value": float(index)},
            {"bool_value": index % 2},
        )
        for index in range(5_002)
    ]

    merged = list(
        _iter_merged_trace_attribute_rows(
            {
                "project_id": "project-a",
                "trace_id": "high-fanout",
                "attribute_rows": packed,
            }
        )
    )

    assert len(merged) == 5_002
    assert merged[0]["shared"] == "extra-0"
    assert merged[-1]["shared"] == "extra-5001"
    assert merged[-1]["physical"] == 5_001
    assert merged[-1]["number_value"] == 5_001.0
    assert merged[0]["bool_value"] is False
    assert merged[1]["bool_value"] is True


@pytest.mark.parametrize("structured_first", [False, True])
def test_trace_attribute_accumulator_preserves_mixed_values_in_both_orders(
    structured_first,
):
    values = []
    ordered = (
        [{"attempt": 2}, "Rechazado"]
        if structured_first
        else ["Rechazado", {"attempt": 2}]
    )

    for value in ordered:
        _append_trace_attribute_value(values, value)
    _append_trace_attribute_value(values, {"attempt": 2})

    assert sorted(values, key=_trace_attribute_value_token) == [
        "Rechazado",
        {"attempt": 2},
    ]


def test_trace_attribute_accumulator_normalizes_direct_write_boolean_once():
    values = []

    _append_trace_attribute_value(values, True)
    _append_trace_attribute_value(values, "true")

    assert values == ["true"]
