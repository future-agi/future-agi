from __future__ import annotations

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.serializers.harness_job import (
    HarnessJobActionSerializer,
    HarnessJobCreateSerializer,
    HarnessPreflightSerializer,
    HarnessSourceUploadResponseSerializer,
)
from simulate.services.harness_provider import get_harness_provider
from tfc.utils.api_contracts import validated_request


class HarnessJobViewSet(viewsets.ViewSet):
    """Provider-neutral control plane for hosted ALK harness jobs.

    Validates the v1.6 request contract and delegates execution to the backend
    selected by ``settings.HARNESS_PROVIDER`` (``daytona`` default, or
    ``sandbox``). See ``simulate.services.harness_provider``.
    """

    permission_classes = [IsAuthenticated]

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

    @action(detail=False, methods=["get"])
    def health(self, request):
        return Response(get_harness_provider().health())
