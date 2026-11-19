"""Each multi-statement reader resolves its trace-name roots once per request.

On the deployed ClickHouse 25.3 schema, in collector order (spans only, no
stored trace name on a child): a span list that classifies its candidates in
several batches, a span graph over several partitions and a Users walk over
several slices each run ONE root lookup, bind its set into every later
statement, and answer exactly what the per-statement lookups answered.
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse import exact_graph_reads
from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests._users_live_ch import LiveExecutor, lane_client

pytestmark = pytest.mark.integration

START = datetime(2026, 7, 14, tzinfo=UTC)
END = START + timedelta(days=1)
PROJECT = str(uuid.uuid4())
NAME = "checkout"
# 240 traces of a root and two children; every 40th root is named NAME. The
# span list must classify all 720 candidates (200 a batch) to fill its page.
TRACES = 240
EVERY = 40
ROOT_LOOKUP = "FROM spans FINAL PREWHERE"
RESOLUTION = "SELECT DISTINCT project_id, trace_id FROM ("


def _leaf(value=NAME, op="equals"):
    return {
        "column_id": "trace_name",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }


def _date():
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


@pytest.fixture(scope="module")
def ch():
    with lane_client() as client:
        columns = (
            "project_id, observation_type, service_name, start_time, trace_id, "
            "id, parent_span_id, name"
        )
        rows = []
        for n in range(TRACES):
            trace = f"{n:032x}"
            root = uuid.uuid4().hex
            # Roots in an earlier storage hour than their children.
            root_at = START + timedelta(minutes=5 * n)
            rows.append(
                (
                    uuid.UUID(PROJECT),
                    "span",
                    "svc",
                    root_at,
                    trace,
                    root,
                    "",
                    NAME if n % EVERY == 0 else "browse",
                )
            )
            for child in range(2):
                rows.append(
                    (
                        uuid.UUID(PROJECT),
                        "span",
                        "svc",
                        root_at + timedelta(hours=1, minutes=child),
                        trace,
                        uuid.uuid4().hex,
                        root,
                        "step",
                    )
                )
        client.execute(f"INSERT INTO spans ({columns}) VALUES", rows)
        yield client


def _span_page(ch, *, resolve_once: bool, max_query_count=None):
    executor = LiveExecutor(ch)
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=[_date(), _leaf()], page_size=50
    )
    patch = (
        mock.patch.object(
            SpanListQueryBuilderV2,
            "prepare_trace_name_root_sets",
            lambda self, execute: None,
        )
        if not resolve_once
        else nullcontext()
    )
    with patch:
        page = read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_date(), _leaf()],
            key_field="id",
            page_number=0,
            page_size=50,
            deadline_ms=60_000,
            max_query_count=max_query_count,
        )
    return page, executor.statements


def _classifiers(statements):
    return [sql for sql in statements if "resolved_annotation_candidates" in sql]


def test_span_list_resolves_roots_once_and_answers_the_same(ch):
    page, statements = _span_page(ch, resolve_once=True)

    assert page.complete
    ids = sorted(str(row["id"]) for row in page.rows)
    # 6 matching traces x 3 spans.
    assert len(ids) == 3 * (TRACES // EVERY)
    classifiers = _classifiers(statements)
    assert len(classifiers) >= 3
    # One resolution statement; no classifier batch reads roots of its own.
    assert sum(sql.lstrip().startswith(RESOLUTION) for sql in statements) == 1
    assert sum(ROOT_LOOKUP in sql for sql in statements) == 1
    assert not any(ROOT_LOOKUP in sql for sql in classifiers)

    # Per statement (the behaviour before): every batch looked them up again,
    # and the page was the same.
    old_page, old_statements = _span_page(ch, resolve_once=False)
    assert old_page.complete
    assert ids == sorted(str(row["id"]) for row in old_page.rows)
    old_classifiers = _classifiers(old_statements)
    assert len(old_classifiers) == len(classifiers)
    assert all(ROOT_LOOKUP in sql for sql in old_classifiers)


def test_span_list_too_broad_root_set_is_refused(ch):
    from tracer.services.clickhouse.trace_name_roots import TraceNameFilterTooBroad

    executor = LiveExecutor(ch)
    leaf = _leaf("o", op="contains")  # browse and checkout: all 240 roots
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=[_date(), leaf], page_size=50
    )
    with (
        mock.patch(
            "tracer.services.clickhouse.trace_name_roots.TRACE_NAME_ROOT_SET_MAX_ROOTS",
            100,
        ),
        pytest.raises(TraceNameFilterTooBroad),
    ):
        read_bounded_filter_page(
            builder=builder,
            analytics=executor,
            filters=[_date(), leaf],
            key_field="id",
            page_number=0,
            page_size=50,
            deadline_ms=60_000,
        )
    # Refused after the one resolution, before any batch bound a set.
    assert sum(sql.lstrip().startswith(RESOLUTION) for sql in executor.statements) == 1


def _graph(ch, *, resolve_once: bool):
    executor = LiveExecutor(ch)
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=[_leaf()],
        interval="hour",
        observe_type="span",
        start_date=START,
        end_date=END,
        exact_snapshot=True,
        resolve_span_versions=True,
    )
    plan = exact_graph_reads.compile_exact_graph_row_predicates(
        [_leaf()], project_id=PROJECT, observe_type="span"
    )
    patch = (
        mock.patch.object(exact_graph_reads, "has_trace_name_leaf", lambda f: False)
        if not resolve_once
        else nullcontext()
    )
    with (
        patch,
        # Several partitions: no growth past the first hour.
        mock.patch.object(
            exact_graph_reads, "EXACT_GRAPH_SPAN_GROW_BELOW_QUERY_MS", -1
        ),
        mock.patch.object(
            exact_graph_reads,
            "EXACT_GRAPH_SPAN_PARTITION_WIDTH",
            timedelta(hours=6),
        ),
    ):
        metrics, query_count, _rows = exact_graph_reads._read_exact_filtered_span_graph(
            analytics=executor,
            builder=builder,
            exact_filter_plan=plan,
            start_date=START,
            end_date=END,
            started=exact_graph_reads.monotonic(),
        )
    return metrics, query_count, executor.statements


def test_span_graph_resolves_roots_once_and_answers_the_same(ch):
    metrics, query_count, statements = _graph(ch, resolve_once=True)

    traffic = sum(point["traffic"] for point in metrics["traffic"])
    assert traffic == 3 * (TRACES // EVERY)
    partitions = [sql for sql in statements if not sql.lstrip().startswith(RESOLUTION)]
    assert len(partitions) == 4
    assert sum(sql.lstrip().startswith(RESOLUTION) for sql in statements) == 1
    assert not any(ROOT_LOOKUP in sql for sql in partitions)

    old_metrics, old_count, old_statements = _graph(ch, resolve_once=False)
    assert metrics == old_metrics
    assert len(old_statements) == 4
    assert all(ROOT_LOOKUP in sql for sql in old_statements)
    assert query_count == old_count + 1


def test_root_resolution_never_takes_a_statement_from_the_page_budget(ch):
    """The resolution replaces a lookup every batch repeated: with exactly the
    statement budget the per-statement page needed, the page still completes."""

    old_page, old_statements = _span_page(ch, resolve_once=False)
    assert old_page.complete
    page, statements = _span_page(
        ch, resolve_once=True, max_query_count=len(old_statements)
    )
    assert page.complete, page.error_code
    assert len(statements) == len(old_statements) + 1
    assert sorted(str(r["id"]) for r in page.rows) == sorted(
        str(r["id"]) for r in old_page.rows
    )


@pytest.mark.parametrize("project_count", [1, 2])
def test_cap_sized_repeated_leaves_refuse_before_parser_overflow(ch, project_count):
    from tracer.services.clickhouse.trace_name_roots import TraceNameFilterTooBroad

    projects = [str(uuid.uuid4()) for _ in range(project_count)]
    rows = []
    candidates = []
    for n in range(2048):
        project = projects[n % project_count]
        trace = f"{n:032x}"
        span = uuid.uuid4().hex
        rows.append((uuid.UUID(project), "span", "svc", START, trace, span, "", NAME))
        if n < 200:
            candidates.append(
                {
                    "project_id": project,
                    "id": span,
                    "trace_id": trace,
                    "start_time": START,
                    "observation_type": "span",
                    "service_name": "svc",
                }
            )
    ch.execute(
        "INSERT INTO spans (project_id, observation_type, service_name, start_time, "
        "trace_id, id, parent_span_id, name) VALUES",
        rows,
    )
    executor = LiveExecutor(ch)
    single = SpanListQueryBuilderV2(
        project_ids=projects, filters=[_date(), _leaf()], bounded_identity_only=True
    )
    single.prepare_trace_name_root_sets(
        lambda sql, params: executor.execute_ch_query(sql, params).data
    )
    sql, params = single.build_filter_match_query_from_seed_rows(candidates)
    # Actual client rendering and execution at the existing cap: no placeholders
    # or raised max_query_size can hide an oversized classifier.
    rendered = ch.substitute_params(sql, params, ch.connection.context)
    assert len(rendered.encode("utf-8")) < 262144
    result, columns = ch.execute(
        sql, params, settings={"max_query_size": 262144}, with_column_types=True
    )
    names = [name for name, _type in columns]
    matched = [dict(zip(names, row, strict=True)) for row in result]
    assert len(matched) == 200
    assert {
        (str(row["project_id"]), row["id"], row["trace_id"]) for row in matched
    } == {(row["project_id"], row["id"], row["trace_id"]) for row in candidates}

    repeated = SpanListQueryBuilderV2(
        project_ids=projects,
        filters=[_date(), *[_leaf() for _ in range(4)]],
        bounded_identity_only=True,
    )
    before = len(executor.statements)
    with pytest.raises(TraceNameFilterTooBroad) as refused:
        repeated.prepare_trace_name_root_sets(
            lambda sql, params: executor.execute_ch_query(sql, params).data
        )
    assert refused.value.code == "trace_name_filter_too_broad"
    assert len(executor.statements) == before + 1
    assert executor.statements[-1].startswith(RESOLUTION)


def test_repeated_nested_boolean_binding_keeps_org_project_pairs(ch):
    from tracer.services.clickhouse.trace_name_roots import (
        bind_trace_name_root_sets,
        resolve_trace_name_root_sets,
    )
    from tracer.services.clickhouse.v2.query_builders.filters import (
        ClickHouseFilterBuilderV2,
    )

    projects = [str(uuid.uuid4()), str(uuid.uuid4())]
    shared_traces = [uuid.uuid4().hex, uuid.uuid4().hex]
    rows = [
        (
            uuid.UUID(project),
            "span",
            "svc",
            START,
            trace,
            uuid.uuid4().hex,
            "",
            NAME if p == t else "browse",
        )
        for p, project in enumerate(projects)
        for t, trace in enumerate(shared_traces)
    ]
    ch.execute(
        "INSERT INTO spans (project_id, observation_type, service_name, start_time, "
        "trace_id, id, parent_span_id, name) VALUES",
        rows,
    )
    predicates = []
    params = {"project_ids": projects, "start_date": START, "end_date": END}
    for index, leaf in enumerate([_leaf(), _leaf("browse")]):
        predicate, leaf_params = ClickHouseFilterBuilderV2(
            project_ids=projects, query_mode="span"
        ).translate([leaf])
        # Each independent compiler starts its parameter counter at zero.
        for key, value in leaf_params.items():
            renamed = f"leaf{index}_{key}"
            predicate = predicate.replace(f"%({key})s", f"%({renamed})s")
            params[renamed] = value
        predicates.append(predicate)
    first, second = predicates
    sql = (
        "SELECT project_id, trace_id FROM spans FINAL "
        "WHERE project_id IN %(project_ids)s AND "
        f"(({first} AND NOT ({second})) OR ({second} AND NOT ({first})))"
    )
    expected = set(ch.execute(sql, params))
    assert len(expected) == 4
    sets = resolve_trace_name_root_sets(
        [_leaf(), _leaf("browse")],
        start=START,
        end=END,
        project_ids=projects,
        execute=lambda query, values: ch.execute(query, values),
    )
    bound, root_params = bind_trace_name_root_sets(sql, sets)
    assert ROOT_LOOKUP not in bound
    assert set(ch.execute(bound, {**params, **root_params})) == expected
