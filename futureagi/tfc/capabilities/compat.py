"""Compatibility bridge between legacy ee_gating and the new capability service.

Provides the mapping from legacy EEFeature enum values to the new
capability registry feature IDs. Used by ee_gating.py to delegate to the
capability service while preserving the existing call signatures.

Deprecation: callers should migrate to tfc.capabilities.service.check()
directly. These wrappers exist so the transition is non-breaking.
"""

from __future__ import annotations

# Legacy EEFeature string -> new capability registry ID mapping.
# Some IDs are unchanged; some legacy names map to new canonical IDs.
_LEGACY_TO_CAPABILITY_ID: dict[str, str] = {
    "knowledge_base": "knowledge_base",
    "review_workflow": "review_workflow",
    "agreement_metrics": "agreement_metrics",
    "required_labels": "required_labels",
    "audit_logs": "audit_logs",
    "scim": "scim",
    "voice_sim": "voice_sim",
    "synthetic_data": "synthetic_data",
    "agentic_eval": "agentic_eval",
    "optimization": "optimization",
    "project_rbac": "project_rbac",
    "custom_roles": "custom_roles",
    "data_masking": "data_masking",
    "extended_retention": "extended_retention",
    "custom_brand": "custom_brand",
    "dedicated_support": "dedicated_support",
    "falcon_ai": "falcon_ai",
    "turing_models": "turing_models",
    "protect": "protect",
    "scenarios": "scenarios",
    "error_feed": "error_feed",
    "fix_my_agent": "fix_my_agent",
}


def legacy_feature_to_capability_id(legacy_feature: str) -> str | None:
    return _LEGACY_TO_CAPABILITY_ID.get(legacy_feature)
