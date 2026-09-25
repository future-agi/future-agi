"""The users graph's latest-row snapshot carries every MATERIALIZED column a
filter can reference.

``SELECT *`` omits MATERIALIZED columns, so a snapshot read as ``SELECT *
FROM spans FINAL`` loses ``trace_name`` and every compiled predicate on it
fails with ClickHouse code 47. The snapshot names those columns after the star
(``SPANS_FILTERABLE_MATERIALIZED_COLUMNS``). These tests keep that list in step
with the DDL and the filter compilers, and render each graph statement that
reads the snapshot for a ``trace_name`` leaf.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tracer.services.clickhouse import exact_graph_reads
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.user_list import (
    USER_NATIVE_SPAN_DIMENSIONS,
)
from tracer.services.clickhouse.v2.query_builders import user_time_series
from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    SPANS_FILTERABLE_MATERIALIZED_COLUMNS,
    UserTimeSeriesQueryBuilderV2,
    latest_physical_span_rows_sql,
)

pytestmark = pytest.mark.unit

PROJECT = str(uuid.UUID(int=331))
START = datetime(2026, 8, 1, tzinfo=UTC)
END = START + timedelta(days=1)
SCHEMA_DIR = Path(user_time_series.__file__).resolve().parent.parent / "schema"
# Every MATERIALIZED spans column the deployed DDL declares (checked against
# a real-schema database: ``system.columns`` with ``default_kind``).
DEPLOYED_MATERIALIZED = {
    "embedding_model",
    "input_length",
    "llm_finish_reason",
    "llm_request_model",
    "llm_response_model",
    "max_tokens",
    "output_length",
    "streaming",
    "temperature",
    "top_p",
    "trace_name",
}
_SPANS_STATEMENT = re.compile(
    r"^\s*(?:CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?|ALTER\s+TABLE\s+)"
    r"(?:\w+\.)?spans\b",
    re.IGNORECASE,
)
# ``<name> <type> MATERIALIZED``: the type is a word, optionally wrapped in
# LowCardinality(...)/Nullable(...) and carrying (...) arguments.
_MATERIALIZED_COLUMN = re.compile(
    r"\b([a-z_][a-z0-9_]*)\s+(?:LowCardinality\(|Nullable\()*\w+(?:\([^)]*\))?\)*"
    r"\s+MATERIALIZED\b",
    re.IGNORECASE,
)


def _ddl_materialized_spans_columns() -> set[str]:
    found: set[str] = set()
    for path in sorted(SCHEMA_DIR.glob("*.sql")):
        text = re.sub(r"--[^\n]*", "", path.read_text())
        for statement in text.split(";"):
            if not _SPANS_STATEMENT.match(statement):
                continue
            found.update(
                name.lower() for name in _MATERIALIZED_COLUMN.findall(statement)
            )
    return found


def _bootstrap_materialized_spans_columns() -> set[str]:
    from tracer.services.clickhouse import oss_cdc_bootstrap

    return {
        name
        for name, declaration in oss_cdc_bootstrap._SPAN_COLUMNS.items()
        if " MATERIALIZED " in f" {declaration} "
    }


def test_the_ddl_parser_finds_every_deployed_materialized_column():
    # A known positive first: a parser that finds nothing would pass the
    # compiler check below vacuously.
    assert _ddl_materialized_spans_columns() == DEPLOYED_MATERIALIZED
    assert _bootstrap_materialized_spans_columns() <= DEPLOYED_MATERIALIZED
    assert "trace_name" in _bootstrap_materialized_spans_columns()


def _leaf(column_id: str, operation: str, col_type: str | None):
    config = {"filter_type": "text", "filter_op": operation}
    if operation not in {"is_null", "is_not_null"}:
        config["filter_value"] = "x"
    if col_type is not None:
        config["col_type"] = col_type
    return {
        "column_id": column_id,
        "property_id": f"system_attribute:traces:{column_id}",
        "filter_config": config,
    }


def _compiled_sql() -> dict[str, str]:
    """The graph's compiled membership SQL for every system-column leaf."""

    columns = sorted(
        {*ClickHouseFilterBuilder.SYSTEM_METRIC_MAP, *USER_NATIVE_SPAN_DIMENSIONS}
    )
    compiled: dict[str, str] = {}
    for column_id in columns:
        for col_type in ("SYSTEM_METRIC", None):
            for operation in ("equals", "not_equals", "is_null"):
                try:
                    flags, condition, _params = (
                        exact_graph_reads.compile_user_membership_leaf(
                            _leaf(column_id, operation, col_type),
                            project_id=PROJECT,
                            namespace="probe",
                        )
                    )
                except (TypeError, ValueError):
                    continue
                compiled[f"{column_id}/{col_type}/{operation}"] = " ".join(
                    (*flags, condition)
                )
    return compiled


def test_every_materialized_column_a_filter_can_reference_is_in_the_snapshot():
    compiled = _compiled_sql()
    assert len(compiled) > 50
    referenced = {
        column
        for sql in compiled.values()
        for column in DEPLOYED_MATERIALIZED
        if re.search(rf"\b{column}\b", sql)
    }
    # A known positive: trace_name is emitted by the compilers.
    assert "trace_name" in referenced
    assert referenced <= set(SPANS_FILTERABLE_MATERIALIZED_COLUMNS), referenced - set(
        SPANS_FILTERABLE_MATERIALIZED_COLUMNS
    )


def _final_reads(query: str) -> list[str]:
    """What each ``FROM spans FINAL`` read selects."""

    return re.findall(
        r"SELECT\s+((?:(?!SELECT).)*?)\s+FROM\s+spans\s+FINAL", query, re.DOTALL
    )


def _date_filter():
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _trace_name_leaf():
    return _leaf("trace_name", "equals", "SYSTEM_METRIC")


def _names_trace_name(query: str) -> None:
    reads = _final_reads(query)
    assert reads, "no FINAL snapshot read"
    for selected in reads:
        assert [part.strip() for part in selected.split(",")] == [
            "*",
            "trace_name",
        ], selected


def test_the_standalone_membership_snapshot_names_trace_name():
    query, _params, _needs_eval = exact_graph_reads._user_id_membership_sql(
        project_id=PROJECT,
        filters=[_date_filter(), _trace_name_leaf()],
        start_date=START,
        end_date=END,
        all_snapshot_users=True,
    )
    assert "trace_name" in query
    _names_trace_name(query)


def test_the_graph_snapshot_a_reused_membership_reads_names_trace_name():
    membership, params, _needs_eval = exact_graph_reads._user_id_membership_sql(
        project_id=PROJECT,
        filters=[_date_filter(), _trace_name_leaf()],
        start_date=START,
        end_date=END,
        all_snapshot_users=True,
        reuse_outer_snapshot=True,
    )
    builder = UserTimeSeriesQueryBuilderV2(
        project_id=PROJECT,
        filters=[_date_filter()],
        interval="day",
        user_membership_sql=membership,
        user_membership_params=params,
        exact_snapshot_start=START,
        exact_snapshot_end=END,
    )
    query, _params = builder.build()
    assert "SELECT * FROM latest_spans" in query
    _names_trace_name(query)


def test_the_partitioned_trace_membership_snapshot_names_trace_name():
    query, _params, _needs_eval = exact_graph_reads._user_trace_membership_sql(
        project_id=PROJECT,
        filters=[_date_filter(), _trace_name_leaf()],
        start_date=START,
        end_date=END,
        candidate_trace_ids_param="candidate_trace_ids",
    )
    assert len(_final_reads(query)) >= 2
    _names_trace_name(query)


def test_a_snapshot_of_another_table_names_nothing_unless_asked():
    sql = latest_physical_span_rows_sql(
        table="spans_copy",
        project_predicate="1",
        start_hour="a",
        end_hour="b",
        mutable_columns=("start_time",),
    )
    assert "SELECT * FROM spans_copy FINAL" in sql
    sql = latest_physical_span_rows_sql(
        table="spans_copy",
        project_predicate="1",
        start_hour="a",
        end_hour="b",
        mutable_columns=("start_time",),
        materialized_columns=("trace_name",),
    )
    assert "SELECT *, trace_name FROM spans_copy FINAL" in sql
