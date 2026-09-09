"""Exact scalar acquisition is permitted only outside complete-hour CH25 FINAL.

RMT fixtures execute in an isolated chdb session; no server or ORM is used.
"""

from datetime import timedelta

import pytest

from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_span_filter_plans,
    compile_trace_filter_plans,
)
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    PV,
    START,
    row,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture
def seed_engine(request):
    return request.getfixturevalue("engine")


def leaf(op="between", value=(1, 3), *, kind="number", key="tag", **config):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": op,
            "filter_value": list(value) if isinstance(value, tuple) else value,
            **config,
        },
    }


class KeyOnlyV2(SpanListQueryBuilderV2):
    """The pre-optimization acquisition contract, with identical FINAL input."""

    def _filter_seed_plan_predicate(self, plan, *, ordinary_seed):
        return SpanListQueryBuilder._filter_seed_plan_predicate(
            self, plan, ordinary_seed=ordinary_seed
        )


def builder(item=None, *, cls=SpanListQueryBuilderV2, extra=(), end=None, **kwargs):
    return cls(
        project_id=PROJECT,
        filters=[
            time_filter(START, end or START + timedelta(hours=2)),
            item or leaf(),
            *extra,
        ],
        bounded_internal_scan=True,
        **kwargs,
    )


def seed(target, **kwargs):
    return target.build_filter_seed_page(
        **{
            "slice_start": START,
            "slice_end": START + timedelta(hours=1),
            "limit": 50,
        }
        | kwargs
    )


@pytest.mark.parametrize(
    "item",
    [
        leaf(),
        leaf("not_between"),
        leaf("greater_than", 0),
        leaf("less_than", 3),
        leaf("not_equals", 0),
        leaf("not_in", [0, 1]),
        leaf("is_not_null", None),
        leaf("contains", "x", kind="text"),
        leaf("not_equals", True, kind="boolean"),
        leaf(
            "not_in",
            [1, True],
            kind="text",
            attribute_value_types=["number", "boolean"],
        ),
    ],
)
def test_metadata_preserves_public_and_raw_predicates(item):
    plan = compile_span_filter_plans([item])[0]
    grouped = compile_span_filter_plans([item], group_attribute_nulls=True)[0]
    trace = compile_trace_filter_plans([item])[0]
    assert plan.post_final_scalar_seed_predicate == plan.seed_predicate
    assert plan.raw_witness_predicate == plan.raw_key_witness_predicate
    assert "latest_filter_param_" not in plan.raw_witness_predicate
    assert grouped.post_final_scalar_seed_predicate is None
    assert trace.post_final_scalar_seed_predicate is None
    for field in ("predicate", "seed_predicate", "aggregates", "params"):
        assert getattr(plan, field) == getattr(grouped, field)


@pytest.mark.parametrize(
    "item",
    [
        leaf("is_null", None),
        leaf("equals", 1),
        leaf("in", [1, 3]),
        leaf("in", [1, True], kind="text", attribute_value_types=["number", "boolean"]),
        leaf("not_equals", "x", kind="text", key="model", col_type="SYSTEM_METRIC"),
        leaf(
            "greater_than", 1, key="unknown_native_fallback", col_type="SYSTEM_METRIC"
        ),
        leaf("contains", [1], kind="array"),
        leaf("contains", {"a": 1}, kind="map"),
    ],
)
def test_ineligible_plans_have_no_post_final_opt_in(item):
    assert compile_span_filter_plans([item])[0].post_final_scalar_seed_predicate is None


def test_range_values_are_only_outside_final_and_probe_stays_key_only():
    target = builder()
    sql, params = seed(target)
    before, after = sql.split(") AS latest_seed_spans", 1)
    assert "SELECT * FROM spans FINAL" in before
    assert "SELECT DISTINCT project_id" in before
    assert "has(attrs_number.keys," in before
    assert "latest_filter_param_" not in before
    assert (
        "BETWEEN %(latest_filter_param_0_low)s AND %(latest_filter_param_0_high)s"
        in after
    )
    assert "mapContains(attrs_number, %(latest_filter_key_0)s)" in after
    assert params["latest_filter_param_0_low"] == 1.0
    assert params["latest_filter_param_0_high"] == 3.0
    for setting in (
        "use_skip_indexes_if_final = 0",
        "optimize_move_to_prewhere = 0",
        "optimize_move_to_prewhere_if_final = 0",
        "enable_optimize_predicate_expression_to_final_subquery = 0",
        "query_plan_merge_expressions = 0",
    ):
        assert setting in sql
    old = builder(cls=KeyOnlyV2)
    old_sql, _ = seed(old)
    assert before == old_sql.split(") AS latest_seed_spans", 1)[0]
    probe, probe_params = target.build_filter_population_time_discovery_query(
        slice_start=START, slice_end=START + timedelta(hours=2)
    )
    assert "latest_filter_param_" not in probe
    assert not any(key.startswith("latest_filter_param_") for key in probe_params)
    assert "FINAL" not in probe and "LIMIT" not in probe
    assert "is_deleted" not in probe and "project_version_id" not in probe
    assert (probe, probe_params) == old.build_filter_population_time_discovery_query(
        slice_start=START, slice_end=START + timedelta(hours=2)
    )


@pytest.mark.parametrize(
    "mode", ["legacy", "anchor", "sample-zero", "sample-full", "newer"]
)
def test_nonordinary_modes_keep_existing_query(mode):
    kwargs = {
        "anchor": {"bounded_anchor_probe": True},
        "sample-zero": {"bounded_sampling_rate": 0, "bounded_sampling_salt": "fixture"},
        "sample-full": {
            "bounded_sampling_rate": 100,
            "bounded_sampling_salt": "fixture",
        },
    }.get(mode, {})
    cls = SpanListQueryBuilder if mode == "legacy" else SpanListQueryBuilderV2
    target = builder(cls=cls, **kwargs)
    options = {"direction": "newer"} if mode == "newer" else {}
    sql, params = seed(target, **options)
    assert "latest_filter_param_0_low" not in params
    assert "latest_filter_param_0_low" not in sql
    if mode != "legacy":
        assert (sql, params) == seed(builder(cls=KeyOnlyV2, **kwargs), **options)
    else:
        assert "FROM spans FINAL" not in sql


@pytest.mark.parametrize("direction", ["older", "newer"])
def test_navigation_explicitly_opts_out_without_mutating_next_ordinary_seed(direction):
    target = builder()
    kwargs = {
        "direction": direction,
        "slice_start": START,
        "slice_end": START + timedelta(hours=1),
        "limit": 5,
        "cursor_start_time": row()["start_time"],
        "cursor_order_token": target.bounded_filter_row_order_token(row()),
    }
    sql, params = target.build_filter_navigation_seed_page(**kwargs)
    assert "latest_filter_param_0_low" not in params
    assert (sql, params) == builder(cls=KeyOnlyV2).build_filter_navigation_seed_page(
        **kwargs
    )
    assert "latest_filter_param_0_low" in seed(target)[1]


def test_annotation_candidate_route_explicitly_opts_out():
    class Relation:
        QUERY_MODE_SPAN = "span"

        def __init__(self, **kwargs):
            pass

        def translate(self, filters):
            return "1 = 1", {}

    class AnnotationCandidate(SpanListQueryBuilderV2):
        _FILTER_BUILDER_CLS = Relation

        def supports_filter_candidate_seed_page(self):
            return True

        def _positive_annotation_seed_filter(self):
            return {"column_id": "fixture-label"}

    target = builder(cls=AnnotationCandidate)
    sql, params = target.build_filter_candidate_seed_page(
        slice_start=START, slice_end=START + timedelta(hours=1), limit=5
    )
    assert "latest_filter_param_0_low" not in sql
    assert "latest_filter_param_0_low" not in params
    assert "latest_filter_param_0_low" in seed(target)[1]


def test_json_micro_seed_bypasses_scalar_hook(monkeypatch):
    item = leaf(
        "equals", "inbound", kind="text", key="call_type", col_type="SYSTEM_METRIC"
    )
    target = builder(item)
    assert target._unindexed_positive_micro_seed_plan() is not None

    def forbidden(*args, **kwargs):
        pytest.fail("micro seed must not invoke ordinary scalar hook")

    monkeypatch.setattr(target, "_filter_seed_plan_predicate", forbidden)
    sql, _ = target.build_filter_unindexed_micro_seed_page(
        slice_start=START, slice_end=START + timedelta(minutes=5), limit=5
    )
    assert "JSONExtract" in sql


@pytest.mark.parametrize(
    "item,column,passing,blocked",
    [
        (leaf(), "attrs_number", 2, 0),
        (leaf("not_between"), "attrs_number", 0, 2),
        (leaf("greater_than", 0), "attrs_number", 2, 0),
        (leaf("not_equals", True, kind="boolean"), "attrs_bool", 0, 1),
        (
            leaf("not_equals", "blocked", kind="text"),
            "attrs_string",
            "allowed",
            "blocked",
        ),
    ],
)
def test_engine_post_final_predicate_preserves_latest_and_complete_physical_identity(
    seed_engine, item, column, passing, blocked
):
    execute, insert = seed_engine
    if column == "attrs_string" and not execute(
        "SELECT name FROM system.functions WHERE name = 'lowerUTF8'"
    ):
        pytest.skip("CH25 reduced engine lacks lowerUTF8; no ASCII substitution")

    def add(identity, value=None, **kwargs):
        maps = {"attrs_number": {}, "attrs_bool": {}, "attrs_string": {}}
        if value is not None:
            maps[column] = {"tag": value}
        insert(id=identity, **(maps | kwargs))

    add("missing")
    wrong = "attrs_number" if column != "attrs_number" else "attrs_bool"
    add("wrong-type", **{wrong: {"tag": 0}})
    add("passing", passing)
    add("blocked", blocked)
    for identity, replacement in [
        ("removed", {column: {}}),
        ("deleted", {"is_deleted": 1}),
        ("corrected-out", {column: {"tag": blocked}}),
    ]:
        add(identity, passing)
        add(
            identity,
            passing,
            _version=2,
            start_time=START + timedelta(minutes=25),
            **replacement,
        )
    add("corrected-in", blocked)
    add("corrected-in", passing, _version=2, start_time=START + timedelta(minutes=25))
    add("collision", passing)
    add("collision", passing, service_name="service-b")
    add("collision", passing, observation_type="other")
    add("outside", passing, project_id=OTHER_PROJECT)

    target = builder(item)
    candidates = execute(*seed(target))
    expected = {
        ("passing", "span", "service-a"),
        ("corrected-in", "span", "service-a"),
        ("collision", "span", "service-a"),
        ("collision", "span", "service-b"),
        ("collision", "other", "service-a"),
    }
    assert {
        (r["id"], r["observation_type"], r["service_name"]) for r in candidates
    } == expected
    assert all(r["project_id"] == PROJECT for r in candidates)
    assert next(r for r in candidates if r["id"] == "corrected-in")["_version"] == 2
    old = builder(item, cls=KeyOnlyV2)
    old_candidates = execute(*seed(old))
    assert {r["id"] for r in old_candidates} > {r["id"] for r in candidates}
    assert execute(
        *target.build_filter_match_query_from_seed_rows(candidates)
    ) == execute(*old.build_filter_match_query_from_seed_rows(old_candidates))


def test_engine_many_rejects_are_removed_before_limit_and_tied_pagination(seed_engine):
    execute, insert = seed_engine
    execute(
        """INSERT INTO spans
        (project_id, observation_type, service_name, start_time, trace_id, id, attrs_number, _version)
        SELECT toUUID(%(project)s), 'span', 'service-a',
               toDateTime64(%(start)s, 6, 'UTC'), 'trace', concat('reject-', toString(number)),
               map('tag', 0.), 1 FROM numbers(2048)""",
        {"project": PROJECT, "start": START + timedelta(minutes=30)},
    )
    for identity in ("a", "b", "c"):
        insert(
            id=identity,
            start_time=START + timedelta(minutes=10, microseconds=123),
            attrs_number={"tag": 2},
        )
    target = builder()
    first = execute(*seed(target, limit=2))
    assert [r["id"] for r in first] == ["c", "b"]
    second = execute(
        *seed(
            target,
            limit=2,
            before_start_time=first[-1]["start_time"],
            before_id=target.bounded_filter_row_order_token(first[-1]),
        )
    )
    assert [r["id"] for r in second] == ["a"]
    old = builder(cls=KeyOnlyV2)
    old_first = execute(*seed(old, limit=2))
    assert all(r["id"].startswith("reject-") for r in old_first)
    assert execute(*old.build_filter_match_query_from_seed_rows(old_first)) == []


@pytest.mark.parametrize(
    "correction", ["past-end", "before-start", "deleted", "project-version"]
)
def test_engine_complete_hour_replay_precedes_mutable_boundaries(
    seed_engine, correction
):
    execute, insert = seed_engine
    start = START + timedelta(minutes=10, microseconds=123)
    end = START + timedelta(minutes=30, microseconds=456)
    insert(attrs_number={"tag": 2}, project_version_id=PV)
    changes = {
        "past-end": {"start_time": end},
        "before-start": {"start_time": start - timedelta(microseconds=1)},
        "deleted": {"is_deleted": 1},
        "project-version": {"project_version_id": OTHER_PROJECT},
    }[correction]
    insert(
        **(
            {"_version": 2, "attrs_number": {"tag": 2}, "project_version_id": PV}
            | changes
        )
    )
    target = builder(project_version_id=PV)
    assert execute(*seed(target, slice_start=start, slice_end=end)) == []


def test_engine_typed_not_in_union_and_residual_native_predicate(seed_engine):
    execute, insert = seed_engine
    item = leaf(
        "not_in", [1, True], kind="text", attribute_value_types=["number", "boolean"]
    )
    payloads = {
        "number-only": {"attrs_number": {"tag": 0}},
        "bool-only": {"attrs_bool": {"tag": 0}},
        "blocked-number": {"attrs_number": {"tag": 1}, "attrs_bool": {"tag": 0}},
        "blocked-bool": {"attrs_number": {"tag": 0}, "attrs_bool": {"tag": 1}},
        "missing": {},
        "native-reject": {"attrs_number": {"tag": 0}, "cost": 7.0},
    }
    for identity, payload in payloads.items():
        insert(id=identity, **({"attrs_number": {}} | payload))
    native = leaf("not_equals", 7, key="cost", col_type="SYSTEM_METRIC")
    target = builder(item, extra=[native])
    candidates = execute(*seed(target))
    assert {r["id"] for r in candidates} == {
        "number-only",
        "bool-only",
        "native-reject",
    }
    rows = execute(*target.build_filter_match_query_from_seed_rows(candidates))
    assert {r["id"] for r in rows} == {"number-only", "bool-only"}
    identities = [target.bounded_filter_row_identity(r) for r in rows]
    content = execute(
        *target.build_content_query([r["id"] for r in rows], span_identities=identities)
    )
    assert {r["id"] for r in content} == {"number-only", "bool-only"}
