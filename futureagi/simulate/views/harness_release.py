"""Environment releases and adaptations (`environment-releases.md` §7.6).

Organization-scoped like the other harness routes: an organization sees system releases and its
own, never another organization's. An agent is composed with a release only through adaptation.
"""

from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.serializers.harness_job import HarnessJobReadSerializer
from simulate.serializers.harness_release import (
    HarnessReleaseAgentRequestSerializer,
    HarnessReleaseListResponseSerializer,
)
from simulate.services.harness_provider import (
    HostedHarnessProvider,
    request_organization,
)
from simulate.services.harness_releases import list_releases
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer


def _no_organization() -> Response:
    return Response(
        build_error_envelope(
            "Organization not found", status_code=status.HTTP_404_NOT_FOUND
        ),
        status=status.HTTP_404_NOT_FOUND,
    )


class HarnessReleaseViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: HarnessReleaseListResponseSerializer})
    def list(self, request):
        organization = request_organization(request)
        if organization is None:
            return _no_organization()
        return Response({"results": list_releases(organization)})


class HarnessAdaptationViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=HarnessReleaseAgentRequestSerializer,
        responses={202: HarnessJobReadSerializer, 404: ApiTextErrorResponseSerializer},
        reject_unknown_fields=True,
    )
    def create(self, request):
        """Adapt an agent to a release suite. The job is followed like any harness job."""
        return HostedHarnessProvider().create_adaptation(request)
