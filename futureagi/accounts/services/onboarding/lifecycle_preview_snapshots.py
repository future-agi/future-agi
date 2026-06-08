from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from accounts.services.onboarding.flow_config import configured_action, configured_path
from accounts.services.onboarding.lifecycle_registry import (
    lifecycle_campaign_by_key,
    lifecycle_campaigns,
)
from accounts.services.onboarding.lifecycle_template_context import (
    render_lifecycle_email_preview,
)
from accounts.services.onboarding.lifecycle_template_contract import (
    required_context_keys_for_template,
)

PREVIEW_SOURCE = "lifecycle_preview_snapshot"
DEFAULT_TARGET_ROUTE = f"/dashboard/home?source={PREVIEW_SOURCE}"
MANIFEST_SCHEMA_VERSION = "onboarding-lifecycle-preview-manifest-2026-05-29.v1"
MANIFEST_FILENAME = "manifest.json"
OBSERVE_WAITING_CAMPAIGN_KEY = "observe_waiting_for_first_trace"
CROSS_PATH_EXPANSION_CAMPAIGN_KEY = "cross_path_expansion"
CROSS_PATH_EXPANSION_PREVIEW_TARGET_PATH = "gateway"
FIRST_LOOP_COMPLETE_CAMPAIGN_KEY = "first_loop_complete_next"
DORMANT_REACTIVATION_CAMPAIGN_KEY = "dormant_reactivation"
OBSERVE_SETUP_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "bedrock": "Bedrock",
    "langchain": "LangChain",
    "llamaindex": "LlamaIndex",
    "mcp": "MCP",
    "openai": "OpenAI",
    "openai_agents": "OpenAI Agents",
}
OBSERVE_SETUP_PROVIDER_ALIASES = {
    "llama-index": "llamaindex",
    "llama_index": "llamaindex",
    "openai-agents": "openai_agents",
    "openaiagents": "openai_agents",
}
OBSERVE_SETUP_LANGUAGE_LABELS = {
    "python": "Python",
    "typescript": "TypeScript",
}
VALUE_COPY_PREVIEW_SNAPSHOTS = {
    FIRST_LOOP_COMPLETE_CAMPAIGN_KEY: {
        "primary_path": "prompt",
        "last_meaningful_event": {
            "name": "prompt_comparison_completed",
            "path": "prompt",
        },
        "value_signal": {
            "headline": "Prompt comparison complete",
            "summary": "2 versions compared and 1 quality check ready",
        },
    },
    DORMANT_REACTIVATION_CAMPAIGN_KEY: {
        "primary_path": "gateway",
        "last_meaningful_event": {
            "name": "gateway_log_opened",
            "path": "gateway",
        },
        "value_signal": {
            "headline": "Gateway request reviewed",
            "summary": "1 routed request and 1 policy ready",
        },
    },
}


@dataclass(frozen=True)
class LifecyclePreviewSnapshotResult:
    output_dir: str
    count: int
    campaign_keys: tuple[str, ...]
    files: tuple[str, ...]

    def to_payload(self):
        return {
            "output_dir": self.output_dir,
            "count": self.count,
            "campaign_keys": list(self.campaign_keys),
            "files": list(self.files),
        }


def _preview_digest(now):
    return {
        "kind": "daily_quality_open_actions",
        "campaign_key": "daily_quality_open_actions",
        "template_key": "daily_quality_open_actions_v1",
        "generated_at": now.isoformat(),
        "workspace_id": "preview-workspace",
        "action_count": 1,
        "omitted_count": 0,
        "actions": [
            {
                "action_id": "preview-quality-action",
                "label": "Review trace regression",
                "route": "/dashboard/home?mode=daily-quality",
                "fallback_route": "/dashboard/home",
                "source_type": "trace",
                "source_id": "preview-trace",
                "primary_path": "observe",
                "status": "open",
                "age_minutes": 30,
                "last_event_at": now.isoformat(),
                "is_overdue": False,
                "body": "Internal note intentionally omitted from template output.",
                "metadata": {"api_token": "redacted-preview-token"},
            }
        ],
    }


def _safe_observe_setup_provider(value):
    normalized = str(value or "").strip().lower().replace(" ", "_")
    normalized = OBSERVE_SETUP_PROVIDER_ALIASES.get(normalized, normalized)
    if not normalized:
        return None
    if normalized not in OBSERVE_SETUP_PROVIDER_LABELS:
        raise ImproperlyConfigured(f"Unsupported observe setup provider: {value}")
    return normalized


def _safe_observe_setup_language(value):
    normalized = str(value or "").strip().lower().replace(" ", "_")
    if not normalized:
        return None
    if normalized not in OBSERVE_SETUP_LANGUAGE_LABELS:
        raise ImproperlyConfigured(f"Unsupported observe setup language: {value}")
    return normalized


def _observe_setup_preview_metadata(
    *,
    campaign,
    credentials_ready=False,
    language=None,
    now,
    provider=None,
):
    if campaign["campaign_key"] != OBSERVE_WAITING_CAMPAIGN_KEY:
        return {}

    provider = _safe_observe_setup_provider(provider)
    language = _safe_observe_setup_language(language)
    metadata = {}
    if credentials_ready:
        metadata["observe_credentials_ready"] = True
        metadata["observe_credentials_ready_at"] = now.isoformat()
    if provider:
        metadata["observe_setup_provider"] = provider
        metadata["observe_setup_provider_label"] = OBSERVE_SETUP_PROVIDER_LABELS[
            provider
        ]
    if language:
        metadata["observe_setup_language"] = language
        metadata["observe_setup_language_label"] = OBSERVE_SETUP_LANGUAGE_LABELS[
            language
        ]
    return metadata


def preview_campaign_for_snapshot(campaign):
    if campaign["campaign_key"] != CROSS_PATH_EXPANSION_CAMPAIGN_KEY:
        return campaign

    path = configured_path(CROSS_PATH_EXPANSION_PREVIEW_TARGET_PATH)
    action_id = path["first_action_id"]
    action = configured_action(action_id)
    return {
        **campaign,
        "target_action_id": action_id,
        "target_success_event": action["completion_event"],
        "expansion_target_href": "/dashboard/gateway/providers",
        "expansion_target_path": CROSS_PATH_EXPANSION_PREVIEW_TARGET_PATH,
        "expansion_target_route_key": action["route_key"],
    }


def _preview_activation_snapshot(campaign, now):
    value_snapshot = VALUE_COPY_PREVIEW_SNAPSHOTS.get(campaign["campaign_key"])
    if not value_snapshot:
        return {}

    last_event = {
        **value_snapshot["last_meaningful_event"],
        "occurred_at": now.isoformat(),
    }
    return {
        "stage": "daily_review",
        "primary_path": value_snapshot["primary_path"],
        "last_meaningful_event": last_event,
        "value_signal": value_snapshot["value_signal"],
    }


def _preview_primary_path(campaign, activation_snapshot):
    return activation_snapshot.get("primary_path") or campaign["primary_path"]


def preview_send_log_for_snapshot(
    campaign,
    *,
    now,
    observe_credentials_ready=False,
    observe_setup_language=None,
    observe_setup_provider=None,
):
    preview_id = uuid5(
        NAMESPACE_URL, f"futureagi:onboarding:{campaign['campaign_key']}"
    )
    metadata = {"source": PREVIEW_SOURCE}
    if campaign.get("requires_digest_preview"):
        metadata["digest_preview"] = _preview_digest(now)
    metadata.update(
        _observe_setup_preview_metadata(
            campaign=campaign,
            credentials_ready=observe_credentials_ready,
            language=observe_setup_language,
            now=now,
            provider=observe_setup_provider,
        )
    )
    user = SimpleNamespace(
        id=uuid5(NAMESPACE_URL, "futureagi:onboarding:preview-user"),
        first_name="FutureAGI reviewer",
        name="FutureAGI reviewer",
        email="reviewer@example.com",
    )
    workspace = SimpleNamespace(
        id=uuid5(NAMESPACE_URL, "futureagi:onboarding:preview-workspace"),
        name="Preview workspace",
    )
    activation_snapshot = _preview_activation_snapshot(campaign, now)
    primary_path = _preview_primary_path(campaign, activation_snapshot)
    evaluation_log = SimpleNamespace(
        activation_state_snapshot=activation_snapshot,
        primary_path=primary_path,
    )
    return SimpleNamespace(
        id=preview_id,
        user=user,
        user_id=user.id,
        workspace=workspace,
        workspace_id=workspace.id,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        primary_path=primary_path,
        activation_stage=campaign["entry_stages"][0],
        recommended_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
        evaluation_log=evaluation_log,
        metadata=metadata,
    )


def _campaigns_for_snapshot(campaign_key=None):
    if campaign_key:
        campaign = lifecycle_campaign_by_key(campaign_key)
        if not campaign:
            raise ImproperlyConfigured(
                f"Unknown onboarding lifecycle campaign: {campaign_key}"
            )
        return (campaign,)
    return lifecycle_campaigns()


def _markdown_cell(value):
    return str(value).replace("\n", " ").replace("|", "\\|")


def _markdown_index(rows):
    lines = [
        "# Onboarding lifecycle email previews",
        "",
        "These previews are generated without sending email.",
        "",
        "| Campaign | Group | Template | Subject | Preheader | HTML | Text |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {campaign_key} | {campaign_group} | {template_key} | {subject} | "
            "{preheader} | [html]({html_file}) | [text]({text_file}) |".format(
                **{key: _markdown_cell(value) for key, value in row.items()}
            )
        )
    lines.append("")
    return "\n".join(lines)


def _text_digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


def _manifest_entry(*, campaign, preview, html_name, text_name, html, text, now):
    entry = {
        "campaign_key": campaign["campaign_key"],
        "campaign_group": campaign["campaign_group"],
        "template_key": campaign["template_key"],
        "template_version": campaign["template_version"],
        "primary_path": campaign["primary_path"],
        "activation_stage": campaign["entry_stages"][0],
        "target_action_id": campaign["target_action_id"],
        "target_success_event": campaign["target_success_event"],
        "route_strategy": campaign["route_strategy"],
        "subject": preview["subject"],
        "preheader": preview["preheader"],
        "html_file": html_name,
        "text_file": text_name,
        "html_sha256": _text_digest(html),
        "text_sha256": _text_digest(text),
        "required_context_keys": sorted(
            required_context_keys_for_template(campaign["template_key"])
        ),
        "digest_preview_required": campaign.get("requires_digest_preview") is True,
        "generated_at": now.isoformat(),
    }
    if campaign.get("expansion_target_path"):
        entry["expansion_target_path"] = campaign["expansion_target_path"]
    return entry


def _manifest(rows, *, now):
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "source": PREVIEW_SOURCE,
        "count": len(rows),
        "campaigns": rows,
    }


def write_lifecycle_preview_snapshots(
    *,
    output_dir,
    campaign_key=None,
    force=False,
    now=None,
    observe_credentials_ready=False,
    observe_setup_language=None,
    observe_setup_provider=None,
):
    now = now or timezone.now()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    campaigns = _campaigns_for_snapshot(campaign_key)
    if (
        campaign_key
        and campaign_key != OBSERVE_WAITING_CAMPAIGN_KEY
        and (
            observe_credentials_ready
            or observe_setup_language
            or observe_setup_provider
        )
    ):
        raise ImproperlyConfigured(
            "Observe setup preview options require campaign "
            f"{OBSERVE_WAITING_CAMPAIGN_KEY} or all campaigns."
        )

    expected_files = [output_path / "index.md", output_path / MANIFEST_FILENAME]
    for campaign in campaigns:
        expected_files.append(output_path / f"{campaign['campaign_key']}.html")
        expected_files.append(output_path / f"{campaign['campaign_key']}.txt")
    if not force:
        for path in expected_files:
            if path.exists():
                raise ImproperlyConfigured(
                    f"{path} already exists. Use --force to overwrite previews."
                )

    rows = []
    manifest_rows = []
    files = []
    for raw_campaign in campaigns:
        campaign = preview_campaign_for_snapshot(raw_campaign)
        send_log = preview_send_log_for_snapshot(
            campaign,
            now=now,
            observe_credentials_ready=observe_credentials_ready,
            observe_setup_language=observe_setup_language,
            observe_setup_provider=observe_setup_provider,
        )
        preview = render_lifecycle_email_preview(
            send_log=send_log,
            campaign=campaign,
            target_route=DEFAULT_TARGET_ROUTE,
            now=now,
        )
        html_name = f"{campaign['campaign_key']}.html"
        text_name = f"{campaign['campaign_key']}.txt"
        html_path = output_path / html_name
        text_path = output_path / text_name
        html = preview["html"]
        text = preview["text"] + "\n"
        html_path.write_text(html, encoding="utf-8")
        text_path.write_text(text, encoding="utf-8")
        files.extend((html_name, text_name))
        manifest_rows.append(
            _manifest_entry(
                campaign=campaign,
                preview=preview,
                html_name=html_name,
                text_name=text_name,
                html=html,
                text=text,
                now=now,
            )
        )
        rows.append(
            {
                "campaign_key": campaign["campaign_key"],
                "campaign_group": campaign["campaign_group"],
                "template_key": campaign["template_key"],
                "subject": preview["subject"],
                "preheader": preview["preheader"],
                "html_file": html_name,
                "text_file": text_name,
            }
        )

    index_path = output_path / "index.md"
    index_path.write_text(_markdown_index(rows), encoding="utf-8")
    files.append("index.md")
    manifest_path = output_path / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(_manifest(manifest_rows, now=now), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    files.append(MANIFEST_FILENAME)
    return LifecyclePreviewSnapshotResult(
        output_dir=str(output_path),
        count=len(rows),
        campaign_keys=tuple(row["campaign_key"] for row in rows),
        files=tuple(files),
    )
