"""Positive model-completion evidence for generation-owned analyses only.

Workers install these instance-local wrappers; foreground clients are untouched.
Terminal records retain no output text or error payload. The raw adapter also
keeps in-memory, generation-owned payload capabilities until dispatch/export.
A successful tool call is not a terminal model response, and a later stop cannot
erase a failed earlier call.
"""

NORMAL_FINISHES = frozenset({"stop", "end_turn"})
INTERMEDIATE_FINISHES = NORMAL_FINISHES | {"tool_calls", "tool_use"}


def _field(value, name, default=None):
    return (
        value.get(name, default)
        if isinstance(value, dict)
        else getattr(value, name, default)
    )


class IncompleteAnalysis(RuntimeError):
    def __init__(self):
        super().__init__("analysis_failed")


class CompletionWitness:
    def __init__(self, max_calls=None):
        self.max_calls = max_calls
        self.calls = []
        self.client_tails = {}
        self.failed = False
        self.fallback_used = False

    def observe_tool_result(self, status, content):
        """Observe actual dispatch results, including caught exceptions/errors.

        Semantic fallbacks are rejected at their source by the tool schemas.
        This boundary covers every allowed tool's generic non-success result,
        before AgentLoop's self-correction can hide it behind a later success.
        """
        if (
            status != "completed"
            or not isinstance(content, str)
            or not content.strip()
            or content == "No result"
        ):
            self.failed = True
            self.fallback_used = True

    def start(self, client):
        # Nested tools and the outer agent spend the same actual-send budget.
        # Refuse BEFORE another transport can send, including raw Falcon calls.
        if (
            self.failed
            or self.fallback_used
            or (self.max_calls is not None and len(self.calls) >= self.max_calls)
        ):
            self.failed = True
            raise IncompleteAnalysis()
        call = {"finish": None, "ended": False, "exception": False}
        self.calls.append(call)
        self.client_tails[client] = call
        return call

    def observe(self, call, response):
        choices = _field(response, "choices", []) or []
        if choices:
            reason = _field(choices[0], "finish_reason")
            call["finish"] = reason
            if len(choices) != 1 or (
                reason is not None and reason not in INTERMEDIATE_FINISHES
            ):
                self.failed = True

    def observe_raw(self, call, provider, response):
        """Retain wire evidence before any provider-to-OpenAI normalization."""
        normal = {
            "openai": {"stop"},
            "anthropic": {"end_turn", "stop_sequence"},
            "bedrock": {"end_turn", "stop_sequence"},
        }
        intermediate = {
            "openai": "tool_calls",
            "anthropic": "tool_use",
            "bedrock": "tool_use",
        }
        if provider == "openai":
            choices = _field(response, "choices", []) or []
            reason = _field(choices[0], "finish_reason") if len(choices) == 1 else None
        else:
            reason = _field(response, "stop_reason")
        call["raw_provider"] = provider
        call["raw_finish"] = reason if isinstance(reason, str) else None
        if not isinstance(reason, str) or provider not in normal:
            self.failed = True
        elif reason in normal[provider]:
            call["finish"] = "stop"
        elif reason == intermediate[provider]:
            call["finish"] = "tool_calls"
        else:
            self.failed = True
        if self.failed:
            raise IncompleteAnalysis()

    def end(self, call):
        call["ended"] = True
        if call["finish"] not in INTERMEDIATE_FINISHES:
            self.failed = True

    @property
    def satisfied(self):
        # A Chauffeur's successful last call cannot stand in for an unfinished
        # Judge. Every installed client must finish normally on its own tail.
        return bool(
            self.calls
            and isinstance(self.max_calls, int)
            and 0 < len(self.calls) < self.max_calls
            and not self.failed
            and not self.fallback_used
            and all(call["ended"] for call in self.calls)
            and all(
                call is not None and call["finish"] in NORMAL_FINISHES
                for call in self.client_tails.values()
            )
        )

    def require(self, *, fallback=False):
        self.fallback_used |= fallback
        if not self.satisfied:
            raise IncompleteAnalysis()


class RecordingLLM:
    """Install raw Falcon evidence before an agent can swallow failures.

    Configured Falcon clients use an instance-local raw transport adapter. The
    normalized API remains available for non-provider protocol clients. The
    background construction census pins Falcon as the worker's provider client;
    Judge/Chauffeur use BackgroundLLM rather than this compatibility protocol.
    """

    def __init__(self, client, witness, *, raw=False):
        self._raw = raw
        self._client = client
        self._witness = witness
        self._key = object()
        witness.client_tails[self._key] = None

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def stream_with_retry(self, *args, **kwargs):
        if self._witness.failed or self._witness.fallback_used:
            raise IncompleteAnalysis()
        call = self._witness.start(self._key)
        try:
            if self._raw or hasattr(self._client, "provider"):
                from ee.agenthub.traceerroragent.background_falcon import (
                    BackgroundFalconClient,
                )
                from ee.falcon_ai.llm_client import FalconLLMClient

                if not isinstance(self._client, FalconLLMClient):
                    raise IncompleteAnalysis()
                adapter = BackgroundFalconClient(self._client, self._witness, call)
                kwargs.pop("max_retries", None)
                async for chunk in adapter.stream_completion(*args, **kwargs):
                    yield chunk
                self._witness.end(call)
                return
            stream = getattr(self._client, "stream_completion", None)
            if stream is not None:
                kwargs.pop("max_retries", None)
            else:
                stream = self._client.stream_with_retry
            async for chunk in stream(*args, **kwargs):
                self._witness.observe(call, chunk)
                yield chunk
            self._witness.end(call)
        except BaseException:
            call["exception"] = True
            call["ended"] = True
            self._witness.failed = True
            raise

    def _get_completion_with_tools(self, *args, **kwargs):
        call = self._witness.start(self._key)
        try:
            response = self._client._get_completion_with_tools(*args, **kwargs)
            self._witness.observe(call, response)
            self._witness.end(call)
            return response
        except BaseException:
            self._witness.failed = True
            raise

    def _get_completion_content(self, *args, **kwargs):
        call = self._witness.start(self._key)
        try:
            # The string API documents last_finish_reason as per-call metadata.
            # Clear it first so a stale successful response cannot be a witness.
            self._client.last_finish_reason = None
            result = self._client._get_completion_content(*args, **kwargs)
            self._witness.observe(
                call, {"choices": [{"finish_reason": self._client.last_finish_reason}]}
            )
            self._witness.end(call)
            if not result:
                self._witness.failed = True
            return result
        except BaseException:
            self._witness.failed = True
            raise

    async def generate_summary(self, *args, **kwargs):
        # Falcon's legacy string-only compactor exposes no terminal metadata.
        # It cannot prove completion; do not silently turn its fallback into a
        # successful background analysis. New hidden conversations need none.
        self._witness.fallback_used = True
        raise IncompleteAnalysis()
