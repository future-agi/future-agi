"""Live ClickHouse proof that unwrapping ``end_user_id`` preserves the answer.

The old compilation (``toString(end_user_id) …``) and the new one are run
against the same spans-shaped table and their result sets compared row for
row. The single intended difference — an upper-case literal, which the text
cast could never match because ClickHouse renders a UUID in lower case — is
asserted explicitly rather than tolerated.

The second half asserts the point of the change: ``EXPLAIN indexes = 1``
reports an ``idx_end_user_id`` stage for the new form and none for the old, so
the bloom filter prunes granules the cast made it skip.

This module issues DDL and DML as an admin user, so it opts in explicitly
before it opens a socket: see ``_live_native_port``. Nothing here resolves a
default port, because a developer host's well-known ClickHouse ports are held
by port-forwards to shared clusters.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from clickhouse_driver import Client

from conftest import _require_safe_ch25_test_target
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder

pytestmark = pytest.mark.integration

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_USER = os.environ.get("CH25_USER", "default")
CH_PASSWORD = os.environ.get("CH25_PASSWORD", "")

LIVE_CH_TESTS_ENV_VAR = "FI_LIVE_CH_TESTS"

# Native ports a developer host keeps pointed at shared ClickHouse clusters.
# This suite creates and drops objects, so it refuses them outright rather
# than trusting whoever exported the variable to have meant the test stack.
REFUSED_NATIVE_PORTS = frozenset({19000, 19001, 19002, 19010, *range(18230, 18233)})

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
USER_A = "2a2b2c2d-eeee-4fff-8aaa-bbbbccccdddd"
USER_B = "4a4b4c4d-cccc-4ddd-8eee-ffffaaaabbbb"
USER_OTHER = "3f3e3d3c-bbbb-4aaa-8fff-eeeeddddcccc"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
NOT_A_UUID = "not-a-uuid"

# Enough rows to span many granules so a skipped bloom stage is visible.
FILLER_ROWS = 20_000
GRANULARITY = 128


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


def _ch_client(*, port: int, database: str) -> Client:
    return Client(
        host=CH_HOST,
        port=port,
        user=CH_USER,
        password=CH_PASSWORD,
        database=database,
        connect_timeout=3,
        settings={"optimize_on_insert": 0},
    )


@pytest.fixture(scope="module")
def ch_port() -> int:
    return _live_native_port()


@pytest.fixture(scope="module")
def ch_database(ch_port: int):
    """Create one unique test-owned database and remove it afterwards."""

    database = f"_test_end_user_uuid_{uuid.uuid4().hex}"
    _require_safe_ch25_test_target(host=CH_HOST, database=database)
    admin = _ch_client(port=ch_port, database="default")
    created = False
    try:
        try:
            admin.execute("SELECT 1")
        except Exception as exc:  # pragma: no cover - environment guard
            pytest.skip(f"CH 25.3 not reachable on {CH_HOST}:{ch_port} ({exc!r})")
        admin.execute(f"CREATE DATABASE {database}")
        created = True
        yield database
    finally:
        if created:
            # The target is the unguessable database created just above, so
            # no shared schema object can be reached by this statement.
            admin.execute(f"DROP DATABASE IF EXISTS {database} SYNC")


@pytest.fixture(scope="module")
def ch_client(ch_port: int, ch_database: str):
    return _ch_client(port=ch_port, database=ch_database)


@pytest.fixture(scope="module")
def spans_table(ch_client):
    """A spans-shaped table: the real sorting key and the real skip index.

    ``end_user_id`` is not in the sorting key, so the bloom filter is the only
    thing that can prune on it — which is what the production table looks like.
    """

    table = "spans_shaped"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            project_id       UUID,
            observation_type LowCardinality(String),
            service_name     LowCardinality(String),
            start_time       DateTime64(6, 'UTC'),
            trace_id         UUID,
            id               UUID,
            end_user_id      Nullable(UUID),
            is_deleted       UInt8 DEFAULT 0,
            INDEX idx_end_user_id end_user_id TYPE bloom_filter(0.001) GRANULARITY 1
        )
        ENGINE = MergeTree
        PARTITION BY toDate(start_time)
        ORDER BY (
            project_id, observation_type, service_name,
            toStartOfHour(start_time), trace_id, id
        )
        SETTINGS index_granularity = {GRANULARITY}
        """
    )

    base_us = 1_757_000_000_000_000

    def _row(index: int, end_user_id: str | None) -> dict[str, Any]:
        return {
            "project_id": PROJECT_ID,
            "observation_type": "LLM",
            "service_name": "svc",
            "start_time": base_us + index,
            "trace_id": str(uuid.uuid4()),
            "id": str(uuid.uuid4()),
            "end_user_id": end_user_id,
            "is_deleted": 0,
        }

    # Six rows carry the semantics: a NULL, the matching user twice, a
    # non-matching user, a second matching user for the IN set, and the zero
    # UUID the product uses as an absent-user sentinel.
    rows = [
        _row(0, None),
        _row(1, USER_A),
        _row(2, USER_OTHER),
        _row(3, USER_A),
        _row(4, USER_B),
        _row(5, ZERO_UUID),
    ]
    # Same project, so the primary key prunes nothing and only the bloom can.
    rows += [_row(6 + index, str(uuid.uuid4())) for index in range(FILLER_ROWS)]
    ch_client.execute(
        f"INSERT INTO {table} (project_id, observation_type, service_name, "
        f"start_time, trace_id, id, end_user_id, is_deleted) VALUES",
        rows,
        types_check=True,
    )
    # No teardown of its own: the table lives in the module's scratch
    # database, which ``ch_database`` drops.
    yield table


def _old_condition(filter_op: str, filter_value: Any) -> tuple[str, dict[str, Any]]:
    """The compilation this change replaces, written out verbatim."""

    builder = ClickHouseFilterBuilder()
    param = builder._next_param("col")
    if filter_op == "in":
        values = filter_value if isinstance(filter_value, list) else [filter_value]
        builder._params[param] = tuple(values)
        return f"toString(end_user_id) IN %({param})s", dict(builder._params)
    builder._params[param] = filter_value
    return f"toString(end_user_id) = %({param})s", dict(builder._params)


def _new_condition(filter_op: str, filter_value: Any) -> tuple[str, dict[str, Any]]:
    builder = ClickHouseFilterBuilder()
    sql = builder._build_column_condition(
        "end_user_id", "text", filter_op, filter_value
    )
    return sql, dict(builder._params)


def _matching_users(ch_client, table: str, condition: str, params: dict[str, Any]):
    rows = ch_client.execute(
        f"SELECT toString(end_user_id) FROM {table} "
        f"WHERE project_id = toUUID('{PROJECT_ID}') AND ({condition}) "
        f"ORDER BY start_time",
        params,
    )
    return [row[0] for row in rows]


@pytest.mark.parametrize(
    ("filter_op", "filter_value"),
    [
        ("equals", USER_A),
        ("equals", NOT_A_UUID),
        ("equals", ZERO_UUID),
        ("in", [USER_A, USER_B]),
        ("in", [USER_A, NOT_A_UUID]),
        ("in", [NOT_A_UUID]),
    ],
    ids=[
        "equals-canonical",
        "equals-non-uuid",
        "equals-zero-uuid",
        "in-two-canonical",
        "in-mixed-valid-and-invalid",
        "in-all-invalid",
    ],
)
def test_new_compilation_returns_the_same_rows(
    ch_client, spans_table, filter_op: str, filter_value: Any
) -> None:
    old_sql, old_params = _old_condition(filter_op, filter_value)
    new_sql, new_params = _new_condition(filter_op, filter_value)

    assert "toString(end_user_id)" not in new_sql
    assert _matching_users(ch_client, spans_table, old_sql, old_params) == (
        _matching_users(ch_client, spans_table, new_sql, new_params)
    )


@pytest.mark.parametrize("filter_op", ["equals", "in"])
def test_uppercase_literal_matches_only_under_the_new_compilation(
    ch_client, spans_table, filter_op: str
) -> None:
    """The one deliberate behaviour change, asserted rather than assumed.

    ``toString`` renders a UUID lower case, so an upper-case literal matched
    nothing however many rows the user had.
    """

    literal = [USER_A.upper()] if filter_op == "in" else USER_A.upper()
    old_sql, old_params = _old_condition(filter_op, literal)
    new_sql, new_params = _new_condition(filter_op, literal)

    assert _matching_users(ch_client, spans_table, old_sql, old_params) == []
    assert _matching_users(ch_client, spans_table, new_sql, new_params) == [
        USER_A,
        USER_A,
    ]


def test_null_end_user_id_is_matched_by_neither_compilation(
    ch_client, spans_table
) -> None:
    old_sql, old_params = _old_condition("equals", USER_A)
    new_sql, new_params = _new_condition("equals", USER_A)

    for sql, params in ((old_sql, old_params), (new_sql, new_params)):
        assert None not in _matching_users(ch_client, spans_table, sql, params)


def _index_stages(ch_client, table: str, condition: str, params: dict[str, Any]) -> str:
    plan = ch_client.execute(
        f"EXPLAIN indexes = 1 SELECT id FROM {table} "
        f"WHERE project_id = toUUID('{PROJECT_ID}') AND ({condition})",
        params,
    )
    return "\n".join(row[0] for row in plan)


def _granules_after_bloom(plan: str) -> tuple[int, int]:
    """Return (selected, total) granules reported by the idx_end_user_id stage."""

    lines = [line.strip() for line in plan.splitlines()]
    index = lines.index("Name: idx_end_user_id")
    granules = next(line for line in lines[index:] if line.startswith("Granules: "))
    selected, total = granules.removeprefix("Granules: ").split("/")
    return int(selected), int(total)


@pytest.mark.parametrize(
    ("filter_op", "filter_value"),
    [("equals", USER_A), ("in", [USER_A, USER_B])],
    ids=["equals", "in"],
)
def test_new_compilation_engages_the_bloom_skip_index(
    ch_client, spans_table, filter_op: str, filter_value: Any
) -> None:
    old_sql, old_params = _old_condition(filter_op, filter_value)
    new_sql, new_params = _new_condition(filter_op, filter_value)

    old_plan = _index_stages(ch_client, spans_table, old_sql, old_params)
    new_plan = _index_stages(ch_client, spans_table, new_sql, new_params)

    assert "idx_end_user_id" not in old_plan
    assert "idx_end_user_id" in new_plan

    selected, total = _granules_after_bloom(new_plan)
    assert total > 100, "fixture too small to show pruning"
    # One granule per distinct user, give or take the bloom's false positives.
    assert selected <= 10 < total
