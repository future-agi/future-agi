"""Live ClickHouse 25 on the DEPLOYED schema: the Users walk's two cost fixes.

Runs on the lane database provisioned with the repo's own DDL
(``provision-lane-ch-db.sh``, named by ``CH25_DATABASE``), under the same gate
as the other ``*_real_schema_ch25.py`` modules. Every statement goes through an
executor that applies the application's read settings, sends
``server_execution_cap_ms`` as ``max_execution_time`` exactly as
``AnalyticsQueryService`` does, and tags each statement with a query id of its
own, so ``system.query_log`` proves what the PRODUCT sent, not a harness clamp.

* R: raw values on every span (``env = production``, and ``region``,
  ``tier``) and a rare ``status = ERROR``. The first page costs the native
  witness first, then the raw ones, with one ``EXPLAIN ESTIMATE`` each (no
  column rows read, each capped on the server) and walks the error leaf -
  also when the raw estimate is stopped at its cap, and when three raw leaves
  outrank it; the pages publish exactly the users graph's members, once,
  newest error first.
* P: every eligible witness keeps the walk exact. Page 1 is forced onto each
  eligible witness of four filter combinations; every later page, with the
  filters reversed, continues on the cursor's witness without costing any.
  The pages equal the users graph's membership, once, in the walked leaf's
  (newest latest-live match, id) order, with stale versions and deletions in
  the data.
* U: a scope with no end user over twelve months, the tail estimate refused
  (forced below it, as production refused MUD's 408 marks). Three statements
  - the first slice, the estimate, the witness-free presence statement - and
  an empty, complete page; the tail estimate is capped at half the probe
  wall, and the presence statement, capped at no less than the other half,
  reads next to nothing.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services import users_matching_walk as walk
from tracer.services.clickhouse import exact_graph_reads
from tracer.services.clickhouse.application_read_policy import (
    application_read_context,
    application_read_settings,
)
from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import UsersListManager
from tracer.tests._users_live_ch import lane_client

pytestmark = pytest.mark.integration

ORGANIZATION = str(uuid.UUID(int=373))
R = str(uuid.uuid4())
U = str(uuid.uuid4())
USERS = 2_000
# R: 400,000 spans over two days, one error in 4,000 spans.
R_START = datetime(2026, 8, 1, tzinfo=UTC)
R_END = R_START + timedelta(days=2)
# U: 300,000 user-less spans over twelve months.
U_END = datetime(2026, 9, 1, tzinfo=UTC)
U_START = U_END - timedelta(days=365)
SERVICE = "tracer.services.users_list_manager.V2AnalyticsQueryService"
TAG = f"r5live-{uuid.uuid4().hex[:10]}"


@pytest.fixture(scope="module")
def ch_client():
    with lane_client() as client:
        yield client


def _user(n: str) -> str:
    return f"reinterpretAsUUID(concat(unhex(lpad(hex({n}), 16, '0')), unhex('00000000000000a5')))"


@pytest.fixture(scope="module")
def worlds(ch_client):
    spans = """
    INSERT INTO spans (project_id, observation_type, service_name, start_time,
      trace_id, id, name, end_time, end_user_id, status, model, provider,
      attrs_string, is_deleted, _version)
    """
    try:
        ch_client.execute(
            f"""{spans}
            SELECT toUUID('{R}'), 'llm', 'svc',
              toDateTime64('2026-08-01 00:00:00', 6, 'UTC')
                + toIntervalMicrosecond(number * 432000),
              toString(generateUUIDv4(number)), concat('r', toString(number)), 'op',
              NULL, {_user("number % " + str(USERS))},
              if(cityHash64(number, 'err') % 4000 = 0, 'ERROR', 'OK'),
              'gpt-4o', 'openai',
              map('env', 'production', 'region', 'us', 'tier', 'std'), 0, 1
            FROM numbers(400000)
            """
        )
        ch_client.execute(
            f"""{spans}
            SELECT toUUID('{U}'), 'llm', 'svc',
              toDateTime64('2025-09-01 00:00:00', 6, 'UTC')
                + toIntervalMicrosecond(intDiv(number * 31536000000000, 300000)),
              toString(generateUUIDv4(number)), concat('u', toString(number)), 'op',
              NULL, NULL,
              if(cityHash64(number, 'err') % 2000 = 0, 'ERROR', 'OK'),
              'gpt-4o', 'openai', map('env', 'production'), 0, 1
            FROM numbers(300000)
            """,
            # A year of daily partitions in one block.
            settings={"max_partitions_per_insert_block": 1_000},
        )
        ch_client.execute(
            f"""
            INSERT INTO end_users (project_id, end_user_id, organization_id,
              user_id, user_id_type, first_seen)
            SELECT toUUID('{R}'), {_user("number")}, toUUID('{ORGANIZATION}'),
              concat('user-', toString(number)), 'string',
              toDateTime64('2026-07-01 00:00:00', 6, 'UTC')
            FROM numbers({USERS})
            """
        )
        yield
    finally:
        for table in ("spans", "end_users"):
            ch_client.execute(
                f"ALTER TABLE {table} DELETE WHERE project_id IN "
                "(toUUID(%(r)s), toUUID(%(u)s)) SETTINGS mutations_sync = 2",
                {"r": R, "u": U},
            )


class _TaggedExecutor:
    """What ``AnalyticsQueryService.execute_ch_query`` sends, tagged per statement."""

    def __init__(self, client, case: str):
        self.client = client
        self.prefix = f"{TAG}-{case}-{uuid.uuid4().hex[:6]}"
        self.statements: list[tuple[str, str, int | None]] = []
        self.stop_raw_estimates = False

    def execute_ch_query(
        self,
        query,
        params=None,
        timeout_ms=None,
        settings=None,
        *,
        server_execution_cap_ms=None,
    ):
        query_id = f"{self.prefix}-{len(self.statements)}"
        self.statements.append((query, query_id, server_execution_cap_ms))
        if (
            self.stop_raw_estimates
            and query.lstrip().startswith("EXPLAIN ESTIMATE")
            and "attrs_string" in query
        ):
            # A raw witness estimate on a cold bloom index, stopped at its
            # cap: what the service raises on TIMEOUT_EXCEEDED.
            raise ReadDeadlineExceeded("stopped at its cap")
        with application_read_context(execution_cap_ms=server_execution_cap_ms):
            sent = application_read_settings(settings)
        started = time.monotonic()
        try:
            rows, columns = self.client.execute(
                query,
                params or {},
                with_column_types=True,
                settings=sent,
                query_id=query_id,
            )
        except Exception as exc:
            if server_execution_cap_ms is not None and getattr(exc, "code", 0) == 159:
                raise ReadDeadlineExceeded("stopped at its cap") from exc
            raise
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows],
            columns=names,
            query_time_ms=(time.monotonic() - started) * 1000.0,
        )

    def kinds(self) -> list[str]:
        def kind(query: str) -> str:
            if query.lstrip().startswith("EXPLAIN ESTIMATE"):
                return "estimate"
            if "AS present" in query:
                return "presence"
            if "AS witnessed" in query:
                return "existence"
            if "AS raw_end_user_id" in query:
                return "slice"
            return "other"

        return [kind(query) for query, _id, _cap in self.statements]

    def logged(self, client) -> dict[str, tuple[int, int, str | None]]:
        """Per query id: read rows, read bytes and the max_execution_time sent."""

        client.execute("SYSTEM FLUSH LOGS")
        rows = client.execute(
            "SELECT query_id, read_rows, read_bytes, "
            "Settings['max_execution_time'] FROM system.query_log "
            "WHERE type = 'QueryFinish' AND query_id LIKE %(prefix)s "
            "AND event_date >= today() - 1",
            {"prefix": self.prefix + "-%"},
        )
        return {
            query_id: (read_rows, read_bytes, cap or None)
            for query_id, read_rows, read_bytes, cap in rows
        }


def _date(start: datetime, end: datetime) -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def _leaf(column: str, value: str, col_type: str) -> dict:
    return {
        "column_id": column,
        "filter_config": {
            "col_type": col_type,
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


STATUS_ERROR = _leaf("status", "ERROR", "SYSTEM_METRIC")
ENV_PRODUCTION = _leaf("env", "production", "SPAN_ATTRIBUTE")


def _page(client, project, filters, *, case, cursor=None, page_size=25, stop_raw=False):
    manager = UsersListManager(
        organization_id=ORGANIZATION,
        allowed_project_ids=[project],
        project_id=project,
        filters=filters,
        requested_columns=[],
        attribute_keys=[],
    )
    executor = _TaggedExecutor(client, case)
    executor.stop_raw_estimates = stop_raw
    with patch(SERVICE, return_value=executor):
        read = manager.list_cursor_payload(page_size=page_size, cursor=cursor)
    return read, executor, manager


REGION_US = _leaf("region", "us", "SPAN_ATTRIBUTE")
TIER_STD = _leaf("tier", "std", "SPAN_ATTRIBUTE")


@pytest.mark.parametrize(
    ("raw_leaves", "stop_raw"),
    [
        ([ENV_PRODUCTION], False),
        # The raw estimate cannot answer (stopped at its cap): the answered
        # native leaf is walked, never the static raw-first choice.
        ([ENV_PRODUCTION], True),
        # Three raw values on every span: the native leaf is still costed.
        ([ENV_PRODUCTION, REGION_US, TIER_STD], False),
    ],
    ids=["one-raw", "raw-estimate-stopped", "three-raw"],
)
def test_a_dense_raw_value_and_a_rare_error_walk_the_error_leaf(
    ch_client, worlds, raw_leaves, stop_raw
):
    window_start = R_END - timedelta(days=30)
    filters = [_date(window_start, R_END), STATUS_ERROR, *raw_leaves]
    names: list[str] = []
    cursor = None
    pages = 0
    # Walls wide enough that a loaded host never degrades a page: this test
    # proves the choice, membership and order, not timing.
    with (
        patch.object(walk, "USER_LIST_PAGE_WALL_MS", 60_000),
        patch.object(walk, "USER_LIST_WALK_FINISH_WALL_MS", 120_000),
    ):
        while True:
            read, executor, manager = _page(
                ch_client, R, filters, case="r", cursor=cursor, stop_raw=stop_raw
            )
            pages += 1
            kinds = executor.kinds()
            assert read.payload["query_status"] == "complete"
            if pages == 1:
                # The native witness costed first, then the raw one(s) (at
                # most three candidates); the error leaf walked.
                costed = min(3, 1 + len(raw_leaves))
                assert kinds[:costed] == ["estimate"] * costed
                assert executor.statements[0][0].count("attrs_string") == 0
                assert (manager._walk_witness.family, manager._walk_witness.key) == (
                    "native",
                    "status",
                )
                logged = executor.logged(ch_client)
                answered = executor.statements[: 1 if stop_raw else costed]
                for _query, query_id, cap in answered:
                    read_rows, _bytes, sent_cap = logged[query_id]
                    # Index analysis only: no column rows.
                    assert read_rows <= 1, (query_id, read_rows)
                    assert cap is not None and 25 <= cap <= 1_000
                    assert sent_cap is not None and float(sent_cap) > 0
            else:
                assert "estimate" not in kinds[:1]
                assert manager._walk_witness.key == "status"
            slices = [
                q for q, _id, _cap in executor.statements if "AS raw_end_user_id" in q
            ]
            assert slices and all("toString(status)" in q for q in slices)
            names.extend(row["user_id"] for row in read.payload["table"])
            if not read.has_more:
                break
            cursor = ListCursor(
                window_start=read.window_start,
                window_end=read.window_end,
                order=tuple(read.checkpoint_order),
                seen_rows=read.seen_rows,
            )
            assert pages < 20

    newest = ch_client.execute(
        "SELECT toString(end_user_id), max(start_time) FROM spans "
        "WHERE project_id = toUUID(%(p)s) AND status = 'ERROR' "
        "GROUP BY end_user_id",
        {"p": R},
    )
    label = {
        row[0]: f"user-{n}"
        for n, row in enumerate(
            ch_client.execute(
                f"SELECT toString({_user('number')}) FROM numbers({USERS})"
            )
        )
    }
    expected = [
        label[user]
        for user, _key in sorted(newest, key=lambda row: (row[1], row[0]), reverse=True)
    ]
    assert len(expected) > 25
    assert names == expected
    query, params, _needs_eval = exact_graph_reads._user_id_membership_sql(
        project_id=R,
        filters=filters,
        start_date=window_start,
        end_date=R_END,
        all_snapshot_users=True,
    )
    graph = {label[str(row[0])] for row in ch_client.execute(query, params)}
    assert set(names) == graph


@pytest.mark.parametrize(
    "leaf",
    [STATUS_ERROR, _leaf("model", "gpt-4o", "SYSTEM_METRIC")],
    ids=["status", "model"],
)
def test_a_user_less_twelve_month_scope_is_empty_and_complete_in_three_statements(
    ch_client, worlds, leaf
):
    # Production refused MUD's tail estimate (408 marks over the 1M-row
    # target); here the target sits below any estimate, so the estimate is
    # refused whatever this host's parts look like.
    with patch.object(walk, "USER_LIST_WALK_PROBE_TARGET_READ_ROWS", -1):
        read, executor, _manager = _page(
            ch_client, U, [_date(U_START, U_END), leaf], case="u"
        )
    assert executor.kinds() == ["slice", "estimate", "presence"]
    assert read.payload["table"] == [] and read.has_more is False
    assert read.payload["query_status"] == "complete"
    logged = executor.logged(ch_client)
    # The tail estimate is stopped on the server at half the probe wall (past
    # it, it could license nothing); the presence statement keeps the rest.
    _query, estimate_id, estimate_cap = executor.statements[1]
    assert estimate_cap == walk.USER_LIST_WALK_PROBE_WALL_MS // 2
    assert float(logged[estimate_id][2]) == estimate_cap / 1000
    _query, query_id, cap = executor.statements[2]
    read_rows, read_bytes, sent_cap = logged[query_id]
    assert cap is not None and walk.USER_LIST_WALK_PROBE_WALL_MS // 2 <= cap <= 1_000
    assert sent_cap is not None and float(sent_cap) > 0
    # Boundary granules through the end-user projection, never the tail.
    assert read_bytes < 64 * 1024 * 1024, read_bytes
    assert read_rows < 300_000, read_rows


# P: every eligible witness walks exactly (review round 5, forced parity).
P = str(uuid.uuid4())
P_USERS = 300
P_START = datetime(2026, 8, 10, tzinfo=UTC)
P_END = P_START + timedelta(days=2)
P_LEAVES = {
    "tag": _leaf("tag", "gold", "SPAN_ATTRIBUTE"),
    "plan": _leaf("plan", "pro", "SPAN_ATTRIBUTE"),
    "score": {
        "column_id": "score",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": 8,
        },
    },
    "status": STATUS_ERROR,
    "model": _leaf("model", "gpt-4o", "SYSTEM_METRIC"),
}
# Each leaf's own predicate over the latest live row, for the expected order.
P_PREDICATES = {
    "tag": "attrs_string['tag'] = 'gold'",
    "plan": "attrs_string['plan'] = 'pro'",
    "score": "mapContains(attrs_number, 'score') AND attrs_number['score'] > 8",
    "status": "status = 'ERROR'",
    "model": "model = 'gpt-4o'",
}
P_COMBOS = {
    "four-leaves": ("tag", "plan", "score", "status"),
    "raw-and-model": ("tag", "model"),
    "text-and-typed": ("plan", "score"),
    "two-native": ("status", "model"),
}
P_CASES = [(combo, walked) for combo, leaves in P_COMBOS.items() for walked in leaves]


def _p_spans(salt: str, version: int, deleted: int, where: str) -> str:
    return f"""
    SELECT toUUID('{P}'), 'llm', 'svc',
      toDateTime64('2026-08-10 00:00:00', 6, 'UTC')
        + toIntervalMicrosecond(number * 5760000),
      toString(reinterpretAsUUID(concat(unhex(lpad(hex(number), 16, '0')),
        unhex('00000000000000b7')))),
      concat('s', toString(number)), 'op',
      NULL, {_user("number % " + str(P_USERS))},
      if(cityHash64(number, 'err{salt}') % 200 = 0, 'ERROR', 'OK'),
      if(cityHash64(number, 'model{salt}') % 3 = 0, 'gpt-4o', 'claude'), 'openai',
      map('tag', if(cityHash64(number, 'tag{salt}') % 4 = 0, 'gold', 'silver'),
          'plan', if(cityHash64(number, 'plan{salt}') % 20 = 0, 'pro', 'free')),
      map('score', toFloat64(cityHash64(number, 'score{salt}') % 10)),
      map('flag', toUInt8(cityHash64(number, 'flag{salt}') % 5 = 0)),
      {deleted}, {version}
    FROM numbers(30000)
    WHERE {where}
    """


@pytest.fixture(scope="module")
def p_world(ch_client):
    insert = """
    INSERT INTO spans (project_id, observation_type, service_name, start_time,
      trace_id, id, name, end_time, end_user_id, status, model, provider,
      attrs_string, attrs_number, attrs_bool, is_deleted, _version)
    """
    try:
        ch_client.execute(insert + _p_spans("", 1, 0, "1"))
        # Stale versions: a later version with other values for 1 span in 7.
        ch_client.execute(insert + _p_spans("v2", 2, 0, "number % 7 = 0"))
        # Deleted spans: 1 in 13.
        ch_client.execute(insert + _p_spans("v3", 3, 1, "number % 13 = 0"))
        ch_client.execute(
            f"""
            INSERT INTO end_users (project_id, end_user_id, organization_id,
              user_id, user_id_type, first_seen)
            SELECT toUUID('{P}'), {_user("number")}, toUUID('{ORGANIZATION}'),
              concat('user-', toString(number)), 'string',
              toDateTime64('2026-07-01 00:00:00', 6, 'UTC')
            FROM numbers({P_USERS})
            """
        )
        yield {
            row[0]: f"user-{n}"
            for n, row in enumerate(
                ch_client.execute(
                    f"SELECT toString({_user('number')}) FROM numbers({P_USERS})"
                )
            )
        }
    finally:
        for table in ("spans", "end_users"):
            ch_client.execute(
                f"ALTER TABLE {table} DELETE WHERE project_id = toUUID(%(p)s) "
                "SETTINGS mutations_sync = 2",
                {"p": P},
            )


def _p_filters(leaves) -> list[dict]:
    return [_date(P_START, P_END), *(P_LEAVES[name] for name in leaves)]


def _p_expected(client, label, leaves, walked):
    query, params, _needs_eval = exact_graph_reads._user_id_membership_sql(
        project_id=P,
        filters=_p_filters(leaves),
        start_date=P_START,
        end_date=P_END,
        all_snapshot_users=True,
    )
    graph = {label[str(row[0])] for row in client.execute(query, params)}
    keys = client.execute(
        "SELECT toString(end_user_id), max(start_time) FROM spans FINAL "
        f"WHERE project_id = toUUID(%(p)s) AND is_deleted = 0 "
        f"AND {P_PREDICATES[walked]} GROUP BY end_user_id",
        {"p": P},
    )
    ordered = [
        label[user]
        for user, _key in sorted(keys, key=lambda row: (row[1], row[0]), reverse=True)
    ]
    return [name for name in ordered if name in graph], graph


def _walked_first(walked):
    original = UserListQueryBuilderV2.matching_activity_witnesses

    def first(self):
        return sorted(original(self), key=lambda witness: witness.key != walked)

    return first


def _no_choice(*args, **kwargs):
    raise AssertionError("a continuation never costs witnesses")


@pytest.mark.parametrize(
    ("combo", "walked"), P_CASES, ids=[f"{c}-{w}" for c, w in P_CASES]
)
def test_every_eligible_witness_walks_exactly_once_in_its_order(
    ch_client, p_world, combo, walked
):
    leaves = P_COMBOS[combo]
    expected, graph = _p_expected(ch_client, p_world, leaves, walked)
    assert len(expected) > 7, (combo, walked, len(expected))
    reversed_filters = [_date(P_START, P_END), *(P_LEAVES[n] for n in leaves[::-1])]
    names: list[str] = []
    with (
        patch.object(walk, "USER_LIST_PAGE_WALL_MS", 60_000),
        patch.object(walk, "USER_LIST_WALK_FINISH_WALL_MS", 120_000),
    ):
        # Page 1 forced onto ``walked``: the static rank's first, uncosted.
        with (
            patch.object(
                UserListQueryBuilderV2,
                "matching_activity_witnesses",
                _walked_first(walked),
            ),
            patch.object(walk, "USER_LIST_WALK_WITNESS_CANDIDATES", 1),
        ):
            read, _executor, manager = _page(
                ch_client, P, _p_filters(leaves), case="p", page_size=7
            )
        assert manager._walk_witness.key == walked
        fingerprint = walk.witness_fingerprint(manager._walk_witness)
        names.extend(row["user_id"] for row in read.payload["table"])
        pages = 1
        while read.has_more:
            assert read.checkpoint_order[4] == fingerprint
            cursor = ListCursor(
                window_start=read.window_start,
                window_end=read.window_end,
                order=tuple(read.checkpoint_order),
                seen_rows=read.seen_rows,
            )
            with patch.object(walk, "_choose_witness", _no_choice):
                read, _executor, manager = _page(
                    ch_client, P, reversed_filters, case="p", cursor=cursor, page_size=7
                )
            assert manager._walk_witness.key == walked
            names.extend(row["user_id"] for row in read.payload["table"])
            pages += 1
            assert pages < 400
    assert len(names) == len(set(names)), "a user was published twice"
    assert set(names) == graph
    assert names == expected


@pytest.mark.parametrize("combo", list(P_COMBOS))
def test_the_costed_choice_is_exact_whichever_leaf_it_walks(ch_client, p_world, combo):
    leaves = P_COMBOS[combo]
    names: list[str] = []
    with (
        patch.object(walk, "USER_LIST_PAGE_WALL_MS", 60_000),
        patch.object(walk, "USER_LIST_WALK_FINISH_WALL_MS", 120_000),
    ):
        read, _executor, manager = _page(
            ch_client, P, _p_filters(leaves), case="q", page_size=7
        )
        walked = manager._walk_witness.key
        names.extend(row["user_id"] for row in read.payload["table"])
        while read.has_more:
            cursor = ListCursor(
                window_start=read.window_start,
                window_end=read.window_end,
                order=tuple(read.checkpoint_order),
                seen_rows=read.seen_rows,
            )
            read, _executor, manager = _page(
                ch_client, P, _p_filters(leaves), case="q", cursor=cursor, page_size=7
            )
            assert manager._walk_witness.key == walked
            names.extend(row["user_id"] for row in read.payload["table"])
    expected, graph = _p_expected(ch_client, p_world, leaves, walked)
    assert len(names) == len(set(names)), "a user was published twice"
    assert set(names) == graph
    assert names == expected
