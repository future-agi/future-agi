"""Unit tests for Error Feed trace list & detail annotations."""

import uuid

import pytest
from django.utils import timezone

from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    Priority,
    TraceErrorGroup,
)
from tracer.models.trace_investigation import (
    TraceInvestigationAttribution,
    TraceInvestigationFinding,
    TraceInvestigationReport,
    TraceInvestigationSource,
)
from tracer.queries.trace_error_feed import (
    annotate_trace_detail_with_error_feed,
    list_issues_for_traces,
)


@pytest.mark.django_db
class TestTraceErrorFeedQueries:
    def test_list_issues_and_annotate_trace_detail_with_multi_issue_focus(
        self, organization, workspace, observe_project
    ):
        trace_id = uuid.uuid4()
        now = timezone.now()

        cluster_a = TraceErrorGroup.objects.create(
            project=observe_project,
            cluster_id="ISS-A",
            error_type="tool_failure",
            source=ClusterSource.SCANNER,
            title="Tool payload truncated",
            priority=Priority.URGENT,
            status=FeedIssueStatus.ESCALATING,
            error_count=5,
            total_events=5,
            unique_traces=2,
        )
        cluster_b = TraceErrorGroup.objects.create(
            project=observe_project,
            cluster_id="ISS-B",
            error_type="hallucination",
            source=ClusterSource.SCANNER,
            title="Assistant invented balance figure",
            priority=Priority.MEDIUM,
            status=FeedIssueStatus.FOR_REVIEW,
            error_count=2,
            total_events=2,
            unique_traces=1,
        )

        report = TraceInvestigationReport.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            trace_id=trace_id,
            source=TraceInvestigationSource.OMEGA,
            engine_version="omega-ef-v20.9",
            contract_version="omega-investigation/v1",
            execution_status="completed",
            outcome="failure",
            is_current=True,
            recorded_at=now,
            read_cutoff=now,
            memory_snapshot_id="mem-1",
            memory_digest="sha256:" + "0" * 64,
            evidence_digest="sha256:" + "1" * 64,
            result_digest="sha256:" + "2" * 64,
        )
        finding_a = TraceInvestigationFinding.no_workspace_objects.create(
            id=uuid.uuid4(),
            report=report,
            finding_id="F1",
            ordinal=0,
            kind="tool_call_failure",
            statement="Tool response truncated before parsing",
            recovery="unrecovered",
            cluster=cluster_a,
        )
        TraceInvestigationAttribution.no_workspace_objects.create(
            finding=finding_a,
            role="origin",
            status="supported",
            span_id="span-origin",
            explanation="Tool response truncated at 200 chars",
        )
        TraceInvestigationAttribution.no_workspace_objects.create(
            finding=finding_a,
            role="symptom",
            status="supported",
            span_id="span-symptom",
            explanation="Final answer fabricated missing field",
        )

        finding_b = TraceInvestigationFinding.no_workspace_objects.create(
            id=uuid.uuid4(),
            report=report,
            finding_id="F2",
            ordinal=1,
            kind="hallucination",
            statement="Second issue on same trace",
            recovery="unrecovered",
            cluster=cluster_b,
        )
        TraceInvestigationAttribution.no_workspace_objects.create(
            finding=finding_b,
            role="origin",
            status="supported",
            span_id="span-symptom",
            explanation="Fabricated balance value",
        )

        ErrorClusterTraces.no_workspace_objects.create(
            trace_id=trace_id,
            cluster=cluster_a,
            finding=finding_a,
        )
        ErrorClusterTraces.no_workspace_objects.create(
            trace_id=trace_id,
            cluster=cluster_b,
            finding=finding_b,
        )

        listed = list_issues_for_traces(
            project_ids=[str(observe_project.id)],
            trace_ids=[str(trace_id)],
        )
        assert str(trace_id) in listed
        issues = listed[str(trace_id)]
        assert [item["cluster_id"] for item in issues] == ["ISS-A", "ISS-B"]
        assert issues[0]["severity"] == "critical"
        assert issues[0]["origin_span_id"] == "span-origin"
        assert issues[0]["symptom_span_id"] == "span-symptom"

        spans_tree = [
            {
                "observation_span": {
                    "id": "span-root",
                    "name": "agent.run",
                    "span_attributes": {},
                },
                "children": [
                    {
                        "observation_span": {
                            "id": "span-origin",
                            "name": "tool.fetch_account",
                            "span_attributes": {},
                        },
                        "children": [],
                    },
                    {
                        "observation_span": {
                            "id": "span-symptom",
                            "name": "llm.respond",
                            "span_attributes": {},
                        },
                        "children": [],
                    },
                ],
            }
        ]

        detail_issues, active_cid = annotate_trace_detail_with_error_feed(
            project_id=str(observe_project.id),
            trace_id=str(trace_id),
            observation_spans=spans_tree,
            cluster_id="ISS-A",
        )
        assert len(detail_issues) == 2
        assert active_cid == "ISS-A"
        origin_attrs = spans_tree[0]["children"][0]["observation_span"][
            "span_attributes"
        ]
        symptom_attrs = spans_tree[0]["children"][1]["observation_span"][
            "span_attributes"
        ]
        assert origin_attrs["error_feed.step"] == 1
        assert origin_attrs["error_feed.role"] == "origin_span"
        assert symptom_attrs["error_feed.step"] == 2
        assert symptom_attrs["error_feed.role"] == "symptom_span"
