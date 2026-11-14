"""Population discovery reads keys and index companions, never Map values.

Discovery only has to locate an interval that could carry a match; the
unchanged seed, latest-state classifier and hydration still apply every leaf.
So the probe may prune granules through the deployed value indexes without
decompressing the attribute value stream that dwarfs them.
"""

from datetime import timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    PROJECT,
    START,
    time_filter,
)

DAY = timedelta(days=1)


def without_index_hints(sql):
    """Drop every balanced ``indexHint(...)`` group from rendered SQL.

    What remains is what ClickHouse actually evaluates per surviving row, so
    assertions about decompressed streams belong against this text.
    """

    out, index = [], 0
    while True:
        found = sql.find("indexHint(", index)
        if found < 0:
            out.append(sql[index:])
            return "".join(out)
        out.append(sql[index:found])
        depth, cursor = 0, found + len("indexHint(") - 1
        while cursor < len(sql):
            if sql[cursor] == "(":
                depth += 1
            elif sql[cursor] == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        index = cursor + 1


def attribute(key, filter_type="text", filter_op="equals", filter_value="wanted"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


def subject(*leaves, days=30):
    return SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[time_filter(START - timedelta(days=days), START + DAY), *leaves],
        bounded_internal_scan=True,
    )


def probe(target, width=DAY):
    return target.build_filter_population_time_discovery_query(
        slice_start=START, slice_end=START + width
    )


@pytest.mark.unit
@pytest.mark.parametrize("filter_type", ["text", "string"])
@pytest.mark.parametrize(
    "extra",
    [
        # The failing sweep shapes: one scalar equality conjoined with 1, 4 or
        # 9 presence leaves. Every one of them used to carry the value
        # comparison into the probe over the whole remaining request window.
        (),
        (attribute("k1", filter_op="is_not_null", filter_value=None),),
        tuple(
            attribute(f"k{i}", filter_op="is_not_null", filter_value=None)
            for i in range(1, 10)
        ),
    ],
    ids=["short-equals", "and-2", "and-10"],
)
def test_probe_carries_key_presence_and_index_hints_only(filter_type, extra):
    target = subject(attribute("value", filter_type=filter_type), *extra)
    sql, params = probe(target)
    evaluated = without_index_hints(sql)
    # The value indexes are offered to the planner...
    assert "indexHint((has(arrayMap(x -> lowerUTF8(x), mapValues(attrs_string))" in sql
    assert "hasAny(arrayMap(x -> lower(x), mapValues(attrs_string))" in sql
    # ...and the per-row work is key presence on the thin ``.keys`` stream.
    assert "has(attrs_string.keys, %(latest_filter_key_0)s)" in evaluated
    assert "attrs_string[" not in evaluated
    assert "mapValues(" not in evaluated
    assert "lowerUTF8(" not in evaluated
    assert all(
        fragment not in sql for fragment in ("FINAL", "is_deleted", "LIMIT", "SAMPLE")
    )
    assert params["latest_filter_key_0"] == "value"
    assert {
        params[f"latest_filter_key_{position}"] for position in range(1, len(extra) + 1)
    } == {leaf["column_id"] for leaf in extra}


@pytest.mark.unit
@pytest.mark.parametrize(
    "extra",
    [
        (),
        (attribute("k1", filter_op="is_not_null", filter_value=None),),
        (attribute("k1", filter_type="number", filter_value=7),),
        (attribute("k1", filter_type="boolean", filter_value=True),),
    ],
    ids=["short-equals", "and-2-presence", "and-2-number", "and-2-boolean"],
)
def test_every_text_lane_stays_on_the_one_day_rung(extra):
    target = subject(attribute("value"), *extra)
    assert target.recommended_filter_population_time_discovery_windows() == (DAY,)
    assert target.recommended_filter_population_time_discovery_window() == timedelta(
        hours=24
    )
    with pytest.raises(ValueError):
        probe(target, width=timedelta(days=2))


@pytest.mark.unit
def test_lane_without_a_text_leaf_keeps_its_widening_ladder():
    start, end = START - timedelta(days=30), START + DAY
    target = subject(attribute("value", filter_type="boolean", filter_value=True))
    assert target.recommended_filter_population_time_discovery_windows() == (
        timedelta(days=7),
        timedelta(days=28),
        end - start,
    )
    assert target.recommended_filter_population_time_discovery_window() == end - start


@pytest.mark.unit
def test_leaves_without_a_value_index_keep_their_existing_witness():
    # Boolean Map values are a byte per row and carry no value index, so the
    # compiler's full raw witness remains the cheapest exhaustive predicate.
    target = subject(attribute("flag", filter_type="boolean", filter_value=True))
    sql, _ = probe(target, width=timedelta(days=1))
    evaluated = without_index_hints(sql)
    assert "has(attrs_bool.keys, %(latest_filter_key_0)s)" in evaluated
    assert "attrs_bool[%(latest_filter_key_0)s]" in evaluated
