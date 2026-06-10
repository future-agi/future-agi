"""
Standard OTLP Support

Enhanced OTLP/HTTP endpoints following the OpenTelemetry Protocol specification.
https://opentelemetry.io/docs/specs/otlp/

Supports:
- Binary protobuf (application/x-protobuf)
- JSON (application/json)
- gzip compression
- Proper OTLP response format with partial_success
"""

import gzip
import json

import structlog
from django.http import HttpResponse
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from google.protobuf.json_format import MessageToDict, Parse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication, LangfuseBasicAuthentication
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tfc.utils.error_codes import get_error_message
from tfc.utils.payload_storage import PAYLOAD_DEFAULT_TTL, payload_storage
from tracer.utils.trace_ingestion import bulk_create_observation_span_task

logger = structlog.get_logger(__name__)


# OTLP Content Types
CONTENT_TYPE_PROTOBUF = "application/x-protobuf"
CONTENT_TYPE_JSON = "application/json"

# OTLP Headers
HEADER_CONTENT_TYPE = "Content-Type"
HEADER_CONTENT_ENCODING = "Content-Encoding"

OTLP_TRACE_REQUEST_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    description=(
        "OpenTelemetry ExportTraceServiceRequest. JSON payloads use the OTLP "
        "HTTP JSON mapping; protobuf payloads use application/x-protobuf."
    ),
)


class OTLPPartialSuccessSerializer(serializers.Serializer):
    rejected_spans = serializers.IntegerField(required=False)
    error_message = serializers.CharField(required=False, allow_blank=True)


class OTLPTraceResponseSerializer(serializers.Serializer):
    partial_success = OTLPPartialSuccessSerializer(required=False)


class OTLPHealthResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("healthy",))
    service = serializers.CharField()


class OTLPExportResult:
    """Result of OTLP export operation."""

    def __init__(
        self,
        accepted_spans: int = 0,
        rejected_spans: int = 0,
        error_message: str | None = None,
    ):
        self.accepted_spans = accepted_spans
        self.rejected_spans = rejected_spans
        self.error_message = error_message

    @property
    def has_partial_success(self) -> bool:
        return self.rejected_spans > 0 and self.accepted_spans > 0

    @property
    def has_error(self) -> bool:
        return self.error_message is not None


class OTLPTraceView(APIView):
    """
    Standard OTLP/HTTP endpoint for trace export.

    Endpoint: POST /v1/traces

    Supports:
    - Content-Type: application/x-protobuf (binary protobuf)
    - Content-Type: application/json (OTLP JSON format)
    - Content-Encoding: gzip (compressed payloads)

    Returns:
    - ExportTraceServiceResponse in the same format as request
    - partial_success field for rejected spans
    """

    authentication_classes = [LangfuseBasicAuthentication, APIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    # Disable DRF parsers - we handle parsing ourselves for OTLP compliance
    parser_classes = []

    @swagger_auto_schema(
        request_body=OTLP_TRACE_REQUEST_SCHEMA,
        # OTLP accepts JSON, gzip, and binary protobuf. Request validation is
        # implemented by _parse_request instead of DRF serializer binding.
        runtime_request_validation=True,
        responses={
            200: OTLPTraceResponseSerializer,
            400: OTLPTraceResponseSerializer,
            403: OTLPTraceResponseSerializer,
            429: OTLPTraceResponseSerializer,
            500: OTLPTraceResponseSerializer,
        },
    )
    def post(self, request: Request, *args, **kwargs) -> HttpResponse:
        """
        Handle OTLP trace export request.

        The request body contains an ExportTraceServiceRequest with resource spans.
        """
        try:
            # Validate user and organization
            user = request.user
            if not hasattr(user, "organization") or not user.organization:
                return self._error_response(
                    request,
                    "User has no organization",
                    status.HTTP_403_FORBIDDEN,
                )

            organization_id = str(user.organization.id)

            # Ingestion rate limit check
            try:
                try:
                    from ee.usage.services.rate_limiter import RateLimiter
                except ImportError:
                    RateLimiter = None

                if RateLimiter is not None:
                    rl_result = RateLimiter.check(organization_id, "ingestion")
                    if not rl_result.allowed:
                        response = self._error_response(request, rl_result.reason, 429)
                        response["Retry-After"] = str(rl_result.retry_after)
                        return response
            except ImportError:
                pass

            user_id = str(user.id)
            workspace = getattr(request, "workspace", None)
            workspace_id = str(workspace.id) if workspace else None

            # Parse the OTLP request
            otlp_request, error = self._parse_request(request)

            if error:
                return self._error_response(request, error, status.HTTP_400_BAD_REQUEST)

            if not otlp_request:
                return self._error_response(
                    request, "Empty request body", status.HTTP_400_BAD_REQUEST
                )

            # Convert to JSON for processing
            request_dict = MessageToDict(otlp_request, preserving_proto_field_name=True)

            # Count spans for response
            span_count = self._count_spans(request_dict)

            # Store payload in Redis and queue for async processing
            request_json = json.dumps(request_dict)
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

            # Return success response
            result = OTLPExportResult(accepted_spans=span_count)
            return self._success_response(request, result)

        except Exception as e:
            logger.exception(f"Error in OTLP trace export: {str(e)}")
            return self._error_response(
                request,
                get_error_message("FAILED_CREATION_OBSERVATION_SPAN"),
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _parse_request(
        self, request: Request
    ) -> tuple[ExportTraceServiceRequest | None, str | None]:
        """
        Parse the OTLP request based on content type and encoding.

        Returns:
            Tuple of (parsed request, error message)
        """
        try:
            # Get raw body
            body = request.body

            # Handle gzip compression
            content_encoding = request.headers.get(HEADER_CONTENT_ENCODING, "").lower()
            if content_encoding == "gzip":
                try:
                    body = gzip.decompress(body)
                except Exception as e:
                    return None, f"Failed to decompress gzip data: {e}"

            if not body:
                return None, None

            # Parse based on content type
            content_type = (request.content_type or "").lower()

            if CONTENT_TYPE_PROTOBUF in content_type or not content_type:
                # Binary protobuf (default)
                return self._parse_protobuf(body)
            elif CONTENT_TYPE_JSON in content_type:
                # JSON format
                return self._parse_json(body)
            else:
                return None, f"Unsupported content type: {content_type}"

        except Exception as e:
            return None, f"Failed to parse request: {e}"

    def _parse_protobuf(
        self, body: bytes
    ) -> tuple[ExportTraceServiceRequest | None, str | None]:
        """Parse binary protobuf request."""
        try:
            request_proto = ExportTraceServiceRequest()
            request_proto.ParseFromString(body)
            return request_proto, None
        except Exception as e:
            return None, f"Failed to parse protobuf: {e}"

    def _parse_json(
        self, body: bytes
    ) -> tuple[ExportTraceServiceRequest | None, str | None]:
        """Parse JSON request into protobuf."""
        try:
            json_str = body.decode("utf-8")
            request_proto = Parse(json_str, ExportTraceServiceRequest())
            return request_proto, None
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON: {e}"
        except Exception as e:
            return None, f"Failed to parse JSON to protobuf: {e}"

    def _count_spans(self, request_dict: dict) -> int:
        """Count total spans in the request."""
        count = 0
        for resource_spans in request_dict.get("resource_spans", []):
            for scope_spans in resource_spans.get("scope_spans", []):
                count += len(scope_spans.get("spans", []))
        return count

    def _success_response(
        self, request: Request, result: OTLPExportResult
    ) -> HttpResponse:
        """
        Build OTLP success response.

        Returns ExportTraceServiceResponse in the same format as the request.
        """
        response_proto = ExportTraceServiceResponse()

        # Add partial_success if there were rejected spans
        if result.has_partial_success:
            response_proto.partial_success.rejected_spans = result.rejected_spans
            if result.error_message:
                response_proto.partial_success.error_message = result.error_message

        return self._build_response(request, response_proto, status.HTTP_200_OK)

    def _error_response(
        self, request: Request, message: str, status_code: int
    ) -> HttpResponse:
        """
        Build OTLP error response.

        For errors, we return a partial_success with all spans rejected.
        """
        response_proto = ExportTraceServiceResponse()
        response_proto.partial_success.error_message = message

        return self._build_response(request, response_proto, status_code)

    def _build_response(
        self,
        request: Request,
        response_proto: ExportTraceServiceResponse,
        status_code: int,
    ) -> HttpResponse:
        """
        Build HTTP response in the appropriate format.

        Returns protobuf or JSON based on request Accept header or Content-Type.
        """
        # Determine response format from Accept header or Content-Type
        accept = request.headers.get("Accept", "")
        content_type = request.content_type or ""

        if CONTENT_TYPE_JSON in accept or CONTENT_TYPE_JSON in content_type:
            # JSON response
            response_dict = MessageToDict(
                response_proto, preserving_proto_field_name=True
            )
            response_body = json.dumps(response_dict).encode("utf-8")
            response_content_type = CONTENT_TYPE_JSON
        else:
            # Protobuf response (default)
            response_body = response_proto.SerializeToString()
            response_content_type = CONTENT_TYPE_PROTOBUF

        response = HttpResponse(
            response_body,
            status=status_code,
            content_type=response_content_type,
        )

        return response


class OTLPHealthView(APIView):
    """
    Health check endpoint for OTLP collectors.

    Returns 200 OK if the service is ready to accept traces.
    No authentication required for health checks.
    """

    permission_classes = []
    authentication_classes = []

    @swagger_auto_schema(
        responses={
            200: OTLPHealthResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def get(self, request: Request, *args, **kwargs) -> HttpResponse:
        """Health check - always returns OK."""
        return HttpResponse(
            json.dumps({"status": "healthy", "service": "otlp-trace-receiver"}),
            status=status.HTTP_200_OK,
            content_type=CONTENT_TYPE_JSON,
        )
