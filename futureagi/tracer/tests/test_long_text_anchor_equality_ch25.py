"""Live proof that the size limits change acquisition and never membership.

The bounded n-gram anchor is the one hint this change still emits, and
``indexHint`` lets ClickHouse drop whole granules on the strength of it. An
anchor that were not a necessary condition would therefore lose rows silently,
which no rendered-SQL assertion can catch. These read the same page twice
against a real ClickHouse, once with the anchor and once with every hint
removed, and require the two answers to be identical.

This module issues DDL and DML as an admin user, so it opts in explicitly
before it opens a socket: see ``_live_native_port``. Nothing here resolves a
default port, because a developer host's well-known ClickHouse ports are held
by port-forwards to shared clusters.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from clickhouse_driver import Client

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.query_builders import latest_filter_predicates
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders import trace_list as trace_list_module
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

pytestmark = pytest.mark.integration

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_USER = os.environ.get("CH25_USER", "default")
CH_PASSWORD = os.environ.get("CH25_PASSWORD", "")

LIVE_CH_TESTS_ENV_VAR = "FI_LIVE_CH_TESTS"

# Native ports a developer host keeps pointed at shared ClickHouse clusters.
# This suite creates and drops objects, so it refuses them outright rather
# than trusting whoever exported the variable to have meant the test stack.
REFUSED_NATIVE_PORTS = frozenset({19000, 19001, 19002, 19010, *range(18230, 18233)})

PROJECT_ID = "00000000-0000-4000-8000-0000000000a1"
KEY = "raw_log"
WINDOW_END = datetime(2025, 1, 1, 12, 0)
WINDOW_START = WINDOW_END - timedelta(hours=6)

# Past the index-companion budget and past the anchor budget, so the companions
# stand down and the anchor is bounded. One value is a single unbroken run, so
# the anchor keeps a prefix; the other carries an i in every unit, so it splits
# into many runs and the anchor must choose among them.
_ONE_RUN_UNIT = "a common message 000123 another response. "
_MANY_RUN_UNIT = "an indexed message 000123 with a distinct reply. "
ONE_RUN_VALUE = _ONE_RUN_UNIT * 500
MANY_RUN_VALUE = _MANY_RUN_UNIT * 440
DECOY_VALUE = ("a decoy message 987654 unrelated wording. ") * 500


def _micros(value: datetime) -> int:
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    delta = moment.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _live_native_port() -> int:
    """Return the opted-into native port, or skip before any socket is opened.

    There is deliberately no default and no fallback chain onto a sibling
    variable. An unpinned run must not resolve to whatever happens to be
    listening on a well-known port: on a developer host those are held by
    port-forwards to shared clusters, and this suite runs ``CREATE``/``INSERT``
    as an admin user. The caller names the disposable stack, or gets a skip.
    """

    if os.environ.get(LIVE_CH_TESTS_ENV_VAR) != "1":
        pytest.skip(f"live ClickHouse tests are opt-in: set {LIVE_CH_TESTS_ENV_VAR}=1")

    raw_port = os.environ.get("CH25_NATIVE_PORT", "").strip()
    if not raw_port:
        pytest.skip(
            "CH25_NATIVE_PORT is not set; this suite will not guess a "
            "ClickHouse port for a test that writes"
        )
    try:
        port = int(raw_port)
    except ValueError:
        pytest.skip(f"CH25_NATIVE_PORT={raw_port!r} is not a port number")

    if port in REFUSED_NATIVE_PORTS:
        pytest.skip(
            f"refusing to write to ClickHouse on port {port}: that port is "
            "reserved for port-forwards to shared clusters on this host"
        )
    return port


@pytest.fixture(scope="module")
def ch_port() -> int:
    return _live_native_port()


@pytest.fixture(scope="module")
def ch_client(ch_port: int):
    client = Client(
        host=CH_HOST,
        port=ch_port,
        user=CH_USER,
        password=CH_PASSWORD,
        connect_timeout=3,
        settings={"optimize_on_insert": 0},
    )
    try:
        client.execute("SELECT 1")
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"CH 25.3 not reachable on {CH_HOST}:{ch_port} ({exc!r})")
    return client


@pytest.fixture()
def anchor_span_table(ch_client):
    """A spans-shaped table carrying the deployed attribute skip indexes.

    ``index_granularity`` is tiny so the rows span many granules and the skip
    indexes can actually drop some; at the default granularity the whole table
    is one granule and an unsound hint would go unnoticed.
    """

    table = f"_test_long_text_anchor_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            id String,
            project_id UUID,
            trace_id String,
            parent_span_id Nullable(String),
            trace_name String,
            trace_session_id Nullable(String),
            project_version_id Nullable(UUID),
            name String,
            service_name String DEFAULT '',
            observation_type String DEFAULT 'span',
            status Nullable(String),
            start_time DateTime64(6, 'UTC'),
            end_time Nullable(DateTime64(6, 'UTC')),
            latency_ms Nullable(Float64),
            cost Nullable(Float64),
            total_tokens Nullable(Int64),
            prompt_tokens Nullable(Int64),
            completion_tokens Nullable(Int64),
            model Nullable(String),
            provider Nullable(String),
            end_user_id Nullable(UUID),
            created_at DateTime64(6, 'UTC'),
            is_deleted UInt8,
            _version UInt64,
            attrs_string Map(String, String),
            attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8),
            attributes_extra String,
            INDEX idx_attrs_str_keys mapKeys(attrs_string)
                TYPE bloom_filter(0.01) GRANULARITY 1,
            INDEX idx_attrs_str_values arrayMap(x -> lower(x), mapValues(attrs_string))
                TYPE bloom_filter(0.01) GRANULARITY 1,
            INDEX idx_attrs_str_ngram
                arrayStringConcat(arrayMap(x -> lower(x), mapValues(attrs_string)))
                TYPE ngrambf_v1(4, 32768, 3, 0) GRANULARITY 1
        )
        ENGINE = ReplacingMergeTree(_version, is_deleted)
        ORDER BY (
            project_id,
            observation_type,
            service_name,
            toStartOfHour(start_time),
            trace_id,
            id
        )
        SETTINGS index_granularity = 4
        """
    )
    ch_client.execute(f"SYSTEM STOP MERGES {table}")
    try:
        yield table
    finally:
        ch_client.execute(f"DROP TABLE {table}")


def _row(index: int, value: str) -> dict:
    moment = WINDOW_START + timedelta(minutes=index)
    return {
        "id": f"span-{index:03d}",
        "project_id": uuid.UUID(PROJECT_ID),
        "trace_id": f"trace-{index:03d}",
        "parent_span_id": "",
        "trace_name": "root",
        "trace_session_id": None,
        "project_version_id": None,
        "name": "root",
        "service_name": "svc",
        "observation_type": "span",
        "status": "ok",
        "start_time": moment,
        "end_time": moment,
        "latency_ms": 1.0,
        "cost": None,
        "total_tokens": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "model": None,
        "provider": None,
        "end_user_id": None,
        "created_at": moment,
        "is_deleted": 0,
        "_version": 1,
        "attrs_string": {KEY: value},
        "attrs_number": {},
        "attrs_bool": {},
        "attributes_extra": "",
    }


def _page(ch_client, table, value, statements):
    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = table

    class LocalAnalytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            statements.append(query)
            data, schema = ch_client.execute(
                query,
                params,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 5.0),
                },
                with_column_types=True,
            )
            names = [name for name, _type in schema]
            mapped = [dict(zip(names, values, strict=True)) for values in data]
            return QueryResult(
                data=mapped,
                row_count=len(mapped),
                backend_used="clickhouse",
                query_time_ms=0.0,
            )

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
            },
        },
        {
            "column_id": KEY,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": value,
            },
        },
    ]
    return read_bounded_filter_page(
        builder=LocalTraceBuilder(project_id=PROJECT_ID, filters=filters),
        analytics=LocalAnalytics(),
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=20_000,
    )


@pytest.mark.parametrize(
    "value", [ONE_RUN_VALUE, MANY_RUN_VALUE], ids=["one-run", "many-runs"]
)
def test_bounded_anchor_returns_the_same_rows_as_no_hint_at_all(
    ch_client, anchor_span_table, monkeypatch, value
):
    """Acquisition may change; the page may not."""

    matching = [0, 3, 7, 11, 19]
    rows = [_row(index, value) for index in matching]
    rows += [_row(index, DECOY_VALUE) for index in range(30, 90)]
    ch_client.execute(f"INSERT INTO {anchor_span_table} VALUES", rows)

    hinted_statements: list[str] = []
    hinted = _page(ch_client, anchor_span_table, value, hinted_statements)
    # Guard against a vacuous pass: the anchored lane must really have run.
    assert any("indexHint(arrayStringConcat" in sql for sql in hinted_statements)

    monkeypatch.setattr(trace_list_module, "_LONG_TEXT_SEED_INLINE_BUDGET_BYTES", 0)
    monkeypatch.setattr(
        latest_filter_predicates, "_MAX_INDEX_COMPANION_VALUE_UTF8_BYTES", 0
    )
    plain_statements: list[str] = []
    plain = _page(ch_client, anchor_span_table, value, plain_statements)
    assert not any("indexHint(arrayStringConcat" in sql for sql in plain_statements)

    hinted_ids = sorted(item["trace_id"] for item in hinted.rows)
    plain_ids = sorted(item["trace_id"] for item in plain.rows)
    assert hinted_ids == sorted(f"trace-{index:03d}" for index in matching)
    assert hinted_ids == plain_ids
    assert hinted.complete == plain.complete
