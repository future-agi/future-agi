from pathlib import Path

TEMPLATE_PREFIX = "onboarding_lifecycle"
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / TEMPLATE_PREFIX

LIFECYCLE_CAMPAIGN_SUBJECTS = {
    "welcome": "Pick the first FutureAGI setup path",
    "recovery": "Continue your FutureAGI setup",
    "sample": "Use a sample trace while you connect data",
    "first_signal": "Your first trace is ready to review",
    "prompt": "Continue your prompt quality loop",
    "agent": "Continue your agent quality loop",
    "gateway": "Continue your gateway request loop",
    "next_loop": "Turn the trace review into coverage",
    "activation_success": "Review today's AI quality signal",
}

SUPPORTED_LIFECYCLE_TEMPLATE_KEYS = frozenset(
    {
        "agent_create_eval_v1",
        "agent_create_first_v1",
        "agent_review_failed_scenario_v1",
        "agent_run_first_scenario_v1",
        "agent_sample_bridge_v1",
        "agent_save_scenario_v1",
        "daily_quality_open_actions_v1",
        "first_loop_complete_next_v1",
        "gateway_add_policy_v1",
        "gateway_add_provider_v1",
        "gateway_create_key_v1",
        "gateway_fix_failed_request_v1",
        "gateway_review_request_v1",
        "gateway_sample_bridge_v1",
        "gateway_send_first_request_v1",
        "observe_connect_first_v1",
        "observe_first_trace_ready_v1",
        "observe_next_loop_v1",
        "observe_sample_bridge_v1",
        "observe_waiting_for_first_trace_v1",
        "prompt_add_failure_example_v1",
        "prompt_compare_candidate_v1",
        "prompt_create_first_v1",
        "prompt_run_first_test_v1",
        "prompt_save_baseline_v1",
        "welcome_choose_goal_v1",
        "welcome_resume_goal_v1",
    }
)

BASE_REQUIRED_CONTEXT_KEYS = frozenset(
    {
        "primary_action_label",
        "primary_action_url",
        "snooze_url",
        "unsubscribe_url",
        "user_name",
        "workspace_name",
    }
)
DIGEST_PREVIEW_TEMPLATE_KEYS = frozenset({"daily_quality_open_actions_v1"})


def required_context_keys_for_template(template_key):
    keys = set(BASE_REQUIRED_CONTEXT_KEYS)
    if template_key in DIGEST_PREVIEW_TEMPLATE_KEYS:
        keys.add("digest_preview")
    return frozenset(keys)


def template_path_for_key(template_key):
    return f"{TEMPLATE_PREFIX}/{template_key}.html"


def template_file_path(template_key):
    return TEMPLATE_DIR / f"{template_key}.html"


def lifecycle_template_contract_errors(campaign):
    template_key = campaign.get("template_key")
    campaign_group = campaign.get("campaign_group")
    requires_digest_preview = campaign.get("requires_digest_preview") is True
    errors = []

    if template_key not in SUPPORTED_LIFECYCLE_TEMPLATE_KEYS:
        errors.append("template_key references unknown lifecycle template.")
        return tuple(errors)
    if not template_file_path(template_key).is_file():
        errors.append("template_key references missing lifecycle template file.")
    if campaign_group not in LIFECYCLE_CAMPAIGN_SUBJECTS:
        errors.append("campaign_group has no lifecycle email subject.")
    if template_key in DIGEST_PREVIEW_TEMPLATE_KEYS and not requires_digest_preview:
        errors.append("requires_digest_preview must be true for this template.")
    if requires_digest_preview and template_key not in DIGEST_PREVIEW_TEMPLATE_KEYS:
        errors.append("requires_digest_preview is not supported by this template.")

    return tuple(errors)
