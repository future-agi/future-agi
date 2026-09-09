"""Session user labels from actual helper SQL over constant ClickHouse rows.

These fixtures exercise canonicalization, physical replay and output rekeying.
Only the curated dictionary transport is mocked; no tables or DDL are needed.
Equal-version or final-order ties may choose either coherent winner.
"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.v2 import end_user_dict_reader
from tracer.views.trace_session import TraceSessionView

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
SESSION = "10000000-0000-4000-8000-000000000001"
ALIAS = "10000000-0000-4000-8000-000000000002"
NEW = "10000000-0000-4000-8000-000000000003"
USER = "20000000-0000-4000-8000-000000000001"
OTHER_USER = "20000000-0000-4000-8000-000000000002"
NIL = "00000000-0000-0000-0000-000000000000"
AT = datetime(2026, 8, 1, 12)
CONTEXT = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))


def row(**changes):
    return {
        "project_id": PROJECT,
        "observation_type": "SPAN",
        "service_name": "service",
        "trace_id": "trace",
        "id": "child",
        "start_time": AT + timedelta(minutes=10),
        "trace_session_id": SESSION,
        "end_user_id": USER,
        "_version": 1,
        "is_deleted": 0,
    } | changes


@pytest.fixture(scope="module")
def engine():
    return pytest.importorskip("chdb", reason="optional constant-fixture engine")


def read(engine, monkeypatch, rows, *, ids=(SESSION,), remaps=(), reverse=False):
    rows = list(reversed(rows)) if reverse else rows
    schema = (
        "project_id UUID, observation_type String, service_name String, "
        "trace_id String, id String, start_time DateTime64(6,'UTC'), "
        "trace_session_id Nullable(UUID), end_user_id Nullable(UUID), "
        "_version UInt64, is_deleted UInt8"
    )
    values = tuple(
        tuple(
            r[key].isoformat(sep=" ") if key == "start_time" else r[key]
            for key in row()
        )
        for r in rows
    )
    literals = escape_params(
        {"schema": schema, "rows": values, "remaps": remaps}, CONTEXT
    )
    remap_source = (
        f"SELECT * FROM values('old_id UUID, new_id UUID', {literals['remaps'][1:-1]})"
        if remaps
        else f"SELECT toUUID('{NIL}') AS old_id, old_id AS new_id WHERE 0"
    )
    prefix = (
        f"WITH spans AS (SELECT * FROM values({literals['schema']}, {literals['rows'][1:-1]})), "
        f"trace_session_id_remap AS ({remap_source})"
    )
    calls = []

    def execute(query, params, **_kwargs):
        calls.append(query)
        # VALUES has no PREWHERE/FINAL; preserve all predicates and aggregates.
        query = query.replace("PREWHERE", "WHERE").replace(
            "trace_session_id_remap FINAL", "trace_session_id_remap"
        )
        query = query.lstrip()
        query = "," + query[4:] if query.startswith("WITH") else " " + query
        sql = prefix + query % escape_params(params, CONTEXT)
        sql += (
            " SETTINGS max_threads=1, max_execution_time=5, "
            "max_memory_usage=134217728, max_result_rows=100, "
            "max_result_bytes=1048576, result_overflow_mode='throw', "
            f"join_use_nulls={int(reverse)}"
        )
        return SimpleNamespace(
            data=[
                json.loads(line)
                for line in str(engine.query(sql, "JSONEachRow")).splitlines()
                if line
            ]
        )

    def curated_fields(user_ids, **_kwargs):
        assert user_ids and NIL not in user_ids and None not in user_ids
        return {
            user_id: {
                "user_id": user_id,
                "user_id_type": "custom",
                "user_id_hash": 0,
            }
            for user_id in user_ids
        }

    monkeypatch.setattr(end_user_dict_reader, "resolve_end_user_fields", curated_fields)
    result = TraceSessionView._fetch_end_user_info(
        ids, SimpleNamespace(execute_ch_query=execute), [PROJECT]
    )
    assert len(calls) == 2  # Existing canonicalization, then the actual helper replay.
    return {session: fields["user_id"] for session, fields in result.items()}


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "latest",
    [
        {"is_deleted": 1},
        {"end_user_id": None},
        {"end_user_id": NIL},
        {"trace_session_id": ALIAS},
        {"trace_session_id": None},
    ],
)
def test_corrected_timestamp_does_not_resurrect_user(
    engine, monkeypatch, reverse, latest
):
    rows = [row(start_time=AT + timedelta(minutes=50)), row(_version=2, **latest)]
    assert read(engine, monkeypatch, rows, reverse=reverse) == {}


@pytest.mark.parametrize("reverse", [False, True])
def test_user_order_uses_corrected_winning_time(engine, monkeypatch, reverse):
    rows = [
        row(start_time=AT + timedelta(minutes=50)),
        row(_version=2),
        row(
            id="other-child",
            start_time=AT + timedelta(minutes=30),
            end_user_id=OTHER_USER,
        ),
    ]
    assert read(engine, monkeypatch, rows, reverse=reverse) == {SESSION: OTHER_USER}


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "identity",
    [
        {"service_name": "other"},
        {"observation_type": "LLM"},
        {"start_time": AT + timedelta(hours=1)},
        {"project_id": OTHER},
    ],
)
def test_tombstone_cannot_delete_another_physical_key(
    engine, monkeypatch, reverse, identity
):
    assert read(
        engine,
        monkeypatch,
        [row(), row(_version=2, is_deleted=1, **identity)],
        reverse=reverse,
    ) == {SESSION: USER}


def test_latest_user_reassignment_and_all_history_child(engine, monkeypatch):
    old = datetime(2010, 1, 1)
    rows = [
        row(start_time=old),
        row(start_time=old, _version=2, end_user_id=OTHER_USER),
        row(id="root", end_user_id=None),
    ]
    assert read(engine, monkeypatch, rows) == {SESSION: OTHER_USER}


@pytest.mark.parametrize("reverse", [False, True])
def test_session_aliases_rekey_one_canonical_label(engine, monkeypatch, reverse):
    rows = [
        row(),
        row(
            id="new-child",
            trace_session_id=NEW,
            end_user_id=OTHER_USER,
            start_time=AT + timedelta(minutes=20),
        ),
    ]
    ids = (SESSION, ALIAS, NEW)
    assert read(
        engine,
        monkeypatch,
        rows,
        ids=ids,
        remaps=((SESSION, NEW), (ALIAS, NEW)),
        reverse=reverse,
    ) == dict.fromkeys(ids, OTHER_USER)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "other",
    [
        {"is_deleted": 1, "start_time": AT + timedelta(minutes=20)},
        {"end_user_id": None},
    ],
)
def test_equal_version_tie_keeps_one_coherent_tuple(
    engine, monkeypatch, reverse, other
):
    rows = [row(_version=2), row(_version=2, trace_session_id=ALIAS, **other)]
    assert read(engine, monkeypatch, rows, ids=(SESSION, ALIAS), reverse=reverse) in (
        {},
        {SESSION: USER},
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_final_order_tie_retains_existing_choice(engine, monkeypatch, reverse):
    rows = [row(), row(service_name="other", end_user_id=OTHER_USER)]
    assert read(engine, monkeypatch, rows, reverse=reverse) in (
        {SESSION: USER},
        {SESSION: OTHER_USER},
    )
