from uuid import UUID

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.models import AgentDefinition
from simulate.serializers.skills import (
    AgentSkillFilesSerializer,
    AgentSkillsQuerySerializer,
    AgentSkillsSerializer,
)
from simulate.services import skills as skill_service
from simulate.services.harness_provider import request_organization, request_workspace
from simulate.services.hosted_harness import HostedHarnessError
from tfc.utils.api_contracts import validated_request


def _uuid_or_none(value) -> UUID | None:
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


class AgentSkillViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, HostedHarnessError):
            return Response(exc.as_dict(), status=exc.status_code)
        return super().handle_exception(exc)

    def _agent(self, request) -> AgentDefinition:
        organization = request_organization(request)
        if organization is None:
            raise HostedHarnessError(
                "organization_required", "an organization is required"
            )
        return skill_service.scoped_agent(
            organization,
            request_workspace(request),
            request.validated_query_data["agent_definition_id"],
        )

    @validated_request(
        query_serializer=AgentSkillsQuerySerializer,
        responses={200: AgentSkillsSerializer},
    )
    def list(self, request):
        skill_service.require_skills()
        agent = self._agent(request)
        version = skill_service.agent_version_of(
            agent, request.validated_query_data.get("agent_version_id")
        )
        return Response(
            AgentSkillsSerializer(
                skill_service.agent_version_skills(agent, version)
            ).data
        )

    @validated_request(
        query_serializer=AgentSkillsQuerySerializer,
        responses={200: AgentSkillFilesSerializer},
    )
    @action(detail=True, methods=["get"], url_path="files")
    def files(self, request, pk=None):
        skill_service.require_skills()
        agent = self._agent(request)
        version = skill_service.agent_version_of(
            agent, request.validated_query_data.get("agent_version_id")
        )
        identifier = _uuid_or_none(pk)
        if version is None or identifier is None:
            raise HostedHarnessError(
                "agent_skill_not_found",
                "this skill is not in this agent version",
                status_code=404,
            )
        return Response({"files": skill_service.skill_files(version, identifier)})
