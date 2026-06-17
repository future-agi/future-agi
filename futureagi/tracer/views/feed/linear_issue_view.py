"""
Linear integration endpoints for Error Feed.

POST /tracer/feed/issues/{cluster_id}/create-linear-issue/
GET  /tracer/feed/integrations/linear/teams/
"""

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
from tracer.utils import feed as feed_service
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


def _build_issue_description(
    cluster: TraceErrorGroup, trace_id: str | None
) -> str:
    """Build the Linear issue description.

    Kept intentionally short: title, link back, and — when a deep
    analysis has run for the supplied trace — its root causes and
    immediate fixes. No noisy stats; those live on the cluster page.
    """
    parts: list[str] = []

    cluster_url = _cluster_url(cluster.cluster_id)
    if cluster_url:
        parts.append(f"**[View in Future AGI]({cluster_url})** — `{cluster.cluster_id}`")
    else:
        parts.append(f"**Cluster**: `{cluster.cluster_id}`")

    if trace_id is None:
        return "\n\n".join(parts)

    try:
        analysis = feed_service.get_deep_analysis(
            cluster.cluster_id, trace_id=str(trace_id)
        )
    except Exception:
        # Deep analysis is best-effort context — never block ticket creation.
        logger.exception(
            "linear_description_deep_analysis_failed",
            cluster_id=cluster.cluster_id,
            trace_id=str(trace_id),
        )
        return "\n\n".join(parts)

    if analysis is None or analysis.status != "done":
        return "\n\n".join(parts)

    if analysis.root_causes:
        parts.append("## Root causes")
        for rc in analysis.root_causes:
            line = f"{rc.rank}. **{rc.title}**"
            if rc.description:
                line += f" — {rc.description}"
            parts.append(line)

    fixes: list[str] = []
    for rec in analysis.recommendations:
        if rec.immediate_fix:
            label = rec.title or rec.id
            fixes.append(f"- **{label}**: {rec.immediate_fix}")
    if fixes:
        parts.append("## Immediate fixes")
        parts.extend(fixes)

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
