from django.conf import settings

from analytics.posthog_util import posthog_tracker

ONBOARDING_FLAG_NAMES = (
    "onboarding_activation_state_api",
    "onboarding_goal_picker",
    "onboarding_path_cards",
    "onboarding_sample_project",
    "onboarding_daily_quality_home",
    "onboarding_lifecycle_email_dry_run",
    "onboarding_email_welcome_enabled",
    "onboarding_email_first_action_recovery_enabled",
    "onboarding_email_first_signal_enabled",
    "onboarding_email_next_loop_enabled",
    "onboarding_email_sample_bridge_enabled",
    "onboarding_email_daily_digest_enabled",
    "onboarding_lifecycle_send_enabled",
)

CONTRACT_FLAG_ALIASES = {
    "onboarding_home_enabled": "onboarding_activation_state_api",
    "onboarding_observe_mvp_enabled": "onboarding_activation_state_api",
    "onboarding_sample_project_enabled": "onboarding_sample_project",
    "onboarding_lifecycle_dry_run_enabled": "onboarding_lifecycle_email_dry_run",
    "daily_quality_home_enabled": "onboarding_daily_quality_home",
}


def _flag_overrides():
    return dict(getattr(settings, "ONBOARDING_FEATURE_FLAGS", {}) or {})


def _groups(organization, workspace):
    groups = {}
    if organization:
        groups["organization"] = str(organization.id)
    if workspace:
        groups["workspace"] = str(workspace.id)
    return groups


def get_onboarding_flags(*, user, organization, workspace):
    flags = dict.fromkeys(ONBOARDING_FLAG_NAMES, False)
    overrides = _flag_overrides()
    overrides_configured = hasattr(settings, "ONBOARDING_FEATURE_FLAGS")
    groups = _groups(organization, workspace)

    for flag_name in ONBOARDING_FLAG_NAMES:
        if flag_name in overrides:
            flags[flag_name] = bool(overrides[flag_name])
            continue
        if overrides_configured:
            continue
        try:
            user_id = getattr(user, "id", None)
            if user_id:
                flags[flag_name] = bool(
                    posthog_tracker.is_feature_enabled(
                        flag_name,
                        user_id,
                        groups=groups,
                    )
                )
        except Exception:
            flags[flag_name] = False

    for alias, source_flag in CONTRACT_FLAG_ALIASES.items():
        flags[alias] = bool(flags.get(source_flag, False))
    flags["onboarding_lifecycle_email_send"] = bool(
        flags.get("onboarding_lifecycle_send_enabled", False)
    )
    flags["activation_state_debug_enabled"] = bool(
        overrides.get("activation_state_debug_enabled", False)
    )
    return flags
