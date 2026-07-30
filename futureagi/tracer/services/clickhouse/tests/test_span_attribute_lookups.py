"""Tests for span_attribute_lookups — v2 schema compliance + project scoping."""

from unittest.mock import patch

import pytest

from tracer.services.clickhouse.span_attribute_lookups import (
    aggregate_attribute_over_traces,
    list_attribute_keys_for_traces,
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
    ):
        instance = MockClient.return_value
        yield instance


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
