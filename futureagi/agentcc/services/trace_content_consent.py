"""Explicit organization consent for exporting MCP tool-span content."""

from types import SimpleNamespace

from accounts.models.audit_log import AuditScope
from agentcc.permissions import require_gateway_admin
from agentcc.services.config_push import OrgConfigRejected
from agentcc.trace_content import AUDIT_ACTION
from tfc.utils import audit


def capture_enabled(config):
    privacy = config.privacy if config is not None else {}
    if not isinstance(privacy, dict):
        return False
    if "trace_content" in privacy and "traceContent" in privacy:
        raise OrgConfigRejected("privacy.trace_content must be supplied only once")
    value = privacy.get("trace_content", privacy.get("traceContent", False))
    if type(value) is not bool:
        raise OrgConfigRejected("privacy.trace_content must be a boolean")
    return value


def require_capture_admin(active, config, user):
    """Called under the org write lock, before any effects of the transition."""
    if capture_enabled(active) != capture_enabled(config):
        require_gateway_admin(
            SimpleNamespace(user=user, organization=config.organization)
        )


def audit_capture_change(active, config, user):
    """Use the existing audit mechanism in the config's transaction; fail closed.

    A missing audit row must not leave consent enabled, even when the shared
    logger is muted or catches a storage error. No content or credentials enter
    the audit record, only the consent boolean and the config version.
    """
    old, new = capture_enabled(active), capture_enabled(config)
    if old == new:
        return
    entry = audit.log_audit(
        organization=config.organization,
        actor=user,
        action=AUDIT_ACTION,
        scope=AuditScope.ORGANIZATION,
        target_id=config.organization_id,
        changes={"trace_content": {"old": old, "new": new}},
        metadata={"config_version": config.version},
    )
    if entry is None:
        raise RuntimeError("Capture consent audit could not be recorded")
