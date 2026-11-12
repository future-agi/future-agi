"""Atomic, value-limited audit of organization BYOK unknown-price consent."""

from types import SimpleNamespace

from accounts.models.audit_log import AuditScope
from agentcc.contracts.gateway_admin import AgentsConfig
from agentcc.permissions import require_gateway_admin
from agentcc.services.config_push import OrgConfigRejected
from tfc.utils import audit

AUDIT_ACTION = "agentcc.byok_unpriced_under_cap_changed"
FIELD = "unpriced_under_cap"


def _value(routing):
    agents = routing.get("agents") if isinstance(routing, dict) else None
    if not isinstance(agents, dict):
        return None  # The whole-config contract rejects malformed containers.
    # Match the value the gateway receives, including canonical-name precedence
    # when a legacy row supplies both spellings. Never audit a shadowed alias.
    agents = AgentsConfig._normalize_input_aliases(agents)
    value = agents.get(FIELD)
    if value is not None and (not isinstance(value, str) or value not in ("reject", "allow")):
        raise OrgConfigRejected("routing.agents.unpriced_under_cap must be reject, allow or unset")
    return value


def _values(config):
    routing = config.routing if config is not None else {}
    rollout = routing.get("rollout") if isinstance(routing, dict) else None
    candidate = rollout.get("routing") if isinstance(rollout, dict) else None
    # A rollout replaces the primary routing, so its consent is independent.
    # Only these enum values, never the enclosing routing object, reach audit.
    return {FIELD: _value(routing), "rollout_unpriced_under_cap": _value(candidate)}


def _changes(active, config):
    old, new = _values(active), _values(config)
    return {field: {"old": old[field], "new": value}
            for field, value in new.items() if old[field] != value}


def require_unpriced_admin(active, config, user):
    """Check the target org's role under its write lock, before side effects."""
    if _changes(active, config):
        require_gateway_admin(SimpleNamespace(user=user, organization=config.organization))


def audit_unpriced_change(active, config, user):
    """Persist consent with the config transaction, before any gateway push.

    Unset remains distinct from explicit reject. The shared logger stamps time
    and records the actor/org; a muted logger or failed insert must abort the
    config transaction, including historical-version activation/rollback.
    """
    changes = _changes(active, config)
    if not changes:
        return
    entry = audit.log_audit(
        organization=config.organization,
        actor=user,
        action=AUDIT_ACTION,
        scope=AuditScope.ORGANIZATION,
        target_id=config.organization_id,
        changes=changes,
        metadata={"config_version": config.version},
    )
    if entry is None:
        raise RuntimeError("BYOK unpriced consent audit could not be recorded")
