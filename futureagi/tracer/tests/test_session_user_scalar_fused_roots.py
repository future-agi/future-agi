"""The user-detail Sessions page with a span-attribute filter reads its
sessions' spans once.

Its candidate statement seeds ``candidate_scalar_span_identities`` by
``trace_session_id`` for the user's sessions, and used to seed
``candidate_root_identities`` by the same key for the same sessions. A
session's roots are a subset of its spans, so the second scan re-read the
first scan's granules for nothing - and on the base table that scan is a
bloom-filter scatter whose false-positive granules grow with the number of
seeded sessions. ``sessions`` now stands on the one all-span replay, deciding
root-ness on each span's latest version exactly as ``latest_roots`` did.

The live half runs the statement against a disposable CH25 schema with a
fixture built for what the fused route could get wrong: a witness on a child
that carries no ``end_user_id``, a span whose latest version turned into a
root and one whose latest version stopped being a root, a tombstoned root,
two live roots so ``session_start`` is a real minimum, another user's session
with a matching witness, and a user session whose only root is deleted.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from conftest import _open_ch_test_native_client
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
SECOND_PROJECT_ID = "00000000-0000-4000-8000-000000000002"
USER_ID = "00000000-0000-4000-8000-000000000003"


def _window(now: datetime) -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [
                (now - timedelta(days=1)).isoformat(),
                (now + timedelta(days=1)).isoformat(),
            ],
        },
    }


def _user(user_id: str = USER_ID) -> dict:
    return {
        "column_id": "end_user_id",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": [user_id],
        },
    }


def _attribute(key="final_status", kind="text", operation="equals", value="Rejected"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _builder(filters, *, project_ids=None, **kwargs):
    return SessionListQueryBuilderV2(
        project_id=None if project_ids else PROJECT_ID,
        project_ids=project_ids,
        filters=filters,
        page_number=0,
        page_size=25,
        bounded_internal_scan=True,
        **kwargs,
    )


def _cte(sql: str, name: str) -> str:
    return sql.split(f"{name} AS (", 1)[1].split("\n        )", 1)[0]


@pytest.mark.parametrize("mode", ["page", "cursor", "count"])
@pytest.mark.parametrize(
    "attribute",
    [
        _attribute(),
        _attribute("lk.interrupted", "boolean", "equals", True),
        _attribute("metadata", "text", "in", ["{" + "x" * 400 + "}"]),
    ],
)
def test_user_scalar_page_reads_the_sessions_spans_once(mode, attribute):
    now = datetime(2026, 9, 12, 0, 0)
    builder = _builder([_window(now), _user(), attribute])
    assert builder.prefers_bounded_filter_page() is False
    assert builder.supports_candidate_cursor_page() is True
    method = {
        "page": builder.build_candidate_page_query,
        "cursor": builder.build_candidate_cursor_page_query,
        "count": builder.build_candidate_count_query,
    }[mode]
    sql, params = method()

    # One user scan, one user replay, one session-keyed scan, one replay.
    assert sql.count("FROM spans") == 4
    assert sql.count("SELECT session_id FROM matching_user_root_ids") == 1
    assert "candidate_root_identities AS (" not in sql
    assert "latest_roots AS (" not in sql
    assert "resolved_root_sessions" not in sql
    assert "matching_scalar_sessions AS (" not in sql
    # The seed CTE is declared before the scan that consumes it.
    assert sql.index("matching_user_root_ids AS (") < sql.index(
        "SELECT session_id FROM matching_user_root_ids"
    )
    replay = _cte(sql, "latest_candidate_scalar_spans")
    assert (
        "argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id" in replay
    )
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in replay
    resolved = _cte(sql, "resolved_candidate_scalar_spans")
    assert (
        "(isNull(latest_parent_span_id) OR latest_parent_span_id = '') AS is_root"
        in resolved
    )
    assert "WHERE latest_is_deleted = 0" in resolved
    assert (
        "latest_start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')"
        in resolved
    )
    assert (
        "latest_start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')"
        in resolved
    )
    sessions = sql.split("sessions AS (", 1)[1].split("FROM sessions", 1)[0]
    assert "FROM resolved_candidate_scalar_spans" in sessions
    assert "minIf(latest_start_time, is_root) AS session_start" in sessions
    assert (
        "WHERE session_id IN (SELECT session_id FROM matching_user_sessions)"
        in sessions
    )
    assert "countIf(is_root) > 0" in sessions
    assert "candidate_filter_sessions" not in sql
    assert (
        "SAMPLE" not in sql
        and "FINAL" not in sql.split("trace_session_id_remap AS remap FINAL")[-1]
    )
    assert params["candidate_filter_user_ids"] == (USER_ID,)
    if mode != "count":
        assert "ORDER BY session_start DESC, session_id DESC" in sql
        assert params["limit"] == 26


def test_user_page_without_attribute_keeps_its_root_scan():
    """The BASE user-detail page has no all-span replay to stand on."""

    now = datetime(2026, 9, 12, 0, 0)
    sql, _ = _builder([_window(now), _user()]).build_candidate_cursor_page_query()
    assert "candidate_scalar_span_identities" not in sql
    assert "candidate_root_identities AS (" in sql
    assert "latest_roots AS (" in sql
    assert "SELECT session_id FROM matching_user_root_ids" in _cte(
        sql, "candidate_root_identities"
    )


def test_org_scope_user_scalar_page_keeps_its_collision_guard_path():
    now = datetime(2026, 9, 12, 0, 0)
    builder = _builder(
        [_window(now), _user(), _attribute()],
        project_ids=[PROJECT_ID, SECOND_PROJECT_ID],
    )
    sql, _ = builder.build_candidate_cursor_page_query()
    assert "candidate_root_identities AS (" in sql
    assert "matching_scalar_sessions AS (" in sql
    assert "candidate_session_project_counts AS" in sql
    assert "countIf(is_root) > 0" not in sql


def _ch25_client():
    # conftest resolves the port (no default; a forwarded one is refused) and,
    # on CI's opted-in sidecar, proves it before the INSERT below.
    database = os.getenv("CH25_DATABASE") or os.getenv("CH_DATABASE") or "test_tfc"
    return _open_ch_test_native_client(database=database)


_COLUMNS = (
    "project_id",
    "observation_type",
    "service_name",
    "start_time",
    "trace_id",
    "id",
    "parent_span_id",
    "name",
    "end_time",
    "latency_ms",
    "org_id",
    "project_version_id",
    "end_user_id",
    "trace_session_id",
    "status",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "attrs_string",
    "attrs_number",
    "attrs_bool",
    "attributes_extra",
    "input",
    "output",
    "is_deleted",
    "_version",
)


def _span(
    project_id,
    org_id,
    *,
    session_id,
    trace_id,
    span_id,
    start,
    parent="",
    user_id=None,
    attrs=None,
    deleted=0,
    version=1,
):
    attrs = dict(attrs or {})
    return (
        project_id,
        "llm",
        "fused-roots-test",
        start,
        trace_id,
        span_id,
        parent,
        span_id,
        start + timedelta(seconds=1),
        10,
        org_id,
        None,
        user_id,
        session_id,
        "OK",
        1,
        1,
        2,
        0.5,
        attrs,
        {},
        {},
        "{}",
        "in",
        "out",
        deleted,
        version,
    )


@pytest.mark.integration
def test_fused_root_replay_publishes_the_same_sessions_as_a_root_scan_would():
    client = _ch25_client()
    project_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    user_id, other_user_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    s1, s2, s3, s4, s5 = (str(uuid.uuid4()) for _ in range(5))
    t = {
        name: now - timedelta(minutes=minutes)
        for name, minutes in (
            ("r1", 30),
            ("c1", 29),
            ("r0", 40),
            ("rc", 50),
            ("cr", 55),
            ("rd", 58),
            ("s2", 20),
            ("s3", 10),
            ("s4", 15),
            ("s5", 5),
        )
    }
    trace = {
        name: str(uuid.uuid4())
        for name in ("s1a", "s1b", "s1c", "s2", "s3", "s4", "s5")
    }
    rows = [
        # Session 1: two live roots, a witness on a child with no user, a span
        # that became a root, a span that stopped being a root, a dead root.
        _span(
            project_id,
            org_id,
            session_id=s1,
            trace_id=trace["s1a"],
            span_id="r1",
            start=t["r1"],
            user_id=user_id,
        ),
        _span(
            project_id,
            org_id,
            session_id=s1,
            trace_id=trace["s1a"],
            span_id="c1",
            start=t["c1"],
            parent="r1",
            attrs={"final_status": "Rejected"},
        ),
        _span(
            project_id,
            org_id,
            session_id=s1,
            trace_id=trace["s1b"],
            span_id="r0",
            start=t["r0"],
            user_id=user_id,
        ),
        _span(
            project_id,
            org_id,
            session_id=s1,
            trace_id=trace["s1c"],
            span_id="rc",
            start=t["rc"],
            parent="was-a-child",
            user_id=user_id,
            version=1,
        ),
        _span(
            project_id,
            org_id,
            session_id=s1,
            trace_id=trace["s1c"],
            span_id="rc",
            start=t["rc"],
            parent="",
            user_id=user_id,
            version=2,
        ),
        _span(
            project_id,
            org_id,
            session_id=s1,
            trace_id=trace["s1c"],
            span_id="cr",
            start=t["cr"],
            parent="",
            user_id=user_id,
            version=1,
        ),
        _span(
            project_id,
            org_id,
            session_id=s1,
            trace_id=trace["s1c"],
            span_id="cr",
            start=t["cr"],
            parent="rc",
            user_id=user_id,
            version=2,
        ),
        _span(
            project_id,
            org_id,
            session_id=s1,
            trace_id=trace["s1b"],
            span_id="rd",
            start=t["rd"],
            user_id=user_id,
            version=1,
        ),
        _span(
            project_id,
            org_id,
            session_id=s1,
            trace_id=trace["s1b"],
            span_id="rd",
            start=t["rd"],
            user_id=user_id,
            deleted=1,
            version=2,
        ),
        # Session 2: the user's, but its witness does not match.
        _span(
            project_id,
            org_id,
            session_id=s2,
            trace_id=trace["s2"],
            span_id="s2-root",
            start=t["s2"],
            user_id=user_id,
            attrs={"final_status": "Accepted"},
        ),
        # Session 3: matches, but belongs to another user.
        _span(
            project_id,
            org_id,
            session_id=s3,
            trace_id=trace["s3"],
            span_id="s3-root",
            start=t["s3"],
            user_id=other_user_id,
        ),
        _span(
            project_id,
            org_id,
            session_id=s3,
            trace_id=trace["s3"],
            span_id="s3-child",
            start=t["s3"],
            parent="s3-root",
            attrs={"final_status": "Rejected"},
        ),
        # Session 4: the user's (its live child carries the user) and matching,
        # but its only root is deleted, so it has no live root to rank by.
        _span(
            project_id,
            org_id,
            session_id=s4,
            trace_id=trace["s4"],
            span_id="s4-root",
            start=t["s4"],
            user_id=user_id,
            version=1,
        ),
        _span(
            project_id,
            org_id,
            session_id=s4,
            trace_id=trace["s4"],
            span_id="s4-root",
            start=t["s4"],
            user_id=user_id,
            deleted=1,
            version=2,
        ),
        _span(
            project_id,
            org_id,
            session_id=s4,
            trace_id=trace["s4"],
            span_id="s4-child",
            start=t["s4"],
            parent="s4-root",
            user_id=user_id,
            attrs={"final_status": "Rejected"},
        ),
        # Session 5: a matching root whose older version sat in session 1 and
        # whose latest version moved to a session the user never touched. The
        # seed acquires it through the old version; the published page must not
        # carry the destination session, whose root set was never acquired.
        _span(
            project_id,
            org_id,
            session_id=s1,
            trace_id=trace["s5"],
            span_id="moved-root",
            start=t["s5"],
            attrs={"final_status": "Rejected"},
            version=1,
        ),
        _span(
            project_id,
            org_id,
            session_id=s5,
            trace_id=trace["s5"],
            span_id="moved-root",
            start=t["s5"],
            attrs={"final_status": "Rejected"},
            version=2,
        ),
    ]
    # Later versions go in a second INSERT: a single block is collapsed to its
    # latest version on insert, which would erase every older version the
    # fixture relies on (the child that became a root, the root that became a
    # child, the tombstoned roots, and the root that moved session).
    for version in (1, 2):
        client.execute(
            f"INSERT INTO spans ({', '.join(_COLUMNS)}) VALUES",
            [row for row in rows if row[-1] == version],
            types_check=True,
        )

    def run(builder_method):
        sql, params = builder_method()
        rows, columns = client.execute(sql, params, with_column_types=True)
        names = [column[0] for column in columns]
        return [dict(zip(names, row, strict=True)) for row in rows]

    filters = [_window(now), _user(user_id), _attribute()]
    builder = SessionListQueryBuilderV2(
        project_id=project_id,
        filters=filters,
        page_number=0,
        page_size=25,
        bounded_internal_scan=True,
    )
    page = run(builder.build_candidate_cursor_page_query)
    assert [str(row["session_id"]) for row in page] == [s1]
    # Earliest LIVE root by latest version: ``rc`` (became a root), not ``cr``
    # (stopped being one) and not ``rd`` (tombstoned).
    assert page[0]["session_start"].replace(tzinfo=None) == t["rc"]
    assert page[0]["remaining_count"] == 1
    assert run(builder.build_candidate_count_query)[0]["total"] == 1
    numbered = run(builder.build_candidate_page_query)
    assert [str(row["session_id"]) for row in numbered] == [s1]
    assert numbered[0]["total_count"] == 1

    # A keyset continuation below the published row is exhausted exactly.
    continuation = SessionListQueryBuilderV2(
        project_id=project_id,
        filters=filters,
        page_number=0,
        page_size=25,
        bounded_internal_scan=True,
    )
    later = run(
        lambda: continuation.build_candidate_cursor_page_query(
            before_start_time=t["rc"], before_session_id=s1
        )
    )
    assert later == []

    # The non-matching value publishes the other user session, never s3/s4/s5.
    accepted = SessionListQueryBuilderV2(
        project_id=project_id,
        filters=[_window(now), _user(user_id), _attribute(value="Accepted")],
        page_number=0,
        page_size=25,
        bounded_internal_scan=True,
    )
    assert [
        str(row["session_id"])
        for row in run(accepted.build_candidate_cursor_page_query)
    ] == [s2]
