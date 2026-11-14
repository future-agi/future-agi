"""Live CH 25.3 proof that the replay's aggregation strategy is not its answer.

The exact dashboard replay groups both legs by the full sorting key. Under
``optimize_aggregation_in_order = 1`` ClickHouse streams one merged stream
per selected part; under ``= 0`` it hashes. Both are set operations: over a
table with one part per stored version the shipped statement (hash), the same
text with in-order restored, and the engine's own FINAL publish identical
rows. ``EXPLAIN PIPELINE`` shows the switch is real.

This module issues DDL and DML as an admin user, so it opts in explicitly
before it opens a socket, through the same gate as the narrow replay proof.
"""

from __future__ import annotations

import uuid

import pytest
from clickhouse_driver import Client

from tracer.tests.test_dashboard_narrow_replay_ch25 import (
    _SPANS_DDL,
    CH_DATABASE,
    CH_HOST,
    CH_PASSWORD,
    CH_USER,
    COLUMNS,
    POPULATION,
    _config,
    _live_native_port,
    _render,
)

pytestmark = pytest.mark.integration

SHIPPED_TAIL = "optimize_aggregation_in_order = 0"
RESTORED_TAIL = "optimize_aggregation_in_order = 1"
IN_ORDER_PROCESSORS = (
    "AggregatingInOrderTransform",
    "FinishAggregatingInOrderTransform",
)


@pytest.fixture(scope="module")
def ch_client():
    client = Client(
        host=CH_HOST,
        port=_live_native_port(),
        user=CH_USER,
        password=CH_PASSWORD,
        database=CH_DATABASE,
        connect_timeout=3,
        settings={"optimize_on_insert": 0},
    )
    try:
        client.execute("SELECT 1")
    except Exception as exc:  # pragma: no cover - environment probe
        pytest.skip(f"CH 25.3 not reachable ({exc!r})")
    return client


@pytest.fixture()
def many_parts_table(ch_client):
    """Every stored version in its own part: merges stopped, one INSERT each."""

    table = f"_test_dashboard_replay_read_settings_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} ({_SPANS_DDL})
        ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        PRIMARY KEY (
            project_id, observation_type, service_name, toStartOfHour(start_time)
        )
        ORDER BY (
            project_id, observation_type, service_name,
            toStartOfHour(start_time), trace_id, id
        )
        SETTINGS allow_nullable_key = 1
        """
    )
    ch_client.execute(f"SYSTEM STOP MERGES {table}")
    rows = [row for versions, _ in POPULATION for row in versions]
    for row in rows:
        ch_client.execute(f"INSERT INTO {table} ({', '.join(COLUMNS)}) VALUES", [row])
    parts = ch_client.execute(
        "SELECT count() FROM system.parts WHERE table = %(table)s AND active",
        {"table": table},
    )[0][0]
    assert parts == len(rows), "the fixture must hold one active part per version"
    try:
        yield table
    finally:
        ch_client.execute(f"DROP TABLE {table}")


def _strategies(config, table):
    """The shipped replay text and the same text with in-order restored."""

    shipped = _render(config, table, final_twin=False)
    head, separator, tail = shipped.rpartition("\nSETTINGS ")
    assert separator and tail.strip().endswith(SHIPPED_TAIL), (
        "the shipped replay statement must state hash aggregation"
    )
    restored = head + separator + tail.replace(SHIPPED_TAIL, RESTORED_TAIL)
    assert restored != shipped
    return shipped, restored


def _pipeline(ch_client, sql):
    return "\n".join(row[0] for row in ch_client.execute("EXPLAIN PIPELINE " + sql))


@pytest.mark.parametrize("granularity", ("day", "month"))
def test_both_strategies_publish_identical_rows_over_many_parts(
    ch_client, many_parts_table, granularity
):
    config = _config(granularity, None)
    shipped, restored = _strategies(config, many_parts_table)

    hashed = ch_client.execute(shipped)
    streamed = ch_client.execute(restored)
    finalized = ch_client.execute(_render(config, many_parts_table, final_twin=True))

    assert sorted(map(repr, hashed)) == sorted(map(repr, streamed))
    assert sorted(map(repr, hashed)) == sorted(map(repr, finalized))
    survivors = [latency for _, latency in POPULATION if latency is not None]
    assert len(hashed) == 1
    assert hashed[0][-1] == pytest.approx(sum(survivors) / len(survivors))


def test_shipped_text_hashes_and_restored_text_streams(ch_client, many_parts_table):
    """The setting is load-bearing: the pipeline changes shape with it."""

    shipped, restored = _strategies(_config("day", None), many_parts_table)

    hashed_plan = _pipeline(ch_client, shipped)
    streamed_plan = _pipeline(ch_client, restored)

    for processor in IN_ORDER_PROCESSORS:
        assert processor not in hashed_plan
        assert processor in streamed_plan
    assert "AggregatingTransform" in hashed_plan
