#!/usr/bin/env python
"""
Populate test data for the list_sessions API.

Creates an observe project with complex traces, spans, sessions, and user IDs
to test the ClickHouse session list query optimizations (count skip, uniq,
root-span attributes, key cap).

Usage:
    # Against local backend (http://localhost:8000):
    cd futureagi
    uv run python scripts/populate_session_test_data.py

    # Against custom endpoint:
    FI_BASE_URL=http://localhost:8000 uv run python scripts/populate_session_test_data.py

Environment variables:
    FI_BASE_URL     - Backend URL (default: http://localhost:8000)
    FI_API_KEY      - API key for authentication
    FI_SECRET_KEY   - Secret key for authentication
    OPENAI_API_KEY  - (Optional) If set, uses real OpenAI calls; otherwise mocks them

What it creates:
    - 1 observe project: "session-perf-test"
    - 10 sessions, each with 3-8 traces
    - Each trace has 1 root span + 2-5 child spans (LLM, tool, retriever)
    - 5 distinct end users distributed across sessions
    - Custom span attributes on root spans (environment, version, region, etc.)
    - Total: ~50 traces, ~250 spans across 10 sessions
"""

import os
import random
import time
import uuid
from datetime import datetime, timedelta

# Point to local backend
os.environ.setdefault("FI_BASE_URL", "http://localhost:8000")
os.environ.setdefault("FI_API_KEY", os.environ.get("FI_API_KEY", ""))
os.environ.setdefault("FI_SECRET_KEY", os.environ.get("FI_SECRET_KEY", ""))

from fi_instrumentation import (
    HTTPSpanExporter,
    using_attributes,
    using_session,
    using_user,
)
from fi_instrumentation.fi_types import ProjectType
from fi_instrumentation.otel import (
    PROJECT_NAME,
    PROJECT_TYPE,
    PROJECT_VERSION_NAME,
)
from fi_instrumentation.settings import UuidIdGenerator, get_env_collector_endpoint
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.id_generator import IdGenerator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_NAME_VAL = "session-perf-test"
NUM_SESSIONS = 10
TRACES_PER_SESSION = (3, 8)  # random range
CHILD_SPANS_PER_TRACE = (2, 5)  # random range
NUM_USERS = 5

USERS = [{"id": f"user-{i}@example.com", "type": "email"} for i in range(NUM_USERS)]

MODELS = ["gpt-4o", "gpt-4o-mini", "claude-3-sonnet", "claude-3-haiku", "gemini-pro"]
ENVIRONMENTS = ["production", "staging", "development"]
REGIONS = ["us-east-1", "eu-west-1", "ap-southeast-1"]
VERSIONS = ["v2.1.0", "v2.2.0", "v2.3.0", "v3.0.0-beta"]
SPAN_TYPES = ["llm", "tool", "retriever", "chain", "embedding"]
TOOL_NAMES = ["web_search", "calculator", "database_query", "file_reader", "api_call"]


# ---------------------------------------------------------------------------
# Setup tracer provider pointing to local backend
# ---------------------------------------------------------------------------


def create_tracer_provider() -> TracerProvider:
    """Create a TracerProvider configured to send to the local backend."""
    endpoint = get_env_collector_endpoint()
    # Ensure the endpoint includes the trace ingestion path
    if not endpoint.endswith("/tracer/v1/traces"):
        endpoint = f"{endpoint}/tracer/v1/traces"

    resource = Resource(
        attributes={
            PROJECT_NAME: PROJECT_NAME_VAL,
            PROJECT_TYPE: ProjectType.OBSERVE.value,
            PROJECT_VERSION_NAME: "",
        }
    )

    provider = TracerProvider(
        resource=resource,
        id_generator=UuidIdGenerator(),
    )

    exporter = HTTPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(SimpleSpanProcessor(span_exporter=exporter))

    return provider


# ---------------------------------------------------------------------------
# Data generation helpers
# ---------------------------------------------------------------------------


def random_input():
    """Generate a random user message."""
    messages = [
        "What's the weather like today?",
        "Summarize the latest news about AI",
        "Help me write a Python function to sort a list",
        "Explain quantum computing in simple terms",
        "What are the best practices for REST API design?",
        "Generate a SQL query to find top customers",
        "How do I deploy a Docker container to AWS?",
        "Write a unit test for this function",
        "Compare React vs Vue for a new project",
        "What's the difference between TCP and UDP?",
    ]
    return random.choice(messages)


def random_output():
    """Generate a random LLM response."""
    responses = [
        "Here's a detailed explanation of the topic you asked about...",
        "Based on my analysis, I recommend the following approach...",
        "The function you need can be implemented as follows...",
        "Let me break this down into simple steps for you...",
        "After searching the available data, here are the results...",
    ]
    return random.choice(responses)


def create_session_traces(tracer, session_id: str, user: dict, num_traces: int):
    """Create multiple traces within a session, each with nested spans."""
    for trace_idx in range(num_traces):
        create_single_trace(tracer, session_id, user, trace_idx)


def create_single_trace(tracer, session_id: str, user: dict, trace_idx: int):
    """Create a single trace with root span and child spans."""
    env = random.choice(ENVIRONMENTS)
    region = random.choice(REGIONS)
    version = random.choice(VERSIONS)
    model = random.choice(MODELS)

    with using_attributes(session_id=session_id, user_id=user["id"]):
        # Root span (agent/chain type) with custom attributes
        with tracer.start_as_current_span(
            f"agent-run-{trace_idx}",
            attributes={
                "gen_ai.span.kind": "chain",
                "environment": env,
                "deployment.region": region,
                "app.version": version,
                "user.plan": random.choice(["free", "pro", "enterprise"]),
                "request.priority": random.choice(["low", "medium", "high"]),
            },
        ) as root_span:
            root_span.set_attribute("input.value", random_input())

            # Simulate processing time
            num_children = random.randint(*CHILD_SPANS_PER_TRACE)

            for child_idx in range(num_children):
                span_type = random.choice(SPAN_TYPES)
                create_child_span(tracer, span_type, model, child_idx)

            root_span.set_attribute("output.value", random_output())


def create_child_span(tracer, span_type: str, model: str, child_idx: int):
    """Create a child span of a specific type."""
    if span_type == "llm":
        with tracer.start_as_current_span(
            f"llm-call-{child_idx}",
            attributes={
                "gen_ai.span.kind": "llm",
                "gen_ai.request.model": model,
                "gen_ai.usage.input_tokens": random.randint(50, 2000),
                "gen_ai.usage.output_tokens": random.randint(20, 1000),
                "llm.input_messages": f'[{{"role": "user", "content": "{random_input()}"}}]',
                "llm.output_messages": f'[{{"role": "assistant", "content": "{random_output()}"}}]',
            },
        ) as span:
            # Simulate some nested tool calls within LLM
            if random.random() > 0.7:
                with tracer.start_as_current_span(
                    f"tool-{random.choice(TOOL_NAMES)}",
                    attributes={
                        "gen_ai.span.kind": "tool",
                        "tool.name": random.choice(TOOL_NAMES),
                    },
                ):
                    pass

    elif span_type == "tool":
        tool_name = random.choice(TOOL_NAMES)
        with tracer.start_as_current_span(
            f"tool-{tool_name}-{child_idx}",
            attributes={
                "gen_ai.span.kind": "tool",
                "tool.name": tool_name,
                "tool.parameters": f'{{"query": "{random_input()}"}}',
            },
        ):
            pass

    elif span_type == "retriever":
        with tracer.start_as_current_span(
            f"retriever-{child_idx}",
            attributes={
                "gen_ai.span.kind": "retriever",
                "retrieval.documents_count": random.randint(1, 10),
                "retrieval.source": random.choice(["pinecone", "weaviate", "chroma"]),
            },
        ):
            pass

    elif span_type == "embedding":
        with tracer.start_as_current_span(
            f"embedding-{child_idx}",
            attributes={
                "gen_ai.span.kind": "embedding",
                "gen_ai.request.model": "text-embedding-3-small",
                "embedding.dimensions": 1536,
            },
        ):
            pass

    else:  # chain
        with tracer.start_as_current_span(
            f"chain-step-{child_idx}",
            attributes={
                "gen_ai.span.kind": "chain",
            },
        ):
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print(f"{'='*60}")
    print(f"  Populating session test data for: {PROJECT_NAME_VAL}")
    print(f"  Backend: {get_env_collector_endpoint()}")
    print(f"  Sessions: {NUM_SESSIONS}")
    print(f"  Users: {NUM_USERS}")
    print(f"{'='*60}\n")

    # Verify credentials
    api_key = os.environ.get("FI_API_KEY")
    secret_key = os.environ.get("FI_SECRET_KEY")
    if not api_key or not secret_key:
        print("ERROR: FI_API_KEY and FI_SECRET_KEY must be set.")
        print("  Get these from your Future AGI dashboard or local admin.")
        print("  Example:")
        print("    export FI_API_KEY=your-api-key")
        print("    export FI_SECRET_KEY=your-secret-key")
        return

    # Create tracer provider
    provider = create_tracer_provider()
    tracer = provider.get_tracer("session-perf-test")

    total_traces = 0
    total_spans = 0

    for session_idx in range(NUM_SESSIONS):
        session_id = f"session-{session_idx:03d}-{uuid.uuid4().hex[:8]}"
        user = USERS[session_idx % NUM_USERS]
        num_traces = random.randint(*TRACES_PER_SESSION)

        print(f"  Session {session_idx+1}/{NUM_SESSIONS}: {session_id}")
        print(f"    User: {user['id']}, Traces: {num_traces}")

        create_session_traces(tracer, session_id, user, num_traces)
        total_traces += num_traces

        # Small delay between sessions to avoid overwhelming the backend
        time.sleep(0.2)

    # Shutdown to flush all spans
    print(f"\n  Flushing spans to backend...")
    provider.shutdown()

    print(f"\n{'='*60}")
    print(f"  Done!")
    print(f"  Project: {PROJECT_NAME_VAL}")
    print(f"  Sessions created: {NUM_SESSIONS}")
    print(f"  Total traces: ~{total_traces}")
    print(f"  Users: {NUM_USERS}")
    print(f"{'='*60}")
    print(f"\n  Now test the list_sessions API:")
    print(
        f"  GET /tracer/trace-session/list_sessions/?project_id=<id>&page_number=0&page_size=30"
    )


if __name__ == "__main__":
    main()
