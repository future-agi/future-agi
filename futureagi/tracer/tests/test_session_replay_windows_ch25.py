"""Real storage-key/window regressions; no copied winner or usage reducer."""

from datetime import timedelta
from uuid import uuid4

import pytest

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.schema import SPANS_TABLE, _to_single_node_engine
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (  # noqa: F401
    _REMAP_DDL,
    _SPANS_DDL,
    _drop_legacy_ch_spans_mvs,
    _ensure_test_score_tenant_column,
)
from tracer.tests.test_session_usage_membership_ch25 import START, execute, time_filter

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", params=["v1", "v2"])
def replay_database(request):
    with _ch_test_owned_database("test_session_replay_") as database:
        with _ch_test_native_client(database=database) as client:
            # Exact CDC DDL. Native fixture adds only the real arrival column
            # from 002_spans_v2.sql; it retains the native service/type/hour key.
            client.execute(
                _to_single_node_engine(SPANS_TABLE)
                if request.param == "v1"
                else _SPANS_DDL.replace(
                    "    _version UInt64,",
                    "    created_at DateTime64(6, 'UTC') DEFAULT now64(6, 'UTC'),\n"
                    "    _version UInt64,",
                )
            )
            client.execute(_REMAP_DDL)
            client.execute("SYSTEM STOP MERGES spans")
            try:
                yield (
                    client,
                    (
                        SessionListQueryBuilder
                        if request.param == "v1"
                        else SessionListQueryBuilderV2
                    ),
                )
            finally:
                client.execute("SYSTEM START MERGES spans")


def span(project, session, *, root=False, **changes):
    return {
        "project_id": project,
        "trace_id": "trace",
        "trace_session_id": session,
        "id": "root" if root else "call",
        "parent_span_id": "" if root else "root",
        "observation_type": "agent" if root else "llm",
        "name": "root" if root else "request",
        "start_time": START + timedelta(minutes=20),
        "end_time": START + timedelta(minutes=21),
        # Arrival and event dates deliberately differ, including partitions.
        "created_at": START - timedelta(days=40),
        "cost": 0.0 if root else 0.25,
        "total_tokens": 0 if root else 12,
        "prompt_tokens": 0 if root else 10,
        "completion_tokens": 0 if root else 2,
        "input": "original input",
        "attrs_string": {"fi.cost.kind": "provider_reported"},
        "_version": 1,
        "is_deleted": 0,
        **changes,
    }


def insert(client, cls, rows):
    for original in rows:
        row = dict(original)
        if cls is SessionListQueryBuilder:
            row["span_attr_str"] = row.pop("attrs_string")
            row["_peerdb_version"] = row.pop("_version")
            row["_peerdb_is_deleted"] = row.pop("is_deleted")
        client.execute(
            f"INSERT INTO spans ({', '.join(row)}) VALUES", [tuple(row.values())]
        )


def number_filter(column, value):
    return {
        "column_id": column,
        "filter_config": {
            "filter_type": "number",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


@pytest.mark.parametrize("explicit_window", [False, True])
def test_arrival_id_query_resolves_late_root_and_child_usage(
    replay_database, explicit_window
):
    client, cls = replay_database
    project, session = uuid4(), uuid4()
    late = START - timedelta(days=400)
    root = span(
        project,
        session,
        root=True,
        start_time=late,
        created_at=START,
        cost=0.25,
        total_tokens=12,
        prompt_tokens=10,
        completion_tokens=2,
    )
    child = span(
        project,
        session,
        start_time=late,
        cost=0.5,
        total_tokens=24,
        prompt_tokens=20,
        completion_tokens=4,
    )
    insert(
        client,
        cls,
        [
            root,
            child,
            {**root, "project_id": uuid4(), "cost": 100.0},
            {
                **root,
                "trace_id": "ceiling",
                "trace_session_id": uuid4(),
                "created_at": START + timedelta(days=1),
            },
        ],
    )
    filters = ([time_filter()] if explicit_window else []) + [
        number_filter("total_cost", 0.75),
        number_filter("total_tokens", 36),
    ]
    builder = cls(project_id=str(project), filters=filters)
    rows = execute(
        client,
        builder.build_id_query(
            created_at_floor=START, created_at_ceiling=START + timedelta(days=1)
        ),
    )
    assert len(rows) == 1
    assert str(rows[0]["session_id"]) == str(session)
    assert rows[0]["total_cost"] == 0.75
    assert rows[0]["total_tokens"] == 36
    assert rows[0]["traces_count"] == 1
    # Without arrival discovery the normal bounded event window still excludes it.
    assert (
        execute(client, cls(project_id=str(project), filters=filters).build_id_query())
        == []
    )


@pytest.mark.parametrize("root", [False, True])
@pytest.mark.parametrize(
    "correction", ["inside", "before", "after", "deleted", "moved", "remapped"]
)
def test_mutable_timestamp_winner_controls_membership_and_usage(
    replay_database, root, correction
):
    client, cls = replay_database
    project, session, other = uuid4(), uuid4(), uuid4()
    original = span(
        project,
        session,
        root=root,
        cost=0.25,
        total_tokens=12,
        prompt_tokens=10,
        completion_tokens=2,
    )
    corrected = {
        **original,
        "_version": 2,
        "start_time": START + timedelta(minutes=30),
        "cost": 0.5,
        "total_tokens": 24,
        "prompt_tokens": 20,
        "completion_tokens": 4,
        "input": "corrected input",
    }
    if correction in {"before", "after"}:
        # CDC can cross days within a key; native correction stays within its hour.
        delta = (
            timedelta(days=-2 if correction == "before" else 2)
            if cls is SessionListQueryBuilder
            else timedelta(minutes=5 if correction == "before" else 55)
        )
        corrected["start_time"] = START + delta
    elif correction == "deleted":
        corrected["is_deleted"] = 1
    elif correction in {"moved", "remapped"}:
        corrected["trace_session_id"] = other
        if correction == "remapped":
            client.execute(
                "INSERT INTO trace_session_id_remap (old_id, new_id) VALUES",
                [(session, other)],
            )
    rows = [
        original,
        corrected,
        {
            **corrected,
            "project_id": uuid4(),
            "_version": 99,
            "cost": 100.0,
            "is_deleted": 1,
        },
    ]
    if not root:
        rows.append(span(project, session, root=True, input=""))
    insert(client, cls, rows)
    window = time_filter()
    window["filter_config"]["filter_value"] = [
        (START + timedelta(minutes=10)).isoformat(),
        (START + timedelta(minutes=50)).isoformat(),
    ]
    live = correction in {"inside", "remapped"}
    expected = (0.5, 24) if live else (0.0, 0)
    builder = cls(project_id=str(project), filters=[window])
    hydrated = execute(client, builder.build_page_hydration_query([str(session)]))
    if root and not live:
        assert hydrated == []
    else:
        assert len(hydrated) == 1
        assert (hydrated[0]["total_cost"], hydrated[0]["total_tokens"]) == expected
        assert hydrated[0]["traces_count"] == 1
        if live:
            assert hydrated[0]["first_message"] == "corrected input"
    filtered = cls(
        project_id=str(project),
        filters=[
            window,
            number_filter("total_cost", expected[0]),
            number_filter("total_tokens", expected[1]),
            {
                "column_id": "session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": str(session),
                },
            },
        ],
    )
    expected_ids = {str(session)} if not root or live else set()
    for query in (
        filtered.build_candidate_page_query(),
        filtered.build_id_query(),
        filtered.build_filter_match_query([str(session)]),
    ):
        assert {
            str(row["session_id"]) for row in execute(client, query)
        } == expected_ids
    assert execute(client, filtered.build_candidate_count_query()) == [
        {"total": len(expected_ids)}
    ]


@pytest.mark.parametrize("dimension", ["created_day", "service", "type", "hour"])
def test_replacement_key_distinguishes_actual_schema_dimensions(
    replay_database, dimension
):
    client, cls = replay_database
    if cls is SessionListQueryBuilder and dimension != "created_day":
        pytest.skip("service/type/hour are native key dimensions, not CDC key columns")
    project, session = uuid4(), uuid4()
    original = span(project, session)
    second = {
        **original,
        "_version": 2,
        "cost": 0.5,
        "total_tokens": 24,
        "prompt_tokens": 20,
        "completion_tokens": 4,
    }
    second.update(
        {
            "created_day": {"created_at": original["created_at"] + timedelta(days=1)},
            "service": {"service_name": "another-service"},
            "type": {"observation_type": "tool"},
            "hour": {"start_time": original["start_time"] + timedelta(hours=1)},
        }[dimension]
    )
    insert(client, cls, [span(project, session, root=True), original, second])
    expected = (
        (0.5, 24)
        if cls is SessionListQueryBuilderV2 and dimension == "created_day"
        else (0.75, 36)
    )
    builder = cls(project_id=str(project), filters=[time_filter()])
    rows = execute(client, builder.build_page_hydration_query([str(session)]))
    assert len(rows) == 1
    assert (rows[0]["total_cost"], rows[0]["total_tokens"]) == expected
    rows = execute(client, builder.build_id_query())
    assert len(rows) == 1
    assert (rows[0]["total_cost"], rows[0]["total_tokens"]) == expected


def test_org_candidate_detects_same_public_session_in_two_projects(replay_database):
    client, cls = replay_database
    first, second, session = uuid4(), uuid4(), uuid4()
    insert(
        client,
        cls,
        [
            span(first, session, root=True),
            span(second, session, root=True),
            span(first, session),
            span(second, session, cost=0.5),
        ],
    )
    builder = cls(project_ids=[str(first), str(second)], filters=[time_filter()])
    rows = execute(client, builder.build_candidate_page_query())
    assert len(rows) == 1
    assert str(rows[0]["session_id"]) == str(session)
    assert rows[0]["project_count"] == 2
