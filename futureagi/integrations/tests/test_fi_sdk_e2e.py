"""E2E tests: simulate FI SDK trace sending and verify DB records.

These tests build OTLP payloads in the same format the FI SDK produces,
run them through the trace ingestion pipeline, and verify the resulting
Trace and ObservationSpan records in the database.

Tests marked @pytest.mark.live_llm make real OpenAI API calls and are
skipped unless OPENAI_API_KEY is set.
"""

import json
import os
import time
import uuid

import pytest

from model_hub.models.ai_model import AIModel
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.utils.otel import bulk_convert_otel_spans_to_observation_spans
from tracer.utils.trace_ingestion import (
    _bulk_insert_observation_spans,
    _bulk_update_traces,
    _fetch_or_create_traces,
    _fetch_prompt_versions,
    _parse_otel_request,
    _prepare_observation_spans_and_trace_updates,
    _resolve_end_user_ids,
    _resolve_session_ids,
)

HAS_OPENAI_KEY = bool(os.environ.get("OPENAI_API_KEY"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_otlp_payload(
    project_name,
    spans,
    project_type="observe",
    trace_id=None,
):
    """Build an OTLP-formatted JSON payload like the FI SDK produces."""
    trace_id = trace_id or uuid.uuid4().hex
    return {
        "resource_spans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "project_name",
                            "value": {"string_value": project_name},
                        },
                        {
                            "key": "project_type",
                            "value": {"string_value": project_type},
                        },
                    ]
                },
                "scope_spans": [{"spans": spans}],
            }
        ]
    }


def _make_llm_span(
    trace_id,
    span_id=None,
    parent_span_id=None,
    model="gpt-4o-mini",
    input_messages=None,
    output_content="Hello!",
    prompt_tokens=10,
    completion_tokens=5,
    total_tokens=15,
    name="chat_completions",
):
    """Build a single LLM span in OTLP format with fi.* attributes."""
    span_id = span_id or uuid.uuid4().hex
    now_ns = int(time.time() * 1e9)

    input_messages = input_messages or [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Say hello."},
    ]

    attrs = [
        {"key": "fi.span.kind", "value": {"string_value": "LLM"}},
        {"key": "llm.model_name", "value": {"string_value": model}},
        {"key": "llm.token_count.prompt", "value": {"int_value": prompt_tokens}},
        {
            "key": "llm.token_count.completion",
            "value": {"int_value": completion_tokens},
        },
        {"key": "llm.token_count.total", "value": {"int_value": total_tokens}},
        {"key": "input.value", "value": {"string_value": json.dumps(input_messages)}},
        {"key": "input.mime_type", "value": {"string_value": "application/json"}},
        {"key": "output.value", "value": {"string_value": json.dumps(output_content)}},
        {"key": "output.mime_type", "value": {"string_value": "application/json"}},
    ]

    # Flatten input messages
    for i, msg in enumerate(input_messages):
        attrs.append(
            {
                "key": f"llm.input_messages.{i}.message.role",
                "value": {"string_value": msg["role"]},
            }
        )
        attrs.append(
            {
                "key": f"llm.input_messages.{i}.message.content",
                "value": {"string_value": msg.get("content", "")},
            }
        )

    # Output message
    attrs.append(
        {
            "key": "llm.output_messages.0.message.role",
            "value": {"string_value": "assistant"},
        }
    )
    attrs.append(
        {
            "key": "llm.output_messages.0.message.content",
            "value": {"string_value": output_content},
        }
    )

    span = {
        "trace_id": trace_id,
        "span_id": span_id,
        "name": name,
        "start_time_unix_nano": str(now_ns),
        "end_time_unix_nano": str(now_ns + 500_000_000),
        "attributes": attrs,
        "status": {"code": 1},
    }
    if parent_span_id:
        span["parent_span_id"] = parent_span_id
    return span


def _make_tool_span(
    trace_id, span_id=None, parent_span_id=None, tool_name="get_weather"
):
    """Build a tool span."""
    span_id = span_id or uuid.uuid4().hex
    now_ns = int(time.time() * 1e9)
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": tool_name,
        "start_time_unix_nano": str(now_ns),
        "end_time_unix_nano": str(now_ns + 100_000_000),
        "attributes": [
            {"key": "fi.span.kind", "value": {"string_value": "TOOL"}},
            {"key": "input.value", "value": {"string_value": '{"city": "London"}'}},
            {"key": "output.value", "value": {"string_value": '{"temp": 15}'}},
        ],
        "status": {"code": 1},
    }


def ingest_payload_sync(payload, organization_id, user_id, workspace_id=None):
    """Run the full OTLP ingestion pipeline synchronously (no Redis/Celery)."""
    otel_data_list = _parse_otel_request(payload)
    if not otel_data_list:
        return []

    parsed_data_list = bulk_convert_otel_spans_to_observation_spans(
        otel_data_list, organization_id, user_id, workspace_id
    )
    if not parsed_data_list:
        return []

    all_traces = _fetch_or_create_traces(parsed_data_list)
    all_end_users, _ch_end_users = _resolve_end_user_ids(
        parsed_data_list, organization_id
    )
    all_sessions, _ch_sessions = _resolve_session_ids(parsed_data_list)
    all_prompt_versions = _fetch_prompt_versions(parsed_data_list, organization_id)

    observation_spans, traces_to_update = _prepare_observation_spans_and_trace_updates(
        parsed_data_list,
        all_traces,
        all_sessions,
        all_end_users,
        all_prompt_versions,
        organization_id,
    )
    _bulk_insert_observation_spans(observation_spans)
    _bulk_update_traces(traces_to_update, all_traces)

    return parsed_data_list


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fi_project(db, organization, workspace):
    """Project for FI SDK E2E tests."""
    return Project.objects.create(
        name="fi-sdk-e2e-test",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


# ---------------------------------------------------------------------------
# Synthetic E2E Tests (no real LLM calls, always run)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
class TestFiSdkSyntheticE2E:
    """E2E tests using synthetic OTLP payloads (no API key needed)."""

    def test_simple_chat(self, fi_project, organization, user):
        """Single LLM span → Trace + ObservationSpan created."""
        trace_id = uuid.uuid4().hex
        span = _make_llm_span(trace_id, model="gpt-4o-mini")
        payload = _make_otlp_payload(fi_project.name, [span])

        ingest_payload_sync(payload, organization.id, user.id)

        # Verify trace
        trace = Trace.no_workspace_objects.filter(project=fi_project).first()
        assert trace is not None

        # Verify observation span
        obs = ObservationSpan.no_workspace_objects.filter(project=fi_project).first()
        assert obs is not None
        assert obs.observation_type == "llm"
        assert obs.model == "gpt-4o-mini"
        assert obs.prompt_tokens == 10
        assert obs.completion_tokens == 5

    def test_tool_calling(self, fi_project, organization, user):
        """LLM span + tool span → both created with parent linkage."""
        trace_id = uuid.uuid4().hex
        llm_span_id = uuid.uuid4().hex
        tool_span_id = uuid.uuid4().hex

        llm_span = _make_llm_span(trace_id, span_id=llm_span_id)
        tool_span = _make_tool_span(
            trace_id, span_id=tool_span_id, parent_span_id=llm_span_id
        )

        payload = _make_otlp_payload(fi_project.name, [llm_span, tool_span])
        ingest_payload_sync(payload, organization.id, user.id)

        spans = ObservationSpan.no_workspace_objects.filter(project=fi_project)
        assert spans.count() == 2

        tool = spans.filter(observation_type="tool").first()
        assert tool is not None
        assert str(tool.parent_span_id) == llm_span_id

    def test_multi_turn(self, fi_project, organization, user):
        """Multiple LLM spans in a single trace (multi-turn conversation)."""
        trace_id = uuid.uuid4().hex
        span1 = _make_llm_span(
            trace_id,
            input_messages=[
                {"role": "user", "content": "Hi"},
            ],
            output_content="Hello!",
            name="turn-1",
        )
        span2 = _make_llm_span(
            trace_id,
            input_messages=[
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
                {"role": "user", "content": "How are you?"},
            ],
            output_content="I'm doing well!",
            name="turn-2",
        )

        payload = _make_otlp_payload(fi_project.name, [span1, span2])
        ingest_payload_sync(payload, organization.id, user.id)

        traces = Trace.no_workspace_objects.filter(project=fi_project)
        assert traces.count() == 1  # Same trace_id

        spans = ObservationSpan.no_workspace_objects.filter(project=fi_project)
        assert spans.count() == 2

    def test_project_assignment(self, fi_project, organization, user):
        """Trace is assigned to the correct project."""
        trace_id = uuid.uuid4().hex
        span = _make_llm_span(trace_id)
        payload = _make_otlp_payload(fi_project.name, [span])

        ingest_payload_sync(payload, organization.id, user.id)

        trace = Trace.no_workspace_objects.filter(project=fi_project).first()
        assert trace is not None
        assert trace.project_id == fi_project.id

    def test_eval_attributes_structure(self, fi_project, organization, user):
        """eval_attributes has the expected fi.* keys."""
        trace_id = uuid.uuid4().hex
        span = _make_llm_span(
            trace_id, model="gpt-4o-mini", prompt_tokens=22, completion_tokens=8
        )
        payload = _make_otlp_payload(fi_project.name, [span])

        ingest_payload_sync(payload, organization.id, user.id)

        obs = ObservationSpan.no_workspace_objects.filter(project=fi_project).first()
        ea = obs.eval_attributes or {}
        assert ea.get("fi.span.kind") == "LLM"
        assert ea.get("llm.model_name") == "gpt-4o-mini"

    def test_streaming_span(self, fi_project, organization, user):
        """Span with streaming flag set."""
        trace_id = uuid.uuid4().hex
        span = _make_llm_span(trace_id, model="gpt-4o-mini")
        # Add streaming attribute
        span["attributes"].append(
            {"key": "llm.is_streaming", "value": {"bool_value": True}}
        )
        payload = _make_otlp_payload(fi_project.name, [span])

        ingest_payload_sync(payload, organization.id, user.id)

        obs = ObservationSpan.no_workspace_objects.filter(project=fi_project).first()
        assert obs is not None
        assert obs.observation_type == "llm"

    def test_empty_payload_no_crash(self, fi_project, organization, user):
        """Payload with no spans does not crash."""
        payload = _make_otlp_payload(fi_project.name, [])
        ingest_payload_sync(payload, organization.id, user.id)

        # No traces or spans created
        assert Trace.no_workspace_objects.filter(project=fi_project).count() == 0

    def test_zero_tokens(self, fi_project, organization, user):
        """Span with zero tokens is stored correctly."""
        trace_id = uuid.uuid4().hex
        span = _make_llm_span(
            trace_id,
            model="gpt-4o-mini",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )
        payload = _make_otlp_payload(fi_project.name, [span])
        ingest_payload_sync(payload, organization.id, user.id)

        obs = ObservationSpan.no_workspace_objects.filter(project=fi_project).first()
        assert obs is not None
        assert obs.prompt_tokens == 0
        assert obs.completion_tokens == 0

    def test_error_status_span(self, fi_project, organization, user):
        """Span with error status code is ingested."""
        trace_id = uuid.uuid4().hex
        span = _make_llm_span(trace_id, model="gpt-4o-mini")
        span["status"] = {"code": 2, "message": "Something went wrong"}
        payload = _make_otlp_payload(fi_project.name, [span])

        ingest_payload_sync(payload, organization.id, user.id)

        obs = ObservationSpan.no_workspace_objects.filter(project=fi_project).first()
        assert obs is not None

    def test_nested_tool_chain(self, fi_project, organization, user):
        """LLM → Tool → LLM chain creates all 3 spans with correct hierarchy."""
        trace_id = uuid.uuid4().hex
        root_id = uuid.uuid4().hex
        tool_id = uuid.uuid4().hex
        child_id = uuid.uuid4().hex

        root_span = _make_llm_span(trace_id, span_id=root_id, name="root-llm")
        tool_span = _make_tool_span(trace_id, span_id=tool_id, parent_span_id=root_id)
        child_span = _make_llm_span(
            trace_id, span_id=child_id, parent_span_id=tool_id, name="child-llm"
        )

        payload = _make_otlp_payload(
            fi_project.name, [root_span, tool_span, child_span]
        )
        ingest_payload_sync(payload, organization.id, user.id)

        spans = ObservationSpan.no_workspace_objects.filter(project=fi_project)
        assert spans.count() == 3

        tool_obs = spans.filter(observation_type="tool").first()
        assert str(tool_obs.parent_span_id) == root_id

        child_obs = spans.get(id=child_id)
        assert str(child_obs.parent_span_id) == tool_id


# ---------------------------------------------------------------------------
# Live LLM E2E Tests (require OPENAI_API_KEY)
# ---------------------------------------------------------------------------


@pytest.mark.live_llm
@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(not HAS_OPENAI_KEY, reason="OPENAI_API_KEY not set")
class TestFiSdkLiveLLME2E:
    """E2E tests making real OpenAI calls and verifying DB records."""

    def _call_openai_and_build_payload(self, project_name, messages, **kwargs):
        """Make a real OpenAI call and build an OTLP payload from the response."""
        from openai import OpenAI

        client = OpenAI()
        model = kwargs.pop("model", "gpt-4o-mini")

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=50,
            **kwargs,
        )

        choice = response.choices[0]
        output_content = choice.message.content or ""
        usage = response.usage

        trace_id = uuid.uuid4().hex
        span = _make_llm_span(
            trace_id,
            model=response.model,
            input_messages=messages,
            output_content=output_content,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )

        payload = _make_otlp_payload(project_name, [span])
        return payload, response

    def test_live_simple_chat(self, fi_project, organization, user):
        messages = [
            {"role": "system", "content": "Reply in exactly 3 words."},
            {"role": "user", "content": "Say hello."},
        ]

        payload, response = self._call_openai_and_build_payload(
            fi_project.name, messages
        )
        ingest_payload_sync(payload, organization.id, user.id)

        obs = ObservationSpan.no_workspace_objects.filter(project=fi_project).first()
        assert obs is not None
        assert obs.observation_type == "llm"
        assert obs.prompt_tokens > 0
        assert obs.completion_tokens > 0

    def test_live_tool_calling(self, fi_project, organization, user):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ]
        messages = [{"role": "user", "content": "What's the weather in London?"}]

        payload, response = self._call_openai_and_build_payload(
            fi_project.name, messages, tools=tools, tool_choice="auto"
        )
        ingest_payload_sync(payload, organization.id, user.id)

        obs = ObservationSpan.no_workspace_objects.filter(project=fi_project).first()
        assert obs is not None
        assert obs.prompt_tokens > 0
