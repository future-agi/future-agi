"""Observe's newest-completed filters are an explicit, propagated opt-in."""

import ast
from pathlib import Path

import pytest

from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    EvalFilterMetadata,
)
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallListQueryBuilder,
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
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = "00000000-0000-4000-8000-000000000001"
TRACE = "00000000-0000-4000-8000-000000000002"
CONFIG = "00000000-0000-4000-8000-000000000003"
BUILDERS = [
    TraceListQueryBuilder,
    TraceListQueryBuilderV2,
    SpanListQueryBuilder,
    SpanListQueryBuilderV2,
    VoiceCallListQueryBuilder,
    VoiceCallListQueryBuilderV2,
]
TIME_FILTER = {
    "column_id": "start_time",
    "filter_config": {
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"],
    },
}


def _eval_filter(op="equals", value="Passed"):
    return {
        "column_id": CONFIG,
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }


@pytest.mark.parametrize("builder_cls", BUILDERS)
def test_list_filter_default_is_all_attempts_and_observe_is_opt_in(builder_cls):
    kwargs = {
        "project_id": PROJECT,
        "filters": [TIME_FILTER, _eval_filter()],
        "eval_config_ids": [CONFIG],
        "eval_filter_metadata": {CONFIG: EvalFilterMetadata((CONFIG,), "PASS_FAIL")},
    }
    default = builder_cls(**kwargs)
    explicit_default = builder_cls(**kwargs, eval_count_mode=False)
    observe = builder_cls(**kwargs, eval_count_mode=True)
    ids = ["span-1"] if isinstance(default, SpanListQueryBuilder) else [TRACE]
    default_sql, default_params = default.build_filter_match_query(ids)
    false_sql, false_params = explicit_default.build_filter_match_query(ids)
    assert default_sql == false_sql
    assert default_params == false_params
    assert "completed_rank" not in default_sql
    assert "physical_eval" not in default_sql
    assert "eval_scan.status" not in default_sql
    assert "LIMIT 1 BY eval_scan.id" in default_sql
    assert "output_bool IN" in default_sql
    sql, _ = observe.build_filter_match_query(ids)
    assert "latest_eval.completed_rank = 1" in sql
    assert (
        "PARTITION BY physical_eval.trace_id, physical_eval.custom_eval_config_id, physical_eval.observation_span_id"
        in sql
    )
    assert sql.index("LIMIT 1 BY eval_scan.id") < sql.index("WHERE physical_eval.")
    assert sql.index("latest_eval.completed_rank = 1") < sql.index("output_bool IN")
    if isinstance(observe, VoiceCallListQueryBuilder):
        assert observe._bounded_delegate().eval_count_mode is True
        assert observe._candidate_witness_delegate().eval_count_mode is True
        assert default._bounded_delegate().eval_count_mode is False


@pytest.mark.parametrize(
    "builder_cls", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
)
@pytest.mark.parametrize(
    "output_type,op,value,presence",
    [
        ("PASS_FAIL", "equals", "Passed", "output_bool IN"),
        ("PASS_FAIL", "is_null", None, "output_bool IS NOT NULL"),
        ("SCORE", "is_not_null", None, "output_float IS NOT NULL"),
        ("CHOICE", "is_null", None, "notEmpty(JSONExtract"),
    ],
)
def test_count_compiler_ranks_after_collapse_but_before_match(
    builder_cls, output_type, op, value, presence, settings
):
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    metadata = {CONFIG: EvalFilterMetadata((CONFIG,), output_type)}
    compiler = builder_cls(
        project_id=PROJECT,
        eval_filter_metadata=metadata,
        eval_count_mode=True,
        candidate_ids_param="candidates",
    )
    sql, _ = compiler.translate([_eval_filter(op, value)])
    physical = sql.index("LIMIT 1 BY eval_scan.id")
    live = sql.index("WHERE physical_eval._peerdb_is_deleted = 0")
    completed = sql.index("AND (physical_eval.error = 0")
    selected = sql.index("latest_eval.completed_rank = 1")
    assert physical < live < completed < selected < sql.index(presence)
    assert (
        "ifNull(physical_eval.status, '') NOT IN ('pending', 'running', 'skipped', 'errored')"
        in sql
    )
    assert "ifNull(physical_eval.output_str, '') != 'ERROR'" in sql
    assert "toString(physical_eval.id) DESC" in sql
    assert sql.index("%(candidates)s") < physical
    default_sql, _ = builder_cls(
        project_id=PROJECT, eval_filter_metadata=metadata
    ).translate([_eval_filter(op, value)])
    assert "completed_rank" not in default_sql
    assert "physical_eval" not in default_sql
    assert "status" not in default_sql


@pytest.mark.parametrize("builder_cls", BUILDERS)
def test_percentage_projection_is_exactly_raw_json_and_count_fallback_is_guarded(
    builder_cls,
):
    builder = builder_cls(project_id=PROJECT, eval_config_ids=[CONFIG])
    prefix = "latest_" if isinstance(builder, VoiceCallListQueryBuilder) else ""
    default_sql, default_params = builder.build_eval_query([TRACE])
    false_sql, false_params = builder.build_eval_query([TRACE], count_mode=False)
    assert default_sql == false_sql
    assert default_params == false_params
    normalized = " ".join(default_sql.split())
    expected = (
        f"groupArrayIf( {prefix}output_str_list, {prefix}error = 0 "
        f"AND ifNull({prefix}output_str, '') != 'ERROR' "
        f"AND ifNull({prefix}status, '') NOT IN ('pending', 'running', 'skipped', 'errored') ) AS str_lists"
    )
    assert expected in normalized
    assert "completed_rank" not in default_sql
    assert "JSONExtract(" not in default_sql
    assert "AS pass_count" not in default_sql
    counted, _ = builder.build_eval_query([TRACE], count_mode=True)
    assert "completed_rank = 1" in counted
    assert f"JSONExtract({prefix}output_str_list, 'Array(String)')" in counted
    assert f"isNull(toFloat64OrNull({prefix}output_str))" in counted
    assert f"isNull({prefix}output_bool) AND isNull({prefix}output_float)" in counted


def test_observe_view_constructors_opt_in_without_changing_task_views():
    views = Path(__file__).resolve().parents[1] / "views"
    expected = {"trace.py": 4, "observation_span.py": 2}
    for filename, count in expected.items():
        calls = [
            node
            for node in ast.walk(ast.parse((views / filename).read_text()))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id
            in {
                "TraceListQueryBuilderV2",
                "ObserveTraceListQueryBuilderV2",
                "SpanListQueryBuilderV2",
                "VoiceCallListQueryBuilderV2",
            }
        ]
        opted = [
            node
            for node in calls
            if any(kw.arg == "eval_count_mode" for kw in node.keywords)
        ]
        assert len(opted) == count
        for node in calls:
            keywords = {kw.arg: kw.value for kw in node.keywords}
            if "project_version_id" not in keywords:
                assert ast.literal_eval(keywords["eval_count_mode"]) is True
            elif "eval_count_mode" in keywords:
                assert (
                    ast.unparse(keywords["eval_count_mode"])
                    == "project_version_id is None"
                )
            else:
                # Project-version/task lists remain on their percentage semantics.
                assert "project_version_id" in keywords


@pytest.mark.parametrize("output_type", ["PASS_FAIL", "SCORE", "CHOICES"])
def test_float_verdict_fallback_is_limited_to_count_mode_pass_fail(output_type):
    metadata = {CONFIG: EvalFilterMetadata((CONFIG,), output_type)}
    for count_mode in (False, True):
        compiler = ClickHouseFilterBuilder(
            project_id=PROJECT,
            eval_filter_metadata=metadata,
            eval_count_mode=count_mode,
        )
        sql, _ = compiler.translate([_eval_filter("is_not_null", None)])
        assert ("toUInt8(eval_scan.output_float >= 0.5)" in sql) is (
            count_mode and output_type == "PASS_FAIL"
        )
