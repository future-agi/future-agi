"""Server-owned first activation, assigned under the central writer's org lock.

UTC is the explicit day boundary; this cap slice has no org-timezone setting.
The Redis admission ledger counts both key and agent model spend, including
BYOK. The timestamp is not evidence of historical reconciliation or total spend.
"""

from copy import deepcopy
from datetime import UTC

from django.db.models import Max

from agentcc.contracts.gateway_admin import AdmissionCapConfig, BudgetsConfig
from agentcc.models.cap_activation import AgentccCapActivation, AgentccCapPolicyRevision
from agentcc.models.org_config import AgentccOrgConfig


def stamp_cap_activation(config, *, advance_revision=True):
    budgets = BudgetsConfig._normalize_input_aliases(deepcopy(config.budgets or {}))
    enabled = budgets.get("admission_caps_enabled") is True
    row = AgentccCapPolicyRevision.objects.filter(
        organization_id=config.organization_id
    ).first()
    has_policy = row is not None or any(
        name in budgets for name in ("admission_caps_enabled", "agents", "key_ids")
    )
    # OFF is a policy state, not a reset to "never configured". Keep its CAS
    # fence even after clearing all cap maps or rolling back to a legacy row.
    if has_policy and (advance_revision or not budgets.get("policy_revision")):
        if row is None:
            row = AgentccCapPolicyRevision.objects.create(
                organization_id=config.organization_id
            )
        # Upgrade from saved-version fences without ever reusing an old order.
        saved_max = (
            AgentccOrgConfig.no_workspace_objects.filter(
                organization_id=config.organization_id
            ).aggregate(Max("version"))["version__max"]
            or 0
        )
        row.revision = max(row.revision, saved_max) + 1
        row.save(update_fields=["revision"])
        budgets["policy_revision"] = row.revision
    for name, prefix in (("agents", "agent:"), ("key_ids", "key_id:")):
        for identity, cap in (budgets.get(name) or {}).items():
            # Neither an updater nor a client may select or reset activation.
            cap = AdmissionCapConfig._normalize_input_aliases(cap)
            budgets[name][identity] = cap
            cap.pop("activated_at", None)
            if enabled:
                row, _ = AgentccCapActivation.objects.get_or_create(
                    organization_id=config.organization_id, level=prefix + identity
                )
                cap["activated_at"] = (
                    row.activated_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
                )
    config.budgets = budgets


def cap_accounting(budgets):
    budgets = BudgetsConfig._normalize_input_aliases(budgets or {})
    policies = {}
    for name, prefix in (("agents", "agent:"), ("key_ids", "key_id:")):
        for identity, cap in (budgets.get(name) or {}).items():
            activated = cap.get("activated_at")
            policies[prefix + identity] = {
                "activated_at": activated,
                "description": f"counts spend admitted since {activated}"
                if activated
                else "Activation unavailable; capped requests are refused.",
                "day_boundary": "UTC",
                "includes_byok": True,
            }
    return {
        "enabled": budgets.get("admission_caps_enabled") is True,
        "policies": policies,
    }
