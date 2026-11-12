"""Future AGI models on by default for new organizations: one key that just
works, OpenRouter-style.

An organization created after this change gets ``managed_models``
``{"enabled": true}`` in its first org-config version, so the first key it
creates reaches the Future AGI catalogue without anyone turning the
"Future AGI credits" switch on. That applies only where the deployment offers
Future AGI models, which takes both:

- the deployment declaring it, with ``AGENTCC_MANAGED_MODELS_ON_FOR_NEW_ORGS``
  set to a true value (unset everywhere by default, so every shipped config
  keeps them off), and
- its gateway answering the org credits route, as the console's managed
  models status does (``views/managed_models.py``). A gateway with no
  managed provider answers 404 ``managed_models_disabled``; that, or any
  failure to reach it, reads as not offered.

Every other organization keeps what it has: an existing version's
``managed_models`` is copied as it is, and an organization created before
``DEFAULT_ON_FOR_ORGS_CREATED_FROM`` starts from ``{}`` as before. Nothing is
spent by the default: with no credit, a call gets the gateway's 402
``managed_credits_exhausted`` ("Add credits or use your own provider keys").
"""

import os
from datetime import UTC, datetime

import structlog

from agentcc.models import AgentccOrgConfig
from agentcc.services.gateway_client import get_gateway_client

logger = structlog.get_logger(__name__)

SETTING = "AGENTCC_MANAGED_MODELS_ON_FOR_NEW_ORGS"

# Organizations created from this instant on are new: the change that made
# Future AGI models the default for them.
DEFAULT_ON_FOR_ORGS_CREATED_FROM = datetime(2026, 10, 25, tzinfo=UTC)

_TRUE = {"1", "true", "yes", "on"}


def declared_by_deployment() -> bool:
    """Whether the deployment declares Future AGI models on for new orgs."""
    return os.environ.get(SETTING, "").strip().lower() in _TRUE


def offered_by_gateway(org) -> bool:
    """Whether the gateway offers Future AGI models to ``org``: it answers the
    org's credits route. A 404 (no managed provider) or any failure is no."""
    try:
        get_gateway_client().get_org_credits(str(org.id))
    except Exception as e:  # noqa: BLE001 - any failure reads as not offered
        logger.info(
            "managed_models_default_not_offered", org_id=str(org.id), error=str(e)
        )
        return False
    return True


def is_new_org(org) -> bool:
    created = getattr(org, "created_at", None)
    return created is not None and created >= DEFAULT_ON_FOR_ORGS_CREATED_FROM


def initial_managed_models(org) -> dict:
    """``managed_models`` for ``org``'s first org-config version:
    ``{"enabled": True}`` for a new organization where the deployment offers
    Future AGI models, ``{}`` (the model default) otherwise. The gateway is
    asked only when the deployment declares them and the org is new."""
    if not declared_by_deployment() or not is_new_org(org):
        return {}
    if not offered_by_gateway(org):
        return {}
    return {"enabled": True}


def ensure_first_org_config(org, user=None):
    """Give a new organization with no org config its first version when that
    version turns Future AGI models on, and push it, so the key it has just
    created works. Returns the version written, or None when the org already
    has a version or the default does not apply. Best effort: a failure is
    logged and the caller carries on."""
    try:
        if AgentccOrgConfig.no_workspace_objects.filter(
            organization=org, deleted=False
        ).exists():
            return None
        if not declared_by_deployment() or not is_new_org(org):
            return None
        if not offered_by_gateway(org):
            return None
        from agentcc.services.org_config_writer import write_config_version

        # The writer's first version takes initial_managed_models (asked of
        # the gateway there, under the org lock); the patch names nothing.
        config, _ = write_config_version(
            org,
            {},
            user=user,
            description="Future AGI models on for a new organization",
        )
        if not (config.managed_models or {}).get("enabled"):
            logger.info("managed_models_default_not_applied", org_id=str(org.id))
        return config
    except Exception:
        logger.exception("managed_models_default_failed", org_id=str(org.id))
        return None
