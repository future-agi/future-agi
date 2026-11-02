"""The code of an agent version: its file tree, one file, and the files that
changed against another version, all read from the version's stored archive."""

from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers
from rest_framework.response import Response

from simulate.serializers.response.agent_version import (
    AgentCodeChangesResponseSerializer,
    AgentCodeFileResponseSerializer,
    AgentCodeTreeResponseSerializer,
)
from simulate.services import agent_code
from simulate.views.agent_versions import _AgentVersionView
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiErrorWithDetailsResponseSerializer,
    StrictInputSerializer,
)


class PathQuerySerializer(StrictInputSerializer):
    path = serializers.CharField(max_length=4096, allow_blank=True)


class AgainstQuerySerializer(StrictInputSerializer):
    against = serializers.UUIDField(required=False)


class AgentCodeTreeView(_AgentVersionView):
    """GET /agent-definitions/{id}/versions/{vid}/code/tree/."""

    @swagger_auto_schema(
        responses={
            200: AgentCodeTreeResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            503: ApiErrorWithDetailsResponseSerializer,
        }
    )
    def get(self, request, agent_id, version_id, *args, **kwargs):
        agent = self._agent(request, agent_id)
        version = self._version(agent, version_id)
        return Response(AgentCodeTreeResponseSerializer(agent_code.tree(version)).data)


class AgentCodeFileView(_AgentVersionView):
    """GET /agent-definitions/{id}/versions/{vid}/code/file/?path=."""

    @validated_request(
        query_serializer=PathQuerySerializer,
        responses={
            200: AgentCodeFileResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            503: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, version_id, *args, **kwargs):
        agent = self._agent(request, agent_id)
        version = self._version(agent, version_id)
        body = agent_code.read_file(version, request.validated_query_data["path"])
        return Response(AgentCodeFileResponseSerializer(body).data)


class AgentCodeChangesView(_AgentVersionView):
    """GET /agent-definitions/{id}/versions/{vid}/code/changes/?against=."""

    @validated_request(
        query_serializer=AgainstQuerySerializer,
        responses={
            200: AgentCodeChangesResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            503: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, version_id, *args, **kwargs):
        agent = self._agent(request, agent_id)
        version = self._version(agent, version_id)
        against_id = request.validated_query_data.get("against")
        against = (
            self._version(agent, against_id)
            if against_id
            else agent_code.previous_version(version)
        )
        return Response(
            AgentCodeChangesResponseSerializer(
                agent_code.changes(version, against)
            ).data
        )
