"""Execute Session conjunctions across distinct traces using SELECT-only fixtures."""

import json
from datetime import timedelta

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests.test_session_entity_filter_membership import (
    END,
    PROJECT,
    SESSION,
    _filter,
)
from tracer.tests.test_users_attribute_physical_replay import (
    CONTEXT,
    values_where,
    without_query_settings,
)


def run(rows, filters, days=7):
    engine = pytest.importorskip("chdb")
    builder = SessionListQueryBuilderV2(
        project_id=PROJECT,
        filters=[
            _filter(
                "created_at",
                [(END - timedelta(days=days)).isoformat(), END.isoformat()],
                kind="datetime",
                operation="between",
                source="SYSTEM_METRIC",
            ),
            *filters,
        ],
        bounded_internal_scan=True,
    )
    sql, params = builder.build_filter_match_query([SESSION])
    schema = "project_id UUID, observation_type String, service_name String, trace_id String, id String, start_time DateTime64(6,'UTC'), trace_session_id Nullable(UUID), parent_span_id Nullable(String), _version UInt64, is_deleted UInt8, string_keys Array(String), string_values Array(String), number_keys Array(String), number_values Array(Float64), bool_keys Array(String), bool_values Array(UInt8)"
    data = []
    for index, kind, key, value, version, deleted in rows:
        text = {key: value} if kind == "text" and value is not None else {}
        number = {key: value} if kind == "number" and value is not None else {}
        boolean = {key: value} if kind == "boolean" and value is not None else {}
        data.append(
            (
                PROJECT,
                "SPAN",
                "svc",
                f"trace-{index}",
                f"span-{index}",
                (END - timedelta(hours=1)).isoformat(sep=" "),
                SESSION,
                None,
                version,
                deleted,
                list(text),
                list(text.values()),
                list(number),
                list(number.values()),
                list(boolean),
                list(boolean.values()),
            )
        )
    literals = escape_params({"schema": schema, "rows": tuple(data)}, CONTEXT)
    rendered = without_query_settings(sql) % escape_params(params, CONTEXT)
    rendered = rendered.replace(
        "trace_session_id_remap FINAL", "trace_session_id_remap"
    )
    prefix = f"""WITH spans AS (SELECT *, mapFromArrays(string_keys,string_values) AS attrs_string,mapFromArrays(number_keys,number_values) AS attrs_number,mapFromArrays(bool_keys,bool_values) AS attrs_bool,'{{}}' AS attributes_extra, start_time AS end_time, 0. AS cost,toInt32(0) AS total_tokens,toInt32(0) AS prompt_tokens,toInt32(0) AS completion_tokens FROM values({literals["schema"]},{literals["rows"][1:-1]})),trace_session_id_remap AS (SELECT toUUID('00000000-0000-0000-0000-000000000000') AS old_id,old_id AS new_id WHERE 0),"""
    return [
        json.loads(line)
        for line in str(
            engine.query(values_where(prefix + rendered.lstrip()[4:]), "JSONEachRow")
        ).splitlines()
        if line
    ]


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("width", [2, 5, 10])
def test_session_conjunction_rejects_each_stale_leaf(days, width):
    # Each leaf belongs to a different trace of one session. The session must
    # satisfy all leaves, even though no individual trace satisfies them all.
    kinds = [("text", "Alpha"), ("number", 7), ("boolean", True)]
    rows = []
    filters = []
    for index in range(width):
        kind, value = kinds[index % len(kinds)]
        key = f"property_{index}"
        rows.append((index, kind, key, value, 1, 0))
        filters.append(_filter(key, value, kind=kind))
    assert [row["session_id"] for row in run(rows, filters, days)] == [SESSION]
    for index, kind, key, value, _, _ in rows:
        # Removing only one latest leaf must reject the session. Its old
        # matching physical version deliberately remains in the input.
        assert not run([*rows, (index, kind, key, None, 2, 0)], filters, days)
        # A latest tombstone must also hide the old matching leaf.
        assert not run([*rows, (index, kind, key, value, 2, 1)], filters, days)


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("width", [2, 5, 10])
def test_session_negative_conjunction_uses_all_latest_traces(days, width):
    # Negative membership excludes the whole session if any latest live trace
    # violates a leaf; unrelated traces with missing keys cannot make it pass.
    cases = [
        ("text", "Allowed", "not_contains", "forbidden", "FORBIDDEN value"),
        ("number", 7, "not_in", [9, 11], 9),
        ("boolean", None, "is_null", None, True),
    ]
    rows, filters, forbidden_values = [], [], []
    for index in range(width):
        kind, value, operation, operand, forbidden = cases[index % len(cases)]
        key = f"negative_property_{index}"
        rows.append((index, kind, key, value, 1, 0))
        filters.append(_filter(key, operand, kind=kind, operation=operation))
        forbidden_values.append(forbidden)
    assert [row["session_id"] for row in run(rows, filters, days)] == [SESSION]
    for original, forbidden in zip(rows, forbidden_values, strict=True):
        index, kind, key, value, _, _ = original
        violation = (index, kind, key, forbidden, 2, 0)
        assert not run([*rows, violation], filters, days)
        # An older violation must not exclude a corrected latest version.
        restored = (index, kind, key, value, 3, 0)
        assert [
            row["session_id"]
            for row in run([*rows, violation, restored], filters, days)
        ] == [SESSION]
