"""Tests for the cluster-RCA tool schema correctness."""

import pytest

from ee.agenthub.cluster_rca.tools import CLUSTER_RCA_TOOLS


def _tool_by_name(name: str) -> dict:
    for t in CLUSTER_RCA_TOOLS:
        if t["function"]["name"] == name:
            return t["function"]
    raise ValueError(f"Tool {name} not found")


class TestAggregateToolSchema:
    def test_session_count_not_in_enum(self):
        """session_count was never wired — must not appear in the schema."""
        agg = _tool_by_name("aggregate")
        enum = agg["parameters"]["properties"]["metric"]["enum"]
        assert "session_count" not in enum

    def test_valid_metrics_present(self):
        agg = _tool_by_name("aggregate")
        enum = agg["parameters"]["properties"]["metric"]["enum"]
        assert "trace_count" in enum
        assert "span_count" in enum
        assert "scan_issue_count" in enum

    def test_group_by_description_uses_span_tool_name(self):
        """The example should use span_tool_name, not bare tool_name."""
        agg = _tool_by_name("aggregate")
        desc = agg["parameters"]["properties"]["group_by"]["description"]
        assert "span_tool_name" in desc
        assert "'tool_name'" not in desc

    def test_group_by_description_no_hour_day(self):
        """hour/day should not be in group_by — use timeline instead."""
        agg = _tool_by_name("aggregate")
        desc = agg["parameters"]["properties"]["group_by"]["description"]
        assert "'hour'" not in desc
        assert "'day'" not in desc


class TestAllToolsHaveRequiredFields:
    @pytest.mark.parametrize("tool", CLUSTER_RCA_TOOLS)
    def test_tool_has_name_and_description(self, tool):
        fn = tool["function"]
        assert fn.get("name"), "Tool missing name"
        assert fn.get("description"), f"Tool {fn['name']} missing description"
        assert fn.get("parameters"), f"Tool {fn['name']} missing parameters"
