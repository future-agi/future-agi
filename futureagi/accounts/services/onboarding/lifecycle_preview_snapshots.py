from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

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


def _preview_send_log(campaign, *, now):
    preview_id = uuid5(
        NAMESPACE_URL, f"futureagi:onboarding:{campaign['campaign_key']}"
    )
    metadata = {"source": PREVIEW_SOURCE}
    if campaign.get("requires_digest_preview"):
        metadata["digest_preview"] = _preview_digest(now)
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
        primary_path=campaign["primary_path"],
        activation_stage=campaign["entry_stages"][0],
        recommended_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
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
    return {
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
):
    now = now or timezone.now()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    campaigns = _campaigns_for_snapshot(campaign_key)

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
    for campaign in campaigns:
        send_log = _preview_send_log(campaign, now=now)
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
