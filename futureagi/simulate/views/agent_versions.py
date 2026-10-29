"""Versions of a connected agent from the Agents page: one change endpoint by kind,
production, retry and discard."""

from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.models import AgentDefinition, AgentVersion
from simulate.serializers.requests.agent_version import (
    AgentVersionChangeRequestSerializer,
)
from simulate.serializers.response.agent_version import (
    AgentVersionActionResponseSerializer,
    AgentVersionChangeResponseSerializer,
    AgentVersionProductionResponseSerializer,
)
from simulate.services import agent_versions
from simulate.services.hosted_harness import HostedHarnessError
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiErrorWithDetailsResponseSerializer,
    EmptyRequestSerializer,
)


class _AgentVersionView(APIView):
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, HostedHarnessError):
            return Response(exc.as_dict(), status=exc.status_code)
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

    @swagger_auto_schema(
        responses={
            202: AgentVersionActionResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            409: ApiErrorWithDetailsResponseSerializer,
        }
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

    @swagger_auto_schema(
        responses={
            200: AgentVersionActionResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            409: ApiErrorWithDetailsResponseSerializer,
        }
    )
    def post(self, request, agent_id, version_id, *args, **kwargs):
        agent = self._agent(request, agent_id)
        agent_versions.discard_version(self._version(agent, version_id))
        return Response(
            AgentVersionActionResponseSerializer({"message": "Version discarded"}).data
        )
