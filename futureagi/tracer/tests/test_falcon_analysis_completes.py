"""A background trace analysis completes through the real model client.

The worker builds its own agent, model client, completion witness and guard.
Between the worker and the model, only the network transport is a stand-in. It
answers as the managed gateway does: one turn whose tool call submits a scored
result for the trace, then a final answer.
"""

import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from django.utils import timezone

from accounts.tests.test_workspace_landing import landing as landing
from accounts.tests.workspace_write_support import scope_no_egress as scope_no_egress
from ee.falcon_ai.models import Conversation
from model_hub.models.ai_model import AIModel
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.queries import background_analysis_state
from tracer.tasks import error_analysis

pytestmark = pytest.mark.django_db(transaction=True)

GATEWAY_URL = "http://gateway.invalid"
GATEWAY_KEY = "synthetic-gateway-key"
CHAT_URL = GATEWAY_URL + "/v1/chat/completions"
SUBMITTED_SCORE = 3.5
FINAL_ANSWER = "The agent answered the question correctly; no errors were found."


def submitted_scores(trace_id):
    """A complete scored result, as the model submits it for the trace."""
    reasons = {
        "factual_grounding": "The answer matches the question.",
        "privacy_and_safety": "No personal data appears in the spans.",
        "instruction_adherence": "The agent answered what was asked.",
        "optimal_plan_execution": "One model call produced the answer.",
    }
    scores = {
        "trace_id": trace_id,
        "overall_score": SUBMITTED_SCORE,
        "insights": "A short, correct answer with no errors.",
        "recommended_priority": "LOW",
    }
    for dimension, reason in reasons.items():
        scores[f"{dimension}_score"] = 4
        scores[f"{dimension}_reason"] = reason
    return scores


def chat_completion(message, finish_reason):
    """A non-streamed chat completion body, as the gateway returns it."""
    body = {
        "id": "completion-stand-in",
        "object": "chat.completion",
        "created": 1,
        "model": "stand-in-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }
    return httpx.Response(
        200,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )


@pytest.fixture
def trace_case(landing, scope_no_egress, monkeypatch, settings):
    project = Project._base_manager.create(
        name="Background analysis",
        organization=landing.org,
        workspace=landing.default,
        user=landing.user,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    trace = Trace._base_manager.create(project=project)
    now = timezone.now()
    root = ObservationSpan._base_manager.create(
        id=f"root_{uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="Answer question",
        observation_type="agent",
        start_time=now - timedelta(seconds=4),
        end_time=now,
        input={"question": "What is the capital of France?"},
        output={"answer": "Paris"},
        status="OK",
    )
    model_call = ObservationSpan._base_manager.create(
        id=f"llm_{uuid4().hex[:16]}",
        project=project,
        trace=trace,
        parent_span_id=root.id,
        name="Model call",
        observation_type="llm",
        start_time=now - timedelta(seconds=3),
        end_time=now - timedelta(seconds=1),
        input={"messages": [{"role": "user", "content": "Capital of France?"}]},
        output={"choices": [{"message": {"content": "Paris"}}]},
        status="OK",
    )

    # The deployment mode and gateway settings the worker reads.
    monkeypatch.setattr("ee.usage.deployment._detect_mode", lambda: "cloud")
    settings.AGENTCC_INTERNAL_URL = GATEWAY_URL
    settings.AGENTCC_INTERNAL_API_KEY = GATEWAY_KEY

    # The scoring tool finds the trace's root span in the span store.
    roots = {
        str(span.trace_id): (span.id, str(span.project_id))
        for span in (root, model_call)
        if not span.parent_span_id
    }
    lookups = []

    class SpanStore:
        def root_ids_by_trace_ids(self, trace_ids, project_ids=None):
            lookups.append(list(trace_ids))
            return {key: roots[key] for key in trace_ids if key in roots}

    @contextmanager
    def span_store():
        yield SpanStore()

    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", span_store)
    # The analytics export that follows a completed analysis stays in process.
    monkeypatch.setattr("tracer.services.clickhouse.client.ClickHouseClient", Mock())

    # The model gateway: every request is recorded and answered here.
    sent = []

    def gateway(request):
        sent.append(request)
        if request.method != "POST" or str(request.url) != CHAT_URL:
            return httpx.Response(404)
        messages = json.loads(request.content)["messages"]
        if messages[-1]["role"] == "tool":
            return chat_completion(
                {"role": "assistant", "content": FINAL_ANSWER}, "stop"
            )
        call = {
            "id": "call-scores",
            "type": "function",
            "function": {
                "name": "submit_trace_scores",
                "arguments": json.dumps(submitted_scores(str(trace.pk))),
            },
        }
        return chat_completion(
            {"role": "assistant", "content": None, "tool_calls": [call]},
            "tool_calls",
        )

    transport = httpx.MockTransport(gateway)
    monkeypatch.setattr(httpx.Client, "_transport_for_url", lambda self, url: transport)
    yield SimpleNamespace(
        landing=landing,
        project=project,
        trace=trace,
        sent=sent,
        lookups=lookups,
    )
    clear_workspace_context()


def test_background_analysis_completes_through_the_real_client(trace_case):
    """The model's scored result is published and the receipt reads completed."""
    case = trace_case
    trace_id = str(case.trace.pk)
    result = error_analysis._falcon_analyze_single_trace(
        trace_id,
        str(case.project.pk),
        requesting_user_id=str(case.landing.user.pk),
        organization_id=str(case.landing.org.pk),
        workspace_id=str(case.landing.default.pk),
    )

    assert result == {"success": True, "trace_id": trace_id, "error_count": 0}

    # The real client sent both model turns through the stand-in transport.
    assert [str(request.url) for request in case.sent] == [CHAT_URL, CHAT_URL]
    for request in case.sent:
        assert request.headers["authorization"] == f"Bearer {GATEWAY_KEY}"
    first, second = (json.loads(request.content) for request in case.sent)
    assert "submit_trace_scores" in {
        tool["function"]["name"] for tool in first["tools"]
    }
    assert first["messages"][-1]["role"] == "user"
    assert second["messages"][-1]["role"] == "tool"
    assert second["messages"][-1]["tool_call_id"] == "call-scores"
    # The scoring tool resolved the trace before saving its result.
    assert case.lookups == [[trace_id]]

    # Publication needs a normal final answer and the model's own scored result.
    receipt = background_analysis_state.get_state(trace_id)
    assert receipt.status == "completed"
    assert receipt.reason_code == ""
    assert receipt.project_id == case.project.pk
    analysis = TraceErrorAnalysis._base_manager.get(trace_id=trace_id)
    assert receipt.result_analysis_id == analysis.pk
    assert analysis.overall_score == SUBMITTED_SCORE
    assert analysis.recommended_priority == "LOW"

    # Readers see the published result.
    clear_workspace_context()
    assert list(TraceErrorAnalysis.objects.filter(trace_id=trace_id)) == [analysis]

    # Nothing recorded a stop on the hidden conversation.
    conversation = Conversation._base_manager.get(
        organization=case.landing.org, metadata__type="background_analysis"
    )
    assert "analysis_outcome" not in conversation.metadata
