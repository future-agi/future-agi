from __future__ import annotations

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.authentication import HarnessAttemptAuthentication
from simulate.serializers.hosted_harness import (
    HarnessAcceptedResponseSerializer,
    HarnessArtifactUploadResponseSerializer,
    HarnessEventBatchResponseSerializer,
    HarnessEventBatchSerializer,
    HarnessManifestSerializer,
    HarnessResultReceiptSerializer,
    HarnessScenarioOperationResponseSerializer,
    HarnessScenarioOperationSerializer,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    begin_scenarios,
    provision_scenarios,
)
from simulate.services.hosted_harness_ingestion import (
    ingest_artifact,
    ingest_event_batch,
    ingest_manifest,
    ingest_result_receipt,
)
from tfc.utils.api_contracts import validated_request


class HostedHarnessAttemptViewSet(viewsets.ViewSet):
    authentication_classes = [HarnessAttemptAuthentication]
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, HostedHarnessError):
            return Response(exc.as_dict(), status=exc.status_code)
        if isinstance(exc, ValidationError):
            return Response(
                {
                    "error": "validation_error",
                    "message": str(exc.detail),
                    "retryable": False,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().handle_exception(exc)

    @property
    def _attempt(self):
        return self.request.auth

    @validated_request(
        request_serializer=HarnessEventBatchSerializer,
        responses={200: HarnessEventBatchResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def events(self, request, pk=None):
        result = ingest_event_batch(self._attempt, request.validated_data["events"])
        return Response(result)

    @validated_request(
        request_serializer=HarnessResultReceiptSerializer,
        responses={200: HarnessAcceptedResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def results(self, request, pk=None):
        _, created = ingest_result_receipt(self._attempt, request.validated_data)
        return Response({"accepted": True, "duplicate": not created})

    @validated_request(
        request_serializer=HarnessScenarioOperationSerializer,
        responses={200: HarnessScenarioOperationResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def scenarios(self, request, pk=None):
        payload = request.validated_data
        if payload["operation"] == "provision":
            result = provision_scenarios(self._attempt, payload)
        else:
            result = begin_scenarios(self._attempt, payload)
        return Response(result)

    @validated_request(
        request_serializer=HarnessManifestSerializer,
        responses={200: HarnessAcceptedResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path=r"artifacts/manifest")
    def artifact_manifest(self, request, pk=None):
        _, created = ingest_manifest(self._attempt, request.validated_data)
        return Response({"accepted": True, "duplicate": not created})

    @swagger_auto_schema(
        method="put",
        request_body=openapi.Schema(type=openapi.TYPE_STRING, format="binary"),
        # Raw binary body, so size and digest are checked in the handler rather
        # than by serializer binding.
        runtime_request_validation=True,
        responses={
            200: HarnessArtifactUploadResponseSerializer,
            201: HarnessArtifactUploadResponseSerializer,
        },
    )
    @action(
        detail=True,
        methods=["put"],
        url_path=r"artifacts/(?P<artifact_digest>[0-9a-f]{64})",
    )
    def artifact_upload(self, request, pk=None, artifact_digest=None):
        try:
            size = int(request.headers.get("X-Artifact-Size", ""))
        except ValueError as exc:
            raise HostedHarnessError(
                "size_invalid",
                "X-Artifact-Size must be a non-negative integer",
                status_code=400,
            ) from exc
        kind = request.headers.get("X-Artifact-Kind", "")
        content_type = request.content_type or "application/octet-stream"
        scenario_key = request.headers.get("X-Scenario-Key") or None
        _, created = ingest_artifact(
            self._attempt,
            digest=str(artifact_digest),
            kind=kind,
            size=size,
            content_type=content_type,
            scenario_key=scenario_key,
            stream=request.stream,
        )
        return Response(
            {"artifact_id": f"sha256:{artifact_digest}", "duplicate": not created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
