"""A call made with a given key uses exactly that key.

An LLM given a key (``api_key``, or a BYOK model route's credential) builds
its provider client with that key, gives it to every model-library call it
makes (plain, streamed, async, async streamed and tool calls), and runs no
final fallback model on the platform's own keys. An LLM given no key runs as
before: its provider client takes the deployment's environment key and its
model-library calls are given no key, so the library reads the environment.

A given key the call cannot use as given is refused before any model call: a
stored configuration (the library takes a key alone here), and a key alone
for a model the library sends to Vertex AI, where it would sign in with the
deployment's own Google credentials instead. No refusal path writes the key
to a log record or into an error's text.

The deployment's environment holds a provider key for every provider and
there is no gateway. Only the provider clients, the model library, the price
list, the usage log and the websocket sender are replaced; each records what
it was given. Every key is synthetic, made here.
"""

import asyncio
import logging
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from model_hub.services.model_route import ModelRoute
from model_hub.utils.model_endpoints import ModelConfigurationRefused

MODEL = "gpt-4o"
PROVIDER = "openai"
VERTEX = ModelConfigs.VERTEX_GEMINI_3_7_FLASH
MESSAGES = [{"role": "user", "content": "Is the reply polite?"}]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]
# Synthetic values: the deployment's own key, a key a caller gives, and the
# credential of a BYOK model route.
ENVIRONMENT_KEY = "sk-test-" + uuid4().hex
GIVEN_KEY = "sk-test-" + uuid4().hex
ROUTE_KEY = "sk-test-" + uuid4().hex
# The environment variables the provider clients read a key from.
ENVIRONMENT_KEY_NAMES = (
    "GORQ_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
)
# The completion entry points that call the model library.
ENTRIES = ["sync", "stream", "async", "async_stream", "tools"]
# The two ways a call goes to Vertex AI: its provider names it, or the model
# library sends its model there whatever provider the call names.
VERTEX_ROUTES = [("vertex_ai", VERTEX.model_name), (PROVIDER, VERTEX.model_name)]
# What a stored configuration's refusal says on these entry points.
STORED_CONFIGURATION_SENTENCE = (
    "This model configuration can't be used for this call yet."
)


def answer(content):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def chunk(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
    )


async def stream_of(*texts):
    for text in texts:
        yield chunk(text)


def primary_down():
    return [RuntimeError("primary model unavailable")] * llm_module.MAX_RETRIES


def free(model_name, token_usage, *args, **kwargs):
    return {
        "total_cost": 0,
        "prompt_cost": 0,
        "completion_cost": 0,
        "pricing_source": "available_models",
    }


@pytest.fixture(autouse=True)
def deployment(monkeypatch, settings):
    """The deployment's own provider keys in the environment, and no gateway
    (no license key and no internal key)."""
    for name in ENVIRONMENT_KEY_NAMES:
        monkeypatch.setenv(name, ENVIRONMENT_KEY)
    settings.EE_LICENSE_KEY = ""
    settings.AGENTCC_INTERNAL_API_KEY = ""


@pytest.fixture
def vertex(monkeypatch):
    """The deployment's own Google credentials as the Vertex client needs
    them (a synthetic path that nothing reads), and that client replaced."""
    monkeypatch.setenv(
        "GOOGLE_APPLICATION_CREDENTIALS", "/fixture/service-account-key.json"
    )
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "fixture-project")
    monkeypatch.setattr(llm_module, "genai", MagicMock())
    monkeypatch.setattr(llm_module, "HttpOptions", MagicMock())


@pytest.fixture
def logs(caplog):
    """Every log record, structlog's included: routed to the standard library
    and not cached, as the backend's own test setup configures it."""
    from tfc.logging.config import configure_structlog

    configure_structlog(cache_logger_on_first_use=False)
    caplog.set_level(logging.DEBUG)
    return caplog


@pytest.fixture
def clients(monkeypatch):
    """The provider clients the LLM builds; each records what it was built
    with."""
    built = MagicMock()
    monkeypatch.setattr(llm_module, "OpenAI", built.OpenAI)
    monkeypatch.setattr(llm_module, "AsyncOpenAI", built.AsyncOpenAI)
    monkeypatch.setattr(llm_module, "AsyncAnthropic", built.AsyncAnthropic)
    monkeypatch.setattr(
        llm_module, "anthropic", SimpleNamespace(Client=built.Anthropic)
    )
    return built


@pytest.fixture
def library(monkeypatch):
    """The model library (each call records what it was given), the price
    list, the usage log and the websocket sender."""
    models = MagicMock()
    models.acompletion = AsyncMock()
    monkeypatch.setattr(llm_module, "litellm", models)
    monkeypatch.setattr(llm_module, "calculate_total_cost", free)
    monkeypatch.setattr(llm_module, "RETRY_DELAY", 0)
    monkeypatch.setattr(llm_module, "log_to_clickhouse", lambda row: None)
    monkeypatch.setattr(llm_module, "call_websocket", MagicMock())
    return models


def make_llm(provider=PROVIDER, model_name=MODEL, **kwargs):
    """An LLM built by its own constructor, with no gateway client."""
    llm = LLM(
        provider=provider,
        model_name=model_name,
        temperature=0,
        max_tokens=256,
        **kwargs,
    )
    llm._gateway_client = None
    llm._async_gateway_client = None
    return llm


def built_with(clients, name):
    """The key each provider client of class ``name`` was built with."""
    calls = getattr(clients, name).call_args_list
    return [call.kwargs.get("api_key") for call in calls]


def library_call(library, entry):
    return library.acompletion if entry.startswith("async") else library.completion


def sent(library, entry):
    """What each model-library call made through ``entry`` was given."""
    return [call.kwargs for call in library_call(library, entry).call_args_list]


def credentials_in(kwargs):
    """The arguments of one model-library call that carry a credential, or
    say where one is sent: the key, any name ending in ``_key``, ``_secret``,
    ``_token`` or ``_credentials``, any ``aws_`` name, a base URL and
    headers."""
    return {
        name: value
        for name, value in kwargs.items()
        if name in ("api_key", "api_base", "base_url", "extra_headers", "headers")
        or name.startswith("aws_")
        or name.endswith(("_key", "_secret", "_token", "_credentials"))
    }


def error_texts(error):
    """Everything an error says: its text and its traceback, with every error
    in its chain."""
    return "".join(traceback.format_exception(error)) + repr(error)


def complete(llm, library, entry):
    """One completion through ``entry``; the model library answers
    "polite"."""
    if entry == "sync":
        library.completion.return_value = answer("polite")
        return llm._get_completion_content(messages=MESSAGES)
    if entry == "stream":
        library.completion.return_value = iter([chunk("pol"), chunk("ite")])
        return llm._get_completion_content(
            messages=MESSAGES,
            streaming=True,
            type="fixture",
            uuid="fixture-uuid",
            organization_id="fixture-org",
        )
    if entry == "async":
        library.acompletion.return_value = answer("polite")
        return asyncio.run(llm._get_completion_content_async(messages=MESSAGES))
    if entry == "async_stream":
        library.acompletion.return_value = stream_of("pol", "ite")
        ws_manager = MagicMock(_send_to_channel=AsyncMock())
        return asyncio.run(
            llm._get_completion_content_async(
                messages=MESSAGES,
                streaming=True,
                type="fixture",
                uuid="fixture-uuid",
                ws_manager=ws_manager,
            )
        )
    library.completion.return_value = answer("polite")
    response = llm._get_completion_with_tools(messages=MESSAGES, tools=TOOLS)
    return response.choices[0].message.content


def fail_over(llm, library, entry):
    """One completion through ``entry`` whose requested model fails every
    attempt, and whose final fallback models would answer."""
    library_call(library, entry).side_effect = [
        *primary_down(),
        answer("fallback answer"),
        answer("second fallback answer"),
    ]
    if entry == "async":
        return asyncio.run(llm._get_completion_content_async(messages=MESSAGES))
    return llm._get_completion_content(messages=MESSAGES)


@pytest.mark.parametrize(
    ("provider", "client", "async_client"),
    [
        ("openai", "OpenAI", "AsyncOpenAI"),
        ("anthropic", "Anthropic", "AsyncAnthropic"),
        ("openrouter", "OpenAI", "AsyncOpenAI"),
        ("xai", "OpenAI", "AsyncOpenAI"),
    ],
)
def test_the_provider_client_is_built_with_the_given_key(
    clients, provider, client, async_client
):
    llm = make_llm(provider=provider, api_key=GIVEN_KEY)

    assert llm.provider_api_keys[provider] == GIVEN_KEY
    assert built_with(clients, client) == [GIVEN_KEY]
    assert built_with(clients, async_client) == [GIVEN_KEY]


def test_without_a_key_the_provider_client_is_built_with_the_environment_key(clients):
    llm = make_llm()

    assert llm.provider_api_keys[PROVIDER] == ENVIRONMENT_KEY
    assert built_with(clients, "OpenAI") == [ENVIRONMENT_KEY]
    assert built_with(clients, "AsyncOpenAI") == [ENVIRONMENT_KEY]


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_completion_made_with_a_given_key_carries_exactly_that_key(
    clients, library, entry
):
    llm = make_llm(api_key=GIVEN_KEY)

    assert complete(llm, library, entry) == "polite"

    calls = sent(library, entry)
    assert [kwargs.get("api_key") for kwargs in calls] == [GIVEN_KEY]
    # The key is the call's only credential: given no key, the library reads
    # the deployment's environment key instead.
    assert [credentials_in(kwargs) for kwargs in calls] == [{"api_key": GIVEN_KEY}]
    assert calls[0].get("stream", False) is ("stream" in entry)


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_byok_route_credential_is_what_the_model_call_carries(
    clients, library, entry
):
    route = ModelRoute(mode="byok", provider=PROVIDER, credential=ROUTE_KEY)
    llm = make_llm(model_route=route)

    assert complete(llm, library, entry) == "polite"

    calls = sent(library, entry)
    assert [kwargs.get("api_key") for kwargs in calls] == [ROUTE_KEY]
    assert [credentials_in(kwargs) for kwargs in calls] == [{"api_key": ROUTE_KEY}]
    assert built_with(clients, "OpenAI") == [ROUTE_KEY]


@pytest.mark.parametrize("entry", ENTRIES)
def test_without_a_key_a_completion_is_given_no_key(clients, library, entry):
    llm = make_llm()

    assert complete(llm, library, entry) == "polite"

    assert ["api_key" in kwargs for kwargs in sent(library, entry)] == [False]


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_given_configuration_is_refused_before_any_model_call(
    clients, library, entry
):
    llm = make_llm(api_key={"key": GIVEN_KEY, "organization": "fixture-org"})

    with pytest.raises(ModelConfigurationRefused) as refused:
        complete(llm, library, entry)

    assert sent(library, entry) == []
    assert str(refused.value) == STORED_CONFIGURATION_SENTENCE


@pytest.mark.parametrize(("provider", "model_name"), VERTEX_ROUTES)
@pytest.mark.parametrize("entry", ENTRIES)
def test_a_given_key_for_a_vertex_model_is_refused_before_any_model_call(
    clients, library, vertex, entry, provider, model_name
):
    llm = make_llm(provider=provider, model_name=model_name, api_key=GIVEN_KEY)

    with pytest.raises(ModelConfigurationRefused) as refused:
        complete(llm, library, entry)

    assert sent(library, entry) == []
    assert GIVEN_KEY not in error_texts(refused.value)


@pytest.mark.parametrize("entry", ENTRIES)
def test_without_a_key_a_vertex_model_is_called_as_before(
    clients, library, vertex, entry
):
    llm = make_llm(provider="vertex_ai", model_name=VERTEX.model_name)

    assert complete(llm, library, entry) == "polite"

    calls = sent(library, entry)
    assert [kwargs["model"] for kwargs in calls] == [VERTEX.model_name]
    assert [credentials_in(kwargs) for kwargs in calls] == [{}]


@pytest.mark.parametrize("entry", ["sync", "async"])
def test_a_call_made_with_a_given_key_runs_no_final_fallback_model(
    clients, library, entry
):
    llm = make_llm(api_key=GIVEN_KEY)

    with pytest.raises(ValueError, match="FAILED_TO_PROCESS_EVALUATION") as failed:
        fail_over(llm, library, entry)

    calls = sent(library, entry)
    retries = llm_module.MAX_RETRIES
    assert [kwargs["model"] for kwargs in calls] == [MODEL] * retries
    assert [kwargs.get("api_key") for kwargs in calls] == [GIVEN_KEY] * retries
    assert "primary model unavailable" in str(failed.value.__context__)


@pytest.mark.parametrize("entry", ["sync", "async"])
def test_without_a_key_the_final_fallback_model_still_answers(clients, library, entry):
    llm = make_llm()

    assert fail_over(llm, library, entry) == "fallback answer"

    calls = sent(library, entry)
    expected = [MODEL] * llm_module.MAX_RETRIES + [VERTEX.model_name]
    assert [kwargs["model"] for kwargs in calls] == expected
    assert not any("api_key" in kwargs for kwargs in calls)


def test_no_refusal_path_writes_the_given_key_to_a_log_or_an_error(
    clients, library, vertex, logs
):
    errors = []
    stored = make_llm(api_key={"key": GIVEN_KEY, "organization": "fixture-org"})
    with pytest.raises(ModelConfigurationRefused) as refused:
        complete(stored, library, "sync")
    errors.append(refused.value)
    on_vertex = make_llm(
        provider="vertex_ai", model_name=VERTEX.model_name, api_key=GIVEN_KEY
    )
    with pytest.raises(ModelConfigurationRefused) as refused:
        complete(on_vertex, library, "async")
    errors.append(refused.value)
    for entry in ("sync", "async"):
        with pytest.raises(ValueError, match="FAILED_TO_PROCESS_EVALUATION") as failed:
            fail_over(make_llm(api_key=GIVEN_KEY), library, entry)
        errors.append(failed.value)

    # The records were captured: the skipped fallback is logged on each path.
    skipped = [
        record
        for record in logs.records
        if "final_fallback_skipped_for_given_key" in record.getMessage()
    ]
    assert len(skipped) == 2
    for record in logs.records:
        assert GIVEN_KEY not in record.getMessage()
        assert GIVEN_KEY not in repr(vars(record))
    for error in errors:
        assert GIVEN_KEY not in error_texts(error)
