"""Selects the agent-under-test driver for a chat simulation (TH-5642).

Product decision (2026-06-05): for an external **hosted** chat agent (e.g. Retell)
the PLATFORM drives the conversation server-side via a provider adapter; SDK-push
stays the path for providers that support it (e.g. LiveKit, whose agent runs the
customer's own code). Dispatch is gated on **provider + assistant_id** so an SDK
agent — which has no external hosted assistant_id — is never double-driven.

Both adapters honour the same ``generate_response(conversation_history) -> {...}``
contract as :class:`PromptBasedAgentAdapter`, so ``run_prompt_based_conversation``
drives any of them unchanged.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from django.db.models import Q

from simulate.models.agent_definition import AgentDefinition
from simulate.models.run_test import RunTest
from simulate.services.retell_chat_agent_adapter import RetellChatAgentAdapter

# NOTE: ``create_adapter_from_run_test`` (prompt path) is imported lazily inside
# create_chat_agent_adapter() — it transitively pulls in litellm/gateway clients,
# which must NOT load just because the trigger query wants EXTERNAL_HOSTED_CHAT_
# PROVIDERS. Keeping this module light keeps the routing filter cheap to import.

logger = structlog.get_logger(__name__)

# Providers whose chat agent-under-test the platform drives SERVER-SIDE (hosted,
# reached by API via assistant_id). provider key -> adapter builder(agent_id, api_key).
EXTERNAL_HOSTED_CHAT_ADAPTERS = {
    "retell": RetellChatAgentAdapter,
}

# Provider keys the platform drives server-side — used by the trigger query to
# route these agent_definition TEXT sims into run_single_prompt_chat.
EXTERNAL_HOSTED_CHAT_PROVIDERS: tuple[str, ...] = tuple(EXTERNAL_HOSTED_CHAT_ADAPTERS)


def _resolve_api_key(agent_definition: AgentDefinition) -> str:
    """Prefer the encrypted ProviderCredentials, fall back to the plain field."""
    creds = getattr(agent_definition, "credentials", None)
    if creds is not None:
        try:
            key = creds.get_api_key()
            if key:
                return key
        except Exception as e:  # pragma: no cover - defensive; never block on creds
            logger.warning("chat_adapter_cred_resolve_failed", error=str(e))
    return agent_definition.api_key or ""


def server_side_chat_filter() -> Q:
    """CallExecution filter for chats the platform drives SERVER-SIDE (TH-5642).

    Prompt-based sims, plus agent_definition TEXT sims whose agent-under-test is an
    external HOSTED chat provider (e.g. Retell) reachable by assistant_id. SDK-driven
    agents (no hosted provider/assistant_id) are excluded so they stay on SDK-push and
    are never double-driven. Lives here (not in the heavy tasks module) so it stays
    cheap to import. Mirrors :func:`is_external_hosted_chat` at the query level.
    """
    rt = "test_execution__run_test"
    ad = f"{rt}__agent_definition"
    return Q(**{f"{rt}__source_type": RunTest.SourceTypes.PROMPT}) | (
        Q(**{f"{rt}__source_type": RunTest.SourceTypes.AGENT_DEFINITION})
        & Q(**{f"{ad}__agent_type": AgentDefinition.AgentTypeChoices.TEXT})
        & Q(**{f"{ad}__provider__in": EXTERNAL_HOSTED_CHAT_PROVIDERS})
        & ~Q(**{f"{ad}__assistant_id__isnull": True})
        & ~Q(**{f"{ad}__assistant_id__exact": ""})
    )


def is_external_hosted_chat(agent_definition: AgentDefinition | None) -> bool:
    """True iff this is a TEXT agent on an external hosted chat provider we can
    drive server-side (provider known + assistant_id present)."""
    if agent_definition is None:
        return False
    return bool(
        agent_definition.agent_type == AgentDefinition.AgentTypeChoices.TEXT
        and (agent_definition.provider or "") in EXTERNAL_HOSTED_CHAT_ADAPTERS
        and (agent_definition.assistant_id or "").strip()
    )


def create_chat_agent_adapter(
    run_test: RunTest,
    organization_id: UUID,
    workspace_id: UUID | None = None,
    variable_values: dict[str, Any] | None = None,
):
    """Return the agent-under-test driver for a chat sim, or ``None`` for SDK-push.

    - ``source_type == prompt`` → :class:`PromptBasedAgentAdapter` (unchanged).
    - ``agent_definition`` + external hosted chat provider + assistant_id → the
      provider adapter (platform drives server-side).
    - otherwise (SDK-driven agents) → ``None``; the caller keeps the SDK-push path.
    """
    if run_test.source_type == RunTest.SourceTypes.PROMPT:
        # Lazy: pulls in litellm/gateway — only needed on the prompt path.
        from simulate.services.prompt_based_agent_adapter import (
            create_adapter_from_run_test,
        )

        return create_adapter_from_run_test(
            run_test, organization_id, workspace_id, variable_values
        )

    agent_definition = run_test.agent_definition
    if is_external_hosted_chat(agent_definition):
        builder = EXTERNAL_HOSTED_CHAT_ADAPTERS[agent_definition.provider]
        adapter = builder(
            agent_id=agent_definition.assistant_id,
            api_key=_resolve_api_key(agent_definition),
        )
        logger.info(
            "chat_agent_adapter_server_side",
            provider=agent_definition.provider,
            agent_definition_id=str(agent_definition.id),
        )
        return adapter

    # SDK-driven agent (e.g. LiveKit / custom): the customer's SDK pushes turns
    # to send_message_to_chat; the platform does not drive it server-side.
    return None
