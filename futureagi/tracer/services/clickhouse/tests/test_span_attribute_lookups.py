"""Tests for span_attribute_lookups — v2 schema compliance + project scoping."""

from unittest.mock import patch

import pytest

from tracer.services.clickhouse.span_attribute_lookups import (
    aggregate_attribute_over_traces,
    list_attribute_keys_for_traces,
    span_id_by_provider_log_id,
    spans_by_eval_attribute_call_execution_ids,
    trace_ids_with_simulator_call_execution_id,
)


@pytest.fixture
def mock_ch():
    """Patch ClickHouseClient and is_clickhouse_enabled."""
    with (
        patch(
            "tracer.services.clickhouse.span_attribute_lookups.is_clickhouse_enabled",
            return_value=True,
        ),
        patch(
            "tracer.services.clickhouse.span_attribute_lookups.ClickHouseClient"
        ) as MockClient,
        patch(
            "tracer.services.clickhouse.span_attribute_lookups.get_clickhouse_client"
        ) as get_client,
    ):
        instance = MockClient.return_value
        instance.is_configured = True
        get_client.return_value = instance
        yield instance


def assert_guarded_read(mock_ch, *, max_result_rows):
    kwargs = mock_ch.execute_read.call_args.kwargs
    assert kwargs["timeout_ms"] == 9_500
    assert kwargs["settings"] == {
        "max_threads": 1,
        "read_overflow_mode": "throw",
        "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
        "max_memory_usage": 36 * 1024 * 1024 * 1024,
        "max_result_bytes": 64 * 1024 * 1024,
        "result_overflow_mode": "throw",
        "timeout_overflow_mode": "throw",
        "max_result_rows": max_result_rows,
    }
    assert "max_rows_to_read" not in kwargs["settings"]


class TestAggregateAttributeOverTraces:
    def test_requires_project_id(self, mock_ch):
        mock_ch.execute_read.return_value = ([], [], 0)
        aggregate_attribute_over_traces("proj-1", ["t1", "t2"], "llm.model")
        query = mock_ch.execute_read.call_args[0][0]
        assert "project_id = %(pid)s" in query

    def test_filters_soft_deleted(self, mock_ch):
        mock_ch.execute_read.return_value = ([], [], 0)
        aggregate_attribute_over_traces("proj-1", ["t1"], "key")
        query = mock_ch.execute_read.call_args[0][0]
        assert "is_deleted = 0" in query

    def test_uses_v2_column_names(self, mock_ch):
        mock_ch.execute_read.return_value = ([], [], 0)
        aggregate_attribute_over_traces("proj-1", ["t1"], "key")
        query = mock_ch.execute_read.call_args[0][0]
        assert "attrs_string" in query
        assert "attrs_number" in query
        assert "attrs_bool" in query
        assert "span_attr_str" not in query
        assert "span_attr_num" not in query
        assert "span_attr_bool" not in query

    def test_empty_trace_ids_returns_empty(self, mock_ch):
        result = aggregate_attribute_over_traces("proj-1", [], "key")
        assert result == []
        mock_ch.execute_read.assert_not_called()

    def test_empty_attr_key_returns_empty(self, mock_ch):
        result = aggregate_attribute_over_traces("proj-1", ["t1"], "")
        assert result == []

    def test_returns_attribute_buckets(self, mock_ch):
        mock_ch.execute_read.return_value = (
            [("us-east-1", 5), ("eu-west-1", 3)],
            ["value", "cnt"],
            42,
        )
        result = aggregate_attribute_over_traces("proj-1", ["t1", "t2"], "region")
        assert len(result) == 2
        assert result[0].value == "us-east-1"
        assert result[0].count == 5

    def test_uses_guarded_read_policy(self, mock_ch):
        mock_ch.execute_read.return_value = ([], [], 0)
        aggregate_attribute_over_traces("proj-1", ["t1"], "region")
        assert_guarded_read(mock_ch, max_result_rows=100_000)


class TestListAttributeKeysForTraces:
    def test_uses_v2_columns(self, mock_ch):
        mock_ch.execute_read.return_value = ([], [], 0)
        list_attribute_keys_for_traces("proj-1", ["t1"])
        query = mock_ch.execute_read.call_args[0][0]
        assert "attrs_string" in query
        assert "attrs_number" in query
        assert "attrs_bool" in query
        assert "span_attr_str" not in query

    def test_project_scoped(self, mock_ch):
        mock_ch.execute_read.return_value = ([], [], 0)
        list_attribute_keys_for_traces("proj-1", ["t1"])
        query = mock_ch.execute_read.call_args[0][0]
        assert "project_id = %(pid)s" in query

    def test_soft_delete_filtered(self, mock_ch):
        mock_ch.execute_read.return_value = ([], [], 0)
        list_attribute_keys_for_traces("proj-1", ["t1"])
        query = mock_ch.execute_read.call_args[0][0]
        assert "is_deleted = 0" in query

    def test_empty_traces_returns_empty(self, mock_ch):
        result = list_attribute_keys_for_traces("proj-1", [])
        assert result == []
        mock_ch.execute_read.assert_not_called()

    def test_returns_attribute_keys(self, mock_ch):
        mock_ch.execute_read.return_value = (
            [("gen_ai.span.kind", "string", 5), ("cost_breakdown", "string", 3)],
            ["key", "type", "trace_count"],
            10,
        )
        result = list_attribute_keys_for_traces("proj-1", ["t1", "t2"])
        assert len(result) == 2
        assert result[0].key == "gen_ai.span.kind"
        assert result[0].count == 5


class TestFormerRawExecuteLookups:
    def test_simulator_trace_lookup_uses_input_bounded_guarded_read(self, mock_ch):
        mock_ch.execute_read.return_value = ([("t1",), ("t2",)], [], 3)

        result = trace_ids_with_simulator_call_execution_id(["t1", "t2"])

        assert result == {"t1", "t2"}
        assert_guarded_read(mock_ch, max_result_rows=2)
        mock_ch.execute.assert_not_called()

    def test_eval_attribute_lookup_uses_guarded_read(self, mock_ch):
        mock_ch.execute_read.return_value = (
            [("s1", "t1", "call-1", '{"ok": true}')],
            [],
            4,
        )

        result = spans_by_eval_attribute_call_execution_ids(["call-1"])

        assert result["call-1"][0]["id"] == "s1"
        assert_guarded_read(mock_ch, max_result_rows=100_000)
        mock_ch.execute.assert_not_called()

    def test_provider_log_lookup_uses_single_row_guarded_read(self, mock_ch):
        mock_ch.execute_read.return_value = ([("span-1",)], [], 2)

        result = span_id_by_provider_log_id("project-1", "openai", "log-1")

        assert result == "span-1"
        assert_guarded_read(mock_ch, max_result_rows=1)
        mock_ch.execute.assert_not_called()
