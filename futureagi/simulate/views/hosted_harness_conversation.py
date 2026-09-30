from __future__ import annotations

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.authentication import HarnessConversationAuthentication
from simulate.serializers.hosted_harness_conversation import (
    HarnessConversationAdjustmentResponseSerializer,
    HarnessConversationAdjustmentSerializer,
    HarnessConversationCommandQuerySerializer,
    HarnessConversationEventAckSerializer,
    HarnessConversationEventBatchSerializer,
    HarnessConversationRerunSerializer,
    HarnessConversationRunStatusSerializer,
    HarnessConversationTranscriptAppendResponseSerializer,
    HarnessConversationTranscriptAppendSerializer,
    HarnessConversationTranscriptQuerySerializer,
    HarnessConversationTranscriptReadSerializer,
    HarnessConversationWorkspaceResponseSerializer,
)
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_conversation import (
    append_provider_transcript,
    conversation_run_status,
    ingest_conversation_events,
    load_provider_transcript,
    pending_commands,
    prepare_conversation_rerun,
    store_workspace_archive,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope


class HostedHarnessConversationViewSet(viewsets.ViewSet):
    authentication_classes = [HarnessConversationAuthentication]
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, HostedHarnessError):
            return Response(exc.as_dict(), status=exc.status_code)
        if isinstance(exc, ValidationError):
            return Response(
                build_error_envelope(
                    exc.detail,
                    code="validation_error",
                    extra={"error": "validation_error", "retryable": False},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().handle_exception(exc)

    @property
    def _conversation(self):
        return self.request.auth

    @validated_request(query_serializer=HarnessConversationCommandQuerySerializer)
    @action(detail=True, methods=["get"])
    @swagger_auto_schema(responses={200: openapi.Schema(type=openapi.TYPE_OBJECT)})
    def commands(self, request, pk=None):
        return Response(
            pending_commands(
                self._conversation,
                after=request.validated_query_data["after"],
            )
        )

    @validated_request(
        request_serializer=HarnessConversationEventBatchSerializer,
        responses={200: HarnessConversationEventAckSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def events(self, request, pk=None):
        for event in request.validated_data["events"]:
            if str(event["conversation_id"]) != str(self._conversation.id):
                raise HostedHarnessError(
                    "conversation_mismatch",
                    "event conversation_id does not match its lease",
                    status_code=403,
                )
        result = ingest_conversation_events(
            self._conversation,
            acknowledged_through=request.validated_data["acknowledged_through"],
            events=list(request.data["events"]),
        )
        return Response(result)

    @action(detail=True, methods=["get"], url_path="run-status")
    @swagger_auto_schema(responses={200: HarnessConversationRunStatusSerializer})
    def run_status(self, request, pk=None):
        return Response(conversation_run_status(self._conversation))

    @validated_request(
        request_serializer=HarnessConversationAdjustmentSerializer,
        responses={200: HarnessConversationAdjustmentResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        from simulate.services.hosted_harness_gateway import HostedHarnessGateway

        body = request.validated_data
        job = HostedHarnessGateway().adjust(self._conversation.job, body)
        adjustments = ((job.payload or {}).get("metadata") or {}).get(
            "adjustments"
        ) or []
        client_request_id = body.get("client_request_id")
        adjustment = next(
            (
                item
                for item in reversed(adjustments)
                if not client_request_id
                or item.get("client_request_id") == client_request_id
            ),
            None,
        )
        return Response(adjustment or {})

    @validated_request(
        request_serializer=HarnessConversationRerunSerializer,
        responses={202: HarnessConversationRunStatusSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    @swagger_auto_schema(responses={202: HarnessConversationRunStatusSerializer})
    def rerun(self, request, pk=None):
        from simulate.services.harness_provider import get_harness_provider

        job = prepare_conversation_rerun(self._conversation)
        try:
            get_harness_provider().rerun_saved(
                str(job.id),
                organization=job.organization,
                workspace=job.workspace,
                environment_values={},
            )
        except HostedHarnessError:
            raise
        except Exception as exc:
            raise HostedHarnessError(
                "conversation_rerun_failed",
                "The saved harness run could not be scheduled",
                status_code=503,
                retryable=True,
            ) from exc
        return Response(
            conversation_run_status(self._conversation),
            status=status.HTTP_202_ACCEPTED,
        )

    @validated_request(query_serializer=HarnessConversationTranscriptQuerySerializer)
    @action(detail=True, methods=["get"], url_path="session-store")
    @swagger_auto_schema(responses={200: HarnessConversationTranscriptReadSerializer})
    def session_store(self, request, pk=None):
        query = request.validated_query_data
        return Response(
            load_provider_transcript(
                self._conversation,
                project_key=query["project_key"],
                provider_session_id=query["session_id"],
                subpath=query["subpath"],
            )
        )

    @validated_request(
        request_serializer=HarnessConversationTranscriptAppendSerializer,
        responses={200: HarnessConversationTranscriptAppendResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="session-store/append")
    def append_session_store(self, request, pk=None):
        body = request.validated_data
        return Response(
            append_provider_transcript(
                self._conversation,
                project_key=body["project_key"],
                provider_session_id=body["session_id"],
                subpath=body["subpath"],
                entries=list(body["entries"]),
            )
        )

    @action(detail=True, methods=["put"], parser_classes=[])
    @swagger_auto_schema(
        request_body=openapi.Schema(type=openapi.TYPE_STRING, format="binary"),
        runtime_request_validation=True,
        responses={200: HarnessConversationWorkspaceResponseSerializer},
    )
    def workspace(self, request, pk=None):
        digest = str(request.headers.get("X-Workspace-Digest") or "")
        try:
            size = int(request.headers.get("X-Workspace-Size") or "")
        except ValueError as exc:
            raise HostedHarnessError(
                "workspace_size_invalid",
                "X-Workspace-Size must be an integer",
                status_code=400,
            ) from exc
        result = store_workspace_archive(
            self._conversation,
            digest=digest,
            size=size,
            body=request.body,
        )
        return Response(result)
