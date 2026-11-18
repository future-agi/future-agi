import json

import structlog
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication, LangfuseBasicAuthentication
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.payload_storage import PAYLOAD_DEFAULT_TTL, payload_storage
from tracer.utils.parsers import ProtobufParser
from tracer.utils.trace_ingestion import bulk_create_observation_span_task

logger = structlog.get_logger(__name__)

OTLP_HTTP_REQUEST_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    description=(
        "Legacy OTLP JSON/protobuf trace payload. Prefer /tracer/v1/traces "
        "for new integrations."
    ),
)


class OTLPHTTPTraceResponseSerializer(serializers.Serializer):
    pass


class OTLPHTTPErrorResponseSerializer(ApiTextErrorResponseSerializer):
    pass


class OTLPTraceHTTPView(APIView):
    """
    HTTP/JSON endpoint for OTLP traces.

    Supports both FutureAGI native auth (X-Api-Key / X-Secret-Key or JWT)
    and Langfuse SDK Basic auth (Authorization: Basic base64(pk:sk)).
    """

    parser_classes = [ProtobufParser, JSONParser]
    authentication_classes = [LangfuseBasicAuthentication, APIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        request_body=OTLP_HTTP_REQUEST_SCHEMA,
        # This compatibility endpoint accepts OTLP JSON and protobuf payloads.
        # Runtime validation is handled by the configured protocol parsers.
        runtime_request_validation=True,
        responses={
            200: OTLPHTTPTraceResponseSerializer,
            403: OTLPHTTPErrorResponseSerializer,
            500: OTLPHTTPErrorResponseSerializer,
        },
    )
    def post(self, request, *args, **kwargs):
        """
        Asynchronously handles the POST request to create ObservationSpans from OTEL data.
        """
        try:
            user = request.user
            if not hasattr(user, "organization") or not user.organization:
                return self._gm.forbidden_response("User has no organization.")

            organization_id = (
                getattr(request, "organization", None) or user.organization
            ).id
            user_id = user.id
            workspace = getattr(request, "workspace", None)
            workspace_id = str(workspace.id) if workspace else None

            request_json = json.dumps(request.data)
            payload_key = payload_storage.store(request_json, ttl=PAYLOAD_DEFAULT_TTL)

            logger.info(
                "trace_payload_stored_in_redis",
                payload_key=payload_key,
                payload_size=len(request_json),
            )

            bulk_create_observation_span_task.apply_async(
                args=[payload_key, organization_id, user_id, workspace_id, "json"],
                queue="trace_ingestion",
            )

            return Response({}, status=200)

        except Exception as e:
            logger.exception(f"Error in creating observation span (HTTP): {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_CREATION_OBSERVATION_SPAN")
            )
