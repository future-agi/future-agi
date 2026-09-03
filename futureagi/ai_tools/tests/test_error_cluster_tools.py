"""
Unit tests for the error cluster Falcon tools:
  - list_error_clusters
  - get_error_cluster_detail
  - analyze_error_cluster

Run:
    pytest ai_tools/tests/test_error_cluster_tools.py -v
"""

import uuid

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
    def test_list_error_clusters_registered(self):
        tool = registry.get("list_error_clusters")
        assert tool is not None
        assert tool.category == "error_feed"

    def test_get_error_cluster_detail_registered(self):
        tool = registry.get("get_error_cluster_detail")
        assert tool is not None
        assert tool.category == "error_feed"

    def test_analyze_error_cluster_registered(self):
        tool = registry.get("analyze_error_cluster")
        assert tool is not None
        assert tool.category == "error_feed"

    def test_input_schemas_are_valid(self):
        for name in [
            "list_error_clusters",
            "get_error_cluster_detail",
            "analyze_error_cluster",
        ]:
            tool = registry.get(name)
            schema = tool.input_schema
            assert "properties" in schema or schema.get("type") == "object"


# ===================================================================
# list_error_clusters
# ===================================================================


class TestListErrorClusters:
    def test_empty_no_clusters(self, tool_context):
        result = run_tool("list_error_clusters", {}, tool_context)
        assert not result.is_error
        assert result.data["total_count"] == 0
        assert result.data["clusters"] == []

    def test_returns_cluster(self, tool_context, cluster):
        result = run_tool("list_error_clusters", {"days": 30}, tool_context)
        assert not result.is_error
        assert result.data["total_count"] >= 1
        found = [
            c for c in result.data["clusters"] if c["cluster_id"] == cluster.cluster_id
        ]
        assert len(found) == 1
        assert found[0]["impact"] == "HIGH"
        assert found[0]["events"] == 10

    def test_filter_by_project(self, tool_context, cluster, project):
        result = run_tool(
            "list_error_clusters",
            {"project_id": str(project.id), "days": 30},
            tool_context,
        )
        assert not result.is_error
        assert result.data["total_count"] >= 1

    def test_filter_by_wrong_project(self, tool_context, cluster):
        result = run_tool(
            "list_error_clusters",
            {"project_id": str(uuid.uuid4())},
            tool_context,
        )
        # Either permission denied or 0 results
        assert result.is_error or result.data["total_count"] == 0

    def test_search_filter(self, tool_context, cluster):
        result = run_tool(
            "list_error_clusters",
            {"search": "Hallucination", "days": 30},
            tool_context,
        )
        assert not result.is_error
        # Should find the cluster since error_type contains "Hallucination"
        assert result.data["total_count"] >= 1

    def test_search_no_match(self, tool_context, cluster):
        result = run_tool(
            "list_error_clusters",
            {"search": "ZZZZNONEXISTENT", "days": 30},
            tool_context,
        )
        assert not result.is_error
        assert result.data["total_count"] == 0

    def test_pagination_limit(self, tool_context, full_cluster):
        result = run_tool(
            "list_error_clusters",
            {"limit": 1, "days": 30},
            tool_context,
        )
        assert not result.is_error
        assert len(result.data["clusters"]) <= 1

    def test_days_filter_excludes_old_clusters(self, tool_context):
        """Cluster with last_seen 30 days ago should not appear with days=1."""
        from django.utils import timezone

        project = make_project(tool_context, name="Old Project")
        make_error_cluster(
            tool_context,
            project=project,
            cluster_id="KOLD001",
            last_seen=timezone.now() - timezone.timedelta(days=30),
            first_seen=timezone.now() - timezone.timedelta(days=60),
        )
        result = run_tool("list_error_clusters", {"days": 1}, tool_context)
        assert not result.is_error
        old_ids = [c["cluster_id"] for c in result.data["clusters"]]
        assert "KOLD001" not in old_ids

    def test_content_has_markdown_table(self, tool_context, cluster):
        result = run_tool("list_error_clusters", {"days": 30}, tool_context)
        assert not result.is_error
        assert "Cluster ID" in result.content
        assert "|" in result.content  # markdown table


# ===================================================================
# get_error_cluster_detail
# ===================================================================


class TestGetErrorClusterDetail:
    def test_not_found(self, tool_context):
        result = run_tool(
            "get_error_cluster_detail",
            {"cluster_id": "NONEXISTENT"},
            tool_context,
        )
        assert result.is_error
        assert result.error_code == "NOT_FOUND"

    def test_basic_detail(self, tool_context, cluster):
        result = run_tool(
            "get_error_cluster_detail",
            {"cluster_id": cluster.cluster_id, "include_traces": False},
            tool_context,
        )
        assert not result.is_error
        assert result.data["cluster_id"] == cluster.cluster_id
        assert result.data["impact"] == "HIGH"
        assert result.data["total_events"] == 10
        assert result.data["unique_traces"] == 3
        assert result.data["unique_users"] == 5
        assert result.data["traces"] == []

    def test_detail_with_traces(self, tool_context, full_cluster):
        cluster, project, traces, analyses, details = full_cluster
        result = run_tool(
            "get_error_cluster_detail",
            {
                "cluster_id": cluster.cluster_id,
                "include_traces": True,
                "trace_limit": 5,
            },
            tool_context,
        )
        assert not result.is_error
        assert len(result.data["traces"]) == 3
        # Each trace should have errors
        for t in result.data["traces"]:
            assert t["error_count"] >= 1
            assert len(t["errors"]) >= 1

    def test_trace_limit_respected(self, tool_context):
        cluster, project, traces, analyses, details = make_full_error_cluster(
            tool_context, num_traces=5, cluster_id="KLIMIT01"
        )
        result = run_tool(
            "get_error_cluster_detail",
            {"cluster_id": cluster.cluster_id, "trace_limit": 2},
            tool_context,
        )
        assert not result.is_error
        assert len(result.data["traces"]) <= 2

    def test_content_has_description(self, tool_context, cluster):
        result = run_tool(
            "get_error_cluster_detail",
            {"cluster_id": cluster.cluster_id, "include_traces": False},
            tool_context,
        )
        assert not result.is_error
        assert "Description" in result.content
        assert "hallucinated" in result.content.lower()

    def test_content_has_affected_traces_table(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "get_error_cluster_detail",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        assert "Affected Traces" in result.content
        assert "Score" in result.content
        assert "Priority" in result.content

    def test_error_details_in_content(self, tool_context, full_cluster):
        cluster = full_cluster[0]
        result = run_tool(
            "get_error_cluster_detail",
            {"cluster_id": cluster.cluster_id},
            tool_context,
        )
        assert not result.is_error
        # Should contain error IDs and root causes from details
        assert "Root causes:" in result.content or "Recommendation:" in result.content

    def test_project_name_in_summary(self, tool_context, full_cluster):
        cluster, project = full_cluster[0], full_cluster[1]
        result = run_tool(
            "get_error_cluster_detail",
            {"cluster_id": cluster.cluster_id, "include_traces": False},
            tool_context,
        )
        assert not result.is_error
        assert project.name in result.content

    def test_first_last_seen_in_data(self, tool_context, cluster):
        result = run_tool(
            "get_error_cluster_detail",
            {"cluster_id": cluster.cluster_id, "include_traces": False},
            tool_context,
        )
        assert not result.is_error
        assert result.data["first_seen"] is not None
        assert result.data["last_seen"] is not None


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
            "get_error_cluster_detail",
            {"cluster_id": "KOTHER01"},
            tool_context,
        )
        assert result.is_error
        assert result.error_code == "NOT_FOUND"

    def test_list_excludes_other_org(self, tool_context, db):
        """list_error_clusters should not return clusters from other orgs."""
        from accounts.models.organization import Organization
        from tracer.models.project import Project
        from tracer.models.trace_error_analysis import TraceErrorGroup

        other_org = Organization.objects.create(name="Other Org 2")
        other_project = Project.objects.create(
            name="Other Project 2",
            organization=other_org,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        TraceErrorGroup.objects.create(
            project=other_project,
            cluster_id="KOTHER02",
            error_type="Other Error",
            combined_impact="LOW",
            error_count=1,
        )

        result = run_tool("list_error_clusters", {"days": 365}, tool_context)
        assert not result.is_error
        cluster_ids = [c["cluster_id"] for c in result.data["clusters"]]
        assert "KOTHER02" not in cluster_ids


# ===================================================================
# EDGE CASES
# ===================================================================


class TestEdgeCases:
    def test_cluster_with_null_fields(self, tool_context):
        """Cluster with many nullable fields set to None."""
        project = make_project(tool_context, name="Null Project")
        cluster = make_error_cluster(
            tool_context,
            project=project,
            cluster_id="KNULL001",
            combined_description=None,
            first_seen=None,
            last_seen=None,
            unique_users=0,
            total_events=0,
        )
        result = run_tool(
            "get_error_cluster_detail",
            {"cluster_id": cluster.cluster_id, "include_traces": False},
            tool_context,
        )
        assert not result.is_error
        assert result.data["first_seen"] is None
        assert result.data["last_seen"] is None
        assert result.data["total_events"] == 0

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

    def test_validation_error_invalid_days(self, tool_context):
        result = run_tool("list_error_clusters", {"days": 0}, tool_context)
        assert result.is_error
        assert result.error_code == "VALIDATION_ERROR"

    def test_validation_error_invalid_limit(self, tool_context):
        result = run_tool("list_error_clusters", {"limit": 0}, tool_context)
        assert result.is_error
        assert result.error_code == "VALIDATION_ERROR"

    def test_validation_error_negative_offset(self, tool_context):
        result = run_tool("list_error_clusters", {"offset": -1}, tool_context)
        assert result.is_error
        assert result.error_code == "VALIDATION_ERROR"

    def test_validation_error_trace_limit_too_high(self, tool_context):
        result = run_tool(
            "get_error_cluster_detail",
            {"cluster_id": "X", "trace_limit": 100},
            tool_context,
        )
        assert result.is_error
        assert result.error_code == "VALIDATION_ERROR"
