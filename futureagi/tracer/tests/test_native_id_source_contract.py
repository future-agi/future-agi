"""Native list classifiers: structural IDs and same-name raw keys stay distinct."""

import json
from uuid import UUID

import pytest

from tracer.serializers.observation_span import SpanObserveListQuerySerializer
from tracer.serializers.trace import TraceObserveListQuerySerializer
from tracer.serializers.trace_session import TraceSessionListQuerySerializer
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    row,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as physical_engine

KEYS = ("trace_id", "span_id", "session")
SOURCES = (None, "NORMAL", "SYSTEM_METRIC")
SURFACES = {
    "traces": (TraceObserveListQuerySerializer, TraceListQueryBuilderV2, "trace_id"),
    "spans": (SpanObserveListQuerySerializer, SpanListQueryBuilderV2, "id"),
    "sessions": (TraceSessionListQuerySerializer, SessionListQueryBuilderV2, "session_id"),
}


def ids(number):
    return {"trace_id": str(UUID(int=number)), "span_id": f"span-{number}",
            "session": str(UUID(int=number + 100))}


def leaf(key, source, operation="in"):
    config = {"filter_type": "text", "filter_op": operation, "filter_value": None if operation in {"is_null", "is_not_null"} else [ids(1)[key]]}
    if source is not None:
        config["col_type"] = source
    return {"column_id": key, "filter_config": config}


def request_serializer(surface, filters):
    return SURFACES[surface][0](data={
        "project_id": PROJECT, "filters": json.dumps([time_filter(), *filters]),
    })


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    yield from physical_engine.__wrapped__(tmp_path_factory.mktemp("native-id-source"))


@pytest.fixture(scope="module")
def population(engine):
    execute, insert = engine
    execute("ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)")
    execute("""CREATE TABLE trace_session_id_remap (
        old_id UUID, new_id UUID, _version UInt64
    ) ENGINE = ReplacingMergeTree(_version) ORDER BY old_id""")
    candidates = []
    for number in (1, 2, 3):
        native = ids(number)
        value = row(trace_id=native["trace_id"], id=native["span_id"],
                    trace_session_id=native["session"], parent_span_id=None,
                    attrs_string=ids(3 - number) if number < 3 else {}, attrs_number={})
        insert(**value)
        candidates.append(value)
    # A higher-version foreign raw witness for C must not contaminate this scope.
    insert(**{**candidates[2], "project_id": OTHER_PROJECT,
              "attrs_string": ids(1), "_version": 99})
    return execute, candidates


def matches(population, surface, filters):
    execute, candidates = population
    serializer = request_serializer(surface, filters)
    assert serializer.is_valid(), serializer.errors
    _, builder_cls, output_key = SURFACES[surface]
    target = builder_cls(project_id=PROJECT, filters=serializer.validated_data["filters"],
                         bounded_internal_scan=True)
    if surface == "traces":
        query = target.build_filter_identity_match_query_from_seed_rows(candidates)
    elif surface == "spans":
        query = target.build_filter_match_query_from_seed_rows(candidates)
    else:
        query = target.build_filter_match_query([ids(n)["session"] for n in (1, 2, 3)])
    return sorted(str(value[output_key]) for value in execute(*query))


def assert_rows(population, surface, filters, numbers):
    identity_key = {"traces": "trace_id", "spans": "span_id", "sessions": "session"}[surface]
    expected = sorted(ids(n)[identity_key] for n in numbers)
    actual = matches(population, surface, filters)
    assert actual == expected, {"surface": surface, "filters": filters, "actual": actual, "expected": expected}


@pytest.mark.integration
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("operation,numbers", [
    ("in", (2,)), ("not_in", (1,)), ("is_null", (3,)), ("is_not_null", (1, 2)),
])
def test_raw_id_collision_typed_presence(population, surface, key, operation, numbers):
    # Value negatives require typed presence; missing C matches only is_null.
    # Contract: latest_filter_predicates._comparison + grouped is_null handling.
    assert_rows(population, surface, [leaf(key, "SPAN_ATTRIBUTE", operation)], numbers)


@pytest.mark.integration
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("source", SOURCES, ids=["omitted", "NORMAL", "SYSTEM_METRIC"])
@pytest.mark.parametrize("operation,numbers", [("in", (1,)), ("not_in", (2, 3))])
def test_native_id_sources(population, surface, key, source, operation, numbers):
    assert_rows(population, surface, [leaf(key, source, operation)], numbers)


@pytest.mark.integration
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("source", SOURCES, ids=["omitted", "NORMAL", "SYSTEM_METRIC"])
@pytest.mark.parametrize("operation,numbers", [("in", ()), ("not_in", (2,))])
def test_same_name_native_and_raw_conjunction(population, surface, key, source, operation, numbers):
    # Both same-name leaves must survive, including empty and nonempty intersections.
    filters = [leaf(key, source, operation), leaf(key, "SPAN_ATTRIBUTE")]
    assert_rows(population, surface, filters, numbers)


@pytest.mark.unit
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("key", KEYS)
def test_property_id_source_mismatch_stays_rejected(surface, key):
    for source, property_id in (
        (None, f"system_attribute:traces:{key}"),
        ("SYSTEM_METRIC", f"system_attribute:traces:{key}"),
        ("SPAN_ATTRIBUTE", f"custom_attribute:{key}"),
    ):
        serializer = request_serializer(surface, [{**leaf(key, source), "property_id": property_id}])
        assert serializer.is_valid(), serializer.errors
    for source, property_id in (
        ("NORMAL", f"system_attribute:traces:{key}"),
        ("SPAN_ATTRIBUTE", f"system_attribute:traces:{key}"),
        ("NORMAL", f"custom_attribute:{key}"),
        ("SYSTEM_METRIC", f"custom_attribute:{key}"),
    ):
        serializer = request_serializer(surface, [{**leaf(key, source), "property_id": property_id}])
        assert not serializer.is_valid(), (source, property_id)
        assert "property_id does not match filter col_type" in str(serializer.errors)
