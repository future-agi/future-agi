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


def test_configured_live_matches_the_rule_before_sources_were_recorded(
    trace_table, monkeypatch
):
    """TH-4804 r9: an older eval log recorded no project, so Usage keeps the
    rule it had before (``latest_live_trace_projects_sql`` over the projects
    running the eval), limited to readable projects. ``configured_live`` must
    agree with that relation trace by trace, from the same pruned scan of the
    readable projects only."""
    client, table = trace_table
    configured_a, configured_b, readable_c, unreadable = (uuid.uuid4() for _ in "abcd")
    client.execute(
        f"INSERT INTO {table} SELECT generateUUIDv4(), %(p)s, 0, 1 FROM numbers({TRACES})",
        {"p": configured_a},
    )
    t = {
        name: uuid.uuid4()
        for name in "only_a both_ab tomb_a a_and_c tomb_a_live_b only_c a_and_unreadable tomb_then_live".split()
    }
    client.execute(
        f"INSERT INTO {table} VALUES",
        [
            (t["only_a"], configured_a, 0, 1),
            (t["both_ab"], configured_a, 0, 1),
            (t["both_ab"], configured_b, 0, 1),
            (t["tomb_a"], configured_a, 0, 1),
            (t["tomb_a"], configured_a, 1, 2),
            (t["a_and_c"], configured_a, 0, 1),
            (t["a_and_c"], readable_c, 0, 1),
            (t["tomb_a_live_b"], configured_a, 1, 2),
            (t["tomb_a_live_b"], configured_b, 0, 1),
            (t["only_c"], readable_c, 0, 1),
            (t["a_and_unreadable"], configured_a, 0, 1),
            (t["a_and_unreadable"], unreadable, 0, 1),
            (t["tomb_then_live"], configured_b, 1, 1),
            (t["tomb_then_live"], configured_b, 0, 2),
        ],
    )
    # No OPTIMIZE: both relations must collapse versions themselves.
    monkeypatch.setattr(trace_project_scope, "_TRACE_TABLE", table)
    candidates = "SELECT toUUID(arrayJoin(%(candidate_ids)s)) AS trace_id"
    params = {
        "project_ids": (configured_a, configured_b, readable_c),
        "configured_project_ids": (configured_a, configured_b),
        "candidate_ids": [str(trace) for trace in t.values()],
    }
    sql = trace_project_scope.live_trace_project_sets_sql(
        candidate_trace_ids_sql=candidates,
        configured_project_ids_param="configured_project_ids",
    )
    listed = {
        trace for trace, _projects, live in client.execute(sql, params) if live == 1
    }
    before = trace_project_scope.latest_live_trace_projects_sql(
        candidate_trace_ids_sql=candidates,
        project_ids_param="configured_project_ids",
    )
    expected = {trace for trace, _project in client.execute(before, params)}
    assert (
        listed
        == expected
        == {
            t["only_a"],
            t["a_and_c"],
            t["a_and_unreadable"],
            t["tomb_then_live"],
        }
    )
    # The scan reads the readable projects only, pruned by the candidates.
    assert "%(project_ids)s" in sql.split("GROUP BY")[0]
    selected, total, plan = _selected_granules(client, table, sql, params)
    assert total >= TRACES // GRANULARITY
    # At most one granule per candidate (plus the unmerged small parts).
    assert selected <= len(t) + 2, plan
