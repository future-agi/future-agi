from __future__ import annotations

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.serializers.harness_job import (
    HarnessJobActionSerializer,
    HarnessJobAdjustmentSerializer,
    HarnessJobCreateSerializer,
    HarnessPreflightSerializer,
    HarnessSecretFileUploadResponseSerializer,
    HarnessSourceUploadResponseSerializer,
)
from simulate.services.harness_provider import get_harness_provider
from simulate.services.harness_sandbox import (
    HarnessSandboxClient,
    HarnessSandboxRejected,
    HarnessSandboxUnavailable,
)
from simulate.services.harness_credentials import (
    credential_file_ref,
    request_scope,
    store_credential_file,
)
from tfc.utils.api_contracts import validated_request


class HarnessJobViewSet(viewsets.ViewSet):
    """Provider-neutral control plane for hosted ALK harness jobs.

    Validates the v1.6 request contract and delegates execution to the backend
    selected by ``settings.HARNESS_PROVIDER`` (``daytona`` default, or
    ``sandbox``). See ``simulate.services.harness_provider``.
    """

    permission_classes = [IsAuthenticated]

    def _client(self) -> HarnessSandboxClient:
        return HarnessSandboxClient()

    @validated_request(
        request_serializer=HarnessJobCreateSerializer,
        reject_unknown_fields=True,
    )
    def create(self, request):
        return get_harness_provider().create(request)

    def list(self, request):
        return get_harness_provider().list(request)

    @action(
        detail=False,
        methods=["post"],
        url_path="sources",
        parser_classes=[MultiPartParser],
    )
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "files",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=True,
                description="Repeat this field once for every source file.",
            ),
            openapi.Parameter(
                "paths",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                required=True,
                description="Repeat in file order with each repository-relative path.",
            ),
            openapi.Parameter(
                "name",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                required=False,
            ),
        ],
        responses={201: HarnessSourceUploadResponseSerializer},
    )
    def source_upload(self, request):
        return get_harness_provider().source_upload(request)

    @action(
        detail=False,
        methods=["post"],
        url_path="secret-files",
        parser_classes=[MultiPartParser],
    )
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "file",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=True,
                description="Credential file; transferred without entering job JSON.",
            ),
            openapi.Parameter(
                "environment_name",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                required=True,
                description="Environment variable that will point to the mounted file.",
            ),
        ],
        responses={201: HarnessSecretFileUploadResponseSerializer},
    )
    def secret_file_upload(self, request):
        uploaded = request.FILES.get("file")
        environment_name = str(request.data.get("environment_name") or "").strip()
        if uploaded is None or not environment_name:
            return Response(
                {"detail": "file and environment_name are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if int(getattr(uploaded, "size", 0) or 0) > 5 * 1024 * 1024:
            return Response(
                {"detail": "credential file may not exceed 5 MiB"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        organization, workspace = request_scope(request)
        if organization is None:
            return Response(
                {"detail": "an organization is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record = store_credential_file(
            uploaded,
            environment_name,
            organization=organization,
            workspace=workspace,
        )
        result = {
            "environment_name": environment_name,
            "secret_ref": credential_file_ref(record),
            "size": record.size,
        }
        return Response(result, status=status.HTTP_201_CREATED)

    @validated_request(
        request_serializer=HarnessPreflightSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"])
    def preflight(self, request):
        return get_harness_provider().preflight(request)

    def retrieve(self, request, pk=None):
        return get_harness_provider().retrieve(request, pk)

    @validated_request(
        request_serializer=HarnessJobActionSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return get_harness_provider().cancel(request, pk)

    @validated_request(
        request_serializer=HarnessJobAdjustmentSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        try:
            return Response(self._client().adjust(str(pk), request.validated_data))
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    @action(detail=False, methods=["get"])
    def health(self, request):
        return Response(get_harness_provider().health())
