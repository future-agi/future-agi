"""Connecting a code agent with no environment: scan a source for its agents, connect
the one picked, and confirm what its first reading found."""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.models import AgentDefinition, AgentScan
from simulate.serializers.requests.agent_connect import (
    AgentConnectRequestSerializer,
    AgentSourceRequestSerializer,
)
from simulate.serializers.response.agent_connect import (
    AgentConnectResponseSerializer,
    AgentFoundCheckResponseSerializer,
    AgentScanResponseSerializer,
    AgentScanStartedResponseSerializer,
)
from simulate.services import agent_connect
from simulate.services.agent_reading_sandbox import expire_stale_scan
from simulate.services.hosted_harness import HostedHarnessError
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope, error_details
from tfc.utils.api_serializers import (
    ApiErrorWithDetailsResponseSerializer,
    EmptyRequestSerializer,
)


class _ConnectView(APIView):
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, HostedHarnessError):
            body = build_error_envelope(
                exc.message,
                status_code=exc.status_code,
                code=exc.code,
                details=error_details(exc.details) if exc.details else None,
                extra={"retryable": exc.retryable},
            )
            return Response(body, status=exc.status_code)
        return super().handle_exception(exc)

    @staticmethod
    def _scope(request):
        organization = (
            getattr(request, "organization", None) or request.user.organization
        )
        workspace = getattr(request, "workspace", None) or getattr(
            request.user, "workspace", None
        )
        return organization, workspace


def _scan_body(scan: AgentScan) -> dict:
    source = scan.source or {}
    report = scan.report or {}
    failure = scan.failure or {}
    return {
        "id": scan.id,
        "status": scan.status,
        "kind": source.get("kind"),
        "repository": source.get("repository"),
        "ref": source.get("ref"),
        "commit_sha": report.get("commit_sha") or source.get("commit_sha"),
        "agents": report.get("agents") or [],
        "truncated": bool(report.get("truncated")),
        "error": report.get("error"),
        "failure": (
            {
                "code": str(failure.get("code") or ""),
                "message": str(failure.get("message") or ""),
                "retryable": bool(failure.get("retryable")),
            }
            if failure
            else None
        ),
    }


class AgentScanView(_ConnectView):
    """POST /agents/scan/: find the agents in a source; the drawer polls the result."""

    @validated_request(
        request_serializer=AgentSourceRequestSerializer,
        responses={
            202: AgentScanStartedResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            422: ApiErrorWithDetailsResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        organization, workspace = self._scope(request)
        scan = agent_connect.start_scan(
            organization=organization,
            workspace=workspace,
            user=request.user,
            source=request.validated_data,
        )
        return Response(
            AgentScanStartedResponseSerializer(scan).data,
            status=status.HTTP_202_ACCEPTED,
        )


class AgentScanDetailView(_ConnectView):
    """GET /agents/scan/{id}/."""

    @validated_request(
        responses={
            200: AgentScanResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, scan_id, *args, **kwargs):
        organization, workspace = self._scope(request)
        scan = AgentScan.no_workspace_objects.filter(
            id=scan_id, organization=organization, workspace=workspace
        ).first()
        if scan is None:
            raise HostedHarnessError(
                "scan_not_found", "Scan not found", status_code=404
            )
        scan = expire_stale_scan(scan)
        return Response(AgentScanResponseSerializer(_scan_body(scan)).data)


class AgentConnectView(_ConnectView):
    """POST /agents/connect/: the agent and its first version, read on their own."""

    @validated_request(
        request_serializer=AgentConnectRequestSerializer,
        responses={
            200: AgentConnectResponseSerializer,
            201: AgentConnectResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            422: ApiErrorWithDetailsResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        organization, workspace = self._scope(request)
        data = dict(request.validated_data)
        name = data.pop("name")
        env_values = data.pop("env_values", None)
        connected = agent_connect.connect_agent(
            organization=organization,
            workspace=workspace,
            user=request.user,
            source=data,
            name=name,
            env_values=env_values,
        )
        body = {
            "agent_id": connected.definition.id,
            "version_id": connected.version.id,
            "exists": not connected.created,
        }
        return Response(
            AgentConnectResponseSerializer(body).data,
            status=status.HTTP_201_CREATED if connected.created else status.HTTP_200_OK,
        )


class AgentFoundCheckView(_ConnectView):
    """POST /agent-definitions/{id}/found-check/: what the first reading found is
    confirmed; the agent can be built from now."""

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={
            200: AgentFoundCheckResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def post(self, request, agent_id, *args, **kwargs):
        organization, _workspace = self._scope(request)
        agent = AgentDefinition.objects.filter(
            id=agent_id, organization=organization, deleted=False
        ).first()
        if agent is None:
            raise HostedHarnessError(
                "agent_not_found", "Agent definition not found", status_code=404
            )
        agent = agent_connect.found_check(agent, request.user)
        return Response(AgentFoundCheckResponseSerializer(agent).data)
