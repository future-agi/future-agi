"""Unknown detailed cost is held before enqueue, never stored as free work."""

import json
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.protobuf.json_format import MessageToJson
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

from tracer.utils.normalization import CONTRACT, normalize_attributes
from tracer.utils.normalization.cost import cost_coverage_error
from tracer.utils.otel import _convert_single_span, calculate_cost
from tracer.views.otlp import OTLPTraceView, _ingestion_hold


@pytest.mark.parametrize("usage_key", CONTRACT["unpriced_usage_keys"])
@pytest.mark.parametrize(
    "vector",
    json.loads(
        files("tracer.utils.normalization").joinpath("cost_vectors.json").read_text()
    ),
    ids=lambda v: v["name"],
)
def test_shared_cost_coverage_vectors(vector, usage_key):
    attrs = {usage_key: 40, **vector["attributes"]}
    scope = {"name": "github.copilot", "version": "1.0.98"}
    reason = cost_coverage_error(attrs, scope)
    assert reason == (CONTRACT["cost_coverage_error"] if vector["held"] else "")
    normalized = normalize_attributes(attrs, scope)
    assert cost_coverage_error(normalized, scope) == reason
    # Unknown producers/versions retain legacy pricing behavior.
    assert cost_coverage_error(attrs, {**scope, "version": "unqualified"}) == ""
    with patch("tracer.utils.otel.calculate_cost_from_tokens") as price:
        if vector["held"]:
            with pytest.raises(ValueError, match=CONTRACT["cost_coverage_error"]):
                calculate_cost(normalized, 100, 20, "synthetic-model")
        else:
            assert calculate_cost(normalized, 100, 20, "synthetic-model") >= 0
        price.assert_not_called()


def mixed_request():
    req = ExportTraceServiceRequest()
    scope = req.resource_spans.add().scope_spans.add()
    scope.scope.name = "gemini-cli"
    scope.scope.version = "v1"
    attrs = scope.spans.add().attributes
    attr = attrs.add(key="gen_ai.usage.cache_read_tokens")
    attr.value.int_value = 40
    # Ordinary second span cannot be silently accepted as part of this batch.
    req.resource_spans.add().scope_spans.add().spans.add()
    return req


@pytest.mark.parametrize("content_type", ["application/json", "application/x-protobuf"])
def test_http_whole_batch_hold_precedes_payload_storage(content_type):
    req = mixed_request()
    body = (
        MessageToJson(req).encode()
        if content_type == "application/json"
        else req.SerializeToString()
    )
    request = SimpleNamespace(
        user=SimpleNamespace(
            id="synthetic-user", organization=SimpleNamespace(id="synthetic-org")
        ),
        body=body,
        headers={"Content-Type": content_type},
        content_type=content_type,
        META={"CONTENT_TYPE": content_type},
    )
    with (
        patch("tracer.views.otlp.payload_storage.store") as store,
        patch(
            "tracer.views.otlp.bulk_create_observation_span_task.apply_async"
        ) as enqueue,
        patch(
            "ee.usage.services.rate_limiter.RateLimiter.check",
            return_value=SimpleNamespace(allowed=True),
        ),
    ):
        response = OTLPTraceView().post(request)
    assert response.status_code == 200
    if content_type == "application/json":
        data = json.loads(response.content)
        partial = data.get("partialSuccess", data.get("partial_success"))
        assert int(partial.get("rejectedSpans", partial.get("rejected_spans"))) == 2
        assert (
            partial.get("errorMessage", partial.get("error_message"))
            == CONTRACT["cost_coverage_error"]
        )
    else:
        result = ExportTraceServiceResponse.FromString(response.content)
        assert result.partial_success.rejected_spans == 2
        assert result.partial_success.error_message == CONTRACT["cost_coverage_error"]
    store.assert_not_called()
    enqueue.assert_not_called()


def test_immutable_hold_has_priority_over_cost_hold():
    req = mixed_request()
    attr = (
        req.resource_spans[1]
        .scope_spans[0]
        .spans[0]
        .attributes.add(key="fi.usage.plan.id")
    )
    attr.value.string_value = "opaque"
    assert _ingestion_hold(req) == CONTRACT["admission_error"]


def test_direct_conversion_cannot_persist_unknown_cache_cost():
    span = {
        "scope": {"name": "gemini-cli", "version": "v1"},
        "span_id": "01" * 8,
        "trace_id": "02" * 16,
        "project_name": "test",
        "project_type": "observe",
        "start_time": 1792024600000000000,
        "end_time": 1792024601000000000,
        "attributes": {
            "gen_ai.operation.name": "llm_call",
            "gen_ai.usage.cache_read_tokens": 40,
        },
    }
    with patch("tracer.utils.otel.calculate_cost_from_tokens") as price:
        with pytest.raises(ValueError, match=CONTRACT["cost_coverage_error"]):
            _convert_single_span(
                span,
                {("test", "org", "observe"): SimpleNamespace(id="project")},
                {},
                "org",
            )
        price.assert_not_called()
