"""
Tests for Simulation Dashboard Query Builder and ClickHouse schema.

Tests cover:
- SimulationQueryBuilder system metric queries
- SimulationQueryBuilder eval metric queries
- Breakdown support (scenario, agent_definition, agent_version, call_type, status)
- Filter support
- Result formatting
- Schema DDL includes simulation tables
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from tracer.services.clickhouse.query_builders.simulation_dashboard import (
    SIMULATION_AGGREGATIONS,
    SIMULATION_BREAKDOWN_COLUMNS,
    SIMULATION_FILTER_COLUMNS,
    SIMULATION_METRIC_UNITS,
    SIMULATION_SYSTEM_METRICS,
    SimulationQueryBuilder,
)
from tracer.services.clickhouse.schema import SCHEMA_DDL_STATEMENTS


class TestSimulationSystemMetrics(unittest.TestCase):
    """Test that all expected system metrics are defined."""

    def test_all_system_metrics_defined(self):
        expected = [
            "call_count",
            "success_rate",
            "failure_rate",
            "duration",
            "response_time",
            "agent_latency",
            "total_cost",
            "stt_cost",
            "llm_cost",
            "tts_cost",
            "customer_cost",
            "overall_score",
            "message_count",
            "user_interruptions",
            "user_interruption_rate",
            "ai_interruptions",
            "ai_interruption_rate",
            "user_wpm",
            "bot_wpm",
            "talk_ratio",
            "stop_time_after_interruption",
        ]
        for m in expected:
            self.assertIn(m, SIMULATION_SYSTEM_METRICS, f"Missing metric: {m}")

    def test_all_metrics_have_units(self):
        for name in SIMULATION_SYSTEM_METRICS:
            self.assertIn(name, SIMULATION_METRIC_UNITS, f"Missing unit for: {name}")

    def test_aggregations_defined(self):
        expected_aggs = [
            "avg",
            "median",
            "max",
            "min",
            "count",
            "sum",
            "p50",
            "p90",
            "p95",
            "p99",
        ]
        for agg in expected_aggs:
            self.assertIn(agg, SIMULATION_AGGREGATIONS)


class TestSimulationBreakdowns(unittest.TestCase):
    """Test breakdown dimension definitions."""

    def test_breakdown_columns_defined(self):
        expected = [
            "scenario",
            "agent_definition",
            "agent_version",
            "persona",
            "call_type",
            "status",
        ]
        for bd in expected:
            self.assertIn(bd, SIMULATION_BREAKDOWN_COLUMNS, f"Missing breakdown: {bd}")

    def test_filter_columns_defined(self):
        expected = [
            "scenario",
            "agent_definition",
            "persona",
            "call_type",
            "status",
            "scenario_type",
        ]
        for fc in expected:
            self.assertIn(fc, SIMULATION_FILTER_COLUMNS, f"Missing filter: {fc}")


class TestSimulationQueryBuilderSystemMetric(unittest.TestCase):
    """Test SimulationQueryBuilder system metric query generation."""

    def _make_config(self, metric_name="duration", aggregation="avg", **kwargs):
        config = {
            "metrics": [
                {
                    "name": metric_name,
                    "type": "system_metric",
                    "aggregation": aggregation,
                }
            ],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "workspace_id": "ws-123",
            **kwargs,
        }
        return config

    def test_basic_system_metric_query(self):
        builder = SimulationQueryBuilder(self._make_config())
        queries = builder.build_all_queries()
        self.assertEqual(len(queries), 1)
        sql, params, info = queries[0]
        self.assertIn("simulate_call_execution", sql)
        self.assertIn("duration_seconds", sql)
        self.assertIn("avg(", sql)
        self.assertIn("time_bucket", sql)
        self.assertIn("workspace_id", params)

    def test_call_count_forces_count_aggregation(self):
        builder = SimulationQueryBuilder(self._make_config("call_count", "avg"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("count()", sql)

    def test_base_query_excludes_soft_deleted_calls(self):
        builder = SimulationQueryBuilder(self._make_config("ended_reason", "count"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("c._peerdb_is_deleted = 0", sql)
        self.assertIn("c.deleted = 0", sql)

    def test_ended_reason_unsupported_aggregation_uses_distinct_non_empty_reasons(self):
        builder = SimulationQueryBuilder(self._make_config("ended_reason", "avg"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("uniqIf(c.ended_reason", sql)
        self.assertIn("c.ended_reason IS NOT NULL AND c.ended_reason != ''", sql)
        self.assertNotIn("uniq(c.ended_reason)", sql)

    def test_ended_reason_count_counts_non_empty_reasons(self):
        builder = SimulationQueryBuilder(self._make_config("ended_reason", "count"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("countIf(c.ended_reason IS NOT NULL AND c.ended_reason != '')", sql)

    def test_ended_reason_count_distinct_ignores_empty_reasons(self):
        builder = SimulationQueryBuilder(
            self._make_config("ended_reason", "count_distinct")
        )
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("uniqIf(c.ended_reason", sql)
        self.assertIn("c.ended_reason IS NOT NULL AND c.ended_reason != ''", sql)

    def test_success_rate_metric(self):
        builder = SimulationQueryBuilder(self._make_config("success_rate", "avg"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("CASE WHEN status = 'completed'", sql)

    def test_cost_metric(self):
        builder = SimulationQueryBuilder(self._make_config("total_cost", "sum"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("sum(cost_cents)", sql)

    def test_conversation_metrics(self):
        for metric in ["user_wpm", "bot_wpm", "talk_ratio", "user_interruptions"]:
            builder = SimulationQueryBuilder(self._make_config(metric, "avg"))
            queries = builder.build_all_queries()
            sql, _, _ = queries[0]
            self.assertIn("avg(", sql)
            self.assertIn("simulate_call_execution", sql)

    def test_component_latency_metrics_use_customer_latency_json(self):
        expected_paths = {
            "stt_latency": "transcriber",
            "tts_latency": "voice",
            "llm_latency": "model",
        }
        for metric, customer_key in expected_paths.items():
            builder = SimulationQueryBuilder(self._make_config(metric, "avg"))
            queries = builder.build_all_queries()
            sql, _, _ = queries[0]
            self.assertIn("customer_latency_metrics", sql)
            self.assertIn(
                f"JSONExtractFloat(c.customer_latency_metrics, 'systemMetrics', '{customer_key}')",
                sql,
            )

    def test_run_test_and_test_execution_string_metrics_are_queryable(self):
        expected_sql = {
            "run_test": "simulate_run_test_dict",
            "test_execution": "toString(c.test_execution_id)",
        }
        for metric, sql_snippet in expected_sql.items():
            builder = SimulationQueryBuilder(self._make_config(metric, "avg"))
            queries = builder.build_all_queries()
            sql, _, _ = queries[0]
            self.assertIn("uniqIf(", sql)
            self.assertIn(sql_snippet, sql)

    def test_persona_string_metrics_are_queryable(self):
        expected_sql = {
            "persona_gender": "gender",
            "persona_language": "language",
        }
        for metric, field_snippet in expected_sql.items():
            builder = SimulationQueryBuilder(self._make_config(metric, "avg"))
            queries = builder.build_all_queries()
            sql, _, _ = queries[0]
            self.assertIn("uniqIf(", sql)
            self.assertIn(field_snippet, sql)

    def test_string_metric_reports_actual_aggregation(self):
        builder = SimulationQueryBuilder(self._make_config("ended_reason", "avg"))
        queries = builder.build_all_queries()
        _, _, info = queries[0]
        self.assertEqual(info["aggregation"], "count_distinct")

    def test_granularity_hour(self):
        config = self._make_config()
        config["granularity"] = "hour"
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("toStartOfHour", sql)

    def test_granularity_month(self):
        config = self._make_config()
        config["granularity"] = "month"
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("toStartOfMonth", sql)


class TestSimulationQueryBuilderBreakdowns(unittest.TestCase):
    """Test breakdown dimension in queries."""

    def _make_config(self, breakdown_name, metric="duration"):
        return {
            "metrics": [
                {"name": metric, "type": "system_metric", "aggregation": "avg"}
            ],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "workspace_id": "ws-123",
            "breakdowns": [{"name": breakdown_name}],
        }

    def test_scenario_breakdown(self):
        builder = SimulationQueryBuilder(self._make_config("scenario"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("breakdown_value", sql)
        self.assertIn("simulate_scenario_dict", sql)

    def test_agent_definition_breakdown(self):
        builder = SimulationQueryBuilder(self._make_config("agent_definition"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("breakdown_value", sql)
        self.assertIn("simulate_agent_dict", sql)

    def test_agent_version_breakdown(self):
        builder = SimulationQueryBuilder(self._make_config("agent_version"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("breakdown_value", sql)
        self.assertIn("version_number", sql)

    def test_call_type_breakdown(self):
        builder = SimulationQueryBuilder(self._make_config("call_type"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("simulation_call_type", sql)

    def test_persona_breakdown(self):
        builder = SimulationQueryBuilder(self._make_config("persona"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("breakdown_value", sql)
        self.assertIn("call_metadata", sql)
        self.assertIn("persona", sql)

    def test_status_breakdown(self):
        builder = SimulationQueryBuilder(self._make_config("status"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("c.status", sql)

    def test_scenario_type_breakdown(self):
        builder = SimulationQueryBuilder(self._make_config("scenario_type"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("breakdown_value", sql)
        self.assertIn("scenario_type", sql)


class TestSimulationQueryBuilderFilters(unittest.TestCase):
    """Test filter application in queries."""

    def _make_config(self, filters):
        return {
            "metrics": [
                {"name": "duration", "type": "system_metric", "aggregation": "avg"}
            ],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "workspace_id": "ws-123",
            "filters": filters,
        }

    def test_status_filter(self):
        config = self._make_config(
            [
                {
                    "metric_type": "system_metric",
                    "metric_name": "status",
                    "operator": "equal_to",
                    "value": "completed",
                }
            ]
        )
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        self.assertIn("c.status", sql)
        self.assertIn("sf_0_val", params)

    def test_call_type_filter(self):
        config = self._make_config(
            [
                {
                    "metric_type": "system_metric",
                    "metric_name": "call_type",
                    "operator": "equal_to",
                    "value": "voice",
                }
            ]
        )
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        self.assertIn("simulation_call_type", sql)

    def test_persona_filter(self):
        config = self._make_config(
            [
                {
                    "metric_type": "system_metric",
                    "metric_name": "persona",
                    "operator": "equal_to",
                    "value": "Angry Customer",
                }
            ]
        )
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        self.assertIn("call_metadata", sql)
        self.assertIn("persona", sql)
        self.assertEqual(params["sf_0_val"], "Angry Customer")

    def test_non_system_filter_skipped(self):
        config = self._make_config(
            [
                {
                    "metric_type": "custom",
                    "metric_name": "foo",
                    "operator": "equal_to",
                    "value": "bar",
                }
            ]
        )
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        self.assertNotIn("foo", sql)

    def test_empty_value_filter_skipped(self):
        config = self._make_config(
            [
                {
                    "metric_type": "system_metric",
                    "metric_name": "status",
                    "operator": "equal_to",
                    "value": "",
                }
            ]
        )
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        self.assertNotIn("sf_0_val", params)


class TestSimulationQueryBuilderEvalMetric(unittest.TestCase):
    """Test eval metric queries from eval_outputs JSONB."""

    def _make_config(
        self, config_id="eval-123", output_type="SCORE", aggregation="avg"
    ):
        return {
            "metrics": [
                {
                    "name": config_id,
                    "type": "eval_metric",
                    "config_id": config_id,
                    "output_type": output_type,
                    "aggregation": aggregation,
                }
            ],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "workspace_id": "ws-123",
        }

    def test_score_eval_metric(self):
        builder = SimulationQueryBuilder(self._make_config())
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        self.assertIn("Nullable(Float64)", sql)
        self.assertNotIn("JSONExtractFloat(c.eval_outputs", sql)
        self.assertIn("eval-123", sql)
        self.assertIn("JSONHas", sql)

    def test_pass_fail_eval_metric(self):
        builder = SimulationQueryBuilder(self._make_config(output_type="PASS_FAIL"))
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("JSONExtractString", sql)
        # pass_rate aggregation: countIf(lower(...) IN ('true','pass',...)) / nullIf(count(), 0)
        self.assertIn("countIf", sql)
        self.assertIn("nullIf(count(), 0)", sql)

    def test_choice_eval_metric(self):
        builder = SimulationQueryBuilder(
            self._make_config(output_type="CHOICE", aggregation="count")
        )
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("count()", sql)


class TestSimulationQueryBuilderTimeRange(unittest.TestCase):
    """Test time range parsing."""

    def test_preset_7d(self):
        config = {
            "metrics": [
                {"name": "duration", "type": "system_metric", "aggregation": "avg"}
            ],
            "time_range": {"preset": "7D"},
            "granularity": "day",
        }
        builder = SimulationQueryBuilder(config)
        start, end = builder.parse_time_range()
        self.assertAlmostEqual(
            (end - start).total_seconds(),
            timedelta(days=7).total_seconds(),
            delta=5,
        )

    def test_custom_range(self):
        config = {
            "metrics": [
                {"name": "duration", "type": "system_metric", "aggregation": "avg"}
            ],
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-31T23:59:59",
            },
            "granularity": "day",
        }
        builder = SimulationQueryBuilder(config)
        start, end = builder.parse_time_range()
        self.assertEqual(start.year, 2025)
        self.assertEqual(start.month, 1)
        self.assertEqual(end.day, 31)


class TestSimulationQueryBuilderResultFormatting(unittest.TestCase):
    """Test result formatting with time bucket filling."""

    def test_format_empty_results(self):
        config = {
            "metrics": [
                {"name": "duration", "type": "system_metric", "aggregation": "avg"}
            ],
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-03T00:00:00",
            },
            "granularity": "day",
        }
        builder = SimulationQueryBuilder(config)
        result = builder.format_results(
            [({"name": "duration", "aggregation": "avg"}, [])]
        )
        self.assertIn("metrics", result)
        self.assertEqual(len(result["metrics"]), 1)
        metric = result["metrics"][0]
        self.assertEqual(metric["name"], "duration")
        self.assertEqual(metric["unit"], "s")
        self.assertEqual(len(metric["series"]), 1)
        self.assertEqual(metric["series"][0]["name"], "total")

    def test_format_with_breakdown(self):
        config = {
            "metrics": [
                {"name": "duration", "type": "system_metric", "aggregation": "avg"}
            ],
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-02T00:00:00",
            },
            "granularity": "day",
            "breakdowns": [{"name": "scenario"}],
        }
        builder = SimulationQueryBuilder(config)
        result = builder.format_results(
            [
                (
                    {"name": "duration", "aggregation": "avg"},
                    [
                        {
                            "time_bucket": datetime(2025, 1, 1),
                            "breakdown_value": "Login Flow",
                            "value": 45.5,
                        },
                        {
                            "time_bucket": datetime(2025, 1, 1),
                            "breakdown_value": "Checkout",
                            "value": 30.2,
                        },
                    ],
                )
            ]
        )
        metric = result["metrics"][0]
        self.assertEqual(len(metric["series"]), 2)
        names = [s["name"] for s in metric["series"]]
        self.assertIn("Login Flow", names)
        self.assertIn("Checkout", names)

    def test_format_rounds_floats(self):
        config = {
            "metrics": [
                {"name": "overall_score", "type": "system_metric", "aggregation": "avg"}
            ],
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-02T00:00:00",
            },
            "granularity": "day",
        }
        builder = SimulationQueryBuilder(config)
        result = builder.format_results(
            [
                (
                    {"name": "overall_score", "aggregation": "avg"},
                    [
                        {"time_bucket": datetime(2025, 1, 1), "value": 7.123456789},
                    ],
                )
            ]
        )
        val = result["metrics"][0]["series"][0]["data"][0]["value"]
        self.assertEqual(val, 7.123457)


class TestSimulationQueryBuilderMultiMetric(unittest.TestCase):
    """Test multi-metric query building."""

    def test_multiple_metrics(self):
        config = {
            "metrics": [
                {"name": "duration", "type": "system_metric", "aggregation": "avg"},
                {
                    "name": "overall_score",
                    "type": "system_metric",
                    "aggregation": "avg",
                },
                {"name": "call_count", "type": "system_metric", "aggregation": "count"},
            ],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "workspace_id": "ws-123",
        }
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        self.assertEqual(len(queries), 3)
        names = [q[2]["name"] for q in queries]
        self.assertEqual(names, ["duration", "overall_score", "call_count"])


class TestSimulationQueryBuilderAgentFilter(unittest.TestCase):
    """Test agent_definition_ids filtering."""

    def test_agent_definition_ids_in_where(self):
        config = {
            "metrics": [
                {"name": "duration", "type": "system_metric", "aggregation": "avg"}
            ],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "workspace_id": "ws-123",
            "agent_definition_ids": ["agent-1", "agent-2"],
        }
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        self.assertIn("agent_definition_ids", params)
        self.assertIn("simulate_version_dict", sql)


class TestSimulationSchemaInDDL(unittest.TestCase):
    """Verify simulation tables are in the schema DDL list."""

    def test_simulation_tables_in_ddl(self):
        names = [name for name, _ in SCHEMA_DDL_STATEMENTS]
        expected = [
            "simulate_scenarios",
            "simulate_agent_definition",
            "simulate_agent_version",
            "simulate_run_test",
            "simulate_test_execution",
            "simulate_call_execution",
            "simulate_scenario_dict",
            "simulate_agent_dict",
            "simulate_version_dict",
            "simulate_run_test_dict",
            "simulate_calls",
        ]
        for table in expected:
            self.assertIn(table, names, f"Missing from DDL: {table}")

    def test_simulation_tables_after_dataset_tables(self):
        names = [name for name, _ in SCHEMA_DDL_STATEMENTS]
        dataset_cells_idx = names.index("dataset_cells")
        sim_scenarios_idx = names.index("simulate_scenarios")
        self.assertGreater(sim_scenarios_idx, dataset_cells_idx)

    def test_dimension_tables_before_fact_tables(self):
        names = [name for name, _ in SCHEMA_DDL_STATEMENTS]
        scenarios_idx = names.index("simulate_scenarios")
        call_exec_idx = names.index("simulate_call_execution")
        self.assertLess(scenarios_idx, call_exec_idx)

    def test_dictionaries_after_dimension_tables(self):
        names = [name for name, _ in SCHEMA_DDL_STATEMENTS]
        agent_def_idx = names.index("simulate_agent_definition")
        agent_dict_idx = names.index("simulate_agent_dict")
        self.assertLess(agent_def_idx, agent_dict_idx)


class TestSimulationQueryBuilderWhereClause(unittest.TestCase):
    """Test WHERE clause construction."""

    def test_base_where_includes_peerdb_filter(self):
        config = {
            "metrics": [
                {"name": "duration", "type": "system_metric", "aggregation": "avg"}
            ],
            "time_range": {"preset": "7D"},
            "granularity": "day",
        }
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        self.assertIn("_peerdb_is_deleted = 0", sql)

    def test_base_where_includes_time_filter(self):
        config = {
            "metrics": [
                {"name": "duration", "type": "system_metric", "aggregation": "avg"}
            ],
            "time_range": {"preset": "7D"},
            "granularity": "day",
        }
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        self.assertIn("start_date", params)
        self.assertIn("end_date", params)

    def test_workspace_filter(self):
        config = {
            "metrics": [
                {"name": "duration", "type": "system_metric", "aggregation": "avg"}
            ],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "workspace_id": "ws-123",
        }
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        self.assertIn("workspace_id", params)
        self.assertIn("simulate_scenario_dict", sql)


class TestSimulationQueryBuilderSecurity(unittest.TestCase):
    """Security tests for SimulationQueryBuilder to prevent injection and misuse."""

    def _make_config(self, metric_name="duration", aggregation="avg", **kwargs):
        config = {
            "metrics": [
                {
                    "name": metric_name,
                    "type": "system_metric",
                    "aggregation": aggregation,
                }
            ],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "workspace_id": "ws-123",
            **kwargs,
        }
        return config

    def test_unknown_metric_raises_value_error(self):
        """Verify that passing an unknown metric_name raises ValueError."""
        builder = SimulationQueryBuilder(self._make_config("nonexistent_metric"))
        with self.assertRaises(ValueError):
            builder.build_all_queries()

    def test_sql_injection_in_metric_name_blocked(self):
        """Verify that a SQL injection attempt in metric_name raises ValueError."""
        builder = SimulationQueryBuilder(
            self._make_config("1; DROP TABLE simulate_call_execution--")
        )
        with self.assertRaises(ValueError):
            builder.build_all_queries()

    def test_eval_key_sanitization(self):
        """Verify _sanitize_key rejects keys with special chars."""
        from tracer.services.clickhouse.query_builders.simulation_dashboard import (
            _sanitize_key,
        )

        # Valid keys should pass
        self.assertEqual(_sanitize_key("eval-empathy"), "eval-empathy")
        self.assertEqual(_sanitize_key("metric.name_v2"), "metric.name_v2")

        # Invalid keys should raise ValueError
        with self.assertRaises(ValueError):
            _sanitize_key("'; DROP TABLE--")
        with self.assertRaises(ValueError):
            _sanitize_key("key with spaces")
        with self.assertRaises(ValueError):
            _sanitize_key("")
        with self.assertRaises(ValueError):
            _sanitize_key("key;injection")

    def test_filter_prefix_uses_sf(self):
        """Verify simulation filter params use sf_ prefix not f_."""
        config = self._make_config(
            filters=[
                {
                    "metric_type": "system_metric",
                    "metric_name": "status",
                    "operator": "equal_to",
                    "value": "completed",
                }
            ]
        )
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        _, params, _ = queries[0]
        # Should use sf_ prefix for simulation filters
        sf_keys = [k for k in params if k.startswith("sf_")]
        self.assertTrue(
            len(sf_keys) > 0, "Expected sf_ prefixed params for simulation filters"
        )
        f_keys = [k for k in params if k.startswith("f_") and not k.startswith("sf_")]
        self.assertEqual(
            len(f_keys), 0, "Should not have bare f_ prefix params in simulation"
        )


if __name__ == "__main__":
    unittest.main()
