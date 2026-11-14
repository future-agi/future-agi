"""Real ClickHouse agreement between count tiles and their eval filters.

Tests use only the explicitly configured disposable sidecar. Physical versions
collapse before deletion/completion, and reruns then select the latest completed
row per span/config. Percentage readers keep every live attempt.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest

from conftest import _ch_test_native_client
from tracer.services.clickhouse import eval_logger_table as eval_logger_table_config
from tracer.services.clickhouse.query_builders.filters import (
    EvalFilterMetadata,
)
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)

pytestmark = pytest.mark.integration

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
TRACE_ID = str(uuid.uuid4())
PASS_FAIL = str(uuid.uuid4())
CHOICES = str(uuid.uuid4())
T0 = datetime(2026, 10, 1, 12, 0, 0)
BUILDERS = (
    TraceListQueryBuilder,
    TraceListQueryBuilderV2,
    SpanListQueryBuilder,
    SpanListQueryBuilderV2,
    VoiceCallListQueryBuilder,
    VoiceCallListQueryBuilderV2,
)


@pytest.fixture(scope="module")
def ch_client():
    with _ch_test_native_client() as client:
        yield client


def _insert(
    ch_client,
    table,
    span,
    config,
    minute,
    *,
    trace=TRACE_ID,
    ok=None,
    score=None,
    text=None,
    labels=None,
    error=0,
    status="completed",
    row_id=None,
    deleted=0,
    cdc=0,
    version=1,
):
    columns = [
        "id",
        "trace_id",
        "observation_span_id",
        "custom_eval_config_id",
        "output_bool",
        "output_float",
        "output_str",
        "output_str_list",
        "error",
        "target_type",
        "created_at",
    ]
    values = [
        uuid.UUID(row_id) if row_id else uuid.uuid4(),
        uuid.UUID(trace),
        span,
        uuid.UUID(config),
        ok,
        score,
        text,
        json.dumps(labels or []),
        error,
        "span",
        T0 + timedelta(minutes=minute),
    ]
    if table.endswith("_v2"):
        columns += ["is_deleted", "_version"]
        values += [int(bool(deleted or cdc)), version]
    else:
        columns += ["status", "deleted", "_peerdb_is_deleted", "_peerdb_version"]
        values += [status, deleted, cdc, version]
    ch_client.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES", [values])


@pytest.fixture(params=["legacy", "v2"])
def eval_table(ch_client, monkeypatch, settings, request):
    table = f"_test_eval_counts_{uuid.uuid4().hex[:8]}"
    direct = request.param == "v2"
    if direct:
        table += "_v2"
    monkeypatch.setattr(
        eval_logger_table_config,
        "SUPPORTED_EVAL_LOGGER_TABLES",
        eval_logger_table_config.SUPPORTED_EVAL_LOGGER_TABLES | {table},
    )
    settings.CH25_EVAL_LOGGER_TABLE = table

    def test_eval_source(alias="", include_cdc_tombstone_guard=False):
        return eval_logger_table_config.eval_logger_source(
            alias, include_cdc_tombstone_guard, table=table
        )

    for builder in BUILDERS:
        monkeypatch.setattr(
            builder, "_EVAL_LOGGER_SOURCE", staticmethod(test_eval_source)
        )
    state = (
        "is_deleted UInt8, _version UInt64"
        if direct
        else "status Nullable(String), skipped_reason Nullable(String), "
        "deleted Nullable(UInt8), _peerdb_is_deleted UInt8, _peerdb_version Int64"
    )
    version = "_version" if direct else "_peerdb_version"
    ch_client.execute(f"""
        CREATE TABLE {table} (
            id UUID, trace_id Nullable(UUID), observation_span_id Nullable(String),
            custom_eval_config_id UUID, output_bool Nullable(UInt8),
            output_float Nullable(Float64), output_str Nullable(String),
            output_str_list String DEFAULT '[]', error UInt8,
            target_type LowCardinality(String) DEFAULT 'span',
            created_at DateTime64(6), {state}
        ) ENGINE = MergeTree ORDER BY (id, {version})
    """)
    try:
        _insert(ch_client, table, "span-a", PASS_FAIL, 1, ok=1)
        deleted_fail = str(uuid.uuid4())
        _insert(ch_client, table, "span-a", PASS_FAIL, 2, ok=0, row_id=deleted_fail)
        _insert(
            ch_client,
            table,
            "span-a",
            PASS_FAIL,
            2,
            ok=0,
            row_id=deleted_fail,
            deleted=1,
            version=2,
        )
        _insert(ch_client, table, "span-b", PASS_FAIL, 1, ok=1)
        _insert(ch_client, table, "span-b", PASS_FAIL, 2, ok=0)
        _insert(ch_client, table, "span-b", PASS_FAIL, 3, error=1, text="ERROR")
        _insert(ch_client, table, "span-b", CHOICES, 1, labels=["clear"])
        _insert(ch_client, table, "span-b", CHOICES, 2, labels=["unclear"])
        yield table
    finally:
        ch_client.execute(f"DROP TABLE {table}")


def _run(ch_client, query, params):
    data, columns = ch_client.execute(query, params, with_column_types=True)
    names = [name for name, _type in columns]
    return [dict(zip(names, row, strict=True)) for row in data]


def _by_config(rows):
    return {str(row["eval_config_id"]): row for row in rows}


@pytest.mark.parametrize(
    "builder_cls",
    [
        TraceListQueryBuilder,
        TraceListQueryBuilderV2,
        VoiceCallListQueryBuilder,
        VoiceCallListQueryBuilderV2,
    ],
)
def test_trace_and_voice_tiles_count_each_span_once(ch_client, eval_table, builder_cls):
    builder = builder_cls(project_id=PROJECT_ID, eval_config_ids=[PASS_FAIL, CHOICES])
    counted = _by_config(
        _run(ch_client, *builder.build_eval_query([TRACE_ID], count_mode=True))
    )
    assert counted[PASS_FAIL]["pass_count"] == 1
    assert counted[PASS_FAIL]["fail_count"] == 1
    assert counted[PASS_FAIL]["success_count"] == 2
    assert counted[PASS_FAIL]["error_count"] == 1
    assert counted[PASS_FAIL]["target_type"] == "span"
    # Count mode now returns native arrays; never json.loads() an array.
    assert counted[CHOICES]["str_lists"] == [["unclear"]]

    averaged = _by_config(_run(ch_client, *builder.build_eval_query([TRACE_ID])))
    assert averaged[PASS_FAIL]["success_count"] == 3
    assert averaged[PASS_FAIL]["pass_rate"] == pytest.approx(200 / 3)
    assert "pass_count" not in averaged[PASS_FAIL]
    assert sorted(averaged[CHOICES]["str_lists"]) == ['["clear"]', '["unclear"]']


@pytest.mark.parametrize("builder_cls", [SpanListQueryBuilder, SpanListQueryBuilderV2])
def test_span_count_tiles_read_the_newest_completed_result(
    ch_client, eval_table, builder_cls
):
    builder = builder_cls(project_id=PROJECT_ID, eval_config_ids=[PASS_FAIL, CHOICES])
    rows = {
        (row["observation_span_id"], str(row["eval_config_id"])): row
        for row in _run(
            ch_client, *builder.build_eval_query(["span-a", "span-b"], count_mode=True)
        )
    }
    assert rows[("span-a", PASS_FAIL)]["pass_count"] == 1
    assert rows[("span-a", PASS_FAIL)]["fail_count"] == 0
    assert rows[("span-b", PASS_FAIL)]["pass_count"] == 0
    assert rows[("span-b", PASS_FAIL)]["fail_count"] == 1
    assert rows[("span-b", PASS_FAIL)]["error_count"] == 1
    assert rows[("span-b", CHOICES)]["str_lists"] == [["unclear"]]


def _filter_matches(
    ch_client,
    builder_cls,
    trace,
    span,
    config,
    op,
    value=None,
    *,
    output_type="PASS_FAIL",
    count_mode=True,
):
    is_span = issubclass(builder_cls, SpanListQueryBuilder)
    compiler = builder_cls._FILTER_BUILDER_CLS(
        project_id=PROJECT_ID,
        query_mode="span" if is_span else "trace",
        eval_count_mode=count_mode,
        eval_filter_metadata={config: EvalFilterMetadata((config,), output_type)},
        score_date_scope=False,
    )
    where, params = compiler.translate(
        [
            {
                "column_id": config,
                "filter_config": {
                    "col_type": "EVAL_METRIC",
                    "filter_type": "text",
                    "filter_op": op,
                    "filter_value": value,
                },
            }
        ]
    )
    params.update(test_trace=trace, test_span=span)
    sql = f"SELECT count() FROM (SELECT %(test_trace)s AS trace_id, %(test_span)s AS id) WHERE {where}"
    return ch_client.execute(sql, params)[0][0] == 1


@pytest.mark.parametrize("builder_cls", BUILDERS)
def test_rerun_status_null_and_tombstone_filters_agree_with_tiles(
    ch_client, eval_table, builder_cls
):
    trace, config, span = str(uuid.uuid4()), str(uuid.uuid4()), "agreement"

    def add(minute, **kwargs):
        _insert(ch_client, eval_table, span, config, minute, trace=trace, **kwargs)

    add(1, ok=1)
    add(2, ok=0)
    # A later failed run must not resurrect the stale pass value. V2 has no
    # lifecycle column; all error representations it supports are tested.
    add(3, ok=1, error=1)
    add(4, ok=1, text="ERROR")
    if not eval_table.endswith("_v2"):
        for minute, state in enumerate(["pending", "running", "skipped", "errored"], 5):
            add(minute, ok=1, status=state)

    builder = builder_cls(project_id=PROJECT_ID, eval_config_ids=[config])
    ids = [span] if issubclass(builder_cls, SpanListQueryBuilder) else [trace]

    def counts():
        rows = _run(ch_client, *builder.build_eval_query(ids, count_mode=True))
        assert len(rows) == 1
        return rows[0]["pass_count"], rows[0]["fail_count"]

    def matches(op, value=None, *, count_mode=True):
        return _filter_matches(
            ch_client,
            builder_cls,
            trace,
            span,
            config,
            op,
            value,
            count_mode=count_mode,
        )

    assert counts() == (0, 1)
    assert not matches("equals", "Passed")
    assert matches("equals", "Failed")
    assert matches("is_not_null")
    assert not matches("is_null")
    # Default/task/history semantics still match an older live attempt.
    assert matches("equals", "Passed", count_mode=False)
    assert matches("equals", "Failed", count_mode=False)

    # NULL/empty status is completed. A newer null value displaces an older
    # result; testing presence before ranking would wrongly retain that result.
    null_id = str(uuid.uuid4())
    add(10, row_id=null_id, ok=1, status="running")
    add(10, row_id=null_id, ok=None, status=None, version=2)
    assert counts() == (0, 0)
    assert matches("is_null")
    assert not matches("is_not_null")
    assert not matches("equals", "Failed")
    assert matches("is_not_null", count_mode=False)

    # CDC tombstones and app soft-deletes are both resolved before rerun rank.
    add(10, row_id=null_id, ok=None, status=None, cdc=1, version=3)
    assert counts() == (0, 1)
    assert matches("equals", "Failed")
    app_id = str(uuid.uuid4())
    add(11, row_id=app_id, ok=1)
    add(11, row_id=app_id, ok=1, deleted=1, version=2)
    assert counts() == (0, 1)
    assert not matches("equals", "Passed")
    # Clearing a nullable deleted marker must not preserve the older 1 via
    # argMax's default NULL skipping, especially in the voice aggregate path.
    add(11, row_id=app_id, ok=1, deleted=None, status="", version=3)
    assert counts() == (1, 0)
    assert matches("equals", "Passed")
    assert not matches("equals", "Failed")


@pytest.mark.parametrize("builder_cls", BUILDERS)
@pytest.mark.parametrize(
    "text,score,expected",
    [
        ("helpful", None, [["helpful"]]),
        ("0.75", None, [[]]),
        ("0.75", 0.75, [[]]),
        ("NaN", None, [[]]),
    ],
)
def test_scalar_choices_are_count_only_and_numeric_strings_stay_scores(
    ch_client, eval_table, builder_cls, text, score, expected
):
    trace, config, span = str(uuid.uuid4()), str(uuid.uuid4()), "scalar"
    _insert(ch_client, eval_table, span, config, 1, trace=trace, text=text, score=score)
    builder = builder_cls(project_id=PROJECT_ID, eval_config_ids=[config])
    ids = [span] if issubclass(builder_cls, SpanListQueryBuilder) else [trace]
    counted = _run(ch_client, *builder.build_eval_query(ids, count_mode=True))[0]
    averaged = _run(ch_client, *builder.build_eval_query(ids))[0]
    assert counted["str_lists"] == expected
    assert averaged["str_lists"] == ["[]"]
    assert averaged["avg_score"] == score
    assert "pass_count" not in averaged


@pytest.mark.parametrize("builder_cls", BUILDERS)
def test_float_only_verdict_filters_match_count_tile_fallback(
    ch_client, eval_table, builder_cls
):
    trace, config, span = str(uuid.uuid4()), str(uuid.uuid4()), "float-verdict"
    _insert(ch_client, eval_table, span, config, 1, trace=trace, score=1.0)
    _insert(ch_client, eval_table, span, config, 2, trace=trace, score=0.0)
    builder = builder_cls(project_id=PROJECT_ID, eval_config_ids=[config])
    ids = [span] if issubclass(builder_cls, SpanListQueryBuilder) else [trace]
    counted = _run(ch_client, *builder.build_eval_query(ids, count_mode=True))[0]
    assert (counted["pass_count"], counted["fail_count"]) == (0, 1)
    for op, value, expected in [
        ("equals", "Passed", False),
        ("equals", "Failed", True),
        ("is_not_null", None, True),
        ("is_null", None, False),
    ]:
        assert (
            _filter_matches(ch_client, builder_cls, trace, span, config, op, value)
            is expected
        )
    assert not _filter_matches(
        ch_client, builder_cls, trace, span, config, "is_not_null", count_mode=False
    )
