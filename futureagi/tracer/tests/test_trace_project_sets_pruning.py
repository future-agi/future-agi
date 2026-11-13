"""TH-4804: the Usage trace-project relation reads only its candidate traces.

``live_trace_project_sets_sql`` receives every project the caller may read, so
its ``project_id IN`` predicate alone selects the whole workspace's traces.
The candidate trace IDs must prune the ``(project_id, id)`` primary key too;
an INNER JOIN on them does not, and the scan then grows with the workspace's
trace volume instead of the eval's usage rows.
"""

from __future__ import annotations

import re
import uuid

import pytest

from conftest import _ch_test_native_client
from tracer.services.clickhouse import trace_project_scope

pytestmark = pytest.mark.integration

GRANULARITY = 64
TRACES = 6_400  # 100 granules in one project


@pytest.fixture
def trace_table():
    table = f"_test_trace_sets_{uuid.uuid4().hex[:10]}"
    with _ch_test_native_client() as client:
        client.execute(
            f"""
            CREATE TABLE {table} (
                id UUID, project_id UUID, is_deleted UInt8, _version UInt64
            ) ENGINE = ReplacingMergeTree(_version, is_deleted)
            ORDER BY (project_id, id)
            SETTINGS index_granularity = {GRANULARITY}
            """
        )
        try:
            yield client, table
        finally:
            client.execute(f"DROP TABLE IF EXISTS {table}")


def test_candidate_trace_ids_prune_the_trace_scan(trace_table, monkeypatch):
    client, table = trace_table
    project = uuid.uuid4()
    other = uuid.uuid4()
    client.execute(
        f"INSERT INTO {table} SELECT generateUUIDv4(), %(p)s, 0, 1 FROM numbers({TRACES})",
        {"p": project},
    )
    candidates = [
        row[0] for row in client.execute(f"SELECT id FROM {table} ORDER BY id LIMIT 2")
    ]
    # One trace is also live in another requested project.
    client.execute(f"INSERT INTO {table} VALUES", [(candidates[0], other, 0, 1)])
    client.execute(f"OPTIMIZE TABLE {table} FINAL")
    monkeypatch.setattr(trace_project_scope, "_TRACE_TABLE", table)
    sql = trace_project_scope.live_trace_project_sets_sql(
        candidate_trace_ids_sql=(
            "SELECT toUUID(arrayJoin(%(candidate_ids)s)) AS trace_id"
        )
    )
    params = {
        "project_ids": (project, other),
        "candidate_ids": candidates,
    }

    rows = client.execute(sql, params)
    assert {trace: set(projects) for trace, projects in rows} == {
        candidates[0]: {project, other},
        candidates[1]: {project},
    }

    plan = "\n".join(
        line for (line,) in client.execute("EXPLAIN indexes = 1 " + sql, params)
    )
    scans = [
        section
        for section in plan.split("ReadFromMergeTree")[1:]
        if section.lstrip().startswith(f"(default.{table})")
        or f".{table})" in section.splitlines()[0]
    ]
    assert scans, plan
    primary_key = scans[0].split("PrimaryKey", 1)[1]
    selected, total = map(
        int, re.search(r"Granules: (\d+)/(\d+)", primary_key).groups()
    )
    # 101 granules in all; the two candidates sit in at most three of them.
    assert total >= TRACES // GRANULARITY
    assert selected <= 3, plan


def _selected_granules(client, table, sql, params):
    plan = "\n".join(
        line for (line,) in client.execute("EXPLAIN indexes = 1 " + sql, params)
    )
    scans = [
        section
        for section in plan.split("ReadFromMergeTree")[1:]
        if section.lstrip().startswith(f"(default.{table})")
        or f".{table})" in section.splitlines()[0]
    ]
    assert scans, plan
    primary_key = scans[0].split("PrimaryKey", 1)[1]
    selected, total = map(
        int, re.search(r"Granules: (\d+)/(\d+)", primary_key).groups()
    )
    return selected, total, plan


def test_outside_copies_come_from_the_same_pruned_scan(trace_table, monkeypatch):
    """TH-4804 gate: an older log is attributed only when no project outside
    the caller's readable scope holds its trace (live or tombstoned). That
    check rides on the same candidate-pruned scan of the readable and the
    organization's other projects."""
    client, table = trace_table
    readable, outside = uuid.uuid4(), uuid.uuid4()
    client.execute(
        f"INSERT INTO {table} SELECT generateUUIDv4(), %(p)s, 0, 1 FROM numbers({TRACES})",
        {"p": readable},
    )
    client.execute(
        f"INSERT INTO {table} SELECT generateUUIDv4(), %(p)s, 0, 1 FROM numbers({TRACES})",
        {"p": outside},
    )
    candidates = [
        row[0]
        for row in client.execute(
            f"SELECT id FROM {table} WHERE project_id = %(p)s ORDER BY id LIMIT 3",
            {"p": readable},
        )
    ]
    replayed, tombstoned_elsewhere, single = candidates
    client.execute(
        f"INSERT INTO {table} VALUES",
        [
            (replayed, outside, 0, 1),
            (tombstoned_elsewhere, outside, 0, 1),
            (tombstoned_elsewhere, outside, 1, 2),
        ],
    )
    client.execute(f"OPTIMIZE TABLE {table} FINAL")
    monkeypatch.setattr(trace_project_scope, "_TRACE_TABLE", table)
    sql = trace_project_scope.live_trace_project_sets_sql(
        candidate_trace_ids_sql=(
            "SELECT toUUID(arrayJoin(%(candidate_ids)s)) AS trace_id"
        ),
        outside_project_ids_param="outside_project_ids",
        scan_project_ids_param="scan_project_ids",
    )
    params = {
        "project_ids": (readable,),
        "outside_project_ids": (outside,),
        "scan_project_ids": (readable, outside),
        "candidate_ids": candidates,
    }

    rows = {
        trace: (set(projects), held)
        for trace, projects, held in client.execute(sql, params)
    }
    # The outside project is never a live listing project; any row there,
    # tombstoned or not, marks the trace as held elsewhere.
    assert rows == {
        replayed: ({readable}, 1),
        tombstoned_elsewhere: ({readable}, 1),
        single: ({readable}, 0),
    }
    selected, total, plan = _selected_granules(client, table, sql, params)
    # 200 granules in all; the three candidates sit in at most a few per project.
    assert total >= 2 * TRACES // GRANULARITY
    assert selected <= 6, plan


def test_navigation_outside_copy_read_is_a_pruned_point_read(trace_table):
    from model_hub.services import eval_log_source_navigation as navigation

    client, table = trace_table
    outside = uuid.uuid4()
    client.execute(
        f"INSERT INTO {table} SELECT generateUUIDv4(), %(p)s, 0, 1 FROM numbers({TRACES})",
        {"p": outside},
    )
    (trace_id,) = client.execute(f"SELECT id FROM {table} LIMIT 1")[0]
    sql = navigation._OUTSIDE_COPY_QUERY.replace("FROM traces", f"FROM {table}")
    params = {"outside_project_ids": (outside,), "trace_id": str(trace_id)}
    assert client.execute(sql, params) == [(1,)]
    selected, total, plan = _selected_granules(client, table, sql, params)
    assert total >= TRACES // GRANULARITY
    assert selected <= 2, plan
