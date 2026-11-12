"""Simulation telemetry is checked at the real exporter's protobuf transport."""

import copy
from urllib.parse import quote

import pytest
from opentelemetry.exporter.otlp.proto.grpc import trace_exporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from simulate.services import sim_collector_emit

pytestmark = pytest.mark.integration
PREFIX = "/agentcc/managed-credits/checkout/"


@pytest.fixture
def wire(monkeypatch):
    requests = []

    class Client:
        def __init__(self, channel):
            pass

        def Export(self, request, **kwargs):
            requests.append((request.SerializeToString(), kwargs))
            return object()

    # Replace only the RPC transport, not the product's exporter construction,
    # SDK translation or the final protobuf boundary.
    monkeypatch.setattr(trace_exporter, "TraceServiceStub", Client)
    monkeypatch.setenv("SIM_COLLECTOR_OTLP_ENDPOINT", "127.0.0.1:9")
    return requests


def _span():
    return {
        "trace_id": "1" * 32,
        "span_id": "2" * 16,
        "parent_span_id": "3" * 16,
        "name": "synthetic simulation",
        "start_time": 1,
        "end_time": 2,
        "attributes": {"unrelated": "synthetic ordinary value"},
    }


@pytest.mark.parametrize("encoding", ["canonical", "percent", "double", "unicode"])
@pytest.mark.parametrize("surface", ["attribute", "name", "project", "eval-tags"])
def test_simulation_export_protects_checkout_output(wire, encoding, surface):
    value = "synthetic-simulation-output-0428"
    path = PREFIX + value + "/"
    if encoding in ("percent", "double"):
        path = quote(path, safe="")
        if encoding == "double":
            path = quote(path, safe="")
    elif encoding == "unicode":
        path = "".join(f"\\u{ord(char):04x}" for char in path)
    span = _span()
    kwargs = {
        "project_name": "synthetic project",
        "project_type": "simulate",
        "api_key": "synthetic-api-key",
        "secret_key": "synthetic-secret-key",
    }
    if surface == "attribute":
        span["attributes"]["http.url"] = path
    elif surface == "name":
        span["name"] = path
    elif surface == "project":
        kwargs["project_name"] = path
    else:
        kwargs["eval_tags"] = [{"path": path}]
    # Correlation must cover the entire encoded request, not only the field
    # that contains the route, including resource-to-span correlations.
    span["attributes"]["session"] = value
    original = copy.deepcopy((span, kwargs))
    assert sim_collector_emit.export_sim_spans([span], **kwargs) == 1
    assert (span, kwargs) == original
    assert len(wire) == 1
    payload, metadata = wire[0]
    assert value.encode() not in payload
    assert path.encode() not in payload
    assert b"[redacted]" in payload
    assert b"synthetic ordinary value" in payload
    request = ExportTraceServiceRequest.FromString(payload)
    [resource] = request.resource_spans
    [scope] = resource.scope_spans
    [exported] = scope.spans
    assert exported.trace_id == bytes.fromhex(span["trace_id"])
    assert exported.span_id == bytes.fromhex(span["span_id"])
    assert exported.parent_span_id == bytes.fromhex(span["parent_span_id"])
    assert (exported.start_time_unix_nano, exported.end_time_unix_nano) == (1, 2)
    assert scope.scope.name == "fi.simulation"
    assert dict(metadata["metadata"])["x-api-key"] == "synthetic-api-key"
    assert dict(metadata["metadata"])["x-secret-key"] == "synthetic-secret-key"


def test_simulation_export_preserves_unrelated_output(wire):
    span = _span()
    value = "synthetic-unrelated-session-0428"
    span["attributes"]["http.url"] = "/ordinary/" + value + "/"
    assert (
        sim_collector_emit.export_sim_spans(
            [span],
            project_name="synthetic project",
            project_type="simulate",
            api_key="synthetic-api-key",
            secret_key="synthetic-secret-key",
        )
        == 1
    )
    assert len(wire) == 1
    assert value.encode() in wire[0][0]
    assert b"[redacted]" not in wire[0][0]
