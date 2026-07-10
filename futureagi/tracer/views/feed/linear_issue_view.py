"""
Linear integration endpoints for Error Feed.

POST /tracer/feed/issues/{cluster_id}/create-linear-issue/
GET  /tracer/feed/integrations/linear/teams/
"""

import uuid as _uuid

import structlog
from django.conf import settings
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from integrations.models.integration_connection import IntegrationPlatform
from integrations.services.credentials import CredentialManager
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.queries.feed import trace_judge, priority_to_severity
from tracer.views.feed._permissions import resolve_requested_project_ids

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiErrorResponseSerializer,
    403: ApiErrorResponseSerializer,
    404: ApiErrorResponseSerializer,
    500: ApiErrorResponseSerializer,
}


class CreateLinearIssueSerializer(serializers.Serializer):
    team_id = serializers.CharField()
    trace_id = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    priority = serializers.IntegerField(required=False, default=0)


class CreateLinearIssueResultSerializer(serializers.Serializer):
    already_linked = serializers.BooleanField(required=False)
    issue_id = serializers.CharField(required=False, allow_null=True)
    issue_url = serializers.CharField(required=False, allow_null=True)
    issue_title = serializers.CharField(required=False, allow_null=True)


class CreateLinearIssueResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = CreateLinearIssueResultSerializer()


class LinearTeamSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    key = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class LinearTeamsResultSerializer(serializers.Serializer):
    connected = serializers.BooleanField()
    teams = LinearTeamSerializer(many=True)


class LinearTeamsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = LinearTeamsResultSerializer()


def _cluster_url(cluster_id: str) -> str:
    """Absolute URL to the cluster's detail page in the Future AGI app."""
    app_url = getattr(settings, "APP_URL", None)
    scheme = getattr(settings, "ssl", "https://")
    if not app_url:
        return ""
    return f"{scheme}{app_url}/dashboard/error-feed/{cluster_id}"


def _build_issue_description(cluster: TraceErrorGroup, trace_id: str | None) -> str:
    """Build the Linear issue description.

    The cluster-level RCA (synthesis + fix + confidence + evidence), when one
    has run, is included in full — the assignee should be able to act from
    the ticket without opening the app. Per-trace deep analysis (when a trace
    is supplied) follows. No noisy stats; those live on the cluster page.
    """
    parts: list[str] = []

    cluster_url = _cluster_url(cluster.cluster_id)
    if cluster_url:
        parts.append(
            f"**[View in Future AGI]({cluster_url})** — `{cluster.cluster_id}`"
        )
    else:
        parts.append(f"**Cluster**: `{cluster.cluster_id}`")

    context_bits = [f"Severity: **{priority_to_severity(cluster.priority)}**"]
    if cluster.unique_traces:
        context_bits.append(f"{cluster.unique_traces} traces")
    if cluster.issue_group:
        context_bits.append(f"`{cluster.issue_group}`")
    parts.append(" · ".join(context_bits))

    if cluster.rca_synthesis:
        parts.append("## Root cause analysis")
        parts.append(cluster.rca_synthesis)
        if cluster.rca_fix:
            parts.append("## Recommended fix")
            parts.append(cluster.rca_fix)
        meta_bits: list[str] = []
        if cluster.rca_confidence:
            meta_bits.append(f"Confidence: **{cluster.rca_confidence}**")
        # Runs persisted before the alias fix stored LLM-facing labels
        # (T01, ...) — meaningless outside the run, so only ship real UUIDs.
        evidence_ids = []
        for t in cluster.rca_evidence_trace_ids or []:
            try:
                _uuid.UUID(str(t))
                evidence_ids.append(str(t))
            except ValueError:
                continue
        if evidence_ids:
            ids = ", ".join(f"`{t}`" for t in evidence_ids[:5])
            meta_bits.append(f"Evidence traces: {ids}")
        if meta_bits:
            parts.append(" · ".join(meta_bits))

    if trace_id is None:
        return "\n\n".join(parts)

    # Eval clusters have no deep analysis; the evaluator's reasoning for the
    # sampled trace is the per-trace context worth shipping instead.
    try:
        judge_reason, judge_score = trace_judge(str(trace_id))
    except Exception:
        logger.warning("trace_judge_failed", exc_info=True)
        judge_reason, judge_score = None, None
    if judge_reason:
        score_sfx = f" ({judge_score:.2f}/1.00)" if judge_score is not None else ""
        parts.append(f"## Evaluator reasoning — sampled trace{score_sfx}")
        parts.append(judge_reason)

    return "\n\n".join(parts)


class CreateLinearIssueView(APIView):
    """POST /tracer/feed/issues/{cluster_id}/create-linear-issue/"""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=CreateLinearIssueSerializer,
        responses={200: CreateLinearIssueResponseSerializer, **ERROR_RESPONSES},
    )
    def post(self, request, cluster_id: str):
        project_ids = resolve_requested_project_ids(request, None)
        if project_ids is None:
            return self._gm.forbidden_response("Access denied")

        # Find the cluster
        cluster = TraceErrorGroup.objects.filter(
            cluster_id=cluster_id,
            project_id__in=project_ids,
        ).first()
        if cluster is None:
            return self._gm.not_found(f"Cluster {cluster_id} not found")

        # Already linked?
        if cluster.external_issue_url:
            return self._gm.success_response(
                {
                    "already_linked": True,
                    "issue_url": cluster.external_issue_url,
                    "issue_id": cluster.external_issue_id,
                }
            )

        # Find active Linear connection for this org
        from integrations.models.integration_connection import (
            ConnectionStatus,
            IntegrationConnection,
        )

        connection = (
            IntegrationConnection.objects.filter(
                organization=request.user.organization,
                platform=IntegrationPlatform.LINEAR,
                deleted=False,
            )
            .exclude(status=ConnectionStatus.ERROR)
            .order_by("-created_at")
            .first()
        )
        if connection is None:
            return self._gm.bad_request(
                "No active Linear integration found. "
                "Connect Linear in Settings > Integrations first."
            )

        credentials = CredentialManager.decrypt(connection.encrypted_credentials)

        # Build default title/description from cluster if not provided
        title = request.validated_data.get("title") or ""
        if not title:
            title = f"[{cluster.cluster_id}] {cluster.title or cluster.error_type}"

        description = request.validated_data.get("description") or ""
        if not description:
            description = _build_issue_description(
                cluster, request.validated_data.get("trace_id")
            )

        try:
            from integrations.services.linear_service import LinearService

            service = LinearService()
            issue = service.create_issue(
                credentials=credentials,
                team_id=request.validated_data["team_id"],
                title=title[:200],
                description=description,
                priority=request.validated_data.get("priority", 0),
            )
        except Exception:
            logger.exception("linear_create_issue_failed", cluster_id=cluster_id)
            return self._gm.bad_request("Failed to create Linear issue")

        # Store the link on the cluster
        cluster.external_issue_url = issue["url"]
        cluster.external_issue_id = issue["identifier"]
        cluster.save(
            update_fields=["external_issue_url", "external_issue_id", "updated_at"]
        )

        logger.info(
            "linear_issue_created",
            cluster_id=cluster_id,
            issue_id=issue["identifier"],
            issue_url=issue["url"],
        )

        return self._gm.success_response(
            {
                "issue_id": issue["identifier"],
                "issue_url": issue["url"],
                "issue_title": issue["title"],
            }
        )


class LinearTeamsView(APIView):
    """GET /tracer/feed/integrations/linear/teams/

    Returns the list of Linear teams for the team picker dropdown.
    Requires an active Linear integration for the user's org.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        responses={200: LinearTeamsResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request):
        from integrations.models.integration_connection import (
            ConnectionStatus,
            IntegrationConnection,
        )

        connection = (
            IntegrationConnection.objects.filter(
                organization=request.user.organization,
                platform=IntegrationPlatform.LINEAR,
                deleted=False,
            )
            .exclude(status=ConnectionStatus.ERROR)
            .order_by("-created_at")
            .first()
        )
        if connection is None:
            return self._gm.success_response(
                {
                    "connected": False,
                    "teams": [],
                }
            )

        credentials = CredentialManager.decrypt(connection.encrypted_credentials)

        try:
            from integrations.services.linear_service import LinearService

            service = LinearService()
            teams = service.get_teams(credentials)
        except Exception:
            logger.exception("linear_get_teams_failed")
            return self._gm.bad_request("Failed to fetch Linear teams")

        return self._gm.success_response(
            {
                "connected": True,
                "teams": teams,
            }
        )
