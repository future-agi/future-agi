ACTIVATION_SCHEMA_VERSION = "activation-state-2026-05-26.v1"

ONBOARDING_GOALS = (
    "improve_prompts",
    "build_ai_agent",
    "monitor_production_ai_app",
    "control_model_traffic",
    "evaluate_quality",
    "connect_voice_ai_agent",
    "explore_sample_data",
)

ONBOARDING_GOAL_ALIASES = {
    "test_and_improve_prompts": "improve_prompts",
    "build_or_prototype_agent": "build_ai_agent",
    "route_llm_traffic_safely": "control_model_traffic",
    "evaluate_quality_on_data_or_traces": "evaluate_quality",
}

PRODUCT_PATHS = (
    "prompt",
    "agent",
    "observe",
    "gateway",
    "voice",
    "evals",
    "dashboards",
    "sample",
)

PRODUCT_PATH_ALIASES = {
    "prompts": "prompt",
    "workbench": "prompt",
    "agents": "agent",
    "simulate_agent": "agent",
    "observability": "observe",
    "traces": "observe",
    "model_gateway": "gateway",
    "voice_ai": "voice",
    "eval": "evals",
    "evaluations": "evals",
    "dashboard": "dashboards",
    "sample_project": "sample",
}

ACTIVATION_STAGES = (
    "feature_disabled",
    "workspace_missing",
    "permission_limited",
    "choose_goal",
    "selected_path_unavailable",
    "activated",
    "daily_review",
    "start_prompt",
    "run_prompt_test",
    "save_prompt_version",
    "compare_prompt_versions",
    "prompt_next_loop",
    "create_agent",
    "run_agent_scenario",
    "review_agent_trace",
    "save_agent_eval",
    "agent_create_eval",
    "connect_observability",
    "waiting_for_first_trace",
    "waiting_for_first_trace_sample_available",
    "review_first_trace",
    "create_trace_evaluator",
    "create_trace_dashboard",
    "create_trace_alert",
    "configure_gateway_provider",
    "create_gateway_key",
    "run_gateway_request",
    "review_gateway_log",
    "fix_gateway_failure",
    "add_gateway_policy",
    "create_voice_agent",
    "run_voice_test_call",
    "review_voice_call",
    "add_voice_success_criteria",
    "voice_monitor_calls",
    "create_eval_dataset",
    "add_eval_scorer",
    "run_eval",
    "review_eval_failures",
    "eval_next_loop",
    "open_sample_project",
    "review_sample_signal",
    "connect_real_data",
)

HOME_MODES = (
    "first_run",
    "daily_quality",
    "fallback",
)

ACTION_KINDS = (
    "choose_goal",
    "setup",
    "send_signal",
    "review",
    "improve",
    "sample_project",
    "request_access",
    "fallback",
    "daily_quality",
    "adjacent_loop",
)

PROGRESS_KEYS = (
    "build",
    "test",
    "observe",
    "ship",
    "improve",
)

PROGRESS_STATES = (
    "not_started",
    "available",
    "selected",
    "in_progress",
    "blocked",
    "complete",
    "sample_only",
)

SAMPLE_PROJECT_STATUSES = (
    "unavailable",
    "available",
    "creating",
    "ready",
    "partial",
    "hidden",
    "stale_manifest",
    "repair_required",
)

LIFECYCLE_ELIGIBILITY_STATES = (
    "eligible",
    "suppressed",
    "skipped",
    "error",
)

LIFECYCLE_SUPPRESSION_REASONS = (
    "activated",
    "target_event_complete",
    "frequency_cap",
    "workspace_suppressed",
    "user_unsubscribed",
    "sample_hidden",
    "route_unavailable",
    "permission_limited",
    "feature_disabled",
    "recent_goal_change",
    "manual_pause",
)

ROUTE_AVAILABILITY_STATES = (
    "available",
    "unavailable",
)

ROUTE_UNAVAILABLE_REASONS = (
    "feature_disabled",
    "missing_id",
    "missing_permission",
    "plan_blocked",
    "route_not_implemented",
    "sample_artifact_missing",
    "target_event_complete",
    "workspace_missing",
)

EMAIL_CONTEXT_STATUSES = (
    "current",
    "stale",
    "expired",
    "invalid",
    "complete",
    "route_unavailable",
)

AVAILABLE_PATH_STATUSES = (
    "available",
    "selected",
    "in_progress",
    "blocked",
    "complete",
    "sample_only",
    "hidden",
)

ONBOARDING_ACTIVATION_EVENTS = (
    "onboarding_transition_viewed",
    "onboarding_home_viewed",
    "onboarding_goal_selected",
    "onboarding_goal_changed",
    "onboarding_recommended_action_viewed",
    "onboarding_recommended_action_clicked",
    "onboarding_path_card_clicked",
    "onboarding_blocked_state_viewed",
    "onboarding_diagnostics_opened",
    "onboarding_sample_project_opened",
    "sample_signal_viewed",
    "sample_to_real_setup_clicked",
    "first_quality_loop_completed",
    "daily_quality_home_viewed",
    "daily_quality_top_change_reviewed",
    "daily_quality_item_reviewed",
    "daily_quality_action_created",
    "daily_quality_action_opened",
    "daily_quality_action_assigned",
    "daily_quality_action_completed",
    "daily_quality_action_dismissed",
    "daily_quality_no_signal_viewed",
    "weekly_quality_review_opened",
    "weekly_quality_action_assigned",
    "weekly_quality_action_completed",
    "weekly_quality_review_completed",
    "reactivation_reason_clicked",
    "observe_project_created",
    "trace_ingested",
    "trace_reviewed",
    "team_member_invited",
    "trace_failure_detected",
)

ONBOARDING_ACTIVATION_EVENT_ALIASES = {
    "goal_selected": "onboarding_goal_selected",
    "goal_changed": "onboarding_goal_changed",
    "sample_project_opened": "onboarding_sample_project_opened",
    "sample_trace_detail_opened": "sample_signal_viewed",
    "quality_dashboard_viewed": "weekly_quality_review_opened",
}


def choices(values):
    return tuple((value, value) for value in values)


def canonical_goal(value):
    return ONBOARDING_GOAL_ALIASES.get(value, value)


def canonical_path(value):
    return PRODUCT_PATH_ALIASES.get(value, value)


def canonical_activation_event(value):
    return ONBOARDING_ACTIVATION_EVENT_ALIASES.get(value, value)
