"""Native Session graph/list membership parity on SELECT-only constant fixtures.

The graph root source uses an explicit six-key argMax fixture to emulate FINAL.
This verifies generated membership SQL, not physical MergeTree FINAL execution,
production storage performance, cache dispatch, or graph bucket formatting.
"""

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
    from tracer.services.clickhouse.exact_graph_reads import (
        _session_aggregate_source_sql,
    )

    source, params = _session_aggregate_source_sql(
        project_id=PROJECT,
        filters=builder.filters,
        start_date=END - timedelta(days=days),
        end_date=END,
        include_trace_ids=False,
        anchor_by_session_start=True,
        use_scalar_witness=True,
    )
    sql = "SELECT session_id FROM (" + source + ")"
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
    rendered = rendered.replace("spans FINAL", "latest_spans")
    rendered = rendered.replace(
        "trace_session_id_remap FINAL", "trace_session_id_remap"
    )
    prefix = f"""WITH spans AS (SELECT *, mapFromArrays(string_keys,string_values) AS attrs_string,mapFromArrays(number_keys,number_values) AS attrs_number,mapFromArrays(bool_keys,bool_values) AS attrs_bool,'{{}}' AS attributes_extra, start_time AS end_time, 0. AS latency_ms, 'UNSET' AS status, 0. AS cost,toInt32(0) AS total_tokens,toInt32(0) AS prompt_tokens,toInt32(0) AS completion_tokens FROM values({literals["schema"]},{literals["rows"][1:-1]})),trace_session_id_remap AS (SELECT toUUID('00000000-0000-0000-0000-000000000000') AS old_id,old_id AS new_id WHERE 0),"""
    fields = [
        "project_id",
        "observation_type",
        "service_name",
        "trace_id",
        "id",
        "start_time",
        "trace_session_id",
        "parent_span_id",
        "_version",
        "is_deleted",
        "end_time",
        "latency_ms",
        "cost",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "status",
    ]
    packed = ",".join("raw." + name for name in fields)
    projected = ",".join(f"w.{i} AS {name}" for i, name in enumerate(fields, 1))
    prefix += f"latest_spans AS (SELECT argMax(tuple({packed}),raw._version) AS w,{projected} FROM spans AS raw GROUP BY raw.project_id,raw.observation_type,raw.service_name,toStartOfHour(raw.start_time),raw.trace_id,raw.id),"
    # Graph SQL has an outer SELECT, so append the fixture CTEs before it.
    return [
        json.loads(line)
        for line in str(
            engine.query(values_where(prefix[:-1] + " " + rendered), "JSONEachRow")
        ).splitlines()
        if line
    ]


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("width", [2, 5, 10])
@pytest.mark.parametrize("negative", [False, True])
def test_session_graph_list_conjunction_parity(days, width, negative):
    from tracer.tests.test_session_native_conjunction_replay import run as run_list

    cases = (
        [
            ("text", "Allowed", "not_contains", "forbidden", "FORBIDDEN value"),
            ("number", 7, "not_in", [9, 11], 9),
            ("boolean", None, "is_null", None, True),
        ]
        if negative
        else [
            ("text", "Alpha", "equals", "Alpha", None),
            ("number", 7, "equals", 7, None),
            ("boolean", True, "equals", True, None),
        ]
    )
    rows, filters, invalid_values = [], [], []
    for index in range(width):
        kind, value, operation, operand, invalid = cases[index % len(cases)]
        key = f"property_{index}"
        rows.append((index, kind, key, value, 1, 0))
        filters.append(_filter(key, operand, kind=kind, operation=operation))
        invalid_values.append(invalid)

    def assert_membership(physical_rows, expected):
        graph_ids = [row["session_id"] for row in run(physical_rows, filters, days)]
        list_ids = [row["session_id"] for row in run_list(physical_rows, filters, days)]
        assert graph_ids == list_ids == expected

    assert_membership(rows, [SESSION])
    for original, invalid in zip(rows, invalid_values, strict=True):
        index, kind, key, _, _, _ = original
        assert_membership([*rows, (index, kind, key, invalid, 2, 0)], [])
