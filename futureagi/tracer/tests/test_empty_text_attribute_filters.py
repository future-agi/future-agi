"""Stored empty text is a value; an absent typed Map key is not."""
# Pytest injects the imported, reusable engine fixture by its name.
# ruff: noqa: F811

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    LatestFilterPredicate,
    UnsupportedFilterShapeError,
    compile_span_filter_plans,
    compile_trace_filter_plans,
)
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_v1_sql_to_v2
from tracer.tests.test_span_latest_state_engine_ch25 import (
    DockerEngine,
    LiveEngine,
    engine,  # noqa: F401 - reusable isolated ClickHouse 25.3 fixture
)

Compiler = Callable[[list[dict[str, object]]], list[LatestFilterPredicate]]
COMPARISONS = [
    ("equals", "", None),
    ("not_equals", "", None),
    ("in", ["", "absent"], None),
    ("not_in", ["", "absent"], None),
    ("in", ["", 7], ["string", "number"]),
    ("not_in", ["", 7], ["string", "number"]),
]


def _bind(sql: str, params: dict[str, object]) -> str:
    context = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))
    return sql % escape_params(params, context)


def _attribute_filter(
    operation: str, value: object, storage_types: list[str] | None = None
) -> dict[str, object]:
    config = {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "text",
        "filter_op": operation,
        "filter_value": value,
    }
    if storage_types is not None:
        config["attribute_value_types"] = storage_types
    return {"column_id": "empty_text", "filter_config": config}


@pytest.mark.parametrize(
    "compiler", [compile_trace_filter_plans, compile_span_filter_plans]
)
@pytest.mark.parametrize("operation,value,storage_types", COMPARISONS)
def test_empty_text_comparisons_preserve_value_and_typed_presence(
    compiler: Compiler, operation: str, value: object, storage_types: list[str] | None
) -> None:
    (plan,) = compiler([_attribute_filter(operation, value, storage_types)])
    text_parameter = (
        "latest_filter_param_0_string" if storage_types else "latest_filter_param_0"
    )
    assert plan.params[text_parameter] == (
        ("",) if storage_types else tuple(value) if isinstance(value, list) else value
    )
    assert "latest_attr_exists_0" in plan.predicate
    assert "mapContains(span_attr_str, %(latest_filter_key_0)s)" in plan.seed_predicate
    if operation in {"equals", "in"}:
        assert plan.raw_witness_predicate is not None
        assert (
            "mapContains(span_attr_str, %(latest_filter_key_0)s)"
            in plan.raw_witness_predicate
        )
        # At a tied maximum version, exists and value can come from different
        # physical rows. Missing text defaults must never prune that identity.
        graph_witness = plan.raw_graph_value_witness_predicate
        assert graph_witness is None or text_parameter not in graph_witness


@pytest.mark.parametrize(
    "compiler", [compile_trace_filter_plans, compile_span_filter_plans]
)
@pytest.mark.parametrize("value", [None, [None], []])
def test_empty_text_support_does_not_admit_null_or_empty_sets(
    compiler: Compiler, value: object
) -> None:
    operation = "in" if isinstance(value, list) else "equals"
    with pytest.raises(UnsupportedFilterShapeError):
        compiler([_attribute_filter(operation, value)])


@pytest.mark.parametrize(
    "compiler", [compile_trace_filter_plans, compile_span_filter_plans]
)
@pytest.mark.parametrize("storage_type", ["number", "boolean"])
def test_empty_text_is_not_an_empty_number_or_boolean(
    compiler: Compiler, storage_type: str
) -> None:
    with pytest.raises(UnsupportedFilterShapeError):
        compiler([_attribute_filter("in", [""], [storage_type])])


@pytest.mark.integration
@pytest.mark.parametrize(
    "compiler", [compile_trace_filter_plans, compile_span_filter_plans]
)
@pytest.mark.parametrize("operation,value,storage_types", COMPARISONS)
def test_empty_text_latest_membership_matches_physical_winners(
    engine: DockerEngine | LiveEngine,
    compiler: Compiler,
    operation: str,
    value: object,
    storage_types: list[str] | None,
) -> None:
    project = str(uuid.uuid4())
    rows = [
        ("empty", {"empty_text": ""}, {}, 1, 0),
        ("text", {"empty_text": "other"}, {}, 1, 0),
        ("absent", {}, {}, 1, 0),
        ("json-null", {}, {}, 1, 0),
        ("number", {}, {"empty_text": 7}, 1, 0),
        ("cleared", {"empty_text": ""}, {}, 1, 0),
        ("cleared", {}, {}, 2, 0),
        ("changed", {"empty_text": ""}, {}, 1, 0),
        ("changed", {"empty_text": "other"}, {}, 2, 0),
        ("deleted", {"empty_text": ""}, {}, 1, 0),
        ("deleted", {"empty_text": ""}, {}, 2, 1),
        ("to-empty", {"empty_text": "other"}, {}, 1, 0),
        ("to-empty", {"empty_text": ""}, {}, 2, 0),
        ("tied", {"empty_text": "other"}, {}, 3, 0),
        ("tied", {}, {}, 3, 0),
    ]
    for span_id, text, number, version, deleted in rows:
        engine.execute(
            _bind(
                "INSERT INTO spans (project_id, observation_type, service_name, start_time, "
                "trace_id, id, name, attrs_string, attrs_number, attributes_extra, _version, is_deleted) "
                "VALUES (%(project)s, 'span', 'empty-test', toDateTime64('2026-08-08 12:20:00', 6, 'UTC'), "
                "%(span_id)s, %(span_id)s, 'empty-test', %(text)s, %(number)s, %(extra)s, %(version)s, %(deleted)s)",
                {
                    "project": project,
                    "span_id": span_id,
                    "text": text,
                    "number": number,
                    "extra": '{"empty_text":null}' if span_id == "json-null" else "{}",
                    "version": version,
                    "deleted": deleted,
                },
            )
        )
    winners = [
        json.loads(line)
        for line in engine.execute(
            _bind(
                "SELECT id, attrs_string, attrs_number, is_deleted FROM spans FINAL "
                "WHERE project_id = toUUID(%(project)s) FORMAT JSONEachRow",
                {"project": project},
            )
        ).splitlines()
    ]
    choices = (
        list(zip(storage_types, value, strict=True))
        if storage_types
        else [
            ("string", selected)
            for selected in (value if isinstance(value, list) else [value])
        ]
    )
    expected = set()
    for row in winners:
        if row["is_deleted"] or row["id"] == "tied":
            continue
        maps = {"string": row["attrs_string"], "number": row["attrs_number"]}
        present = any("empty_text" in maps[kind] for kind, _ in choices)
        matches = any(
            maps[kind].get("empty_text") == selected for kind, selected in choices
        )
        if (
            (present and not matches)
            if operation in {"not_equals", "not_in"}
            else matches
        ):
            expected.add(row["id"])

    (plan,) = compiler([_attribute_filter(operation, value, storage_types)])
    bindings = {**plan.params, "project": project}
    classifier = rewrite_v1_sql_to_v2(
        "SELECT id FROM (SELECT id, argMax(is_deleted, _peerdb_version) AS gone, "
        + ", ".join(plan.aggregates)
        + " FROM spans WHERE project_id = toUUID(%(project)s) "
        "GROUP BY project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id) "
        "WHERE gone = 0 AND (" + plan.predicate + ") ORDER BY id"
    )
    actual = set(engine.execute(_bind(classifier, bindings)).splitlines())
    assert actual - {"tied"} == expected
    if operation in {"equals", "in"}:
        witness = (
            plan.raw_graph_value_witness_predicate or plan.raw_key_witness_predicate
        )
        assert witness is not None
        candidates = set(
            engine.execute(
                _bind(
                    rewrite_v1_sql_to_v2(
                        "SELECT DISTINCT id FROM spans WHERE project_id = toUUID(%(project)s) AND ("
                        + witness
                        + ")"
                    ),
                    bindings,
                )
            ).splitlines()
        )
        assert actual <= candidates
