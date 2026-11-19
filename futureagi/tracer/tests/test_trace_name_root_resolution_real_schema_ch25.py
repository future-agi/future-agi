"""Collector-order root names on the deployed CH25 schema (no dictionary reload)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tracer.services.clickhouse import cluster_rca_spans
from tracer.services.clickhouse.trace_name_sql import TRACE_NAME_SQL
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.observe_usage import (
    ObserveTraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests._users_live_ch import lane_client


@pytest.fixture(scope="module")
def seeded_roots():
    with lane_client() as client:
        project = uuid.uuid4()
        now = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=4)
        traces = {key: str(uuid.uuid4()) for key in ("alpha", "zeta", "demo")}
        rows = [
            ("alpha", "Alpha Sunrise", None),
            ("zeta", "Zeta Moonlight", None),
            ("demo", "SQL Agent Executor", "SQL Agent Trace"),
        ]
        for key, name, explicit in rows:
            root = f"root-{uuid.uuid4()}"
            columns = "project_id, trace_id, id, parent_span_id, name, start_time, observation_type, service_name"
            values = (project, traces[key], root, "", name, now, "agent", "reader-test")
            # A pre-039 collector insert before traces persisted the empty
            # dictionary result. Reproduce that historic physical row on the
            # new lane schema (which otherwise computes the name at INSERT).
            # The demo instead writes its explicit stored presentation name.
            columns += ", trace_name"
            values += (explicit or "",)
            client.execute(
                f"INSERT INTO spans ({columns}) VALUES",
                [values],
                settings={"insert_allow_materialized_columns": 1},
            )
            client.execute(
                "INSERT INTO spans (project_id, trace_id, id, parent_span_id, name, "
                "start_time, observation_type, service_name) VALUES",
                [
                    (
                        project,
                        traces[key],
                        f"child-{uuid.uuid4()}",
                        root,
                        "llm.call",
                        now + timedelta(seconds=1),
                        "llm",
                        "reader-test",
                    )
                ],
            )
        client.execute(
            "INSERT INTO traces (id, project_id, name, created_at) VALUES",
            [(uuid.UUID(traces[key]), project, name, now) for key, name, _ in rows],
        )
        yield client, str(project), traces


def test_collector_order_trace_list_search_finds_root_name(seeded_roots):
    client, project, traces = seeded_roots
    # build_id_query compiles the same root search as the (guarded) unbounded
    # builder; the normal UI list routes search through bounded root predicates.
    builder = TraceListQueryBuilderV2(project_id=project, search="Sunri")
    sql, params = builder.build_id_query()
    found = [r[0] for r in client.execute(sql, params)]
    assert found == [traces["alpha"]]
    # The public search path uses this finite latest-state classifier, not
    # build_id_query's legacy root scan. Its predicate comes from the filters
    # lane; search must match a root whose stored name is still blank.
    bounded_sql, bounded_params = builder.build_filter_match_query(
        list(traces.values())
    )
    matching = client.execute(bounded_sql, bounded_params, with_column_types=True)[0]
    assert [row[1] for row in matching] == [traces["alpha"]]


def test_sort_by_trace_name_orders_resolved_root_names(seeded_roots):
    client, project, traces = seeded_roots
    builder = TraceListQueryBuilderV2(
        project_id=project,
        sort_params=[{"column_id": "name", "direction": "asc"}],
    )
    # Public custom sorting currently rejects unsupported_filter_modifiers;
    # exercise the actual allowlisted ORDER BY expression on the finite lane
    # corpus without widening the public scan.
    with pytest.raises(ValueError, match="unsupported_filter_modifiers"):
        builder.build()
    order = ClickHouseFilterBuilderV2().translate_sort(
        builder.sort_params, field_map=builder.SORT_FIELD_MAP
    )
    results = client.execute(
        f"SELECT trace_id, {TRACE_NAME_SQL} AS resolved_name FROM spans WHERE project_id = %(pid)s "
        f"AND parent_span_id = '' {order} LIMIT 3",
        {"pid": project},
    )
    ordered = [row[0] for row in results if row[0] in traces.values()]
    assert ordered == [traces["alpha"], traces["demo"], traces["zeta"]]
    assert {row[0]: row[1] for row in results if row[0] in traces.values()} == {
        traces["alpha"]: "Alpha Sunrise",
        traces["demo"]: "SQL Agent Trace",
        traces["zeta"]: "Zeta Moonlight",
    }
    # The actual v2 page hydrator replays a finite physical root identity.
    identities = client.execute(
        "SELECT toString(project_id), trace_id, id, "
        "toUnixTimestamp64Micro(start_time), observation_type, service_name, "
        "toUnixTimestamp(toStartOfHour(start_time)) * 1000000, _version "
        "FROM spans WHERE project_id = %(pid)s AND parent_span_id = ''",
        {"pid": project},
    )
    sql, params = builder._root_replay_query(identities, content=False)
    hydrated, columns = client.execute(sql, params, with_column_types=True)
    names = [key for key, _type in columns]
    assert {
        row[names.index("trace_id")]: row[names.index("trace_name")] for row in hydrated
    } == {
        traces["alpha"]: "Alpha Sunrise",
        traces["demo"]: "SQL Agent Trace",
        traces["zeta"]: "Zeta Moonlight",
    }


def test_cluster_rca_reads_resolve_root_and_not_child_name(seeded_roots):
    client, project, traces = seeded_roots
    with patch.object(
        cluster_rca_spans,
        "_execute_read",
        side_effect=lambda sql, params: client.execute(sql, params),
    ):
        by_trace = cluster_rca_spans.trace_roots(
            project, [traces["alpha"], traces["demo"]]
        )
    assert by_trace[traces["alpha"]]["trace_name"] == "Alpha Sunrise"
    assert by_trace[traces["demo"]]["trace_name"] == "SQL Agent Trace"
    rows = client.execute(
        f"SELECT {', '.join(cluster_rca_spans._SPAN_COLS)} FROM spans "
        "WHERE project_id = %(pid)s AND trace_id = %(tid)s "
        "ORDER BY parent_span_id",
        {"pid": project, "tid": traces["alpha"]},
    )
    keys = cluster_rca_spans._AGENT_SPAN_KEYS
    assert [row[keys.index("trace_name")] for row in rows] == ["Alpha Sunrise", ""]


def test_observe_usage_sort_displays_resolved_root_name(seeded_roots):
    client, project, traces = seeded_roots
    builder = ObserveTraceListQueryBuilderV2(project_id=project)
    sql, params = builder.build_usage_sorted_query(
        [{"column_id": "cost", "direction": "asc"}]
    )
    rows, columns = client.execute(sql, params, with_column_types=True)
    names = [key for key, _type in columns]
    actual = {
        row[names.index("trace_id")]: row[names.index("trace_name")]
        for row in rows
        if row[names.index("trace_id")] in traces.values()
    }
    assert actual == {
        traces["alpha"]: "Alpha Sunrise",
        traces["demo"]: "SQL Agent Trace",
        traces["zeta"]: "Zeta Moonlight",
    }


def test_explicit_root_name_wins_on_display_search_and_sort(seeded_roots):
    client, project, traces = seeded_roots
    # A demo write may explicitly set a materialized column; old stored names
    # behave identically. A child must not impersonate its trace's root name.
    assert client.execute(
        "SELECT name, trace_name FROM spans WHERE project_id = %(project)s "
        "AND trace_id = %(trace)s ORDER BY parent_span_id",
        {"project": project, "trace": traces["demo"]},
    ) == [("SQL Agent Executor", "SQL Agent Trace"), ("llm.call", "")]
    builder = TraceListQueryBuilderV2(project_id=project, search="Agent Trace")
    sql, params = builder.build_id_query()
    assert [row[0] for row in client.execute(sql, params)] == [traces["demo"]]
    builder = TraceListQueryBuilderV2(
        project_id=project,
        sort_params=[{"column_id": "trace_name", "direction": "asc"}],
    )
    order = ClickHouseFilterBuilderV2().translate_sort(
        builder.sort_params, field_map=builder.SORT_FIELD_MAP
    )
    assert [
        row[0]
        for row in client.execute(
            f"SELECT trace_id FROM spans WHERE project_id = %(pid)s "
            f"AND parent_span_id = '' {order} LIMIT 3",
            {"pid": project},
        )
    ] == [traces["alpha"], traces["demo"], traces["zeta"]]
