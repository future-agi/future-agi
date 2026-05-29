from django.conf import settings

from accounts.services.onboarding.feature_flag_contract import (
    CONTRACT_FLAG_ALIASES,
    ONBOARDING_FLAG_NAMES,
)
from analytics.posthog_util import posthog_tracker

CORE_ONBOARDING_DEFAULTS = {
    "onboarding_activation_state_api": True,
    "onboarding_goal_picker": True,
    "onboarding_path_cards": True,
    "onboarding_sample_project": True,
    "onboarding_observe_route_modes": True,
    "onboarding_prompt_path": True,
    "onboarding_prompt_route_modes": True,
    "onboarding_agent_path": True,
    "onboarding_agent_route_modes": True,
    "onboarding_gateway_path": True,
    "onboarding_gateway_route_modes": True,
    "onboarding_eval_path": True,
    "onboarding_eval_route_modes": True,
    "onboarding_voice_path": True,
    "onboarding_voice_route_modes": True,
}

SELF_HOST_ONBOARDING_DEFAULTS = CORE_ONBOARDING_DEFAULTS

CLOUD_DEPLOYMENT_VALUES = {"US", "EU", "DEV"}


def _flag_overrides():
    return dict(getattr(settings, "ONBOARDING_FEATURE_FLAGS", {}) or {})


def _uses_cloud_flag_service():
    return (
        str(getattr(settings, "CLOUD_DEPLOYMENT", "") or "").upper()
        in CLOUD_DEPLOYMENT_VALUES
    )


def _groups(organization, workspace):
    groups = {}
    if organization:
        groups["organization"] = str(organization.id)
    if workspace:
        groups["workspace"] = str(workspace.id)
    return groups


def get_onboarding_flags(*, user, organization, workspace):
    flags = dict.fromkeys(ONBOARDING_FLAG_NAMES, False)
    flags.update(CORE_ONBOARDING_DEFAULTS)
    overrides = _flag_overrides()
    overrides_configured = hasattr(settings, "ONBOARDING_FEATURE_FLAGS")
    groups = _groups(organization, workspace)
    use_cloud_flags = _uses_cloud_flag_service()

    if not use_cloud_flags:
        flags.update(SELF_HOST_ONBOARDING_DEFAULTS)

    remote_flag_names = []
    for flag_name in ONBOARDING_FLAG_NAMES:
        if flag_name in overrides:
            flags[flag_name] = bool(overrides[flag_name])
            continue
        if flag_name in CORE_ONBOARDING_DEFAULTS:
            continue
        if overrides_configured:
            continue
        if not use_cloud_flags:
            continue
        remote_flag_names.append(flag_name)

    user_id = getattr(user, "id", None)
    if user_id and remote_flag_names:
        remote_flags = posthog_tracker.get_feature_flags(
            remote_flag_names,
            user_id,
            groups=groups,
        )
        for flag_name in remote_flag_names:
            flags[flag_name] = bool(remote_flags.get(flag_name, False))

    for alias, source_flag in CONTRACT_FLAG_ALIASES.items():
        flags[alias] = bool(flags.get(source_flag, False))
    flags["onboarding_email_agent"] = bool(
        flags.get("onboarding_email_agent")
        or flags.get("onboarding_email_agent_enabled")
    )
    flags["onboarding_email_agent_enabled"] = bool(flags["onboarding_email_agent"])
    flags["onboarding_email_gateway"] = bool(
        flags.get("onboarding_email_gateway")
        or flags.get("onboarding_email_gateway_enabled")
    )
    flags["onboarding_email_gateway_enabled"] = bool(flags["onboarding_email_gateway"])
    flags["onboarding_lifecycle_email_send"] = bool(
        flags.get("onboarding_lifecycle_send_enabled", False)
    )
    flags["activation_state_debug_enabled"] = bool(
        overrides.get("activation_state_debug_enabled", False)
    )
    return flags
