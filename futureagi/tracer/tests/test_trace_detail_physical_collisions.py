"""Real ClickHouse trace-detail replay of distinct physical service/type keys."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from clickhouse_driver import Client

from tracer.services.clickhouse.v2.trace_detail_reads import read_trace_detail

PROJECT = "11111111-1111-4111-8111-111111111111"
START = datetime(2026, 8, 8, 12, 20, tzinfo=UTC)


@pytest.fixture
def analytics(tmp_path):
    pytest.importorskip("chdb")
    from chdb.session import Session

    session = Session(str(tmp_path / "trace-detail-spans"))
    formatter = Client("invalid.invalid")
    formatter.connection.context.server_info = SimpleNamespace(
        get_timezone=lambda: "UTC"
    )

    def execute(sql, params=None):
        if params:
            sql = formatter.substitute_params(sql, params, formatter.connection.context)
        rows = str(session.query(sql, "JSONEachRow"))
        return [json.loads(line) for line in rows.splitlines() if line]

    execute("""CREATE TABLE spans (
        project_id UUID, observation_type String, service_name String,
        start_time DateTime64(6, 'UTC'), trace_id String, id String,
        parent_span_id Nullable(String), name String DEFAULT '',
        end_time Nullable(DateTime64(6, 'UTC')),
        input String DEFAULT '', output String DEFAULT '', model String DEFAULT '',
        latency_ms Nullable(Int64), prompt_tokens Int64 DEFAULT 0,
        completion_tokens Int64 DEFAULT 0, total_tokens Int64 DEFAULT 0,
        cost Float64 DEFAULT 0, status String DEFAULT '',
        status_message Nullable(String), tags String DEFAULT '[]',
        span_events String DEFAULT '[]', provider String DEFAULT '',
        attributes_extra String DEFAULT '{}', project_version_id Nullable(UUID),
        custom_eval_config_id Nullable(UUID), trace_session_id Nullable(UUID),
        metadata Map(String, String), attrs_string Map(String, String),
        attrs_number Map(String, Float64), attrs_bool Map(String, UInt8),
        is_deleted UInt8 DEFAULT 0, _version UInt64
    ) ENGINE = ReplacingMergeTree(_version, is_deleted)
    PARTITION BY toDate(start_time)
    ORDER BY (project_id, observation_type, service_name,
              toStartOfHour(start_time), trace_id, id)""")
    execute("SYSTEM STOP MERGES spans")

    class Analytics:
        def insert(self, span_id, *, service="svc", kind="SPAN", version=1):
            execute(
                """INSERT INTO spans
                (project_id, trace_id, id, start_time, service_name,
                 observation_type, _version, input)
                VALUES (%(project)s, 'trace', %(span)s,
                        fromUnixTimestamp64Micro(%(start_us)s),
                        %(service)s, %(kind)s, %(version)s, %(input)s)""",
                {
                    "project": PROJECT,
                    "span": span_id,
                    "start_us": int(START.timestamp() * 1_000_000),
                    "service": service,
                    "kind": kind,
                    "version": version,
                    "input": f"version-{version}",
                },
            )

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            rows = execute(query, params)
            for row in rows:
                for field in ("start_time", "end_time", "start_hour"):
                    if isinstance(row.get(field), str):
                        row[field] = datetime.fromisoformat(row[field]).replace(
                            tzinfo=UTC
                        )
            return SimpleNamespace(data=rows)

    try:
        yield Analytics()
    finally:
        session.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    "variants",
    [
        [("svc-a", "SPAN"), ("svc-b", "SPAN")],
        [("svc-a", "SPAN"), ("svc-a", "TOOL")],
        [("svc-a", "SPAN"), ("svc-b", "SPAN"), ("svc-b", "TOOL")],
    ],
    ids=["service", "observation-type", "service-and-type"],
)
def test_trace_detail_replays_latest_logical_span_across_physical_keys(
    analytics, variants
):
    for version, (service, kind) in enumerate(variants, start=1):
        analytics.insert("root", service=service, kind=kind, version=version)
    analytics.insert("child", version=len(variants) + 1)

    detail = read_trace_detail(
        analytics=analytics,
        project_ids=[PROJECT],
        trace_id="trace",
        include_annotations=False,
        deadline_ms=30_000,
    )

    assert {span["id"] for span in detail.spans} == {"root", "child"}
    by_id = {span["id"]: span for span in detail.spans}
    root, child = by_id["root"], by_id["child"]
    assert root["service_name"] == variants[-1][0]
    assert root["observation_type"] == variants[-1][1]
    assert root["input"] == f"version-{len(variants)}"
    assert root["_version"] == str(len(variants))
    assert root["start_hour"] == START.replace(minute=0)
    assert child["input"] == f"version-{len(variants) + 1}"
