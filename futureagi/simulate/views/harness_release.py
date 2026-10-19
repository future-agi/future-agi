"""Environment releases, adaptations and compositions (`environment-releases.md` §7.6).

Organization-scoped like the other harness routes: an organization sees system releases and its
own, never another organization's.
"""

from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.serializers.harness_job import HarnessJobReadSerializer
from simulate.serializers.harness_release import (
    HarnessCompositionCreateResponseSerializer,
    HarnessReleaseAgentRequestSerializer,
    HarnessReleaseListResponseSerializer,
)
from simulate.services.harness_provider import (
    HostedHarnessProvider,
    request_organization,
    request_workspace,
)
from simulate.services.harness_releases import (
    create_composition,
    list_releases,
    organization_agent_version,
    visible_suite,
)
from simulate.services.hosted_harness import HostedHarnessError
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


class HarnessCompositionViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=HarnessReleaseAgentRequestSerializer,
        responses={
            201: HarnessCompositionCreateResponseSerializer,
            404: ApiTextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def create(self, request):
        """Compose a release suite with an agent through a canonical binding, no adaptation."""
        organization = request_organization(request)
        if organization is None:
            return _no_organization()
        data = request.validated_data
        try:
            job = create_composition(
                organization,
                request_workspace(request),
                suite=visible_suite(organization, data["suite_id"]),
                # Re-validated as a hosted job agent; the request serializer only shapes it.
                agent=request.data["agent"],
                agent_version=organization_agent_version(
                    organization, data["agent_version_id"]
                ),
                name=data["name"],
                idempotency_key=data["idempotency_key"],
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        return Response({"id": str(job.id)}, status=status.HTTP_201_CREATED)
