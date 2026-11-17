"""Compare cap edits with the revision read by their caller, under the org lock.

The lock is owned by org_config_writer and remains held through commit. Checking
in a serializer or view would race the subsequent write. A missing/zero revision
means "expect no policy", not "overwrite whatever is current". Disabled policies
retain a positive revision; clearing or rolling back cannot reset that fence.
"""

from collections.abc import Mapping

from rest_framework.response import Response

from agentcc.contracts.gateway_admin import AgentsConfig, BudgetsConfig
from agentcc.models.cap_activation import AgentccCapPolicyRevision
from agentcc.services.config_push import OrgConfigRejected

CAP_FIELDS = frozenset(
    {"admission_caps_enabled", "agents", "key_ids", "policy_revision"}
)
CAP_PREREQUISITES = frozenset({"enabled", "fail_mode", "no_max_tokens"})
ROUTING_CAP_FIELDS = frozenset({"key_bindings", "max_run_usd"})
CAP_LEVEL_REJECTION = "Cap levels must use update-config with budgets.policy_revision."


class CapPolicyRevisionConflict(OrgConfigRejected):
    code = "cap_policy_revision_conflict"

    def __init__(self, policy_revision):
        self.policy_revision = policy_revision
        super().__init__("Cap policy changed; read the current policy before retrying.")

    def response(self):
        # Never echo the submitted patch, credentials, org identity or config.
        return Response(
            {"code": self.code, "policy_revision": self.policy_revision}, status=409
        )


def _routing_cap_patch(config, value, *, replace_sections):
    """Normalize both sides of an agent patch and detect explicit/implicit clears."""
    stored = config.routing or {}
    stored_agents = AgentsConfig._normalize_input_aliases(stored.get("agents"))
    stored_fields = set(stored_agents) if isinstance(stored_agents, Mapping) else set()
    removes_caps = bool(stored_fields & ROUTING_CAP_FIELDS)
    if not isinstance(value, Mapping):
        return value, removes_caps
    value = dict(value)
    touches_caps = replace_sections and removes_caps
    if "agents" in value:
        if "agents" in stored:
            # The writer gives us an unsaved copy under the org lock. Normalize
            # its merge base too: a canonical patch beside a legacy alias can
            # resurrect a deletion or shadow bindings that the patch must keep.
            config.routing = {**stored, "agents": stored_agents}
        agents = AgentsConfig._normalize_input_aliases(value["agents"])
        value["agents"] = agents
        if isinstance(agents, Mapping):
            touches_caps |= bool(set(agents) & ROUTING_CAP_FIELDS)
        else:
            touches_caps |= removes_caps
    return value, touches_caps


def cap_patch_under_lock(config, patch, *, replace_sections):
    """Check the caller's *unmerged* precondition and consume it before validation.

    Includes cap-map deletion, disabling, full budget replacement/clear and the
    prerequisites of an enabled cap policy. Once a policy exists, routing agent
    bindings and run limits (including section clears) use the same fence, even
    while disabled. Other fields retain their merge behavior without a revision.
    """
    checked = dict(patch)
    stored = BudgetsConfig._normalize_input_aliases(config.budgets or {})
    value = patch.get("budgets")
    incoming = BudgetsConfig._normalize_input_aliases(value)
    fields = set(incoming) if isinstance(incoming, Mapping) else set()
    touches_caps = "budgets" in patch and (
        bool(fields & CAP_FIELDS)
        or ((replace_sections or value is None) and bool(set(stored) & CAP_FIELDS))
        or (
            stored.get("admission_caps_enabled") is True
            and bool(fields & CAP_PREREQUISITES)
        )
    )
    touches_routing = False
    if "routing" in patch:
        checked["routing"], touches_routing = _routing_cap_patch(
            config, patch["routing"], replace_sections=replace_sections
        )
    if not touches_caps and not touches_routing:
        return checked

    durable = (
        AgentccCapPolicyRevision.objects.filter(organization_id=config.organization_id)
        .values_list("revision", flat=True)
        .first()
    )
    current = max(durable or 0, stored.get("policy_revision") or 0)
    if not touches_caps and current == 0:
        # Ordinary routing configuration before the first cap policy stays free
        # of preconditions; turning caps off later does not reopen this case.
        return checked
    expected = (
        incoming.get("policy_revision", 0) if isinstance(incoming, Mapping) else 0
    )
    if type(expected) is not int or expected < 0:
        raise OrgConfigRejected("budgets.policy_revision must be a nonnegative integer")
    if expected != current:
        raise CapPolicyRevisionConflict(current)

    # 0 is an API precondition, never a gateway policy revision. Alias
    # normalization before merging also prevents a stored canonical field from
    # silently shadowing a requested camelCase cap edit.
    if "budgets" in patch:
        if isinstance(incoming, Mapping):
            incoming = dict(incoming)
            incoming.pop("policy_revision", None)
        checked["budgets"] = incoming
    return checked
