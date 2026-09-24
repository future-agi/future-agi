"""Raw key indexes may prune immutable prefixes, never replacement versions.

The engine fixture is an isolated chdb session, with the production short
primary key. No production table, connection, index or data is modified.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    attr_filter,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_span_population_time_discovery import _engine_population_page


@pytest.fixture
def seed_engine(request):
    return request.getfixturevalue("engine")


class PreviousSource(SpanListQueryBuilderV2):
    def _filter_seed_source_sql(self, *, raw_key_predicate=""):
        return self._latest_window_source_sql("filter_slice")


def builder(*, previous=False, filters=None, **kwargs):
    cls = PreviousSource if previous else SpanListQueryBuilderV2
    return cls(
        project_id=PROJECT,
        filters=filters or [time_filter(), attr_filter("greater_than", 1)],
        bounded_internal_scan=True,
        **kwargs,
    )


def query(target, **kwargs):
    return target.build_filter_seed_page(
        **{
            "slice_start": START,
            "slice_end": START + timedelta(hours=1),
            "limit": 50,
        }
        | kwargs
    )


def raw_population(sql):
    assert "SELECT DISTINCT project_id" in sql
    return (
        "SELECT DISTINCT project_id"
        + sql.split("SELECT DISTINCT project_id", 1)[1].split("\n              )", 1)[0]
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "op,value",
    [
        ("greater_than", 1),
        ("less_than", 1),
        ("is_not_null", None),
        ("not_equals", 1),
        ("not_in", [1, 2]),
        ("not_between", [1, 2]),
    ],
)
def test_prefix_population_contains_only_raw_keys_and_immutable_scope(op, value):
    sql, params = query(builder(filters=[time_filter(), attr_filter(op, value)]))
    raw = raw_population(sql)
    assert "FROM spans\n" in raw and "FINAL" not in raw
    assert "has(attrs_number.keys," in raw
    assert "indexHint(has(mapKeys(attrs_number)" in raw
    assert "project_id" in raw and "toStartOfHour(start_time)" in raw
    for forbidden in ("is_deleted", "project_version_id", "LIMIT", "SAMPLE", "['tag']"):
        assert forbidden not in raw
    assert "FROM spans FINAL" in sql
    assert "AND is_deleted = 0" in sql
    assert "use_skip_indexes_if_final = 0" in sql
    assert "enable_optimize_predicate_expression_to_final_subquery = 0" in sql
    assert "tag" in params.values()


@pytest.mark.unit
@pytest.mark.parametrize("op", ["equals", "in", "is_null"])
def test_value_and_absence_seeds_keep_existing_source(op):
    value = [1] if op == "in" else 1
    sql, _ = query(builder(filters=[time_filter(), attr_filter(op, value)]))
    assert ("SELECT DISTINCT project_id" in sql) is (op != "is_null")


@pytest.mark.integration
@pytest.mark.parametrize(
    "replacement",
    [
        {"is_deleted": 1, "attrs_number": {"tag": 4}},
        {"attrs_number": {}},
        {"attrs_number": {"tag": 0}},
        {"attrs_number": {"tag": 4}},
    ],
)
def test_latest_replay_retains_tombstone_correction_and_other_key_collisions(
    seed_engine, replacement
):
    execute, insert = seed_engine
    insert(attrs_number={"tag": 3})
    insert(start_time=START + timedelta(minutes=10), _version=2, **replacement)
    insert(id="same", attrs_number={"tag": 2})
    insert(id="same", service_name="service-b", attrs_number={"tag": 3})
    insert(id="same", observation_type="other", attrs_number={"tag": 4})
    insert(project_id=OTHER_PROJECT, attrs_number={"tag": 4})
    old = execute(*query(builder(previous=True)))
    new = execute(*query(builder()))
    assert new == old
    # The raw prefix remains key-only. Ordinary V2 seeds now apply the scalar
    # range only after FINAL, so the winning zero cannot occupy the public
    # candidate working set. Residual classifier semantics remain unchanged.
    expected_seed = 4 if replacement == {"attrs_number": {"tag": 4}} else 3
    assert len(new) == expected_seed
    assert {row["project_id"] for row in new} == {PROJECT}
    classified = execute(*builder().build_filter_match_query_from_seed_rows(new))
    expected_matches = 4 if replacement == {"attrs_number": {"tag": 4}} else 3
    assert len(classified) == expected_matches


@pytest.mark.integration
def test_nonhour_boundary_keeps_winner_outside_exact_slice(seed_engine):
    execute, insert = seed_engine
    insert(attrs_number={"tag": 3})
    insert(start_time=START + timedelta(minutes=40), _version=2, attrs_number={})
    end = START + timedelta(minutes=30)
    filters = [time_filter(end=end), attr_filter("greater_than", 1)]
    assert execute(*query(builder(filters=filters), slice_end=end)) == []
    assert execute(*query(builder(previous=True, filters=filters), slice_end=end)) == []


@pytest.mark.integration
def test_missing_numeric_key_bloom_prunes_raw_population(seed_engine, record_property):
    execute, _ = seed_engine
    execute(
        "ALTER TABLE spans ADD INDEX idx_test_number_keys mapKeys(attrs_number) TYPE bloom_filter GRANULARITY 1"
    )
    # Build index-bearing local parts. Neither seed can return a match; only
    # the new raw key read may use the mutable-key bloom safely before FINAL.
    execute(
        """INSERT INTO spans
        (project_id, observation_type, service_name, start_time, trace_id, id,
         attrs_number, _version)
        SELECT toUUID(%(project)s), 'span', 'service-a',
               toDateTime64(%(start)s, 6, 'UTC'), 'trace', toString(number),
               map('other', 2.), 1 FROM numbers(65536)""",
        {"project": PROJECT, "start": START},
    )
    sql, params = query(builder())
    # EXPLAIN executes the IN set while preparing the outer plan; inspect the
    # raw set explicitly to retain its skip-index statistics in the evidence.
    explain = execute("EXPLAIN indexes = 1, json = 1 " + raw_population(sql), params)
    tree = json.loads("\n".join(row["explain"] for row in explain))
    indexes = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("Name") == "idx_test_number_keys":
                indexes.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(tree)
    assert indexes, tree
    record_property("raw_key_index", indexes)
    assert all(index["Selected Granules"] == 0 for index in indexes)
    assert execute(sql, params) == execute(*query(builder(previous=True))) == []


@pytest.mark.unit
@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize(
    "op,value",
    [
        ("greater_than", 1),
        ("not_equals", 1),
        ("not_in", [1, 2]),
        ("not_between", [1, 2]),
    ],
)
def test_key_only_discovery_can_prove_complete_long_window_without_value_scan(
    days, op, value
):
    start, end = START - timedelta(days=days), START
    target = builder(filters=[time_filter(start, end), attr_filter(op, value)])
    assert target.recommended_filter_population_time_discovery_window() == end - start
    assert target.recommended_filter_cursor_seed_batch_size() == 32
    assert target.recommended_filter_population_time_discovery_windows() == tuple(
        dict.fromkeys(timedelta(days=min(days, size)) for size in (7, 28, days))
    )
    sql, params = target.build_filter_population_time_discovery_query(
        slice_start=start, slice_end=end
    )
    assert "has(attrs_number.keys," in sql and "mapKeys(attrs_number)" in sql
    assert "latest_filter_param_" not in sql
    assert "SELECT maxOrNull" in sql
    assert "FINAL" not in sql and "LIMIT" not in sql
    assert "is_deleted" not in sql and "project_version_id" not in sql
    assert len([key for key in params if key.startswith("latest_filter_")]) == 1


@pytest.mark.unit
@pytest.mark.parametrize("op", ["is_null"])
def test_non_key_only_modes_cannot_silently_expand_discovery_to_a_year(op):
    start = START - timedelta(days=365)
    target = builder(filters=[time_filter(start, START), attr_filter(op, 1)])
    assert target.recommended_filter_population_time_discovery_window() == timedelta(
        days=1
    )
    assert target.recommended_filter_population_time_discovery_windows() is None
    assert target.recommended_filter_cursor_seed_batch_size() is None
    with pytest.raises(ValueError, match="qualified window"):
        target.build_filter_population_time_discovery_query(
            slice_start=start, slice_end=START
        )


@pytest.mark.integration
@pytest.mark.parametrize("present", [False, True])
def test_full_year_sparse_numeric_gap_uses_adjacent_proofs_then_latest_replay(
    seed_engine, present
):
    execute, insert = seed_engine
    start, end = START - timedelta(days=100), START + timedelta(days=265)
    insert(id="irrelevant", start_time=end - timedelta(minutes=5), attrs_number={})
    if present:
        insert(id="wanted", attrs_number={"tag": 3})
        insert(id="deleted", attrs_number={"tag": 4})
        insert(id="deleted", _version=2, is_deleted=1, attrs_number={"tag": 4})
    filters = [time_filter(start, end), attr_filter("greater_than", 1)]
    target = builder(filters=filters)
    result = _engine_population_page(execute, target, filters)
    assert result.complete
    assert [row["id"] for row in result.rows] == (["wanted"] if present else [])
    probes = [a for a in result.attempts if a.kind == "population_time_discovery"]
    assert len(probes) == (6 if present else 3)
    assert probes[0].slice_end - probes[0].slice_start == timedelta(days=7)
    assert probes[1].slice_end - probes[1].slice_start == timedelta(days=28)
    assert probes[1].slice_end == probes[0].slice_start
    assert probes[2].slice_end == probes[1].slice_start
    assert probes[2].slice_start == start
    if not present:
        assert len(result.attempts) == 4  # empty seed + three complete intervals
    else:
        # The raw hit is replayed and classified before another gap jump; the
        # new proof is strictly below the fully exhausted hour, not its match.
        assert probes[3].slice_end == START.replace(minute=0, second=0, microsecond=0)
        assert probes[-1].slice_start == start
        assert len(result.attempts) < 12


@pytest.mark.integration
def test_repeated_gap_proof_does_not_stop_at_a_nonmatching_raw_hit(seed_engine):
    execute, insert = seed_engine
    start, end = START - timedelta(days=100), START + timedelta(days=265)
    # Rejected value, cleared key and tombstone all produce raw key hits. A
    # later absence proof must not turn those hits into rows or hide older ones.
    for days, identity in ((60, "reject"), (40, "clear"), (20, "deleted")):
        stamp = START + timedelta(days=days)
        insert(id=identity, start_time=stamp, attrs_number={"tag": 3})
        insert(
            id=identity,
            start_time=stamp + timedelta(minutes=1),
            _version=2,
            attrs_number={} if identity == "clear" else {"tag": 0},
            is_deleted=int(identity == "deleted"),
        )
    insert(id="older", attrs_number={"tag": 3})
    filters = [time_filter(start, end), attr_filter("greater_than", 1)]
    result = _engine_population_page(execute, builder(filters=filters), filters)
    assert result.complete and [row["id"] for row in result.rows] == ["older"]
    probes = [a for a in result.attempts if a.kind == "population_time_discovery"]
    assert len(probes) > 6
    assert all(
        next_.slice_end < previous.slice_end
        for previous, next_ in zip(probes, probes[1:], strict=False)
    )
    assert probes[-1].slice_start == start


@pytest.mark.integration
@pytest.mark.parametrize("matching", [False, True])
def test_one_row_preview_batches_rejected_witnesses_without_changing_page(
    seed_engine, matching
):
    execute, insert = seed_engine
    for index in range(30):
        insert(id=f"reject-{index:02d}", attrs_number={"tag": 0})
    wanted_time = START - timedelta(minutes=1)
    if matching:
        insert(id="wanted-a", start_time=wanted_time, attrs_number={"tag": 3})
        insert(id="wanted-b", start_time=wanted_time, attrs_number={"tag": 3})
    start, end = START - timedelta(days=100), START + timedelta(days=265)
    filters = [time_filter(start, end), attr_filter("greater_than", 1)]
    target = builder(filters=filters)
    result = _engine_population_page(execute, target, filters, page_size=1)
    assert result.complete
    assert [row["id"] for row in result.rows] == (["wanted-b"] if matching else [])
    assert result.has_more is matching
    # Value rejects are excluded after FINAL before candidate publication.
    # Only the older positive tie group needs a classifier; an exhausted
    # negative-only population does not spend one on known rejects.
    assert sum(a.kind == "classify" for a in result.attempts) == (1 if matching else 0)
    assert len(result.attempts) < 12
    if matching:
        next_page = _engine_population_page(
            execute,
            target,
            filters,
            page_size=1,
            cursor_start_time=wanted_time,
            cursor_order_token=target.bounded_filter_row_order_token(result.rows[0]),
        )
        assert next_page.complete
        assert [row["id"] for row in next_page.rows] == ["wanted-a"]
        assert not next_page.has_more


@pytest.mark.integration
def test_full_window_cross_type_key_proof_requires_keys_on_same_raw_span(seed_engine):
    execute, insert = seed_engine
    insert(id="numeric-only", attrs_number={"tag": 3})
    insert(id="text-only", attrs_number={}, attrs_string={"note": "long text"})
    start, end = START - timedelta(days=1), START + timedelta(days=200)
    filters = [
        time_filter(start, end),
        attr_filter("greater_than", 1),
        {
            "column_id": "note",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "is_not_null",
            },
        },
    ]
    target = builder(filters=filters)
    sql, params = target.build_filter_population_time_discovery_query(
        slice_start=start, slice_end=end
    )
    assert execute(sql, params) == [{"newest_raw_time_us": None}]
    insert(id="both", attrs_number={"tag": 4}, attrs_string={"note": ""})
    assert execute(sql, params)[0]["newest_raw_time_us"] is not None
    result = _engine_population_page(execute, target, filters)
    assert result.complete and [row["id"] for row in result.rows] == ["both"]


def scalar(key, value, kind="number", op="equals", **config):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
            **config,
        },
    }


class PreviousAcquisition(SpanListQueryBuilderV2):
    """Old key-only acquisition policy, with identical latest-state SQL."""

    def _filter_population_plan_predicate(self, plan, *, ordinary_seed):
        if plan.raw_witness_predicate == plan.raw_key_witness_predicate:
            return plan.raw_key_witness_predicate
        return None


@pytest.fixture
def span_reference(monkeypatch):
    # Independent QA compiler, unpruned FINAL population, no application IDs.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts/qa"))
    import observe_span_reference

    return observe_span_reference


def reference_rows(reference, execute, filters):
    start, end = (
        datetime.fromisoformat(value)
        for value in filters[0]["filter_config"]["filter_value"]
    )
    case = {
        "surface": "spans",
        "window": {
            "start": start.replace(tzinfo=UTC).isoformat(),
            "end": end.replace(tzinfo=UTC).isoformat(),
        },
        "request": {"target_rows": 100, "params": {"page_number": 0}},
    }
    sql, params = reference.build_span_reference_query(
        case,
        {"project_id": PROJECT},
        filters[1:],
        raw_prefix=False,
    )
    settings = ", ".join(
        f"{key} = {value}" if isinstance(value, int) else f"{key} = '{value}'"
        for key, value in reference.SAFE_FINAL_SETTINGS.items()
    )
    return execute(sql + " SETTINGS " + settings, params)


@pytest.mark.unit
@pytest.mark.parametrize(
    "kind,value", [("number", 0), ("boolean", False), ("text", "Kelvin")]
)
@pytest.mark.parametrize("op", ["equals", "in"])
def test_value_population_keeps_seed_witness_and_uses_thin_text_discovery(kind, value, op):
    start, end = START - timedelta(days=365), START + timedelta(hours=1)
    leaf = scalar("value", [value] if op == "in" else value, kind, op)
    target = builder(filters=[time_filter(start, end), leaf])
    raw = raw_population(query(target)[0])
    probe, params = target.build_filter_population_time_discovery_query(
        slice_start=end - timedelta(days=1) if kind == "text" else start,
        slice_end=end
    )
    assert "latest_filter_param_0" in raw
    if kind == "text":
        assert "attrs_" not in probe and "latest_filter_param_0" not in params
        assert target.recommended_filter_population_time_discovery_windows() == (timedelta(days=1),)
    else:
        assert raw.rsplit("WHERE ", 1)[-1].strip() in probe
        assert "latest_filter_param_0" in params
        assert target.recommended_filter_population_time_discovery_windows()[-1] == end - start
    assert target.recommended_filter_cursor_seed_batch_size() is None
    assert target.recommended_filter_cursor_adaptive_seed_batch_size() is None
    assert "FINAL" not in probe and "LIMIT" not in raw + probe


@pytest.mark.unit
@pytest.mark.parametrize(
    "mode",
    ["legacy", "anchor", "sample-zero", "sample-full", "sort", "newer", "navigation"],
)
def test_value_population_opt_in_preserves_nonordinary_seed_sql(mode):
    kwargs = {
        "anchor": {"bounded_anchor_probe": True},
        "sample-zero": {"bounded_sampling_rate": 0, "bounded_sampling_salt": "fixture"},
        "sample-full": {
            "bounded_sampling_rate": 100,
            "bounded_sampling_salt": "fixture",
        },
        "sort": {"sort_params": [{"column_id": "cost", "direction": "asc"}]},
    }.get(mode, {})
    cls = SpanListQueryBuilder if mode == "legacy" else SpanListQueryBuilderV2
    filters = [time_filter(), scalar("value", False, "boolean")]
    target = cls(
        project_id=PROJECT, filters=filters, bounded_internal_scan=True, **kwargs
    )
    old = PreviousAcquisition(
        project_id=PROJECT, filters=filters, bounded_internal_scan=True, **kwargs
    )
    options = {"direction": "newer"} if mode == "newer" else {}
    if mode == "navigation":
        options = {"_navigation_seed": True}
    if mode == "sort":
        for subject in (target, old):
            with pytest.raises(
                ValueError, match="unsupported bounded span filter scan"
            ):
                query(subject)
        return
    actual = query(target, **options)
    assert "SELECT DISTINCT project_id" not in actual[0]
    if mode != "legacy":
        assert actual == query(old, **options)


@pytest.mark.integration
@pytest.mark.parametrize(
    "kind,column,value",
    [("boolean", "attrs_bool", False), ("text", "attrs_string", "Kelvin")],
)
def test_mixed_text_single_witness_does_not_change_joint_membership(
    seed_engine, span_reference, kind, column, value
):
    execute, insert = seed_engine
    if (
        kind == "text"
        and not execute(
            "SELECT count() AS n FROM system.functions WHERE name = 'lowerUTF8'"
        )[0]["n"]
    ):
        pytest.skip("Full Unicode CH25 engine required; reduced chdb lacks lowerUTF8")
    start, end = START - timedelta(days=29), START + timedelta(days=1)
    filters = [
        time_filter(start, end),
        scalar("number", 7),
        scalar("other", value, kind),
    ]
    insert(id="number-only", attrs_number={"number": 7})
    stored_value = int(value) if kind == "boolean" else value
    insert(id="other-only", attrs_number={}, **{column: {"other": stored_value}})
    insert(
        id="wrong-number",
        attrs_number={"number": 8},
        **{column: {"other": stored_value}},
    )
    assert reference_rows(span_reference, execute, filters) == []
    target = builder(filters=filters)
    # One necessary leaf may hit although no physical winner satisfies the
    # conjunction. This permitted false positive must be rejected by FINAL.
    sql, params = target.build_filter_population_time_discovery_query(
        slice_start=START, slice_end=end
    )
    hit = execute(sql, params)[0]["newest_raw_time_us"]
    assert (hit is not None) is (kind == "text")
    # Numeric + Boolean WITHOUT text retains its original joint absence proof.
    page = _engine_population_page(execute, target, filters)
    assert page.complete and page.rows == []
    assert any(a.kind == "population_time_discovery" for a in page.attempts)
    insert(
        id="together", attrs_number={"number": 7}, **{column: {"other": stored_value}}
    )
    expected = reference_rows(span_reference, execute, filters)
    assert [row["id"] for row in expected] == ["together"]
    page = _engine_population_page(execute, target, filters)
    assert page.complete and [row["id"] for row in page.rows] == ["together"]


def assert_mixed_result_queries_unchanged(target, filters, leaves):
    class PreviousPopulationPolicy(SpanListQueryBuilderV2):
        def _mixed_population_plans(self):
            return []

    previous = PreviousPopulationPolicy(
        project_id=PROJECT, filters=filters, bounded_internal_scan=True
    )
    assert query(target) == query(previous)
    classified = target.build_filter_match_query(["seed"])
    assert classified == previous.build_filter_match_query(["seed"])
    # Absence leaves have no necessary seed witness; they belong in the final
    # classifier. Preserve their actual SQL/bindings rather than requiring an
    # incorrect raw-key presence test for a missing attribute.
    assert {leaf["column_id"] for leaf in leaves} <= {
        value for value in classified[1].values() if isinstance(value, str)
    }


@pytest.mark.unit
@pytest.mark.parametrize("leaf_count", [2, 5, 10])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("value", [0, 7])
def test_mixed_gap_uses_one_numeric_witness_without_changing_seed_or_schedule(leaf_count, reverse, value):
    start, end = START - timedelta(days=30), START + timedelta(days=1)
    leaves = [scalar("number", value), scalar("text", "wanted", "text")]
    leaves += [scalar(f"extra{i}", None, op="is_null") if i % 2 == 0
               else scalar(f"extra{i}", "wanted", "text") for i in range(2, leaf_count)]
    filters = [time_filter(start, end), *(reversed(leaves) if reverse else leaves)]
    target = builder(filters=filters)
    # Do not narrow the shared plan list used by actual seed/prefix acquisition.
    assert len(target._filter_population_plans()) >= 2
    assert target.recommended_filter_population_time_discovery_windows() == (
        timedelta(days=7), timedelta(days=28), end - start)
    assert target.recommended_filter_population_time_discovery_window() == end - start
    sql, params = target.build_filter_population_time_discovery_query(slice_start=START, slice_end=end)
    assert "attrs_number" in sql and "attrs_string" not in sql and "attrs_bool" not in sql
    assert {v for k, v in params.items() if k.startswith("latest_filter_key_")} == {"number"}
    assert any(v == value for k, v in params.items() if k.startswith("latest_filter_param_"))
    assert params["population_start_us"] == (START - datetime(1970, 1, 1)) // timedelta(microseconds=1)
    assert params["population_end_us"] == (end - datetime(1970, 1, 1)) // timedelta(microseconds=1)
    assert params["project_id"] == PROJECT and "FROM spans" in sql
    assert all(part not in sql for part in ("FINAL", "is_deleted", "project_version_id", "LIMIT", "SAMPLE"))
    seed, bound = query(target)
    assert "FROM spans FINAL" in seed and "toStartOfHour(start_time)" in seed
    assert_mixed_result_queries_unchanged(target, filters, leaves)
    assert "attrs_string" in seed and "attrs_number" in seed


@pytest.mark.unit
@pytest.mark.parametrize("first_kind", ["number", "boolean"])
@pytest.mark.parametrize("leading_null", [False, True])
def test_mixed_gap_uses_all_explicit_raw_witnesses_and_skips_absence(first_kind, leading_null):
    other_kind = "boolean" if first_kind == "number" else "number"
    leaves = [scalar("text", "wanted", "text")]
    if leading_null:
        leaves.append(scalar("null-only", None, op="is_null"))
    # Native-looking NAME remains a Map attribute because col_type is explicit.
    leaves += [scalar("latency_ms", False if first_kind == "boolean" else 0, first_kind),
               scalar("later", False if other_kind == "boolean" else 0, other_kind)]
    filters = [time_filter(START - timedelta(days=30), START + timedelta(days=1)), *leaves]
    target = builder(filters=filters)
    sql, params = target.build_filter_population_time_discovery_query(slice_start=START, slice_end=START + timedelta(days=1))
    assert "attrs_number" in sql and "attrs_bool" in sql and "attrs_string" not in sql
    assert params[f"latest_filter_key_{int(leading_null)}"] == "latency_ms"
    assert {v for k, v in params.items() if k.startswith("latest_filter_key_")} == {"latency_ms", "later"}
    seed, bound = query(target)
    assert_mixed_result_queries_unchanged(target, filters, leaves)
    assert all(column in seed for column in ("attrs_string", "attrs_number", "attrs_bool"))


@pytest.mark.unit
@pytest.mark.parametrize("other_kind", ["number", "boolean"])
def test_without_text_both_raw_witnesses_and_schedules_remain(other_kind):
    start, end = START - timedelta(days=30), START + timedelta(days=1)
    filters = [time_filter(start, end), scalar("number", 7),
               scalar("other", False if other_kind == "boolean" else 0, other_kind)]
    target = builder(filters=filters)
    sql, params = target.build_filter_population_time_discovery_query(slice_start=START, slice_end=end)
    assert {v for k, v in params.items() if k.startswith("latest_filter_key_")} == {"number", "other"}
    assert "attrs_number" in sql and ("attrs_bool" in sql) is (other_kind == "boolean")
    assert target.recommended_filter_population_time_discovery_windows() == (timedelta(days=7), timedelta(days=28), end - start)


@pytest.mark.unit
def test_native_numeric_metric_is_not_misclassified_as_raw_attribute():
    target = builder(filters=[time_filter(START - timedelta(days=7), START + timedelta(days=1)),
        scalar("latency_ms", 7, col_type="SYSTEM_METRIC"), scalar("text", "wanted", "text")])
    sql, params = target.build_filter_population_time_discovery_query(slice_start=START, slice_end=START + timedelta(days=1))
    assert "attrs_string" in sql and "attrs_number" not in sql
    assert {v for k, v in params.items() if k.startswith("latest_filter_key_")} == {"text"}


@pytest.mark.unit
@pytest.mark.parametrize("op", ["is_null", "not_equals"])
def test_numeric_absence_or_negative_is_never_promoted_to_raw_value(op):
    filters = [time_filter(START - timedelta(days=7), START + timedelta(days=1)),
               scalar("number", None if op == "is_null" else 7, op=op),
               scalar("text", "wanted", "text")]
    target = builder(filters=filters)
    sql, params = target.build_filter_population_time_discovery_query(slice_start=START, slice_end=START + timedelta(days=1))
    # Null has no necessary key presence; negative values allow ONLY the
    # compiler's presence witness, never the forbidden comparison itself.
    if op == "is_null":
        assert "attrs_number" not in sql and "number" not in params.values()
    assert not any(v == 7 for k, v in params.items() if k.startswith("latest_filter_param_"))
    assert "maxOrNull" in sql and "FINAL" not in sql


@pytest.mark.unit
def test_mixed_picker_leaf_cannot_be_reduced_to_its_numeric_or_branch():
    leaves = [scalar("picker", ["wanted", 7], "text", "in",
                     attribute_value_types=["string", "number"]),
              scalar("text", "wanted", "text")]
    target = builder(filters=[time_filter(START - timedelta(days=7), START + timedelta(days=1)), *leaves])
    sql, params = target.build_filter_population_time_discovery_query(slice_start=START, slice_end=START + timedelta(days=1))
    assert "attrs_number" in sql and "attrs_string" in sql and " OR " in sql
    assert "picker" in params.values()


@pytest.mark.integration
@pytest.mark.parametrize("leaf_count", [2, 5, 10])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("pair", ["number_boolean", "number_text", "boolean_text"])
def test_mixed_value_discovery_replays_stale_hours_and_preserves_reference_order(
    seed_engine, span_reference, leaf_count, reverse, pair
):
    execute, insert = seed_engine
    primary_kind, other_kind = pair.split("_")
    primary_column = "attrs_bool" if primary_kind == "boolean" else "attrs_number"
    if other_kind == "text" and not execute("SELECT count() AS n FROM system.functions WHERE name = 'lowerUTF8'")[0]["n"]:
        pytest.skip("Full Unicode CH25 engine required; reduced chdb lacks lowerUTF8")
    start = START + timedelta(minutes=15, microseconds=123456)
    end = START + timedelta(days=365, minutes=45, microseconds=654321)
    other_column = "attrs_bool" if other_kind == "boolean" else "attrs_string"
    good, bad = (0, 1) if other_kind == "boolean" else ("wanted", "other")
    leaves = [scalar("number", False if primary_kind == "boolean" else 0, primary_kind),
              scalar("flag", False if other_kind == "boolean" else good, other_kind)]
    numeric = {} if primary_kind == "boolean" else {"number": 0}
    for index in range(2, leaf_count):
        leaves.append(
            scalar(
                f"extra{index}",
                None if index % 2 == 0 else 1,
                op="is_null" if index % 2 == 0 else "not_equals",
            )
        )
        if index % 2:
            numeric[f"extra{index}"] = 0
    filters = [time_filter(start, end), *(reversed(leaves) if reverse else leaves)]
    values = {"attrs_number": numeric, other_column: {"flag": good}}
    if primary_kind == "boolean":
        values[primary_column] = {"number": 0}
    for discriminator in (
        {},
        {"service_name": "service-b"},
        {"observation_type": "tool"},
    ):
        insert(
            id="wanted", start_time=START + timedelta(days=1), **values, **discriminator
        )
    insert(id="foreign", project_id=OTHER_PROJECT, **values)
    # Every stale raw hit remains in the prefix set, but not in final results.
    replacements = (
        {primary_column: {}},
        {other_column: {"flag": bad}},
        {"is_deleted": 1},
    )
    for index, replacement in enumerate(replacements):
        stamp = START + timedelta(days=300 + index)
        insert(id=f"stale{index}", start_time=stamp, **values)
        insert(
            id=f"stale{index}",
            start_time=stamp + timedelta(minutes=1),
            _version=2,
            **(values | replacement),
        )
    for name, before, after in (
        ("before", start + timedelta(microseconds=1), start - timedelta(microseconds=1)),
        ("after", end - timedelta(microseconds=1), end),
    ):
        insert(id=name, start_time=before, **values)
        insert(id=name, start_time=after, _version=2, **values)
    # Same external IDs in another trace/hour are different physical keys.
    insert(id="wanted", trace_id="other-trace", start_time=START + timedelta(days=1), _version=99, is_deleted=1, **values)
    insert(id="cross-hour", start_time=START + timedelta(days=2, minutes=20), **values)
    insert(id="cross-hour", start_time=START + timedelta(days=2, hours=1, minutes=20), _version=99, is_deleted=1, **values)
    # Equal-version ties are coherent: either possible winner fails the AND.
    stamp = START + timedelta(days=310)
    insert(id="tie-split", start_time=stamp, _version=9, **(values | {other_column: {"flag": bad}}))
    insert(id="tie-split", start_time=stamp, _version=9, **(values | {primary_column: values[primary_column] | {"number": 1}}))
    # A missing selected Map key is not zero/False, even if another physical
    # Map contains the numeric-looking string.
    insert(id="wrong-map-zero", start_time=START + timedelta(days=3), **(values | {
        primary_column: {}, "attrs_string": values.get("attrs_string", {}) | {"number": "0"}}))
    expected = reference_rows(span_reference, execute, filters)
    assert len(expected) == 4 and {row["id"] for row in expected} == {"wanted", "cross-hour"}
    target = builder(filters=filters)
    page = _engine_population_page(execute, target, filters, page_size=2)
    assert page.complete and page.has_more
    next_page = _engine_population_page(
        execute,
        target,
        filters,
        page_size=2,
        cursor_start_time=page.rows[-1]["start_time"],
        cursor_order_token=target.bounded_filter_row_order_token(page.rows[-1]),
    )
    assert next_page.complete and not next_page.has_more
    def identity(row):
        return (
            target.bounded_filter_row_identity(row),
            row["start_time"],
            int(row["_version"]),
        )
    assert list(map(identity, page.rows + next_page.rows)) == list(
        map(identity, expected)
    )
    assert any(attempt.kind == "population_time_discovery" for attempt in page.attempts)


@pytest.mark.integration
@pytest.mark.parametrize("typed_picker", [False, True])
def test_dense_equality_and_typed_picker_union_keep_all_winners(
    seed_engine, span_reference, typed_picker, record_property
):
    execute, _ = seed_engine
    # Dense control: every row inhabits the same primary prefix. No pruning or
    # speedup is asserted; extra acquisition work must not alter pagination.
    execute(
        """INSERT INTO spans (project_id, observation_type, service_name,
        start_time, trace_id, id, attrs_number, attrs_bool, _version)
        SELECT toUUID(%(project)s), 'span', 'service-a', toDateTime64(%(start)s, 6, 'UTC'),
               'trace', toString(number), map('value', if(modulo(number, 2) = 0, 0., 1.)),
               map('value', toUInt8(modulo(number, 2))), 1 FROM numbers(96)""",
        {"project": PROJECT, "start": START},
    )
    selected = (
        scalar(
            "value",
            [0, True],
            "text",
            "in",
            attribute_value_types=["number", "boolean"],
        )
        if typed_picker
        else scalar("value", 0)
    )
    filters = [
        time_filter(START, START + timedelta(days=2)),
        selected,
        scalar("absent", None, op="is_null"),
    ]
    target = builder(filters=filters)
    expected = reference_rows(span_reference, execute, filters)
    assert len(expected) == (96 if typed_picker else 48)
    sql, params = query(target)
    prefixes = execute(raw_population(sql), params)
    assert len(prefixes) == 1
    record_property("dense_raw_prefixes", len(prefixes))
    if typed_picker:
        assert " OR " in raw_population(sql)
    # Reject a mixed-domain OR escaping the mandatory numeric companion.
    if typed_picker:
        filters.append(scalar("value", 0))
        target = builder(filters=filters)
        expected = reference_rows(span_reference, execute, filters)
        assert len(expected) == 48
    actual, before = [], None
    for _ in range(8):
        options = (
            {}
            if before is None
            else {
                "before_start_time": before["start_time"],
                "before_id": target.bounded_filter_row_order_token(before),
            }
        )
        rows = execute(*query(target, limit=17, **options))
        actual.extend(rows)
        if len(rows) < 17:
            break
        before = rows[-1]
    else:
        pytest.fail("finite dense fixture did not exhaust its keyset")
    assert actual == execute(*query(builder(previous=True, filters=filters), limit=100))
    def identity(row):
        return (
            target.bounded_filter_row_identity(row),
            int(row["_version"]),
        )
    assert list(map(identity, actual)) == list(map(identity, expected))
    page = _engine_population_page(execute, target, filters)
    assert page.complete and page.has_more
    assert list(map(identity, page.rows)) == list(map(identity, expected[:25]))


@pytest.mark.integration
@pytest.mark.parametrize("mixed", [False, True])
def test_picker_value_index_preserves_unicode_and_latest_winners(
    seed_engine, span_reference, mixed
):
    execute, insert = seed_engine
    # Synthetic database only; these match the deployed value-index expressions.
    for name, expression in (
        ("str_values", "arrayMap(x -> lower(x), mapValues(attrs_string))"),
        ("num_values", "mapValues(attrs_number)"),
    ):
        execute(f"ALTER TABLE spans ADD INDEX {name} {expression} TYPE bloom_filter GRANULARITY 1")
    selected = scalar(
        "value", ["K", 7, False] if mixed else ["K", "7"], "text", "in",
        attribute_value_types=["string", "number", "boolean"] if mixed else ["string", "string"],
    )
    filters = [time_filter(START, START + timedelta(days=2)), selected]
    for identity, maps in (
        ("ascii", {"attrs_string": {"value": "K"}}),
        ("kelvin", {"attrs_string": {"value": "K"}}),
        ("numeric", {"attrs_number": {"value": 7}}),
        ("string-number", {"attrs_string": {"value": "7"}}),
        ("false", {"attrs_bool": {"value": 0}}),
        ("wrong-key", {"attrs_string": {"other": "K"}}),
        ("missing", {}),
    ):
        insert(id=identity, attrs_number=maps.get("attrs_number", {}), **{k:v for k,v in maps.items() if k != "attrs_number"})
    for identity, correction in (
        ("removed", {"attrs_string": {}}),
        ("changed", {"attrs_string": {"value": "different"}}),
        ("deleted", {"is_deleted": 1}),
    ):
        insert(id=identity, attrs_string={"value": "K"})
        insert(id=identity, _version=2, **correction)
    insert(id="foreign", project_id=OTHER_PROJECT, attrs_string={"value": "K"})
    expected = reference_rows(span_reference, execute, filters)
    assert {r["id"] for r in expected} == (
        {"ascii", "kelvin", "numeric", "false"} if mixed
        else {"ascii", "kelvin", "string-number"}
    )
    target = builder(filters=filters)
    sql, params = query(target)
    assert "arrayMap(x -> lower(x), mapValues(attrs_string))" in raw_population(sql)
    actual = execute(sql, params)
    def identity(r):
        return target.bounded_filter_row_identity(r), int(r["_version"])

    assert list(map(identity, actual)) == list(map(identity, expected))
