# ruff: noqa: F811
"""Execute real list classifiers on the existing isolated CH25 test engine.

No service credentials or shared ClickHouse are required: the reused fixture
requires a nominated loopback test instance or an isolated Docker container.
"""

from datetime import timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_input_output_search_contract import search_filter
from tracer.tests.test_input_output_search_plans import PROJECT, START, window
from tracer.tests.test_span_latest_state_engine_ch25 import bind, engine  # noqa: F401

pytestmark = pytest.mark.integration
NEEDLE = "É_%'\\needle"


def insert(engine, span_id, trace_id, **overrides):
    row = {
        "project_id": PROJECT,
        "observation_type": "span",
        "service_name": "search-test",
        "start_time": START + timedelta(minutes=5),
        "trace_id": trace_id,
        "id": span_id,
        "parent_span_id": "",
        "input": "",
        "output": "",
        "is_deleted": 0,
        "_version": 1,
        **overrides,
    }
    # Test data only; use the same explicit driver-style binding as the engine suite.
    columns = ", ".join(row)
    values = ", ".join(f"%({column})s" for column in row)
    engine.execute(bind(f"INSERT INTO spans ({columns}) VALUES ({values})", row))


@pytest.fixture(scope="module")
def content_population(engine):
    insert(engine, "io-root", "io-trace")
    insert(
        engine,
        "io-input",
        "io-trace",
        parent_span_id="io-root",
        input="x" * 10000 + NEEDLE.lower(),
    )
    insert(engine, "io-output", "io-trace", parent_span_id="io-root", output=NEEDLE)
    insert(engine, "io-stale", "io-stale-trace", input=NEEDLE)
    insert(engine, "io-stale", "io-stale-trace", input="replaced", _version=2)
    insert(engine, "io-deleted", "io-deleted-trace", output=NEEDLE)
    insert(
        engine,
        "io-deleted",
        "io-deleted-trace",
        output=NEEDLE,
        is_deleted=1,
        _version=2,
    )
    insert(engine, "io-split", "io-split-trace", input=NEEDLE[:4], output=NEEDLE[4:])
    insert(
        engine,
        "io-outside",
        "io-outside-trace",
        input=NEEDLE,
        start_time=START - timedelta(days=2),
    )
    insert(
        engine,
        "io-foreign",
        "io-foreign-trace",
        input=NEEDLE,
        project_id="22222222-2222-2222-2222-222222222222",
    )
    # Same logical id on another physical service key must not erase a live match.
    insert(
        engine,
        "io-input",
        "io-trace",
        parent_span_id="io-root",
        input="other service",
        service_name="search-other",
        _version=9,
    )
    return engine


def result_rows(engine, statement):
    sql, params = statement
    return [
        line.split("\t")
        for line in engine.execute(bind(sql, params)).splitlines()
        if line
    ]


def test_span_search_is_local_full_length_latest_live_and_scoped(content_population):
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[window(), search_filter(NEEDLE)],
        bounded_internal_scan=True,
    )
    rows = result_rows(
        content_population,
        builder.build_filter_match_query(
            [
                "io-root",
                "io-input",
                "io-output",
                "io-stale",
                "io-deleted",
                "io-split",
                "io-outside",
                "io-foreign",
            ]
        ),
    )
    matched = {
        span_id
        for row in rows
        for span_id in (
            "io-root",
            "io-input",
            "io-output",
            "io-stale",
            "io-deleted",
            "io-split",
            "io-outside",
            "io-foreign",
        )
        if span_id in row
    }
    assert matched == {"io-input", "io-output"}
    assert len(rows) == 2


def test_trace_search_accepts_child_match_once_and_rejects_stale_deleted_and_foreign(
    content_population,
):
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=[window(), search_filter(NEEDLE)],
        bounded_identity_only=True,
    )
    rows = result_rows(
        content_population,
        builder.build_filter_match_query(
            [
                "io-trace",
                "io-stale-trace",
                "io-deleted-trace",
                "io-split-trace",
                "io-outside-trace",
                "io-foreign-trace",
            ]
        ),
    )
    assert len(rows) == 1
    assert rows[0][0] == "io-trace"
