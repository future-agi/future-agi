"""Which guardrail policies an org's config may name.

A guardrail check names, in `_policy`, the guardrail policy whose encrypted
blob holds its credentials, and the config push injects them. The push reads
only the pushed org's own live policies (config_push.org_guardrail_policies),
so another org's id is inert there. A save that names one is also refused
here, before anything is stored, with the same code and message as a deleted,
an unknown or a non-UUID id.
"""

import structlog

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.services.config_push import (
    OrgConfigRejected,
    guardrail_check_items,
    guardrail_policy_id,
    guardrail_policy_ref,
    org_guardrail_policies,
)

logger = structlog.get_logger(__name__)


class GuardrailPolicyNotInOrg(OrgConfigRejected):
    """A guardrail check names a policy that is not one of the org's own."""

    code = "guardrail_policy_not_in_org"

    def __init__(self, policy_refs):
        self.policy_ids = tuple(sorted(policy_refs))
        super().__init__(
            f"Guardrail policy {', '.join(self.policy_ids)} is not one of this "
            "organization's policies."
        )


def guardrail_section_items(guardrails):
    """(name, check) pairs of every check in a guardrails section: its rules
    and its checks, stored as a list or as a map keyed by name."""
    if not isinstance(guardrails, dict):
        return []
    return [
        *guardrail_check_items(guardrails.get("rules")),
        *guardrail_check_items(guardrails.get("checks")),
    ]


def _policy_key(check):
    """What identifies a check's `_policy` when comparing with stored ones:
    the canonical id, the raw value of a string that is not a UUID, and None
    for any other value."""
    pid = guardrail_policy_id(check)
    if pid is not None:
        return pid
    value = check.get("_policy")
    return value if isinstance(value, str) else None


def reject_unowned_guardrail_policies(org, guardrails, held_guardrails):
    """Refuse a guardrails section a save sends whose `_policy` is not the
    org's to name. Raises GuardrailPolicyNotInOrg naming the ids.

    A check may name one of the org's own live policies. Another org's live
    policy is always refused. Any other id (deleted, unknown, not a UUID) is
    refused with the same answer, unless `held_guardrails`, the section of
    the org's active version, already holds it: the console sends saved
    checks back whole, with the ids they were saved with. Such a held id stays
    inert, because the push reads only the org's own policies.

    The caller passes the active section itself: `_update_org_config`
    switches the active row off before its updater runs, so a lookup here
    would find none.
    """
    named = [
        check
        for _, check in guardrail_section_items(guardrails)
        if check.get("_policy")
    ]
    if not named:
        return
    held = {_policy_key(check) for _, check in guardrail_section_items(held_guardrails)}
    ids = {pid for check in named if (pid := guardrail_policy_id(check))}
    own = {
        str(pid)
        for pid in org_guardrail_policies(org.id, ids).values_list("id", flat=True)
    }
    foreign = {
        str(pid)
        for pid in AgentccGuardrailPolicy.no_workspace_objects.filter(
            id__in=ids - own, deleted=False
        )
        .exclude(organization_id=org.id)
        .values_list("id", flat=True)
    }
    allowed = own | (held - {None} - foreign)
    unowned = {
        guardrail_policy_ref(check)
        for check in named
        if _policy_key(check) not in allowed
    }
    if unowned:
        raise GuardrailPolicyNotInOrg(unowned)


def log_policy_refused(event, org, error):
    """Log a save refused for a policy id: the code and the ids, never a
    value."""
    logger.warning(
        event, org_id=str(org.id), code=error.code, policy_ids=error.policy_ids
    )
