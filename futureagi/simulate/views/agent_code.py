"""The code of an agent version: its file tree, one file, and the files that
changed against another version, all read from the version's stored archive."""

from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers
from rest_framework.response import Response

from simulate.serializers.response.agent_version import (
    AgentCodeChangesResponseSerializer,
    AgentCodeCompareResponseSerializer,
    AgentCodeFileResponseSerializer,
    AgentCodePatchesResponseSerializer,
    AgentCodeTreeResponseSerializer,
)
from simulate.services import agent_code, agent_code_compare
from simulate.services.hosted_harness import HostedHarnessError
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


class PatchesQuerySerializer(StrictInputSerializer):
    against = serializers.UUIDField(required=False)
    path = serializers.ListField(
        child=serializers.CharField(max_length=4096),
        min_length=1,
        max_length=agent_code_compare.MAX_PATCH_BATCH,
    )


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


class _AgentCodeCompareBase(_AgentVersionView):
    def _pair(self, request, agent_id, version_id):
        agent = self._agent(request, agent_id)
        version = self._version(agent, version_id)
        against_id = request.validated_query_data.get("against")
        against = (
            self._version(agent, against_id)
            if against_id
            else agent_code.previous_version(version)
        )
        if against is not None and (against.version_number or 0) > (
            version.version_number or 0
        ):
            version, against = against, version
        return version, against


class AgentCodeCompareView(_AgentCodeCompareBase):
    """GET /agent-definitions/{id}/versions/{vid}/code/compare/?against=."""

    @validated_request(
        query_serializer=AgainstQuerySerializer,
        responses={
            200: AgentCodeCompareResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            422: ApiErrorWithDetailsResponseSerializer,
            503: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, version_id, *args, **kwargs):
        version, against = self._pair(request, agent_id, version_id)
        return Response(
            AgentCodeCompareResponseSerializer(
                {
                    "version": str(version.id),
                    **agent_code_compare.compare(version, against),
                }
            ).data
        )


class AgentCodePatchesView(_AgentCodeCompareBase):
    """GET /agent-definitions/{id}/versions/{vid}/code/compare/patches/?against=&path=."""

    @validated_request(
        query_serializer=PatchesQuerySerializer,
        responses={
            200: AgentCodePatchesResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            422: ApiErrorWithDetailsResponseSerializer,
            503: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, version_id, *args, **kwargs):
        version, against = self._pair(request, agent_id, version_id)
        if against is None:
            raise HostedHarnessError(
                "no_previous_version",
                "This version has no version to compare with",
                status_code=404,
            )
        return Response(
            AgentCodePatchesResponseSerializer(
                {
                    "version": str(version.id),
                    **agent_code_compare.patches(
                        version, against, request.validated_query_data["path"]
                    ),
                }
            ).data
        )
