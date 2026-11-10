"""The pre-existing point reader must expose a partition-prunable hour range."""

from datetime import UTC, datetime

from tracer.services.clickhouse.v2.physical_span_detail_reads import (
    build_physical_span_detail_query,
)


def test_physical_tag_precondition_has_explicit_project_and_full_hour_bounds():
    sql, params = build_physical_span_detail_query(
        span_id="0123456789abcdef",
        reference={
            "project_id": "11111111-1111-1111-1111-111111111111",
            "trace_id": "22222222-2222-2222-2222-222222222222",
            "start_hour": datetime(2020, 1, 1, tzinfo=UTC),
            "service_name": "'quoted-service'",
            "observation_type": "span",
        },
    )
    scope = sql.split("PREWHERE", 1)[1]
    assert "project_id = %(physical_project_id)s" in scope
    assert (
        "start_time >= fromUnixTimestamp64Micro(%(physical_hour_us)s, 'UTC')" in scope
    )
    assert (
        "start_time < fromUnixTimestamp64Micro(%(physical_hour_us)s, 'UTC') + INTERVAL 1 HOUR"
        in scope
    )
    assert "is_deleted" not in scope and "parent_span_id" not in scope
    assert "expected" not in scope and "_version" not in scope
    assert "quoted-service" not in sql
    assert params["physical_service_name"] == "'quoted-service'"
    assert params["physical_hour_us"] == 1577836800000000
