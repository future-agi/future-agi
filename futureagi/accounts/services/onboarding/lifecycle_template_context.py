from __future__ import annotations

from html import unescape
from urllib.parse import urlencode

from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from accounts.services.onboarding.flow_config import configured_action, configured_path
from accounts.services.onboarding.lifecycle_template_contract import (
    TEMPLATE_PREFIX,
    lifecycle_email_copy_for_campaign,
)
from accounts.services.onboarding.lifecycle_tokens import sign_lifecycle_token

SUPPORT_URL = "/dashboard/settings/support"
NOTIFICATION_SETTINGS_URL = "/dashboard/settings/notifications"
DEFAULT_POSTAL_ADDRESS = "Future AGI, Inc., 600 California St, San Francisco, CA 94108"

DEFAULT_OBSERVE_TRACE_CHECKS = {
    "title": "Check the first-trace setup",
    "checks": [
        "Confirm the FutureAGI API key and secret are loaded where the request runs.",
        "Run the request after project registration and package instrumentation.",
        "Keep FutureAGI open so the trace can be detected and opened for review.",
    ],
}

OBSERVE_TRACE_CHECKS_BY_PROVIDER = {
    "anthropic": {
        "title": "Check the Anthropic trace setup",
        "checks": [
            "Confirm ANTHROPIC_API_KEY is loaded where the request runs.",
            "Call AnthropicInstrumentor before creating the Anthropic client.",
            "Run client.messages.create once so the first trace can arrive.",
        ],
    },
    "bedrock": {
        "title": "Check the Bedrock trace setup",
        "checks": [
            "Confirm AWS credentials, AWS_REGION, and BEDROCK_MODEL_ID are loaded.",
            "Confirm the role can call bedrock:InvokeModel for the selected model.",
            "Call BedrockInstrumentor before creating or using the bedrock-runtime client.",
        ],
    },
    "langchain": {
        "title": "Check the LangChain trace setup",
        "checks": [
            "Confirm the model provider key, such as OPENAI_API_KEY, is loaded.",
            "Call LangChainInstrumentor before creating ChatOpenAI or your chain.",
            "Run llm.invoke or your chain once so the first trace can arrive.",
        ],
    },
    "llama_index": {
        "title": "Check the LlamaIndex trace setup",
        "checks": [
            "Confirm the LLM or embedding provider key, such as OPENAI_API_KEY, is loaded.",
            "Call LlamaIndexInstrumentor before building the index or query engine.",
            "Run query_engine.query once so retrieval or generation spans are created.",
        ],
    },
    "mcp": {
        "title": "Check the MCP trace setup",
        "checks": [
            "Confirm MCP_SERVER_URL and MCP_SERVER_TOKEN reach a server that lists tools.",
            "Instrument both OpenAI Agents and MCP before Runner.run starts.",
            "Run one safe MCP tool call so the trace can arrive.",
        ],
    },
    "openai": {
        "title": "Check the OpenAI trace setup",
        "checks": [
            "Confirm OPENAI_API_KEY is loaded where the request runs.",
            "Call OpenAIInstrumentor before creating the OpenAI client.",
            "Run responses.create once so the first trace can arrive.",
        ],
    },
    "openai_agents": {
        "title": "Check the OpenAI Agents trace setup",
        "checks": [
            "Confirm OPENAI_API_KEY is loaded where Runner.run executes.",
            "Call OpenAIAgentsInstrumentor before constructing or running the agent.",
            "Run Runner.run or Runner.run_sync once so the first trace can arrive.",
        ],
    },
}

OBSERVE_TRACE_CHECK_PROVIDER_ALIASES = {
    "llamaindex": "llama_index",
    "openaiagents": "openai_agents",
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
    return lifecycle_email_copy_for_campaign(campaign)["subject"]


def preheader_for_campaign(campaign):
    return lifecycle_email_copy_for_campaign(campaign)["preheader"]


def _safe_int(value, fallback=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return fallback


def _safe_inline_text(value, *, fallback="", limit=96):
    text = " ".join(str(value or fallback).split())
    return text[:limit]


def _quality_item_label(count):
    return f"{count} quality item" if count == 1 else f"{count} quality items"


def daily_quality_digest_summary(metadata):
    metadata = metadata if isinstance(metadata, dict) else {}
    preview = metadata.get("digest_preview")
    if not isinstance(preview, dict):
        return {
            "action_count": 0,
            "action_count_label": "0 quality items",
            "action_waiting_text": "0 quality items are waiting",
            "first_action_label": "",
            "omitted_count": 0,
            "omitted_count_label": "",
            "overdue_count": 0,
            "overdue_count_label": "",
            "subject": "A quality action is waiting for review",
            "preheader": (
                "Review, assign, or dismiss the open item from the daily quality view."
            ),
        }

    raw_actions = preview.get("actions")
    if isinstance(raw_actions, list):
        actions = [action for action in raw_actions if isinstance(action, dict)]
    else:
        actions = []
    action_count = _safe_int(preview.get("action_count"), len(actions))
    visible_count = len(actions)
    omitted_count = _safe_int(
        preview.get("omitted_count"),
        max(action_count - visible_count, 0),
    )
    overdue_count = _safe_int(
        preview.get("overdue_count"),
        sum(1 for action in actions if action.get("is_overdue")),
    )
    first_action_label = _safe_inline_text(actions[0].get("label")) if actions else ""
    action_count_label = _quality_item_label(action_count)
    action_waiting_text = (
        f"{action_count_label} is waiting"
        if action_count == 1
        else f"{action_count_label} are waiting"
    )
    subject = action_waiting_text
    if overdue_count:
        verb = "needs" if action_count == 1 else "need"
        subject = f"{action_count_label} {verb} review ({overdue_count} overdue)"
    if first_action_label:
        omitted_text = f" and {omitted_count} more" if omitted_count else ""
        preheader = f"Open Daily Quality: {first_action_label}{omitted_text}."
    else:
        preheader = f"Open Daily Quality to review {action_count_label}."

    return {
        "action_count": action_count,
        "action_count_label": action_count_label,
        "action_waiting_text": action_waiting_text,
        "first_action_label": first_action_label,
        "omitted_count": omitted_count,
        "omitted_count_label": (
            f"{omitted_count} more item"
            if omitted_count == 1
            else f"{omitted_count} more items"
        )
        if omitted_count
        else "",
        "overdue_count": overdue_count,
        "overdue_count_label": (
            f"{overdue_count} overdue item"
            if overdue_count == 1
            else f"{overdue_count} overdue items"
        )
        if overdue_count
        else "",
        "subject": _safe_inline_text(subject, limit=78),
        "preheader": _safe_inline_text(preheader, limit=120),
    }


def _configured_path_copy(path_id):
    try:
        return configured_path(path_id)
    except (KeyError, TypeError):
        return {}


def _configured_action_copy(action_id):
    try:
        return configured_action(action_id)
    except (KeyError, TypeError):
        return {}


def cross_path_expansion_summary(campaign):
    campaign = campaign if isinstance(campaign, dict) else {}
    path_id = _safe_inline_text(campaign.get("expansion_target_path"), limit=64)
    path_config = _configured_path_copy(path_id)
    action_id = campaign.get("target_action_id")
    action_config = _configured_action_copy(action_id)
    path_label = _safe_inline_text(
        path_config.get("label"),
        fallback="the next AI workflow",
        limit=64,
    )
    path_description = _safe_inline_text(
        path_config.get("description"),
        fallback="Add another part of your AI system to this workspace.",
        limit=180,
    )
    action_title = _safe_inline_text(
        action_config.get("title"),
        fallback=lifecycle_action_label(action_id, campaign.get("campaign_group")),
        limit=64,
    )
    action_description = _safe_inline_text(
        action_config.get("description"),
        fallback=path_description,
        limit=180,
    )
    targeted = bool(path_id and path_config and action_config)
    subject = (
        f"Next: {action_title}"
        if targeted
        else lifecycle_email_copy_for_campaign(campaign)["subject"]
    )
    preheader = (
        action_description
        if targeted
        else lifecycle_email_copy_for_campaign(campaign)["preheader"]
    )
    return {
        "targeted": targeted,
        "target_path": path_id,
        "path_label": path_label,
        "path_description": path_description,
        "action_title": action_title,
        "action_description": action_description,
        "subject": _safe_inline_text(subject, limit=78),
        "preheader": _safe_inline_text(preheader, limit=120),
    }


def _activation_snapshot_for_send(send_log):
    evaluation_log = getattr(send_log, "evaluation_log", None)
    snapshot = getattr(evaluation_log, "activation_state_snapshot", None)
    return snapshot if isinstance(snapshot, dict) else {}


def _dormant_reactivation_path(send_log, snapshot):
    last_event = snapshot.get("last_meaningful_event")
    if isinstance(last_event, dict):
        event_path = _safe_inline_text(last_event.get("path"), limit=64)
        if event_path:
            return event_path
    snapshot_path = _safe_inline_text(snapshot.get("primary_path"), limit=64)
    if snapshot_path:
        return snapshot_path
    return _safe_inline_text(getattr(send_log, "primary_path", ""), limit=64)


def dormant_reactivation_summary(send_log):
    snapshot = _activation_snapshot_for_send(send_log)
    path_id = _dormant_reactivation_path(send_log, snapshot)
    path_config = _configured_path_copy(path_id)
    area_label = _safe_inline_text(
        path_config.get("lifecycle_reactivation_label"),
        fallback="AI quality",
        limit=64,
    )
    path_label = _safe_inline_text(
        path_config.get("label"),
        fallback="Daily Quality",
        limit=64,
    )
    value_signal = snapshot.get("value_signal")
    if not isinstance(value_signal, dict):
        value_signal = {}
    value_headline = _safe_inline_text(value_signal.get("headline"), limit=80)
    value_summary = _safe_inline_text(value_signal.get("summary"), limit=120)
    targeted = bool(path_config and path_id not in {"any", "sample"})
    subject = (
        f"Review your {area_label}"
        if targeted
        else "Pick your AI quality review back up"
    )
    preheader = (
        f"Open Daily Quality to review {area_label} and choose the next fix."
        if targeted
        else "Your workspace is set up and has gone quiet, so here is the next item worth a look."
    )
    if targeted and value_headline and value_summary:
        preheader = f"{value_headline}: {value_summary}"
    return {
        "targeted": targeted,
        "target_path": path_id if targeted else "",
        "area_label": area_label,
        "path_label": path_label,
        "value_headline": value_headline,
        "value_summary": value_summary,
        "has_value_signal": bool(value_headline or value_summary),
        "subject": _safe_inline_text(subject, limit=78),
        "preheader": _safe_inline_text(preheader, limit=120),
    }


def first_loop_complete_summary(send_log):
    base = dormant_reactivation_summary(send_log)
    targeted = base["targeted"]
    subject = (
        f"Your {base['area_label']} review is live"
        if targeted
        else "Your first AI quality review is live"
    )
    preheader = (
        f"Open Daily Quality to keep reviewing {base['area_label']} after the first loop."
        if targeted
        else "Open daily quality to review the next signal after your first review."
    )
    if targeted and base["value_headline"] and base["value_summary"]:
        preheader = f"{base['value_headline']}: {base['value_summary']}"
    return {
        **base,
        "subject": _safe_inline_text(subject, limit=78),
        "preheader": _safe_inline_text(preheader, limit=120),
    }


def lifecycle_email_copy_for_send(campaign, metadata, *, send_log=None):
    copy = lifecycle_email_copy_for_campaign(campaign)
    campaign_key = (campaign or {}).get("campaign_key")
    if campaign_key == "daily_quality_open_actions":
        summary = daily_quality_digest_summary(metadata)
        if summary["action_count"] <= 0:
            return copy
        return {
            "subject": summary["subject"],
            "preheader": summary["preheader"],
        }
    if campaign_key == "cross_path_expansion":
        summary = cross_path_expansion_summary(campaign)
        if summary["targeted"]:
            return {
                "subject": summary["subject"],
                "preheader": summary["preheader"],
            }
    if campaign_key == "dormant_reactivation" and send_log is not None:
        summary = dormant_reactivation_summary(send_log)
        if summary["targeted"]:
            return {
                "subject": summary["subject"],
                "preheader": summary["preheader"],
            }
    if campaign_key == "first_loop_complete_next" and send_log is not None:
        summary = first_loop_complete_summary(send_log)
        if summary["targeted"]:
            return {
                "subject": summary["subject"],
                "preheader": summary["preheader"],
            }
    return copy


def lifecycle_email_postal_address():
    value = getattr(settings, "ONBOARDING_LIFECYCLE_EMAIL_POSTAL_ADDRESS", "")
    if not isinstance(value, str):
        return DEFAULT_POSTAL_ADDRESS
    value = " ".join(value.split())
    return value or DEFAULT_POSTAL_ADDRESS


def observe_setup_package_label(metadata):
    provider = metadata.get("observe_setup_provider_label")
    language = metadata.get("observe_setup_language_label")
    parts = [part for part in (provider, language) if part]
    return " ".join(parts)


def observe_trace_checks(metadata):
    provider = (
        (metadata.get("observe_setup_provider") or "").strip().lower().replace("-", "_")
    )
    provider = OBSERVE_TRACE_CHECK_PROVIDER_ALIASES.get(provider, provider)
    return OBSERVE_TRACE_CHECKS_BY_PROVIDER.get(provider, DEFAULT_OBSERVE_TRACE_CHECKS)


def build_lifecycle_template_context(*, send_log, campaign, target_route, now=None):
    send_metadata = send_log.metadata or {}
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
    action_label = campaign.get("email_cta_label") or lifecycle_action_label(
        send_log.recommended_action_id,
        send_log.campaign_group,
    )
    digest_summary = daily_quality_digest_summary(send_metadata)
    expansion_summary = cross_path_expansion_summary(campaign)
    dormant_summary = dormant_reactivation_summary(send_log)
    first_loop_summary = first_loop_complete_summary(send_log)
    email_copy = lifecycle_email_copy_for_send(
        campaign,
        send_metadata,
        send_log=send_log,
    )
    observe_trace_check_copy = observe_trace_checks(send_metadata)

    return {
        "user_name": user_name,
        "workspace_name": workspace_name,
        "email_subject": email_copy["subject"],
        "preheader_text": email_copy["preheader"],
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
        "notification_settings_url": absolute_lifecycle_url(NOTIFICATION_SETTINGS_URL),
        "mailing_address": lifecycle_email_postal_address(),
        "target_route": target_route,
        "digest_preview": send_metadata.get("digest_preview"),
        "digest_summary": digest_summary,
        "expansion_summary": expansion_summary,
        "dormant_summary": dormant_summary,
        "first_loop_summary": first_loop_summary,
        "observe_credentials_ready": bool(
            send_metadata.get("observe_credentials_ready")
        ),
        "observe_credentials_ready_at": send_metadata.get(
            "observe_credentials_ready_at"
        ),
        "observe_setup_language": send_metadata.get("observe_setup_language"),
        "observe_setup_language_label": send_metadata.get(
            "observe_setup_language_label"
        ),
        "observe_setup_package_label": observe_setup_package_label(send_metadata),
        "observe_setup_provider": send_metadata.get("observe_setup_provider"),
        "observe_setup_provider_label": send_metadata.get(
            "observe_setup_provider_label"
        ),
        "observe_trace_check_title": observe_trace_check_copy["title"],
        "observe_trace_checks": observe_trace_check_copy["checks"],
    }


def render_lifecycle_email_preview(*, send_log, campaign, target_route, now=None):
    template = template_path(send_log.template_key)
    context = build_lifecycle_template_context(
        send_log=send_log,
        campaign=campaign,
        target_route=target_route,
        now=now,
    )
    html = render_to_string(template, context)
    text = " ".join(unescape(strip_tags(html)).split())
    return {
        "subject": context["email_subject"],
        "preheader": context["preheader_text"],
        "template": template,
        "context": context,
        "html": html,
        "text": text,
    }


def lifecycle_action_label(action_id, campaign_group):
    labels = {
        "choose_onboarding_goal": "Choose setup path",
        "create_observe_project": "Create Observe project",
        "send_first_trace": "Send first trace",
        "open_sample_trace": "Open sample trace",
        "review_first_trace": "Review trace",
        "create_trace_evaluator": "Create quality check",
        "create_prompt": "Create prompt",
        "run_prompt_test": "Run prompt test",
        "save_prompt_version": "Save baseline",
        "create_second_prompt_version": "Create second version",
        "compare_prompt_versions": "Compare versions",
        "add_prompt_failure_example": "Add failing example",
        "open_prompt_metrics": "Open prompt metrics",
        "create_agent": "Create agent",
        "add_agent_node": "Add step",
        "run_agent_scenario": "Run scenario",
        "review_agent_trace": "Review run",
        "save_agent_eval": "Save as eval",
        "create_agent_eval": "Create eval",
        "open_agent_quality": "Open agent quality",
        "gateway_add_provider": "Add model provider",
        "gateway_create_key": "Create key",
        "gateway_send_first_request": "Send request",
        "gateway_review_request": "Review log",
        "gateway_fix_failed_request": "Review failure",
        "gateway_add_policy": "Add control",
        "open_gateway_logs": "Open logs",
        "open_gateway_overview": "Open gateway",
        "create_eval_dataset": "Create eval dataset",
        "add_eval_scorer": "Add scorer",
        "run_eval": "Run eval",
        "review_eval_failures": "Review result",
        "fix_eval_source": "Fix source",
        "open_eval_usage": "Open eval results",
        "create_voice_agent": "Create voice agent",
        "run_voice_test_call": "Run test call",
        "review_voice_call": "Review call",
        "add_voice_success_criteria": "Add criteria",
        "voice_monitor_calls": "Monitor calls",
        "review_daily_quality": "Review quality signal",
    }
    if action_id in labels:
        return labels[action_id]
    if campaign_group == "activation_success":
        return "Review quality signal"
    return "Continue setup"
