"""No credential reaches an exported span (tfc.telemetry.credentials).

Model-library instrumentors record a call's own arguments on its span:
traceai_litellm writes every keyword argument of ``litellm.completion``,
``acompletion`` and ``completion_with_retries`` to ``raw.input``, and every
one but the model and the messages to ``llm.invocation_parameters``. The
export boundary masks the value of every name like a credential at any depth
(keeping the name and every other value), and withholds call arguments that
do not parse. The real instrumentor runs here, through the processor chain
``build_span_processor`` builds, to an in-memory exporter; the model library
answers from ``mock_response`` and calls no provider. Every key is synthetic,
made here.
"""

import asyncio
import json
from unittest.mock import MagicMock
from uuid import uuid4

import litellm
import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tfc.telemetry.config import build_span_processor

MODEL = "gpt-4o-mini"
MESSAGES = [{"role": "user", "content": "Is the reply polite?"}]
LIBRARY_CALLS = ["completion", "acompletion", "completion_with_retries"]


def synthetic():
    return "sk-test-" + uuid4().hex


def exported_text(spans):
    """Every name and value the spans export, as one text."""
    return repr(
        [
            (
                span.name,
                dict(span.attributes or {}),
                [(event.name, dict(event.attributes or {})) for event in span.events],
                [dict(link.attributes or {}) for link in span.links],
                span.status.description,
                dict(span.resource.attributes),
            )
            for span in spans
        ]
    )


def deployed_chain():
    """A provider whose spans pass through the processor chain the deployment
    builds (build_span_processor), to an in-memory exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        build_span_processor(exporter, use_batch_processor=False)
    )
    return provider, exporter


@pytest.fixture(scope="module")
def instrumented():
    """The real LiteLLM instrumentor, sending its spans through the deployed
    chain; instrumented once for this module and uninstrumented after it."""
    from opentelemetry import trace
    from traceai_litellm import LiteLLMInstrumentor

    provider, exporter = deployed_chain()
    instrumentor = LiteLLMInstrumentor()
    already = instrumentor.is_instrumented_by_opentelemetry
    if already:
        instrumentor.uninstrument()
    instrumentor.instrument(tracer_provider=provider, raise_exception_on_conflict=True)
    try:
        yield exporter
    finally:
        instrumentor.uninstrument()
        if already:
            instrumentor.instrument(tracer_provider=trace.get_tracer_provider())
        provider.shutdown()


def call(kind, **arguments):
    """One model-library call of ``kind``, answered by ``mock_response``."""
    arguments = {"model": MODEL, "messages": MESSAGES, **arguments}
    if kind == "acompletion":
        return asyncio.run(litellm.acompletion(**arguments))
    return getattr(litellm, kind)(**arguments)


@pytest.mark.unit
def test_the_processor_masks_every_credential_and_keeps_every_other_value():
    from tfc.telemetry.credentials import (
        MASKED,
        CredentialMaskingSpanProcessor,
        mask_span,
    )

    key, secret = synthetic(), synthetic()
    arguments = {
        "model": MODEL,
        "messages": MESSAGES,
        "max_tokens": 256,
        "api_key": key,
        "aws_secret_access_key": secret,
        "extra_headers": {"AUTHORIZATION": "Bearer " + key, "X-Trace": "kept"},
        "metadata": {"payload": [{"Api_Key": key, "note": "kept"}]},
    }
    parameters = {
        name: value
        for name, value in arguments.items()
        if name not in ("model", "messages")
    }
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        CredentialMaskingSpanProcessor(SimpleSpanProcessor(exporter))
    )
    try:
        tracer = provider.get_tracer("credential-unit")
        with tracer.start_as_current_span("completion") as span:
            span.set_attribute("raw.input", json.dumps(arguments))
            span.set_attribute("llm.invocation_parameters", json.dumps(parameters))
            span.set_attribute("llm.model_name", MODEL)
            span.set_attribute("input.value", "Is the reply polite?")
            span.set_attribute("http.request.header.x-api-key", key)
            span.add_event("retry", {"raw.input": json.dumps(arguments)})
        (exported,) = exporter.get_finished_spans()
    finally:
        provider.shutdown()

    text = exported_text([exported])
    assert key not in text and secret not in text
    expected = {
        **arguments,
        "api_key": MASKED,
        "aws_secret_access_key": MASKED,
        "extra_headers": {"AUTHORIZATION": MASKED, "X-Trace": "kept"},
        "metadata": {"payload": [{"Api_Key": MASKED, "note": "kept"}]},
    }
    assert json.loads(exported.attributes["raw.input"]) == expected
    assert json.loads(exported.attributes["llm.invocation_parameters"]) == {
        name: value
        for name, value in expected.items()
        if name not in ("model", "messages")
    }
    assert json.loads(exported.events[0].attributes["raw.input"]) == expected
    assert exported.attributes["llm.model_name"] == MODEL
    assert exported.attributes["input.value"] == "Is the reply polite?"
    assert exported.attributes["http.request.header.x-api-key"] == MASKED
    # A span with nothing to mask is passed on as it is.
    unrelated = ReadableSpan(
        name="completion",
        attributes={
            "raw.input": json.dumps({"model": MODEL, "messages": MESSAGES}),
            "input.value": "[INST] Is the reply polite? [/INST]",
        },
    )
    assert mask_span(unrelated) is unrelated


@pytest.mark.integration
@pytest.mark.parametrize("kind", LIBRARY_CALLS)
def test_no_credential_given_to_the_model_library_reaches_an_exported_span(
    instrumented, kind
):
    key, payload_key, header_key = synthetic(), synthetic(), synthetic()
    instrumented.clear()

    result = call(
        kind,
        api_key=key,
        mock_response="ok",
        metadata={"payload": {"api_key": payload_key, "note": "kept"}},
        extra_headers={"Authorization": "Bearer " + header_key, "X-Trace": "kept"},
    )

    assert result.choices[0].message.content == "ok"
    spans = instrumented.get_finished_spans()
    text = exported_text(spans)
    for value in (key, payload_key, header_key):
        assert value not in text
    # The instrumentor recorded this call, with everything but the
    # credentials as it was given.
    (recorded,) = [span for span in spans if span.name == kind]
    raw = json.loads(recorded.attributes["raw.input"])
    parameters = json.loads(recorded.attributes["llm.invocation_parameters"])
    assert raw["model"] == MODEL and raw["messages"] == MESSAGES
    from tfc.telemetry.credentials import MASKED

    for given in (raw, parameters):
        assert given["mock_response"] == "ok"
        assert given["api_key"] == MASKED
        assert given["metadata"] == {"payload": {"api_key": MASKED, "note": "kept"}}
        assert given["extra_headers"] == {"Authorization": MASKED, "X-Trace": "kept"}


@pytest.mark.integration
def test_call_arguments_that_do_not_parse_are_withheld_whole():
    key = synthetic()
    whole = json.dumps({"model": MODEL, "messages": MESSAGES, "api_key": key})
    # Cut short inside the key, as an attribute length limit would cut it.
    cut_short = whole[: whole.index(key) + len(key) // 2]
    provider, exporter = deployed_chain()
    try:
        tracer = provider.get_tracer("credential-export")
        with tracer.start_as_current_span("completion") as span:
            span.set_attribute("raw.input", cut_short)
            # A dict's repr: not JSON.
            span.set_attribute("llm.invocation_parameters", str({"api_key": key}))
            span.set_attribute(
                "input.value", '{"extra_headers": {"Authorization": "Bearer ' + key
            )
            span.set_attribute("output.value", "[INST] plain text [/INST]")
            span.set_attribute("llm.model_name", MODEL)
        (exported,) = exporter.get_finished_spans()
    finally:
        provider.shutdown()

    text = exported_text([exported])
    assert key[: len(key) // 2] not in text
    from tfc.telemetry.credentials import UNCHECKED

    for name in ("raw.input", "llm.invocation_parameters", "input.value"):
        assert exported.attributes[name] == UNCHECKED, name
    assert exported.attributes["output.value"] == "[INST] plain text [/INST]"
    assert exported.attributes["llm.model_name"] == MODEL


@pytest.mark.integration
def test_init_telemetry_installs_the_credential_boundary(monkeypatch):
    from opentelemetry import trace

    from tfc.telemetry import config

    key = synthetic()
    exporter = InMemorySpanExporter()
    installed = []
    monkeypatch.setattr(config, "OTEL_ENABLED", True)
    monkeypatch.setattr(config, "_initialized", False)
    monkeypatch.setattr(config, "_tracer_provider", None)
    monkeypatch.setattr(config, "_create_exporter", lambda: exporter)
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    provider = config.init_telemetry(
        component="credential-export-test", use_batch_processor=False
    )
    assert installed == [provider]
    try:
        tracer = provider.get_tracer("credential-export")
        with tracer.start_as_current_span("completion") as span:
            span.set_attribute("raw.input", json.dumps({"api_key": key}))
        (exported,) = exporter.get_finished_spans()
    finally:
        provider.shutdown()
    assert key not in exported_text([exported])


@pytest.mark.integration
def test_the_observe_project_exports_through_the_credential_boundary(monkeypatch):
    from agentic_eval.observability import project_registrations

    provider = MagicMock()
    batches = []
    monkeypatch.setenv("FI_API_KEY", "fixture-observe-key")
    monkeypatch.setenv("FI_SECRET_KEY", "fixture-observe-secret")
    monkeypatch.setattr(project_registrations, "load_dotenv", lambda: None)
    monkeypatch.setattr(project_registrations, "register", lambda **kw: provider)
    monkeypatch.setattr(
        project_registrations,
        "BatchSpanProcessor",
        lambda **kw: batches.append(kw) or "the observe exporting processor",
        raising=False,
    )

    assert project_registrations.register_project("fixture-project") is provider

    added = [call.args[0] for call in provider.add_span_processor.call_args_list]
    assert len(added) == 1, "the observe project's exporter is not behind the boundary"
    from tfc.telemetry.credentials import CredentialMaskingSpanProcessor

    assert isinstance(added[0], CredentialMaskingSpanProcessor)
    assert added[0]._delegate == "the observe exporting processor"
    assert batches == [{"transport": project_registrations.Transport.HTTP}]
