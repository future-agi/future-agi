"""Isolated E2E issue transition seeder; never registered in normal deployments."""

import uuid

from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tracer.models.project import Project
from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.services.feed_alerts.events import issue_snapshot, record_issue_event


class E2EIssueRequest(serializers.Serializer):
    project_id = serializers.UUIDField()
    issue_id = serializers.UUIDField(required=False)
    title = serializers.CharField(max_length=200, required=False)
    source = serializers.ChoiceField(choices=["scanner", "eval"], required=False)
    status = serializers.ChoiceField(
        choices=["for_review", "escalating", "acknowledged", "resolved"],
        required=False,
    )
    severity = serializers.ChoiceField(
        choices=["low", "medium", "high", "critical"], required=False
    )
    occurrences = serializers.IntegerField(min_value=0, required=False)
    issue_group = serializers.CharField(max_length=100, required=False)
    issue_category = serializers.CharField(max_length=100, required=False)


class E2EFeedIssueView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if settings.ENV_TYPE != "local" or not settings.E2E_ERROR_FEED_ENABLED:
            return Response(status=404)
        serializer = E2EIssueRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = (
            getattr(request, "organization", None) or request.user.organization
        )
        workspace = getattr(request, "workspace", None)
        if not workspace or not request.user.can_write_to_workspace(workspace):
            return Response(
                {"error": "Workspace write access is required."}, status=403
            )
        project = Project.no_workspace_objects.filter(
            pk=data["project_id"],
            organization=organization,
            workspace=workspace,
            deleted=False,
        ).first()
        if project is None:
            return Response({"error": "Project not found."}, status=404)
        with transaction.atomic():
            if "issue_id" in data:
                issue = (
                    TraceErrorGroup.no_workspace_objects.select_for_update()
                    .filter(pk=data["issue_id"], project=project, deleted=False)
                    .first()
                )
                if issue is None:
                    return Response({"error": "Issue not found."}, status=404)
                before = issue_snapshot(issue)
            else:
                title = data.get("title") or "E2E Error Feed issue"
                issue = TraceErrorGroup.no_workspace_objects.create(
                    project=project,
                    cluster_id=f"E2E-{uuid.uuid4().hex[:8].upper()}",
                    error_type=title,
                    title=title,
                    source=data.get("source", "scanner"),
                    status=data.get("status", "for_review"),
                    priority={"critical": "urgent"}.get(
                        data.get("severity"), data.get("severity", "medium")
                    ),
                    error_count=data.get("occurrences", 1),
                    issue_group=data.get("issue_group", ""),
                    issue_category=data.get("issue_category", ""),
                )
                before = None
            if before is not None:
                fields = []
                for request_field, model_field in (
                    ("title", "title"),
                    ("source", "source"),
                    ("status", "status"),
                    ("occurrences", "error_count"),
                    ("issue_group", "issue_group"),
                    ("issue_category", "issue_category"),
                ):
                    if request_field in data:
                        setattr(issue, model_field, data[request_field])
                        fields.append(model_field)
                if "severity" in data:
                    issue.priority = {"critical": "urgent"}.get(
                        data["severity"], data["severity"]
                    )
                    issue.severity_source = "manual"
                    fields.extend(["priority", "severity_source"])
                if fields:
                    issue.save(update_fields=[*fields, "updated_at"])
            record_issue_event(
                cluster=issue, before=before, source_key=f"e2e:{uuid.uuid4()}"
            )
        return Response({"issue_id": str(issue.pk), "cluster_id": issue.cluster_id})
