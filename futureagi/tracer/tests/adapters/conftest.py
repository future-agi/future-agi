"""
Shared fixtures for trace adapter tests.

Provides canonical LLM call data and adapter-format attribute builders
so every adapter test operates on the same ground-truth data.
"""

import json

import pytest

# ---------------------------------------------------------------------------
# Canonical LLM call data — all adapter fixtures derive from these
# ---------------------------------------------------------------------------


@pytest.fixture
def llm_call_data():
    """Simple chat completion ground truth."""
    return {
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello in 3 words."},
        ],
        "output_message": {"role": "assistant", "content": "Hello to you!"},
        "prompt_tokens": 22,
        "completion_tokens": 4,
        "total_tokens": 26,
    }


@pytest.fixture
def tool_call_data():
    """LLM call with tool_calls in the assistant output."""
    return {
        "model": "gpt-4o",
        "provider": "openai",
        "input_messages": [
            {"role": "system", "content": "You have access to a search tool."},
            {"role": "user", "content": "What's the weather in SF?"},
        ],
        "output_message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_abc123",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"location": "San Francisco"}',
                    },
                }
            ],
        },
        "prompt_tokens": 50,
        "completion_tokens": 20,
        "total_tokens": 70,
    }


@pytest.fixture
def multi_turn_data():
    """Multi-turn conversation with system prompt."""
    return {
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_messages": [
            {"role": "system", "content": "You are a math tutor."},
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
            {"role": "user", "content": "And 3+3?"},
        ],
        "output_message": {"role": "assistant", "content": "6"},
        "prompt_tokens": 40,
        "completion_tokens": 2,
        "total_tokens": 42,
    }


# ---------------------------------------------------------------------------
# Adapter-format attribute builders
# ---------------------------------------------------------------------------


@pytest.fixture
def langfuse_generation_attrs(llm_call_data):
    """Langfuse OTLP attributes for a generation span."""
    d = llm_call_data
    return {
        "langfuse.observation.type": "generation",
        "langfuse.observation.model.name": d["model"],
        "langfuse.observation.model.parameters": json.dumps(
            {"max_tokens": 20, "temperature": 0.7}
        ),
        "langfuse.observation.usage_details": json.dumps(
            {
                "input_tokens": d["prompt_tokens"],
                "output_tokens": d["completion_tokens"],
                "total_tokens": d["total_tokens"],
            }
        ),
        "langfuse.observation.input": json.dumps(d["input_messages"]),
        "langfuse.observation.output": json.dumps(d["output_message"]),
        "langfuse.observation.metadata": json.dumps({"env": "test"}),
        "langfuse.trace.tags": json.dumps(["test", "unit"]),
        "user.id": "user-42",
        "session.id": "sess-abc",
    }


@pytest.fixture
def langfuse_chain_attrs():
    """Langfuse OTLP attributes for a chain/span type."""
    return {
        "langfuse.observation.type": "span",
        "langfuse.observation.input": json.dumps({"query": "hello"}),
        "langfuse.observation.output": json.dumps({"answer": "world"}),
        "langfuse.observation.metadata": json.dumps({"pipeline": "test"}),
        "langfuse.trace.tags": json.dumps(["chain-test"]),
    }


@pytest.fixture
def openinference_llm_attrs(llm_call_data):
    """OpenInference attributes for an LLM span (fi-native except span kind)."""
    d = llm_call_data
    return {
        "openinference.span.kind": "LLM",
        "llm.model_name": d["model"],
        "llm.provider": d["provider"],
        "llm.system": d["provider"],
        "llm.token_count.prompt": d["prompt_tokens"],
        "llm.token_count.completion": d["completion_tokens"],
        "llm.token_count.total": d["total_tokens"],
        "llm.input_messages.0.message.role": "system",
        "llm.input_messages.0.message.content": "You are a helpful assistant.",
        "llm.input_messages.1.message.role": "user",
        "llm.input_messages.1.message.content": "Say hello in 3 words.",
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": "Hello to you!",
        "input.value": json.dumps(d["input_messages"]),
        "input.mime_type": "application/json",
        "output.value": json.dumps(d["output_message"]),
        "output.mime_type": "application/json",
    }


@pytest.fixture
def openinference_retriever_attrs():
    """OpenInference attributes for a RETRIEVER span."""
    return {
        "openinference.span.kind": "RETRIEVER",
        "input.value": "What is machine learning?",
        "input.mime_type": "text/plain",
        "output.value": json.dumps([{"text": "ML is a branch of AI", "score": 0.95}]),
        "output.mime_type": "application/json",
    }


@pytest.fixture
def openllmetry_llm_attrs(llm_call_data):
    """OpenLLMetry (gen_ai.* + traceloop.*) attributes for an LLM span."""
    d = llm_call_data
    attrs = {
        "gen_ai.system": "openai",
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": d["model"],
        "gen_ai.response.model": f"{d['model']}-2024-07-18",
        "gen_ai.usage.input_tokens": d["prompt_tokens"],
        "gen_ai.usage.output_tokens": d["completion_tokens"],
        "gen_ai.request.temperature": 0.7,
        "gen_ai.request.max_tokens": 20,
        "llm.request.type": "chat",
        "llm.is_streaming": False,
    }
    # Add indexed prompt messages
    for i, msg in enumerate(d["input_messages"]):
        attrs[f"gen_ai.prompt.{i}.role"] = msg["role"]
        attrs[f"gen_ai.prompt.{i}.content"] = msg["content"]
    # Add indexed completion
    out = d["output_message"]
    attrs["gen_ai.completion.0.role"] = out["role"]
    attrs["gen_ai.completion.0.content"] = out["content"]
    return attrs


@pytest.fixture
def openllmetry_workflow_attrs():
    """OpenLLMetry attributes for a workflow (chain) span."""
    return {
        "traceloop.span.kind": "workflow",
        "traceloop.entity.name": "TestWorkflow",
        "traceloop.entity.input": json.dumps({"query": "hello"}),
        "traceloop.entity.output": json.dumps({"answer": "world"}),
        "traceloop.association.properties": json.dumps(
            {"user_id": "u-1", "session": "s-1"}
        ),
    }


@pytest.fixture
def openllmetry_tool_attrs():
    """OpenLLMetry attributes for a tool span."""
    return {
        "traceloop.span.kind": "tool",
        "traceloop.entity.name": "search_docs",
        "traceloop.entity.input": json.dumps({"query": "test", "top_k": 3}),
        "traceloop.entity.output": json.dumps({"results": ["doc1", "doc2"]}),
    }


@pytest.fixture
def fi_native_llm_attrs(llm_call_data):
    """FI-native attributes for an LLM span."""
    d = llm_call_data
    return {
        "fi.span.kind": "LLM",
        "llm.model_name": d["model"],
        "llm.provider": d["provider"],
        "llm.system": d["provider"],
        "llm.token_count.prompt": d["prompt_tokens"],
        "llm.token_count.completion": d["completion_tokens"],
        "llm.token_count.total": d["total_tokens"],
        "llm.invocation_parameters": json.dumps({"temperature": 0.7, "max_tokens": 20}),
        "input.value": json.dumps(d["input_messages"]),
        "input.mime_type": "application/json",
        "output.value": json.dumps(d["output_message"]),
        "output.mime_type": "application/json",
    }


@pytest.fixture
def fi_native_chain_attrs():
    """FI-native attributes for a CHAIN span with input/output."""
    return {
        "fi.span.kind": "CHAIN",
        "input.value": json.dumps({"query": "hello world"}),
        "input.mime_type": "application/json",
        "output.value": json.dumps({"answer": "hi there"}),
        "output.mime_type": "application/json",
    }


@pytest.fixture
def fi_native_chain_no_io_attrs():
    """FI-native CHAIN span without input/output (manual instrumentation)."""
    return {
        "fi.span.kind": "CHAIN",
    }


@pytest.fixture
def fi_native_legacy_io_attrs():
    """FI-native span using legacy fi.llm.input/fi.llm.output keys."""
    return {
        "fi.span.kind": "CHAIN",
        "fi.llm.input": json.dumps({"query": "test input"}),
        "fi.llm.output": json.dumps({"result": "test output"}),
    }
