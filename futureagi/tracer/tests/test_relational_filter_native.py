"""Offline relational predicates on native CH, with independent identity oracles.

Reuse the physical-span/Score fixtures; no ORM, sockets or service databases.
These are compiler/classifier proofs, not live adapter qualification.
"""

import json
import socket
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest

from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    EvalFilterMetadata,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    partition_span_filter_plans,
    partition_trace_filter_plans,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    PROJECT,
    START,
    row,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_span_score_physical_identity import LABEL
from tracer.tests.test_span_score_physical_identity import (
    scored_engine as scored_engine,
)

pytestmark = pytest.mark.unit
CONFIG = str(uuid5(NAMESPACE_URL, "relational-eval"))
LONG_TEXT = "Équipe literal 50%_done \\ " * 30


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Relational native regression attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def identity(name):
    return str(uuid5(NAMESPACE_URL, "relational-trace-" + name))


def leaf(kind, operation, value=None, *, column=CONFIG, source="EVAL_METRIC"):
    return {
        "column_id": column,
        "filter_config": {
            "col_type": source,
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": value,
        },
    }


@pytest.fixture(params=["tracer_eval_logger", "tracer_eval_logger_v2"])
def relational_engine(scored_engine, request, settings):  # noqa: F811 -- pytest fixture injection
    execute, insert_span, insert_score = scored_engine
    table = request.param
    settings.CH25_EVAL_LOGGER_TABLE = table
    direct = table.endswith("_v2")
    version = "_version" if direct else "_peerdb_version"
    state = "is_deleted UInt8" if direct else "deleted UInt8, _peerdb_is_deleted UInt8"
    execute(f"""CREATE TABLE {table} (
        id UUID, trace_id Nullable(UUID), observation_span_id Nullable(String),
        custom_eval_config_id UUID, created_at DateTime64(6, 'UTC'),
        output_bool Nullable(UInt8), output_float Nullable(Float64),
        output_str Nullable(String), output_str_list String, error UInt8,
        {state}, {version} UInt64
    ) ENGINE = ReplacingMergeTree({version}) ORDER BY id""")
    execute(f"SYSTEM STOP MERGES {table}")

    def insert_eval(name, *, revision=1, hard_deleted=0, soft_deleted=0, **changes):
        values = {
            "id": str(uuid5(NAMESPACE_URL, "relational-eval-" + name)),
            "trace_id": identity(name),
            "observation_span_id": name,
            "custom_eval_config_id": CONFIG,
            # Relational history is deliberately older than every root window.
            "created_at": START - timedelta(days=400),
            "output_bool": None,
            "output_float": None,
            "output_str": None,
            "output_str_list": "[]",
            "error": 0,
            version: revision,
            **(
                {"is_deleted": hard_deleted or soft_deleted}
                if direct
                else {
                    "deleted": soft_deleted,
                    "_peerdb_is_deleted": hard_deleted,
                }
            ),
            **changes,
        }
        execute(
            f"INSERT INTO {table} ({', '.join(values)}) VALUES ("
            + ", ".join(f"%({key})s" for key in values)
            + ")",
            values,
        )

    return execute, insert_span, insert_score, insert_eval


def eval_matches(execute, compiler, output, operation, value, *, mode="trace"):
    predicate, params = compiler(
        project_id=PROJECT,
        query_mode=mode,
        score_date_scope=False,
        eval_filter_metadata={CONFIG: EvalFilterMetadata((CONFIG,), output)},
    ).translate([leaf("text", operation, value)])
    result = execute(
        "SELECT id FROM spans FINAL WHERE is_deleted = 0 AND " + predicate,
        {"project_id": PROJECT, **params},
    )
    return {item["id"] for item in result}


@pytest.mark.parametrize(
    "compiler", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
)
@pytest.mark.parametrize("mode", ["trace", "span"])
@pytest.mark.parametrize("storage", ["output_str", "output_str_list"])
@pytest.mark.parametrize(
    "operation", ["contains", "not_contains", "starts_with", "ends_with"]
)
@pytest.mark.parametrize(
    "needle,decoy", [("50%_done", "50XXdone"), (r"a\b", "ab"), ("ÉQUIPE", "other")]
)
def test_eval_choice_text_is_literal_and_caseless(
    relational_engine, compiler, mode, storage, operation, needle, decoy
):
    execute, insert_span, _, insert_eval = relational_engine
    for name, text in (("literal", needle.lower()), ("decoy", decoy)):
        insert_span(id=name, trace_id=identity(name))
        insert_eval(
            name, **{storage: json.dumps([text]) if storage.endswith("list") else text}
        )
    expected = {"decoy"} if operation == "not_contains" else {"literal"}
    assert (
        eval_matches(execute, compiler, "CHOICES", operation, needle, mode=mode)
        == expected
    )


@pytest.mark.parametrize(
    "output,column,selected,other,values",
    [
        ("SCORE", "output_float", 0.5, 0.8, [50, 60]),
        ("PASS_FAIL", "output_bool", 1, 0, ["Passed"]),
        ("CHOICES", "output_str_list", '["yes"]', '["other"]', ["yes", "extra"]),
    ],
)
@pytest.mark.parametrize(
    "operation,expected",
    [
        ("in", {"live"}),
        ("not_in", {"changed"}),
        ("is_not_null", {"live", "changed"}),
        ("is_null", {"hard", "soft", "error", "blank", "absent", "foreign"}),
    ],
)
def test_eval_typed_multivalues_apply_after_latest_state(
    relational_engine, output, column, selected, other, values, operation, expected
):
    execute, insert_span, _, insert_eval = relational_engine
    for name in (
        "live",
        "changed",
        "hard",
        "soft",
        "error",
        "blank",
        "absent",
        "foreign",
    ):
        insert_span(id=name, trace_id=identity(name))
        if name != "absent":
            insert_eval(
                name,
                **{column: selected},
                **(
                    {
                        "custom_eval_config_id": str(
                            uuid5(NAMESPACE_URL, "foreign-config")
                        )
                    }
                    if name == "foreign"
                    else {}
                ),
            )
    for name, changes in (
        ("changed", {column: other}),
        ("hard", {"hard_deleted": 1}),
        ("soft", {"soft_deleted": 1}),
        ("error", {"error": 1}),
        ("blank", {column: "[]" if column.endswith("list") else None}),
    ):
        insert_eval(name, revision=2, **changes)
    assert (
        eval_matches(execute, ClickHouseFilterBuilderV2, output, operation, values)
        == expected
    )


@pytest.mark.parametrize(
    "partition", [partition_trace_filter_plans, partition_span_filter_plans]
)
def test_type_first_mixed_relational_partition(partition):
    raw_text = leaf("text", "equals", LONG_TEXT, column=LABEL, source="SPAN_ATTRIBUTE")
    raw_number = leaf(
        "number", "greater_than", 0, column=CONFIG, source="SPAN_ATTRIBUTE"
    )
    annotation = leaf("text", "equals", "yes", column=LABEL, source="ANNOTATION")
    evaluation = leaf("number", "in", [50, 60])
    plans, residual = partition(
        [time_filter(), raw_text, annotation, raw_number, evaluation]
    )
    assert len(plans) == 2
    assert residual == [annotation, evaluation]


@pytest.mark.parametrize("scored_engine", ["UUID"], indirect=True)
@pytest.mark.parametrize("mode", ["trace", "span"])
@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize(
    "operation,expected",
    [
        ("equals", {"live"}),
        ("not_equals", {"changed"}),
        ("is_null", {"hard", "absent"}),
    ],
)
def test_mixed_long_text_number_annotation_eval_uses_root_window_and_all_history(
    relational_engine, mode, days, operation, expected
):
    execute, insert_span, insert_score, insert_eval = relational_engine
    candidates = []
    for name in (
        "live",
        "changed",
        "hard",
        "absent",
        "wrong-number",
        "wrong-text",
        "outside",
    ):
        started = START - timedelta(days=days + 1) if name == "outside" else START
        span = row(id=name, trace_id=identity(name), start_time=started)
        candidates.append(span)
        insert_span(
            **span,
            parent_span_id=None,
            attrs_string={LABEL: "other" if name == "wrong-text" else LONG_TEXT},
            attrs_number={CONFIG: -1 if name == "wrong-number" else 2},
        )
        insert_eval(name, output_float=0.5)
        if name != "absent":
            score = {
                "id": name,
                "trace_id": identity(name),
                "observation_span_id": name,
                "created_at": START - timedelta(days=400),
            }
            insert_score(**score, value='{"text":"yes"}')
            if name in ("changed", "hard"):
                insert_score(
                    **score,
                    value='{"text":"other"}',
                    _peerdb_version=2,
                    _peerdb_is_deleted=int(name == "hard"),
                )
    filters = [
        time_filter(START - timedelta(days=days), START + timedelta(hours=1)),
        leaf("text", "equals", LONG_TEXT, column=LABEL, source="SPAN_ATTRIBUTE"),
        leaf("text", operation, "yes", column=LABEL, source="ANNOTATION"),
        leaf("number", "greater_than", 0, column=CONFIG, source="SPAN_ATTRIBUTE"),
        leaf("number", "in", [50, 60]),
    ]
    cls = TraceListQueryBuilderV2 if mode == "trace" else SpanListQueryBuilderV2
    target = cls(
        project_id=PROJECT,
        filters=filters,
        bounded_internal_scan=True,
        eval_filter_metadata={CONFIG: EvalFilterMetadata((CONFIG,), "SCORE")},
    )
    query = (
        target.build_filter_identity_match_query_from_seed_rows(candidates)
        if mode == "trace"
        else target.build_filter_match_query_from_seed_rows(candidates)
    )
    actual = execute(*query)
    assert {item["trace_id"] for item in actual} == {
        identity(name) for name in expected
    }
