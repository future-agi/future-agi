"""Regression coverage for raw eval-metric graph logger reads.

The eval logger intentionally has no ``project_id`` column in either the
legacy PeerDB or direct-write topology. Tenant authorization happens against
``CustomEvalConfig`` before graph dispatch; the ClickHouse read must then stay
bound to that globally unique config (and to project-owned trace candidates
when row filters are present).
"""

from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.services.clickhouse.query_builders.eval_metrics import (
    CHOICES,
    PASS_FAIL,
    SCORE,
    EvalMetricsQueryBuilder,
    normalize_eval_output_type,
)
from tracer.services.clickhouse.v2.query_builders.eval_metrics import (
    EvalMetricsQueryBuilderV2,
)
from tracer.utils.graphs_optimized import get_eval_graph_data

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
EVAL_CONFIG_ID = "22222222-2222-4222-8222-222222222222"
START = datetime(2026, 7, 20)
END = datetime(2026, 8, 3)


@pytest.mark.unit
def test_eval_graph_common_boundary_scopes_config_to_request_project():
    """Reject a foreign config before a project-less raw logger read can run."""

    with mock.patch.object(
        CustomEvalConfig.objects, "select_related"
    ) as select_related:
        scoped_configs = select_related.return_value
        scoped_configs.get.side_effect = CustomEvalConfig.DoesNotExist

        with pytest.raises(
            ValueError, match="Evaluation config is not available for this project"
        ):
            get_eval_graph_data(
                interval="day",
                filters=[],
                property="average",
                observe_type="charts",
                req_data_config={"id": EVAL_CONFIG_ID, "type": "EVAL"},
                eval_logger_filters={"project_id": PROJECT_ID},
            )

    select_related.assert_called_once_with("eval_template")
    scoped_configs.get.assert_called_once_with(
        id=EVAL_CONFIG_ID,
        project_id=PROJECT_ID,
        deleted=False,
    )


@pytest.mark.unit
def test_eval_graph_common_boundary_rejects_missing_project_before_any_read():
    with mock.patch.object(
        CustomEvalConfig.objects, "select_related"
    ) as select_related:
        with pytest.raises(
            ValueError, match="Evaluation config is not available for this project"
        ):
            get_eval_graph_data(
                interval="day",
                filters=[],
                property="average",
                observe_type="trace",
                req_data_config={"id": EVAL_CONFIG_ID, "type": "EVAL"},
                eval_logger_filters={},
            )

    select_related.assert_not_called()


def _raw_builder(
    output_type: str,
    *,
    filters: list[dict] | None = None,
) -> EvalMetricsQueryBuilderV2:
    return EvalMetricsQueryBuilderV2(
        custom_eval_config_id=EVAL_CONFIG_ID,
        project_id=PROJECT_ID,
        start_date=START,
        end_date=END,
        interval="day",
        eval_output_type=output_type,
        choices=["accepted", "rejected"],
        use_preaggregated=False,
        filters=filters or [],
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("output_type", "canonical_type", "metric_expression"),
    [
        ("SCORE", SCORE, "avg(output_float)"),
        ("score", SCORE, "avg(output_float)"),
        ("PASS_FAIL", PASS_FAIL, "output_bool = 1"),
        ("Pass/Fail", PASS_FAIL, "output_bool = 1"),
        ("pass-fail", PASS_FAIL, "output_bool = 1"),
        ("CHOICES", CHOICES, "JSONExtract(output_str_list, 'Array(String)')"),
        ("choices", CHOICES, "JSONExtract(output_str_list, 'Array(String)')"),
    ],
)
@pytest.mark.parametrize(
    ("logger_table", "live_predicate", "foreign_live_columns"),
    [
        (
            "tracer_eval_logger",
            "raw_eval_logger._peerdb_is_deleted = 0 AND "
            "(raw_eval_logger.deleted = 0 OR raw_eval_logger.deleted IS NULL)",
            ("raw_eval_logger.is_deleted = 0",),
        ),
        (
            "tracer_eval_logger_v2",
            "raw_eval_logger.is_deleted = 0",
            (
                "raw_eval_logger._peerdb_is_deleted",
                "raw_eval_logger.deleted",
            ),
        ),
    ],
)
def test_v2_raw_terminal_graph_uses_configured_authoritative_logger(
    settings,
    output_type,
    canonical_type,
    metric_expression,
    logger_table,
    live_predicate,
    foreign_live_columns,
):
    """CH25 graphs preserve the selected eval logger's physical schema."""

    settings.CH25_EVAL_LOGGER_TABLE = logger_table

    query, params = _raw_builder(output_type).build()
    normalized = " ".join(query.split())

    assert f"FROM {logger_table} AS raw_eval_logger FINAL" in normalized
    assert live_predicate in normalized
    for foreign_live_column in foreign_live_columns:
        assert foreign_live_column not in normalized
    assert metric_expression in normalized
    assert normalize_eval_output_type(output_type) == canonical_type
    # Neither physical logger has project_id. The config UUID is the authorized
    # tenant anchor for this unfiltered raw query.
    assert "project_id" not in normalized
    assert "custom_eval_config_id = toUUID(%(eval_config_id)s)" in normalized
    assert "created_at >= %(start_date)s" in normalized
    assert "created_at < %(end_date)s" in normalized
    assert params["eval_config_id"] == EVAL_CONFIG_ID
    assert params["start_date"] == START
    assert params["end_date"] == END
    assert EVAL_CONFIG_ID not in query


@pytest.mark.unit
def test_v2_eval_graph_uses_raw_authoritative_table_even_when_rollup_requested(
    settings,
):
    """The insertion-only eval rollup is not authoritative for this path."""

    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    builder = EvalMetricsQueryBuilderV2(
        custom_eval_config_id=EVAL_CONFIG_ID,
        project_id=PROJECT_ID,
        start_date=START,
        end_date=END,
        interval="day",
        eval_output_type="SCORE",
        use_preaggregated=True,
    )

    query, _ = builder.build()

    assert "FROM tracer_eval_logger AS raw_eval_logger FINAL" in query
    assert "raw_eval_logger._peerdb_is_deleted = 0" in query
    assert "eval_metrics_hourly" not in query


@pytest.mark.unit
def test_v2_eval_graph_defaults_to_legacy_authoritative_table(settings):
    del settings.CH25_EVAL_LOGGER_TABLE

    query, _ = _raw_builder("SCORE").build()

    assert "FROM tracer_eval_logger AS raw_eval_logger FINAL" in query
    assert "raw_eval_logger._peerdb_is_deleted = 0" in query
    assert "raw_eval_logger.is_deleted = 0" not in query


@pytest.mark.unit
def test_v1_legacy_raw_eval_graph_includes_cdc_tombstone_guard(settings):
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    builder = EvalMetricsQueryBuilder(
        custom_eval_config_id=EVAL_CONFIG_ID,
        project_id=PROJECT_ID,
        start_date=START,
        end_date=END,
        eval_output_type="score",
        use_preaggregated=False,
    )

    query, _ = builder.build()

    assert "raw_eval_logger._peerdb_is_deleted = 0" in query
    assert "raw_eval_logger.is_deleted = 0" not in query


@pytest.mark.unit
def test_filtered_raw_graph_freezes_membership_window_across_partitions(settings):
    """Outer eval buckets must not shrink trace membership semantics."""

    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    filters = [
        {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "OK",
            },
        }
    ]

    query, params = _raw_builder("SCORE", filters=filters).build()

    assert "SELECT DISTINCT trace_id FROM spans FINAL" in query
    assert "start_time >= %(snapshot_start_date)s" in query
    assert "start_time < %(snapshot_end_date)s" in query
    assert params["snapshot_start_date"] == START
    assert params["snapshot_end_date"] == END
    # The exact worker overwrites only these outer eval bounds per output
    # partition; the membership pair remains frozen to the request window.
    partition_params = {
        **params,
        "start_date": datetime(2026, 7, 25),
        "end_date": datetime(2026, 7, 26),
    }
    assert partition_params["snapshot_start_date"] == START
    assert partition_params["snapshot_end_date"] == END


@pytest.mark.unit
def test_preaggregated_eval_graph_remains_directly_project_scoped(settings):
    """Only raw logger reads omit project_id; the rollup owns that column."""

    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    builder = EvalMetricsQueryBuilder(
        custom_eval_config_id=EVAL_CONFIG_ID,
        project_id=PROJECT_ID,
        start_date=START,
        end_date=END,
        interval="day",
        eval_output_type="SCORE",
        use_preaggregated=True,
    )

    query, params = builder.build()
    normalized = " ".join(query.split())

    assert "FROM eval_metrics_hourly" in normalized
    assert "WHERE project_id = %(project_id)s" in normalized
    assert "tracer_eval_logger" not in normalized
    assert params["project_id"] == PROJECT_ID
