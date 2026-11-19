"""
API Views for Agent Version Diff and Changelog.

GET /simulate/agent-definitions/<agent_id>/versions/diff/?base_version_id=<id>&target_version_id=<id>
    Compare two agent versions side-by-side (prompt diff, parameter diff, performance delta).

GET /simulate/agent-definitions/<agent_id>/versions/<version_id>/diff/<target_version_id>/
    Direct path variant of the diff endpoint.

GET /simulate/agent-definitions/<agent_id>/versions/changelog/
    Get the full chronological release changelog with change summaries across all versions.
"""

from __future__ import annotations

import structlog
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from simulate.models import AgentDefinition, AgentVersion
from simulate.serializers.response.agent_version_diff import (
    AgentChangelogItemSerializer,
    AgentVersionDiffResponseSerializer,
)
from simulate.services.agent_version_diff import (
    compare_agent_versions,
    get_agent_changelog,
)
from tfc.utils.api_serializers import ApiErrorWithDetailsResponseSerializer
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _get_org(request):
    return getattr(request, "organization", None) or request.user.organization


class AgentVersionDiffView(APIView):
    """
    API View to compare two versions of an Agent Definition side-by-side.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        operation_description="Compare two agent versions (system prompt diff, parameter diff, performance deltas).",
        manual_parameters=[
            openapi.Parameter(
                "base_version_id",
                openapi.IN_QUERY,
                description="UUID of the baseline version (e.g. prior version)",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                required=False,
            ),
            openapi.Parameter(
                "target_version_id",
                openapi.IN_QUERY,
                description="UUID of the target version to compare against baseline",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                required=False,
            ),
        ],
        responses={
            200: AgentVersionDiffResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(
        self,
        request,
        agent_id,
        version_id=None,
        target_version_id=None,
        *args,
        **kwargs,
    ):
        try:
            org = _get_org(request)
            try:
                agent = AgentDefinition.objects.get(
                    id=agent_id,
                    organization=org,
                    deleted=False,
                )
            except AgentDefinition.DoesNotExist:
                return self._gm.not_found("Agent definition not found.")

            # Resolve base and target version IDs from path kwargs or query params
            base_id = version_id or request.query_params.get("base_version_id")
            target_id = target_version_id or request.query_params.get("target_version_id")

            if not base_id or not target_id:
                return self._gm.bad_request(
                    "Both 'base_version_id' and 'target_version_id' are required."
                )

            try:
                base_version = AgentVersion.objects.get(
                    id=base_id,
                    agent_definition=agent,
                    organization=org,
                    deleted=False,
                )
            except AgentVersion.DoesNotExist:
                return self._gm.not_found(f"Base agent version '{base_id}' not found.")

            try:
                target_version = AgentVersion.objects.get(
                    id=target_id,
                    agent_definition=agent,
                    organization=org,
                    deleted=False,
                )
            except AgentVersion.DoesNotExist:
                return self._gm.not_found(f"Target agent version '{target_id}' not found.")

            diff_result = compare_agent_versions(base_version, target_version)
            return self._gm.success_response(diff_result)

        except Exception as e:
            logger.exception(
                "agent_version_diff_failed",
                agent_id=str(agent_id),
                error=str(e),
            )
            return self._gm.internal_server_error_response(
                f"Failed to calculate version diff: {str(e)}"
            )


class AgentVersionChangelogView(APIView):
    """
    API View to retrieve the full release changelog timeline for an Agent Definition.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        operation_description="Get the full chronological release changelog with change summaries across all versions.",
        responses={
            200: AgentChangelogItemSerializer(many=True),
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, *args, **kwargs):
        try:
            org = _get_org(request)
            try:
                agent = AgentDefinition.objects.get(
                    id=agent_id,
                    organization=org,
                    deleted=False,
                )
            except AgentDefinition.DoesNotExist:
                return self._gm.not_found("Agent definition not found.")

            changelog = get_agent_changelog(agent)
            return self._gm.success_response(changelog)

        except Exception as e:
            logger.exception(
                "agent_changelog_failed",
                agent_id=str(agent_id),
                error=str(e),
            )
            return self._gm.internal_server_error_response(
                f"Failed to fetch agent changelog: {str(e)}"
            )
