ONBOARDING_FLAG_NAMES = (
    "onboarding_activation_state_api",
    "onboarding_first_run_home_kill",
    "onboarding_goal_picker",
    "onboarding_path_cards",
    "onboarding_sample_project",
    "onboarding_daily_quality_home",
    "onboarding_observe_route_modes",
    "onboarding_prompt_path",
    "onboarding_prompt_route_modes",
    "onboarding_agent_path",
    "onboarding_agent_route_modes",
    "onboarding_gateway_path",
    "onboarding_gateway_route_modes",
    "onboarding_eval_path",
    "onboarding_eval_route_modes",
    "onboarding_voice_path",
    "onboarding_voice_route_modes",
    "onboarding_weekly_team_review",
    "onboarding_lifecycle_email_dry_run",
    "onboarding_email_welcome_enabled",
    "onboarding_email_first_action_recovery_enabled",
    "onboarding_email_first_signal_enabled",
    "onboarding_email_next_loop_enabled",
    "onboarding_email_sample_bridge_enabled",
    "onboarding_email_daily_digest_enabled",
    "onboarding_email_prompt_enabled",
    "onboarding_email_agent_enabled",
    "onboarding_email_agent",
    "onboarding_email_gateway_enabled",
    "onboarding_email_gateway",
    "onboarding_email_eval",
    "onboarding_eval_notifications",
    "onboarding_email_voice",
    "onboarding_voice_notifications",
    "onboarding_lifecycle_send_enabled",
)

CONTRACT_FLAG_ALIASES = {
    "onboarding_home_enabled": "onboarding_activation_state_api",
    "onboarding_observe_mvp_enabled": "onboarding_activation_state_api",
    "onboarding_sample_project_enabled": "onboarding_sample_project",
    "onboarding_lifecycle_dry_run_enabled": "onboarding_lifecycle_email_dry_run",
    "daily_quality_home_enabled": "onboarding_daily_quality_home",
}

DERIVED_ONBOARDING_FLAG_NAMES = (
    *CONTRACT_FLAG_ALIASES,
    "activation_state_debug_enabled",
    "onboarding_lifecycle_email_send",
)

SUPPORTED_ONBOARDING_FLAG_NAMES = frozenset(
    (*ONBOARDING_FLAG_NAMES, *DERIVED_ONBOARDING_FLAG_NAMES)
)
