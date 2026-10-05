"""Eval value filters count the same results the Observe roll-up tile counts.

The trace list renders a Pass/Fail or Choices eval as a roll-up tile: how many
completed results passed, failed, or picked each label. A row is a completed
result only when it is not errored, not an ``ERROR`` output and not a work
item that is still pending, running, skipped or errored. A requeued row keeps
the verdict of its previous run while it waits, so the error flag alone is not
enough.

The filter compiler must apply that same rule after version collapse, or
``Failed`` returns traces whose tile shows no failure (TH-8106).

Everything here is query-string building: no database, no ClickHouse. The
live agreement proof is ``test_eval_filter_rollup_agreement_ch25.py``.
"""

from __future__ import annotations

import uuid

import pytest

from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    EvalFilterMetadata,
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

CONFIG_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())

COMPLETED = (
    "error = 0 AND ifNull(output_str, '') != 'ERROR' AND "
    "ifNull(status, '') NOT IN ('pending', 'running', 'skipped', 'errored')"
)

# (output type, operator, value) for every value and presence shape the
# filter panel can send for an eval column.
SHAPES = [
    ("PASS_FAIL", "equals", ["Passed"]),
    ("PASS_FAIL", "equals", ["Failed"]),
    ("PASS_FAIL", "in", ["Passed", "Failed"]),
    ("PASS_FAIL", "not_equals", ["Failed"]),
    ("PASS_FAIL", "is_not_null", None),
    ("PASS_FAIL", "is_null", None),
    ("CHOICES", "equals", ["clear"]),
    ("CHOICES", "not_equals", ["clear"]),
    ("CHOICES", "contains", ["cle"]),
    ("CHOICES", "is_not_null", None),
    ("SCORE", "greater_than", 50),
    ("SCORE", "between", [20, 80]),
    ("SCORE", "in", [50]),
    ("SCORE", "is_not_null", None),
]


def _where(builder_cls, output_type, op, value, *, query_mode):
    config = {"col_type": ClickHouseFilterBuilder.EVAL_METRIC, "filter_op": op}
    if value is not None:
        config["filter_value"] = value
    builder = builder_cls(
        project_id=PROJECT_ID,
        query_mode=query_mode,
        eval_filter_metadata={CONFIG_ID: EvalFilterMetadata((CONFIG_ID,), output_type)},
    )
    where, _ = builder.translate([{"column_id": CONFIG_ID, "filter_config": config}])
    return where


@pytest.mark.unit
def test_completed_result_predicate_is_the_tile_rule():
    from tracer.services.clickhouse.eval_expressions import (
        EVAL_NON_RESULT_STATUSES,
        eval_completed_result_predicate,
    )

    assert EVAL_NON_RESULT_STATUSES == ("pending", "running", "skipped", "errored")
    assert eval_completed_result_predicate() == COMPLETED


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder_cls", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
)
@pytest.mark.parametrize(
    "query_mode",
    [ClickHouseFilterBuilder.QUERY_MODE_TRACE, ClickHouseFilterBuilder.QUERY_MODE_SPAN],
)
@pytest.mark.parametrize(("output_type", "op", "value"), SHAPES)
def test_legacy_table_filters_only_completed_results(
    settings, builder_cls, query_mode, output_type, op, value
):
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    where = _where(builder_cls, output_type, op, value, query_mode=query_mode)

    # The real lifecycle column is carried through the version collapse ...
    assert "eval_scan.status" in where
    # ... and tested after it, beside the live and error predicates. Testing
    # it inside the collapse would resurrect an older completed version.
    collapse = where.index("LIMIT 1 BY eval_scan.id")
    latest = where.index(") AS latest_eval", collapse)
    assert where.index(COMPLETED) > latest


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder_cls", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
)
@pytest.mark.parametrize(("output_type", "op", "value"), SHAPES)
def test_v2_table_has_no_status_column_and_stays_terminal(
    settings, builder_cls, output_type, op, value
):
    # The direct-write v2 table has no lifecycle column; its rows are written
    # when a result exists. The count tile projects 'completed' for it, and the
    # filter must do the same rather than reference a column that is absent.
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    where = _where(
        builder_cls,
        output_type,
        op,
        value,
        query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE,
    )

    assert "eval_scan.status" not in where
    assert "'completed' AS status" in where
    assert COMPLETED in where


@pytest.mark.unit
@pytest.mark.parametrize("table", ["tracer_eval_logger", "tracer_eval_logger_v2"])
@pytest.mark.parametrize("tile", ["trace_list", "span_list"])
def test_tile_counts_and_filter_use_one_predicate(settings, table, tile):
    # Both Observe tiles (trace list and span list) must count with the same
    # rule the filter applies, in both query modes.
    settings.CH25_EVAL_LOGGER_TABLE = table
    if tile == "trace_list":
        count_sql, _ = TraceListQueryBuilderV2(
            project_id=PROJECT_ID, eval_config_ids=[CONFIG_ID]
        ).build_eval_query(["trace-1"], count_mode=True)
        query_mode = ClickHouseFilterBuilder.QUERY_MODE_TRACE
    else:
        count_sql, _ = SpanListQueryBuilderV2(
            project_id=PROJECT_ID, eval_config_ids=[CONFIG_ID]
        ).build_eval_query(
            ["span-1"], span_entities=[("trace-1", "span-1")], count_mode=True
        )
        query_mode = ClickHouseFilterBuilder.QUERY_MODE_SPAN
    collapsed = " ".join(count_sql.split())

    for verdict in (1, 0):
        assert f"countIf( output_bool = {verdict} AND {COMPLETED} )" in collapsed, (
            collapsed
        )

    for op, value in (("equals", ["Passed"]), ("equals", ["Failed"])):
        where = _where(
            ClickHouseFilterBuilderV2,
            "PASS_FAIL",
            op,
            value,
            query_mode=query_mode,
        )
        assert COMPLETED in where
