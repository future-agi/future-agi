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


# Normalized whole-agent config keys (the kit's agent-config vocabulary).
# Writers map the subset their provider supports; unknown keys are reported
# back as ``skipped_fields`` rather than silently dropped.
CONFIG_FIELDS = ("instructions", "model", "temperature", "first_message", "voice")


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

    def get_config(self, assistant_id: str) -> dict[str, Any]:
        """Read the agent's current config in normalized CONFIG_FIELDS keys."""
        raise NotImplementedError

    def set_config(self, assistant_id: str, config: dict[str, Any]) -> list[str]:
        """Write the normalized ``config`` subset; return the field names applied."""
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

    def apply_config(self, assistant_id: str, config: dict[str, Any]) -> dict[str, Any]:
        """Write a WHOLE-agent config (kit ``best_config.agent``) and verify.

        Only normalized CONFIG_FIELDS are written; provider-identity keys
        (type/name/provider/assistant_id) and unknown keys are skipped and
        reported. Read-back verifies every field the provider echoes back.
        """
        desired = {
            k: config[k] for k in CONFIG_FIELDS if config.get(k) not in (None, "")
        }
        if not desired:
            raise PromptApplyError(
                "Candidate config has no applicable fields "
                f"(supported: {', '.join(CONFIG_FIELDS)})."
            )
        previous = self.get_config(assistant_id)
        applied_fields = self.set_config(assistant_id, desired)
        current = self.get_config(assistant_id)
        mismatched = [
            f
            for f in applied_fields
            if f in current and current.get(f) != desired.get(f)
        ]
        if mismatched:
            raise PromptApplyError(
                f"{self.provider} read-back mismatch after config update on: "
                f"{', '.join(mismatched)}"
            )
        skipped = sorted(
            k
            for k in config
            if k not in applied_fields
            and k not in ("type", "name", "provider", "assistant_id")
        )
        return {
            "provider": self.provider,
            "assistant_id": assistant_id,
            "applied": True,
            "applied_fields": applied_fields,
            "skipped_fields": skipped,
            "previous_config": previous,
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

    def _get_conversation_config(self, assistant_id: str) -> dict[str, Any]:
        data = _check(
            requests.get(
                f"{self.BASE_URL}/v1/convai/agents/{assistant_id}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            ),
            "ElevenLabs get-agent",
        )
        return data.get("conversation_config") or {}

    def get_config(self, assistant_id: str) -> dict[str, Any]:
        cc = self._get_conversation_config(assistant_id)
        agent = cc.get("agent") or {}
        prompt = agent.get("prompt") or {}
        return {
            "instructions": str(prompt.get("prompt") or ""),
            "model": prompt.get("llm"),
            "temperature": prompt.get("temperature"),
            "first_message": agent.get("first_message"),
            "voice": (cc.get("tts") or {}).get("voice_id"),
        }

    def set_config(self, assistant_id: str, config: dict[str, Any]) -> list[str]:
        prompt_patch: dict[str, Any] = {}
        agent_patch: dict[str, Any] = {}
        cc_patch: dict[str, Any] = {}
        applied: list[str] = []
        if "instructions" in config:
            prompt_patch["prompt"] = str(config["instructions"])
            applied.append("instructions")
        if "model" in config:
            prompt_patch["llm"] = str(config["model"])
            applied.append("model")
        if "temperature" in config:
            prompt_patch["temperature"] = float(config["temperature"])
            applied.append("temperature")
        if "first_message" in config:
            agent_patch["first_message"] = str(config["first_message"])
            applied.append("first_message")
        if "voice" in config:
            cc_patch["tts"] = {"voice_id": str(config["voice"])}
            applied.append("voice")
        if prompt_patch:
            agent_patch["prompt"] = prompt_patch
        if agent_patch:
            cc_patch["agent"] = agent_patch
        _check(
            requests.patch(
                f"{self.BASE_URL}/v1/convai/agents/{assistant_id}",
                headers=self._headers(),
                json={"conversation_config": cc_patch},
                timeout=REQUEST_TIMEOUT,
            ),
            "ElevenLabs update-agent",
        )
        return applied


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

    def get_config(self, assistant_id: str) -> dict[str, Any]:
        assistant = self._get_assistant(assistant_id)
        model = assistant.get("model") or {}
        return {
            "instructions": self._system_content(model),
            "model": model.get("model"),
            "temperature": model.get("temperature"),
            "first_message": assistant.get("firstMessage"),
            "voice": (assistant.get("voice") or {}).get("voiceId"),
        }

    def set_config(self, assistant_id: str, config: dict[str, Any]) -> list[str]:
        assistant = self._get_assistant(assistant_id)
        # Vapi PATCH replaces whole sub-objects, so read-modify-write model/voice.
        model = dict(assistant.get("model") or {})
        body: dict[str, Any] = {}
        applied: list[str] = []
        if "instructions" in config:
            messages = [
                dict(m) for m in (model.get("messages") or []) if isinstance(m, dict)
            ]
            for msg in messages:
                if msg.get("role") == "system":
                    msg["content"] = str(config["instructions"])
                    break
            else:
                messages.insert(
                    0, {"role": "system", "content": str(config["instructions"])}
                )
            model["messages"] = messages
            applied.append("instructions")
        if "model" in config:
            model["model"] = str(config["model"])
            applied.append("model")
        if "temperature" in config:
            model["temperature"] = float(config["temperature"])
            applied.append("temperature")
        if applied:
            body["model"] = model
        if "first_message" in config:
            body["firstMessage"] = str(config["first_message"])
            applied.append("first_message")
        if "voice" in config:
            voice = dict(assistant.get("voice") or {})
            voice["voiceId"] = str(config["voice"])
            body["voice"] = voice
            applied.append("voice")
        _check(
            requests.patch(
                f"{self.BASE_URL}/assistant/{assistant_id}",
                headers=self._headers(),
                json=body,
                timeout=REQUEST_TIMEOUT,
            ),
            "Vapi update-assistant",
        )
        return applied


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

    def get_config(self, assistant_id: str) -> dict[str, Any]:
        agent = _check(
            requests.get(
                f"{self.BASE_URL}/get-agent/{assistant_id}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            ),
            "Retell get-agent",
        )
        llm = _check(
            requests.get(
                f"{self.BASE_URL}/get-retell-llm/{self._llm_id(assistant_id)}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            ),
            "Retell get-retell-llm",
        )
        return {
            "instructions": str(llm.get("general_prompt") or ""),
            "model": llm.get("model"),
            "temperature": llm.get("model_temperature"),
            "first_message": llm.get("begin_message"),
            "voice": agent.get("voice_id"),
        }

    def set_config(self, assistant_id: str, config: dict[str, Any]) -> list[str]:
        llm_patch: dict[str, Any] = {}
        agent_patch: dict[str, Any] = {}
        applied: list[str] = []
        if "instructions" in config:
            llm_patch["general_prompt"] = str(config["instructions"])
            applied.append("instructions")
        if "model" in config:
            llm_patch["model"] = str(config["model"])
            applied.append("model")
        if "temperature" in config:
            llm_patch["model_temperature"] = float(config["temperature"])
            applied.append("temperature")
        if "first_message" in config:
            llm_patch["begin_message"] = str(config["first_message"])
            applied.append("first_message")
        if "voice" in config:
            agent_patch["voice_id"] = str(config["voice"])
            applied.append("voice")
        if llm_patch:
            _check(
                requests.patch(
                    f"{self.BASE_URL}/update-retell-llm/{self._llm_id(assistant_id)}",
                    headers=self._headers(),
                    json=llm_patch,
                    timeout=REQUEST_TIMEOUT,
                ),
                "Retell update-retell-llm",
            )
        if agent_patch:
            _check(
                requests.patch(
                    f"{self.BASE_URL}/update-agent/{assistant_id}",
                    headers=self._headers(),
                    json=agent_patch,
                    timeout=REQUEST_TIMEOUT,
                ),
                "Retell update-agent",
            )
        return applied


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


def _writer_for_agent_definition(
    agent_definition,
) -> tuple[ProviderPromptWriter, str]:
    """Resolve (writer, assistant_id) for an agent definition or raise."""
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
    return writer_cls(api_key=api_key), assistant_id


def apply_prompt_to_provider_agent(agent_definition, prompt: str) -> dict[str, Any]:
    """Write ``prompt`` to the agent definition's provider-side agent.

    Resolves credentials like the chat adapters do (encrypted ProviderCredentials
    first, plain ``api_key`` field as fallback). Raises ``PromptApplyUnsupported``
    with a per-provider reason when there is nothing writable, ``PromptApplyError``
    on API failure or read-back mismatch.
    """
    writer, assistant_id = _writer_for_agent_definition(agent_definition)
    result = writer.apply(assistant_id, prompt)
    logger.info(
        "provider_prompt_applied",
        provider=writer.provider,
        assistant_id=assistant_id,
        agent_definition_id=str(agent_definition.id),
    )
    return result


def apply_config_to_provider_agent(
    agent_definition, config: dict[str, Any]
) -> dict[str, Any]:
    """Write a WHOLE-agent config (kit candidate / ``best_config.agent``) to the
    provider-side agent — instructions, model, temperature, first message, voice —
    with per-field read-back verification. The kit is the engine that produced the
    config; this is the platform's apply edge.
    """
    writer, assistant_id = _writer_for_agent_definition(agent_definition)
    result = writer.apply_config(assistant_id, config)
    logger.info(
        "provider_config_applied",
        provider=writer.provider,
        assistant_id=assistant_id,
        agent_definition_id=str(agent_definition.id),
        applied_fields=result.get("applied_fields"),
    )
    return result
