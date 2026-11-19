from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.models import AgentDefinition, AgentVersion
from simulate.serializers.response.agent_setup import StoredAgentResponseSerializer
from simulate.services.agent_setup import stored_agent_answer
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import ApiErrorWithDetailsResponseSerializer


def _not_found(message):
    return Response(
        build_error_envelope(message, status_code=status.HTTP_404_NOT_FOUND),
        status=status.HTTP_404_NOT_FOUND,
    )


class StoredAgentView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: StoredAgentResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, version_id, *args, **kwargs):
        agent = AgentDefinition.objects.filter(
            id=agent_id,
            organization=getattr(request, "organization", None)
            or request.user.organization,
            deleted=False,
        ).first()
        if agent is None:
            return _not_found("Agent definition not found")
        version = AgentVersion.objects.filter(
            id=version_id, agent_definition=agent
        ).first()
        if version is None:
            return _not_found("Agent version not found")
        answer = stored_agent_answer(version)
        if answer is None:
            return _not_found("Agent not stored for this version")
        return Response(
            StoredAgentResponseSerializer(answer).data, status=status.HTTP_200_OK
        )
