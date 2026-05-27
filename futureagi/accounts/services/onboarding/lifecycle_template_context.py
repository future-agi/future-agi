from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings

from accounts.services.onboarding.lifecycle_tokens import sign_lifecycle_token

TEMPLATE_PREFIX = "onboarding_lifecycle"
SUPPORT_URL = "/dashboard/settings/support"


SUBJECTS = {
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


def _base_url():
    base = (
        getattr(settings, "ONBOARDING_LIFECYCLE_EMAIL_BASE_URL", "")
        or getattr(settings, "BASE_URL", "")
        or ""
    )
    return base.rstrip("/")


def absolute_lifecycle_url(path):
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base = _base_url()
    return f"{base}{path}" if base else path


def lifecycle_endpoint_path(endpoint, token, extra_params=None):
    params = {"token": token}
    if extra_params:
        params.update(extra_params)
    return absolute_lifecycle_url(
        f"/accounts/onboarding/lifecycle/{endpoint}/?{urlencode(params)}"
    )


def template_path(template_key):
    return f"{TEMPLATE_PREFIX}/{template_key}.html"


def subject_for_campaign(campaign):
    return SUBJECTS.get(
        campaign.get("campaign_group"),
        "Continue your FutureAGI setup",
    )


def build_lifecycle_template_context(*, send_log, campaign, target_route, now=None):
    click_token = sign_lifecycle_token(send_log=send_log, kind="click", now=now)
    unsubscribe_token = sign_lifecycle_token(
        send_log=send_log,
        kind="unsubscribe",
        now=now,
    )
    snooze_token = sign_lifecycle_token(send_log=send_log, kind="snooze", now=now)

    user_name = (
        getattr(send_log.user, "first_name", "")
        or getattr(send_log.user, "name", "")
        or getattr(send_log.user, "email", "")
    )
    workspace_name = getattr(send_log.workspace, "name", "") or "your workspace"
    action_label = campaign.get("email_cta_label") or _label_from_action(
        send_log.recommended_action_id,
        send_log.campaign_group,
    )

    return {
        "user_name": user_name,
        "workspace_name": workspace_name,
        "campaign_key": send_log.campaign_key,
        "campaign_family": send_log.campaign_group,
        "template_key": send_log.template_key,
        "template_version": send_log.template_version,
        "primary_path": send_log.primary_path,
        "activation_stage": send_log.activation_stage,
        "recommended_action_id": send_log.recommended_action_id,
        "target_success_event": send_log.target_success_event,
        "primary_action_label": action_label,
        "primary_action_url": lifecycle_endpoint_path("click", click_token),
        "fallback_url": absolute_lifecycle_url("/dashboard/home?source=email"),
        "unsubscribe_url": lifecycle_endpoint_path("unsubscribe", unsubscribe_token),
        "snooze_url": lifecycle_endpoint_path("snooze", snooze_token, {"days": 7}),
        "support_url": absolute_lifecycle_url(SUPPORT_URL),
        "target_route": target_route,
        "digest_preview": (send_log.metadata or {}).get("digest_preview"),
    }


def _label_from_action(action_id, campaign_group):
    labels = {
        "choose_onboarding_goal": "Choose setup path",
        "create_observe_project": "Connect observability",
        "send_first_trace": "Send first trace",
        "open_sample_trace": "Open sample trace",
        "review_first_trace": "Review trace",
        "create_trace_evaluator": "Create evaluator",
        "create_prompt": "Create prompt",
        "run_prompt_test": "Run prompt test",
        "save_prompt_version": "Save baseline",
        "compare_prompt_versions": "Compare versions",
        "add_prompt_failure_example": "Add failing example",
        "open_prompt_metrics": "Open prompt metrics",
        "create_agent": "Create agent",
        "run_agent_scenario": "Run scenario",
        "review_agent_trace": "Review run",
        "save_agent_eval": "Save as eval",
        "create_agent_eval": "Create eval",
        "open_agent_quality": "Open agent quality",
        "gateway_add_provider": "Add provider",
        "gateway_create_key": "Create key",
        "gateway_send_first_request": "Send request",
        "gateway_review_request": "Review log",
        "gateway_fix_failed_request": "Review failure",
        "gateway_add_policy": "Add control",
        "open_gateway_logs": "Open logs",
        "open_gateway_overview": "Open gateway",
        "review_daily_quality": "Review quality signal",
    }
    if action_id in labels:
        return labels[action_id]
    if campaign_group == "activation_success":
        return "Review quality signal"
    return "Continue setup"
