"""Trace-name roots resolved once per request, then bound per statement.

A span-mode trace_name leaf compiles to a tagged root lookup over ``spans
FINAL``. A reader with many statements per request (span-list classifier
batches, span graph partitions, Users walk slices and certifications)
resolves each leaf's roots ONCE (``resolve_trace_name_root_sets``) and binds
the set into every statement (``bind_trace_name_root_sets``):

- one resolution statement per distinct leaf, capped, refused above the cap;
- every reader's statement binds the set and reads no roots of its own;
- a statement carrying a set at the cap still parses (max_query_size).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse import trace_name_roots
from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.trace_name_roots import (
    TraceNameFilterTooBroad,
    bind_trace_name_root_sets,
    resolve_trace_name_root_sets,
    tagged_trace_name_root_lookups,
)
from tracer.services.clickhouse.trace_name_sql import (
    TRACE_NAME_ROOT_SET_MAX_BYTES,
    TRACE_NAME_ROOT_SET_MAX_ROOTS,
    trace_name_root_leaf_key,
    trace_name_root_set_rendered_bytes,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT = "00000000-0000-4000-8000-0000000000b1"
OTHER = "00000000-0000-4000-8000-0000000000b2"
ORG = "00000000-0000-4000-8000-0000000000b3"
START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(days=30)
ROOT_LOOKUP = "FROM spans FINAL PREWHERE"
PLACEHOLDER = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")
DRIVER_CONTEXT = SimpleNamespace(
    server_info=SimpleNamespace(get_timezone=lambda: "UTC")
)
# ClickHouse's default max_query_size: a longer statement never runs.
MAX_QUERY_SIZE = 262_144


def _leaf(value="checkout", op="equals"):
    return {
        "column_id": "trace_name",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }


def _model():
    return {
        "column_id": "model",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "gpt-4o",
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


def _key(leaf):
    config = leaf["filter_config"]
    return trace_name_root_leaf_key(
        config["filter_type"], config["filter_op"], config["filter_value"]
    )


def _render(sql, params):
    missing = set(PLACEHOLDER.findall(sql)) - set(params)
    assert not missing, f"unbound placeholders {sorted(missing)}"
    return sql % escape_params(params, context=DRIVER_CONTEXT)


def _full_set(leaf, projects=(PROJECT,)):
    """A set at the root cap: 32-hex OTel trace ids spread over ``projects``."""

    per_project = TRACE_NAME_ROOT_SET_MAX_ROOTS // len(projects)
    return {
        _key(leaf): tuple(
            (project, tuple(f"{p:02x}{n:030x}" for n in range(per_project)))
            for p, project in enumerate(projects)
        )
    }


class Recorder:
    def __init__(self, rows=(("p", "t"),)):
        self.rows = list(rows)
        self.calls = []

    def __call__(self, sql, params):
        self.calls.append((sql, params))
        return self.rows


# -- tag, scan, bind ---------------------------------------------------------


def test_a_span_leaf_lookup_is_tagged_with_its_leaf_key():
    builder = ClickHouseFilterBuilderV2(project_id=PROJECT, query_mode="span")
    predicate, _params = builder.translate([_leaf(), _model()])
    ((key, start, end, subquery),) = tagged_trace_name_root_lookups(predicate)
    assert key == _key(_leaf())
    assert predicate[start:end].startswith("(project_id, trace_id) IN (SELECT")
    assert subquery.lstrip().startswith("SELECT project_id, trace_id FROM spans FINAL")
    # The model leaf is untouched and outside the lookup.
    assert "model" in predicate[end:]


def test_binding_replaces_the_lookup_with_the_resolved_pairs():
    builder = ClickHouseFilterBuilderV2(project_ids=[PROJECT, OTHER], query_mode="span")
    predicate, params = builder.translate([_leaf(), _model()])
    sql = f"SELECT 1 WHERE {predicate} OR NOT ({predicate})"
    roots = {_key(_leaf()): ((PROJECT, ("t1", "t2")), (OTHER, ("t1",)))}

    bound, root_params = bind_trace_name_root_sets(sql, roots)

    assert ROOT_LOOKUP not in bound
    assert bound.count("(project_id = %(") == 4
    rendered = _render(bound, {**params, **root_params})
    # Pairs stay tenant-correlated: the same trace id in two projects.
    assert f"(project_id = '{PROJECT}' AND trace_id IN ('t1', 't2'))" in rendered
    assert f"(project_id = '{OTHER}' AND trace_id IN ('t1'))" in rendered


def test_an_empty_set_matches_nothing_and_an_unresolved_leaf_keeps_its_lookup():
    builder = ClickHouseFilterBuilderV2(project_id=PROJECT, query_mode="span")
    first, _ = builder.translate([_leaf("checkout")])
    second, _ = builder.translate([_leaf("browse")])
    sql = f"{first} AND {second}"

    bound, root_params = bind_trace_name_root_sets(sql, {_key(_leaf("checkout")): ()})

    assert bound.startswith("(0 = 1) AND (project_id, trace_id) IN (SELECT")
    assert bound.count(ROOT_LOOKUP) == 1
    assert root_params == {}
    assert bind_trace_name_root_sets(sql, None) == (sql, {})


def test_the_scanner_skips_parentheses_inside_quoted_text():
    key = "0123456789abcdef"
    lookup = (
        "(project_id, trace_id) IN (SELECT project_id, trace_id FROM spans FINAL "
        "PREWHERE project_id = %(project_id)s WHERE name != ')(' AND "
        f"(x = 'a\\')') /* trace-name roots {key} */)"
    )
    sql = f"SELECT 1 WHERE {lookup} AND y = 1"
    ((found, start, end, _subquery),) = tagged_trace_name_root_lookups(sql)
    assert found == key and sql[start:end] == lookup
    bound, _ = bind_trace_name_root_sets(sql, {key: ()})
    assert bound == "SELECT 1 WHERE (0 = 1) AND y = 1"


# -- resolution ---------------------------------------------------------------


def test_one_resolution_statement_per_distinct_leaf():
    execute = Recorder([(PROJECT, "t2"), (PROJECT, "t1"), (PROJECT, "t1")])
    sets = resolve_trace_name_root_sets(
        [_date(), _leaf(), _model(), _leaf(), _leaf("browse")],
        start=START,
        end=END,
        execute=execute,
        project_id=PROJECT,
    )
    assert len(execute.calls) == 2
    assert set(sets) == {_key(_leaf()), _key(_leaf("browse"))}
    assert sets[_key(_leaf())] == ((PROJECT, ("t1", "t2")),)
    sql, params = execute.calls[0]
    assert sql.startswith("SELECT DISTINCT project_id, trace_id FROM (SELECT")
    assert sql.endswith(f"LIMIT {TRACE_NAME_ROOT_SET_MAX_ROOTS + 1}")
    # The request window and the reader's own project scope.
    assert params["start_date"] == START and params["end_date"] == END
    assert params["project_id"] == PROJECT
    assert "project_id = %(project_id)s" in sql
    _render(sql, params)


def test_an_organization_read_resolves_over_its_project_set():
    execute = Recorder([{"project_id": OTHER, "trace_id": "t9"}])
    sets = resolve_trace_name_root_sets(
        [_leaf()],
        start=START,
        end=END,
        execute=execute,
        project_ids=[PROJECT, OTHER],
    )
    ((sql, params),) = execute.calls
    assert "project_id IN %(project_ids)s" in sql
    assert params["project_ids"] == [PROJECT, OTHER]
    assert sets == {_key(_leaf()): ((OTHER, ("t9",)),)}


def test_filters_without_a_trace_name_leaf_resolve_nothing():
    execute = Recorder()
    assert (
        resolve_trace_name_root_sets(
            [_date(), _model()],
            start=START,
            end=END,
            execute=execute,
            project_id=PROJECT,
        )
        == {}
    )
    assert execute.calls == []


def test_more_roots_than_the_cap_are_refused_as_too_broad():
    at_cap = Recorder(
        [(PROJECT, f"t{n}") for n in range(TRACE_NAME_ROOT_SET_MAX_ROOTS)]
    )
    sets = resolve_trace_name_root_sets(
        [_leaf()], start=START, end=END, execute=at_cap, project_id=PROJECT
    )
    assert len(sets[_key(_leaf())][0][1]) == TRACE_NAME_ROOT_SET_MAX_ROOTS

    over = Recorder(
        [(PROJECT, f"t{n}") for n in range(TRACE_NAME_ROOT_SET_MAX_ROOTS + 1)]
    )
    with pytest.raises(TraceNameFilterTooBroad) as refused:
        resolve_trace_name_root_sets(
            [_leaf()], start=START, end=END, execute=over, project_id=PROJECT
        )
    assert refused.value.code == "trace_name_filter_too_broad"
    assert "more than 2,048 traces" in str(refused.value)


def test_long_trace_ids_are_refused_by_the_byte_cap():
    long_ids = Recorder(
        [
            (PROJECT, f"{n:04d}" + "x" * 1_000)
            for n in range(1 + TRACE_NAME_ROOT_SET_MAX_BYTES // 1_000)
        ]
    )
    with pytest.raises(TraceNameFilterTooBroad):
        resolve_trace_name_root_sets(
            [_leaf()], start=START, end=END, execute=long_ids, project_id=PROJECT
        )


# -- every reader binds the set, and still parses at the cap -------------------


def _span_classifier(sets, *, project_ids=None):
    builder = SpanListQueryBuilderV2(
        project_id=None if project_ids else PROJECT,
        project_ids=project_ids,
        filters=[_date(), _leaf(), _model()],
        bounded_identity_only=True,
    )
    if sets is not None:
        builder.prepare_trace_name_root_sets(lambda sql, params: [])
        builder._trace_name_root_sets = sets
    rows = [
        {
            "project_id": (project_ids or [PROJECT])[n % len(project_ids or [PROJECT])],
            "id": f"{n:032x}",
            "trace_id": f"{n:032x}",
            "start_time": START + timedelta(minutes=n),
            "observation_type": "span",
            "service_name": "svc",
        }
        for n in range(200)
    ]
    return builder.build_filter_match_query_from_seed_rows(rows)


def _graph_partition(sets):
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=[_leaf(), _model()],
        interval="hour",
        observe_type="span",
        start_date=START,
        end_date=END,
        exact_snapshot=True,
        resolve_span_versions=True,
    )
    builder.trace_name_root_sets = sets
    return builder.build_exact_span_partition(
        partition_start=START + timedelta(days=3),
        partition_end=START + timedelta(days=3, hours=1),
    )


def _users_builder(sets, project_ids=(PROJECT,)):
    builder = UserListQueryBuilderV2(
        organization_id=ORG,
        project_ids=list(project_ids),
        filters=[_date(), _leaf(), _model()],
    )
    builder.walk_witness = next(
        w for w in builder.matching_activity_witnesses() if w.family == "native"
    )
    builder.trace_name_root_sets = sets
    return builder


def _users_statements(sets, project_ids=(PROJECT,)):
    builder = _users_builder(sets, project_ids)
    leaves = [(1, _leaf()), (2, _model())]
    return {
        "slice": builder.build_matching_activity_slice_query(
            slice_start=START + timedelta(days=1),
            slice_end=START + timedelta(days=1, hours=1),
            limit=200,
        ),
        "instant": builder.build_matching_activity_instant_query(
            instant=START + timedelta(days=1), limit=200
        ),
        "existence": builder.build_matching_activity_existence_query(
            range_start=START, range_end=END
        ),
        "estimate": builder.build_matching_activity_existence_estimate_query(
            range_start=START, range_end=END
        ),
        "certification": builder.build_native_span_dimension_query(
            [f"00000000-0000-4000-8000-{n:012x}" for n in range(25)], leaves
        ),
    }


def _users_statement(name, sets):
    return _users_statements(sets)[name]


READERS = {
    "span-classifier": _span_classifier,
    "graph-partition": _graph_partition,
    **{
        f"users-{name}": partial(_users_statement, name)
        for name in ("slice", "instant", "existence", "estimate", "certification")
    },
}


@pytest.mark.parametrize("reader", sorted(READERS))
def test_every_reader_binds_the_resolved_set_and_still_parses_at_the_cap(reader):
    build = READERS[reader]
    unbound_sql, unbound_params = build(None)
    assert ROOT_LOOKUP in unbound_sql

    sql, params = build(_full_set(_leaf()))

    assert ROOT_LOOKUP not in sql
    assert "trace_name_roots_" in sql
    rendered = _render(sql, params)
    assert len(rendered.encode("utf-8")) < MAX_QUERY_SIZE, len(rendered)


def test_an_organization_classifier_binds_one_branch_per_project():
    projects = [PROJECT, OTHER]
    sql, params = _span_classifier(
        _full_set(_leaf(), projects=projects), project_ids=projects
    )
    assert ROOT_LOOKUP not in sql
    rendered = _render(sql, params)
    assert len(rendered.encode("utf-8")) < MAX_QUERY_SIZE, len(rendered)
    assert f"project_id = '{OTHER}' AND trace_id IN (" in rendered


def test_a_span_list_prepares_once_and_binds_only_request_window_batches():
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=[_date(), _leaf()], bounded_identity_only=True
    )
    execute = Recorder([(PROJECT, "t1")])
    builder.prepare_trace_name_root_sets(execute)
    builder.prepare_trace_name_root_sets(execute)
    assert len(execute.calls) == 1
    window_sql, _ = builder.build_filter_match_query(["a" * 32])
    assert ROOT_LOOKUP not in window_sql
    # Full-state (windowless) classification keeps its candidate-bounded lookup.
    builder.filters = [_leaf()]
    full_sql, _ = builder.build_filter_match_query(
        ["a" * 32], candidate_full_state=True
    )
    assert ROOT_LOOKUP in full_sql


def test_a_span_list_without_a_trace_name_leaf_resolves_nothing():
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=[_date(), _model()], bounded_identity_only=True
    )
    execute = Recorder()
    builder.prepare_trace_name_root_sets(execute)
    assert execute.calls == []


def test_the_cap_is_the_documented_message(monkeypatch):
    from tfc.utils.error_codes import get_error_message

    assert TRACE_NAME_ROOT_SET_MAX_ROOTS == 2_048
    assert get_error_message("TRACE_NAME_FILTER_TOO_BROAD") == (
        trace_name_roots.TRACE_NAME_FILTER_TOO_BROAD_MESSAGE
    )


def test_a_span_list_without_a_time_filter_binds_every_batch_of_its_request():
    # The default window ends at ``now``, which moves between batches; the set
    # resolved for the request still serves each of them.
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=[_leaf()], bounded_identity_only=True
    )
    builder.prepare_trace_name_root_sets(Recorder([(PROJECT, "t1")]))
    for _batch in range(2):
        sql, params = builder.build_filter_match_query(["a" * 32])
        assert ROOT_LOOKUP not in sql
        _render(sql, params)
    # A different time filter is another window: its batches look roots up.
    builder.filters = [_date(), _leaf()]
    sql, _ = builder.build_filter_match_query(["a" * 32])
    assert ROOT_LOOKUP in sql


def test_the_annotation_span_graph_resolves_roots_once_for_all_its_batches(
    monkeypatch,
):
    from contextlib import nullcontext

    from tracer.services.clickhouse import exact_graph_reads as exact_module
    from tracer.tests.test_exact_aggregation_contract import _ScoreListManager

    class Analytics:
        def __init__(self):
            self.calls = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((query, params, settings))
            if query.startswith("SELECT DISTINCT project_id, trace_id FROM ("):
                return SimpleNamespace(data=[{"project_id": PROJECT, "trace_id": "t1"}])
            return SimpleNamespace(
                data=[
                    {"id": span_id, "identity_count": 1, "matched": 1}
                    for span_id in params["candidate_span_ids"]
                ]
            )

    scores = [
        {
            "trace_id": f"t{n}",
            "observation_span_id": f"span-{n}",
            "created_at": (START + timedelta(days=1, hours=n)).replace(tzinfo=None),
            "value": {"rating": 4},
        }
        for n in range(3)
    ]
    label = SimpleNamespace(name="quality", type="numeric")
    monkeypatch.setattr(
        exact_module,
        "Score",
        SimpleNamespace(no_workspace_objects=_ScoreListManager(scores)),
    )
    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: SimpleNamespace(get=lambda **_kwargs: label),
    )
    monkeypatch.setattr(
        exact_module, "_annotation_label_ids_for_filters", lambda *_a, **_k: []
    )
    monkeypatch.setattr(exact_module.transaction, "atomic", nullcontext)
    monkeypatch.setattr(exact_module, "connection", SimpleNamespace(vendor="sqlite"))
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_MEMBERSHIP_BATCH_SIZE", 1)
    analytics = Analytics()

    result = exact_module.read_exact_annotation_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=[_date(), _leaf()],
        interval="day",
        req_data_config={
            "id": "55555555-5555-4555-8555-555555555555",
            "output_type": "float",
        },
        observe_type="span",
    )

    statements = [query for query, _params, _settings in analytics.calls]
    assert len(statements) == 4
    assert statements[0].startswith("SELECT DISTINCT project_id, trace_id FROM (")
    assert sum(ROOT_LOOKUP in sql for sql in statements) == 1
    for query, params, _settings in analytics.calls[1:]:
        assert ROOT_LOOKUP not in query
        _render(query, params)
    # The resolution reads under the graph's own settings, one row over the cap.
    assert analytics.calls[0][2]["max_result_rows"] == TRACE_NAME_ROOT_SET_MAX_ROOTS + 1
    assert result["query_complete"] is True


def test_the_cap_holds_for_all_leaves_of_a_request_together():
    # Each leaf alone is under the cap; a statement binds them all.
    half = TRACE_NAME_ROOT_SET_MAX_ROOTS // 2 + 1

    def execute(sql, params):
        return [(PROJECT, f"{len(sql)}-{n:030x}") for n in range(half)]

    with pytest.raises(TraceNameFilterTooBroad):
        resolve_trace_name_root_sets(
            [_leaf("checkout"), _leaf("browse")],
            start=START,
            end=END,
            execute=execute,
            project_id=PROJECT,
        )


def test_a_statement_with_several_leaves_at_the_shared_cap_still_parses():
    leaves = [_leaf(f"name-{n}") for n in range(4)]
    per_leaf = TRACE_NAME_ROOT_SET_MAX_ROOTS // len(leaves)
    sets = {
        _key(leaf): ((PROJECT, tuple(f"{i:02x}{n:030x}" for n in range(per_leaf))),)
        for i, leaf in enumerate(leaves)
    }
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=leaves,
        interval="hour",
        observe_type="span",
        start_date=START,
        end_date=END,
        exact_snapshot=True,
        resolve_span_versions=True,
    )
    builder.trace_name_root_sets = sets
    sql, params = builder.build_exact_span_partition(
        partition_start=START + timedelta(days=3),
        partition_end=START + timedelta(days=3, hours=1),
    )
    assert ROOT_LOOKUP not in sql
    assert len(_render(sql, params).encode("utf-8")) < MAX_QUERY_SIZE


def test_the_users_walk_charges_one_statement_per_trace_name_leaf(monkeypatch):
    from tracer.services import users_matching_walk as walk

    taken = []

    class Budget:
        deadline = SimpleNamespace(remaining_ms=lambda: 1_000)
        exhausted_by = None

        def take(self, statements):
            taken.append(statements)
            return True

    builder = SimpleNamespace(project_ids=[PROJECT], project_id=None)
    manager = SimpleNamespace(
        filters=[_date(), _leaf("checkout"), _leaf("browse")],
        use_trace_name_root_sets=lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        walk, "resolve_trace_name_root_sets", lambda *_a, **_k: {"k": ()}
    )
    state = SimpleNamespace(
        manager=manager,
        builder=builder,
        budget=Budget(),
        window_start=START,
        window_end=END,
    )
    walk._resolve_walk_trace_name_roots(state)
    assert taken == [2]
    assert builder.trace_name_root_sets == {"k": ()}


@pytest.mark.parametrize("projects", [(PROJECT,), (PROJECT, OTHER)])
def test_repeated_leaves_at_root_cap_are_refused_before_classifier_dispatch(projects):
    sets = _full_set(_leaf(), projects=projects)
    rows = [
        (project, trace) for project, traces in sets[_key(_leaf())] for trace in traces
    ]
    execute = Recorder(rows)
    builder = SpanListQueryBuilderV2(
        project_ids=list(projects),
        filters=[_date(), *[_leaf() for _ in range(4)]],
        bounded_identity_only=True,
    )
    # Deduplicate reads, not the bytes of the four rendered occurrences.
    with pytest.raises(TraceNameFilterTooBroad) as refused:
        builder.prepare_trace_name_root_sets(execute)
    assert refused.value.code == "trace_name_filter_too_broad"
    assert len(execute.calls) == 1

    # Readers may duplicate a predicate independently of the request leaves.
    # Binding itself must refuse that expansion, even with pre-resolved sets.
    predicate, _ = ClickHouseFilterBuilderV2(
        project_ids=list(projects), query_mode="span"
    ).translate([_leaf()])
    sql = f"SELECT 1 WHERE ({predicate} AND {predicate}) OR NOT ({predicate} OR {predicate})"
    with pytest.raises(TraceNameFilterTooBroad):
        bind_trace_name_root_sets(sql, sets)


@pytest.mark.parametrize("projects", [(PROJECT,), (PROJECT, OTHER)])
def test_repeated_nested_predicates_preserve_boolean_and_project_correlation(projects):
    roots = tuple((project, ("same-trace", "quote'\\\\trace")) for project in projects)
    sets = {_key(_leaf()): roots}
    predicate, params = ClickHouseFilterBuilderV2(
        project_ids=list(projects), query_mode="span"
    ).translate([_leaf()])
    single, single_params = bind_trace_name_root_sets(predicate, sets)
    sql = f"SELECT 1 WHERE ({predicate} AND NOT ({predicate})) OR ({predicate} OR NOT ({predicate}))"
    bound, root_params = bind_trace_name_root_sets(sql, sets)
    expected = (
        f"SELECT 1 WHERE ({single} AND NOT ({single})) OR ({single} OR NOT ({single}))"
    )
    assert _render(bound, {**params, **root_params}) == _render(
        expected, {**params, **single_params}
    )
    assert trace_name_root_set_rendered_bytes(roots) == len(
        _render(single, single_params).encode("utf-8")
    )
    assert (
        len(_render(bound, {**params, **root_params}).encode("utf-8")) < MAX_QUERY_SIZE
    )


@pytest.mark.parametrize("escaped", ["'", "\\", "\n", "\0", "é", "🔥"])
@pytest.mark.parametrize("projects", [(PROJECT,), (PROJECT, OTHER)])
def test_byte_cap_uses_driver_escaping_and_utf8_at_exact_boundary(escaped, projects):
    predicate, params = ClickHouseFilterBuilderV2(
        project_ids=list(projects), query_mode="span"
    ).translate([_leaf()])
    # Measure with the actual driver, not the placeholder SQL or raw ID length.
    empty_roots = tuple((project, ("",)) for project in projects)
    empty, empty_params = bind_trace_name_root_sets(
        predicate, {_key(_leaf()): empty_roots}
    )
    overhead = len(_render(empty, {**params, **empty_params}).encode("utf-8"))
    unit_bytes = len(_render("%(value)s", {"value": escaped}).encode("utf-8")) - 2
    count, padding = divmod(TRACE_NAME_ROOT_SET_MAX_BYTES - overhead, unit_bytes)
    trace = escaped * count + "x" * padding
    roots = ((projects[0], (trace,)), *empty_roots[1:])
    bound, root_params = bind_trace_name_root_sets(predicate, {_key(_leaf()): roots})
    rendered_bytes = len(_render(bound, {**params, **root_params}).encode("utf-8"))
    assert rendered_bytes == TRACE_NAME_ROOT_SET_MAX_BYTES
    assert trace_name_root_set_rendered_bytes(roots) == rendered_bytes
    rows = [(project, value) for project, values in roots for value in values]
    assert resolve_trace_name_root_sets(
        [_leaf()], start=START, end=END, execute=Recorder(rows), project_ids=projects
    ) == {_key(_leaf()): roots}

    over = ((projects[0], (trace + "x",)), *empty_roots[1:])
    with pytest.raises(TraceNameFilterTooBroad):
        bind_trace_name_root_sets(predicate, {_key(_leaf()): over})
    with pytest.raises(TraceNameFilterTooBroad):
        resolve_trace_name_root_sets(
            [_leaf()],
            start=START,
            end=END,
            execute=Recorder(
                [(project, value) for project, values in over for value in values]
            ),
            project_ids=projects,
        )


@pytest.mark.parametrize(
    ("initial", "durations", "timeouts", "published"),
    [
        (0, [6], [5000], False),
        (0, [1, 5], [5000, 4000], False),
        (6, [], [], False),
        (0, [1, 1, 1], [5000, 4000, 3000], True),
    ],
)
def test_users_root_resolution_checks_shared_wall_before_every_statement(
    monkeypatch, initial, durations, timeouts, published
):
    from tracer.services import users_list_manager as ulm
    from tracer.services import users_matching_walk as walk
    from tracer.services.clickhouse import read_budget

    clock = [0.0]
    monkeypatch.setattr(read_budget.time, "monotonic", lambda: clock[0])
    deadline = read_budget.ReadDeadline.start(5000)
    clock[0] = initial
    calls = []
    bindings = []

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            calls.append(timeout_ms)
            clock[0] += durations[len(calls) - 1]
            # A successful lookup, not a timeout/error: only admission of the
            # NEXT optional lookup is denied. No server-kill policy is added.
            assert not deadline.enforce_on_server
            return SimpleNamespace(data=[(PROJECT, f"t{len(calls)}")])

    monkeypatch.setattr(ulm, "V2AnalyticsQueryService", Analytics)
    builder = SimpleNamespace(
        project_ids=[PROJECT], project_id=None, trace_name_root_sets=None
    )
    manager = SimpleNamespace(
        filters=[_leaf("checkout"), _leaf("browse"), _leaf("pay")],
        use_trace_name_root_sets=lambda *args, **kwargs: bindings.append(args[0]),
    )
    budget = SimpleNamespace(deadline=deadline, take=lambda n: True, exhausted_by=None)
    state = SimpleNamespace(
        manager=manager,
        builder=builder,
        budget=budget,
        window_start=START,
        window_end=END,
    )
    walk._resolve_walk_trace_name_roots(state)
    assert calls == timeouts
    if published:
        assert budget.exhausted_by is None
        assert len(builder.trace_name_root_sets) == 3
        assert bindings == [builder.trace_name_root_sets]
    else:
        assert budget.exhausted_by == "wall"
        assert builder.trace_name_root_sets is None
        assert bindings == []  # All partial sets discarded; no mixed modes.
