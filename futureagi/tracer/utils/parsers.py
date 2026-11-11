import json
from typing import Any

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from rest_framework.exceptions import ParseError
from rest_framework.parsers import BaseParser, JSONParser


def deserialize_trace_payload(
    payload_bytes: bytes, payload_format: str = "json"
) -> dict[str, Any]:
    """Deserialize raw trace payload bytes into a request data dict.

    Args:
        payload_bytes: Raw bytes of the trace payload.
        payload_format: Either ``"protobuf"`` or ``"json"``.

    Returns:
        Parsed request data dictionary.
    """
    if payload_format == "protobuf":
        proto_request = ExportTraceServiceRequest()
        proto_request.ParseFromString(payload_bytes)
        return MessageToDict(proto_request, preserving_proto_field_name=True)
    elif payload_format == "json":
        return json.loads(payload_bytes.decode("utf-8"))
    else:
        raise ValueError(f"Invalid payload format: {payload_format}")


class ProtobufParser(BaseParser):
    """
    Parses incoming OTLP Protobuf (binary) requests.
    """

    media_type = "application/x-protobuf"

    def parse(self, stream, media_type=None, parser_context=None):
        try:
            data = stream.read()
            if not data:
                raise ParseError("Empty protobuf data")

            request_proto = ExportTraceServiceRequest()
            request_proto.ParseFromString(data)
            # Convert to dict so it can be JSON serialized
            return MessageToDict(request_proto, preserving_proto_field_name=True)
        except Exception as e:
            raise ParseError(f"Failed to parse Protobuf data: {e}")  # noqa: B904


class NestingSafeJSONParser(JSONParser):
    """DRF's JSON parser, with too-deeply nested bodies reported as a 400.

    The C JSON decoder raises RecursionError, not ValueError, once nesting
    exceeds the interpreter's recursion guard, so DRF's JSONParser lets it
    escape as an unhandled 500. Such a body is client input DRF cannot parse.
    """

    def parse(self, stream, media_type=None, parser_context=None):
        try:
            return super().parse(stream, media_type, parser_context)
        except RecursionError as exc:
            raise ParseError("JSON parse error - nesting is too deep") from exc
