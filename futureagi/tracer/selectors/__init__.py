"""Observability DB selectors."""

from __future__ import annotations

import logging
from typing import Optional

from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices
from tracer.utils.vapi_recording import VapiRecordingService

logger = logging.getLogger(__name__)


def get_observability_provider(
    project_id, provider_name: str
) -> Optional[ObservabilityProvider]:
    """Return the enabled ObservabilityProvider for a project/provider pair, or None."""
    return (
        ObservabilityProvider.objects.filter(
            project_id=project_id,
            provider=provider_name,
            enabled=True,
        )
        .first()
    )


def get_agent_api_key(project_id, provider_name: str) -> Optional[str]:
    """Resolve the agent api_key for a project/provider pair; None if unresolved."""
    if provider_name == ProviderChoices.VAPI:
        return VapiRecordingService.get_api_key_for_project(project_id)

    provider = get_observability_provider(project_id, provider_name)
    if not provider:
        return None
    try:
        agent_def = getattr(provider, "agent_definition", None)
        if agent_def is None:
            return None
        return getattr(agent_def, "api_key", None) or None
    except Exception:
        logger.warning(
            "get_agent_api_key: failed to resolve api_key for project=%s provider=%s",
            project_id,
            provider_name,
            exc_info=True,
        )
        return None
