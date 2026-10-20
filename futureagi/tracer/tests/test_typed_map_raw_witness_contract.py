from __future__ import annotations

import sys
from collections.abc import Callable

import pytest

from tracer.services.clickhouse.query_builders import latest_filter_predicates
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    LatestFilterPredicate,
    compile_span_filter_plans,
    compile_trace_filter_plans,
)
from tracer.utils.attribute_suggestion_contract import (
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)

Compiler = Callable[[list[dict[str, object]]], list[LatestFilterPredicate]]


def _attribute_filter(
    *,
    filter_type: str,
    operation: str,
    value: object,
) -> dict[str, object]:
    return {
        "column_id": "final_status",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _plan(
    compiler: Compiler,
    *,
    filter_type: str,
    operation: str,
    value: object,
) -> LatestFilterPredicate:
    plans = compiler(
        [
            _attribute_filter(
                filter_type=filter_type,
                operation=operation,
                value=value,
            )
        ]
    )
    assert len(plans) == 1
    return plans[0]


@pytest.mark.parametrize(
    "compiler",
    [compile_trace_filter_plans, compile_span_filter_plans],
    ids=["trace", "span"],
)
@pytest.mark.parametrize(
    ("filter_type", "map_column", "equals_value", "in_values"),
    [
        ("text", "span_attr_str", "Rejected", ["Rejected", "Approved"]),
        ("number", "span_attr_num", 7, [7, 9]),
        ("boolean", "span_attr_bool", True, [True, False]),
    ],
)
@pytest.mark.parametrize("operation", ["equals", "in"])
def test_positive_typed_map_equality_raw_witness_binds_key_and_value(
    compiler: Compiler,
    filter_type: str,
    map_column: str,
    equals_value: object,
    in_values: list[object],
    operation: str,
) -> None:
    plan = _plan(
        compiler,
        filter_type=filter_type,
        operation=operation,
        value=equals_value if operation == "equals" else in_values,
    )

    witness = plan.raw_witness_predicate
    assert witness is not None
    assert plan.raw_witness_rank == 0
    assert f"indexHint(has(mapKeys({map_column}), %(latest_filter_key_0)s))" in witness
    assert f"has({map_column}.keys, %(latest_filter_key_0)s)" in witness
    assert f"mapContains({map_column}, %(latest_filter_key_0)s)" in witness
    assert "%(latest_filter_param_0)s" in witness
    assert plan.params["latest_filter_key_0"] == "final_status"

    comparison = "=" if operation == "equals" else "IN"
    assert f"{map_column}[%(latest_filter_key_0)s]" in witness
    assert f" {comparison} %(latest_filter_param_0)s" in witness

    key_witness = plan.raw_key_witness_predicate
    assert key_witness is not None
    assert f"has({map_column}.keys, %(latest_filter_key_0)s)" in key_witness
    assert "latest_filter_param_0" not in key_witness
    assert f"{map_column}[%(latest_filter_key_0)s]" not in key_witness


@pytest.mark.parametrize(
    ("filter_type", "map_column", "operation", "value", "index_expression"),
    [
        (
            "text",
            "span_attr_str",
            "equals",
            "Rejected",
            "has(arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str)), "
            "%(latest_filter_param_0)s)",
        ),
        (
            "text",
            "span_attr_str",
            "in",
            ["Rejected", "Approved"],
            "hasAny(arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str)), [",
        ),
        (
            "number",
            "span_attr_num",
            "equals",
            7,
            "has(mapValues(span_attr_num), %(latest_filter_param_0)s)",
        ),
        (
            "number",
            "span_attr_num",
            "in",
            [7, 9],
            "hasAny(mapValues(span_attr_num), [",
        ),
    ],
)
def test_text_and_numeric_positive_witnesses_keep_only_safe_index_companions(
    filter_type: str,
    map_column: str,
    operation: str,
    value: object,
    index_expression: str,
) -> None:
    plan = _plan(
        compile_span_filter_plans,
        filter_type=filter_type,
        operation=operation,
        value=value,
    )

    assert plan.raw_witness_predicate is not None
    assert index_expression in plan.seed_predicate
    assert index_expression in plan.raw_witness_predicate
    semantic, index_only = plan.seed_predicate.split(" AND indexHint(", 1)
    assert f"mapContains({map_column}, %(latest_filter_key_0)s)" in semantic
    assert f"{map_column}[%(latest_filter_key_0)s]" in semantic
    assert "mapValues(" not in semantic
    assert index_expression in index_only
    assert index_only.endswith(")")
    if operation == "in":
        assert "%(latest_filter_index_0_0)s" in plan.raw_witness_predicate
        assert "%(latest_filter_index_0_1)s" in plan.raw_witness_predicate
    assert map_column in plan.raw_witness_predicate


def test_ascii_filter_keeps_unicode_semantics_with_exhaustive_legacy_witness() -> None:
    stored = "\N{KELVIN SIGN}"
    assert not stored.isascii()
    assert stored.lower() == "k"

    plan = _plan(
        compile_span_filter_plans,
        filter_type="text",
        operation="equals",
        value="K",
    )

    assert plan.params["latest_filter_param_0"] == "k"
    assert "lowerUTF8(toString(span_attr_str[" in plan.seed_predicate
    assert "lowerUTF8(toString(span_attr_str[" in plan.raw_witness_predicate
    assert "arrayMap(x -> lower(x), mapValues(span_attr_str))" in plan.seed_predicate
    assert "arrayMap(x -> lower(x), mapValues(span_attr_str))" in (
        plan.raw_witness_predicate or ""
    )
    assert "arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))" in (
        plan.seed_predicate or ""
    )
    assert "arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))" in (
        plan.raw_witness_predicate or ""
    )
    assert {
        value
        for key, value in plan.params.items()
        if key.startswith("latest_filter_legacy_index_0_")
    } == {"k", stored}


def _kelvin_spellings(value: str) -> set[str]:
    """Every spelling whose ASCII ``lower()`` is ``value``, by bitmask."""

    slots = [position for position, char in enumerate(value) if char == "k"]
    spellings = set()
    for mask in range(1 << len(slots)):
        chars = list(value)
        for bit, position in enumerate(slots):
            if mask >> bit & 1:
                chars[position] = "\N{KELVIN SIGN}"
        spellings.add("".join(chars))
    return spellings


@pytest.mark.parametrize(
    ("operation", "picker_types"),
    [("equals", False), ("in", False), ("in", True)],
    ids=["equals", "in", "picker_in"],
)
@pytest.mark.parametrize("kelvin_slots", [0, 1, 2, 8, 9])
def test_legacy_ascii_bloom_witness_enumerates_every_kelvin_spelling(
    kelvin_slots: int,
    operation: str,
    picker_types: bool,
) -> None:
    value = "K".join(["seg"] * (kelvin_slots + 1))
    leaf = _attribute_filter(
        filter_type="text",
        operation=operation,
        value=[value] if operation == "in" else value,
    )
    if picker_types:
        leaf["filter_config"]["attribute_value_types"] = ["string"]
    (plan,) = compile_trace_filter_plans([leaf])

    legacy = {
        param_value
        for key, param_value in plan.params.items()
        if key.startswith("latest_filter_legacy_index_0_")
    }
    # Past 256 spellings (the ninth "k") the witness stands down entirely and
    # the Unicode comparison stays the only value predicate.
    expected = _kelvin_spellings(value.lower()) if kelvin_slots <= 8 else set()
    assert legacy == expected


def _python_lines_executed(call: Callable[[], object]) -> int:
    """Count the Python lines one call executes, across every frame it enters."""

    executed = 0

    def trace(frame, event, arg):
        nonlocal executed
        if event == "line":
            executed += 1
        return trace

    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        call()
    finally:
        sys.settrace(previous)
    return executed


@pytest.mark.parametrize(
    "compiler",
    [compile_trace_filter_plans, compile_span_filter_plans],
    ids=["trace", "span"],
)
@pytest.mark.parametrize("short", ["absent", "k" * 8], ids=["no_k", "eight_k"])
def test_max_length_text_equality_compiles_without_per_character_work(
    compiler: Compiler,
    short: str,
) -> None:
    """A 16 KiB equals value compiles with the Python work of a short one.

    One Voice list request compiles its filter plans a few hundred times. On
    dev a per-character loop in the legacy bloom witness made a max-length
    absent value cost 6.0 s before the response headers while ClickHouse
    answered in about 0.1 s; the same value at 32 bytes took 0.48 s.
    """

    long = short + "x" * (TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES - len(short))

    def compile_equals(value: str) -> Callable[[], object]:
        return lambda: _plan(
            compiler,
            filter_type="text",
            operation="equals",
            value=value,
        )

    short_lines = _python_lines_executed(compile_equals(short))
    long_lines = _python_lines_executed(compile_equals(long))

    assert long_lines <= short_lines + 64, (short_lines, long_lines)


def _legacy_spellings(plan: LatestFilterPredicate) -> list[str]:
    return [
        value
        for key, value in plan.params.items()
        if key.startswith("latest_filter_legacy_index_")
    ]


def _max_length_value_with_k(kelvin_slots: int) -> str:
    """A 16 KiB ASCII value carrying exactly ``kelvin_slots`` lowercase "k"s."""

    size = TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
    if not kelvin_slots:
        return "x" * size
    chunk = "k" + "x" * (size // kelvin_slots - 1)
    return (chunk * kelvin_slots).ljust(size, "x")


@pytest.mark.parametrize(
    ("operation", "picker_types"),
    [("equals", False), ("in", False), ("in", True)],
    ids=["equals", "in", "picker_in"],
)
@pytest.mark.parametrize("kelvin_slots", [0, 1, 2, 3, 8])
def test_max_length_legacy_witness_builds_no_more_than_one_value_of_spellings(
    kelvin_slots: int,
    operation: str,
    picker_types: bool,
) -> None:
    """The witness is bounded by the bytes it builds, not only by its count.

    Every spelling is the whole value again, so 256 spellings of a 16 KiB
    value are 4 MiB, built each of the ~530 times one Voice list request
    compiled the leaf. On the local stack that value cost 4.1 s per request
    against 0.1 s without its "k"s, while ClickHouse answered in under 20 ms.
    Past one value's worth of spellings the witness stands down, as it does
    past the ninth "k", and the Unicode comparison decides alone.
    """

    value = _max_length_value_with_k(kelvin_slots)
    assert len(value) == TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
    assert value.count("k") == kelvin_slots
    leaf = _attribute_filter(
        filter_type="text",
        operation=operation,
        value=[value] if operation == "in" else value,
    )
    if picker_types:
        leaf["filter_config"]["attribute_value_types"] = ["string"]
    (plan,) = compile_trace_filter_plans([leaf])

    spellings = _legacy_spellings(plan)
    assert sum(map(len, spellings)) <= TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
    # A value with no "k" is its own single spelling and keeps its witness.
    assert spellings == ([value] if kelvin_slots == 0 else [])


@pytest.mark.parametrize(
    ("length", "kept"),
    [(64, True), (65, False)],
    ids=["exactly-one-value", "one-byte-over"],
)
def test_legacy_witness_keeps_every_spelling_while_their_bytes_fit(
    length: int,
    kept: bool,
) -> None:
    """Eight "k"s spell 256 ways: 64 bytes each is 16 KiB, 65 is past it."""

    value = "k" * 8 + "x" * (length - 8)
    plan = _plan(
        compile_trace_filter_plans,
        filter_type="text",
        operation="equals",
        value=value,
    )

    assert set(_legacy_spellings(plan)) == (_kelvin_spellings(value) if kept else set())


def test_kelvin_spellings_are_built_once_per_value_not_once_per_compile() -> None:
    """One Voice list request compiled "kkkkkkkk" ~1,800 times.

    Replayed against an empty ClickHouse that request took 433 ms against
    28 ms for "alpha", 250 ms of it rebuilding the same 256 spellings. The
    plans must stay identical when they come from the per-value memo.
    """

    value = "k" * 8

    def compile_equals() -> LatestFilterPredicate:
        return _plan(
            compile_trace_filter_plans,
            filter_type="text",
            operation="equals",
            value=value,
        )

    memo = latest_filter_predicates._kelvin_sign_spellings
    first = compile_equals()
    before = memo.cache_info()
    repeats = [compile_equals() for _ in range(3)]
    after = memo.cache_info()

    assert (after.hits - before.hits, after.misses - before.misses) == (3, 0)
    assert set(_legacy_spellings(first)) == _kelvin_spellings(value)
    assert all(plan == first for plan in repeats)


def test_non_ascii_filter_declines_legacy_ascii_bloom_witness() -> None:
    plan = _plan(
        compile_span_filter_plans,
        filter_type="text",
        operation="equals",
        value="İstanbul",
    )

    assert "arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))" in (
        plan.seed_predicate or ""
    )
    assert "arrayMap(x -> lower(x), mapValues(span_attr_str))" not in (
        plan.seed_predicate or ""
    )
    assert not any(
        key.startswith("latest_filter_legacy_index_0_") for key in plan.params
    )


@pytest.mark.parametrize(
    ("filter_type", "operation", "value", "map_column"),
    [
        ("text", "contains", "ject", "span_attr_str"),
        ("number", "greater_than", 7, "span_attr_num"),
        ("boolean", "is_not_null", None, "span_attr_bool"),
    ],
)
def test_other_positive_typed_map_witnesses_remain_key_only(
    filter_type: str,
    operation: str,
    value: object,
    map_column: str,
) -> None:
    plan = _plan(
        compile_span_filter_plans,
        filter_type=filter_type,
        operation=operation,
        value=value,
    )

    witness = plan.raw_witness_predicate
    assert witness is not None
    assert plan.raw_witness_rank == 10
    assert f"has({map_column}.keys, %(latest_filter_key_0)s)" in witness
    assert "latest_filter_param_0" not in witness
    assert f"{map_column}[%(latest_filter_key_0)s]" not in witness
    assert plan.raw_key_witness_predicate == witness


@pytest.mark.parametrize(
    ("filter_type", "operation", "value"),
    [
        ("text", "not_equals", "Rejected"),
        ("text", "not_in", ["Rejected", "Approved"]),
        ("text", "not_contains", "ject"),
        ("number", "not_equals", 7),
        ("boolean", "not_in", [True]),
        ("boolean", "is_null", None),
    ],
)
def test_negative_typed_map_value_filters_use_only_necessary_presence(
    filter_type: str,
    operation: str,
    value: object,
) -> None:
    plan = _plan(
        compile_span_filter_plans,
        filter_type=filter_type,
        operation=operation,
        value=value,
    )

    if operation == "is_null":
        assert plan.raw_witness_predicate is None
        assert plan.raw_key_witness_predicate is None
        assert plan.raw_witness_rank is None
    else:
        map_column = {
            "text": "span_attr_str",
            "number": "span_attr_num",
            "boolean": "span_attr_bool",
        }[filter_type]
        witness = (
            f"(indexHint(has(mapKeys({map_column}), %(latest_filter_key_0)s)) AND "
            f"has({map_column}.keys, %(latest_filter_key_0)s))"
        )
        assert plan.raw_witness_predicate == plan.raw_key_witness_predicate == witness
        assert plan.raw_witness_rank == 10
        assert "latest_attr_exists_0 AND" in plan.predicate
    assert plan.raw_graph_value_witness_predicate is None


@pytest.mark.parametrize("compiler", [compile_trace_filter_plans, compile_span_filter_plans])
@pytest.mark.parametrize(
    ("values", "types", "indexed_maps"),
    [
        (["K", "Approved"], ["string", "string"], ["span_attr_str"]),
        (["İstanbul"], ["string"], ["span_attr_str"]),
        ([7, 9], ["number", "number"], ["span_attr_num"]),
        (["K", 7, True], ["string", "number", "boolean"], ["span_attr_str", "span_attr_num"]),
    ],
)
def test_picker_in_reuses_value_indexes_without_changing_typed_membership(
    compiler, values, types, indexed_maps,
) -> None:
    leaf = _attribute_filter(filter_type="text", operation="in", value=values)
    leaf["filter_config"]["attribute_value_types"] = types
    plan, = compiler([leaf])
    raw = plan.raw_witness_predicate
    assert raw is not None
    for map_column in indexed_maps:
        assert f"mapValues({map_column})" in raw
        assert f"mapContains({map_column}, %(latest_filter_key_0)s)" in raw
    assert "mapValues(span_attr_bool)" not in raw
    assert "mapValues(" not in plan.seed_predicate
    assert "indexHint(" not in plan.predicate
    assert plan.raw_graph_value_witness_predicate == raw
    for storage_type in set(types):
        assert f"latest_filter_param_0_{storage_type}" in plan.params
        assert f"latest_attr_exists_0_{storage_type}" in plan.predicate
    legacy = {v for k, v in plan.params.items() if k.startswith("latest_filter_legacy_index_")}
    if "K" in values:
        assert {"k", "\N{KELVIN SIGN}"} <= legacy
    else:
        assert not legacy


@pytest.mark.parametrize("compiler", [compile_trace_filter_plans, compile_span_filter_plans])
@pytest.mark.parametrize(("value", "storage_type"), [(0, "number"), (False, "boolean")])
def test_picker_default_values_keep_graph_key_presence(compiler, value, storage_type):
    leaf = _attribute_filter(filter_type="text", operation="in", value=[value])
    leaf["filter_config"]["attribute_value_types"] = [storage_type]
    plan, = compiler([leaf])
    assert plan.raw_graph_value_witness_predicate == plan.raw_key_witness_predicate
    assert "mapValues(" not in plan.raw_graph_value_witness_predicate
    assert "latest_filter_param_0" in plan.seed_predicate


def test_picker_not_in_does_not_use_positive_value_index_hints():
    leaf = _attribute_filter(filter_type="text", operation="not_in", value=["K", 7, False])
    leaf["filter_config"]["attribute_value_types"] = ["string", "number", "boolean"]
    plan, = compile_span_filter_plans([leaf])
    assert plan.raw_witness_predicate == plan.raw_key_witness_predicate
    assert "mapValues(" not in plan.raw_witness_predicate
    assert plan.raw_graph_value_witness_predicate is None
    assert not any("index_" in key for key in plan.params)
