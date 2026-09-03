import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallListQueryBuilder,
)

PROJECT_ID = "00000000-0000-0000-0000-000000000001"
START = datetime(2025, 1, 1)


def _attribute_filter(operation: str) -> dict:
    return {
        "column_id": "coupon",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": None,
        },
    }


def _surface_query(surface: str, operation: str) -> str:
    builder_cls = {
        "trace": TraceListQueryBuilder,
        "voice": VoiceCallListQueryBuilder,
        "session": SessionListQueryBuilder,
        "span": SpanListQueryBuilder,
    }[surface]
    query, _ = builder_cls(
        project_id=PROJECT_ID,
        filters=[_attribute_filter(operation)],
    ).build()
    return query


def _bounded_surface_query(surface: str, operation: str) -> str:
    builder_cls = {
        "trace": TraceListQueryBuilder,
        "voice": VoiceCallListQueryBuilder,
        "session": SessionListQueryBuilder,
        "span": SpanListQueryBuilder,
    }[surface]
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    START.isoformat(),
                    (START + timedelta(days=1)).isoformat(),
                ],
            },
        },
        _attribute_filter(operation),
    ]
    candidate_id = (
        "00000000-0000-4000-8000-000000000010"
        if surface == "session"
        else f"{surface}-candidate"
    )
    query, _ = builder_cls(
        project_id=PROJECT_ID,
        filters=filters,
    ).build_filter_match_query([candidate_id])
    return query


@pytest.mark.parametrize("surface", ["trace", "voice", "session"])
def test_entity_surfaces_compile_attribute_null_as_trace_anti_membership(surface):
    query = _surface_query(surface, "is_null")

    assert "trace_id NOT IN (SELECT trace_id FROM (" in query
    assert "argMax(toUInt8(mapContains(span_attr_str, 'coupon'))" in query
    assert "AS latest_attribute_match" in query
    assert "GROUP BY project_id, trace_id, id, start_time" in query
    assert "WHERE latest_is_deleted = 0" in query
    assert "NOT mapContains(span_attr_str, 'coupon')" not in query


@pytest.mark.parametrize("surface", ["trace", "voice", "session"])
def test_entity_surfaces_compile_attribute_not_null_as_trace_membership(surface):
    query = _surface_query(surface, "is_not_null")

    assert "trace_id IN (SELECT trace_id FROM (" in query
    assert "argMax(toUInt8(mapContains(span_attr_str, 'coupon'))" in query
    assert "AS latest_attribute_match" in query
    assert "GROUP BY project_id, trace_id, id, start_time" in query
    assert "WHERE latest_is_deleted = 0" in query
    assert "trace_id NOT IN (SELECT trace_id FROM spans" not in query


@pytest.mark.parametrize(
    ("operation", "predicate"),
    [
        ("is_null", "NOT mapContains(span_attr_str, 'coupon')"),
        ("is_not_null", "mapContains(span_attr_str, 'coupon')"),
    ],
)
def test_span_surface_keeps_row_level_attribute_nullness(operation, predicate):
    query = _surface_query("span", operation)

    assert predicate in query
    assert "trace_id NOT IN (SELECT trace_id FROM spans" not in query


@pytest.mark.parametrize(
    ("surface", "operation", "expected", "forbidden"),
    [
        (
            "trace",
            "is_null",
            "countIf(latest_attr_exists_0) = 0",
            "countIf(latest_attr_exists_0) > 0",
        ),
        (
            "trace",
            "is_not_null",
            "countIf(latest_attr_exists_0) > 0",
            "countIf(latest_attr_exists_0) = 0",
        ),
        (
            "voice",
            "is_null",
            "countIf(latest_attr_exists_0) = 0",
            "countIf(latest_attr_exists_0) > 0",
        ),
        (
            "voice",
            "is_not_null",
            "countIf(latest_attr_exists_0) > 0",
            "countIf(latest_attr_exists_0) = 0",
        ),
        (
            "session",
            "is_null",
            "countIf(latest_attr_exists_0) = 0",
            "countIf(latest_attr_exists_0) > 0",
        ),
        (
            "session",
            "is_not_null",
            "countIf(latest_attr_exists_0) > 0",
            "countIf(latest_attr_exists_0) = 0",
        ),
        (
            "span",
            "is_null",
            "AND NOT latest_attr_exists_0",
            "countIf(latest_attr_exists_0) = 0",
        ),
        (
            "span",
            "is_not_null",
            "AND latest_attr_exists_0",
            "countIf(latest_attr_exists_0) > 0",
        ),
    ],
)
def test_bounded_surfaces_apply_attribute_nullness_at_target_grain(
    surface,
    operation,
    expected,
    forbidden,
):
    query = _bounded_surface_query(surface, operation)

    assert expected in query
    assert forbidden not in query
    if surface == "session":
        assert "GROUP BY project_id, session_id, trace_id" in query
        assert "FROM matching_scalar_traces" in query


def _local_ch25_client():
    host = os.environ.get("CH25_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.skip("physical-version proof is restricted to local ClickHouse")
    try:
        from clickhouse_driver import Client

        client = Client(
            host=host,
            port=int(os.environ.get("CH25_NATIVE_PORT", "29010")),
            user=os.environ.get("CH25_USER", "default"),
            password=os.environ.get("CH25_PASSWORD", ""),
            database="default",
            connect_timeout=2,
            send_receive_timeout=10,
        )
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse is unavailable: {exc!r}")
    return client


@pytest.mark.integration
def test_trace_attribute_nullness_replays_versions_and_tombstones():
    from tracer.services.clickhouse.v2.query_builders.filters import (
        ClickHouseFilterBuilderV2,
    )

    client = _local_ch25_client()
    database = f"test_span_attr_null_rmt_{uuid4().hex}"
    project_id = str(uuid4())
    started_at = datetime(2026, 8, 25, 12)
    traces = {
        "removed": (1, 0),
        "tombstoned": (1, 0),
        "added": (0, 1),
    }

    try:
        client.execute(f"CREATE DATABASE {database}")
        client.execute(f"USE {database}")
        client.execute(
            """
            CREATE TABLE spans
            (
                project_id UUID,
                start_time DateTime64(6, 'UTC'),
                trace_id String,
                id String,
                attrs_string Map(String, String),
                is_deleted UInt8,
                _version UInt64
            )
            ENGINE = ReplacingMergeTree(_version, is_deleted)
            ORDER BY (project_id, trace_id, id, start_time)
            """
        )
        client.execute("SYSTEM STOP MERGES spans")
        initial_rows = [
            # A sibling keeps every target trace live while the mutable child
            # exercises key removal, key addition, and a latest tombstone.
            *[
                (project_id, started_at, trace_id, "sibling", {}, 0, 1)
                for trace_id in traces
            ],
            (project_id, started_at, "removed", "child", {"coupon": "yes"}, 0, 1),
            (
                project_id,
                started_at,
                "tombstoned",
                "child",
                {"coupon": "yes"},
                0,
                1,
            ),
            (project_id, started_at, "added", "child", {}, 0, 1),
        ]
        latest_rows = [
            (project_id, started_at, "removed", "child", {}, 0, 2),
            (
                project_id,
                started_at,
                "tombstoned",
                "child",
                {"coupon": "yes"},
                1,
                2,
            ),
            (project_id, started_at, "added", "child", {"coupon": "yes"}, 0, 2),
        ]
        # Separate blocks are intentional: ReplacingMergeTree may collapse
        # duplicate sorting keys inside one insert block even while merges are
        # stopped. Two blocks preserve both physical versions for this proof.
        client.execute("INSERT INTO spans VALUES", initial_rows)
        client.execute("INSERT INTO spans VALUES", latest_rows)
        assert client.execute("SELECT count() FROM spans") == [(9,)]

        builder = ClickHouseFilterBuilderV2(
            query_mode="trace",
            project_id=project_id,
        )
        for operation, expected_index in (("is_null", 0), ("is_not_null", 1)):
            predicate, params = builder.translate([_attribute_filter(operation)])
            assert "argMax(is_deleted, _version)" in predicate
            assert "argMax(toUInt8(mapContains(attrs_string, 'coupon')), _version)" in (
                predicate
            )
            assert "GROUP BY project_id, trace_id, id, start_time" in predicate
            for trace_id, expected in traces.items():
                result = client.execute(
                    f"""
                    SELECT uniqExact(trace_id)
                    FROM spans
                    WHERE project_id = %(project_id)s
                      AND trace_id = %(trace_id)s
                      AND is_deleted = 0
                      AND {predicate}
                    """,
                    {
                        **params,
                        "project_id": project_id,
                        "trace_id": trace_id,
                    },
                )[0][0]
                assert result == expected[expected_index]
    finally:
        client.execute("USE default")
        client.execute(f"DROP DATABASE IF EXISTS {database}")
