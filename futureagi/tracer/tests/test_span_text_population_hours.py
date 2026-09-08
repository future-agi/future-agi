"""Thin discovery is a superset; exact physical membership/order is unchanged."""
from datetime import timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    engine,
    time_filter,
)
from tracer.tests.test_span_population_time_discovery import _engine_population_page


def text_filter(key="company_id", op="in", value=None, **config):
    return {"column_id": key, "filter_config": {
        "col_type": "SPAN_ATTRIBUTE", "filter_type": "text", "filter_op": op,
        "filter_value": (["wanted", "Kelvin"] if op == "in" else "wanted") if value is None else value,
        **config,
    }}


def subject(filters, workspace=False):
    scope = {"project_id": None, "project_ids": [PROJECT, OTHER_PROJECT]} if workspace else {"project_id": PROJECT}
    return SpanListQueryBuilderV2(**scope, filters=filters, bounded_internal_scan=True)


@pytest.mark.unit
@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("op", ["equals", "in"])
@pytest.mark.parametrize("kind", ["text", "string"])
def test_text_discovery_is_daily_timestamp_only_and_seed_retains_all_values(workspace, op, kind):
    filters = [time_filter(START - timedelta(days=365), START + timedelta(days=1)),
               text_filter(op=op, filter_type=kind), text_filter("region", "equals", "east")]
    target = subject(filters, workspace)
    assert target.recommended_filter_population_time_discovery_windows() == (timedelta(days=1),)
    sql, params = target.build_filter_population_time_discovery_query(slice_start=START, slice_end=START + timedelta(days=1))
    assert "maxOrNull" in sql and "start_time" in sql
    assert "project_ids" in params if workspace else params["project_id"] == PROJECT
    assert all(fragment not in sql for fragment in ("attrs_", "FINAL", "is_deleted", "LIMIT", "SAMPLE"))
    assert not any(key.startswith("latest_filter_") for key in params)
    seed, bound = target.build_filter_seed_page(slice_start=START, slice_end=START + timedelta(hours=1), limit=25)
    assert "FROM spans FINAL" in seed and "toStartOfHour(start_time)" in seed
    assert "company_id" in bound.values() and "region" in bound.values()
    assert "latest_filter_param_0" in seed and "latest_filter_param_1" in seed
    assert bound["latest_filter_param_0"] == (("wanted", "kelvin") if op == "in" else "wanted")


@pytest.mark.unit
@pytest.mark.parametrize("extra", [
    {"filter_op": "not_in", "filter_value": ["wanted"]},
    {"filter_op": "is_null", "filter_value": None},
    {"filter_op": "contains", "filter_value": "wanted"},
    {"filter_type": "number", "filter_value": [1]},
    {"filter_type": "boolean", "filter_value": [False]},
    {"attribute_value_types": ["string", "number"], "filter_value": ["wanted", 1]},
    {"col_type": "SYSTEM_METRIC", "filter_value": ["native"]},
])
def test_other_operator_type_and_source_policy_is_unchanged(extra):
    filters = [time_filter(START - timedelta(days=7), START + timedelta(days=1)), text_filter("trace_id", **extra)]
    class WitnessPolicy(SpanListQueryBuilderV2):
        def _uses_thin_text_population_discovery(self):
            return False
    for leaves in (filters, [*filters, text_filter("region", "equals", "east")]):
        target = subject(leaves)
        previous = WitnessPolicy(project_id=PROJECT, filters=leaves, bounded_internal_scan=True)
        assert target.recommended_filter_population_time_discovery_windows() == previous.recommended_filter_population_time_discovery_windows()
        bounds = {"slice_start": START, "slice_end": START + timedelta(days=1)}
        assert target.build_filter_population_time_discovery_query(**bounds) == previous.build_filter_population_time_discovery_query(**bounds)


@pytest.mark.unit
@pytest.mark.parametrize("type_key", ["attribute_value_types", "attributeValueTypes"])
def test_explicit_string_provenance_admits_without_dropping_values(type_key):
    leaf = text_filter(value=["wanted", "Kelvin", "FALSE"], **{type_key: ["string"] * 3})
    target = subject([time_filter(START - timedelta(days=7), START + timedelta(days=1)), leaf])
    assert target._uses_thin_text_population_discovery()
    sql, params = target.build_filter_population_time_discovery_query(slice_start=START, slice_end=START + timedelta(days=1))
    assert "attrs_" not in sql and not any(k.startswith("latest_filter_") for k in params)
    seed, bound = target.build_filter_seed_page(slice_start=START, slice_end=START + timedelta(hours=1), limit=25)
    assert "attrs_string" in seed and ("wanted", "kelvin", "false") in bound.values()


@pytest.mark.unit
@pytest.mark.parametrize("types", [["string", "number"], ["string", None], [], ["string"]])
def test_nonhomogeneous_or_misaligned_provenance_does_not_opt_in(types):
    target = subject([time_filter(START - timedelta(days=7), START + timedelta(days=1)),
                      text_filter(value=["wanted", "1"], attribute_value_types=types)])
    assert not target._uses_thin_text_population_discovery()


@pytest.fixture(scope="module")
def population(tmp_path_factory):
    fixture = engine.__wrapped__(tmp_path_factory.mktemp("span-text-hours"))
    execute, insert = next(fixture)
    yes = {"company_id": "wanted", "region": "east"}
    def put(key, when, **kwargs):
        insert(id=key, start_time=when, attrs_string=kwargs.pop("attrs_string", yes), **kwargs)
    put("deleted", START + timedelta(hours=12))
    put("deleted", START + timedelta(hours=12, minutes=10), _version=2, is_deleted=1)
    put("missing", START + timedelta(hours=11), attrs_string={})
    put("cleared", START + timedelta(hours=10))
    put("cleared", START + timedelta(hours=10, minutes=20), attrs_string={}, _version=2)
    put("split-a", START + timedelta(hours=9), attrs_string={"company_id": "wanted", "region": "west"})
    put("split-b", START + timedelta(hours=9), attrs_string={"company_id": "other", "region": "east"})
    put("typed-only", START + timedelta(hours=8), attrs_string={}, attrs_number={"company_id": 1}, attrs_bool={"company_id": 1})
    put("moved", START + timedelta(hours=2, minutes=50))
    put("moved", START + timedelta(hours=2, minutes=10), _version=2)
    for service in ("service-a", "service-b"):
        put("tie", START + timedelta(minutes=20), service_name=service)
    for hour in (0, 1):
        put("cross-hour", START - timedelta(days=2) + timedelta(hours=hour, minutes=20))
    put("old", START - timedelta(days=4) + timedelta(minutes=20))
    put("boundary", START - timedelta(days=5) + timedelta(minutes=40))
    put("boundary", START - timedelta(days=5) + timedelta(minutes=20), _version=2)
    put("authorized", START + timedelta(hours=1), project_id=OTHER_PROJECT)
    put("foreign", START + timedelta(hours=13), project_id="44444444-4444-4444-4444-444444444444")
    yield execute
    try:
        next(fixture)
    except StopIteration:
        pass


@pytest.mark.integration
@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("op,typed", [("equals", False), ("in", False), ("in", True)])
@pytest.mark.parametrize("wanted", ["wanted", "absent"])
def test_native_sparse_latest_and_cross_property_pages(population, workspace, op, typed, wanted):
    filters = [time_filter(START - timedelta(days=5) + timedelta(minutes=30), START + timedelta(days=2)),
               text_filter(op=op, value=[wanted, "unseen-b", "unseen-c"] if op == "in" else wanted,
                           **({"attribute_value_types": ["string"] * 3} if typed else {})),
               text_filter("region", "equals", "east")]
    from tracer.serializers.filters import FilterItemField
    filters = [FilterItemField().run_validation(leaf) for leaf in filters]
    target = subject(filters, workspace)
    calls, visible, cursor = [], [], {}
    def record(sql, params):
        calls.append((sql, params))
        return population(sql, params)
    while True:
        page = _engine_population_page(record, target, filters, page_size=2, **cursor)
        assert page.complete, page.error_code
        visible.extend(page.rows)
        if not page.has_more:
            break
        assert page.rows
        cursor = {"cursor_start_time": page.rows[-1]["start_time"],
                  "cursor_order_token": target.bounded_filter_row_order_token(page.rows[-1])}
    expected = [("moved", "service-a", START + timedelta(hours=2, minutes=10), PROJECT)]
    if workspace:
        expected.append(("authorized", "service-a", START + timedelta(hours=1), OTHER_PROJECT))
    expected += [("tie", service, START + timedelta(minutes=20), PROJECT) for service in ("service-b", "service-a")]
    expected += [("cross-hour", "service-a", START - timedelta(days=2) + timedelta(hours=h, minutes=20), PROJECT) for h in (1, 0)]
    expected.append(("old", "service-a", START - timedelta(days=4) + timedelta(minutes=20), PROJECT))
    if wanted == "absent":
        expected = []
    actual = [(r["id"], r["service_name"], r["start_time"], str(r["project_id"])) for r in visible]
    assert actual == expected, (actual, expected)
    assert all(r["trace_id"] == "trace" and r["observation_type"] == "span" for r in visible)
    assert all(int(r["_version"]) == (2 if r["id"] == "moved" else 1) for r in visible)
    probes = [(sql, p) for sql, p in calls if "population_start_us" in p]
    assert probes and all("attrs_" not in sql for sql, _ in probes)
    assert all(p["population_end_us"] - p["population_start_us"] <= 86400_000_000 for _, p in probes)
