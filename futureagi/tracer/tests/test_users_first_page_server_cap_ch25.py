"""Real-ClickHouse proof: the Users first page's statements carry a server cap.

TH-6934. The first batch of a seeded Users cursor page (the unfiltered tab,
or one filtered only by its date range) is a whole-window latest-state replay
joined through the global end-user survivor map. Before this change it
reached ClickHouse with ``max_execution_time = 0``.

This module drives the real ``UsersListManager`` through the real
``V2AnalyticsQueryService`` (its own native ``ClickHouseClient`` and the
application read policy) against an owned test database carrying the repo's
v2 schema, then reads what the server saw from ``system.query_log``:

* the candidate statement and its survivor statement are logged with
  ``max_execution_time`` = what was left of the request's analytics wall
  (positive, at most the wall, above the refill's eight-second hint);
* when one millisecond of that wall is left, the SERVER stops the candidate
  statement and the page raises a read-budget failure instead of publishing.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import (
    _ch_test_apply_v2_schema,
    _ch_test_native_client,
    _ch_test_native_port,
    _ch_test_owned_database,
)

pytestmark = pytest.mark.integration

PROJECT_ID = "6934a5e1-0000-4000-8000-00000000c001"
ORGANIZATION_ID = "6934a5e1-0000-4000-8000-00000000c0a1"
USERS = 40
SPANS_PER_USER = 5_000
LO = datetime(2026, 10, 1, tzinfo=UTC)
HI = LO + timedelta(days=7)


def _user(number: str) -> str:
    """A deterministic end-user UUID for ``number``, spelled in SQL."""

    return (
        "reinterpretAsUUID(concat(unhex(lpad(hex(" + number + "), 16, '0')),"
        " unhex('0000000000006934')))"
    )


@pytest.fixture(scope="module")
def store():
    span_user = _user(f"number % {USERS}")
    span_start = LO.strftime("%Y-%m-%d %H:%M:%S")
    first_seen = (LO - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    with _ch_test_owned_database("test_users_first_cap_") as database:
        _ch_test_apply_v2_schema(database)
        with _ch_test_native_client(database=database) as client:
            # 200,000 spans over about three days, spread across 40 users.
            client.execute(
                f"""
                INSERT INTO spans (project_id, observation_type, service_name,
                  start_time, trace_id, id, name, end_time, end_user_id, status,
                  model, provider, is_deleted, _version)
                SELECT toUUID('{PROJECT_ID}'), 'llm', 'svc', start, trace, span,
                  'op', start + toIntervalMillisecond(500),
                  {span_user}, 'OK', 'gpt-4o', 'openai', 0, 1
                FROM (
                  SELECT number,
                    toDateTime64('{span_start}', 6, 'UTC')
                      + toIntervalMicrosecond(number * 1200000) AS start,
                    toString(generateUUIDv4(number)) AS trace,
                    concat('s', toString(number)) AS span
                  FROM numbers({USERS * SPANS_PER_USER})
                )
                """
            )
            client.execute(
                f"""
                INSERT INTO end_users (project_id, end_user_id, organization_id,
                  user_id, user_id_type, first_seen)
                SELECT toUUID('{PROJECT_ID}'), {_user("number")},
                  toUUID('{ORGANIZATION_ID}'), concat('user-', toString(number)),
                  'string', toDateTime64('{first_seen}', 6, 'UTC')
                FROM numbers({USERS})
                """
            )
            yield SimpleNamespace(database=database, client=client)


@pytest.fixture()
def manager(store, monkeypatch):
    """The production manager on the real V2 service, bound to the sidecar."""

    from tracer.services.clickhouse.v2 import query_service as v2_query_service
    from tracer.services.users_list_manager import UsersListManager

    monkeypatch.setattr(
        v2_query_service,
        "get_v2_config",
        lambda: {
            "host": os.environ.get("CH25_HOST", "127.0.0.1"),
            "tcp_port": _ch_test_native_port().port,
            "user": os.environ.get("CH25_USER")
            or os.environ.get("CH_USERNAME")
            or "default",
            "password": os.environ.get("CH25_PASSWORD")
            or os.environ.get("CH_PASSWORD")
            or "",
            "database": store.database,
            "server_enforced_readonly": False,
        },
    )
    v2_query_service.reset_v2_query_client()

    def build(hi):
        return UsersListManager(
            organization_id=ORGANIZATION_ID,
            allowed_project_ids=[PROJECT_ID],
            project_id=PROJECT_ID,
            # The Users tab's default request: its date-range preset only.
            filters=[
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [LO.isoformat(), hi.isoformat()],
                    },
                }
            ],
            requested_columns=[],
            attribute_keys=[],
        )

    yield build
    v2_query_service.reset_v2_query_client()


def _statements(store, hi):
    """(kind, exception code, max_execution_time, is_survivor) per statement."""

    marker = str(int(hi.timestamp() * 1_000_000))
    store.client.execute("SYSTEM FLUSH LOGS")
    candidates = store.client.execute(
        "SELECT type, exception_code, Settings['max_execution_time']"
        " FROM system.query_log"
        " WHERE current_database = %(database)s"
        "   AND event_date >= yesterday()"
        "   AND type != 'QueryStart'"
        "   AND position(query, 'eu_survivor_map AS') > 0"
        "   AND position(query, %(marker)s) > 0"
        "   AND position(query, 'system.query_log') = 0"
        " ORDER BY event_time_microseconds",
        {"database": store.database, "marker": marker},
    )
    survivors = store.client.execute(
        "SELECT type, exception_code, Settings['max_execution_time']"
        " FROM system.query_log"
        " WHERE current_database = %(database)s"
        "   AND event_date >= yesterday()"
        "   AND type != 'QueryStart'"
        "   AND position(query, 'toString(survivor_id) AS survivor_id') > 0"
        "   AND position(query, 'system.query_log') = 0"
        " ORDER BY event_time_microseconds",
        {"database": store.database},
    )
    return candidates, survivors


def test_first_page_statements_reach_clickhouse_with_the_analytics_wall_left(
    store, manager
):
    from django.conf import settings

    from tracer.services.users_list_manager import USER_LIST_QUERY_TIMEOUT_MS

    page = manager(HI).list_cursor_payload(page_size=25)

    assert page.payload["query_status"] == "complete", page.payload
    assert page.payload["query_exact"] is True
    assert page.has_more is True
    table = page.payload["table"]
    assert len(table) == 25
    assert len({row["end_user_id"] for row in table}) == 25
    assert {row["user_id"] for row in table} <= {f"user-{i}" for i in range(USERS)}

    candidates, survivors = _statements(store, HI)
    wall_s = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS / 1000
    assert len(candidates) == 1, candidates
    assert len(survivors) == 1, survivors
    for kind, code, cap in candidates + survivors:
        assert (kind, code) == ("QueryFinish", 0)
        # The server saw what was left of the analytics wall, not "no limit"
        # and not the refill's per-statement hint.
        assert 0 < float(cap) <= wall_s, cap
        assert float(cap) > USER_LIST_QUERY_TIMEOUT_MS / 1000, cap


def test_a_first_page_past_its_wall_is_stopped_by_the_server(
    store, manager, monkeypatch
):
    from tracer.services.clickhouse.read_budget import (
        ReadDeadline,
        is_read_budget_error,
    )

    # One millisecond of the wall is left when the statement is sent: the
    # server, not a client clock, has to stop it.
    monkeypatch.setattr(
        ReadDeadline, "remaining_ms", lambda _self, cap_ms=None, *, floor_ms=25: 1
    )
    hi = HI - timedelta(hours=1)
    with pytest.raises(Exception) as stopped:
        manager(hi).list_cursor_payload(page_size=25)

    assert is_read_budget_error(stopped.value), repr(stopped.value)
    candidates, _survivors = _statements(store, hi)
    assert len(candidates) == 1, candidates
    kind, code, cap = candidates[0]
    assert kind in {"ExceptionWhileProcessing", "ExceptionBeforeStart"}, candidates
    # TIMEOUT_EXCEEDED; a cap this small can also surface as QUERY_WAS_CANCELLED.
    assert code in {159, 394}, candidates
    assert float(cap) == pytest.approx(0.001), cap
