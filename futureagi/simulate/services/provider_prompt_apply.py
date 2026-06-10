"""Apply an optimised prompt to the customer's PROVIDER-SIDE agent (TH-5642).

"Directly apply the fix" for agent-definition runs: unlike prompt-template runs
(where the platform owns the prompt and applying = a new PromptVersion, see
``optimizer_apply.py``), an AgentDefinition describes an agent hosted by a third
party — its prompt lives on the provider. Applying there means writing through
the provider's management API.

Each writer follows the same contract:
- read the agent's current prompt first (returned as ``previous_prompt`` so the
  caller can surface an undo),
- write the new prompt,
- read it back and verify the write landed (no trust-the-200).

Providers whose "agent" is not a single prompt (Bland pathways are node graphs;
Twilio agents are the customer's own TwiML/app code; LiveKit/Pipecat agents are
the customer's own deployment) raise ``PromptApplyUnsupported`` with the reason —
callers turn that into a clear 400, never a silent skip.
"""

from __future__ import annotations

from typing import Any

import requests
import structlog

logger = structlog.get_logger(__name__)

REQUEST_TIMEOUT = 30


class PromptApplyError(RuntimeError):
    """A provider API call failed or the read-back didn't match."""


class PromptApplyUnsupported(PromptApplyError):
    """The provider has no single-prompt agent to write to."""


def _check(resp: requests.Response, what: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise PromptApplyError(f"{what} failed ({resp.status_code}): {resp.text[:500]}")
    try:
        return resp.json() or {}
    except ValueError:
        return {}


class ProviderPromptWriter:
    provider: str = ""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError(f"{type(self).__name__} requires an api_key")
        self._api_key = api_key

    def get_prompt(self, assistant_id: str) -> str:
        raise NotImplementedError

    def set_prompt(self, assistant_id: str, prompt: str) -> None:
        raise NotImplementedError

    def apply(self, assistant_id: str, prompt: str) -> dict[str, Any]:
        """Write ``prompt`` to the provider agent and verify by read-back."""
        previous = self.get_prompt(assistant_id)
        self.set_prompt(assistant_id, prompt)
        current = self.get_prompt(assistant_id)
        if current != prompt:
            raise PromptApplyError(
                f"{self.provider} read-back mismatch after update: "
                f"expected the applied prompt, got {current[:200]!r}"
            )
        return {
            "provider": self.provider,
            "assistant_id": assistant_id,
            "applied": True,
            "previous_prompt": previous,
        }


class ElevenLabsPromptWriter(ProviderPromptWriter):
    """ConvAI agent prompt at ``conversation_config.agent.prompt.prompt``."""

    provider = "eleven_labs"
    BASE_URL = "https://api.elevenlabs.io"

    def _headers(self) -> dict[str, str]:
        return {"xi-api-key": self._api_key, "Content-Type": "application/json"}

    def get_prompt(self, assistant_id: str) -> str:
        data = _check(
            requests.get(
                f"{self.BASE_URL}/v1/convai/agents/{assistant_id}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            ),
            "ElevenLabs get-agent",
        )
        return str(
            ((data.get("conversation_config") or {}).get("agent") or {})
            .get("prompt", {})
            .get("prompt", "")
        )

    def set_prompt(self, assistant_id: str, prompt: str) -> None:
        _check(
            requests.patch(
                f"{self.BASE_URL}/v1/convai/agents/{assistant_id}",
                headers=self._headers(),
                json={"conversation_config": {"agent": {"prompt": {"prompt": prompt}}}},
                timeout=REQUEST_TIMEOUT,
            ),
            "ElevenLabs update-agent",
        )


class VapiPromptWriter(ProviderPromptWriter):
    """Assistant prompt = the system message in ``model.messages``.

    Vapi's PATCH replaces the whole ``model`` object, so read-modify-write.
    """

    provider = "vapi"
    BASE_URL = "https://api.vapi.ai"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _get_assistant(self, assistant_id: str) -> dict[str, Any]:
        return _check(
            requests.get(
                f"{self.BASE_URL}/assistant/{assistant_id}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            ),
            "Vapi get-assistant",
        )

    @staticmethod
    def _system_content(model: dict[str, Any]) -> str:
        for msg in model.get("messages") or []:
            if isinstance(msg, dict) and msg.get("role") == "system":
                return str(msg.get("content") or "")
        return ""

    def get_prompt(self, assistant_id: str) -> str:
        return self._system_content(
            self._get_assistant(assistant_id).get("model") or {}
        )

    def set_prompt(self, assistant_id: str, prompt: str) -> None:
        model = dict(self._get_assistant(assistant_id).get("model") or {})
        messages = [
            dict(m) for m in (model.get("messages") or []) if isinstance(m, dict)
        ]
        for msg in messages:
            if msg.get("role") == "system":
                msg["content"] = prompt
                break
        else:
            messages.insert(0, {"role": "system", "content": prompt})
        model["messages"] = messages
        _check(
            requests.patch(
                f"{self.BASE_URL}/assistant/{assistant_id}",
                headers=self._headers(),
                json={"model": model},
                timeout=REQUEST_TIMEOUT,
            ),
            "Vapi update-assistant",
        )


class RetellPromptWriter(ProviderPromptWriter):
    """Agent prompt = ``general_prompt`` on the agent's Retell-LLM.

    The agent references its LLM via ``response_engine.llm_id``; only
    ``response_engine.type == "retell-llm"`` agents carry an editable prompt.
    """

    provider = "retell"
    BASE_URL = "https://api.retellai.com"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _llm_id(self, assistant_id: str) -> str:
        agent = _check(
            requests.get(
                f"{self.BASE_URL}/get-agent/{assistant_id}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            ),
            "Retell get-agent",
        )
        engine = agent.get("response_engine") or {}
        if engine.get("type") != "retell-llm" or not engine.get("llm_id"):
            raise PromptApplyUnsupported(
                "Retell agent does not use a retell-llm response engine; "
                "its prompt is not editable via the Retell API."
            )
        return str(engine["llm_id"])

    def get_prompt(self, assistant_id: str) -> str:
        llm = _check(
            requests.get(
                f"{self.BASE_URL}/get-retell-llm/{self._llm_id(assistant_id)}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            ),
            "Retell get-retell-llm",
        )
        return str(llm.get("general_prompt") or "")

    def set_prompt(self, assistant_id: str, prompt: str) -> None:
        _check(
            requests.patch(
                f"{self.BASE_URL}/update-retell-llm/{self._llm_id(assistant_id)}",
                headers=self._headers(),
                json={"general_prompt": prompt},
                timeout=REQUEST_TIMEOUT,
            ),
            "Retell update-retell-llm",
        )


_UNSUPPORTED: dict[str, str] = {
    "bland": "Bland pathways are node graphs, not a single prompt; apply per-node "
    "via the Bland pathway editor/API.",
    "twilio": "A Twilio agent is the customer's own TwiML application code; there "
    "is no provider-side prompt to write.",
    "livekit": "LiveKit agents are the customer's own deployment; the prompt lives "
    "in their code.",
    "livekit_bridge": "LiveKit agents are the customer's own deployment; the prompt "
    "lives in their code.",
    "pipecat": "Pipecat agents are the customer's own deployment; the prompt lives "
    "in their code.",
    "deepgram": "Deepgram Voice Agents are defined inline per-connection (Settings "
    "message), not stored provider-side.",
    "agora": "Agora ConvAI pipeline prompt updates need project access (TH-5682).",
}

PROVIDER_PROMPT_WRITERS: dict[str, type[ProviderPromptWriter]] = {
    "vapi": VapiPromptWriter,
    "retell": RetellPromptWriter,
    "elevenlabs": ElevenLabsPromptWriter,
    "eleven_labs": ElevenLabsPromptWriter,  # provider-string drift
}


def apply_prompt_to_provider_agent(agent_definition, prompt: str) -> dict[str, Any]:
    """Write ``prompt`` to the agent definition's provider-side agent.

    Resolves credentials like the chat adapters do (encrypted ProviderCredentials
    first, plain ``api_key`` field as fallback). Raises ``PromptApplyUnsupported``
    with a per-provider reason when there is nothing writable, ``PromptApplyError``
    on API failure or read-back mismatch.
    """
    provider = (agent_definition.provider or "").strip().lower()
    assistant_id = (agent_definition.assistant_id or "").strip()
    if not provider:
        raise PromptApplyUnsupported("Agent definition has no provider set.")
    if provider in _UNSUPPORTED:
        raise PromptApplyUnsupported(_UNSUPPORTED[provider])
    writer_cls = PROVIDER_PROMPT_WRITERS.get(provider)
    if writer_cls is None:
        raise PromptApplyUnsupported(f"No prompt writer for provider {provider!r}.")
    if not assistant_id:
        raise PromptApplyUnsupported(
            "Agent definition has no assistant_id to apply to."
        )

    from simulate.services.chat_agent_adapter_factory import _resolve_api_key

    api_key = _resolve_api_key(agent_definition)
    if not api_key:
        raise PromptApplyError(
            f"No {provider} credentials on the agent definition to apply with."
        )
    result = writer_cls(api_key=api_key).apply(assistant_id, prompt)
    logger.info(
        "provider_prompt_applied",
        provider=provider,
        assistant_id=assistant_id,
        agent_definition_id=str(agent_definition.id),
    )
    return result
