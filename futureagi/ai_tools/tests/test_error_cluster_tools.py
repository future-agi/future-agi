"""
Unit tests for the analyze_error_cluster Falcon tool.

list_error_clusters / get_error_cluster now come from the generated MCP
catalog and are covered by mcp_server/tests.

Run:
    pytest ai_tools/tests/test_error_cluster_tools.py -v
"""

import pytest

from ai_tools.registry import registry
from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import (
    make_error_cluster,
    make_error_cluster_trace,
    make_full_error_cluster,
    make_project,
    make_trace,
    make_trace_error_analysis,
    make_trace_error_detail,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tool_context):
    return make_project(tool_context)


@pytest.fixture
def cluster(tool_context, project):
    return make_error_cluster(tool_context, project=project)


@pytest.fixture
def full_cluster(tool_context):
    """A cluster with 3 traces, each having 2 error details."""
    return make_full_error_cluster(tool_context, num_traces=3)


# ===================================================================
# TOOL REGISTRATION
# ===================================================================


class TestToolRegistration:
    def test_analyze_error_cluster_registered(self):
        tool = registry.get("analyze_error_cluster")
        assert tool is not None
        assert tool.category == "error_feed"

    def test_input_schemas_are_valid(self):
        tool = registry.get("analyze_error_cluster")
        schema = tool.input_schema
        assert "properties" in schema or schema.get("type") == "object"


# ===================================================================
# analyze_error_cluster
# ===================================================================


class TestAnalyzeErrorCluster:
    def test_not_found(self, tool_context):
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": "NONEXISTENT"},
            tool_context,
        )
        assert result.is_error
        assert result.error_code == "NOT_FOUND"

    def test_cluster_no_traces(self, tool_context, cluster):
        """Cluster exists but has no ErrorClusterTraces records."""
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert result.data["traces_analyzed"] == 0
        assert "No traces found" in result.content

    def test_basic_analysis(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert result.data["traces_analyzed"] == 3
        assert result.data["total_errors"] == 6  # 3 traces * 2 errors each
        assert result.data["cluster_id"] == cluster.cluster_id

    def test_root_causes_aggregated(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert len(result.data["top_root_causes"]) > 0
        # Each root cause has cause and count
        for rc in result.data["top_root_causes"]:
            assert "cause" in rc
            assert "count" in rc
            assert rc["count"] >= 1

    def test_recommendations_aggregated(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert len(result.data["top_recommendations"]) > 0

    def test_score_distribution(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert result.data["score_avg"] is not None
        assert result.data["score_min"] is not None
        assert result.data["score_max"] is not None
        assert (
            result.data["score_min"]
            <= result.data["score_avg"]
            <= result.data["score_max"]
        )

    def test_question_appears_in_content(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "analyze_error_cluster",
            {
                "cluster_id": cluster.cluster_id,
                "question": "Why do these errors happen on Mondays?",
            },
            tool_context,
        )
        assert not result.is_error
        assert "Why do these errors happen on Mondays?" in result.content

    def test_max_traces_respected(self, tool_context):
        cluster, *_ = make_full_error_cluster(
            tool_context, num_traces=5, cluster_id="KMAX001"
        )
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id, "max_traces": 2},
            tool_context,
        )
        assert not result.is_error
        assert result.data["traces_analyzed"] <= 2

    def test_content_has_sections(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert "Cluster Analysis" in result.content
        assert "Top Root Causes" in result.content
        assert "Top Recommendations" in result.content
        assert "Per-Trace Summary" in result.content

    def test_impact_distribution_in_content(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert "Error Impact Distribution" in result.content

    def test_evidence_samples_in_content(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert "Evidence Samples" in result.content

    def test_temporal_pattern_in_content(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert "Temporal Pattern" in result.content

    def test_per_trace_data_structure(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        for t in result.data["traces"]:
            assert "trace_id" in t
            assert "score" in t
            assert "error_count" in t


# ===================================================================
# CROSS-ORG ACCESS CONTROL
# ===================================================================


class TestAccessControl:
    """Verify clusters from other organizations are not accessible."""

    def test_cluster_from_other_org_not_found(self, tool_context, db):
        """Create a cluster under a different org and verify it's invisible."""
        from accounts.models.organization import Organization
        from tracer.models.project import Project
        from tracer.models.trace_error_analysis import TraceErrorGroup

        other_org = Organization.objects.create(name="Other Org")
        other_project = Project.objects.create(
            name="Other Project",
            organization=other_org,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        TraceErrorGroup.objects.create(
            project=other_project,
            cluster_id="KOTHER01",
            error_type="Test Error",
            combined_impact="HIGH",
            error_count=1,
        )

        # Try to access with our tool_context (different org)
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": "KOTHER01"},
            tool_context,
        )
        assert result.is_error
        assert result.error_code == "NOT_FOUND"


# ===================================================================
# EDGE CASES
# ===================================================================


class TestEdgeCases:
    def test_analysis_with_no_scores(self, tool_context):
        """Traces with no score data should not crash analyze."""
        project = make_project(tool_context, name="No Score Project")
        cluster = make_error_cluster(
            tool_context, project=project, cluster_id="KNOSCORE"
        )
        trace = make_trace(tool_context, project=project, name="noscore-trace")
        analysis = make_trace_error_analysis(
            tool_context,
            trace=trace,
            project=project,
            overall_score=None,
            factual_grounding_score=None,
            privacy_and_safety_score=None,
            instruction_adherence_score=None,
            optimal_plan_execution_score=None,
        )
        make_trace_error_detail(tool_context, analysis=analysis)
        make_error_cluster_trace(tool_context, cluster=cluster, trace=trace)

        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert result.data["score_avg"] is None
        assert result.data["score_min"] is None
        assert result.data["score_max"] is None

    def test_detail_with_empty_root_causes(self, tool_context):
        """Error details with empty root_causes and no recommendation."""
        project = make_project(tool_context, name="Empty RC Project")
        cluster = make_error_cluster(
            tool_context, project=project, cluster_id="KEMPTYRC"
        )
        trace = make_trace(tool_context, project=project)
        analysis = make_trace_error_analysis(tool_context, trace=trace, project=project)
        make_trace_error_detail(
            tool_context,
            analysis=analysis,
            root_causes=[],
            recommendation=None,
            evidence_snippets=[],
            description=None,
        )
        make_error_cluster_trace(tool_context, cluster=cluster, trace=trace)

        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert result.data["top_root_causes"] == []
        assert result.data["top_recommendations"] == []

    def test_large_cluster_many_traces(self, tool_context):
        """Cluster with many traces — verifies performance doesn't degrade."""
        cluster, project, traces, analyses, details = make_full_error_cluster(
            tool_context, num_traces=10, cluster_id="KLARGE01"
        )
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": cluster.cluster_id, "max_traces": 10},
            tool_context,
        )
        assert not result.is_error
        assert result.data["traces_analyzed"] == 10
        assert result.data["total_errors"] == 20

    def test_validation_error_max_traces_too_high(self, tool_context):
        result = run_tool(
            "analyze_error_cluster",
            {"cluster_id": "X", "max_traces": 1000},
            tool_context,
        )
        assert result.is_error
        assert result.error_code == "VALIDATION_ERROR"
