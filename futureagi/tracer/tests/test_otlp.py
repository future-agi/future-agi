"""
Tests for Standard OTLP Support

Tests for the enhanced OTLP/HTTP endpoints following OpenTelemetry Protocol specification.
"""

import gzip
import json
import unittest
from unittest.mock import patch

import pytest
from google.protobuf.json_format import MessageToDict
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
from rest_framework import status

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)


@pytest.fixture(autouse=True)
def mock_payload_storage_store():
    with patch("tracer.views.otlp.payload_storage.store", return_value="payload-key"):
        yield


def create_test_otlp_request(num_spans: int = 1) -> ExportTraceServiceRequest:
    """Create a test OTLP ExportTraceServiceRequest with sample spans."""
    request = ExportTraceServiceRequest()

    resource_spans = request.resource_spans.add()

    # Add resource with service name
    resource_spans.resource.CopyFrom(
        Resource(
            attributes=[
                KeyValue(
                    key="service.name", value=AnyValue(string_value="test-service")
                ),
                KeyValue(key="service.version", value=AnyValue(string_value="1.0.0")),
            ]
        )
    )

    scope_spans = resource_spans.scope_spans.add()
    scope_spans.scope.name = "test-scope"
    scope_spans.scope.version = "1.0.0"

    for i in range(num_spans):
        span = scope_spans.spans.add()
        span.trace_id = bytes.fromhex("0" * 32)
        span.span_id = bytes.fromhex(f"{i:016x}")
        span.name = f"test-span-{i}"
        span.kind = Span.SpanKind.SPAN_KIND_CLIENT
        span.start_time_unix_nano = 1000000000
        span.end_time_unix_nano = 2000000000
        span.status.CopyFrom(Status(code=Status.StatusCode.STATUS_CODE_OK))

        # Add some attributes
        span.attributes.add(key="llm.model_name", value=AnyValue(string_value="gpt-4"))
        span.attributes.add(key="llm.token_count.prompt", value=AnyValue(int_value=100))

    return request


# TODO: Re-enable after PII scrubbing is ported to fi-collector (tracer/urls.py routes re-activated at that point)
pytestmark_trace_routes_skip = pytest.mark.skip(
    reason="OTLP trace routes migrated to fi-collector — pending PII scrubbing port"
)


@pytest.mark.integration
@pytest.mark.api
class TestOTLPHealthEndpoint:
    """Tests for GET /tracer/v1/health endpoint."""

    def test_health_check_success(self, api_client):
        """Health check returns OK without authentication."""
        response = api_client.get("/tracer/v1/health")
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "otlp-trace-receiver"

    def test_health_check_content_type(self, api_client):
        """Health check returns JSON content type."""
        response = api_client.get("/tracer/v1/health")
        assert response.status_code == status.HTTP_200_OK
        assert "application/json" in response["Content-Type"]


@pytestmark_trace_routes_skip
@pytest.mark.integration
@pytest.mark.api
class TestOTLPTraceEndpointAuth:
    """Tests for authentication on OTLP trace endpoint."""

    def test_post_traces_unauthenticated(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/v1/traces",
            data=b"",
            content_type="application/x-protobuf",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_post_traces_with_trailing_slash_unauthenticated(self, api_client):
        """Trailing slash endpoint also requires auth."""
        response = api_client.post(
            "/tracer/v1/traces/",
            data=b"",
            content_type="application/x-protobuf",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES


@pytestmark_trace_routes_skip
@pytest.mark.integration
@pytest.mark.api
class TestOTLPTraceEndpointProtobuf:
    """Tests for POST /tracer/v1/traces with protobuf content."""

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_post_traces_protobuf_success(self, mock_task, auth_client):
        """Successfully process protobuf trace export."""
        mock_task.apply_async.return_value = None

        request = create_test_otlp_request(num_spans=3)
        body = request.SerializeToString()

        response = auth_client.post(
            "/tracer/v1/traces",
            data=body,
            content_type="application/x-protobuf",
        )

        assert response.status_code == status.HTTP_200_OK
        assert mock_task.apply_async.called

        # Verify response is protobuf
        assert response["Content-Type"] == "application/x-protobuf"

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_post_traces_empty_body(self, mock_task, auth_client):
        """Empty request body returns error."""
        response = auth_client.post(
            "/tracer/v1/traces",
            data=b"",
            content_type="application/x-protobuf",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not mock_task.apply_async.called

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_post_traces_invalid_protobuf(self, mock_task, auth_client):
        """Invalid protobuf returns error."""
        response = auth_client.post(
            "/tracer/v1/traces",
            data=b"invalid protobuf data",
            content_type="application/x-protobuf",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not mock_task.apply_async.called


@pytestmark_trace_routes_skip
@pytest.mark.integration
@pytest.mark.api
class TestOTLPTraceEndpointJSON:
    """Tests for POST /tracer/v1/traces with JSON content."""

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_post_traces_json_success(self, mock_task, auth_client):
        """Successfully process JSON trace export."""
        mock_task.apply_async.return_value = None

        request = create_test_otlp_request(num_spans=2)
        request_dict = MessageToDict(request, preserving_proto_field_name=True)
        body = json.dumps(request_dict).encode("utf-8")

        response = auth_client.post(
            "/tracer/v1/traces",
            data=body,
            content_type="application/json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert mock_task.apply_async.called

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_post_traces_json_returns_json(self, mock_task, auth_client):
        """JSON request returns JSON response."""
        mock_task.apply_async.return_value = None

        request = create_test_otlp_request()
        request_dict = MessageToDict(request, preserving_proto_field_name=True)
        body = json.dumps(request_dict).encode("utf-8")

        response = auth_client.post(
            "/tracer/v1/traces",
            data=body,
            content_type="application/json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "application/json" in response["Content-Type"]

        # Response should be valid JSON
        response_data = response.json()
        assert isinstance(response_data, dict)

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_post_traces_invalid_json(self, mock_task, auth_client):
        """Invalid JSON returns error."""
        response = auth_client.post(
            "/tracer/v1/traces",
            data=b"{ invalid json }",
            content_type="application/json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not mock_task.apply_async.called


@pytestmark_trace_routes_skip
@pytest.mark.integration
@pytest.mark.api
class TestOTLPTraceEndpointGzip:
    """Tests for POST /tracer/v1/traces with gzip compression."""

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_post_traces_gzip_protobuf(self, mock_task, auth_client):
        """Successfully process gzip-compressed protobuf."""
        mock_task.apply_async.return_value = None

        request = create_test_otlp_request(num_spans=5)
        body = request.SerializeToString()
        compressed_body = gzip.compress(body)

        response = auth_client.post(
            "/tracer/v1/traces",
            data=compressed_body,
            content_type="application/x-protobuf",
            HTTP_CONTENT_ENCODING="gzip",
        )

        assert response.status_code == status.HTTP_200_OK
        assert mock_task.apply_async.called

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_post_traces_gzip_json(self, mock_task, auth_client):
        """Successfully process gzip-compressed JSON."""
        mock_task.apply_async.return_value = None

        request = create_test_otlp_request()
        request_dict = MessageToDict(request, preserving_proto_field_name=True)
        body = json.dumps(request_dict).encode("utf-8")
        compressed_body = gzip.compress(body)

        response = auth_client.post(
            "/tracer/v1/traces",
            data=compressed_body,
            content_type="application/json",
            HTTP_CONTENT_ENCODING="gzip",
        )

        assert response.status_code == status.HTTP_200_OK
        assert mock_task.apply_async.called

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_post_traces_invalid_gzip(self, mock_task, auth_client):
        """Invalid gzip data returns error."""
        response = auth_client.post(
            "/tracer/v1/traces",
            data=b"not gzip data",
            content_type="application/x-protobuf",
            HTTP_CONTENT_ENCODING="gzip",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not mock_task.apply_async.called


@pytestmark_trace_routes_skip
@pytest.mark.integration
@pytest.mark.api
class TestOTLPTraceEndpointContentNegotiation:
    """Tests for content type negotiation."""

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_accept_header_json(self, mock_task, auth_client):
        """Accept: application/json returns JSON response."""
        mock_task.apply_async.return_value = None

        request = create_test_otlp_request()
        body = request.SerializeToString()

        response = auth_client.post(
            "/tracer/v1/traces",
            data=body,
            content_type="application/x-protobuf",
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "application/json" in response["Content-Type"]

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_default_content_type_protobuf(self, mock_task, auth_client):
        """Default response is protobuf when no Accept header."""
        mock_task.apply_async.return_value = None

        request = create_test_otlp_request()
        body = request.SerializeToString()

        response = auth_client.post(
            "/tracer/v1/traces",
            data=body,
            content_type="application/x-protobuf",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/x-protobuf"

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_unsupported_content_type(self, mock_task, auth_client):
        """Unsupported content type returns error."""
        response = auth_client.post(
            "/tracer/v1/traces",
            data=b"some data",
            content_type="text/plain",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not mock_task.apply_async.called


@pytestmark_trace_routes_skip
@pytest.mark.integration
@pytest.mark.api
class TestOTLPTraceEndpointResponse:
    """Tests for OTLP response format."""

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_response_is_valid_protobuf(self, mock_task, auth_client):
        """Response can be parsed as ExportTraceServiceResponse."""
        mock_task.apply_async.return_value = None

        request = create_test_otlp_request()
        body = request.SerializeToString()

        response = auth_client.post(
            "/tracer/v1/traces",
            data=body,
            content_type="application/x-protobuf",
        )

        assert response.status_code == status.HTTP_200_OK

        # Should be valid protobuf
        response_proto = ExportTraceServiceResponse()
        response_proto.ParseFromString(response.content)

    @patch("tracer.views.otlp.bulk_create_observation_span_task")
    def test_error_response_has_partial_success(self, mock_task, auth_client):
        """Error responses include partial_success with error message."""
        # Send invalid data to trigger error
        response = auth_client.post(
            "/tracer/v1/traces",
            data=b"invalid",
            content_type="application/x-protobuf",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Parse error response
        response_proto = ExportTraceServiceResponse()
        response_proto.ParseFromString(response.content)

        # Should have error message in partial_success
        assert response_proto.partial_success.error_message
