"""Current-dimension coverage for exact dashboard query builders."""

from __future__ import annotations

import re

import pytest

from tracer.services.clickhouse.query_builders.dataset_dashboard import (
    DatasetQueryBuilder,
)
from tracer.services.clickhouse.query_builders.simulation_dashboard import (
    SimulationQueryBuilder,
)


@pytest.mark.unit
def test_dataset_exact_builder_exposes_source_and_scope_relations():
    metric = {
        "id": "column_name",
        "name": "column_name",
        "type": "system_metric",
        "aggregation": "count_distinct",
    }
    builder = DatasetQueryBuilder(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "granularity": "day",
            "time_range": {"preset": "12M"},
            "metrics": [metric],
            "filters": [],
            "breakdowns": [],
            "exact_snapshot_dimensions": True,
        }
    )

    sql, _params = builder.build_metric_query(metric)

    assert "dictGet" not in sql
    for table in ("model_hub_cell", "model_hub_column", "model_hub_dataset"):
        assert re.search(rf"\b{table}\b(?:\s+AS\s+\w+)?\s+FINAL\b", sql)


@pytest.mark.unit
def test_simulation_exact_builder_exposes_source_and_dimension_relations():
    metric = {
        "id": "duration",
        "name": "duration",
        "type": "system_metric",
        "aggregation": "avg",
    }
    builder = SimulationQueryBuilder(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "granularity": "day",
            "time_range": {"preset": "12M"},
            "metrics": [metric],
            "filters": [],
            "breakdowns": [],
            "exact_snapshot_dimensions": True,
        }
    )

    sql, _params = builder.build_metric_query(metric)

    assert "dictGet" not in sql
    for table in (
        "simulate_agent_definition",
        "simulate_agent_version",
        "simulate_call_execution",
        "simulate_run_test",
        "simulate_scenarios",
        "simulate_test_execution",
    ):
        assert re.search(rf"\b{table}\b(?:\s+AS\s+\w+)?\s+FINAL\b", sql)
