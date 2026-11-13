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
