"""Shared doubles for the one-key model route tests (no tests here).

``FakeGatewayClient`` stands in for the gateway managed client's transport:
no network, no credential. It records every create() call (its keyword
arguments, the default headers and the ``with_options`` it was made with),
so tests see exactly what a managed call would send. The gateway port is
doubled by ``model_hub.services.model_route.MockGatewayManagedRouting``.

``provider_tripwires`` puts a sentinel platform key in the environment and
records any managed call that reaches litellm or builds an OpenAI SDK client
of its own, the two ways a call could reach a provider on the platform's
keys. Tests assert the record is empty and the sentinel was never sent.
"""

from types import SimpleNamespace

# A stored BYOK provider value for fixtures; not a real provider key.
BYOK_FIXTURE_VALUE = "byok-fixture-provider-value"

# Put in the environment by ``provider_tripwires``; a managed call that sent
# it anywhere would be using the platform's own provider key.
PLATFORM_KEY_SENTINEL = "platform-env-key-sentinel"

PLATFORM_KEY_ENV_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GORQ_API_KEY",
)

LEGACY_REFUSAL = (
    "API key not configured for {provider}. Please add your API key in settings."
)

# Text a gateway or provider error carries; no user-facing message, log
# field or exception in a managed failure's chain may contain it.
PRIVATE_UPSTREAM_TEXT = "upstream said something private"

# Catalog models (agentic_eval/core_evals/run_prompt/available_models.py).
OPENAI_CHAT_MODEL = "gpt-4o-mini"  # providers=openai, mode=chat
ANTHROPIC_CHAT_MODEL = "claude-3-5-haiku-20241022"  # providers=anthropic, mode=chat
TTS_MODEL = "blizzard"  # providers=lmnt, mode=tts

MESSAGES = [{"role": "user", "content": "hello"}]


class GatewayUsage(SimpleNamespace):
    """SDK-shaped usage: both attribute access and dict(usage) are supported."""

    def __iter__(self):
        return iter(vars(self).items())


def chat_response(content="managed ok", model="gateway-model"):
    return SimpleNamespace(
        model=model,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(total_tokens=3, prompt_tokens=2, completion_tokens=1),
    )


def stream_chunk(content, model="gateway-model"):
    return SimpleNamespace(
        model=model,
        choices=[
            SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=None))
        ],
        usage=None,
    )


class GatewayStatusError(Exception):
    """Shaped like the OpenAI SDK's APIStatusError for a gateway refusal:
    ``status_code``, ``body`` (the error object), ``code`` and a
    ``response`` with headers. Its text stands for upstream text that must
    never reach a user."""

    def __init__(self, status_code, body, *, headers=None, text=PRIVATE_UPSTREAM_TEXT):
        super().__init__(f"Error code: {status_code} - {text}")
        self.status_code = status_code
        self.body = body
        self.code = body.get("code") if isinstance(body, dict) else None
        self.type = body.get("type") if isinstance(body, dict) else None
        self.response = SimpleNamespace(
            status_code=status_code, headers=dict(headers or {})
        )


def gateway_refusal(status_code, code, *, error_type=None, metadata=None, headers=None):
    body = {"code": code, "message": PRIVATE_UPSTREAM_TEXT}
    if error_type:
        body["type"] = error_type
    if metadata is not None:
        body["metadata"] = metadata
    return GatewayStatusError(status_code, body, headers=headers)


class _RawResponse:
    def __init__(self, headers, value):
        self.headers = dict(headers)
        self._value = value

    def parse(self):
        return self._value


class _AsyncRawResponse(_RawResponse):
    async def parse(self):
        return self._value


class _RawCompletions:
    def __init__(self, completions):
        self._completions = completions

    def create(self, **kwargs):
        value = self._completions.create(**kwargs)
        return _RawResponse(self._completions._owner.response_headers, value)


class _AsyncRawCompletions(_RawCompletions):
    async def create(self, **kwargs):
        value = await self._completions.create(**kwargs)
        return _AsyncRawResponse(self._completions._owner.response_headers, value)


class _Completions:
    def __init__(self, owner):
        self._owner = owner
        self.with_raw_response = (_AsyncRawCompletions(self) if owner.is_async else _RawCompletions(self))

    def _record(self, kwargs):
        self._owner.calls.append(
            {
                "kwargs": kwargs,
                "default_headers": dict(self._owner.default_headers),
                "options": list(self._owner.options),
            }
        )

    def create(self, **kwargs):
        self._record(kwargs)
        if self._owner.error is not None:
            raise self._owner.error
        if kwargs.get("stream"):
            return self._owner.sync_stream()
        return self._owner.response


class _AsyncCompletions(_Completions):
    async def create(self, **kwargs):  # noqa: D102 - same as the sync double
        self._record(kwargs)
        if self._owner.error is not None:
            raise self._owner.error
        if kwargs.get("stream"):
            return self._owner.async_stream()
        return self._owner.response


class FakeGatewayClient:
    def __init__(
        self,
        *,
        response=None,
        error=None,
        stream_chunks=(),
        stream_error=None,
        response_headers=None,
        default_headers=None,
        calls=None,
        options=None,
        is_async=False,
    ):
        self.response = response if response is not None else chat_response()
        self.error = error
        self.stream_chunks = tuple(stream_chunks)
        self.stream_error = stream_error
        # G05 success fixtures need a real response-side reconcile key. An
        # explicit empty/malformed header map must remain empty/malformed for
        # the money controls; never infer this key from request headers/body.
        self.response_headers = dict(
            {"x-agentcc-request-id": "fixture-gateway-response"}
            if response_headers is None else response_headers
        )
        self.default_headers = dict(default_headers or {})
        self.calls = calls if calls is not None else []
        self.options = list(options or [])
        self.is_async = is_async
        completions = _AsyncCompletions(self) if is_async else _Completions(self)
        self.chat = SimpleNamespace(completions=completions)

    def sync_stream(self):
        yield from self.stream_chunks
        if self.stream_error is not None:
            raise self.stream_error

    async def async_stream(self):
        for chunk in self.stream_chunks:
            yield chunk
        if self.stream_error is not None:
            raise self.stream_error

    def with_options(self, **options):
        merged = {**self.default_headers, **(options.get("default_headers") or {})}
        return FakeGatewayClient(
            response=self.response,
            error=self.error,
            stream_chunks=self.stream_chunks,
            stream_error=self.stream_error,
            response_headers=self.response_headers,
            default_headers=merged,
            calls=self.calls,
            options=[*self.options, options],
            is_async=self.is_async,
        )


def eligible_port(mr, organization, models, **kwargs):
    """A MockGatewayManagedRouting admitting ``models`` for ``organization``
    only, with fake sync and async gateway clients."""
    kwargs.setdefault("client", FakeGatewayClient())
    kwargs.setdefault("async_client", FakeGatewayClient(is_async=True))
    eligible = {str(organization.id): set(models)}
    eligible.update(kwargs.pop("eligible", {}))
    return mr.MockGatewayManagedRouting(eligible=eligible, **kwargs)


def sent_request_ids(client):
    return [call["kwargs"]["extra_headers"]["x-agentcc-request-id"] for call in client.calls]


def exception_chain(error):
    """Every exception reachable from ``error`` through __cause__ and
    __context__ (bounded, cycle-safe)."""
    seen, pending, chain = set(), [error], []
    while pending and len(chain) < 32:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        pending.extend([current.__cause__, current.__context__])
    return chain


class RecordingWsManager:
    """The WebSocket manager a streaming handler talks to; records every
    message. Nothing is ever stopped."""

    def __init__(self):
        self.messages = []

    def is_streaming_stopped(self, *args, **kwargs):
        return False

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.messages.append((name, args, kwargs))

        return record

    def sent(self, name):
        return [kwargs for message, _, kwargs in self.messages if message == name]


def provider_tripwires(monkeypatch):
    """Sets ``PLATFORM_KEY_SENTINEL`` as every platform provider key and
    returns a list that records each litellm entry point or OpenAI SDK
    client construction a test reaches. Tripped entries also raise, but the
    list is what tests assert on (code under test may swallow errors)."""
    import litellm
    import openai

    for name in PLATFORM_KEY_ENV_NAMES:
        monkeypatch.setenv(name, PLATFORM_KEY_SENTINEL)
    tripped = []

    def tripwire(label):
        def tripped_call(*args, **kwargs):
            tripped.append(label)
            raise AssertionError(f"a managed call reached {label}")

        return tripped_call

    for name in (
        "completion",
        "acompletion",
        "responses",
        "aresponses",
        "speech",
        "aspeech",
        "transcription",
        "atranscription",
        "image_generation",
        "aimage_generation",
    ):
        monkeypatch.setattr(litellm, name, tripwire(f"litellm.{name}"), raising=False)
    for client_class in (openai.OpenAI, openai.AsyncOpenAI):
        monkeypatch.setattr(
            client_class, "__init__", tripwire(f"openai.{client_class.__name__}")
        )
    return tripped
