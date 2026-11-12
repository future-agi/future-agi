"""Versions of a connected agent from the Agents page: one change endpoint by kind,
production, retry and discard."""

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import viewer_denied
from simulate.models import AgentDefinition, AgentVersion
from simulate.serializers.requests.agent_version import (
    AgentVersionChangeRequestSerializer,
)
from simulate.serializers.response.agent_version import (
    AgentBuildPreflightResponseSerializer,
    AgentVersionActionResponseSerializer,
    AgentVersionChangeResponseSerializer,
    AgentVersionCompatibilityResponseSerializer,
    AgentVersionProductionResponseSerializer,
)
from simulate.services import agent_versions
from simulate.services.agent_compatibility import environment_compatibility
from simulate.services.hosted_harness import HostedHarnessError
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope, error_details
from tfc.utils.api_serializers import (
    ApiErrorWithDetailsResponseSerializer,
    EmptyRequestSerializer,
    StrictInputSerializer,
)


class EnvironmentQuerySerializer(StrictInputSerializer):
    environment_id = serializers.UUIDField()


class VersionQuerySerializer(StrictInputSerializer):
    version_id = serializers.UUIDField(required=False)


class _AgentVersionView(APIView):
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

    def _agent(self, request, agent_id) -> AgentDefinition:
        agent = AgentDefinition.objects.filter(
            id=agent_id,
            organization=getattr(request, "organization", None)
            or request.user.organization,
            deleted=False,
        ).first()
        if agent is None:
            raise HostedHarnessError(
                "agent_not_found", "Agent definition not found", status_code=404
            )
        return agent

    def _version(self, agent: AgentDefinition, version_id) -> AgentVersion:
        version = AgentVersion.objects.filter(
            id=version_id, agent_definition=agent
        ).first()
        if version is None:
            raise HostedHarnessError(
                "version_not_found", "Agent version not found", status_code=404
            )
        return version


class AgentVersionChangeView(_AgentVersionView):
    """POST /agent-definitions/{id}/versions/change/: a new version by kind."""

    @viewer_denied("create an agent version")
    @validated_request(
        request_serializer=AgentVersionChangeRequestSerializer,
        responses={
            200: AgentVersionChangeResponseSerializer,
            202: AgentVersionChangeResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            409: ApiErrorWithDetailsResponseSerializer,
            422: ApiErrorWithDetailsResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, agent_id, *args, **kwargs):
        agent = self._agent(request, agent_id)
        change = agent_versions.change_version(
            agent, user=request.user, **request.validated_data
        )
        body = {
            "version": change.version,
            "created": change.created,
            "up_to_date": not change.created,
        }
        return Response(
            AgentVersionChangeResponseSerializer(body).data,
            status=status.HTTP_202_ACCEPTED if change.created else status.HTTP_200_OK,
        )


class AgentVersionProductionView(_AgentVersionView):
    """POST /agent-definitions/{id}/versions/{vid}/production/."""

    @viewer_denied("change the production version")
    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={
            200: AgentVersionProductionResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            409: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def post(self, request, agent_id, version_id, *args, **kwargs):
        agent = self._agent(request, agent_id)
        version = self._version(agent, version_id)
        moved = agent_versions.set_production(version)
        version.refresh_from_db()
        body = {
            "version": version,
            "moved_environment_ids": [str(one) for one in moved],
        }
        return Response(AgentVersionProductionResponseSerializer(body).data)


class AgentVersionRetryView(_AgentVersionView):
    """POST /agent-definitions/{id}/versions/{vid}/retry/."""

    @viewer_denied("retry an agent version check")
    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={
            202: AgentVersionActionResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            409: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def post(self, request, agent_id, version_id, *args, **kwargs):
        agent = self._agent(request, agent_id)
        agent_versions.retry_version(self._version(agent, version_id))
        return Response(
            AgentVersionActionResponseSerializer({"message": "Check queued"}).data,
            status=status.HTTP_202_ACCEPTED,
        )


class AgentVersionDiscardView(_AgentVersionView):
    """POST /agent-definitions/{id}/versions/{vid}/discard/."""

    @viewer_denied("discard an agent version")
    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={
            200: AgentVersionActionResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            409: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def post(self, request, agent_id, version_id, *args, **kwargs):
        agent = self._agent(request, agent_id)
        agent_versions.discard_version(self._version(agent, version_id))
        return Response(
            AgentVersionActionResponseSerializer({"message": "Version discarded"}).data
        )


class AgentVersionCompatibilityView(_AgentVersionView):
    """GET /agent-definitions/{id}/versions/{vid}/compatibility/?environment_id=."""

    @validated_request(
        query_serializer=EnvironmentQuerySerializer,
        responses={
            200: AgentVersionCompatibilityResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, version_id, *args, **kwargs):
        agent = self._agent(request, agent_id)
        version = self._version(agent, version_id)
        environment_id = request.validated_query_data["environment_id"]
        environment = agent_versions.environment_of(agent, environment_id)
        body = {"environment_id": str(environment.id), "version_id": str(version.id)}
        body.update(environment_compatibility(environment, version))
        return Response(AgentVersionCompatibilityResponseSerializer(body).data)


class AgentBuildPreflightView(_AgentVersionView):
    """GET /agent-definitions/{id}/build-preflight/?version_id=."""

    @validated_request(
        query_serializer=VersionQuerySerializer,
        responses={
            200: AgentBuildPreflightResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, *args, **kwargs):
        agent = self._agent(request, agent_id)
        version_id = request.validated_query_data.get("version_id")
        version = self._version(agent, version_id) if version_id else None
        return Response(
            AgentBuildPreflightResponseSerializer(
                agent_versions.build_preflight(agent, version)
            ).data
        )
