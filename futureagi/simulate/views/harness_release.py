"""Environment releases, adaptations and our hosted agents (`environment-releases.md` §6.4, §7.6).

Organization-scoped like the other harness routes: an organization sees system releases and its
own, never another organization's. A customer's agent is composed with a release only through
adaptation; our own hosted agent through the binding the platform builds for it.
"""

from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.serializers.harness_job import HarnessJobReadSerializer
from simulate.serializers.harness_release import (
    HarnessHostedAgentRequestSerializer,
    HarnessHostedAgentResponseSerializer,
    HarnessReleaseAgentRequestSerializer,
    HarnessReleaseListResponseSerializer,
)
from simulate.services.harness_provider import (
    HostedHarnessProvider,
    request_organization,
    request_workspace,
)
from simulate.services.harness_releases import (
    create_hosted_agent_composition,
    list_releases,
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


class HarnessHostedAgentViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=HarnessHostedAgentRequestSerializer,
        responses={
            201: HarnessHostedAgentResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            409: ApiTextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def create(self, request):
        """Compose our own agent with a release suite; it answers the composed environment."""
        organization = request_organization(request)
        if organization is None:
            return _no_organization()
        data = request.validated_data
        try:
            job = create_hosted_agent_composition(
                organization,
                request_workspace(request),
                suite=visible_suite(organization, data["suite_id"]),
                name=data["name"],
                instructions=data["instructions"],
                model=data["model"],
                idempotency_key=data["idempotency_key"],
                parallelism=data.get("parallelism"),
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        return Response(
            {"id": str(job.id), "agent_version_id": str(job.run_test.agent_version_id)},
            status=status.HTTP_201_CREATED,
        )
