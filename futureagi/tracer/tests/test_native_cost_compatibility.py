"""Native producer qualification is not opt-in to new accounting admission."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.protobuf.json_format import MessageToJson
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

from tracer.utils.normalization import CONTRACT, normalize_attributes
from tracer.utils.otel import _convert_single_span, calculate_cost
from tracer.views.otlp import OTLPTraceView, _ingestion_hold

PRODUCERS = [
    {"name": "github.copilot", "version": "1.0.98"},
    {"name": "gemini-cli", "version": "v1"},
]
USAGE_KEYS = [
    "gen_ai.usage.cache_read_tokens",
    "gen_ai.usage.cache_write_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_write.input_tokens",
    "gen_ai.usage.output_tokens.reasoning",
    "gen_ai.usage.reasoning.output_tokens",
]


@pytest.mark.parametrize("scope", PRODUCERS)
@pytest.mark.parametrize("usage_key", USAGE_KEYS)
@pytest.mark.parametrize(
    "reported",
    [
        {},
        {"gen_ai.cost.total": 0.25},
        {"llm.cost.total": 0.25},
        {"gen_ai.cost.total": 0},
    ],
)
def test_native_detailed_usage_preserves_legacy_pricing(scope, usage_key, reported):
    attrs = normalize_attributes({usage_key: 40, **reported}, scope)
    with patch(
        "tracer.utils.otel.calculate_cost_from_tokens", return_value=0.5
    ) as price:
        assert calculate_cost(attrs, 100, 20, "synthetic-model") == (
            next(iter(reported.values())) if reported else 0.5
        )
        if reported:
            price.assert_not_called()
            assert attrs["fi.cost.kind"] == "provider_reported"
        else:
            price.assert_called_once_with(
                prompt_tokens=100,
                completion_tokens=20,
                model="synthetic-model",
                organization_id=None,
            )
            assert attrs["fi.cost.kind"] == "estimated"
            assert "gen_ai.cost.total" not in attrs  # no synthetic USD zero


def mixed_request(scope, reported_cost=False, immutable=False):
    req = ExportTraceServiceRequest()
    native = req.resource_spans.add().scope_spans.add()
    native.scope.name = scope["name"]
    native.scope.version = scope["version"]
    attrs = native.spans.add().attributes
    attrs.add(key="gen_ai.usage.cache_read_tokens").value.int_value = 40
    if reported_cost:
        attrs.add(key="gen_ai.usage.total_cost").value.double_value = 0.25
    if immutable:
        attrs.add(key="fi.usage.plan.id").value.string_value = "opaque"
    # A rejection must not silently lose the unrelated SDK span either.
    req.resource_spans.add().scope_spans.add().spans.add()
    return req


@pytest.mark.parametrize("content_type", ["application/json", "application/x-protobuf"])
@pytest.mark.parametrize("scope", PRODUCERS)
@pytest.mark.parametrize("reported_cost", [False, True])
@pytest.mark.parametrize("immutable", [False, True])
def test_native_mixed_batch_only_refuses_explicit_contract(
    content_type, scope, reported_cost, immutable
):
    req = mixed_request(scope, reported_cost, immutable)
    expected_reason = CONTRACT["admission_error"] if immutable else ""
    assert _ingestion_hold(req) == expected_reason
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
        patch(
            "tracer.views.otlp.payload_storage.store", return_value="test-payload"
        ) as store,
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
        partial = data.get("partialSuccess", data.get("partial_success", {}))
        rejected = int(partial.get("rejectedSpans", partial.get("rejected_spans", 0)))
        reason = partial.get("errorMessage", partial.get("error_message", ""))
    else:
        result = ExportTraceServiceResponse.FromString(response.content)
        rejected = result.partial_success.rejected_spans
        reason = result.partial_success.error_message
    assert rejected == (2 if immutable else 0)
    assert reason == expected_reason
    if immutable:
        store.assert_not_called()
        enqueue.assert_not_called()
    else:
        store.assert_called_once()
        stored = json.loads(store.call_args.args[0])
        assert (
            sum(
                len(s["spans"])
                for r in stored["resource_spans"]
                for s in r["scope_spans"]
            )
            == 2
        )
        enqueue.assert_called_once_with(
            args=["test-payload", "synthetic-org", "synthetic-user", None, "json"],
            queue="trace_ingestion",
        )


@pytest.mark.parametrize("scope", PRODUCERS)
@pytest.mark.parametrize("immutable", [False, True])
def test_native_direct_conversion_preserves_legacy_cost(scope, immutable):
    span = {
        "scope": scope,
        "span_id": "01" * 8,
        "trace_id": "02" * 16,
        "project_name": "test",
        "project_type": "observe",
        "start_time": 1792024600000000000,
        "end_time": 1792024601000000000,
        "attributes": {
            "gen_ai.operation.name": "chat"
            if scope["name"] == "github.copilot"
            else "llm_call",
            "gen_ai.usage.input_tokens": 100,
            "gen_ai.usage.output_tokens": 20,
            "gen_ai.usage.cache_read_tokens": 40,
        },
    }
    if immutable:
        span["attributes"]["fi.usage.plan.id"] = "opaque"
    with patch(
        "tracer.utils.otel.calculate_cost_from_tokens", return_value=0.5
    ) as price:
        if immutable:
            with pytest.raises(ValueError, match=CONTRACT["admission_error"]):
                _convert_single_span(
                    span,
                    {("test", "org", "observe"): SimpleNamespace(id="project")},
                    {},
                    "org",
                )
            price.assert_not_called()
        else:
            result = _convert_single_span(
                span,
                {("test", "org", "observe"): SimpleNamespace(id="project")},
                {},
                "org",
            )
            assert result["observation_span"]["cost"] == 0.5
            assert result["observation_span"]["prompt_tokens"] == 100
            assert result["observation_span"]["completion_tokens"] == 20
            price.assert_called_once()
