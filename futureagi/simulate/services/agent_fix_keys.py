"""The AgentCC key a fix's engine reaches its models on. Each engine gets its own key,
limited to the engine's models and expiring with the fix, so the platform's own gateway
key never enters a sandbox. Releasing the sandbox revokes the key."""

from datetime import datetime

import structlog
from django.conf import settings
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge
from simulate.models import AgentFixRun

logger = structlog.get_logger(__name__)


def mint(fix_run: AgentFixRun, expires_at: datetime) -> str:
    """A new key for the fix's next engine, recorded on the run before anything uses it.
    Returns the raw key, which only the engine's capability file holds."""
    api_key, raw_key = auth_bridge.provision_key(
        name=f"agent-fix-{fix_run.id}",
        owner="agent-fix",
        models=list(settings.AGENT_FIX_ENGINE_MODELS),
        metadata={"fix_run_id": str(fix_run.id)},
        organization=fix_run.organization,
    )
    # AgentCC creates keys without an expiry; the gateway drops this one at its first
    # key sync after the row expires.
    api_key.expires_at = expires_at
    api_key.save(update_fields=["expires_at", "updated_at"])
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        model_key_id=api_key.gateway_key_id, updated_at=timezone.now()
    )
    return raw_key


def revoke(fix_run_id: str) -> None:
    """Revoke the fix's model key, if it has one. Safe to repeat."""
    key_id = AgentFixRun.no_workspace_objects.values_list(
        "model_key_id", flat=True
    ).get(id=fix_run_id)
    if not key_id:
        return
    for api_key in AgentccAPIKey.no_workspace_objects.filter(
        gateway_key_id=key_id, status=AgentccAPIKey.ACTIVE
    ):
        _, gateway_failed = auth_bridge.revoke_key(api_key)
        if gateway_failed:
            # The row is revoked; the gateway drops the key at its next key sync.
            logger.warning(
                "agent_fix_model_key_revoke_deferred",
                fix_run=str(fix_run_id),
                key_id=key_id,
            )
    AgentFixRun.no_workspace_objects.filter(id=fix_run_id, model_key_id=key_id).update(
        model_key_id="", updated_at=timezone.now()
    )
    logger.info("agent_fix_model_key_revoked", fix_run=str(fix_run_id), key_id=key_id)
