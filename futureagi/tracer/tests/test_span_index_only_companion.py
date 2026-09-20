"""Index-only helpers retain actual typed membership and complete FINAL input."""

import re
from datetime import timedelta

import pytest

from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_span_filter_plans,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import OTHER_PROJECT, START
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_span_post_final_scalar_seed import builder, leaf, seed


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture
def seed_engine(request):
    return request.getfixturevalue("engine")


class EvaluatedCompanions(SpanListQueryBuilderV2):
    """Evaluate the same redundant conditions as the prior query route did."""

    def _filter_seed_plan_predicate(self, plan, *, ordinary_seed):
        predicate = super()._filter_seed_plan_predicate(
            plan, ordinary_seed=ordinary_seed
        )
        return re.sub(r"\bindexHint\s*\(", "(", predicate)


@pytest.mark.parametrize(
    "item",
    [
        leaf("equals", 1),
        leaf("equals", 0),
        leaf("in", [0, 2]),
        leaf("equals", False, kind="boolean"),
        leaf("equals", "a%_\\b'K", kind="text"),
        leaf("equals", "Kelvin İıß", kind="text"),
        leaf(
            "in",
            ["one", 0, False],
            kind="text",
            attribute_value_types=["string", "number", "boolean"],
        ),
    ],
)
def test_positive_seed_replays_complete_prefixes_before_exact_value_filter(item):
    plan = compile_span_filter_plans([item])[0]
    target = builder(item)
    sql, params = seed(target)
    inside, outside = sql.split(") AS latest_seed_spans", 1)
    prefix_columns = (
        "project_id, observation_type, service_name, toStartOfHour(start_time)"
    )
    final_scope, raw_probe = inside.split(f"AND ({prefix_columns}) IN (", 1)
    assert f"SELECT DISTINCT {prefix_columns}" in raw_probe
    assert "latest_filter_key_0" not in final_scope
    assert "latest_filter_param_" not in final_scope
    # The raw value probe selects immutable prefixes only. All replacements
    # in those prefixes still enter FINAL, including key removals/tombstones.
    assert "latest_filter_key_0" in raw_probe
    assert "latest_filter_key_0" in outside
    assert "latest_filter_param_" in outside
    for name, value in plan.params.items():
        if f"%({name})s" in plan.raw_witness_predicate:
            assert params[name] == value
    for forbidden in (
        "is_deleted",
        "project_version_id",
        "filter_before_",
        "LIMIT",
        "SAMPLE",
    ):
        assert forbidden not in inside
    assert "SELECT * FROM spans FINAL" in inside
    assert "AND is_deleted = 0" in outside
    assert "use_skip_indexes_if_final = 0" in sql
    # Prefix pruning does not change the independent time-discovery contract.
    old = builder(item, cls=EvaluatedCompanions)
    seed(old)
    probe = {"slice_start": START, "slice_end": START + timedelta(hours=2)}
    assert target.build_filter_population_time_discovery_query(
        **probe
    ) == old.build_filter_population_time_discovery_query(**probe)


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize(
    "item,column,passing,blocked",
    [
        (leaf("equals", 2), "attrs_number", 2, 0),
        (leaf("equals", 0), "attrs_number", 0, 2),
        (leaf("in", [0, 2]), "attrs_number", 0, 3),
        (leaf("equals", False, kind="boolean"), "attrs_bool", 0, 1),
        (leaf("equals", "KELVIN", kind="text"), "attrs_string", "kelvin", "other"),
    ],
)
def test_index_only_values_keep_all_replacements_and_physical_collisions(
    seed_engine, item, column, passing, blocked, indexed
):
    execute, insert = seed_engine
    if column == "attrs_string" and not execute(
        "SELECT name FROM system.functions WHERE name = 'lowerUTF8'"
    ):
        pytest.skip("Reduced CH25 engine lacks lowerUTF8; no ASCII substitution")
    if indexed:
        # Isolated RMT fixture only. Index-bearing old matching parts must not
        # hide a new correction/removal/tombstone from complete-hour FINAL.
        execute(
            f"ALTER TABLE spans ADD INDEX idx_value_prefix_keys mapKeys({column}) "
            "TYPE bloom_filter GRANULARITY 1"
        )
        execute(
            f"ALTER TABLE spans ADD INDEX idx_value_prefix_values mapValues({column}) "
            "TYPE bloom_filter GRANULARITY 1"
        )

    def add(identity, value=None, **kwargs):
        maps = {"attrs_number": {}, "attrs_string": {}, "attrs_bool": {}}
        if value is not None:
            maps[column] = {"tag": value}
        insert(id=identity, **(maps | kwargs))

    add("missing")
    add("passing", passing)
    add("nonmatching", blocked)
    for identity, replacement in [
        ("removed", {column: {}}),
        ("deleted", {"is_deleted": 1}),
        ("changed", {column: {"tag": blocked}}),
    ]:
        add(identity, passing)
        add(
            identity,
            passing,
            _version=2,
            start_time=START + timedelta(minutes=10),
            **replacement,
        )
    add("new-match", blocked)
    add("new-match", passing, _version=2)
    add("collision", passing)
    add("collision", passing, service_name="other")
    add("collision", passing, observation_type="other")
    add("foreign", passing, project_id=OTHER_PROJECT)
    target, old = builder(item), builder(item, cls=EvaluatedCompanions)
    candidates = execute(*seed(target))
    assert candidates == execute(*seed(old))
    assert {r["id"] for r in candidates} == {"passing", "new-match", "collision"}
    assert len(candidates) == 5
    assert execute(
        *target.build_filter_match_query_from_seed_rows(candidates)
    ) == execute(*old.build_filter_match_query_from_seed_rows(candidates))
    # Preserve full-tuple order through every page, including a shared id.
    seen, before = [], None
    for _ in range(7):
        page = execute(
            *seed(
                target,
                limit=1,
                before_start_time=before["start_time"] if before else None,
                before_id=target.bounded_filter_row_order_token(before)
                if before
                else None,
            )
        )
        if not page:
            break
        before = page[0]
        seen.extend(page)
    assert seen == candidates


@pytest.mark.parametrize("correction", ["before", "after", "deleted"])
def test_index_companion_does_not_cut_versions_at_exact_boundaries(
    seed_engine, correction
):
    execute, insert = seed_engine
    start, end = START + timedelta(minutes=10), START + timedelta(minutes=30)
    insert(attrs_number={"tag": 2})
    changes = {
        "before": {"start_time": start - timedelta(microseconds=1)},
        "after": {"start_time": end},
        "deleted": {"is_deleted": 1},
    }[correction]
    insert(_version=2, attrs_number={"tag": 2}, **changes)
    target = builder(leaf("equals", 2))
    assert execute(*seed(target, slice_start=start, slice_end=end)) == []


def test_mixed_type_in_keeps_union_of_storage_domains(seed_engine):
    execute, insert = seed_engine
    item = leaf(
        "in", [2, False], kind="text", attribute_value_types=["number", "boolean"]
    )
    for identity, maps in [
        ("number", {"attrs_number": {"tag": 2}}),
        ("false", {"attrs_bool": {"tag": 0}}),
        ("both", {"attrs_number": {"tag": 2}, "attrs_bool": {"tag": 0}}),
        ("neither", {"attrs_number": {"tag": 0}, "attrs_bool": {"tag": 1}}),
        ("missing", {}),
    ]:
        insert(**({"id": identity, "attrs_number": {}, "attrs_bool": {}} | maps))
    target, old = builder(item), builder(item, cls=EvaluatedCompanions)
    candidates = execute(*seed(target))
    assert candidates == execute(*seed(old))
    assert {r["id"] for r in candidates} == {"number", "false", "both"}
    assert execute(
        *target.build_filter_match_query_from_seed_rows(candidates)
    ) == execute(*old.build_filter_match_query_from_seed_rows(candidates))


def test_cross_type_conjunction_cannot_join_different_rows_or_versions(seed_engine):
    execute, insert = seed_engine
    for identity, version, number, flag in [
        ("matching", 1, 2, 0),
        ("only-number", 1, 2, 1),
        ("only-boolean", 1, 0, 0),
        ("changed", 1, 2, 0),
        ("changed", 2, 0, 0),
        ("new-match", 1, 0, 1),
        ("new-match", 2, 2, 0),
    ]:
        insert(
            id=identity,
            _version=version,
            attrs_number={"tag": number},
            attrs_bool={"flag": flag},
        )
    item = leaf("equals", 2)
    extra = [leaf("equals", False, kind="boolean", key="flag")]
    target, old = (
        builder(item, extra=extra),
        builder(item, cls=EvaluatedCompanions, extra=extra),
    )
    candidates = execute(*seed(target))
    assert candidates == execute(*seed(old))
    assert {r["id"] for r in candidates} == {"matching", "new-match"}
    assert execute(
        *target.build_filter_match_query_from_seed_rows(candidates)
    ) == execute(*old.build_filter_match_query_from_seed_rows(candidates))
