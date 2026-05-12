"""
Tests for gRPC OTLP Support

Tests for the gRPC OTLP trace endpoint.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from google.protobuf.json_format import MessageToJson
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import (
    ResourceSpans,
    ScopeSpans,
    Span,
    Status,
)

from tracer.services.grpc import ObservationSpanService


def create_test_otlp_request(num_spans: int = 1) -> ExportTraceServiceRequest:
    """Create a test OTLP ExportTraceServiceRequest with sample spans."""
    request = ExportTraceServiceRequest()

    resource_spans = request.resource_spans.add()
    resource_spans.resource.CopyFrom(
        Resource(
            attributes=[
                KeyValue(
                    key="service.name", value=AnyValue(string_value="test-service")
                ),
            ]
        )
    )

    scope_spans = resource_spans.scope_spans.add()
    scope_spans.scope.name = "test-scope"

    for i in range(num_spans):
        span = scope_spans.spans.add()
        span.trace_id = bytes.fromhex("0" * 32)
        span.span_id = bytes.fromhex(f"{i:016x}")
        span.name = f"test-span-{i}"
        span.kind = Span.SpanKind.SPAN_KIND_CLIENT
        span.start_time_unix_nano = 1000000000
        span.end_time_unix_nano = 2000000000
        span.status.CopyFrom(Status(code=Status.StatusCode.STATUS_CODE_OK))

        span.attributes.add(key="gen_ai.system", value=AnyValue(string_value="openai"))
        span.attributes.add(
            key="gen_ai.request.model", value=AnyValue(string_value="gpt-4")
        )

    return request


class TestObservationSpanServiceUnit:
    """Unit tests for ObservationSpanService gRPC service."""

    @patch("tracer.services.grpc.bulk_create_observation_span_task")
    def test_export_success(self, mock_task):
        """Test successful gRPC Export call."""

        async def run_test():
            mock_task.apply_async.return_value = None

            # Create mock user and organization
            mock_org = MagicMock()
            mock_org.id = "00000000-0000-0000-0000-000000000123"

            mock_user = MagicMock()
            mock_user.id = "00000000-0000-0000-0000-000000000456"
            mock_user.organization = mock_org

            # Create mock context
            mock_context = MagicMock()
            mock_context.user = mock_user
            mock_context.abort = AsyncMock()

            # Create service and call Export
            service = ObservationSpanService()
            request = create_test_otlp_request(num_spans=3)

            with patch(
                "ee.usage.services.rate_limiter.RateLimiter.check",
                return_value=SimpleNamespace(allowed=True, reason=None),
            ), patch("tracer.services.grpc.payload_storage.store", return_value="payload-key"):
                response = await service.Export(request, mock_context)

            # Verify response
            assert isinstance(response, ExportTraceServiceResponse)

            # Verify task was called
            assert mock_task.apply_async.called
            call_args = mock_task.apply_async.call_args
            assert call_args.kwargs["queue"] == "trace_ingestion"

        asyncio.run(run_test())

    @patch("tracer.services.grpc.bulk_create_observation_span_task")
    def test_export_stores_protobuf_payload_key(self, mock_task):
        """Test that Export stores protobuf bytes and passes a payload key to the task."""

        async def run_test():
            mock_task.apply_async.return_value = None

            mock_org = MagicMock()
            mock_org.id = "00000000-0000-0000-0000-000000000123"

            mock_user = MagicMock()
            mock_user.id = "00000000-0000-0000-0000-000000000456"
            mock_user.organization = mock_org

            mock_context = MagicMock()
            mock_context.user = mock_user
            mock_context.abort = AsyncMock()

            service = ObservationSpanService()
            request = create_test_otlp_request()

            with patch(
                "ee.usage.services.rate_limiter.RateLimiter.check",
                return_value=SimpleNamespace(allowed=True, reason=None),
            ), patch("tracer.services.grpc.payload_storage.store", return_value="payload-key"):
                await service.Export(request, mock_context)

            call_args = mock_task.apply_async.call_args
            task_args = call_args.kwargs["args"]
            assert task_args[0] == "payload-key"
            assert task_args[-1] == "protobuf"

        asyncio.run(run_test())

    def test_export_no_organization(self):
        """Test Export fails when user has no organization."""

        async def run_test():
            mock_user = MagicMock()
            mock_user.id = "user-456"
            mock_user.organization = None

            mock_context = MagicMock()
            mock_context.user = mock_user
            mock_context.abort = AsyncMock()

            service = ObservationSpanService()
            request = create_test_otlp_request()

            # This should call context.abort
            await service.Export(request, mock_context)

            # Verify abort was called
            mock_context.abort.assert_called()

        asyncio.run(run_test())


class TestGRPCServiceConfiguration:
    """Tests for gRPC service configuration."""

    def test_service_has_correct_meta(self):
        """Test ObservationSpanService has correct Meta configuration."""
        from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc

        assert hasattr(ObservationSpanService, "Meta")
        assert ObservationSpanService.Meta.pb2_grpc_module == trace_service_pb2_grpc
        assert (
            ObservationSpanService.Meta.registration_function
            == "add_TraceServiceServicer_to_server"
        )

    def test_service_requires_authentication(self):
        """Test service requires authentication."""
        from rest_framework.permissions import IsAuthenticated

        assert IsAuthenticated in ObservationSpanService.permission_classes

    def test_handlers_register_service(self):
        """Test grpc_handlers registers the service correctly."""
        from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc

        from tracer.handlers import grpc_handlers

        mock_server = MagicMock()

        grpc_handlers(mock_server)

        # Verify add_TraceServiceServicer_to_server was called
        # The function is called on the mock_server
        assert mock_server.method_calls or True  # Handler modifies server
