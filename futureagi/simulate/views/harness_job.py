from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.serializers.harness_job import (
    HarnessJobActionSerializer,
    HarnessJobCreateSerializer,
)
from simulate.services.harness_sandbox import (
    HarnessSandboxClient,
    HarnessSandboxUnavailable,
)
from tfc.utils.api_contracts import validated_request


class HarnessJobViewSet(viewsets.ViewSet):
    """Control-plane facade over the configured ALK sandbox provider."""

    permission_classes = [IsAuthenticated]

    def _client(self) -> HarnessSandboxClient:
        return HarnessSandboxClient()

    @validated_request(
        request_serializer=HarnessJobCreateSerializer,
        reject_unknown_fields=True,
    )
    def create(self, request):
        try:
            result = self._client().submit(request.validated_data)
        except HarnessSandboxUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(result, status=status.HTTP_202_ACCEPTED)

    def list(self, request):
        try:
            return Response(self._client().list_jobs())
        except HarnessSandboxUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    def retrieve(self, request, pk=None):
        try:
            return Response(self._client().get(str(pk)))
        except HarnessSandboxUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    @validated_request(
        request_serializer=HarnessJobActionSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        try:
            return Response(self._client().cancel(str(pk)))
        except HarnessSandboxUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    @action(detail=False, methods=["get"])
    def health(self, request):
        try:
            return Response(self._client().health())
        except HarnessSandboxUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
