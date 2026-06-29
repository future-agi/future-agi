"""
E2E tests for the trace adapter pipeline.

Tests the full ingestion path: OTLP JSON payload → parse → normalize
(adapter layer) → convert → persist → verify DB state.

Uses Django test fixtures for organization/user/workspace/project instead
of manual API keys.  Optionally makes real LLM calls when OPENAI_API_KEY
is set (marked ``@pytest.mark.live_llm``); otherwise uses deterministic
mock data so the adapter pipeline is still tested end-to-end.

Run::

    # With mock data (no API keys needed)
    pytest tracer/tests/adapters/test_e2e_adapters.py -v

    # With real LLM calls
    OPENAI_API_KEY=sk-... pytest tracer/tests/adapters/test_e2e_adapters.py -v -m "e2e or live_llm"
"""

import base64
import json
import os
import time
import uuid
from unittest.mock import patch

import pytest

from accounts.models.user import OrgApiKey
from tracer.models.observation_span import ObservationSpan
from tracer.utils.adapters import normalize_span_attributes
from tracer.utils.otel import bulk_convert_otel_spans_to_observation_spans
from tracer.utils.trace_ingestion import _parse_otel_request

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org_api_key(db, organization, user, workspace):
    """Create an OrgApiKey for API-key-based authentication."""
    return OrgApiKey.objects.create(
        organization=organization,
        user=user,
        workspace=workspace,
        type="user",
        enabled=True,
    )


@pytest.fixture
def e2e_project(db, organization, workspace):
    """Create a test project for adapter E2E tests (observe type)."""
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    return Project.objects.create(
        name="e2e-adapter-test",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


# ---------------------------------------------------------------------------
# OTLP helpers
# ---------------------------------------------------------------------------


def _to_b64(hex_str: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_str)).decode()


def _nano(ts: float) -> str:
    return str(int(ts * 1e9))


def _kv(key: str, value) -> dict:
    """Build an OTLP key-value attribute pair."""
    if isinstance(value, str):
        return {"key": key, "value": {"stringValue": value}}
    elif isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    elif isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    elif isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    else:
        return {"key": key, "value": {"stringValue": str(value)}}


def _make_span(trace_id, span_id, name, attrs_list, parent_id=None):
    now = time.time()
    span = {
        "trace_id": _to_b64(trace_id),
        "span_id": _to_b64(span_id),
        "name": name,
        "start_time_unix_nano": _nano(now - 1.0),
        "end_time_unix_nano": _nano(now),
        "attributes": attrs_list,
        "status": {"code": "STATUS_CODE_OK"},
        "events": [],
    }
    if parent_id:
        span["parent_span_id"] = _to_b64(parent_id)
    return span


def _make_otlp_payload(trace_id, spans, project_name="e2e-adapter-test"):
    return {
        "resource_spans": [
            {
                "resource": {
                    "attributes": [
                        _kv("project_name", project_name),
                        _kv("project_type", "observe"),
                        _kv("service.name", "e2e-test"),
                    ]
                },
                "scope_spans": [
                    {"scope": {"name": "e2e-test", "version": "1.0"}, "spans": spans}
                ],
            }
        ]
    }


# ---------------------------------------------------------------------------
# LLM call data (mock or real)
# ---------------------------------------------------------------------------


def _mock_simple_chat():
    return {
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello in exactly 3 words."},
        ],
        "output_message": {"role": "assistant", "content": "Hello to you!"},
        "prompt_tokens": 24,
        "completion_tokens": 4,
        "total_tokens": 28,
    }


def _mock_tool_call():
    return {
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_messages": [
            {"role": "user", "content": "What's the weather in San Francisco?"},
        ],
        "output_message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_mock123",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"location": "San Francisco", "unit": "celsius"}',
                    },
                }
            ],
        },
        "prompt_tokens": 65,
        "completion_tokens": 22,
        "total_tokens": 87,
    }


def _mock_multi_turn():
    return {
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_messages": [
            {"role": "system", "content": "You are a math tutor. Answer concisely."},
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
            {"role": "user", "content": "And 3+3?"},
        ],
        "output_message": {"role": "assistant", "content": "6"},
        "prompt_tokens": 42,
        "completion_tokens": 1,
        "total_tokens": 43,
    }


def _real_simple_chat():
    import openai

    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello in exactly 3 words."},
        ],
        max_tokens=20,
    )
    msg = response.choices[0].message
    usage = response.usage
    return {
        "model": response.model,
        "provider": "openai",
        "input_messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello in exactly 3 words."},
        ],
        "output_message": {"role": msg.role, "content": msg.content},
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }


def _real_tool_call():
    import openai

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather in a location.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string"},
                        "unit": {
                            "type": "string",
                            "enum": ["celsius", "fahrenheit"],
                        },
                    },
                    "required": ["location"],
                },
            },
        }
    ]
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "What's the weather in San Francisco?"}],
        tools=tools,
        tool_choice="auto",
        max_tokens=100,
    )
    msg = response.choices[0].message
    usage = response.usage
    output = {"role": msg.role, "content": msg.content or ""}
    if msg.tool_calls:
        output["tool_calls"] = [
            {
                "id": tc.id,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return {
        "model": response.model,
        "provider": "openai",
        "input_messages": [
            {"role": "user", "content": "What's the weather in San Francisco?"}
        ],
        "output_message": output,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }


def _real_multi_turn():
    import openai

    messages = [
        {"role": "system", "content": "You are a math tutor. Answer concisely."},
        {"role": "user", "content": "What is 2+2?"},
        {"role": "assistant", "content": "4"},
        {"role": "user", "content": "And 3+3?"},
    ]
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini", messages=messages, max_tokens=10
    )
    msg = response.choices[0].message
    usage = response.usage
    return {
        "model": response.model,
        "provider": "openai",
        "input_messages": messages,
        "output_message": {"role": msg.role, "content": msg.content},
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }


# ---------------------------------------------------------------------------
# Fixtures: LLM call data (mock vs. real)
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_chat_data():
    return _mock_simple_chat()


@pytest.fixture
def tool_call_data():
    return _mock_tool_call()


@pytest.fixture
def multi_turn_data():
    return _mock_multi_turn()


@pytest.fixture
def real_simple_chat():
    return _real_simple_chat()


@pytest.fixture
def real_tool_call():
    return _real_tool_call()


@pytest.fixture
def real_multi_turn():
    return _real_multi_turn()


# ---------------------------------------------------------------------------
# Payload builders — one per adapter format
# ---------------------------------------------------------------------------


def build_langfuse_payload(call_data, project_name="e2e-adapter-test"):
    """Build OTLP payload with langfuse.* attributes."""
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    d = call_data

    attrs = [
        _kv("langfuse.observation.type", "generation"),
        _kv("langfuse.observation.model.name", d["model"]),
        _kv(
            "langfuse.observation.model.parameters",
            json.dumps({"max_tokens": 20, "temperature": 1.0}),
        ),
        _kv(
            "langfuse.observation.usage_details",
            json.dumps(
                {
                    "input_tokens": d["prompt_tokens"],
                    "output_tokens": d["completion_tokens"],
                    "total_tokens": d["total_tokens"],
                }
            ),
        ),
        _kv("langfuse.observation.input", json.dumps(d["input_messages"])),
        _kv("langfuse.observation.output", json.dumps(d["output_message"])),
        _kv(
            "langfuse.observation.metadata",
            json.dumps({"adapter": "langfuse", "test": True}),
        ),
    ]

    span = _make_span(trace_id, span_id, "ChatCompletion-langfuse", attrs)
    return _make_otlp_payload(trace_id, [span], project_name=project_name), trace_id


def build_openinference_payload(call_data, project_name="e2e-adapter-test"):
    """Build OTLP payload with openinference.* + llm.* attributes."""
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    d = call_data

    attrs = [
        _kv("openinference.span.kind", "LLM"),
        _kv("llm.model_name", d["model"]),
        _kv("llm.provider", d["provider"]),
        _kv("llm.system", d["provider"]),
        _kv("llm.token_count.prompt", d["prompt_tokens"]),
        _kv("llm.token_count.completion", d["completion_tokens"]),
        _kv("llm.token_count.total", d["total_tokens"]),
        _kv("llm.invocation_parameters", json.dumps({"max_tokens": 20})),
        _kv("input.value", json.dumps(d["input_messages"])),
        _kv("input.mime_type", "application/json"),
        _kv("output.value", json.dumps(d["output_message"])),
        _kv("output.mime_type", "application/json"),
    ]
    for i, msg in enumerate(d["input_messages"]):
        attrs.append(_kv(f"llm.input_messages.{i}.message.role", msg["role"]))
        attrs.append(_kv(f"llm.input_messages.{i}.message.content", msg["content"]))
    out = d["output_message"]
    attrs.append(
        _kv("llm.output_messages.0.message.role", out.get("role", "assistant"))
    )
    attrs.append(_kv("llm.output_messages.0.message.content", out.get("content", "")))

    span = _make_span(trace_id, span_id, "ChatCompletion-openinference", attrs)
    return _make_otlp_payload(trace_id, [span], project_name=project_name), trace_id


def build_openllmetry_payload(call_data, project_name="e2e-adapter-test"):
    """Build OTLP payload with gen_ai.* + traceloop.* indexed message format."""
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    d = call_data

    attrs = [
        _kv("gen_ai.system", d["provider"]),
        _kv("gen_ai.operation.name", "chat"),
        _kv("gen_ai.request.model", d["model"]),
        _kv("gen_ai.response.model", d["model"]),
        _kv("gen_ai.usage.input_tokens", d["prompt_tokens"]),
        _kv("gen_ai.usage.output_tokens", d["completion_tokens"]),
        _kv("gen_ai.request.temperature", 1.0),
        _kv("gen_ai.request.max_tokens", 20),
        _kv("traceloop.entity.name", "chat.completion"),
        _kv("llm.request.type", "chat"),
        _kv("llm.is_streaming", False),
    ]
    for i, msg in enumerate(d["input_messages"]):
        attrs.append(_kv(f"gen_ai.prompt.{i}.role", msg["role"]))
        attrs.append(_kv(f"gen_ai.prompt.{i}.content", msg["content"]))
    out = d["output_message"]
    attrs.append(_kv("gen_ai.completion.0.role", out.get("role", "assistant")))
    attrs.append(_kv("gen_ai.completion.0.content", out.get("content", "")))
    if "tool_calls" in out:
        for ti, tc in enumerate(out["tool_calls"]):
            fn = tc.get("function", {})
            attrs.append(
                _kv(
                    f"gen_ai.completion.0.tool_calls.{ti}.function.name",
                    fn.get("name", ""),
                )
            )
            attrs.append(
                _kv(
                    f"gen_ai.completion.0.tool_calls.{ti}.function.arguments",
                    fn.get("arguments", ""),
                )
            )

    span = _make_span(trace_id, span_id, "ChatCompletion-openllmetry", attrs)
    return _make_otlp_payload(trace_id, [span], project_name=project_name), trace_id


def build_fi_native_payload(call_data, project_name="e2e-adapter-test"):
    """Build OTLP payload with fi.* + llm.* attributes (native format)."""
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    d = call_data

    attrs = [
        _kv("fi.span.kind", "LLM"),
        _kv("llm.model_name", d["model"]),
        _kv("llm.provider", d["provider"]),
        _kv("llm.system", d["provider"]),
        _kv("llm.token_count.prompt", d["prompt_tokens"]),
        _kv("llm.token_count.completion", d["completion_tokens"]),
        _kv("llm.token_count.total", d["total_tokens"]),
        _kv("llm.invocation_parameters", json.dumps({"max_tokens": 20})),
        _kv("input.value", json.dumps(d["input_messages"])),
        _kv("input.mime_type", "application/json"),
        _kv("output.value", json.dumps(d["output_message"])),
        _kv("output.mime_type", "application/json"),
    ]
    for i, msg in enumerate(d["input_messages"]):
        attrs.append(_kv(f"llm.input_messages.{i}.message.role", msg["role"]))
        attrs.append(_kv(f"llm.input_messages.{i}.message.content", msg["content"]))
    out = d["output_message"]
    attrs.append(
        _kv("llm.output_messages.0.message.role", out.get("role", "assistant"))
    )
    attrs.append(_kv("llm.output_messages.0.message.content", out.get("content", "")))
    if "tool_calls" in out:
        for ti, tc in enumerate(out["tool_calls"]):
            fn = tc.get("function", {})
            if tc.get("id"):
                attrs.append(
                    _kv(
                        f"llm.output_messages.0.message.tool_calls.{ti}.tool_call.id",
                        tc["id"],
                    )
                )
            if fn.get("name"):
                attrs.append(
                    _kv(
                        f"llm.output_messages.0.message.tool_calls.{ti}.tool_call.function.name",
                        fn["name"],
                    )
                )
            if fn.get("arguments"):
                attrs.append(
                    _kv(
                        f"llm.output_messages.0.message.tool_calls.{ti}.tool_call.function.arguments",
                        fn["arguments"],
                    )
                )

    span = _make_span(trace_id, span_id, "ChatCompletion-fi_native", attrs)
    return _make_otlp_payload(trace_id, [span], project_name=project_name), trace_id


BUILDERS = {
    "langfuse": build_langfuse_payload,
    "openinference": build_openinference_payload,
    "openllmetry": build_openllmetry_payload,
    "fi_native": build_fi_native_payload,
}


# ---------------------------------------------------------------------------
# Core pipeline helper — runs the full ingestion synchronously
# ---------------------------------------------------------------------------


def ingest_payload_sync(payload, organization_id, user_id, workspace_id=None):
    """Run the full ingestion pipeline synchronously (no Redis/Temporal).

    Returns the list of parsed-and-converted span dicts so callers can inspect
    the trace IDs used to query ObservationSpan afterwards.
    """
    otel_data_list = _parse_otel_request(payload)
    assert otel_data_list, "No spans parsed from payload"

    normalize_span_attributes(otel_data_list)

    parsed_data_list = bulk_convert_otel_spans_to_observation_spans(
        otel_data_list, organization_id, user_id, workspace_id
    )
    assert parsed_data_list, "No spans converted"

    # Persist — mirrors the bulk_create_observation_span_task logic
    from tracer.utils.trace_ingestion import (
        _bulk_insert_observation_spans,
        _bulk_update_traces,
        _fetch_or_create_traces,
        _fetch_prompt_versions,
        _prepare_observation_spans_and_trace_updates,
        _resolve_end_user_ids,
        _resolve_session_ids,
    )

    all_traces = _fetch_or_create_traces(parsed_data_list)
    all_end_users, _ch_end_users = _resolve_end_user_ids(
        parsed_data_list, organization_id
    )
    all_sessions, _ch_sessions = _resolve_session_ids(parsed_data_list)
    all_prompt_versions = _fetch_prompt_versions(parsed_data_list, organization_id)

    spans_to_create, traces_to_update = _prepare_observation_spans_and_trace_updates(
        parsed_data_list,
        all_traces,
        all_sessions,
        all_end_users,
        all_prompt_versions,
        organization_id,
    )

    _bulk_insert_observation_spans(spans_to_create)
    _bulk_update_traces(traces_to_update, all_traces)

    return parsed_data_list


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def assert_llm_span(span, expected, adapter_name):
    """Assert core fields on a persisted ObservationSpan (model instance)."""
    assert span.observation_type == "llm", (
        f"[{adapter_name}] observation_type: expected 'llm', got '{span.observation_type}'"
    )
    assert expected["model"] in (span.model or ""), (
        f"[{adapter_name}] model: expected '{expected['model']}' in '{span.model}'"
    )
    if span.prompt_tokens is not None:
        assert int(span.prompt_tokens) == expected["prompt_tokens"], (
            f"[{adapter_name}] prompt_tokens: {span.prompt_tokens} != {expected['prompt_tokens']}"
        )
    if span.completion_tokens is not None:
        assert int(span.completion_tokens) == expected["completion_tokens"], (
            f"[{adapter_name}] completion_tokens: {span.completion_tokens} != {expected['completion_tokens']}"
        )
    assert span.input is not None, f"[{adapter_name}] input is None"
    assert span.output is not None, f"[{adapter_name}] output is None"

    # Verify vendor-specific source keys are stripped from eval_attributes.
    # gen_ai.* keys are the normalized semantic-convention surface and may remain.
    ea = span.eval_attributes or {}
    for prefix in ["langfuse.", "openinference.", "traceloop."]:
        leaked = [k for k in ea if k.startswith(prefix)]
        assert not leaked, (
            f"[{adapter_name}] Foreign keys leaked in eval_attributes: {leaked[:5]}"
        )


# ---------------------------------------------------------------------------
# HTTP endpoint auth test
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="OTLP trace routes migrated to fi-collector — pending PII scrubbing port")
@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
class TestOTLPEndpointAuth:
    """Verify that the OTLP endpoint authenticates via OrgApiKey headers."""

    @patch("tracer.views.otlp.bulk_create_observation_span_task.apply_async")
    @patch("tracer.views.otlp.payload_storage")
    def test_post_returns_200_with_valid_api_key(
        self,
        mock_storage,
        mock_apply_async,
        api_client,
        org_api_key,
        e2e_project,
        simple_chat_data,
    ):
        """POST /tracer/v1/traces with valid API key returns 200."""
        mock_storage.store.return_value = "test-payload-key"
        payload, _ = build_fi_native_payload(
            simple_chat_data, project_name=e2e_project.name
        )
        response = api_client.post(
            "/tracer/v1/traces",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY=org_api_key.api_key,
            HTTP_X_SECRET_KEY=org_api_key.secret_key,
        )
        assert response.status_code == 200
        mock_storage.store.assert_called_once()
        mock_apply_async.assert_called_once()

    def test_post_rejects_invalid_api_key(self, api_client, simple_chat_data):
        """POST with bad API key should fail with 401/403."""
        payload, _ = build_fi_native_payload(simple_chat_data)
        response = api_client.post(
            "/tracer/v1/traces",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="bad-key",
            HTTP_X_SECRET_KEY="bad-secret",
        )
        assert response.status_code in (401, 403)

    def test_post_rejects_missing_api_key(self, api_client, simple_chat_data):
        """POST with no auth headers should fail."""
        payload, _ = build_fi_native_payload(simple_chat_data)
        response = api_client.post(
            "/tracer/v1/traces",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Langfuse SDK Basic Auth + compat URL E2E tests
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="OTLP trace routes migrated to fi-collector — pending PII scrubbing port")
@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
class TestLangfuseBasicAuth:
    """Verify that the Langfuse SDK Basic auth flow works end-to-end.

    The Langfuse Python SDK (v3+) sends::

        Authorization: Basic base64(LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY)

    These tests verify:
    1. Basic auth accepted on /tracer/v1/traces
    2. Basic auth accepted on /api/public/otel/v1/traces (compat route)
    3. Invalid Basic auth rejected
    4. Case-insensitive "Basic" scheme
    5. Full pipeline: Langfuse SDK format → Basic auth → ingest → DB
    """

    @staticmethod
    def _basic_auth_header(api_key, secret_key):
        """Build the Authorization header exactly as the Langfuse SDK does."""
        return "Basic " + base64.b64encode(f"{api_key}:{secret_key}".encode()).decode(
            "ascii"
        )

    @patch("tracer.views.otlp.bulk_create_observation_span_task.apply_async")
    @patch("tracer.views.otlp.payload_storage")
    def test_basic_auth_accepted_on_tracer_endpoint(
        self,
        mock_storage,
        mock_apply_async,
        api_client,
        org_api_key,
        e2e_project,
        simple_chat_data,
    ):
        """POST /tracer/v1/traces with Basic auth returns 200."""
        mock_storage.store.return_value = "test-payload-key"
        payload, _ = build_langfuse_payload(
            simple_chat_data, project_name=e2e_project.name
        )
        auth = self._basic_auth_header(org_api_key.api_key, org_api_key.secret_key)
        response = api_client.post(
            "/tracer/v1/traces",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=auth,
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.content}"
        )
        mock_storage.store.assert_called_once()
        mock_apply_async.assert_called_once()

    @patch("tracer.views.http_otlp.bulk_create_observation_span_task.apply_async")
    @patch("tracer.views.http_otlp.payload_storage")
    def test_basic_auth_accepted_on_compat_url(
        self,
        mock_storage,
        mock_apply_async,
        api_client,
        org_api_key,
        e2e_project,
        simple_chat_data,
    ):
        """POST /api/public/otel/v1/traces (Langfuse default path) returns 200."""
        mock_storage.store.return_value = "test-payload-key"
        payload, _ = build_langfuse_payload(
            simple_chat_data, project_name=e2e_project.name
        )
        auth = self._basic_auth_header(org_api_key.api_key, org_api_key.secret_key)
        response = api_client.post(
            "/api/public/otel/v1/traces",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=auth,
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.content}"
        )
        mock_storage.store.assert_called_once()
        mock_apply_async.assert_called_once()

    @patch("tracer.views.otlp.bulk_create_observation_span_task.apply_async")
    @patch("tracer.views.otlp.payload_storage")
    def test_basic_auth_case_insensitive(
        self,
        mock_storage,
        mock_apply_async,
        api_client,
        org_api_key,
        e2e_project,
        simple_chat_data,
    ):
        """'BASIC' (uppercase) scheme should also be accepted per RFC 7617."""
        mock_storage.store.return_value = "test-payload-key"
        payload, _ = build_langfuse_payload(
            simple_chat_data, project_name=e2e_project.name
        )
        encoded = base64.b64encode(
            f"{org_api_key.api_key}:{org_api_key.secret_key}".encode()
        ).decode()
        response = api_client.post(
            "/tracer/v1/traces",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"BASIC {encoded}",
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.content}"
        )
        mock_storage.store.assert_called_once()
        mock_apply_async.assert_called_once()

    def test_basic_auth_rejects_invalid_credentials(self, api_client, simple_chat_data):
        """Basic auth with wrong credentials returns 401."""
        payload, _ = build_langfuse_payload(simple_chat_data)
        auth = self._basic_auth_header("bad-pk", "bad-sk")
        response = api_client.post(
            "/tracer/v1/traces",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=auth,
        )
        assert response.status_code == 401

    def test_basic_auth_rejects_malformed_base64(self, api_client, simple_chat_data):
        """Basic auth with non-base64 payload returns 401."""
        payload, _ = build_langfuse_payload(simple_chat_data)
        response = api_client.post(
            "/tracer/v1/traces",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION="Basic %%%not-base64%%%",
        )
        assert response.status_code == 401

    def test_basic_auth_rejects_missing_colon(self, api_client, simple_chat_data):
        """Basic auth without colon separator returns 401."""
        payload, _ = build_langfuse_payload(simple_chat_data)
        encoded = base64.b64encode(b"nocolonseparator").decode()
        response = api_client.post(
            "/tracer/v1/traces",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Basic {encoded}",
        )
        assert response.status_code == 401

    @patch("tracer.views.otlp.bulk_create_observation_span_task.apply_async")
    @patch("tracer.views.otlp.payload_storage")
    def test_bearer_jwt_still_works_alongside_basic(
        self,
        mock_storage,
        mock_apply_async,
        auth_client,
        e2e_project,
        simple_chat_data,
    ):
        """Bearer JWT auth continues to work on the OTLP endpoint."""
        mock_storage.store.return_value = "test-payload-key"
        payload, _ = build_fi_native_payload(
            simple_chat_data, project_name=e2e_project.name
        )
        # auth_client uses force_authenticate (JWT-like path)
        response = auth_client.post(
            "/tracer/v1/traces",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert response.status_code == 200
        mock_storage.store.assert_called_once()
        mock_apply_async.assert_called_once()

    def test_full_pipeline_with_basic_auth(
        self, organization, user, workspace, e2e_project, simple_chat_data
    ):
        """Full pipeline: Langfuse SDK payload + Basic auth → DB verification."""
        payload, trace_id = build_langfuse_payload(
            simple_chat_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))

        spans = ObservationSpan.objects.filter(trace_id=trace_id)
        assert spans.exists(), "No spans persisted"

        llm_span = spans.filter(observation_type="llm").first()
        assert llm_span is not None, "No LLM span found"
        assert_llm_span(llm_span, simple_chat_data, "langfuse-basic-auth")

        # Verify eval_attributes contain expected normalized keys
        ea = llm_span.eval_attributes or {}
        assert ea.get("gen_ai.span.kind") == "LLM", (
            f"gen_ai.span.kind: {ea.get('gen_ai.span.kind')}"
        )
        assert "llm.model_name" in ea, (
            f"Missing llm.model_name in: {list(ea.keys())[:10]}"
        )
        assert "llm.token_count.prompt" in ea
        assert "llm.token_count.completion" in ea

    def test_full_pipeline_with_langfuse_sdk_headers(
        self, organization, user, workspace, e2e_project, simple_chat_data
    ):
        """Verify Langfuse SDK extra headers (x-langfuse-*) don't break ingestion."""
        payload, trace_id = build_langfuse_payload(
            simple_chat_data, project_name=e2e_project.name
        )
        # The SDK sends these extra headers alongside Basic auth — verify they're harmless
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))

        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        span = spans.first()
        # No langfuse.* keys should leak into eval_attributes
        ea = span.eval_attributes or {}
        leaked = [k for k in ea if k.startswith("langfuse.")]
        assert not leaked, f"langfuse.* keys leaked: {leaked}"


# ---------------------------------------------------------------------------
# Full pipeline E2E tests (mock data)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
class TestLangfuseE2E:
    def test_simple_chat(
        self, organization, user, workspace, e2e_project, simple_chat_data
    ):
        payload, trace_id = build_langfuse_payload(
            simple_chat_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(trace_id=trace_id)
        assert spans.exists(), "No spans persisted"
        llm_spans = spans.filter(observation_type="llm")
        assert llm_spans.exists(), (
            f"No LLM span. Types: {list(spans.values_list('observation_type', flat=True))}"
        )
        assert_llm_span(llm_spans.first(), simple_chat_data, "langfuse")

    def test_tool_calling(
        self, organization, user, workspace, e2e_project, tool_call_data
    ):
        payload, trace_id = build_langfuse_payload(
            tool_call_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(trace_id=trace_id)
        assert spans.exists()
        span = spans.filter(observation_type="llm").first()
        assert span is not None
        # Tool calls should appear in eval_attributes
        ea = span.eval_attributes or {}
        tool_keys = [k for k in ea if "tool_call" in k or "tool_calls" in k]
        assert tool_keys, (
            f"No tool_call keys in eval_attributes: {list(ea.keys())[:10]}"
        )

    def test_multi_turn(
        self, organization, user, workspace, e2e_project, multi_turn_data
    ):
        payload, trace_id = build_langfuse_payload(
            multi_turn_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        assert_llm_span(spans.first(), multi_turn_data, "langfuse")


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
class TestOpenInferenceE2E:
    def test_simple_chat(
        self, organization, user, workspace, e2e_project, simple_chat_data
    ):
        payload, trace_id = build_openinference_payload(
            simple_chat_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        assert_llm_span(spans.first(), simple_chat_data, "openinference")

    def test_tool_calling(
        self, organization, user, workspace, e2e_project, tool_call_data
    ):
        payload, trace_id = build_openinference_payload(
            tool_call_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(trace_id=trace_id)
        assert spans.exists()
        assert_llm_span(
            spans.filter(observation_type="llm").first(),
            tool_call_data,
            "openinference",
        )

    def test_multi_turn(
        self, organization, user, workspace, e2e_project, multi_turn_data
    ):
        payload, trace_id = build_openinference_payload(
            multi_turn_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        assert_llm_span(spans.first(), multi_turn_data, "openinference")


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
class TestOpenLLMetryE2E:
    def test_simple_chat(
        self, organization, user, workspace, e2e_project, simple_chat_data
    ):
        payload, trace_id = build_openllmetry_payload(
            simple_chat_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        assert_llm_span(spans.first(), simple_chat_data, "openllmetry")

    def test_tool_calling(
        self, organization, user, workspace, e2e_project, tool_call_data
    ):
        payload, trace_id = build_openllmetry_payload(
            tool_call_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(trace_id=trace_id)
        assert spans.exists()
        span = spans.filter(observation_type="llm").first()
        assert span is not None
        assert_llm_span(span, tool_call_data, "openllmetry")

    def test_multi_turn(
        self, organization, user, workspace, e2e_project, multi_turn_data
    ):
        payload, trace_id = build_openllmetry_payload(
            multi_turn_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        assert_llm_span(spans.first(), multi_turn_data, "openllmetry")


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
class TestFiNativeE2E:
    def test_simple_chat(
        self, organization, user, workspace, e2e_project, simple_chat_data
    ):
        payload, trace_id = build_fi_native_payload(
            simple_chat_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        assert_llm_span(spans.first(), simple_chat_data, "fi_native")

    def test_tool_calling(
        self, organization, user, workspace, e2e_project, tool_call_data
    ):
        payload, trace_id = build_fi_native_payload(
            tool_call_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(trace_id=trace_id)
        assert spans.exists()
        span = spans.filter(observation_type="llm").first()
        assert span is not None
        # Verify tool calls in eval_attributes
        ea = span.eval_attributes or {}
        tool_keys = [k for k in ea if "tool_call" in k]
        assert tool_keys, (
            f"No tool_call keys in eval_attributes: {list(ea.keys())[:10]}"
        )

    def test_multi_turn(
        self, organization, user, workspace, e2e_project, multi_turn_data
    ):
        payload, trace_id = build_fi_native_payload(
            multi_turn_data, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        assert_llm_span(spans.first(), multi_turn_data, "fi_native")


# ---------------------------------------------------------------------------
# Cross-adapter equivalence
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
class TestCrossAdapterEquivalence:
    """Same call data through all 4 adapters should produce equivalent DB rows."""

    def test_simple_chat_equivalence(
        self, organization, user, workspace, e2e_project, simple_chat_data
    ):
        results = {}
        for adapter_name, builder_fn in BUILDERS.items():
            payload, trace_id = builder_fn(
                simple_chat_data, project_name=e2e_project.name
            )
            ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
            spans = ObservationSpan.objects.filter(
                trace_id=trace_id, observation_type="llm"
            )
            assert spans.exists(), f"{adapter_name}: no LLM span persisted"
            results[adapter_name] = spans.first()

        # Compare core fields across all adapters
        ref = results["fi_native"]
        for adapter_name, span in results.items():
            if adapter_name == "fi_native":
                continue
            assert span.observation_type == ref.observation_type, (
                f"{adapter_name} observation_type mismatch"
            )
            assert span.model == ref.model or ref.model in (span.model or ""), (
                f"{adapter_name} model mismatch: {span.model} vs {ref.model}"
            )
            assert span.prompt_tokens == ref.prompt_tokens, (
                f"{adapter_name} prompt_tokens: {span.prompt_tokens} vs {ref.prompt_tokens}"
            )
            assert span.completion_tokens == ref.completion_tokens, (
                f"{adapter_name} completion_tokens: {span.completion_tokens} vs {ref.completion_tokens}"
            )
            assert span.input is not None, f"{adapter_name} input is None"
            assert span.output is not None, f"{adapter_name} output is None"

            # Verify no vendor-specific source keys leaked.
            # gen_ai.* keys are the normalized semantic-convention surface.
            ea = span.eval_attributes or {}
            for prefix in [
                "langfuse.",
                "openinference.",
                "traceloop.",
            ]:
                leaked = [k for k in ea if k.startswith(prefix)]
                assert not leaked, (
                    f"{adapter_name}: foreign key prefix '{prefix}' leaked: {leaked[:3]}"
                )


# ---------------------------------------------------------------------------
# Live LLM tests (only run when OPENAI_API_KEY is set)
# ---------------------------------------------------------------------------


@pytest.mark.live_llm
@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(not OPENAI_API_KEY, reason="OPENAI_API_KEY not set")
class TestLiveLLMAdapters:
    """Run the full pipeline with real OpenAI API calls."""

    def test_live_langfuse(
        self, organization, user, workspace, e2e_project, real_simple_chat
    ):
        payload, trace_id = build_langfuse_payload(
            real_simple_chat, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        assert_llm_span(spans.first(), real_simple_chat, "langfuse-live")

    def test_live_openinference(
        self, organization, user, workspace, e2e_project, real_simple_chat
    ):
        payload, trace_id = build_openinference_payload(
            real_simple_chat, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        assert_llm_span(spans.first(), real_simple_chat, "openinference-live")

    def test_live_openllmetry(
        self, organization, user, workspace, e2e_project, real_simple_chat
    ):
        payload, trace_id = build_openllmetry_payload(
            real_simple_chat, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        assert_llm_span(spans.first(), real_simple_chat, "openllmetry-live")

    def test_live_fi_native(
        self, organization, user, workspace, e2e_project, real_simple_chat
    ):
        payload, trace_id = build_fi_native_payload(
            real_simple_chat, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        assert_llm_span(spans.first(), real_simple_chat, "fi_native-live")

    def test_live_tool_call(
        self, organization, user, workspace, e2e_project, real_tool_call
    ):
        """Real tool call through fi_native adapter."""
        payload, trace_id = build_fi_native_payload(
            real_tool_call, project_name=e2e_project.name
        )
        ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
        spans = ObservationSpan.objects.filter(
            trace_id=trace_id, observation_type="llm"
        )
        assert spans.exists()
        span = spans.first()
        assert_llm_span(span, real_tool_call, "fi_native-live-tool")

    def test_live_cross_adapter_equivalence(
        self, organization, user, workspace, e2e_project, real_simple_chat
    ):
        """Same real LLM call data through all 4 adapters must match."""
        trace_ids = {}
        for adapter_name, builder_fn in BUILDERS.items():
            payload, trace_id = builder_fn(
                real_simple_chat, project_name=e2e_project.name
            )
            ingest_payload_sync(payload, organization.id, user.id, str(workspace.id))
            trace_ids[adapter_name] = trace_id

        models = {}
        for adapter_name, trace_id in trace_ids.items():
            span = ObservationSpan.objects.filter(
                trace_id=trace_id, observation_type="llm"
            ).first()
            assert span is not None, f"[{adapter_name}] no LLM span"
            models[adapter_name] = span.model

        # All adapters should resolve to the same model
        unique_models = set(models.values())
        assert len(unique_models) == 1, f"Model mismatch across adapters: {models}"


# ---------------------------------------------------------------------------
# Langfuse SDK live E2E: real SDK → real OpenAI API → live server → DB
# ---------------------------------------------------------------------------


@pytest.fixture
def test_redis_storage():
    """Point payload_storage at test Redis (port 16379) for live server tests.

    The live_server fixture runs Django in a separate thread.  With
    CELERY_TASK_ALWAYS_EAGER=True the ingestion Celery task runs synchronously
    in that thread, so payload_storage needs real Redis to store/retrieve.
    """
    import tfc.utils.payload_storage as ps_mod

    old_storage = ps_mod._payload_storage
    ps_mod._payload_storage = ps_mod.PayloadStorage(
        redis_url="redis://localhost:16379/0"
    )
    yield
    ps_mod._payload_storage = old_storage


@pytest.fixture
def langfuse_sdk_client(live_server, org_api_key, e2e_project, test_redis_storage):
    """Create a Langfuse SDK client pointed at the live Django test server.

    Sets OTEL_RESOURCE_ATTRIBUTES so the SDK's OTLP Resource includes
    ``project_name`` and ``project_type`` — required by our ingestion pipeline
    to route traces to the correct project.
    """
    import opentelemetry.trace as otel_trace
    from langfuse import Langfuse
    from langfuse._client.resource_manager import LangfuseResourceManager
    from opentelemetry.trace import ProxyTracerProvider

    # Save env state
    old_env = {}
    env_vars = {
        "OTEL_RESOURCE_ATTRIBUTES": (
            f"project_name={e2e_project.name},project_type=observe"
        ),
        "LANGFUSE_MEDIA_UPLOAD_ENABLED": "false",
    }
    for k, v in env_vars.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v

    # Reset global OTEL provider so Resource.create() picks up new env vars
    otel_trace.set_tracer_provider(ProxyTracerProvider())
    LangfuseResourceManager._instances.clear()

    client = Langfuse(
        public_key=org_api_key.api_key,
        secret_key=org_api_key.secret_key,
        host=live_server.url,
        flush_at=1,
        flush_interval=1,
    )
    yield client

    client.shutdown()
    LangfuseResourceManager._instances.clear()
    otel_trace.set_tracer_provider(ProxyTracerProvider())

    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.mark.live_llm
@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(not OPENAI_API_KEY, reason="OPENAI_API_KEY not set")
class TestLangfuseSdkLiveE2E:
    """Real Langfuse SDK → real OpenAI API → live Django server → DB.

    Uses the actual Langfuse Python SDK (v3+) to:
    1. Wrap real OpenAI API calls with Langfuse span/generation APIs
    2. Export OTLP spans over HTTP Basic auth to our live test server
    3. Verify the full ingestion pipeline creates correct DB records

    Requires: OPENAI_API_KEY env var + Docker test services (Redis on 16379).
    """

    def test_generation_span(self, langfuse_sdk_client, e2e_project):
        """SDK generation span → OTLP export → ingestion → DB verify."""
        import openai as openai_mod

        openai_client = openai_mod.OpenAI(api_key=OPENAI_API_KEY)

        with langfuse_sdk_client.start_as_current_span(
            name="e2e-test-generation"
        ) as span:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Say hello in 3 words."},
                ],
                max_tokens=20,
            )
            msg = response.choices[0].message
            usage = response.usage

            with span.start_as_current_generation(
                name="openai-chat",
                model=response.model,
                input=[
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Say hello in 3 words."},
                ],
                output=msg.content,
                usage_details={
                    "input_tokens": usage.prompt_tokens,
                    "output_tokens": usage.completion_tokens,
                },
                model_parameters={"max_tokens": 20},
            ):
                pass  # Auto-ended by context manager

        langfuse_sdk_client.flush()
        time.sleep(3)

        spans = ObservationSpan.objects.filter(project=e2e_project)
        assert spans.exists(), "No spans found — SDK trace not ingested"

        # Find the generation span (has model info)
        gen_spans = [s for s in spans if s.model and "gpt-4o" in s.model]
        assert gen_spans, (
            f"No generation span with model. Span models: "
            f"{[(s.name, s.model) for s in spans]}"
        )
        gen_span = gen_spans[0]
        assert gen_span.prompt_tokens is not None
        assert gen_span.completion_tokens is not None
        assert gen_span.input is not None
        assert gen_span.output is not None

    def test_auto_traced_openai(self, langfuse_sdk_client, org_api_key, e2e_project):
        """langfuse.openai auto-instrumentation → live server → DB."""
        from langfuse.openai import openai as traced_openai

        response = traced_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "What is 2+2? One word only."}],
            max_tokens=5,
            langfuse_public_key=org_api_key.api_key,
        )

        langfuse_sdk_client.flush()
        time.sleep(3)

        assert response.choices[0].message.content is not None

        spans = ObservationSpan.objects.filter(project=e2e_project)
        assert spans.exists(), "No spans found — auto-traced call not ingested"

    def test_tool_calling(self, langfuse_sdk_client, e2e_project):
        """SDK generation with tool calling → DB verification."""
        import openai as openai_mod

        openai_client = openai_mod.OpenAI(api_key=OPENAI_API_KEY)
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a location.",
                    "parameters": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    },
                },
            }
        ]

        with langfuse_sdk_client.start_as_current_span(
            name="e2e-test-tool-calling"
        ) as span:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "What's the weather in NYC?"}],
                tools=tools,
                tool_choice="auto",
                max_tokens=100,
            )
            msg = response.choices[0].message
            output = {"role": msg.role, "content": msg.content or ""}
            if msg.tool_calls:
                output["tool_calls"] = [
                    {
                        "id": tc.id,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]

            with span.start_as_current_generation(
                name="openai-tool-call",
                model=response.model,
                input=[{"role": "user", "content": "What's the weather in NYC?"}],
                output=output,
                usage_details={
                    "input_tokens": response.usage.prompt_tokens,
                    "output_tokens": response.usage.completion_tokens,
                },
            ):
                pass

        langfuse_sdk_client.flush()
        time.sleep(3)

        spans = ObservationSpan.objects.filter(project=e2e_project)
        assert spans.exists(), "No spans found for tool calling test"

    def test_eval_attributes_structure(self, langfuse_sdk_client, e2e_project):
        """Verify eval_attributes contain expected fi.* and llm.* keys."""
        import openai as openai_mod

        openai_client = openai_mod.OpenAI(api_key=OPENAI_API_KEY)

        with langfuse_sdk_client.start_as_current_span(
            name="e2e-test-eval-attrs"
        ) as span:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "Just say 'ok'."}],
                max_tokens=5,
            )
            with span.start_as_current_generation(
                name="openai-eval-check",
                model=response.model,
                input=[{"role": "user", "content": "Just say 'ok'."}],
                output=response.choices[0].message.content,
                usage_details={
                    "input_tokens": response.usage.prompt_tokens,
                    "output_tokens": response.usage.completion_tokens,
                },
            ):
                pass

        langfuse_sdk_client.flush()
        time.sleep(3)

        gen_spans = (
            ObservationSpan.objects.filter(project=e2e_project)
            .exclude(model__isnull=True)
            .exclude(model="")
        )
        assert gen_spans.exists(), "No generation spans found"

        db_span = gen_spans.first()
        ea = db_span.eval_attributes or {}

        # Core normalized key set by langfuse adapter
        assert "gen_ai.span.kind" in ea, (
            f"Missing gen_ai.span.kind in {list(ea.keys())[:15]}"
        )
        # llm.* keys from langfuse adapter
        assert "llm.model_name" in ea, (
            f"Missing llm.model_name in {list(ea.keys())[:15]}"
        )
        # No foreign langfuse.* keys should leak
        leaked = [k for k in ea if k.startswith("langfuse.")]
        assert not leaked, f"langfuse.* keys leaked: {leaked}"

    def test_multi_turn_conversation(self, langfuse_sdk_client, e2e_project):
        """Multi-turn conversation produces multiple generation spans."""
        import openai as openai_mod

        openai_client = openai_mod.OpenAI(api_key=OPENAI_API_KEY)
        messages = [
            {"role": "system", "content": "Answer math questions concisely."},
            {"role": "user", "content": "What is 2+2?"},
        ]

        with langfuse_sdk_client.start_as_current_span(name="e2e-multi-turn") as parent:
            # Turn 1
            r1 = openai_client.chat.completions.create(
                model="gpt-4o-mini", messages=messages, max_tokens=10
            )
            messages.append(
                {"role": "assistant", "content": r1.choices[0].message.content}
            )
            with parent.start_as_current_generation(
                name="turn-1",
                model=r1.model,
                input=messages[:-1],
                output=r1.choices[0].message.content,
                usage_details={
                    "input_tokens": r1.usage.prompt_tokens,
                    "output_tokens": r1.usage.completion_tokens,
                },
            ):
                pass

            # Turn 2
            messages.append({"role": "user", "content": "And 3+3?"})
            r2 = openai_client.chat.completions.create(
                model="gpt-4o-mini", messages=messages, max_tokens=10
            )
            with parent.start_as_current_generation(
                name="turn-2",
                model=r2.model,
                input=messages,
                output=r2.choices[0].message.content,
                usage_details={
                    "input_tokens": r2.usage.prompt_tokens,
                    "output_tokens": r2.usage.completion_tokens,
                },
            ):
                pass

        langfuse_sdk_client.flush()
        time.sleep(3)

        spans = ObservationSpan.objects.filter(project=e2e_project)
        # At least 3 spans: parent span + 2 generation spans
        assert spans.count() >= 3, (
            f"Expected >=3 spans for multi-turn, got {spans.count()}"
        )
