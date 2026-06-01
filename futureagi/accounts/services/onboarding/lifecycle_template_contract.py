from pathlib import Path

TEMPLATE_PREFIX = "onboarding_lifecycle"
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / TEMPLATE_PREFIX
EMAIL_SUBJECT_MAX_LENGTH = 78
EMAIL_PREHEADER_MAX_LENGTH = 120
USER_FACING_INTERNAL_COPY_TERMS = (
    "aha",
    "fast-track",
    "fast track",
    "sticky",
    "onboarding loop",
    "onboarding lifecycle",
    "real activation",
    "dry run",
)

DEFAULT_LIFECYCLE_EMAIL_COPY = {
    "subject": "Continue your FutureAGI setup",
    "preheader": "Open the next setup step for this workspace.",
}

SUPPORTED_LIFECYCLE_CAMPAIGN_GROUPS = frozenset(
    {
        "activation_success",
        "agent",
        "eval",
        "first_signal",
        "gateway",
        "next_loop",
        "prompt",
        "recovery",
        "sample",
        "voice",
        "welcome",
    }
)

SUPPORTED_LIFECYCLE_TEMPLATE_KEYS = frozenset(
    {
        "agent_add_starter_prompt_v1",
        "agent_create_eval_v1",
        "agent_create_first_v1",
        "agent_review_failed_scenario_v1",
        "agent_run_first_scenario_v1",
        "agent_sample_bridge_v1",
        "agent_save_scenario_v1",
        "daily_quality_open_actions_v1",
        "eval_add_scorer_v1",
        "eval_create_source_v1",
        "eval_fix_source_v1",
        "eval_review_failures_v1",
        "eval_run_first_v1",
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
        "voice_add_success_criteria_v1",
        "voice_create_agent_v1",
        "voice_review_call_v1",
        "voice_run_test_call_v1",
        "welcome_choose_goal_v1",
        "welcome_resume_goal_v1",
    }
)

BASE_REQUIRED_CONTEXT_KEYS = frozenset(
    {
        "primary_action_label",
        "primary_action_url",
        "email_subject",
        "preheader_text",
        "snooze_url",
        "unsubscribe_url",
        "user_name",
        "workspace_name",
    }
)
DIGEST_PREVIEW_TEMPLATE_KEYS = frozenset({"daily_quality_open_actions_v1"})
OBSERVE_CREDENTIAL_CONTEXT_TEMPLATE_KEYS = frozenset(
    {"observe_waiting_for_first_trace_v1"}
)


def required_context_keys_for_template(template_key):
    keys = set(BASE_REQUIRED_CONTEXT_KEYS)
    if template_key in DIGEST_PREVIEW_TEMPLATE_KEYS:
        keys.add("digest_preview")
    if template_key in OBSERVE_CREDENTIAL_CONTEXT_TEMPLATE_KEYS:
        keys |= {"observe_credentials_ready", "observe_credentials_ready_at"}
    return frozenset(keys)


def template_path_for_key(template_key):
    return f"{TEMPLATE_PREFIX}/{template_key}.html"


def template_file_path(template_key):
    return TEMPLATE_DIR / f"{template_key}.html"


def lifecycle_email_copy_for_campaign(campaign):
    campaign = campaign or {}
    subject = campaign.get("email_subject")
    preheader = campaign.get("email_preheader")
    if isinstance(subject, str):
        subject = subject.strip()
    else:
        subject = ""
    if isinstance(preheader, str):
        preheader = preheader.strip()
    else:
        preheader = ""
    return {
        "subject": subject or DEFAULT_LIFECYCLE_EMAIL_COPY["subject"],
        "preheader": preheader or DEFAULT_LIFECYCLE_EMAIL_COPY["preheader"],
    }


def _copy_text_errors(value, field_name, max_length):
    if not isinstance(value, str) or not value.strip():
        return [f"{field_name} must be a non-empty string."]
    value = value.strip()
    errors = []
    if len(value) > max_length:
        errors.append(f"{field_name} must be {max_length} characters or fewer.")
    if "\n" in value or "\r" in value:
        errors.append(f"{field_name} must be a single line.")
    if "{{" in value or "{%" in value:
        errors.append(f"{field_name} must not contain template syntax.")
    normalized = value.lower()
    for term in USER_FACING_INTERNAL_COPY_TERMS:
        if term in normalized:
            errors.append(f"{field_name} must not contain internal term: {term}.")
    return errors


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
    if campaign_group not in SUPPORTED_LIFECYCLE_CAMPAIGN_GROUPS:
        errors.append("campaign_group is not supported by lifecycle email.")
    subject = campaign.get("email_subject")
    preheader = campaign.get("email_preheader")
    errors.extend(
        _copy_text_errors(
            subject,
            "email_subject",
            EMAIL_SUBJECT_MAX_LENGTH,
        )
    )
    errors.extend(
        _copy_text_errors(
            preheader,
            "email_preheader",
            EMAIL_PREHEADER_MAX_LENGTH,
        )
    )
    if (
        isinstance(subject, str)
        and isinstance(preheader, str)
        and subject.strip().lower() == preheader.strip().lower()
    ):
        errors.append("email_preheader must not repeat email_subject.")
    if template_key in DIGEST_PREVIEW_TEMPLATE_KEYS and not requires_digest_preview:
        errors.append("requires_digest_preview must be true for this template.")
    if requires_digest_preview and template_key not in DIGEST_PREVIEW_TEMPLATE_KEYS:
        errors.append("requires_digest_preview is not supported by this template.")

    return tuple(errors)
